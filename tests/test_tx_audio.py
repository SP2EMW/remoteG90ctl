# SPDX-License-Identifier: GPL-3.0-or-later
"""Voice from the browser to the radio (stage 4): only during a lease, always limited.

AGENTS.md section 3: no sound reaches the radio outside an active lease. The
sink is a fake; no test opens a sound card (rule 9).
"""

import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest_asyncio
from aiohttp.test_utils import TestClient

from remoteg90ctl.audio.opus import OpusEncoder
from remoteg90ctl.config import AudioConfig, AudioSourceKind
from remoteg90ctl.server.audio_service import FRAME_SAMPLES, FRAME_TX_AUDIO, HEADER
from remoteg90ctl.server.tx_audio import CODEC_OPUS, CODEC_RAW, Limiter, TxAudio
from server_helpers import System, async_wait_for, running_system
from test_web import OURS, ask, start_logged_in, web_config

CONFIG = AudioConfig(AudioSourceKind.SYNTHETIC, None, tx_peak_dbfs=-6.0)


class FakeSink:
    def __init__(self) -> None:
        self.open_count = 0
        self.is_open = False
        self.frames: list[npt.NDArray[np.int16]] = []

    def open(self) -> None:
        self.open_count += 1
        self.is_open = True

    def write(self, pcm: npt.NDArray[np.int16]) -> None:
        assert self.is_open, "audio written to a closed card"
        self.frames.append(pcm)

    def close(self) -> None:
        self.is_open = False


def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def loud_frame() -> npt.NDArray[np.int16]:
    t = np.arange(FRAME_SAMPLES) / 48_000
    return (32000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)


def test_limiter_keeps_the_ceiling() -> None:
    limiter = Limiter(-6.0)
    out = limiter.apply(loud_frame())
    assert np.max(np.abs(out)) <= 32767 * 10 ** (-6 / 20) + 1


def test_nothing_reaches_the_card_outside_a_lease() -> None:
    sink = FakeSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner, stranger = object(), object()
    assert not tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    assert sink.open_count == 0
    tx.start(owner)
    assert sink.is_open
    assert not tx.feed(stranger, CODEC_RAW, loud_frame().tobytes())
    encoder = OpusEncoder(48_000, 24_000)
    assert tx.feed(owner, CODEC_OPUS, encoder.encode(loud_frame()))
    assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    tx.stop(play_tail=True)
    assert not sink.is_open
    assert len(sink.frames) == 2
    assert all(np.max(np.abs(f)) <= 32767 * 10 ** (-6 / 20) + 1 for f in sink.frames)
    assert not tx.feed(owner, CODEC_RAW, loud_frame().tobytes())


@pytest_asyncio.fixture
async def web(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any], FakeSink]]:
    config = replace(web_config(tmp_path), audio=CONFIG)
    async with running_system(config) as system:
        sink = FakeSink()
        assert system.core.tx_audio is not None
        system.core.tx_audio._sink_factory = lambda _c: sink
        client = await start_logged_in(system)
        try:
            yield system, client, sink
        finally:
            await client.close()


def tx_frame(seq: int) -> bytes:
    return HEADER.pack(FRAME_TX_AUDIO, CODEC_RAW, FRAME_SAMPLES, seq) + loud_frame().tobytes()


async def test_browser_audio_plays_during_its_lease_only(
    web: tuple[System, TestClient[Any, Any], FakeSink],
) -> None:
    _, client, sink = web
    ws = await client.ws_connect("/ws", headers=OURS)
    await ws.send_bytes(tx_frame(0))  # before any lease: dropped
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request"))["lease"]
    assert sink.is_open
    for seq in range(6):  # more than the first margin (4), less than margin + slack
        await ws.send_bytes(tx_frame(seq))
    await async_wait_for(lambda: len(sink.frames) == 6)
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    assert not sink.is_open
    await ws.send_bytes(tx_frame(99))  # after the lease: dropped
    assert (await ask(ws, "status"))["ok"]
    assert len(sink.frames) == 6
    await ws.close()


async def test_closing_the_browser_closes_the_card(
    web: tuple[System, TestClient[Any, Any], FakeSink],
) -> None:
    system, client, sink = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="L-D"))["ok"]
    assert (await ask(ws, "tx_request"))["ok"]
    assert sink.is_open
    await ws.close()
    await async_wait_for(lambda: not sink.is_open and not system.ptt_active)


# Stopping must not hold up the radio's return to receive (code review 2026-11-10, point 4)


class SlowSink(FakeSink):
    """A card that takes a frame in 10 ms, as a real one does."""

    def write(self, pcm: npt.NDArray[np.int16]) -> None:
        time.sleep(0.01)
        super().write(pcm)


def test_stopping_with_a_full_queue_is_quick_and_leaves_no_thread() -> None:
    sink = SlowSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    thread = tx._thread
    assert thread is not None
    for _ in range(40):  # more than the queue holds: it is full when we stop
        tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    began = time.monotonic()
    tx.stop()
    assert time.monotonic() - began < 0.5  # was a whole second of the event loop's time
    assert not thread.is_alive()  # was left waiting for ever
    assert not sink.is_open
    assert not tx.active


def test_what_was_queued_is_played_before_the_card_closes() -> None:
    """The tail of the last word is not cut off by the end of the lease."""
    sink = SlowSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    for _ in range(5):
        assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    tx.stop(play_tail=True)
    assert len(sink.frames) == 5


def test_when_the_guard_ended_the_lease_the_queue_is_thrown_away() -> None:
    """Note #189 (18.11): PTT is already released, so nothing more to the card
    (AGENTS.md section 3) — at most the frame being written."""
    sink = SlowSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    for _ in range(8):
        assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    tx.stop()
    assert len(sink.frames) <= 1
    assert not sink.is_open


async def test_only_the_servers_own_release_plays_the_tail(
    web: tuple[System, TestClient[Any, Any], FakeSink],
) -> None:
    system, client, _ = web
    assert system.core.tx_audio is not None
    calls: list[bool] = []
    original = system.core.tx_audio.stop

    def record(*, play_tail: bool = False) -> None:
        calls.append(play_tail)
        original(play_tail=play_tail)

    system.core.tx_audio.stop = record  # type: ignore[method-assign]
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request"))["lease"]
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    assert calls[-1] is True  # the server released: the guard still held PTT
    lease = (await ask(ws, "tx_request"))["lease"]
    tx = system.core._tx
    assert tx is not None
    await system.core._end_tx(tx, notify_reason="client_silent")
    assert calls[-1] is False  # a reason: may race the guard's expiry, no tail
    await ws.receive(timeout=5)  # the tx_ended event
    assert (await ask(ws, "tx_request"))["ok"]
    # The guard ends it on its own (as on expiry, the hard limit or a lost port).
    tx = system.core._tx
    assert tx is not None
    system.core._on_guard_event(
        {"event": "tx_ended", "lease": tx.guard_lease, "reason": "lease_expired"}
    )
    assert calls[-1] is False
    await ws.close()


class StuckSink(FakeSink):
    """A card that stops answering in the middle of a write."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.after_close = 0

    def write(self, pcm: npt.NDArray[np.int16]) -> None:
        self.release.wait(5)
        if not self.is_open:
            self.after_close += 1
            return
        super().write(pcm)


def test_a_card_that_does_not_answer_gets_nothing_after_the_lease() -> None:
    """AGENTS.md section 3: no sound reaches the radio outside a lease, also when the
    playing thread is stuck in the card past the stop's wait (review of 2026-11-10)."""
    from remoteg90ctl.server.tx_audio import JOIN_S  # noqa: PLC0415

    sink = StuckSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    for _ in range(5):
        tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    began = time.monotonic()
    tx.stop()
    assert time.monotonic() - began < JOIN_S + 0.5
    assert not sink.is_open  # closed by stop itself, the thread being stuck
    sink.release.set()  # the card answers again, after the lease
    time.sleep(0.3)
    assert sink.frames == []  # nothing queued was played after the lease ended
    assert sink.after_close <= 1  # at most the frame it was stuck on, refused as closed


# The jitter margin (note #189, 2026-11-18): late frames over LTE left gaps.


def test_the_margin_starts_small_and_grows_after_running_dry() -> None:
    """Latency budget (2026-11-21): 80 ms (4 frames) first, a frame more after
    each time the voice ran dry, up to tx_jitter_ms."""
    sink = FakeSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    assert tx.margin_ms == 80
    for _ in range(3):
        assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    time.sleep(0.1)
    assert sink.frames == []  # still collecting
    assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    wait_for(lambda: len(sink.frames) == 4)
    wait_for(lambda: tx.margin_ms == 100)  # ran dry
    for _ in range(4):
        assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    time.sleep(0.1)
    assert len(sink.frames) == 4  # collecting the larger margin
    tx.stop(play_tail=True)  # a release: what is queued is played
    assert len(sink.frames) == 8


def test_a_burst_after_a_stall_keeps_the_delay_small() -> None:
    """The oldest frames beyond the margin go: the voice stays about 80 ms late."""
    sink = SlowSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    for _ in range(14):  # a stall's worth arriving at once
        tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    wait_for(lambda: tx._queue.qsize() == 0)
    assert len(sink.frames) <= 4 + 3 + 1  # margin + slack (+ one being written)
    assert tx.dropped >= 6
    tx.stop()


def test_the_margin_never_exceeds_the_configured_limit() -> None:
    sink = FakeSink()
    tx = TxAudio(replace(CONFIG, tx_jitter_ms=100.0), sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    for _ in range(4):
        for _ in range(tx.margin_ms // 20):  # just the margin: it plays, then runs dry
            tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
        wait_for(lambda: tx._queue.qsize() == 0)
        time.sleep(0.06)
    assert tx.margin_ms == 100
    tx.stop()


def test_server_audio_is_not_delayed() -> None:
    """FT8/FT4 is paced on the server: a margin would move its timing."""
    sink = FakeSink()
    tx = TxAudio(CONFIG, sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    assert tx.feed_pcm(owner, loud_frame())
    wait_for(lambda: len(sink.frames) == 1)
    tx.stop()


def test_no_margin_when_it_is_off() -> None:
    sink = FakeSink()
    tx = TxAudio(replace(CONFIG, tx_jitter_ms=0.0), sink_factory=lambda _c: sink)
    owner = object()
    tx.start(owner)
    assert tx.feed(owner, CODEC_RAW, loud_frame().tobytes())
    wait_for(lambda: len(sink.frames) == 1)
    tx.stop()


async def test_a_renewal_tells_the_page_about_its_voice_path(
    web: tuple[System, TestClient[Any, Any], FakeSink],
) -> None:
    """Shown next to PTT (2026-11-21): margin, queue, gaps, per lease."""
    _, client, sink = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(ws, "tx_request"))["lease"]
    for seq in range(5):
        await ws.send_bytes(tx_frame(seq))
    await async_wait_for(lambda: len(sink.frames) == 5)
    answer = await ask(ws, "tx_renew", lease=lease)
    audio = answer["audio"]
    assert audio["margin_ms"] >= 80
    assert audio["played"] == 5
    assert set(audio) == {"margin_ms", "queued_ms", "card_ms", "ran_dry", "dropped", "played"}
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    lease = (await ask(ws, "tx_request"))["lease"]
    assert (await ask(ws, "tx_renew", lease=lease))["audio"]["played"] == 0  # a new lease
    await ws.close()
