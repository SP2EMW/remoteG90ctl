# SPDX-License-Identifier: GPL-3.0-or-later
"""VFO A/B and split (CI-V 07, 0F, 25, 26 01), stage 5.

Measured on the owner's G90 (firmware 1.81, 2026-11-08): 07 00/01 select a
VFO, each with its own mode; 0F reads and sets split; 25 00/01 give the
frequency of the selected/other VFO, with the selected VFO in the reply's
sub-command byte; 26 01 reports the other VFO's data flag correctly; in split
the radio transmits on the other VFO, in that VFO's mode. Runs on the simulator.
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from guard_helpers import CTRL, RADIO, make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import (
    CivParseError,
    Mode,
    data_mode_from_frame,
    parse_frame,
    read_data_mode_request,
    read_split_request,
    read_vfo_frequency_request,
    select_vfo_request,
    set_split_request,
    split_from_frame,
    vfo_frequency_from_frame,
)
from remoteg90ctl.guard.framecheck import FrameRejectedError, check_frame
from server_helpers import AsyncClient, System, running_system
from test_server_iq import iq_config
from test_web import OURS, ask, start_logged_in


def _check(body: str) -> None:
    check_frame(f"fefe{RADIO:02x}{CTRL:02x}{body}fd", radio_address=RADIO, controller_address=CTRL)


@pytest.mark.parametrize(
    ("reply", "vfo", "hz"),
    [
        ("25000010142100", "A", 21_141_000),  # A selected, 25 00
        ("25000050142100", "A", 21_145_000),  # A selected, 25 01: the byte stays 00
        ("25010050142100", "B", 21_145_000),  # B selected, 25 00
        ("25010010142100", "B", 21_141_000),  # B selected, 25 01
    ],
)
def test_measured_vfo_replies_decode(reply: str, vfo: str, hz: int) -> None:
    frame = parse_frame(bytes.fromhex(f"fefee070{reply}fd"))
    assert vfo_frequency_from_frame(frame) == (vfo, hz)


def test_frames() -> None:
    assert read_vfo_frequency_request(0x70, 0xE0, unselected=True).hex() == "fefe70e02501fd"
    assert select_vfo_request(0x70, 0xE0, "B").hex() == "fefe70e00701fd"
    assert read_split_request(0x70, 0xE0).hex() == "fefe70e00ffd"
    assert set_split_request(0x70, 0xE0, True).hex() == "fefe70e00f01fd"
    assert read_data_mode_request(0x70, 0xE0, unselected=True).hex() == "fefe70e02601fd"
    assert split_from_frame(parse_frame(bytes.fromhex("fefee0700f01fd"))) is True
    assert split_from_frame(parse_frame(bytes.fromhex("fefee0700f00fd"))) is False
    # 26 01, measured: B in USB, then in U-D.
    other = parse_frame(bytes.fromhex("fefee0702601010001fd"))
    assert data_mode_from_frame(other) == (Mode.USB, False, 1)
    other = parse_frame(bytes.fromhex("fefee0702601010101fd"))
    assert data_mode_from_frame(other) == (Mode.USB, True, 1)


def test_a_reply_without_a_whole_frequency_is_refused() -> None:
    with pytest.raises(CivParseError):
        vfo_frequency_from_frame(parse_frame(bytes.fromhex("fefee070250000101421fd")))


@pytest.mark.parametrize("body", ["0700", "0701", "0f", "0f00", "0f01", "2500", "2501", "2601"])
def test_measured_commands_pass_the_guard(body: str) -> None:
    _check(body)


@pytest.mark.parametrize(
    "body",
    [
        "07",
        "07b0",
        "07a0",
        "0702",
        "070000",
        "0f02",
        "0f0100",
        "25",
        "2502",
        "250000",
        "2601010101",
    ],
)
def test_other_vfo_frames_are_refused(body: str) -> None:
    """No swap, no copy, no writes through 25 or 26 01: not measured or not decided."""
    with pytest.raises(FrameRejectedError):
        _check(body)


def _config(tmp_path: Path, *, ssb_data_mode: bool = True) -> Any:
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    return dataclasses.replace(
        config, radio=dataclasses.replace(config.radio, ssb_data_mode=ssb_data_mode)
    )


@pytest_asyncio.fixture
async def system(tmp_path: Path) -> AsyncIterator[System]:
    async with running_system(_config(tmp_path)) as running:
        radio = running.harness.port.radio
        radio.frequency_hz, radio.mode, radio.data_mode = 21_141_000, Mode.USB, True
        radio.other_vfo = (21_145_000, Mode.USB, False, 1)
        yield running


@pytest_asyncio.fixture
async def client(system: System) -> AsyncIterator[AsyncClient]:
    connection = await system.client()
    yield connection
    await connection.close()


async def test_get_vfo(client: AsyncClient) -> None:
    answer = await client.request("get_vfo")
    assert (answer["vfo"], answer["other_hz"], answer["split"]) == ("A", 21_145_000, False)


async def test_switching_the_vfo_puts_ssb_into_data_mode(
    client: AsyncClient, system: System
) -> None:
    """Owner's decision 2026-11-08: USB on the page is U-D on the radio, on either VFO."""
    answer = await client.request("set_vfo", vfo="B")
    assert answer["ok"], answer
    assert (answer["vfo"], answer["other_hz"]) == ("B", 21_141_000)
    radio = system.harness.port.radio
    assert (radio.vfo, radio.frequency_hz, radio.data_mode) == ("B", 21_145_000, True)
    assert (system.core.radio_hz, system.core.radio_mode) == (21_145_000, "U-D")


async def test_without_ssb_data_mode_the_mode_is_left_alone(tmp_path: Path) -> None:
    async with running_system(_config(tmp_path, ssb_data_mode=False)) as system:
        system.harness.port.radio.other_vfo = (21_145_000, Mode.USB, False, 1)
        client = await system.client()
        assert (await client.request("set_vfo", vfo="B"))["ok"]
        assert system.harness.port.radio.data_mode is False
        await client.close()


@pytest.mark.parametrize("vfo", ["C", "a", "", None, 1])
async def test_invalid_vfo_is_refused(client: AsyncClient, vfo: object) -> None:
    assert (await client.request("set_vfo", vfo=vfo))["error"] == "invalid_value"


async def test_split_on_puts_the_transmit_vfo_into_data_mode(
    client: AsyncClient, system: System
) -> None:
    answer = await client.request("set_split", on=True)
    assert answer["ok"], answer
    assert answer["split"] is True
    radio = system.harness.port.radio
    assert radio.split is True
    assert radio.vfo == "A"  # back on the receive VFO
    assert radio.other_vfo[2] is True  # the transmit VFO is now U-D
    assert (await client.request("set_split", on=False))["split"] is False
    assert radio.split is False


async def test_split_is_logged_with_the_transmit_frequency(
    client: AsyncClient, system: System
) -> None:
    """AGENTS.md rule 7: the TX log gets where the radio transmits, the other VFO in split."""
    system.harness.port.radio.split = True
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("tx_release", lease=lease))["ok"]
    (start,) = system.harness.tx_events("tx_start")
    assert start["frequency_hz"] == 21_145_000


async def test_a_tuning_in_split_is_logged_on_the_receive_vfo(tmp_path: Path) -> None:
    """Measured 2026-11-08: the G90 tunes on the selected VFO, also in split."""
    config = dataclasses.replace(
        _config(tmp_path), safety=dataclasses.replace(_config(tmp_path).safety, allow_atu_tune=True)
    )
    async with running_system(config) as system:
        radio = system.harness.port.radio
        radio.frequency_hz, radio.split = 21_141_000, True
        radio.other_vfo = (21_145_000, Mode.USB, True, 1)
        client = await system.client()
        assert (await client.request("atu_tune"))["ok"]
        (record,) = system.harness.tx_events("atu_tune")
        assert record["frequency_hz"] == 21_141_000
        await client.close()


async def test_vfo_and_split_cannot_change_during_a_transmission(
    client: AsyncClient, system: System
) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert not (await client.request("set_vfo", vfo="B"))["ok"]
    assert not (await client.request("set_split", on=True))["ok"]
    radio = system.harness.port.radio
    assert (radio.vfo, radio.split) == ("A", False)
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_browser_in_split_needs_data_mode_on_the_transmit_vfo(system: System) -> None:
    """In split the G90 transmits in the other VFO's mode (measured): USB there would
    send the radio's microphone, so the browser is refused although A is in U-D."""
    web = await start_logged_in(system)
    try:
        ws = await web.ws_connect("/ws", headers=OURS)
        radio = system.harness.port.radio
        radio.split = True  # switched on at the radio: the server did not set B's mode
        refused = await ask(ws, "tx_request", antenna_confirmed=True)
        assert refused["error"] == "not_data_mode"
        assert "split" in refused["message"]
        assert not system.ptt_active
        radio.other_vfo = (21_145_000, Mode.USB, True, 1)  # B in U-D
        granted = await ask(ws, "tx_request", antenna_confirmed=True)
        assert granted["ok"], granted
        assert system.ptt_active
        assert (await ask(ws, "tx_release", lease=granted["lease"]))["ok"]
        (start,) = system.harness.tx_events("tx_start")
        assert start["frequency_hz"] == 21_145_000
        radio.split = False
        radio.data_mode = False  # A in USB, split off: refused as before
        assert (await ask(ws, "tx_request", antenna_confirmed=True))["error"] == "not_data_mode"
        await ws.close()
    finally:
        await web.close()


async def test_cli(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "split", "on"]) == 0
    assert capsys.readouterr().out.strip() == (
        "VFO A, VFO B: 21145000 Hz, split on (transmits on VFO B)"
    )
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "vfo", "b"]) == 0
    assert capsys.readouterr().out.strip() == (
        "VFO B, VFO A: 21141000 Hz, split on (transmits on VFO A)"
    )
