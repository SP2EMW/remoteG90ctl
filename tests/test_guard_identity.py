# SPDX-License-Identifier: GPL-3.0-or-later
"""Radio identity check on port open (decisions.md D32).

Transmitting is blocked until the radio answers ``1D 19`` with the configured
identity; CAT keeps working either way.
"""

from pathlib import Path

from guard_helpers import RADIO, FakeWallClock, Harness, make_config
from remoteg90ctl.civ import read_frequency_request
from remoteg90ctl.config import Config
from remoteg90ctl.guard.core import GuardCore, IdentityState
from remoteg90ctl.guard.txlog import TxLog
from remoteg90ctl.sim import SimulatedRadio, SimulatedSerialPort

FAST_TIMEOUT_S = 0.05


def started(config: Config, radio: SimulatedRadio | None = None) -> Harness:
    harness = Harness(config)
    if radio is not None:
        harness.port = SimulatedSerialPort(radio, clock=harness.clock)
        harness.core = harness.new_core()
    harness.core.start()
    return harness


def identity(harness: Harness) -> dict[str, object]:
    value = harness.send("status")["identity"]
    assert isinstance(value, dict)
    return value


def assert_tx_blocked_but_cat_works(harness: Harness, reason: str) -> None:
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert f"radio identity {reason}" in response["message"]
    assert not harness.ptt_active
    assert harness.send("civ", frame=read_frequency_request(RADIO, 0xE0).hex())["ok"]


def test_matching_identity_allows_transmitting(tmp_path: Path) -> None:
    harness = started(make_config(tmp_path))
    assert identity(harness) == {
        "state": "confirmed",
        "detail": "radio answered 190090",
        "reply": "190090",
    }
    assert harness.tx_request()["ok"]


def test_identity_reply_is_not_forwarded_to_the_server(tmp_path: Path) -> None:
    harness = started(make_config(tmp_path))
    assert harness.core.take_events() == []


def test_other_identity_blocks_transmitting(tmp_path: Path) -> None:
    harness = started(make_config(tmp_path, identity=b"\x00\x91"))
    assert identity(harness)["state"] == "mismatch"
    assert "expected 0091" in str(identity(harness)["detail"])
    assert_tx_blocked_but_cat_works(harness, "mismatch")


def test_ng_reply_blocks_transmitting(tmp_path: Path) -> None:
    """Firmware before 1.8 does not know the query."""
    harness = started(make_config(tmp_path), SimulatedRadio(RADIO, identity=None))
    assert identity(harness)["state"] == "rejected"
    assert_tx_blocked_but_cat_works(harness, "rejected")


def test_no_reply_blocks_transmitting(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    harness = Harness(config)
    harness.port.set_unresponsive(True)
    txlog = TxLog(config.guard.tx_log_path, FakeWallClock(harness.clock))
    harness.core = GuardCore(
        config,
        lambda: harness.port,
        clock=harness.clock,
        txlog=txlog,
        identity_timeout_s=FAST_TIMEOUT_S,
    )
    harness.core.start()
    assert harness.core.port_state.value == "open"
    assert identity(harness)["state"] == "no_reply"
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert not harness.ptt_active


def test_unconfigured_identity_reports_the_reply(tmp_path: Path) -> None:
    """The value to put into radio.identity can be read from status and the log."""
    harness = started(make_config(tmp_path, identity=None))
    state = identity(harness)
    assert state["state"] == "not_configured"
    assert state["reply"] == "190090"
    assert "'190090'" in str(state["detail"])
    assert_tx_blocked_but_cat_works(harness, "not_configured")


def test_identity_is_checked_again_after_port_loss(tmp_path: Path) -> None:
    harness = started(make_config(tmp_path))
    assert identity(harness)["state"] == "confirmed"
    harness.port.disconnect()
    harness.core.poll()
    assert identity(harness)["state"] == "not_checked"
    # Another device answers when the port comes back.
    harness.port.radio.identity = b"\x00\x42"
    harness.port.reconnect()
    assert not harness.send("port_open").get("error")
    assert identity(harness)["state"] == "mismatch"
    assert harness.tx_request()["error"] == "tx_blocked"


def test_identity_state_enum_matches_status_values() -> None:
    assert {s.value for s in IdentityState} == {
        "not_checked",
        "confirmed",
        "mismatch",
        "rejected",
        "no_reply",
        "not_configured",
    }
