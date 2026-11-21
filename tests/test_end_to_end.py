# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard, server and CLI as separate processes, on the simulator."""

import json
import signal
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from guard_helpers import TIMEOUT, wait_for


@dataclass
class Processes:
    config: Path
    server_socket: Path
    tx_log: Path

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-m", "remoteg90ctl.cli", "--socket", str(self.server_socket), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def tx_records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.tx_log.read_text().splitlines()]


def _spawn(module: str, config: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-m", module, "--config", str(config)], stderr=subprocess.DEVNULL
    )


@pytest.fixture
def processes(tmp_path: Path) -> Iterator[Processes]:
    state = tmp_path / "state"
    state.mkdir()
    config = tmp_path / "config.toml"
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
socket_path = "{tmp_path / "g.sock"}"
tx_log_path = "{tmp_path / "tx.jsonl"}"
state_dir = "{state}"
[server]
socket_path = "{tmp_path / "s.sock"}"
[logging]
level = "WARNING"
"""
    )
    guard = _spawn("remoteg90ctl.guard.main", config)
    server = _spawn("remoteg90ctl.server.main", config)
    procs = Processes(config, tmp_path / "s.sock", tmp_path / "tx.jsonl")
    try:
        wait_for(
            lambda: (
                procs.server_socket.exists()
                and "serial port:    open" in procs.cli("status").stdout
            ),
            timeout=10,
        )
        yield procs
    finally:
        for process in (server, guard):
            process.send_signal(signal.SIGTERM)
        for process in (server, guard):
            try:
                assert process.wait(TIMEOUT) == 0
            finally:
                if process.poll() is None:
                    process.kill()


def test_cat_and_transmission(processes: Processes) -> None:
    assert processes.cli("freq", "14074000").returncode == 0
    assert processes.cli("freq").stdout.strip() == "14074000"
    assert processes.cli("mode", "CW").returncode == 0
    assert processes.cli("mode").stdout.strip() == "CW"

    result = processes.cli("tx-test", "--seconds", "1.5")
    assert result.returncode == 0, result.stderr

    records = processes.tx_records()
    start = next(r for r in records if r["event"] == "tx_start")
    end = next(r for r in records if r["event"] == "tx_end")
    assert start["frequency_hz"] == 14_074_000
    assert str(start["source"]).startswith("cli uid=")
    assert end["reason"] == "released"


def test_status_while_idle(processes: Processes) -> None:
    out = processes.cli("status").stdout
    assert "transmitting:   no" in out
    assert "lockout:        none" in out
