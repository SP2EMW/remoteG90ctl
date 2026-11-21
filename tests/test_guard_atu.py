# SPDX-License-Identifier: GPL-3.0-or-later
"""Antenna tuning as a guard function (decisions.md D43).

A tuning transmits a carrier (1-1.5 s with a matched antenna, about 9 s with a
poor one, measured 2026-11-13) and cannot be stopped, so the guard starts one
only when a transmission could start, keeps it going until the radio's power
meter reads zero twice (at least ATU_TUNE_MIN_S, at most ATU_TUNE_MAX_S, then
a lockout), starts the next one ATU_TUNE_INTERVAL_S after the end, and counts
the real duration towards the duty cycle.
"""

from pathlib import Path

import pytest

from guard_helpers import CTRL, RADIO, Harness, hardware_config, make_config
from remoteg90ctl.civ import (
    POWER_METER_SUBCOMMAND,
    Command,
    Frame,
    Mode,
    atu_tune_request,
    read_frequency_request,
    set_frequency_request,
    set_mode_request,
    set_rf_power_request,
)
from remoteg90ctl.guard.core import (
    ATU_TUNE_INTERVAL_S,
    ATU_TUNE_MAX_S,
    ATU_TUNE_MIN_S,
)

TUNE = atu_tune_request(RADIO, CTRL)


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    return harness


def tune(h: Harness) -> dict[str, object]:
    return h.send("atu_tune", source="test", frequency_hz=21_074_000)


def meter(
    h: Harness, level: bytes, *, sender: int = RADIO, sub: int = POWER_METER_SUBCOMMAND
) -> None:
    """A meter reply arrives from the radio (the server asks every second)."""
    h.port.inject_rx(Frame(CTRL, sender, Command.READ_METER, bytes([sub]) + level).to_bytes())
    h.core.poll()


EMITTING = b"\x02\x55"  # 255: the end of the scale, as measured during tuning
ZERO = b"\x00\x00"


def finish(h: Harness) -> None:
    """The radio stops: two zero readings after the shortest tuning."""
    h.clock.advance(ATU_TUNE_MIN_S)
    meter(h, ZERO)
    meter(h, ZERO)


def test_tuning_sends_the_frame_and_is_logged(h: Harness) -> None:
    response = tune(h)
    assert response["ok"], response
    assert h.port.radio.received[-1] == TUNE
    (record,) = h.tx_events("atu_tune")
    assert record["source"] == "test"
    assert record["frequency_hz"] == 21_074_000
    assert record["max_s"] == ATU_TUNE_MAX_S
    assert not h.ptt_active  # no PTT line: the radio tunes by itself


def test_tunings_are_spaced(h: Harness) -> None:
    """From the end of the previous tuning, not its start."""
    assert tune(h)["ok"]
    finish(h)
    h.clock.advance(ATU_TUNE_INTERVAL_S - 1)
    refused = tune(h)
    assert refused["error"] == "tx_blocked"
    assert "next tuning allowed" in str(refused["message"])
    h.clock.advance(1)
    assert tune(h)["ok"]


def test_no_transmission_while_tuning(h: Harness) -> None:
    assert tune(h)["ok"]
    assert h.tx_request()["error"] == "tx_busy"
    h.clock.advance(ATU_TUNE_MIN_S + 5)  # a long tuning: time alone ends nothing
    meter(h, EMITTING)
    assert h.tx_request()["error"] == "tx_busy"
    meter(h, ZERO)
    assert h.tx_request()["error"] == "tx_busy"  # one zero is not enough
    meter(h, ZERO)
    assert h.tx_request()["ok"]


def test_a_tuning_lasts_at_least_the_shortest_time(h: Harness) -> None:
    assert tune(h)["ok"]
    h.clock.advance(1)
    meter(h, ZERO)
    meter(h, ZERO)
    assert h.tx_request()["error"] == "tx_busy"
    h.clock.advance(ATU_TUNE_MIN_S)
    meter(h, ZERO)
    meter(h, ZERO)
    assert h.tx_request()["ok"]
    (end,) = h.tx_events("atu_tune_end")
    assert end["reason"] == "meter_zero"


@pytest.mark.parametrize(
    ("sender", "sub"),
    [(0x5A, POWER_METER_SUBCOMMAND), (RADIO, 0x12)],  # another device; the SWR meter
)
def test_only_the_radios_power_meter_ends_a_tuning(h: Harness, sender: int, sub: int) -> None:
    assert tune(h)["ok"]
    h.clock.advance(ATU_TUNE_MIN_S)
    meter(h, ZERO, sender=sender, sub=sub)
    meter(h, ZERO, sender=sender, sub=sub)
    assert h.tx_request()["error"] == "tx_busy"


def test_meter_replies_still_reach_the_server(h: Harness) -> None:
    assert tune(h)["ok"]
    h.core.take_events()
    meter(h, EMITTING)
    frames = [e["frame"] for e in h.core.take_events() if e.get("event") == "civ_rx"]
    assert any(str(f).startswith(f"fefe{CTRL:02x}{RADIO:02x}1511") for f in frames)


def test_a_tuning_that_does_not_end_locks_transmit(h: Harness) -> None:
    """No zero readings within ATU_TUNE_MAX_S (still emitting, or no server
    asking): a fault, transmit locked until tx_unlock, also after a restart."""
    assert tune(h)["ok"]
    h.clock.advance(ATU_TUNE_MAX_S - 1)
    meter(h, EMITTING)
    assert h.core.lockout is None
    h.clock.advance(1)
    h.core.poll()
    assert h.core.lockout is not None
    assert h.core.lockout.reason == "atu_tune_overrun"
    (end,) = h.tx_events("atu_tune_end")
    assert end["reason"] == "overrun"
    assert end["emitted"] is True
    assert h.tx_request()["error"] == "tx_locked"
    restarted = h.new_core()
    restarted.start()
    assert restarted.lockout is not None


def test_the_real_duration_counts_towards_the_duty_cycle(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, duty_window_s=600.0, duty_max_tx_s=50.0))
    harness.core.start()
    assert tune(harness)["ok"]
    harness.clock.advance(9)  # a poorly matched antenna (measured: about 9 s)
    meter(harness, EMITTING)
    meter(harness, ZERO)
    meter(harness, ZERO)
    harness.clock.advance(ATU_TUNE_INTERVAL_S)
    # 9 s used; another tuning needs room for the longest one (30 s): 39 <= 50.
    assert tune(harness)["ok"]


def test_a_restart_during_a_tuning_keeps_the_longest_one_counted(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, duty_window_s=600.0, duty_max_tx_s=50.0))
    harness.core.start()
    assert tune(harness)["ok"]
    harness.core = harness.new_core()  # the end of this tuning is never seen
    harness.core.start()
    harness.clock.advance(ATU_TUNE_MAX_S + ATU_TUNE_INTERVAL_S)
    refused = tune(harness)  # 30 s counted, 30 more would exceed 50 s
    assert refused["error"] == "tx_blocked"
    assert "duty" in str(refused["message"])


def test_no_tuning_during_a_transmission(h: Harness) -> None:
    h.start_tx()
    assert tune(h)["error"] == "tx_busy"
    assert TUNE not in h.port.radio.received


def test_tuning_needs_room_for_the_longest_tuning(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, duty_window_s=600.0, duty_max_tx_s=20.0))
    harness.core.start()
    refused = tune(harness)  # it cannot be stopped: up to 30 s must fit
    assert refused["error"] == "tx_blocked"
    assert "duty" in str(refused["message"])


def test_tuning_refused_when_locked(h: Harness) -> None:
    h.start_tx()
    h.clock.advance(181)
    h.core.poll()  # hard limit: lockout
    assert tune(h)["error"] == "tx_locked"


def test_tuning_needs_its_own_switch_on_hardware(tmp_path: Path) -> None:
    """allow_transmit does not allow tuning, and allow_atu_tune does not allow PTT."""
    harness = Harness(hardware_config(tmp_path, allow_transmit=True))
    harness.core.start()
    refused = tune(harness)
    assert refused["error"] == "tx_blocked"
    assert "allow_atu_tune" in str(refused["message"])
    (tmp_path / "b").mkdir()
    tuner_only = Harness(hardware_config(tmp_path / "b", allow_atu_tune=True))
    tuner_only.core.start()
    assert tune(tuner_only)["ok"]
    finish(tuner_only)
    refused_tx = tuner_only.tx_request()
    assert refused_tx["error"] == "tx_blocked"
    assert "allow_transmit" in str(refused_tx["message"])


def test_tuning_needs_a_confirmed_radio(tmp_path: Path) -> None:
    harness = Harness(make_config(tmp_path, identity=None))
    harness.core.start()
    refused = tune(harness)
    assert refused["error"] == "tx_blocked"
    assert "identity" in str(refused["message"])


def test_tuning_refused_without_a_log(h: Harness) -> None:
    h.config.guard.tx_log_path.unlink()
    h.config.guard.tx_log_path.mkdir()  # a directory cannot be opened for append
    refused = tune(h)
    assert refused["error"] == "tx_log_failed"
    assert TUNE not in h.port.radio.received


@pytest.mark.parametrize("fields", [{}, {"source": ""}, {"source": "x", "frequency_hz": "1"}])
def test_tuning_request_is_validated(h: Harness, fields: dict[str, object]) -> None:
    assert h.send("atu_tune", **fields)["error"] == "bad_request"


@pytest.mark.parametrize(
    "frame",
    [
        set_frequency_request(RADIO, CTRL, 14_074_000),
        set_mode_request(RADIO, CTRL, Mode.LSB),
        set_rf_power_request(RADIO, CTRL, 255),
    ],
)
def test_state_changes_are_refused_while_tuning(h: Harness, frame: bytes) -> None:
    """The radio transmits a carrier while it tunes (code review 2026-11-10, point 3):
    like a lease, that stops changes of its frequency, mode and power."""
    assert tune(h)["ok"]
    written = len(h.port.written)
    assert h.send("civ", frame=frame.hex())["error"] == "tx_active"
    assert h.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())["ok"]  # reads go on
    assert h.port.written[written:] == [read_frequency_request(RADIO, CTRL)]
    h.clock.advance(ATU_TUNE_MIN_S + 6)
    assert h.send("civ", frame=frame.hex())["error"] == "tx_active"  # still emitting
    meter(h, ZERO)
    meter(h, ZERO)
    assert h.send("civ", frame=frame.hex())["ok"]  # the tuning is over


# The page warns when the radio did not tune (owner's decision 2026-11-14): with
# its screen blanked the G90 acknowledges the command and tunes only when the
# screen comes on (section 8 item 13).


def _end_events(h: Harness) -> list[dict[str, object]]:
    return [e for e in h.core.take_events() if e.get("event") == "atu_tune_end"]


def test_the_end_of_a_tuning_tells_whether_the_radio_emitted(h: Harness) -> None:
    assert tune(h)["ok"]
    meter(h, EMITTING)
    finish(h)
    (end,) = _end_events(h)
    assert (end["reason"], end["emitted"]) == ("meter_zero", True)
    h.clock.advance(ATU_TUNE_INTERVAL_S)
    assert tune(h)["ok"]
    finish(h)  # the meter never showed power: the radio did not tune
    (end,) = _end_events(h)
    assert (end["reason"], end["emitted"]) == ("meter_zero", False)
    assert h.tx_request()["ok"]  # a warning only: nothing is locked


def test_an_overrun_also_tells_its_end(h: Harness) -> None:
    assert tune(h)["ok"]
    meter(h, EMITTING)
    h.clock.advance(ATU_TUNE_MAX_S)
    h.core.poll()
    names = [e.get("event") for e in h.core.take_events()]
    assert "atu_tune_overrun" in names
    assert "atu_tune_end" in names
