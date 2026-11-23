# SPDX-License-Identifier: GPL-3.0-or-later
"""FT8/FT4 transmission (stage 7, decisions.md D53): the server makes the signal.

Only within the page's own lease, only with a synchronised clock, only valid
messages and tones inside the radio's audio band (AGENTS.md stage 7). The sink
is a fake; no test opens a sound card (rule 9).
"""

import asyncio
import re
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import ft8lib  # type: ignore[import-untyped]
import numpy as np
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient

from remoteg90ctl.iq.source import SourceError
from remoteg90ctl.server import audio_service
from remoteg90ctl.server.clock import ClockWatch
from remoteg90ctl.server.ft8_tx import (
    BANDWIDTH_HZ,
    TX_DELAY_S,
    Ft8Error,
    check_timing,
    make_signal,
    play,
)
from remoteg90ctl.server.web import STATIC_DIR
from server_helpers import System, async_wait_for, running_system
from test_tx_audio import CONFIG, FakeSink
from test_web import OURS, ask, start_logged_in, web_config

CYCLE = 1_794_225_600_000  # 2026-11-09 12:00:00 UTC, a cycle start of both modes


def test_the_page_shows_the_same_signal_width() -> None:
    """The TX band on the waterfall (static/ft8.js) is the signal's real width."""
    script = (STATIC_DIR / "ft8.js").read_text(encoding="utf-8")
    found = re.search(r"SIGNAL_WIDTH_HZ = \{ FT8: ([\d.]+), FT4: ([\d.]+) \}", script)
    assert found is not None
    assert (float(found[1]), float(found[2])) == (BANDWIDTH_HZ["FT8"], BANDWIDTH_HZ["FT4"])


def test_the_signal_decodes_back() -> None:
    signal = make_signal("FT8", "cq sp1abc  jo82", 1500, -10, CYCLE)
    assert signal.message == "CQ SP1ABC JO82"
    assert signal.duration_s == pytest.approx(12.64)
    peak = np.max(np.abs(signal.pcm))
    assert peak == pytest.approx(32767 * 10 ** (-10 / 20), rel=0.01)
    period = np.zeros(ft8lib.FT8.NMAX)
    audio = signal.pcm[::4] / 32768  # 48 kHz to 12 kHz: the signal is far below 6 kHz
    period[6000 : 6000 + len(audio)] = audio
    (decoded,) = ft8lib.decode_ft8(period)
    assert decoded.message == "CQ SP1ABC JO82"
    assert decoded.freq == pytest.approx(1500, abs=2)


@pytest.mark.parametrize(
    "message",
    [
        "<SX200H> SP1ABC JO82",
        "<HF10SQ8W> SP1ABC R-07",
        "<F4ELU/QRP> SP1ABC 73",
        "DL1XYZ <SX200H> JO93",
        "CQ SX200H",
    ],
)
def test_nonstandard_calls_go_as_their_hash(message: str) -> None:
    """The page's messages for calls a standard message cannot hold (S740, 2026-11-15:
    'SX200H SP2EMW JO93' was refused, as the encoder would have cut it)."""
    assert make_signal("FT4", message, 1000.0, -6, CYCLE).message == message


@pytest.mark.parametrize(
    "message",
    [
        "S51A SP1ABC JO82",
        "A71AB SP1ABC -05",
        "Z31AB SP1ABC RR73",
        "E73M SP1ABC R+02",
        "SP1ABC S59ABC 73",
        "CQ P40A EK70",
        "T77C SP1ABC JN63",
    ],
)
def test_calls_with_digits_in_the_2nd_and_3rd_place_are_standard(message: str) -> None:
    """FT8 review 2026-11-15: ft8lib 1.3.2 took them for free text (refused)."""
    signal = make_signal("FT8", message, 1500, -10, CYCLE)
    assert signal.message == message
    period = np.zeros(ft8lib.FT8.NMAX)
    audio = signal.pcm[::4] / 32768
    period[6000 : 6000 + len(audio)] = audio
    (decoded,) = ft8lib.decode_ft8(period)
    assert decoded.message == message


@pytest.mark.parametrize("message", ["SX200H SP1ABC JO82", "F4ELU/QRP SP1ABC JO82"])
def test_a_nonstandard_call_in_full_is_refused(message: str) -> None:
    with pytest.raises(Ft8Error, match="cannot be sent"):
        make_signal("FT4", message, 1000.0, -6, CYCLE)


def test_ft4_too() -> None:
    signal = make_signal("FT4", "DL1XYZ SP1ABC R-07", 1000.0, -6, CYCLE + 7500)
    assert signal.duration_s == pytest.approx(5.04)


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"mode": "FT2"}, "mode"),
        ({"message": ""}, "message"),
        ({"message": "CQ SP1ABC JO82 ÄÖ"}, "message"),
        ({"message": "THIS IS A FREE TEXT FAR TOO LONG"}, "cannot be sent"),
        ({"message": 42}, "message"),
        ({"tone_hz": 150}, "tone_hz"),
        ({"tone_hz": 2790}, "tone_hz"),  # the signal would end above 2800 Hz
        ({"tone_hz": True}, "tone_hz"),
        ({"tone_hz": float("nan")}, "tone_hz"),
        ({"level_db": 3}, "level_db"),
        ({"start_ms": CYCLE + 1000}, "start_ms"),
    ],
)
def test_bad_requests_are_refused(change: dict[str, Any], words: str) -> None:
    fields = {
        "mode": "FT8",
        "message": "CQ SP1ABC JO82",
        "tone_hz": 1500,
        "level_db": -10,
        "start_ms": CYCLE,
    }
    with pytest.raises(Ft8Error, match=words) as raised:
        make_signal(**{**fields, **change})
    assert raised.value.code == "invalid_value"


def test_timing() -> None:
    signal = make_signal("FT8", "CQ SP1ABC JO82", 1500, -10, CYCLE)
    start = CYCLE / 1000
    check_timing(signal, start - 10)
    check_timing(signal, start + 1.9)  # late (0.5 s + 1.4 s), still sent
    with pytest.raises(Ft8Error) as late:
        check_timing(signal, start + 2.1)
    assert late.value.code == "too_late"
    ft4 = make_signal("FT4", "CQ SP1ABC JO82", 1500, -10, CYCLE)
    check_timing(ft4, start + 1.2)
    with pytest.raises(Ft8Error):
        check_timing(ft4, start + 1.4)
    with pytest.raises(Ft8Error):
        check_timing(signal, start - 20)


class FakeTime:
    """Wall clock moved by the sleeps, which also give the event loop a turn."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)
        await asyncio.sleep(0.001)


async def test_play_starts_half_a_second_into_the_cycle_and_stops_with_the_lease() -> None:
    signal = make_signal("FT4", "CQ SP1ABC JO82", 1000, -10, CYCLE)
    clock = FakeTime(CYCLE / 1000 - 3)
    fed: list[float] = []

    def feed(pcm: Any) -> bool:
        fed.append(clock.now)
        return len(fed) < 100  # the lease ends after 100 frames

    assert not await play(
        signal, feed, alive=lambda: len(fed) < 100, wall_clock=clock, sleep=clock.sleep
    )
    assert fed[0] == pytest.approx(CYCLE / 1000 + TX_DELAY_S)
    assert len(fed) == 100

    fed.clear()
    clock.now = CYCLE / 1000
    assert await play(
        signal, lambda pcm: fed.append(clock.now) is None, wall_clock=clock, sleep=clock.sleep
    )
    assert len(fed) == int(np.ceil(len(signal.pcm) / 960))
    # Never far ahead of the card: at most a few frames over real time.
    for index, at in enumerate(fed):
        assert index * 0.02 - (at - fed[0]) <= 0.1


@pytest_asyncio.fixture
async def web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[System, TestClient[Any, Any], FakeSink, Callable[[bool | None], None]]]:
    synchronized: list[bool | None] = [True]

    async def check() -> bool | None:
        return synchronized[0]

    monkeypatch.setattr(audio_service, "ClockWatch", lambda: ClockWatch(check))
    config = replace(web_config(tmp_path), audio=CONFIG)
    async with running_system(config) as system:
        sink = FakeSink()
        assert system.core.tx_audio is not None
        assert system.core.audio is not None
        system.core.tx_audio._sink_factory = lambda _c: sink
        clock = FakeTime(CYCLE / 1000 - 1)
        system.core.ft8_wall_clock = clock
        system.core.ft8_monotonic = clock
        system.core.ft8_sleep = clock.sleep
        await async_wait_for(lambda: system.core.audio.clock.synchronized is True)  # type: ignore[union-attr]

        def set_clock(value: bool | None) -> None:
            synchronized[0] = value
            system.core.audio.clock.synchronized = value  # type: ignore[union-attr]

        client = await start_logged_in(system)
        try:
            yield system, client, sink, set_clock
        finally:
            await client.close()


FT4 = {
    "mode": "FT4",
    "message": "CQ SP1ABC JO82",
    "tone_hz": 1000,
    "level_db": -10,
    "start_ms": CYCLE,
}


async def test_the_page_sends_ft4_within_its_lease(web: Any) -> None:
    system, client, sink, _ = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "ft8_transmit", lease="nope", **FT4))["error"] == "unknown_lease"
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    answer = await ask(ws, "ft8_transmit", lease=lease, **FT4)
    assert answer["ok"], answer
    assert answer["duration_s"] == pytest.approx(5.04)
    assert (await ask(ws, "ft8_transmit", lease=lease, **FT4))["error"] == "tx_busy"
    while True:  # the event comes when the whole signal has been played
        message = await ws.receive_json(timeout=10)
        if message.get("event") == "ft8_sent":
            break
    assert message["lease"] == lease
    assert len(sink.frames) == int(np.ceil(5.04 * 50))
    peak = max(int(np.max(np.abs(frame))) for frame in sink.frames)
    assert peak <= 32767 * 10 ** (-6 / 20) + 1  # the server's limiter (audio.tx_peak_dbfs)
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    assert not system.ptt_active
    await ws.close()


async def test_releasing_the_lease_stops_the_signal(web: Any) -> None:
    system, client, sink, _ = web
    system.core.ft8_sleep = lambda s: asyncio.sleep(0.05)  # the card's real pace
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    assert (await ask(ws, "ft8_transmit", lease=lease, **FT4))["ok"]
    await async_wait_for(lambda: len(sink.frames) > 0)
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    played = len(sink.frames)
    await asyncio.sleep(0.3)
    assert len(sink.frames) == played < 100
    assert not sink.is_open
    await ws.close()


async def test_no_ft8_without_a_synchronised_clock(web: Any) -> None:
    _, client, sink, set_clock = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    for value in (False, None):
        set_clock(value)
        refused = await ask(ws, "ft8_transmit", lease=lease, **FT4)
        assert refused["error"] == "clock_not_synchronized"
    set_clock(True)
    late = {**FT4, "start_ms": CYCLE - 7500}  # the page's clock is 1 s before CYCLE
    assert (await ask(ws, "ft8_transmit", lease=lease, **late))["error"] == "too_late"
    assert sink.frames == []
    await ws.close()


async def test_the_cli_cannot_send_ft8(web: Any) -> None:
    system, _, sink, _ = web
    cli = await system.client()
    lease = (await cli.request("tx_request", dummy_load_confirmed=True))["lease"]
    refused = await cli.request("ft8_transmit", lease=lease, **FT4)
    assert refused["error"] == "forbidden"
    assert (await cli.request("tx_release", lease=lease))["ok"]
    assert sink.frames == []
    await cli.close()


# ---- A playback that breaks while the lease lives (code review 2026-11-10, point 1)


async def test_a_full_queue_for_a_moment_does_not_end_the_signal() -> None:
    signal = make_signal("FT4", "CQ SP1ABC JO82", 1000, -10, CYCLE)
    clock = FakeTime(CYCLE / 1000)
    refusals = [0]

    def feed(pcm: Any) -> bool:
        refusals[0] += 1
        return refusals[0] % 7 != 0  # every 7th offer finds the card's queue full

    assert await play(signal, feed, alive=lambda: True, wall_clock=clock, sleep=clock.sleep)


async def test_a_card_that_stays_stuck_ends_the_signal_after_a_second() -> None:
    signal = make_signal("FT4", "CQ SP1ABC JO82", 1000, -10, CYCLE)
    clock = FakeTime(CYCLE / 1000)
    began = clock.now
    assert not await play(
        signal, lambda pcm: False, alive=lambda: True, wall_clock=clock, sleep=clock.sleep
    )
    assert 0.9 <= clock.now - began - TX_DELAY_S <= 1.2


async def test_a_gone_lease_ends_the_signal_at_once() -> None:
    signal = make_signal("FT4", "CQ SP1ABC JO82", 1000, -10, CYCLE)
    clock = FakeTime(CYCLE / 1000)
    began = clock.now
    assert not await play(
        signal, lambda pcm: False, alive=lambda: False, wall_clock=clock, sleep=clock.sleep
    )
    assert clock.now - began - TX_DELAY_S < 0.05


async def test_pacing_ignores_a_jump_of_the_wall_clock() -> None:
    """The cycle start is by the wall clock, the pace by the monotonic one."""
    signal = make_signal("FT4", "CQ SP1ABC JO82", 1000, -10, CYCLE)
    wall, mono = FakeTime(CYCLE / 1000), FakeTime(0.0)
    fed = [0]

    async def sleep(seconds: float) -> None:
        await wall.sleep(seconds)
        await mono.sleep(seconds)

    def feed(pcm: Any) -> bool:
        fed[0] += 1
        if fed[0] == 50:
            wall.now -= 30.0  # NTP steps the clock back in the middle of the signal
        return True

    assert await play(
        signal, feed, alive=lambda: True, wall_clock=wall, monotonic=mono, sleep=sleep
    )
    assert fed[0] == int(np.ceil(len(signal.pcm) / 960))


async def test_a_stuck_card_ends_the_lease_and_tells_the_page(web: Any) -> None:
    system, client, sink, _ = web
    release = threading.Event()
    write = sink.write

    def slow_write(pcm: Any) -> None:
        release.wait(5)  # the card does not take audio any more
        write(pcm)

    sink.write = slow_write  # type: ignore[method-assign]
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    assert (await ask(ws, "ft8_transmit", lease=lease, **FT4))["ok"]
    while True:
        message = await ws.receive_json(timeout=10)
        if message.get("event") == "tx_ended":
            break
    assert message["reason"] == "ft8_playback_failed"
    await async_wait_for(lambda: not system.ptt_active)
    release.set()
    await ws.close()


async def test_a_card_that_does_not_open_refuses_the_voice_lease(web: Any) -> None:
    system, client, _, _ = web
    assert system.core.tx_audio is not None

    def broken(_config: Any) -> Any:
        raise SourceError("card gone")

    system.core.tx_audio._sink_factory = broken
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    refused = await ask(ws, "tx_request", antenna_confirmed=True)
    assert refused["error"] == "audio_unavailable"
    await async_wait_for(lambda: not system.ptt_active)
    assert system.core._tx is None
    await ws.close()
