# SPDX-License-Identifier: GPL-3.0-or-later
"""S-meter (CI-V 15 02), read only.

Measured on the owner's G90 (firmware 1.81, 2026-11-07), tinySA and RF gain:
S1 22, S3 44, about S5 73, S7 106, S9 136, S9+20 160, S9+40 174; below the
scale 242, also without an antenna. Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import parse_frame, read_s_meter_request, s_meter_from_frame
from remoteg90ctl.server.smeter import db_to_text, level_to_db
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
    [
        ("0026", 22),
        ("004c", 44),
        ("0079", 73),
        ("010a", 106),
        ("0138", 136),
        ("0160", 160),
        ("017e", 174),
        ("0242", 242),
    ],
)
def test_measured_replies_decode(reply: str, level: int) -> None:
    assert read_s_meter_request(0x70, 0xE0).hex() == "fefe70e01502fd"
    assert s_meter_from_frame(parse_frame(bytes.fromhex(f"fefee0701502{reply}fd"))) == level


@pytest.mark.parametrize(
    ("level", "text"),
    [
        (22, "S1"),
        (44, "S3"),
        (73, "S5"),
        (106, "S7"),
        (136, "S9"),
        (160, "S9+20"),
        (174, "S9+40"),
        (242, "S0"),
        (0, "S0"),
    ],
)
def test_measured_points_read_as_on_the_radio(level: int, text: str) -> None:
    assert db_to_text(level_to_db(level)) == text


def test_the_scale_rises_with_the_level_and_stops_at_s9_plus_60() -> None:
    dbs = [level_to_db(level) for level in range(1, 200)]
    assert dbs == sorted(dbs)
    assert max(dbs) == 60.0
    assert db_to_text(level_to_db(90)) == "S6"


async def test_s_meter_request_and_cli(
    client: AsyncClient, system: System, capsys: pytest.CaptureFixture[str]
) -> None:
    meter = (await client.request("get_s_meter"))["s_meter"]
    assert meter == {"level": 106, "db": -12.0, "text": "S7"}
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "smeter"]) == 0
    assert capsys.readouterr().out.strip() == "S7 (-12 dB from S9, level 106)"


async def test_s_meter_is_read_with_the_vfo_only_in_receive(tmp_path: Path) -> None:
    # The VFO is followed with an I/Q input, while a browser is connected.
    async with running_system(make_config(tmp_path, iq=iq_config(tmp_path))) as system:
        system.core._browser_connected = lambda: True  # type: ignore[method-assign]
        system.harness.port.radio.s_meter = 136
        await async_wait_for(lambda: system.core.s_meter == 136)
        client = await system.client()
        lease = (await client.request("tx_request"))["lease"]
        system.harness.port.radio.s_meter = 22
        await asyncio.sleep(1.2)
        assert system.core.s_meter is None  # not read during the transmission
        assert (await client.request("tx_release", lease=lease))["ok"]
        await async_wait_for(lambda: system.core.s_meter == 22)  # read again after it
        await client.close()
