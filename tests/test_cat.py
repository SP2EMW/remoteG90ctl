# SPDX-License-Identifier: GPL-3.0-or-later
"""CAT transactions against a fake guard: reply matching and late replies."""

import asyncio
from typing import Any, cast

import pytest

from remoteg90ctl.civ import REPLY_OK, Command, Frame, Mode, encode_frequency
from remoteg90ctl.server.cat import STALE_DRAIN_S, Cat, CatError
from remoteg90ctl.server.guard_client import GuardClient

RADIO = 0x88
CTRL = 0xE0


class FakeGuard:
    """Accepts every frame; the test decides what the radio answers and when."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        self.sent.append(str(fields["frame"]))
        return {"ok": True}


def reply(hz: int) -> str:
    frame = Frame(CTRL, RADIO, Command.READ_FREQUENCY, encode_frequency(hz))
    return frame.to_bytes().hex()


def make_cat(guard: FakeGuard) -> Cat:
    return Cat(
        cast(GuardClient, guard),
        radio_address=RADIO,
        controller_address=CTRL,
        reply_timeout_s=0.2,
    )


async def test_reply_is_matched() -> None:
    guard = FakeGuard()
    cat = make_cat(guard)
    task = asyncio.create_task(cat.get_frequency())
    await asyncio.sleep(0.05)
    cat.on_civ_rx(Frame(0x00, RADIO, Command.TRANSCEIVE_FREQUENCY, b"\x00" * 5).to_bytes().hex())
    cat.on_civ_rx(reply(7_074_000))
    assert await task == 7_074_000


async def test_late_reply_is_not_taken_for_the_next_answer() -> None:
    guard = FakeGuard()
    cat = make_cat(guard)
    with pytest.raises(CatError) as info:
        await cat.get_frequency()
    assert info.value.code == "radio_timeout"

    task = asyncio.create_task(cat.get_frequency())
    await asyncio.sleep(0.05)
    cat.on_civ_rx(reply(3_500_000))  # the late answer to the first question
    await asyncio.sleep(STALE_DRAIN_S)
    cat.on_civ_rx(reply(14_074_000))  # the real answer
    assert await task == 14_074_000
    assert len(guard.sent) == 2


async def test_ng_reply() -> None:
    guard = FakeGuard()
    cat = make_cat(guard)
    task = asyncio.create_task(cat.set_frequency(7_074_000))
    await asyncio.sleep(0.05)
    cat.on_civ_rx(Frame(CTRL, RADIO, 0xFA).to_bytes().hex())
    with pytest.raises(CatError) as info:
        await task
    assert info.value.code == "radio_rejected"


async def test_set_mode_keeps_the_filter_reported_by_the_radio() -> None:
    """The G90 refuses a mode without the filter byte (firmware 1.81)."""
    guard = FakeGuard()
    cat = make_cat(guard)
    task = asyncio.create_task(cat.set_mode(Mode.LSB))
    await asyncio.sleep(0.05)
    cat.on_civ_rx(Frame(CTRL, RADIO, Command.READ_MODE, b"\x01\x02").to_bytes().hex())
    await asyncio.sleep(0.05)
    cat.on_civ_rx(Frame(CTRL, RADIO, REPLY_OK).to_bytes().hex())
    await task
    assert guard.sent == ["fefe88e004fd", "fefe88e0060002fd"]
