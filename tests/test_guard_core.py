# SPDX-License-Identifier: GPL-3.0-or-later
"""GuardCore: lease, hard limit, lockout, port loss and CI-V forwarding."""

import logging
from pathlib import Path

import pytest

from guard_helpers import CTRL, IDENTITY_QUERY, RADIO, Harness, hardware_config, make_config
from remoteg90ctl import __version__
from remoteg90ctl.civ import (
    Mode,
    read_frequency_request,
    set_frequency_request,
    set_mode_request,
    set_rf_power_request,
)
from remoteg90ctl.config import PttLine
from remoteg90ctl.guard.core import PortState
from remoteg90ctl.guard.state import LOCKOUT_FILE, RUNNING_FILE
from remoteg90ctl.serial_port import INACTIVE, LineState
from remoteg90ctl.sim import SimulatedRadio, SimulatedSerialPort


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    return harness


# Start-up


def test_ptt_inactive_after_start(h: Harness) -> None:
    assert h.core.port_state is PortState.OPEN
    assert not h.ptt_active
    assert h.port.active_intervals("dtr") == []
    assert h.port.active_intervals("rts") == []


def test_ptt_never_active_when_port_defaults_to_asserted(tmp_path: Path) -> None:
    port = SimulatedSerialPort(SimulatedRadio(RADIO))
    # Without the guard's sequence, opening this port would assert both lines.
    assert port._requested is None
    harness = Harness(make_config(tmp_path), port)
    harness.core.start()
    assert port.active_intervals("dtr") == []
    assert port.active_intervals("rts") == []


def test_guard_start_is_logged(h: Harness) -> None:
    (record,) = h.tx_events("guard_start")
    assert record["unclean_previous_shutdown"] is False


def test_status(h: Harness) -> None:
    status = h.send("status")
    assert status["ok"]
    assert status["version"] == __version__
    assert status["port"] == "open"
    assert status["tx"] is None
    assert status["blockers"] == []
    assert status["limits"] == {
        "lease_renew_interval_s": 0.5,
        "lease_timeout_s": 2.0,
        "hard_limit_s": 180.0,
        "duty_window_s": 600.0,
        "duty_max_tx_s": 300.0,
    }
    assert status["duty"] == {"used_s": 0.0, "available_in_s": 0.0}


# Lease


def test_tx_request_asserts_configured_line(h: Harness) -> None:
    response = h.tx_request()
    assert response["ok"]
    assert response["hard_limit_s"] == 180.0
    assert h.port.lines == LineState(dtr=False, rts=True)


def test_dtr_line(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, ptt_line=PttLine.DTR))
    harness.core.start()
    harness.start_tx()
    assert harness.port.lines == LineState(dtr=True, rts=False)


def test_release(h: Harness) -> None:
    lease = h.start_tx()
    h.clock.advance(1.25)
    assert h.send("tx_release", lease=lease)["ok"]
    assert not h.ptt_active
    (end,) = h.tx_events("tx_end")
    assert end["reason"] == "released"
    assert end["duration_s"] == 1.25
    assert end["source"] == "test"
    assert end["frequency_hz"] == 7_074_000
    assert end["started"]


def test_renewal_keeps_lease(h: Harness) -> None:
    lease = h.start_tx()
    for _ in range(20):
        h.clock.advance(0.5)
        assert h.send("tx_renew", lease=lease)["ok"]
        h.core.poll()
    assert h.ptt_active


def test_missing_renewal_releases_after_timeout(h: Harness) -> None:
    lease = h.start_tx()
    h.clock.advance(1.9)
    h.core.poll()
    assert h.ptt_active
    h.clock.advance(0.1)
    h.core.poll()
    assert not h.ptt_active
    (end,) = h.tx_events("tx_end")
    assert end["reason"] == "lease_expired"
    assert h.send("tx_renew", lease=lease)["error"] == "unknown_lease"
    assert h.core.take_events()[-1] == {
        "v": 1,
        "event": "tx_ended",
        "lease": lease,
        "reason": "lease_expired",
    }


def test_late_renewal_does_not_revive_lease(h: Harness) -> None:
    lease = h.start_tx()
    h.clock.advance(2.5)  # no poll in between: the request itself must expire it
    assert h.send("tx_renew", lease=lease)["error"] == "unknown_lease"
    assert not h.ptt_active


def test_hard_limit_releases_despite_renewals_and_locks(h: Harness) -> None:
    lease = h.start_tx()
    for _ in range(int(180 / 0.5) - 1):
        h.clock.advance(0.5)
        assert h.send("tx_renew", lease=lease)["ok"]
        h.core.poll()
    assert h.ptt_active
    h.clock.advance(0.5)
    h.core.poll()
    assert not h.ptt_active
    assert h.tx_events("tx_end")[0]["reason"] == "hard_limit"
    assert h.core.lockout is not None
    response = h.tx_request()
    assert response["error"] == "tx_locked"
    assert not h.ptt_active
    # Waiting does not help.
    h.clock.advance(3600)
    h.core.poll()
    assert h.tx_request()["error"] == "tx_locked"
    unlock = h.send("tx_unlock")
    assert unlock["ok"]
    assert unlock["was_locked"] is True
    assert h.tx_events("tx_unlock")[0]["previous_reason"] == "hard_limit"
    assert h.tx_request()["ok"]


def test_hard_limit_persists_lockout_before_release(h: Harness) -> None:
    h.start_tx()
    lockout_file = h.config.guard.state_dir / LOCKOUT_FILE
    seen: list[bool] = []
    original = h.port.set_lines

    def spy(state: LineState) -> None:
        if state == INACTIVE:
            seen.append(lockout_file.exists())
        original(state)

    h.port.set_lines = spy  # type: ignore[method-assign]
    h.clock.advance(180)
    h.core.poll()
    assert seen
    assert seen[0] is True


def test_shorter_hard_limit(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, hard_limit_s=10))
    harness.core.start()
    harness.start_tx()
    harness.clock.advance(10)
    harness.core.poll()
    assert not harness.ptt_active


def test_second_request_rejected(h: Harness) -> None:
    h.start_tx()
    assert h.tx_request("other")["error"] == "tx_busy"
    assert h.tx_request("test")["error"] == "tx_busy"  # same source too (D24.5)
    assert h.ptt_active
    assert len(h.tx_events("tx_start")) == 1


def test_every_tx_log_record_carries_the_version(h: Harness) -> None:
    lease = h.start_tx()
    h.send("tx_release", lease=lease)
    records = h.tx_log()
    assert {r["event"] for r in records} >= {"tx_start", "tx_end"}
    assert all(r["version"] == __version__ for r in records)


def test_release_with_wrong_lease(h: Harness) -> None:
    h.start_tx()
    assert h.send("tx_release", lease="nope")["error"] == "unknown_lease"
    assert h.ptt_active


def test_client_disconnect_releases_immediately(h: Harness) -> None:
    h.start_tx()
    h.core.client_disconnected()
    assert not h.ptt_active
    assert h.tx_events("tx_end")[0]["reason"] == "client_disconnected"


def test_unlock_refused_during_tx(h: Harness) -> None:
    h.start_tx()
    assert h.send("tx_unlock")["error"] == "tx_busy"


def test_unlock_when_not_locked(h: Harness) -> None:
    assert h.send("tx_unlock")["was_locked"] is False


@pytest.mark.parametrize(
    "fields",
    [
        {"source": "", "frequency_hz": 1},
        {"source": "x" * 201, "frequency_hz": 1},
        {"source": 5, "frequency_hz": 1},
        {"source": "a", "frequency_hz": -1},
        {"source": "a", "frequency_hz": True},
        {"source": "a", "frequency_hz": "7074000"},
    ],
)
def test_tx_request_field_validation(h: Harness, fields: dict[str, object]) -> None:
    assert h.send("tx_request", **fields)["error"] == "bad_request"
    assert not h.ptt_active


# Transmit enablement


def test_hardware_without_allow_transmit_refused(tmp_path: Path) -> None:
    harness = Harness(hardware_config(tmp_path, allow_transmit=False))
    harness.core.start()
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert "allow_transmit" in response["message"]
    assert not harness.ptt_active
    assert harness.tx_events("tx_start") == []


def test_hardware_with_allow_transmit(tmp_path: Path) -> None:
    harness = Harness(hardware_config(tmp_path, allow_transmit=True))
    harness.core.start()
    assert harness.tx_request()["ok"]


def test_simulator_ignores_allow_transmit(h: Harness) -> None:
    assert h.config.safety.allow_transmit is False
    assert h.tx_request()["ok"]


@pytest.mark.parametrize("hardware", [True, False])
def test_ptt_line_civ_starts_blocked(tmp_path: Path, hardware: bool) -> None:
    config = (
        hardware_config(tmp_path, ptt_line=PttLine.CIV, allow_transmit=True)
        if hardware
        else make_config(tmp_path, ptt_line=PttLine.CIV)
    )
    harness = Harness(config)
    harness.core.start()
    assert harness.core.port_state is PortState.OPEN
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert "CI-V" in response["message"]
    assert harness.port.active_intervals("rts") == []
    assert harness.port.active_intervals("dtr") == []


# Durable records before PTT (D24.8, D24.9)


def test_tx_refused_when_tx_log_unwritable(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    harness.config.guard.tx_log_path.unlink()
    harness.config.guard.tx_log_path.mkdir()  # a directory cannot be opened for append
    response = harness.tx_request()
    assert response["error"] == "tx_log_failed"
    assert harness.port.active_intervals("rts") == []


def test_tx_refused_when_state_unwritable(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    state_dir = harness.config.guard.state_dir
    for child in state_dir.iterdir():
        child.unlink()
    state_dir.rmdir()
    response = harness.tx_request()
    assert response["error"] == "state_write_failed"
    assert harness.port.active_intervals("rts") == []


def test_marker_records_active_tx(h: Harness) -> None:
    marker = h.config.guard.state_dir / RUNNING_FILE
    lease = h.start_tx()
    assert '"tx_active": true' in marker.read_text()
    h.send("tx_release", lease=lease)
    assert '"tx_active": false' in marker.read_text()


# Line confirmation (D24.10)


def test_line_mismatch_after_assert(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    harness.port.read_lines = lambda: INACTIVE  # type: ignore[method-assign]
    response = harness.tx_request()
    assert response["error"] == "port_unavailable"
    assert not harness.ptt_active
    assert harness.core.port_state is PortState.LOST
    assert harness.tx_events("tx_end")[0]["reason"] == "line_mismatch"
    assert harness.tx_request()["error"] == "tx_blocked"


def test_line_change_during_tx(h: Harness) -> None:
    h.start_tx()
    h.port.set_lines(LineState(dtr=True, rts=True))  # something else touched the lines
    h.core.poll()
    assert not h.ptt_active
    assert h.core.port_state is PortState.LOST
    assert h.tx_events("tx_end")[0]["reason"] == "line_mismatch"


def test_lines_not_confirmed_at_open(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path))
    harness.port.read_lines = lambda: LineState(dtr=False, rts=True)  # type: ignore[method-assign]
    harness.core.start()
    assert harness.core.port_state is PortState.UNAVAILABLE
    assert harness.tx_request()["error"] == "tx_blocked"


# Port loss (AGENTS.md rule 14, D24.1)


def test_port_loss_during_tx(h: Harness) -> None:
    h.start_tx()
    h.port.disconnect()
    h.core.poll()
    assert not h.ptt_active
    assert h.core.port_state is PortState.LOST
    assert h.tx_events("tx_end")[0]["reason"] == "port_lost"
    assert h.tx_events("port_lost")
    assert {"v": 1, "event": "port_lost", "message": "simulated device was disconnected"} in (
        h.core.take_events()
    )
    # No automatic reopen, even after the device is back.
    h.port.reconnect()
    for _ in range(10):
        h.clock.advance(1)
        h.core.poll()
    assert h.core.port_state is PortState.LOST
    assert h.tx_request()["error"] == "tx_blocked"
    # Explicit reopen restores transmit ability.
    assert h.send("port_open")["ok"]
    assert h.send("status")["port"] == "open"
    assert h.tx_request()["ok"]


def test_port_open_fails_while_device_absent(h: Harness) -> None:
    h.port.disconnect()
    h.core.poll()
    response = h.send("port_open")
    assert response["error"] == "port_unavailable"
    assert h.core.port_state is not PortState.OPEN
    assert h.tx_events("port_open")[0]["result"] == "unavailable"


def test_port_missing_at_start(tmp_path: Path) -> None:
    port = SimulatedSerialPort(SimulatedRadio(RADIO))
    port.disconnect()
    harness = Harness(make_config(tmp_path), port)
    harness.core.start()  # does not raise: the guard starts with TX blocked (D24.2)
    assert harness.core.port_state is PortState.UNAVAILABLE
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert "serial port unavailable" in response["message"]
    port.reconnect()
    assert harness.send("port_open")["ok"]


# CI-V forwarding


def test_civ_forwarded_and_reply_returned(h: Harness) -> None:
    request = read_frequency_request(RADIO, CTRL)
    assert h.send("civ", frame=request.hex())["ok"]
    h.core.poll()
    events = h.core.take_events()
    # The echo of our own frame is dropped; only the radio's reply is forwarded.
    assert events == [{"v": 1, "event": "civ_rx", "frame": "fefee088030040070700fd"}]


def test_civ_without_echo(tmp_path: Path) -> None:
    port = SimulatedSerialPort(SimulatedRadio(RADIO), echo=False)
    harness = Harness(make_config(tmp_path), port)
    harness.core.start()
    harness.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())
    harness.core.poll()
    assert len(harness.core.take_events()) == 1


def test_broadcast_forwarded_and_garbage_logged(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    radio = h.port.radio
    h.port.inject_rx(b"\x01\x02" + radio.broadcast_frequency())
    with caplog.at_level(logging.WARNING):
        h.core.poll()
    assert [e["event"] for e in h.core.take_events()] == ["civ_rx"]
    assert "civ_rx_garbage" in caplog.text


def test_frames_from_other_devices_not_forwarded(h: Harness) -> None:
    h.port.inject_rx(bytes.fromhex("fefee070fbfd"))  # wrong source address
    h.core.poll()
    assert h.core.take_events() == []


def test_civ_not_on_allowlist(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        response = h.send("civ", frame="fefe88e01c0001fd")  # 0x1C 0x00: Icom PTT
    assert response["error"] == "command_not_allowed"
    assert "civ_rejected" in caplog.text
    assert h.port.written == [IDENTITY_QUERY]  # nothing after the guard's own query
    assert not h.ptt_active


@pytest.mark.parametrize(
    "frame",
    [
        set_frequency_request(RADIO, CTRL, 14_074_000).hex(),
        set_mode_request(RADIO, CTRL, Mode.LSB).hex(),
        set_rf_power_request(RADIO, CTRL, 255).hex(),
    ],
)
def test_state_changes_refused_during_tx(h: Harness, frame: str) -> None:
    h.start_tx()
    assert h.send("civ", frame=frame)["error"] == "tx_active"
    assert h.port.written == [IDENTITY_QUERY]  # nothing after the guard's own query


def test_reads_allowed_during_tx(h: Harness) -> None:
    h.start_tx()
    assert h.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())["ok"]


def test_civ_write_failure_marks_port_lost(h: Harness) -> None:
    h.port.disconnect()
    response = h.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())
    assert response["error"] == "port_unavailable"
    assert h.core.port_state is PortState.LOST


# Shutdown


def test_clean_shutdown_releases_and_removes_marker(h: Harness) -> None:
    h.start_tx()
    h.core.shutdown(clean=True)
    assert not h.ptt_active
    assert not h.port.is_open
    assert h.tx_events("tx_end")[0]["reason"] == "shutdown"
    assert not (h.config.guard.state_dir / RUNNING_FILE).exists()


def test_error_shutdown_keeps_marker_and_locks_next_start(h: Harness) -> None:
    h.start_tx()
    h.core.emergency_lower()
    assert not h.ptt_active
    h.core.shutdown(clean=False)
    assert h.tx_events("tx_end")[0]["reason"] == "error"
    restarted = h.new_core()
    restarted.start()
    assert restarted.lockout is not None


def test_emergency_lower_never_raises(h: Harness) -> None:
    h.start_tx()
    h.port.disconnect()
    h.core.emergency_lower()  # port gone: must not raise
    assert not h.ptt_active


def test_poll_after_emergency_lower_records_shutdown(h: Harness) -> None:
    # Signal path: lines dropped by the handler must not be mistaken for a line fault.
    h.start_tx()
    h.core.emergency_lower()
    h.core.poll()
    assert not h.core.tx_active
    assert h.tx_events("tx_end")[0]["reason"] == "shutdown"
    assert h.core.port_state is PortState.OPEN


def test_no_tx_after_emergency_lower(h: Harness) -> None:
    h.core.emergency_lower()
    assert h.tx_request()["error"] == "tx_blocked"
    assert h.port.active_intervals("rts") == []


# Lines activated outside the guard (D25.1)


def test_foreign_line_activity_while_idle(h: Harness) -> None:
    h.clock.advance(1)
    h.port.set_lines(LineState(dtr=False, rts=True))  # e.g. another process opened the device
    h.core.poll()
    assert not h.ptt_active
    assert h.port.is_open  # kept open so the guard can keep the lines low
    assert h.core.port_state is PortState.LINE_FAULT
    assert h.tx_events("line_fault")
    assert [e["event"] for e in h.core.take_events()] == ["line_fault"]
    response = h.tx_request()
    assert response["error"] == "tx_blocked"
    assert "line_fault" in response["message"]


def test_foreign_activity_is_lowered_every_iteration(h: Harness) -> None:
    for _ in range(3):
        h.port.set_lines(LineState(dtr=True, rts=False))
        h.core.poll()
        assert not h.ptt_active
    assert len(h.tx_events("line_fault")) == 1  # reported once


def test_port_open_after_line_fault(h: Harness) -> None:
    h.port.set_lines(LineState(dtr=False, rts=True))
    h.core.poll()
    assert h.send("port_open")["ok"]
    assert h.send("status")["port"] == "open"
    assert h.tx_request()["ok"]


def test_cat_still_works_during_line_fault(h: Harness) -> None:
    h.port.set_lines(LineState(dtr=False, rts=True))
    h.core.poll()
    assert h.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())["ok"]


def test_idle_lines_unreadable_marks_port_lost(h: Harness) -> None:
    h.port.disconnect()
    h.core.poll()
    assert h.core.port_state is PortState.LOST


# Stop signal while keying (D25.3)


def test_stop_signal_during_keying_releases_at_once(h: Harness) -> None:
    original = h.port.set_lines

    def signal_arrives(state: LineState) -> None:
        original(state)
        if state.any_active:
            h.core.emergency_lower()  # what the SIGTERM handler does

    h.port.set_lines = signal_arrives  # type: ignore[method-assign]
    response = h.tx_request()
    assert not response["ok"]
    assert not h.ptt_active
    assert not h.core.tx_active
    assert h.tx_events("tx_end")[0]["reason"] == "shutdown"
    assert h.core.port_state is PortState.OPEN
