# SPDX-License-Identifier: GPL-3.0-or-later
"""Preamplifier (CI-V 16 02) and attenuator (11): one front-panel button on the G90.

Measured on the owner's G90 (firmware 1.81, 2026-11-07): switching one on
switches the other off, any data byte toggles the attenuator (11 00 too), it
answers with its new state instead of OK, and its only step is 12 dB (0C,
binary). Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import (
    CivParseError,
    Frame,
    attenuator_from_frame,
    parse_frame,
    preamp_from_frame,
    read_attenuator_request,
    read_preamp_request,
    set_attenuator_request,
    set_preamp_request,
)
from remoteg90ctl.sim import SimulatedRadio
from server_helpers import AsyncClient, System, running_system

RADIO, CTRL = 0x70, 0xE0


@pytest_asyncio.fixture
async def system(tmp_path: Path) -> AsyncIterator[System]:
    async with running_system(make_config(tmp_path)) as running:
        yield running


@pytest_asyncio.fixture
async def client(system: System) -> AsyncIterator[AsyncClient]:
    connection = await system.client()
    yield connection
    await connection.close()


def test_frames_match_the_measured_ones() -> None:
    assert read_preamp_request(RADIO, CTRL).hex() == "fefe70e01602fd"
    assert set_preamp_request(RADIO, CTRL, True).hex() == "fefe70e0160201fd"
    assert set_preamp_request(RADIO, CTRL, False).hex() == "fefe70e0160200fd"
    assert read_attenuator_request(RADIO, CTRL).hex() == "fefe70e011fd"
    assert set_attenuator_request(RADIO, CTRL, True).hex() == "fefe70e0110cfd"
    assert set_attenuator_request(RADIO, CTRL, False).hex() == "fefe70e01100fd"


def test_replies_are_decoded() -> None:
    assert preamp_from_frame(parse_frame(bytes.fromhex("fefee070160201fd"))) is True
    assert preamp_from_frame(parse_frame(bytes.fromhex("fefee070160200fd"))) is False
    assert attenuator_from_frame(parse_frame(bytes.fromhex("fefee070110cfd"))) is True
    assert attenuator_from_frame(parse_frame(bytes.fromhex("fefee0701100fd"))) is False


@pytest.mark.parametrize(
    "frame",
    [
        Frame(CTRL, RADIO, 0x16, b"\x02\x02"),  # unknown preamp value
        Frame(CTRL, RADIO, 0x16, b"\x12\x01"),  # AGC, not the preamp
        Frame(CTRL, RADIO, 0x16, b"\x02"),  # no value
    ],
)
def test_bad_preamp_replies_are_refused(frame: Frame) -> None:
    with pytest.raises(CivParseError):
        preamp_from_frame(frame)


@pytest.mark.parametrize("body", [b"", b"\x12", b"\x0c\x00"])
def test_bad_attenuator_replies_are_refused(body: bytes) -> None:
    with pytest.raises(CivParseError):
        attenuator_from_frame(Frame(CTRL, RADIO, 0x11, body))


def test_the_simulator_switches_them_like_the_radio() -> None:
    radio = SimulatedRadio(RADIO)
    assert radio.handle(set_preamp_request(RADIO, CTRL, True)).hex() == "fefee070fbfd"
    assert radio.preamp_on
    # 11 toggles the attenuator whatever the data, and answers with the new state.
    assert radio.handle(set_attenuator_request(RADIO, CTRL, False)).hex() == "fefee070110cfd"
    assert (radio.preamp_on, radio.attenuator_on) == (False, True)
    assert radio.handle(set_attenuator_request(RADIO, CTRL, True)).hex() == "fefee0701100fd"
    radio.handle(set_attenuator_request(RADIO, CTRL, True))
    radio.handle(set_preamp_request(RADIO, CTRL, True))
    assert (radio.preamp_on, radio.attenuator_on) == (True, False)


async def test_front_end_states(client: AsyncClient, system: System) -> None:
    radio = system.harness.port.radio
    assert (await client.request("get_front_end"))["front_end"] == "off"
    # Every transition, including to the same state (11 toggles: never sent twice).
    steps = ["att", "att", "pre", "pre", "off", "off", "pre", "att", "off", "att", "pre", "off"]
    flags_of = {"off": (False, False), "pre": (True, False), "att": (False, True)}
    for state in steps:
        flags = flags_of[state]
        reply = await client.request("set_front_end", state=state)
        assert reply["front_end"] == state, reply
        assert (radio.preamp_on, radio.attenuator_on) == flags
    # Switching on from the front panel is seen by the next read.
    radio.attenuator_on = True
    assert (await client.request("get_front_end"))["front_end"] == "att"


@pytest.mark.parametrize("state", ["ATT", "on", None, 1])
async def test_invalid_states_are_refused(client: AsyncClient, state: object) -> None:
    assert (await client.request("set_front_end", state=state))["error"] == "invalid_value"


async def test_a_radio_that_does_not_follow_is_reported(
    client: AsyncClient, system: System
) -> None:
    radio = system.harness.port.radio
    radio.handle = lambda message: b""  # type: ignore[method-assign]
    reply = await client.request("set_front_end", state="pre")
    assert reply["ok"] is False


async def test_no_switching_during_tx(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("set_front_end", state="att"))["error"] == "tx_active"
    assert not system.harness.port.radio.attenuator_on
    assert (await client.request("get_front_end"))["front_end"] == "off"  # reading is fine
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_cli(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "frontend", "pre"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "frontend"]) == 0
    assert capsys.readouterr().out.strip() == "pre"
