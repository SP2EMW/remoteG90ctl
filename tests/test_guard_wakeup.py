# SPDX-License-Identifier: GPL-3.0-or-later
"""The guard wakes up when the radio answers (owner's decision 2026-11-08).

Before, the loop read the port only after its select timeout (50 ms), so every
CI-V exchange took about 52 ms on the owner's radio. Now the port's descriptor
is in the selector too. The safety loop is unchanged: it still runs at least
every LOOP_INTERVAL_S and signals the watchdog only after a full pass.
"""

import contextlib
import logging
import os
import statistics
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from guard_helpers import CTRL, RADIO, Harness, RunningGuard, make_config, wait_for
from remoteg90ctl.civ import read_frequency_request
from remoteg90ctl.guard.main import EMPTY_WAKEUPS_LIMIT, LOOP_INTERVAL_S
from remoteg90ctl.guard.port import PySerialPort
from remoteg90ctl.sim.port import SimulatedSerialPort
from remoteg90ctl.sim.radio import SimulatedRadio
from test_guard_server import Client

REPLY_DELAY_S = 0.005


class PipePort(SimulatedSerialPort):
    """A simulated port whose bytes arrive REPLY_DELAY_S after a write, signalled on a pipe."""

    def __init__(self, *, drain: bool = True) -> None:
        super().__init__(SimulatedRadio(RADIO))
        self._r, self._w = os.pipe()
        os.set_blocking(self._r, False)
        self._drain = drain
        self._ready = bytearray()
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        super().write(data)
        produced = bytes(self._rx)  # what the radio (and the echo) sent back
        self._rx.clear()
        timer = threading.Timer(REPLY_DELAY_S, self._arrive, (produced,))
        timer.daemon = True
        timer.start()

    def _arrive(self, data: bytes) -> None:
        with self._lock:
            self._ready.extend(data)
        with contextlib.suppress(OSError):
            os.write(self._w, b"!")

    def fileno(self) -> int | None:
        return self._r if self.is_open else None

    def read_available(self) -> bytes:
        if self._drain:
            with contextlib.suppress(BlockingIOError):
                os.read(self._r, 4096)
        with self._lock:
            data = bytes(self._ready)
            self._ready.clear()
        return super().read_available() + data


@pytest.fixture
def running(tmp_path: Path) -> Iterator[RunningGuard]:
    harness = Harness(make_config(tmp_path), PipePort(), real_time=True)
    with RunningGuard(harness) as guard:
        yield guard


def _round_trip(client: Client) -> float:
    frame = read_frequency_request(RADIO, CTRL).hex()
    started = time.monotonic()
    assert client.request("civ", frame=frame)["ok"]
    while client.receive().get("event") != "civ_rx":
        pass
    return time.monotonic() - started


def test_a_reply_wakes_the_loop_at_once(running: RunningGuard) -> None:
    wait_for(lambda: running.server._port_wakeup is not None)
    client = Client(running.harness.config.guard.socket_path)
    times = [_round_trip(client) for _ in range(10)]
    # Without the wake-up each answer waits for the loop timeout (50 ms).
    assert statistics.median(times) < LOOP_INTERVAL_S / 2, times


def test_a_descriptor_that_keeps_waking_with_nothing_is_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A device in a bad state must not spin the loop: back to the timeout for that open."""
    port = PipePort()
    harness = Harness(make_config(tmp_path), port, real_time=True)
    with caplog.at_level(logging.WARNING), RunningGuard(harness) as guard:
        wait_for(lambda: guard.server._port_wakeup is not None)
        port._drain = False
        os.write(port._w, b"x")  # readable for ever, and nothing to read
        wait_for(lambda: guard.server._port_wakeup is None)
        assert guard.server._empty_wakeups >= EMPTY_WAKEUPS_LIMIT
        client = Client(harness.config.guard.socket_path)
        assert client.request("status")["ok"]  # the loop still serves
    assert any("port_wakeup_disabled" in r.getMessage() for r in caplog.records)


def test_a_lost_port_leaves_the_selector(running: RunningGuard) -> None:
    wait_for(lambda: running.server._port_wakeup is not None)
    running.harness.port.disconnect()
    wait_for(lambda: running.server._port_wakeup is None)
    assert running.harness.core.port_wakeup() is None


def test_a_closed_hardware_port_has_no_descriptor() -> None:
    assert PySerialPort("/dev/serial/by-id/none", 19200).fileno() is None
