# SPDX-License-Identifier: GPL-3.0-or-later
"""Transmissions seen on the radio's power meter (decisions.md D33), and watts."""

from pathlib import Path
from typing import Any

import pytest

from guard_helpers import make_config
from remoteg90ctl.civ import CivParseError, g90_level_from_bytes, g90_level_to_bytes
from remoteg90ctl.config import parse_config
from remoteg90ctl.server.core import ServerCore
from remoteg90ctl.server.power import meter_to_watts, setting_to_watts
from server_helpers import async_wait_for, running_system

# Measured on the owner's G90 (2026-11-03): the meter ends its scale near 3 W.
POINTS = ((0, 0.0), (171, 1.0), (241, 2.0))


@pytest.mark.parametrize(
    ("value", "watts", "above"),
    [(0, 0.0, False), (171, 1.0, False), (206, 1.5, False), (241, 2.0, False), (255, 2.0, True)],
)
def test_meter_to_watts_interpolates_without_extending_the_scale(
    value: int, watts: float, above: bool
) -> None:
    result, beyond = meter_to_watts(POINTS, value)
    assert result == pytest.approx(watts)
    assert beyond is above


def test_no_watts_without_calibration() -> None:
    assert meter_to_watts((), 200) == (None, False)


@pytest.mark.parametrize(
    ("data", "level"),
    [("0000", 0), ("017b", 171), ("0241", 241), ("025f", 255), ("0172", 178), ("015d", 157)],
)
def test_g90_levels_decode_as_measured(data: str, level: int) -> None:
    """Replies seen on the radio, with the G90's wrong last BCD digit."""
    assert g90_level_from_bytes(bytes.fromhex(data)) == level
    assert g90_level_to_bytes(level) == bytes.fromhex(data)


@pytest.mark.parametrize("data", ["0300", "01a0", "00", "000000", "0261"])
def test_impossible_g90_levels_are_refused(data: str) -> None:
    with pytest.raises(CivParseError):
        g90_level_from_bytes(bytes.fromhex(data))


def test_every_level_round_trips() -> None:
    for level in range(256):
        assert g90_level_from_bytes(g90_level_to_bytes(level)) == level


def core_with_events(tmp_path: Path) -> tuple[ServerCore, list[dict[str, Any]]]:
    core = ServerCore(make_config(tmp_path))
    events: list[dict[str, Any]] = []
    core._broadcast = events.append  # type: ignore[method-assign]
    return core, events


def transmitting(events: list[dict[str, Any]]) -> list[tuple[bool, bool]]:
    return [(e["active"], e["leased"]) for e in events if e.get("event") == "radio_transmitting"]


def test_power_without_lease_is_warned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    core, events = core_with_events(tmp_path)
    with caplog.at_level("INFO", logger="remoteg90ctl.server.core"):
        core._power_reading(0, now=0.0)
        core._power_reading(255, now=1.0)  # TUNE or microphone PTT with modulation
    assert transmitting(events) == [(True, False)]
    assert core.warnings_without_lease == 1
    assert any("tx_without_lease" in r.getMessage() for r in caplog.records)
    assert [e["value"] for e in events if e.get("event") == "radio_power"] == [0, 255]


def test_output_right_after_our_lease_is_ours(tmp_path: Path) -> None:
    """Measured 2026-11-08: the meter, read once a second, still showed power just after
    PTT on the page was released, which raised a false "without a lease" warning."""
    core, events = core_with_events(tmp_path)
    core._tx_ended_at = 100.0  # our lease ended (time.monotonic in the server)
    core._power_reading(28, now=100.9)
    core._power_reading(57, now=101.9)
    assert transmitting(events) == []
    assert core.warnings_without_lease == 0
    core._power_reading(0, now=102.5)
    core._power_reading(255, now=103.0)  # later: microphone PTT, seen again
    assert transmitting(events) == [(True, False)]
    assert core.warnings_without_lease == 1


def test_pauses_in_speech_do_not_end_the_transmission(tmp_path: Path) -> None:
    core, events = core_with_events(tmp_path)
    readings = [300, 0, 0, 250, 0, 0, 0]  # a reading per second; 3 s of silence ends it
    for second, value in enumerate(readings):
        core._power_reading(value, now=float(second))
    assert transmitting(events) == [(True, False), (False, False)]
    assert core.warnings_without_lease == 1


def test_ptt_without_modulation_is_not_a_transmission(tmp_path: Path) -> None:
    """SSB with PTT and no audio emits nothing; the meter shows 0 (measured)."""
    core, events = core_with_events(tmp_path)
    for second in range(10):
        core._power_reading(0, now=float(second))
    assert transmitting(events) == []


async def test_meter_is_read_without_a_browser_and_sees_our_lease(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path)) as system:
        system.core.power_interval_s = 0.05
        system.core.power_hold_s = 0.2
        client = await system.client()
        lease = (await client.request("tx_request"))["lease"]
        seen = await client.next_event("radio_transmitting", timeout=3.0)
        assert seen == {"v": 1, "event": "radio_transmitting", "active": True, "leased": True}
        assert system.core.warnings_without_lease == 0
        # The lease is announced at once; the meter sees the output a moment later.
        await async_wait_for(lambda: system.core.rf_active, timeout=3.0)
        assert system.core.warnings_without_lease == 0
        status = await client.request("status")
        assert status["power"]["transmitting"] is True
        assert (await client.request("tx_release", lease=lease))["ok"]
        await async_wait_for(lambda: not system.core.rf_active, timeout=3.0)
        await client.close()


async def test_keying_outside_the_guard_is_seen(tmp_path: Path) -> None:
    """Microphone PTT keys the radio without the guard: the meter still shows it."""
    async with running_system(make_config(tmp_path)) as system:
        system.core.power_interval_s = 0.05
        client = await system.client()
        system.harness.port.radio.keyed = True  # as if keyed on the microphone
        seen = await client.next_event("radio_transmitting", timeout=3.0)
        assert seen["active"] is True
        assert seen["leased"] is False
        assert system.core.warnings_without_lease == 1
        system.harness.port.radio.keyed = False
        await client.close()


def test_power_calibration_is_validated() -> None:
    from test_config import VALID, problems_of  # noqa: PLC0415

    def with_points(points: object) -> dict[str, Any]:
        data = {k: dict(v) if isinstance(v, dict) else v for k, v in VALID.items()}
        data["radio"]["power_meter_watts"] = points
        return data

    config = parse_config(with_points([[0, 0], [171, 1], [241, 2]]))
    assert config.radio.power_meter_watts == ((0, 0.0), (171, 1.0), (241, 2.0))
    for bad in ([[0, 0]], [[0, 0], [0, 5]], [[0, 5], [100, 1]], [[0, 0], [100, 500]], "x"):
        problems = problems_of(with_points(bad))
        assert any(p.startswith("radio.power_meter_watts") for p in problems), (bad, problems)


@pytest.mark.parametrize("watts", range(1, 21))
def test_power_setting_shows_the_watts_set_on_the_radio(watts: int) -> None:
    """The radio truncates watts to a level (5 W comes as 53, measured)."""
    level = int((watts - 1) * 255 / 19)
    assert round(setting_to_watts(level), 1) == watts


def test_power_fields_carry_the_setting_in_watts(tmp_path: Path) -> None:
    core, events = core_with_events(tmp_path)
    core._power_reading(0, now=0.0, setting=255)
    (power,) = [e for e in events if e.get("event") == "radio_power"]
    assert power["setting"] == 255
    assert power["setting_watts"] == 20.0


def test_high_swr_is_warned_after_two_readings_at_the_end_of_the_scale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    core, events = core_with_events(tmp_path)
    with caplog.at_level("WARNING", logger="remoteg90ctl.server.core"):
        core._power_reading(100, now=0.0, swr=255)  # the rise of a transmission
        assert not core.swr_high
        core._power_reading(255, now=1.0, swr=255)
        assert core.swr_high
        core._power_reading(0, now=2.0, swr=0)  # receive: SWR is not measured
        assert core.swr_high
        core._power_reading(255, now=10.0, swr=220)  # next transmission, SWR 1.5
        assert not core.swr_high
    assert [r.getMessage() for r in caplog.records].count("high_swr") == 1
    assert [e["swr_high"] for e in events if e.get("event") == "radio_power"][-1] is False


def test_moderate_swr_is_not_warned(tmp_path: Path) -> None:
    core, _ = core_with_events(tmp_path)
    for second in range(10):
        core._power_reading(255, now=float(second), swr=220)  # SWR 1.5 (measured)
    assert not core.swr_high


def suspected(events: list[dict[str, Any]]) -> list[bool]:
    return [e["active"] for e in events if e.get("event") == "radio_tx_suspected"]


def test_a_muted_s_meter_suggests_a_transmission(tmp_path: Path) -> None:
    """Measured 2026-11-08: microphone PTT without output (U-D, or no speech) mutes the
    S-meter to 242; the power meter sees nothing. Owner's decision: a yellow bar."""
    core, events = core_with_events(tmp_path)
    for second, level in enumerate([71, 75, 242, 242, 242, 73]):
        core._muted_s_meter(level, now=second * 0.5)
    assert suspected(events) == [True, False]  # after 1 s at 242, cleared by a reading


def test_a_quiet_band_or_a_short_dip_is_no_warning(tmp_path: Path) -> None:
    core, events = core_with_events(tmp_path)
    for second, level in enumerate([242, 242, 242, 242]):  # below S1 from the start
        core._muted_s_meter(level, now=second * 0.5)
    for second, level in enumerate([10, 242, 242, 242]):  # from below S1
        core._muted_s_meter(level, now=10 + second * 0.5)
    for second, level in enumerate([71, 242, 71, 242, 71]):  # 0.5 s dips
        core._muted_s_meter(level, now=20 + second * 0.5)
    assert suspected(events) == []


def test_our_transmission_resets_the_muted_s_meter(tmp_path: Path) -> None:
    """While the server transmits the S-meter is not read (None): no yellow bar after it."""
    core, events = core_with_events(tmp_path)
    core._muted_s_meter(71, now=0.0)
    core._muted_s_meter(None, now=0.5)  # our lease
    for second in range(4):
        core._muted_s_meter(242, now=1.0 + second * 0.5)
    assert suspected(events) == []
    for second, level in enumerate([71, 242, 242, 242]):
        core._muted_s_meter(level, now=5 + second * 0.5)
    core._muted_s_meter(None, now=7.0)  # the power meter now shows output: the red bar
    assert suspected(events) == [True, False]


def test_a_silent_band_after_a_retune_is_no_warning(tmp_path: Path) -> None:
    """Seen 2026-11-08: 15 m at S2, then 160 m at 242 from the first reading."""
    core, events = core_with_events(tmp_path)
    core._radio_state(hz=21_140_000)
    core._muted_s_meter(44, now=0.0)
    core._radio_state(hz=1_905_000)
    for second in range(6):
        core._muted_s_meter(242, now=0.5 + second * 0.5)
    assert suspected(events) == []


def test_a_retune_clears_the_yellow_bar(tmp_path: Path) -> None:
    core, events = core_with_events(tmp_path)
    core._radio_state(hz=21_140_000)
    for second, level in enumerate([71, 242, 242, 242]):
        core._muted_s_meter(level, now=second * 0.5)
    core._radio_state(hz=28_850_000)
    assert suspected(events) == [True, False]
