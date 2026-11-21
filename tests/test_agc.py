# SPDX-License-Identifier: GPL-3.0-or-later
"""AGC (CI-V 16 12). Measured on the owner's G90 (firmware 1.81, 2026-11-07):
00 off, 01 fast (AGC-F), 02 slow (AGC-S), 03 auto (AGC-A); set answered OK and
read back as set. Runs on the simulator.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import CivParseError, Frame, agc_from_frame, parse_frame, set_agc_request
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


@pytest.mark.parametrize(
    ("name", "code"), [("off", "00"), ("fast", "01"), ("slow", "02"), ("auto", "03")]
)
def test_codes_match_the_measured_ones(name: str, code: str) -> None:
    assert set_agc_request(RADIO, CTRL, name).hex() == f"fefe70e01612{code}fd"
    assert agc_from_frame(parse_frame(bytes.fromhex(f"fefee0701612{code}fd"))) == name


def test_unknown_names_and_replies_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown AGC"):
        set_agc_request(RADIO, CTRL, "mid")
    for body in (b"\x12\x04", b"\x12", b"\x02\x01"):
        with pytest.raises(CivParseError):
            agc_from_frame(Frame(CTRL, RADIO, 0x16, body))


async def test_agc(client: AsyncClient, system: System) -> None:
    assert (await client.request("get_agc"))["agc"] == "auto"
    for name, code in [("off", 0), ("fast", 1), ("slow", 2), ("auto", 3)]:
        assert (await client.request("set_agc", agc=name))["agc"] == name
        assert system.harness.port.radio.agc == code
    system.harness.port.radio.agc = 1  # changed on the front panel
    assert (await client.request("get_agc"))["agc"] == "fast"


@pytest.mark.parametrize("agc", ["FAST", "mid", None, 1])
async def test_invalid_settings_are_refused(client: AsyncClient, agc: object) -> None:
    assert (await client.request("set_agc", agc=agc))["error"] == "invalid_value"


async def test_no_change_during_tx(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("set_agc", agc="off"))["error"] == "tx_active"
    assert system.harness.port.radio.agc == 3
    assert (await client.request("get_agc"))["agc"] == "auto"
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_cli(system: System, capsys: pytest.CaptureFixture[str]) -> None:
    socket = str(system.config.server.socket_path)
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "agc", "slow"]) == 0
    assert await asyncio.to_thread(cli.main, ["--socket", socket, "agc"]) == 0
    assert capsys.readouterr().out.strip() == "slow"
