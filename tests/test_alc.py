# SPDX-License-Identifier: GPL-3.0-or-later
"""ALC meter (CI-V 15 13), read only, during a transmission.

Measured on the owner's G90 (firmware 1.81, 2026-11-08), SSB, microphone PTT:
00 00 in receive and in silence; with a steady sound the display showed 5-6
at level 7, 30 at 40, 82 at 95 and 99. Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import CivParseError, alc_from_frame, parse_frame, read_alc_request
from remoteg90ctl.server.alc import level_to_display
from server_helpers import AsyncClient, System, async_wait_for, running_system
from test_server_iq import iq_config


@pytest_asyncio.fixture
async def system(tmp_path: Path) -> AsyncIterator[System]:
    async with running_system(make_config(tmp_path)) as running:
        yield running


@pytest_asyncio.fixture
async def client(system: System) -> AsyncIterator[AsyncClient]:
    connection = await system.client()
    yield connection
    await connection.close()


@pytest.mark.parametrize(
    ("reply", "level"),
    [("0000", 0), ("0030", 32), ("003f", 31), ("004e", 46), ("0108", 104), ("010c", 108)],
)
def test_measured_replies_decode(reply: str, level: int) -> None:
    assert read_alc_request(0x70, 0xE0).hex() == "fefe70e01513fd"
    assert alc_from_frame(parse_frame(bytes.fromhex(f"fefee0701513{reply}fd"))) == level


def test_another_meter_is_not_an_alc_reply() -> None:
    with pytest.raises(CivParseError):
        alc_from_frame(parse_frame(bytes.fromhex("fefee07015020106fd")))


@pytest.mark.parametrize(
    ("level", "shown"), [(0, 0), (7, 6), (40, 30), (95, 82), (99, 82), (120, 82)]
)
def test_measured_points_read_as_on_the_radio(level: int, shown: int) -> None:
    assert level_to_display(level) == shown


def test_the_display_rises_with_the_level() -> None:
    shown = [level_to_display(level) for level in range(256)]
    assert shown == sorted(shown)
    assert max(shown) == 82


async def test_alc_request_and_cli(
    client: AsyncClient, system: System, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (await client.request("get_alc"))["alc"] == {"level": 0, "display": 0}  # receive
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "alc"]) == 0
    assert capsys.readouterr().out.strip() == "ALC 0 (level 0 of 255)"


async def test_alc_is_read_with_the_vfo_only_in_transmit(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path, iq=iq_config(tmp_path))) as system:
        system.core._browser_connected = lambda: True  # type: ignore[method-assign]
        radio = system.harness.port.radio
        radio.alc = 104
        await async_wait_for(lambda: system.core.s_meter is not None)
        assert system.core.alc is None  # not read in receive
        client = await system.client()
        lease = (await client.request("tx_request"))["lease"]
        await async_wait_for(lambda: system.core.alc == 104)
        assert (await client.request("tx_release", lease=lease))["ok"]
        await async_wait_for(lambda: system.core.alc is None and system.core.s_meter is not None)
        await client.close()
