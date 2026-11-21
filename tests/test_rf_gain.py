# SPDX-License-Identifier: GPL-3.0-or-later
"""RF gain (CI-V 14 02), 1-100 % on the radio.

Measured on the owner's G90 (firmware 1.81, 2026-11-07): 0 -> 1 %, 255 ->
100 %; a written level is cut down to a whole percent (126 -> 49 %, 127 ->
50 %) and read back as floor((p - 1) * 255 / 99): 50 % set on the radio reads
126. Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import parse_frame, rf_gain_from_frame, set_rf_gain_request
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


@pytest.mark.parametrize(("percent", "written"), [(1, "0000"), (50, "0127"), (100, "0255")])
def test_a_percent_is_written_as_the_smallest_level_that_gives_it(
    percent: int, written: str
) -> None:
    assert set_rf_gain_request(RADIO, CTRL, percent).hex() == f"fefe70e01402{written}fd"


@pytest.mark.parametrize(("reply", "percent"), [("0000", 1), ("012e", 50), ("025f", 100)])
def test_measured_replies(reply: str, percent: int) -> None:
    assert rf_gain_from_frame(parse_frame(bytes.fromhex(f"fefee0701402{reply}fd"))) == percent


@pytest.mark.parametrize("percent", range(1, 101))
def test_every_percent_survives_the_radio(percent: int) -> None:
    radio = SimulatedRadio(RADIO)
    radio.handle(set_rf_gain_request(RADIO, CTRL, percent))
    reply = parse_frame(radio.handle(bytes.fromhex("fefe70e01402fd")))
    assert rf_gain_from_frame(reply) == percent


@pytest.mark.parametrize("percent", [0, 101])
def test_percents_outside_the_radio_are_refused(percent: int) -> None:
    with pytest.raises(ValueError, match="outside 1-100"):
        set_rf_gain_request(RADIO, CTRL, percent)


async def test_rf_gain(client: AsyncClient, system: System) -> None:
    assert (await client.request("get_rf_gain"))["rf_gain"] == 100
    assert (await client.request("set_rf_gain", percent=50))["rf_gain"] == 50
    assert system.harness.port.radio.rf_gain == 126
    system.harness.port.radio.rf_gain = 0  # 1 % set on the radio
    assert (await client.request("get_rf_gain"))["rf_gain"] == 1


@pytest.mark.parametrize("percent", [0, 101, 50.5, "50", True, None])
async def test_invalid_values_are_refused(client: AsyncClient, percent: object) -> None:
    assert (await client.request("set_rf_gain", percent=percent))["error"] == "invalid_value"


async def test_no_change_during_tx(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("set_rf_gain", percent=10))["error"] == "tx_active"
    assert (await client.request("get_rf_gain"))["rf_gain"] == 100
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_cli(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "rfgain", "30"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "rfgain"]) == 0
    assert capsys.readouterr().out.strip() == "30 %"
