# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures for guard tests: config, fake clocks and a core on the simulator."""

import itertools
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from remoteg90ctl.civ import read_xiegu_id_request
from remoteg90ctl.config import (
    Backend,
    ClusterConfig,
    Config,
    GuardConfig,
    IqConfig,
    LoggingConfig,
    PttLine,
    RadioConfig,
    SafetyConfig,
    ServerConfig,
)
from remoteg90ctl.guard.core import GuardCore
from remoteg90ctl.guard.main import GuardServer
from remoteg90ctl.guard.notify import Notifier
from remoteg90ctl.guard.protocol import Request
from remoteg90ctl.guard.txlog import TxLog
from remoteg90ctl.sim import DEFAULT_IDENTITY, SimulatedRadio, SimulatedSerialPort

RADIO = 0x88
CTRL = 0xE0
# The guard's own identity query, written on every port open (decisions.md D32).
IDENTITY_QUERY = read_xiegu_id_request(RADIO, CTRL)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeWallClock:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def __call__(self) -> datetime:
        return datetime(2026, 10, 31, tzinfo=UTC) + timedelta(seconds=self._clock.now)


def make_config(
    tmp_path: Path,
    *,
    backend: Backend = Backend.SIMULATOR,
    ptt_line: PttLine = PttLine.RTS,
    allow_transmit: bool = False,
    allow_atu_tune: bool = False,
    max_rf_power_w: float | None = None,
    hard_limit_s: float = 180.0,
    lease_renew_interval_s: float = 0.5,
    lease_timeout_s: float = 2.0,
    identity: bytes | None = DEFAULT_IDENTITY,
    duty_window_s: float = 600.0,
    duty_max_tx_s: float = 300.0,
    iq: IqConfig | None = None,
) -> Config:
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    return Config(
        radio=RadioConfig(
            backend=backend,
            port="/dev/serial/by-id/usb-test" if backend is Backend.HARDWARE else None,
            baudrate=19200,
            radio_address=RADIO,
            controller_address=CTRL,
            ptt_line=ptt_line,
            identity=identity,
        ),
        safety=SafetyConfig(
            allow_transmit=allow_transmit,
            lease_renew_interval_s=lease_renew_interval_s,
            lease_timeout_s=lease_timeout_s,
            hard_limit_s=hard_limit_s,
            duty_window_s=duty_window_s,
            duty_max_tx_s=duty_max_tx_s,
            allow_atu_tune=allow_atu_tune,
            max_rf_power_w=max_rf_power_w,
        ),
        guard=GuardConfig(
            socket_path=tmp_path / "guard.sock",
            tx_log_path=tmp_path / "tx.jsonl",
            state_dir=state_dir,
        ),
        server=ServerConfig(
            socket_path=tmp_path / "server.sock", listen=(), state_dir=tmp_path / "server-state"
        ),
        logging=LoggingConfig(level="DEBUG"),
        iq=iq,
        cluster=ClusterConfig(socket_path=tmp_path / "cluster.sock"),
    )


class Harness:
    """A GuardCore on a simulated port, with a fake clock unless ``real_time``."""

    def __init__(
        self, config: Config, port: SimulatedSerialPort | None = None, *, real_time: bool = False
    ) -> None:
        self.config = config
        self.clock = FakeClock()
        self._core_clock: Callable[[], float] = time.monotonic if real_time else self.clock
        # A fresh simulated port asserts both lines on open unless the guard lowers
        # them first, so these tests also prove the guard's open sequence.
        self.port = port or SimulatedSerialPort(SimulatedRadio(RADIO), clock=self.clock)
        self._ids = itertools.count(1)
        self.core = self.new_core()

    def new_core(self) -> GuardCore:
        """A fresh core on the same port, state dir and log (simulates a restart)."""
        txlog = TxLog(self.config.guard.tx_log_path, FakeWallClock(self.clock))
        return GuardCore(self.config, lambda: self.port, clock=self._core_clock, txlog=txlog)

    def send(self, kind: str, **fields: Any) -> dict[str, Any]:
        response = self.core.handle(Request(next(self._ids), kind, fields))
        return dict(response)

    def tx_request(self, source: str = "test") -> dict[str, Any]:
        return self.send("tx_request", source=source, frequency_hz=7_074_000)

    def start_tx(self) -> str:
        response = self.tx_request()
        assert response["ok"], response
        lease = response["lease"]
        assert isinstance(lease, str)
        return lease

    @property
    def ptt_active(self) -> bool:
        return self.port.lines.rts or self.port.lines.dtr

    def tx_log(self) -> list[dict[str, Any]]:
        path = self.config.guard.tx_log_path
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def tx_events(self, name: str) -> list[dict[str, Any]]:
        return [r for r in self.tx_log() if r["event"] == name]


def hardware_config(tmp_path: Path, **kwargs: Any) -> Config:
    """Hardware backend config; tests still inject a simulated port."""
    return make_config(tmp_path, backend=Backend.HARDWARE, **kwargs)


TIMEOUT = 5.0


def wait_for(condition: Callable[[], bool], timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


class RunningGuard:
    """A GuardServer for ``harness`` running in a background thread."""

    def __init__(self, harness: Harness, notifier: Notifier | None = None) -> None:
        self.harness = harness
        self.server = GuardServer(
            harness.core, harness.config.guard.socket_path, notifier or Notifier({})
        )
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.server.serve_forever()
        except BaseException as exc:
            self.error = exc

    def __enter__(self) -> Self:
        self.thread.start()
        wait_for(lambda: self.server.ready or self.error is not None)
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        self.server.request_stop()
        self.thread.join(TIMEOUT)
