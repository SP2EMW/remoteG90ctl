# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI: commands against a running server, interruption, and its isolation from the guard."""

import argparse
import asyncio
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from guard_helpers import hardware_config, make_config
from import_graph import PACKAGE, SRC, closure
from remoteg90ctl import __version__, cli
from remoteg90ctl.guard.state import HARD_LIMIT, Lockout, LockoutStore
from server_helpers import System, async_wait_for, running_system

RENEW_S = 0.2
TIMEOUT_S = 0.6


@pytest_asyncio.fixture
async def system(tmp_path: Path) -> AsyncIterator[System]:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    async with running_system(config) as running:
        yield running


async def run_cli(system: System, *args: str) -> int:
    argv = ["--socket", str(system.config.server.socket_path), *args]
    return await asyncio.to_thread(cli.main, argv)


# Structure (AGENTS.md rule 5)


def test_cli_has_no_path_to_the_guard() -> None:
    reachable, _external = closure(f"{PACKAGE}.cli")
    forbidden = (f"{PACKAGE}.guard", f"{PACKAGE}.server", f"{PACKAGE}.config")
    assert not [m for m in reachable if m.startswith(forbidden)]
    source = (SRC / PACKAGE / "cli.py").read_text()
    assert "guard.sock" not in source
    assert "socket_path" not in source  # does not read the configuration
    assert cli.DEFAULT_SOCKET.name == "server.sock"


# Commands


async def test_status(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "status") == 0
    out = capsys.readouterr().out
    assert "backend:        simulator" in out
    assert f"versions:       cli {__version__}, server {__version__}, guard {__version__}" in out
    assert "WARNING" not in out
    assert "serial port:    open" in out
    assert "transmitting:   no" in out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"remoteg90ctl {__version__}"


async def test_freq_and_mode(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "freq", "14074000") == 0
    assert await run_cli(system, "freq") == 0
    assert await run_cli(system, "mode", "LSB") == 0
    assert await run_cli(system, "mode") == 0
    assert capsys.readouterr().out.split() == ["14074000", "LSB"]


async def test_power(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "power") == 0
    fields = capsys.readouterr().out.split()
    assert fields[:2] == ["meter", "0"]
    assert fields[3].endswith("15110000fd")
    assert fields[4:6] == ["setting", "255"]
    assert fields[8:10] == ["swr", "0"]


async def test_atu(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "atu", "on") == 0
    assert await run_cli(system, "atu", "state") == 0
    assert await run_cli(system, "atu", "tune") == 0
    assert capsys.readouterr().out.split() == ["on"]
    assert system.harness.port.radio.tunings == 1


async def test_invalid_mode(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "mode", "SSB") == 1
    assert "invalid_value" in capsys.readouterr().err


async def test_tx_test(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "tx-test", "--seconds", "1") == 0
    assert not system.ptt_active
    assert system.core.guard_renewals >= 3
    (end,) = system.harness.tx_events("tx_end")
    assert end["reason"] == "released"
    assert 0.9 <= end["duration_s"] < 1.5
    assert "done after" in capsys.readouterr().out


async def test_tx_test_rejects_seconds_over_hard_limit(system: System) -> None:
    assert await run_cli(system, "tx-test", "--seconds", "181") == 2
    assert system.harness.tx_events("tx_start") == []


async def test_tx_test_reports_system_end(
    system: System, capsys: pytest.CaptureFixture[str]
) -> None:
    task = asyncio.create_task(run_cli(system, "tx-test", "--seconds", "10"))
    await async_wait_for(lambda: system.ptt_active)
    system.harness.port.disconnect()
    assert await task == 1
    assert not system.ptt_active
    assert "transmission ended by the system: port_lost" in capsys.readouterr().err


async def test_tx_unlock_and_port_open(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    assert await run_cli(system, "tx-unlock") == 0
    assert await run_cli(system, "port-open") == 0
    out = capsys.readouterr().out
    assert "transmit was not locked" in out
    assert "serial port open" in out


async def test_hardware_tx_test_requires_dummy_load_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = hardware_config(
        tmp_path, allow_transmit=True, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S
    )
    async with running_system(config) as system:
        assert await run_cli(system, "tx-test", "--seconds", "0.5") == 2
        assert system.harness.tx_events("tx_start") == []
        assert "--dummy-load-connected" in capsys.readouterr().err
        assert await run_cli(system, "tx-test", "--seconds", "0.5", "--dummy-load-connected") == 0
        assert "WARNING" in capsys.readouterr().err


def test_no_server(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--socket", str(tmp_path / "missing.sock"), "status"]) == 2
    assert "cannot connect to the server" in capsys.readouterr().err


# Interruption


def _start_tx_test(system: System, seconds: str = "30") -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "remoteg90ctl.cli",
            "--socket",
            str(system.config.server.socket_path),
            "tx-test",
            "--seconds",
            seconds,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def test_killed_cli_releases_tx_immediately(system: System) -> None:
    process = _start_tx_test(system)
    try:
        await async_wait_for(lambda: system.ptt_active, timeout=10)
        process.kill()  # SIGKILL: no chance to send tx_release
        elapsed = await async_wait_for(lambda: not system.ptt_active)
        assert elapsed < TIMEOUT_S  # released on disconnect, not by timeout
    finally:
        process.kill()
        process.wait()


async def test_ctrl_c_releases_tx(system: System) -> None:
    process = _start_tx_test(system)
    try:
        await async_wait_for(lambda: system.ptt_active, timeout=10)
        process.send_signal(signal.SIGINT)
        assert await asyncio.to_thread(process.wait, 5) == 130
        assert not system.ptt_active
        await async_wait_for(lambda: bool(system.harness.tx_events("tx_end")))
        assert system.harness.tx_events("tx_end")[0]["reason"] == "released"
    finally:
        process.kill()
        process.wait()


async def test_stopped_cli_loses_tx(system: System) -> None:
    """A frozen client (SIGSTOP: connection open, no renewals) loses TX by timeout."""
    process = _start_tx_test(system)
    try:
        await async_wait_for(lambda: system.ptt_active, timeout=10)
        process.send_signal(signal.SIGSTOP)
        elapsed = await async_wait_for(lambda: not system.ptt_active, timeout=TIMEOUT_S + 1)
        assert elapsed <= TIMEOUT_S + 0.3
    finally:
        process.kill()
        process.wait()


async def test_server_shutdown_during_tx_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    async with running_system(config) as system:
        task = asyncio.create_task(run_cli(system, "tx-test", "--seconds", "10"))
        await async_wait_for(lambda: system.ptt_active)
    assert await task == 1
    assert not system.ptt_active
    err = capsys.readouterr().err
    assert "release failed" not in err
    assert "the transmission was ended by the system (reason: server_shutdown)" in err


class _EndedBeforeRenewal:
    """Server that ended the lease just before a renewal arrived.

    Reproduces the order seen when the server shuts down during ``tx-test``: the
    renewal is answered with ``unknown_lease`` and the ``tx_ended`` event with the
    reason arrives only afterwards (or, with ``send_event=False``, never).
    """

    def __init__(self, *, send_event: bool) -> None:
        self.events: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self._send_event = send_event
        self._refused = False

    def request(self, kind: str, **_fields: object) -> dict[str, Any]:
        self.sent.append(kind)
        match kind:
            case "status":
                return {
                    "ok": True,
                    "backend": "simulator",
                    "guard": {"limits": {"hard_limit_s": 180}},
                }
            case "tx_request":
                return {"ok": True, "lease": "L1", "lease_renew_interval_s": 0.02}
            case "tx_renew":
                self._refused = True
                return {"ok": False, "error": "unknown_lease", "message": "no active lease"}
        raise AssertionError(f"unexpected request {kind}")

    def wait_events(self, _seconds: float) -> None:
        if self._refused and self._send_event:
            self.events.append({"event": "tx_ended", "lease": "L1", "reason": "server_shutdown"})


@pytest.mark.parametrize(
    ("send_event", "message"),
    [
        (True, "transmission ended by the system: server_shutdown"),
        (False, "renewal refused: unknown_lease"),
    ],
)
def test_tx_test_reason_arrives_after_refused_renewal(send_event: bool, message: str) -> None:
    conn = _EndedBeforeRenewal(send_event=send_event)
    args = argparse.Namespace(seconds=10.0, dummy_load_connected=False, antenna_low_power=False)
    with pytest.raises(cli.CliError, match=message):
        cli.cmd_tx_test(cast(cli.ServerConnection, conn), args)
    assert "tx_release" not in conn.sent


async def test_tx_test_refuses_locked_before_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    LockoutStore(config.guard.state_dir).save(Lockout(HARD_LIMIT, "2026-10-31T00:00:00+00:00"))
    async with running_system(config) as system:
        assert await run_cli(system, "tx-test", "--seconds", "1") == 1
        assert not system.harness.tx_events("tx_start")
    err = capsys.readouterr().err
    assert "transmit locked: hard_limit" in err
    assert "will TRANSMIT" not in err


async def test_status_lists_lockout_as_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    LockoutStore(config.guard.state_dir).save(Lockout(HARD_LIMIT, "2026-10-31T00:00:00+00:00"))
    async with running_system(config) as system:
        assert await run_cli(system, "status") == 0
    out = capsys.readouterr().out
    assert "TX blocked by:  lockout hard_limit (tx-unlock)" in out
