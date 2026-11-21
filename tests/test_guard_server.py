# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard serving loop: socket protocol, single client, watchdog, signals, failures."""

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import guard_helpers
from guard_helpers import CTRL, RADIO, TIMEOUT, Harness, make_config, wait_for
from remoteg90ctl.civ import read_frequency_request
from remoteg90ctl.guard.main import STOP_SIGNALS, GuardServer, install_signal_handlers
from remoteg90ctl.guard.notify import Notifier
from remoteg90ctl.guard.protocol import MAX_LINE_BYTES
from remoteg90ctl.guard.state import RUNNING_FILE


class Client:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        self.sock.connect(str(path))
        self._buf = b""
        self._ids = 0

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        self._ids += 1
        self.send_raw(
            json.dumps({"v": 1, "id": self._ids, "type": kind, **fields}).encode() + b"\n"
        )
        while True:
            message = self.receive()
            if message.get("id") == self._ids:
                return message

    def receive(self) -> dict[str, Any]:
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        result: dict[str, Any] = json.loads(line)
        return result

    def is_closed(self) -> bool:
        try:
            self.sock.settimeout(TIMEOUT)
            while True:
                if not self.sock.recv(65536):
                    return True
        except (ConnectionError, OSError):
            return True

    def close(self) -> None:
        self.sock.close()


def connect_when_listening(path: Path) -> Client:
    """The socket file appears at bind(); connections succeed only after listen()."""
    connected: list[Client] = []

    def attempt() -> bool:
        try:
            connected.append(Client(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return False
        return True

    wait_for(attempt)
    return connected[0]


class RunningGuard(guard_helpers.RunningGuard):
    def client(self) -> Client:
        return Client(self.harness.config.guard.socket_path)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(make_config(tmp_path))


@pytest.fixture
def guard(harness: Harness) -> Iterator[RunningGuard]:
    with RunningGuard(harness) as running:
        yield running


def test_socket_permissions(guard: RunningGuard) -> None:
    mode = guard.harness.config.guard.socket_path.stat().st_mode
    assert stat.S_IMODE(mode) == 0o660


def test_status_over_socket(guard: RunningGuard) -> None:
    client = guard.client()
    response = client.request("status")
    assert response["ok"]
    assert response["v"] == 1
    assert response["port"] == "open"


def test_civ_round_trip_over_socket(guard: RunningGuard) -> None:
    client = guard.client()
    assert client.request("civ", frame=read_frequency_request(RADIO, CTRL).hex())["ok"]
    assert client.receive() == {"v": 1, "event": "civ_rx", "frame": "fefee088030040070700fd"}


def test_bad_requests(guard: RunningGuard) -> None:
    client = guard.client()
    client.send_raw(b"not json\n")
    assert client.receive()["error"] == "bad_request"
    client.send_raw(b'{"v": 2, "id": 1, "type": "status"}\n')
    assert client.receive()["error"] == "unsupported_version"
    client.send_raw(b'{"v": 1, "id": 2, "type": "ptt_on"}\n')
    assert client.receive()["error"] == "unknown_type"
    client.send_raw(b'{"v": 1, "id": 3, "type": "status", "extra": 1}\n')
    assert client.receive()["error"] == "bad_request"
    assert client.request("status")["ok"]


def test_oversized_line_disconnects(guard: RunningGuard, harness: Harness) -> None:
    client = guard.client()
    assert client.request("tx_request", source="t", frequency_hz=1)["ok"]
    client.send_raw(b"x" * (MAX_LINE_BYTES + 10))
    assert client.is_closed()
    wait_for(lambda: not harness.ptt_active)


def test_second_client_rejected(guard: RunningGuard, harness: Harness) -> None:
    first = guard.client()
    lease = first.request("tx_request", source="first", frequency_hz=1)["lease"]
    second = guard.client()
    assert second.receive()["error"] == "busy"
    assert second.is_closed()
    # The first client and its transmission are unaffected.
    assert first.request("tx_renew", lease=lease)["ok"]
    assert harness.ptt_active


def test_disconnect_during_tx_releases_immediately(guard: RunningGuard, harness: Harness) -> None:
    client = guard.client()
    assert client.request("tx_request", source="t", frequency_hz=1)["ok"]
    assert harness.ptt_active
    client.close()
    # No clock advance: release is immediate, not after the lease timeout.
    wait_for(lambda: not harness.ptt_active)
    wait_for(lambda: bool(harness.tx_events("tx_end")))
    assert harness.tx_events("tx_end")[0]["reason"] == "client_disconnected"
    # A new client can connect afterwards.
    assert guard.client().request("status")["tx"] is None


def test_silent_client_lease_expires(guard: RunningGuard, harness: Harness) -> None:
    client = guard.client()
    lease = client.request("tx_request", source="t", frequency_hz=1)["lease"]
    harness.clock.advance(2.0)  # connection open, but no renewal
    event = client.receive()
    assert event == {"v": 1, "event": "tx_ended", "lease": lease, "reason": "lease_expired"}
    assert not harness.ptt_active


# Watchdog (AGENTS.md rule 12)


class NotifyListener:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(path))
        self.sock.settimeout(0.05)
        self.messages: list[tuple[float, str]] = []

    def drain(self) -> None:
        while True:
            try:
                data = self.sock.recv(1024)
            except TimeoutError:
                return
            self.messages.append((time.monotonic(), data.decode()))

    def collect_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.drain()


def test_watchdog_only_from_completed_safety_loop(tmp_path: Path, harness: Harness) -> None:
    listener = NotifyListener(tmp_path / "n.sock")
    notifier = Notifier({"NOTIFY_SOCKET": str(tmp_path / "n.sock"), "WATCHDOG_USEC": "200000"})
    assert notifier.watchdog_interval_s == pytest.approx(0.05)

    blocked = threading.Event()
    unblock = threading.Event()
    real_poll = harness.core.poll

    def poll() -> None:
        if blocked.is_set():
            unblock.wait(TIMEOUT)
        real_poll()

    harness.core.poll = poll  # type: ignore[method-assign]
    with RunningGuard(harness, notifier):
        listener.collect_for(0.3)
        assert listener.messages[0][1] == "READY=1"
        assert sum(m == "WATCHDOG=1" for _, m in listener.messages) >= 3

        # Hang the safety loop: no WATCHDOG=1 may be sent while it is stuck.
        blocked.set()
        listener.collect_for(0.1)  # let an iteration already in flight finish
        hang_started = time.monotonic()
        listener.collect_for(0.5)
        hang_ended = time.monotonic()
        during = [t for t, m in listener.messages if m == "WATCHDOG=1" and t > hang_started]
        assert during == []

        blocked.clear()
        unblock.set()
        listener.collect_for(0.3)
        after = [t for t, m in listener.messages if m == "WATCHDOG=1" and t > hang_ended]
        assert after


def test_ready_after_port_confirmed(tmp_path: Path, harness: Harness) -> None:
    listener = NotifyListener(tmp_path / "n.sock")
    notifier = Notifier({"NOTIFY_SOCKET": str(tmp_path / "n.sock")})
    with RunningGuard(harness, notifier):
        listener.collect_for(0.2)
    messages = [m for _, m in listener.messages]
    assert messages[:2] == ["READY=1", "STATUS=port open"]
    assert "WATCHDOG=1" not in messages  # watchdog not enabled in this environment
    assert harness.port.active_intervals("rts") == []


# Failures and signals


def test_exception_in_loop_lowers_lines(harness: Harness) -> None:
    real_poll = harness.core.poll
    explode = threading.Event()

    def poll() -> None:
        if explode.is_set():
            raise RuntimeError("injected failure")
        real_poll()

    harness.core.poll = poll  # type: ignore[method-assign]
    running = RunningGuard(harness)
    with running:
        client = running.client()
        assert client.request("tx_request", source="t", frequency_hz=1)["ok"]
        assert harness.ptt_active
        explode.set()
        running.thread.join(TIMEOUT)
    assert isinstance(running.error, RuntimeError)
    assert not harness.ptt_active
    assert not harness.port.is_open
    assert harness.tx_events("tx_end")[0]["reason"] == "error"
    # Unclean exit: marker left behind, so the next start locks TX (D24.8).
    assert (harness.config.guard.state_dir / RUNNING_FILE).exists()
    assert not harness.config.guard.socket_path.exists()


@pytest.mark.parametrize("sig", STOP_SIGNALS)
def test_signal_lowers_lines_and_stops(harness: Harness, sig: signal.Signals) -> None:
    server = GuardServer(harness.core, harness.config.guard.socket_path, Notifier({}))
    previous = {s: signal.getsignal(s) for s in STOP_SIGNALS}
    install_signal_handlers(server)
    observed: dict[str, bool] = {}
    clients: list[Client] = []  # stay connected: the signal, not a disconnect, must end TX

    def operate() -> None:
        wait_for(lambda: server.ready)
        clients.append(Client(harness.config.guard.socket_path))
        clients[0].request("tx_request", source="t", frequency_hz=1)
        observed["ptt_before"] = harness.ptt_active
        os.kill(os.getpid(), sig)

    worker = threading.Thread(target=operate, daemon=True)
    try:
        worker.start()
        server.serve_forever()  # signal handlers run in the main thread
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    worker.join(TIMEOUT)
    assert observed["ptt_before"] is True
    assert not harness.ptt_active
    assert harness.tx_events("tx_end")[0]["reason"] == "shutdown"
    assert not (harness.config.guard.state_dir / RUNNING_FILE).exists()


def test_guard_process_end_to_end(tmp_path: Path) -> None:
    """Real process, simulator backend: start, transmit, SIGTERM, clean exit."""
    state = tmp_path / "state"
    state.mkdir()
    sock = tmp_path / "g.sock"
    config = tmp_path / "guard.toml"
    config.write_text(
        f"""
[radio]
backend = "simulator"
radio_address = 0x88
controller_address = 0xE0
ptt_line = "rts"
identity = "190090"
[safety]
lease_renew_interval_s = 0.5
lease_timeout_s = 2.0
hard_limit_s = 180
duty_window_s = 600
duty_max_tx_s = 300
[guard]
socket_path = "{sock}"
tx_log_path = "{tmp_path / "tx.jsonl"}"
state_dir = "{state}"
[server]
socket_path = "{tmp_path / "s.sock"}"
"""
    )
    process = subprocess.Popen(  # noqa: S603 - fixed argv, test-controlled config
        [sys.executable, "-m", "remoteg90ctl.guard.main", "--config", str(config)],
        stderr=subprocess.PIPE,
    )
    try:
        client = connect_when_listening(sock)
        assert client.request("tx_request", source="e2e", frequency_hz=7_074_000)["ok"]
        process.send_signal(signal.SIGTERM)
        assert process.wait(TIMEOUT) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(TIMEOUT)
    records = [json.loads(line) for line in (tmp_path / "tx.jsonl").read_text().splitlines()]
    events = [r["event"] for r in records]
    assert events == ["guard_start", "tx_start", "tx_end", "guard_stop"]
    assert records[2]["reason"] == "shutdown"
    assert not sock.exists()


def test_invalid_config_refuses_to_start(tmp_path: Path) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[safety]\nhard_limit_s = 300\n")
    result = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "remoteg90ctl.guard.main", "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    assert result.returncode == 2
    assert "safety.hard_limit_s: 300 s exceeds the safety limit of 180 s" in result.stderr


def test_refuses_to_start_if_another_guard_listens(guard: RunningGuard, harness: Harness) -> None:
    other = GuardServer(harness.new_core(), harness.config.guard.socket_path, Notifier({}))
    with pytest.raises(RuntimeError, match="another guard"):
        other.serve_forever()
    # The running guard keeps its socket; its probe connection is gone shortly.
    assert harness.config.guard.socket_path.exists()

    def status_ok() -> bool:
        try:
            return bool(guard.client().request("status")["ok"])
        except (ConnectionError, OSError):
            return False

    wait_for(status_ok)
