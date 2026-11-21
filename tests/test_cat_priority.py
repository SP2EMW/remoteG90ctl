# SPDX-License-Identifier: GPL-3.0-or-later
"""A tuning request goes before the reads waiting for the CAT bus (owner's decision
2026-11-08): the follow loop and the power meter queue reads all the time, and
the fine slider or the waterfall must not wait behind them."""

import asyncio
from typing import Any

from remoteg90ctl.civ import Command, Frame, encode_frequency, parse_frame
from remoteg90ctl.civ.frame import REPLY_OK
from remoteg90ctl.server.cat import Cat

RADIO, CTRL = 0x70, 0xE0


class SlowGuard:
    """Answers every frame after a short delay, like the radio; records the order."""

    def __init__(self) -> None:
        self.sent: list[int] = []
        self.cat: Cat | None = None

    async def request(self, _kind: str, **fields: Any) -> dict[str, Any]:
        frame = parse_frame(bytes.fromhex(fields["frame"]))
        self.sent.append(frame.command)
        asyncio.get_running_loop().call_later(0.02, self._answer, frame)
        return {"ok": True}

    def _answer(self, frame: Frame) -> None:
        assert self.cat is not None
        if frame.command == Command.READ_FREQUENCY:
            reply = Frame(CTRL, RADIO, Command.READ_FREQUENCY, encode_frequency(7_074_000))
        else:
            reply = Frame(CTRL, RADIO, REPLY_OK)
        self.cat.on_civ_rx(reply.to_bytes().hex())


async def test_tuning_goes_before_waiting_reads() -> None:
    guard = SlowGuard()
    cat = Cat(guard, radio_address=RADIO, controller_address=CTRL)  # type: ignore[arg-type]
    guard.cat = cat
    first = asyncio.create_task(cat.get_frequency())  # holds the bus
    await asyncio.sleep(0.005)
    reads = [asyncio.create_task(cat.get_frequency()) for _ in range(2)]
    await asyncio.sleep(0.005)
    tune = asyncio.create_task(cat.set_frequency(7_075_000))
    await asyncio.gather(first, *reads, tune)
    read, tuned = Command.READ_FREQUENCY, Command.SET_FREQUENCY
    assert guard.sent == [read, tuned, read, read]


async def test_a_cancelled_wait_does_not_block_the_bus() -> None:
    guard = SlowGuard()
    cat = Cat(guard, radio_address=RADIO, controller_address=CTRL)  # type: ignore[arg-type]
    guard.cat = cat
    first = asyncio.create_task(cat.get_frequency())
    await asyncio.sleep(0.005)
    waiting = asyncio.create_task(cat.set_frequency(7_075_000))
    await asyncio.sleep(0.005)
    waiting.cancel()
    await first
    assert await asyncio.wait_for(cat.get_frequency(), 1.0) == 7_074_000
