# SPDX-License-Identifier: GPL-3.0-or-later
"""Noise blanker (CI-V 16 22) and its level (14 12).

Measured on the owner's G90 (firmware 1.81, 2026-11-07): 16 22 00/01 set and
read back; 14 12 is the LEVEL (menu 0-10), not the width. A written level is
cut down to a whole step (25 -> 0, 26 -> 1, 128 -> 5, 255 -> 10) and read back
as floor(step * 25.5): 25 (step 1), 127 (5), 178 (7, set in the menu), 255.
Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import (
    nb_from_frame,
    nb_level_from_frame,
    nb_level_to_step,
    nb_step_to_level,
    parse_frame,
    set_nb_level_request,
    set_nb_request,
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


def test_on_off_frames() -> None:
    assert set_nb_request(RADIO, CTRL, True).hex() == "fefe70e0162201fd"
    assert set_nb_request(RADIO, CTRL, False).hex() == "fefe70e0162200fd"
    assert nb_from_frame(parse_frame(bytes.fromhex("fefee070162201fd"))) is True


@pytest.mark.parametrize(("step", "written"), [(0, "0000"), (1, "0026"), (5, "0128"), (10, "0255")])
def test_a_step_is_written_as_the_smallest_level_that_gives_it(step: int, written: str) -> None:
    assert set_nb_level_request(RADIO, CTRL, step).hex() == f"fefe70e01412{written}fd"


@pytest.mark.parametrize(
    ("reply", "step"), [("0000", 0), ("0029", 1), ("012f", 5), ("0172", 7), ("025f", 10)]
)
def test_measured_replies_give_the_menu_step(reply: str, step: int) -> None:
    assert nb_level_from_frame(parse_frame(bytes.fromhex(f"fefee0701412{reply}fd"))) == step


@pytest.mark.parametrize("step", range(11))
def test_every_step_survives_the_radio(step: int) -> None:
    level = nb_step_to_level(step)
    assert level * 10 // 255 == step  # the radio cuts down to this step
    assert nb_level_to_step(step * 255 // 10) == step  # and reports it back
    radio = SimulatedRadio(RADIO)
    radio.handle(set_nb_level_request(RADIO, CTRL, step))
    assert nb_level_to_step(radio.nb_level) == step


@pytest.mark.parametrize("step", [-1, 11])
def test_steps_outside_the_menu_are_refused(step: int) -> None:
    with pytest.raises(ValueError, match="outside 0-10"):
        set_nb_level_request(RADIO, CTRL, step)


async def test_nb(client: AsyncClient, system: System) -> None:
    radio = system.harness.port.radio
    reply = await client.request("get_nb")
    assert (reply["nb_on"], reply["nb_level"]) == (False, 5)
    reply = await client.request("set_nb", on=True, level=7)
    assert (reply["nb_on"], reply["nb_level"]) == (True, 7)
    assert radio.nb_on
    assert radio.nb_level == 178
    assert (await client.request("set_nb", level=0))["nb_level"] == 0
    assert (await client.request("set_nb", on=False))["nb_on"] is False
    radio.nb_level = 25  # level 1 set in the menu
    assert (await client.request("get_nb"))["nb_level"] == 1


@pytest.mark.parametrize(
    "fields",
    [{}, {"on": "yes"}, {"level": 11}, {"level": -1}, {"level": 2.5}, {"level": True}],
)
async def test_invalid_settings_are_refused(client: AsyncClient, fields: dict[str, object]) -> None:
    assert (await client.request("set_nb", **fields))["error"] == "invalid_value"


async def test_no_change_during_tx(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("set_nb", on=True))["error"] == "tx_active"
    assert (await client.request("set_nb", level=9))["error"] == "tx_active"
    assert not system.harness.port.radio.nb_on
    assert (await client.request("get_nb"))["ok"]
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_cli(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "nb", "on"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "nb", "level", "3"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "nb"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "nb", "level"]) == 1
    out = capsys.readouterr()
    assert out.out.strip() == "on, level 3"
    assert "needs a value" in out.err
