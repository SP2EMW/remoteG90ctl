# SPDX-License-Identifier: GPL-3.0-or-later
"""Band limits by ITU region (decisions.md D54): the region from the account's
locator, the regional amateur allocations, the IARU band plans for display, and
a browser's transmission or tuning refused outside the bands."""

import asyncio
import itertools
from typing import Any

import pytest
from aiohttp.test_utils import TestClient

from remoteg90ctl.bandplan import BANDS, PLANS, in_band, line_b, locator_to_latlon, region_of
from remoteg90ctl.server.cat import CatError
from server_helpers import System, async_wait_for
from test_web import OURS, ask, web  # noqa: F401  (pytest fixture)

# Locators whose region is known, including the exceptions of RR No. 5.3-5.9.
REGIONS = {
    "JO82lj": 1,  # Poland
    "KO85": 1,  # Moscow
    "NO14": 1,  # Novosibirsk: Asian Russia is Region 1
    "PN53": 1,  # Vladivostok
    "ON47": 1,  # Ulaanbaatar: Mongolia is Region 1
    "MN83": 1,  # Almaty
    "AP19": 1,  # Chukotka, east of 180 degrees
    "KN41": 1,  # Istanbul
    "LL75": 1,  # Dubai, west of line A
    "HP94": 1,  # Iceland, east of line B (a great circle arc)
    "HM77": 1,  # Azores
    "JF96": 1,  # Cape Town
    "LM55": 3,  # Tehran: Iran is wholly Region 3
    "LM45": 3,  # north-west Iran, west of line A
    "MM44": 3,  # Kabul
    "OM89": 3,  # Beijing
    "PN43": 3,  # China next to Vladivostok
    "PM95": 3,  # Tokyo
    "QF56": 3,  # Sydney
    "AJ88": 3,  # Pacific west of line C
    "FN42": 2,  # Boston
    "GP44": 2,  # Greenland is Region 2
    "GG87": 2,  # Rio de Janeiro
    "BL11": 2,  # Hawaii
    "RO92": 2,  # the Aleutians, west of 180 degrees
}


@pytest.mark.parametrize(("locator", "region"), REGIONS.items())
def test_region_of_a_locator(locator: str, region: int) -> None:
    assert region_of(locator) == region


def test_locator_centre() -> None:
    assert locator_to_latlon("JO82") == (52.5, 17.0)
    lat, lon = locator_to_latlon("jo82LJ")
    assert (round(lat, 3), round(lon, 3)) == (52.396, 16.958)
    with pytest.raises(ValueError, match="locator"):
        locator_to_latlon("ZZ99")


def test_line_b_is_a_great_circle() -> None:
    # A straight line in degrees would put Iceland (21 W) west of line B.
    assert line_b(64.5) == pytest.approx(-29.8, abs=0.1)


def test_regional_differences() -> None:
    # 160 m: 1850-2000 kHz is a national allocation in Region 1 (RR 5.96).
    assert in_band(1, 1_840_000, 1_843_000)
    assert not in_band(1, 1_900_000, 1_903_000)
    assert in_band(2, 1_900_000, 1_903_000)
    assert not in_band(1, 7_250_000, 7_253_000)
    assert in_band(2, 7_250_000, 7_253_000)
    assert in_band(3, 3_850_000, 3_853_000)
    assert not in_band(1, 3_850_000, 3_853_000)
    # The whole signal counts: 3 kHz of USB above 14.349 MHz leaves 20 m.
    assert not in_band(1, 14_349_000, 14_352_000)
    assert in_band(1, 14_347_000, 14_350_000)


@pytest.mark.parametrize("region", [1, 2, 3])
def test_the_tables_are_consistent(region: int) -> None:
    bands = BANDS[region]
    assert all(1_800_000 <= low < high <= 30_000_000 for low, high in bands)
    assert list(bands) == sorted(bands)
    segments = PLANS[region]
    assert all(s.low_hz < s.high_hz for s in segments)
    assert all(a.high_hz <= b.low_hz for a, b in itertools.pairwise(segments))
    assert {s.kind for s in segments} <= {
        "cw",
        "narrow",
        "all",
        "beacon",
        "satellite",
        "fm",
        "guard",
    }
    # Every band has a plan (the plans may also show national extensions).
    for low, high in bands:
        assert any(s.low_hz < high and low < s.high_hz for s in segments), (low, high)


async def test_the_page_gets_its_region_and_plan(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    plan = await ask(ws, "get_bandplan")
    assert plan["region"] == 1
    assert [1_810_000, 1_850_000] in plan["bands"]
    assert any(s["kind"] == "beacon" and s["low"] == 14_099_000 for s in plan["segments"])
    await ws.close()


async def test_transmitting_outside_the_band_is_refused(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
) -> None:
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    for hz in (7_199_000, 7_250_000, 6_999_000):
        assert (await ask(ws, "set_frequency", hz=hz))["ok"]
        refused = await ask(ws, "tx_request", antenna_confirmed=True)
        assert refused["error"] == "out_of_band", refused
        assert "Region 1" in refused["message"]
    assert not system.ptt_active
    assert (await ask(ws, "set_frequency", hz=7_196_000))["ok"]  # 7.196-7.199 MHz: inside
    granted = await ask(ws, "tx_request", antenna_confirmed=True)
    assert granted["ok"], granted
    assert (await ask(ws, "tx_release", lease=granted["lease"]))["ok"]
    # L-D: the signal lies below the dial.
    assert (await ask(ws, "set_mode", mode="L-D"))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_001_000))["ok"]
    assert (await ask(ws, "tx_request", antenna_confirmed=True))["error"] == "out_of_band"
    await ws.close()


async def test_another_region_allows_its_own_bands(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system, client = web
    monkeypatch.setattr(system.core, "_region", lambda _session: 2)
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_250_000))["ok"]
    granted = await ask(ws, "tx_request", antenna_confirmed=True)
    assert granted["ok"], granted
    assert (await ask(ws, "tx_release", lease=granted["lease"]))["ok"]
    await ws.close()


async def test_tuning_outside_the_band_is_refused(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
) -> None:
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_atu", on=True))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_250_000))["ok"]
    assert (await ask(ws, "atu_tune"))["error"] == "out_of_band"
    assert system.harness.port.radio.tunings == 0
    await ws.close()


async def test_no_locator_no_transmission(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system, client = web
    monkeypatch.setattr(system.core, "_region", lambda _session: None)
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    refused = await ask(ws, "tx_request", antenna_confirmed=True)
    assert refused["error"] == "out_of_band"
    assert "locator" in refused["message"]
    await ws.close()


# The band check and the keying are one step (code review 2026-11-10, point 2)


async def test_the_radio_is_held_while_a_transmission_starts(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another card, or the page's restore after login, must not retune between the
    band check and the PTT."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    other = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_100_000))["ok"]
    reading = asyncio.Event()
    go = asyncio.Event()
    real = system.core._tx_target

    async def slow_target() -> tuple[int, str, bool]:
        target = await real()
        reading.set()
        await go.wait()  # the check is half-way: the radio was read, nothing keyed yet
        return target

    monkeypatch.setattr(system.core, "_tx_target", slow_target)
    request = asyncio.create_task(ask(ws, "tx_request", antenna_confirmed=True))
    try:
        await asyncio.wait_for(reading.wait(), 5)
        for change in (
            ("set_frequency", {"hz": 7_250_000}),
            ("set_mode", {"mode": "USB"}),
            ("set_vfo", {"vfo": "B"}),
            ("set_split", {"on": True}),
        ):
            refused = await ask(other, change[0], **change[1])
            assert refused["error"] == "tx_busy", (change, refused)
        go.set()
        granted = await asyncio.wait_for(request, 5)
        assert granted["ok"], granted
        assert (await ask(ws, "tx_release", lease=granted["lease"]))["ok"]
        assert (await ask(other, "set_frequency", hz=7_250_000))["ok"]  # free again
    finally:
        go.set()  # a failing assertion must neither leave the request waiting ...
        request.cancel()
        await ws.close()  # ... nor the sockets open
        await other.close()


async def test_a_change_that_slipped_in_is_caught_once_the_lease_exists(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the grant the guard refuses state changes, so the radio read then is the truth."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_100_000))["ok"]
    real = system.core._tx_target
    calls = [0]

    async def drifting_target() -> tuple[int, str, bool]:
        calls[0] += 1
        if calls[0] == 1:
            return await real()  # in band when first read ...
        return 7_250_000, "U-D", False  # ... outside it when read again

    monkeypatch.setattr(system.core, "_tx_target", drifting_target)
    refused = await ask(ws, "tx_request", antenna_confirmed=True)
    assert refused["error"] == "out_of_band", refused
    await async_wait_for(lambda: not system.ptt_active)
    assert system.core._tx is None
    # A radio that cannot be read the second time is not transmitted on either.
    calls[0] = 0

    async def blind_target() -> tuple[int, str, bool]:
        calls[0] += 1
        if calls[0] == 1:
            return await real()
        raise CatError("radio_timeout", "no reply")

    monkeypatch.setattr(system.core, "_tx_target", blind_target)
    assert (await ask(ws, "tx_request", antenna_confirmed=True))["error"] == "radio_timeout"
    await async_wait_for(lambda: not system.ptt_active)
    assert system.core._tx is None
    await ws.close()


async def test_the_tuning_carrier_is_checked_without_a_gap(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    other = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_atu", on=True))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_100_000))["ok"]
    reading = asyncio.Event()
    go = asyncio.Event()
    real = system.core.cat.get_frequency

    async def slow_frequency() -> int:
        hz = await real()
        reading.set()
        await go.wait()
        return hz

    monkeypatch.setattr(system.core.cat, "get_frequency", slow_frequency)
    tuning = asyncio.create_task(ask(ws, "atu_tune"))
    try:
        await asyncio.wait_for(reading.wait(), 5)
        assert (await ask(other, "set_frequency", hz=7_250_000))["error"] == "tx_busy"
        go.set()
        assert (await asyncio.wait_for(tuning, 5))["ok"]
        assert system.harness.port.radio.frequency_hz == 7_100_000  # tuned where it was read
    finally:
        go.set()
        tuning.cancel()
        await ws.close()
        await other.close()


async def test_a_lease_ended_by_others_while_starting_is_not_blamed_on_the_card(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review of 2026-11-10: only a card that did not open answers audio_unavailable."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_frequency", hz=7_100_000))["ok"]
    real = system.core._tx_target
    calls = [0]

    async def ended_meanwhile() -> tuple[int, str, bool]:
        calls[0] += 1
        if calls[0] == 2:  # the lease exists: something else ends it (the port, the guard)
            tx = system.core._tx
            assert tx is not None
            await system.core._end_tx(tx, notify_reason="port_lost")
        return await real()

    monkeypatch.setattr(system.core, "_tx_target", ended_meanwhile)
    refused = await ask(ws, "tx_request", antenna_confirmed=True)
    assert refused["error"] == "unknown_lease", refused
    assert "sound card" not in refused["message"]
    assert not system.ptt_active
    await ws.close()
