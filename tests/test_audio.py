# SPDX-License-Identifier: GPL-3.0-or-later
"""Receive audio (stage 3): Opus, frames, one listener, configuration, WebSocket.

The source is synthetic; no test opens a sound card (AGENTS.md rule 9).
"""

import asyncio
import copy
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient

from remoteg90ctl.audio.opus import COMPLEXITY, OpusEncoder, _library
from remoteg90ctl.config import AudioConfig, AudioSourceKind, parse_config
from remoteg90ctl.server import clock as clock_module
from remoteg90ctl.server.audio_service import (
    CLOCK,
    CLOCK_SYNCHRONIZED,
    CLOCK_UNKNOWN,
    CODEC_OPUS,
    CODEC_RAW,
    FRAME_AUDIO,
    FRAME_CLOCK,
    FRAME_SAMPLES,
    HEADER,
    AudioBusyError,
    AudioService,
)
from remoteg90ctl.server.clock import (
    MAX_ERROR_S,
    ClockWatch,
    chrony_synchronized,
    chrony_verdict,
    clock_synchronized,
    timedated_synchronized,
)
from server_helpers import System, async_wait_for, running_system
from test_web import OURS, ask, start_logged_in, web_config

SYNTHETIC = AudioConfig(AudioSourceKind.SYNTHETIC, None)


@pytest.mark.parametrize("kbps", [16, 24, 32, 48])
def test_opus_packets_fit_the_bitrate(kbps: int) -> None:
    encoder = OpusEncoder(48_000, kbps * 1000)
    t = np.arange(FRAME_SAMPLES * 50) / 48_000
    pcm = (8000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    sizes = [
        len(encoder.encode(pcm[i : i + FRAME_SAMPLES])) for i in range(0, len(pcm), FRAME_SAMPLES)
    ]
    encoder.close()
    bits_per_s = sum(sizes) * 8  # 50 frames of 20 ms = 1 s
    assert 0.5 * kbps * 1000 < bits_per_s < 1.5 * kbps * 1000


async def test_frames_reach_one_listener_and_a_second_is_refused() -> None:
    service = AudioService(SYNTHETIC)
    task = asyncio.create_task(service.run())
    try:
        listener = service.subscribe("opus", 24)
        clock = await asyncio.wait_for(listener.queue.get(), 2)
        assert CLOCK.unpack(clock)[0] == FRAME_CLOCK
        frame = await asyncio.wait_for(listener.queue.get(), 2)
        kind, codec, samples, seq = HEADER.unpack_from(frame)
        assert (kind, codec, samples, seq) == (FRAME_AUDIO, CODEC_OPUS, FRAME_SAMPLES, 0)
        with pytest.raises(AudioBusyError):
            service.subscribe("raw", None)
        service.unsubscribe(listener)
        raw = service.subscribe("raw", None)
        await asyncio.wait_for(raw.queue.get(), 2)  # the clock frame
        frame = await asyncio.wait_for(raw.queue.get(), 2)
        assert HEADER.unpack_from(frame)[1] == CODEC_RAW
        assert len(frame) == HEADER.size + 2 * FRAME_SAMPLES
        assert service.status()["listening"] is True
    finally:
        task.cancel()


def _feed(service: AudioService, blocks: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Blocks of (samples, arrival time) in; (seq, UTC) of every clock frame out."""
    listener = service.subscribe("raw", None)
    for samples, arrival in blocks:
        service._process(np.zeros(samples, dtype=np.float32), arrival)
    clocks = []
    while not listener.queue.empty():
        frame = listener.queue.get_nowait()
        if frame[0] == FRAME_CLOCK:
            _, _, _, seq, utc = CLOCK.unpack(frame)
            clocks.append((seq, utc))
    return clocks


def test_clock_frames_give_the_time_of_the_audio() -> None:
    """Stage 6 (D52): the first sample's UTC time, once a second, without the jitter."""
    service = AudioService(SYNTHETIC)
    rng = np.random.default_rng(1)
    start = 1_762_689_600.0
    block = 480  # 10 ms
    # Each block arrives when its last sample is captured, 0-8 ms late.
    arrivals = [(block, start + (i + 1) * 0.01 + rng.uniform(0, 0.008)) for i in range(120)]
    listener_clocks = _feed(service, arrivals)
    # Queue of 25 frames: only the last clock frames are still there.
    seqs = [seq for seq, _ in listener_clocks]
    assert seqs
    assert all(seq % 50 == 0 for seq in seqs)
    for seq, utc in listener_clocks:
        assert abs(utc - (start + seq * 0.02)) < 0.01


def test_a_gap_in_the_audio_restarts_the_time() -> None:
    service = AudioService(SYNTHETIC)
    start = 1_762_689_600.0
    blocks = [(960, start + (i + 1) * 0.02) for i in range(50)]
    blocks += [(960, start + 5 + (i + 1) * 0.02) for i in range(1)]  # 4 s missing
    clocks = _feed(service, blocks)
    assert clocks[-1] == (50, pytest.approx(start + 5, abs=1e-6))


def test_clock_flags() -> None:
    from remoteg90ctl.server.audio_service import encode_clock  # noqa: PLC0415

    assert CLOCK.unpack(encode_clock(7, 1.5, None))[1] == CLOCK_UNKNOWN
    assert CLOCK.unpack(encode_clock(7, 1.5, True))[1] == CLOCK_SYNCHRONIZED
    assert CLOCK.unpack(encode_clock(7, 1.5, False))[1] == 0


async def test_clock_watch_reports_changes() -> None:
    answers = iter([True, False])

    async def check() -> bool | None:
        return next(answers, False)

    watch = ClockWatch(check)
    task = asyncio.create_task(watch.run())
    await async_wait_for(lambda: watch.synchronized is True)
    task.cancel()
    assert isinstance(await timedated_synchronized(), bool | None)


@pytest.mark.parametrize(
    ("codec", "bitrate"), [("opus", 12), ("opus", 64), ("opus", None), ("mp3", 24)]
)
def test_only_the_chosen_bitrates_and_raw_are_offered(codec: str, bitrate: int | None) -> None:
    with pytest.raises(ValueError, match="codec"):
        AudioService(SYNTHETIC).subscribe(codec, bitrate)


def test_audio_section_is_validated() -> None:
    from test_config import VALID, problems_of  # noqa: PLC0415

    data = copy.deepcopy(VALID)
    data["audio"] = {"source": "alsa", "device": "hw:CARD=G90AUDIO,DEV=0", "capture_gain_db": 14}
    audio = parse_config(data).audio
    assert audio == AudioConfig(AudioSourceKind.ALSA, "hw:CARD=G90AUDIO,DEV=0", 14.0)
    for bad in (
        {"source": "alsa"},
        {"source": "alsa", "device": "hw:1,0"},
        {"source": "x"},
        {"source": "synthetic", "capture_gain_db": 30},
    ):
        data["audio"] = bad
        assert any(p.startswith("audio.") for p in problems_of(data)), bad


@pytest_asyncio.fixture
async def web(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    config = replace(web_config(tmp_path), audio=SYNTHETIC)
    async with running_system(config) as system:
        client = await start_logged_in(system)
        try:
            yield system, client
        finally:
            await client.close()


async def test_browser_listens_and_a_second_browser_is_refused(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = web
    first = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(first, "audio_subscribe", codec="opus", bitrate=16))["ok"]
    while True:  # skip waterfall lines and events until an audio frame arrives
        message = await first.receive(timeout=5)
        if message.type is WSMsgType.BINARY and message.data[0] == FRAME_AUDIO:
            break
    second = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(second, "audio_subscribe", codec="raw"))["error"] == "audio_busy"
    await first.close()
    await async_wait_for(
        lambda: web[0].core.audio is not None and web[0].core.audio.listener is None
    )
    assert (await ask(second, "audio_subscribe", codec="raw"))["ok"]
    await second.close()


async def test_a_wrong_request_keeps_the_listening_it_would_replace(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """Audit 2026-11-15, A3: refused before the earlier listener is dropped."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "audio_subscribe", codec="opus", bitrate=16))["ok"]
    audio = system.core.audio
    assert audio is not None
    listener = audio.listener
    for wrong in ({"complexity": 11}, {"bitrate": 12}, {"codec": "mp3"}):
        fields = {"codec": "opus", "bitrate": 16, **wrong}
        assert (await ask(ws, "audio_subscribe", **fields))["error"] == "invalid_value"
        assert audio.listener is listener
    await ws.close()


async def test_network_usage_is_counted_per_browser_and_server(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "audio_subscribe", codec="raw"))["ok"]
    assert (await ask(ws, "system_subscribe"))["ok"]
    stats: list[dict[str, Any]] = []
    while len(stats) < 2:
        message = await ws.receive(timeout=5)
        if message.type is WSMsgType.TEXT and message.json().get("event") == "system_stats":
            stats.append(message.json())
    network = stats[-1]["network"]
    browser, server = network["browser"], network["server"]
    assert browser["audio"] > stats[0]["network"]["browser"]["audio"]
    assert browser["received"] > 0
    assert server["audio"] >= browser["audio"]
    assert network["connected_s"] > 0
    await ws.close()


# ``chronyc -c -n tracking`` on the owner's ThinkPad (2026-11-11); fields 5, 11
# and 12 are the offset, root delay and root dispersion, 14 the leap status.
TRACKING = (
    "B949D303,185.73.211.3,3,1794406446.472574551,-0.001707808,0.002696855,"
    "0.001107728,-5.297,0.034,0.203,0.023821797,0.015111951,1028.3,Normal"
)


def tracking(offset: float = -0.0017, delay: float = 0.0238, dispersion: float = 0.0151) -> str:
    fields = TRACKING.split(",")
    fields[4], fields[10], fields[11] = str(offset), str(delay), str(dispersion)
    return ",".join(fields)


def test_chrony_within_the_limit_is_synchronised() -> None:
    assert chrony_verdict(TRACKING + "\n") is True
    assert chrony_verdict(tracking(offset=0.1, delay=0.04, dispersion=0.05)) is True  # 0.17 s


@pytest.mark.parametrize(
    ("offset", "delay", "dispersion"),
    [
        (2.3, 0.02, 0.01),  # the laptop of 2026-11-11, with no warning then
        (-0.25, 0.0, 0.0),  # fast or slow alike
        (0.0, 0.0, 28.8),  # just started: root dispersion of tens of seconds
        (0.1, 0.1, 0.06),  # 0.21 s: the bound counts, not the offset alone
    ],
)
def test_chrony_beyond_the_limit_is_not_synchronised(
    offset: float, delay: float, dispersion: float
) -> None:
    assert abs(offset) + delay / 2 + dispersion > MAX_ERROR_S
    assert chrony_verdict(tracking(offset, delay, dispersion)) is False


def test_chrony_without_a_source_is_not_synchronised() -> None:
    fields = TRACKING.split(",")
    fields[13] = "Not synchronised"
    assert chrony_verdict(",".join(fields)) is False


@pytest.mark.parametrize(
    "line",
    [
        "",
        "506 Cannot talk to daemon",
        "a,b,c",
        tracking(offset=float("nan")),
        TRACKING.replace("-0.001707808", "x"),
    ],
)
def test_chrony_answer_that_cannot_be_read_is_unknown(line: str) -> None:
    assert chrony_verdict(line) is None


async def test_chrony_answers_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def answer(value: bool | None) -> bool | None:
        return value

    flag = tmp_path / "synchronized"
    flag.touch()
    # chrony says no: timedated and the timesyncd file are not asked.
    monkeypatch.setattr(clock_module, "chrony_synchronized", lambda: answer(False))
    monkeypatch.setattr(clock_module, "timedated_synchronized", lambda: answer(True))
    assert await clock_synchronized(flag) is False
    # No chrony: timedated counts.
    monkeypatch.setattr(clock_module, "chrony_synchronized", lambda: answer(None))
    assert await clock_synchronized(flag) is True
    monkeypatch.setattr(clock_module, "timedated_synchronized", lambda: answer(False))
    assert await clock_synchronized(flag) is False
    assert isinstance(await chrony_synchronized(), bool | None)  # the real one: never raises


async def test_without_timedated_the_timesyncd_file_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A system without D-Bus (DietPi, for one): timedatectl cannot answer."""

    async def no_answer() -> None:
        return None

    monkeypatch.setattr(clock_module, "chrony_synchronized", no_answer)
    monkeypatch.setattr(clock_module, "timedated_synchronized", no_answer)
    flag = tmp_path / "synchronized"
    assert await clock_synchronized(flag) is None  # unknown: FT8/FT4 transmission refused
    flag.touch()
    assert await clock_synchronized(flag) is True


def test_opus_complexity_is_full_at_every_bitrate() -> None:
    """Speech and FT8/FT4 reception are encoded at the highest quality."""
    import ctypes  # noqa: PLC0415

    assert COMPLEXITY == 10
    for kbps, expected in ((16, 10), (48, 10)):
        encoder = OpusEncoder(48_000, kbps * 1000)
        value = ctypes.c_int32()
        _library().opus_encoder_ctl(
            ctypes.c_void_p(encoder._handle),
            4011,
            ctypes.byref(value),  # OPUS_GET_COMPLEXITY
        )
        encoder.close()
        assert value.value == expected


def _complexity(encoder: OpusEncoder) -> int:
    import ctypes  # noqa: PLC0415

    value = ctypes.c_int32()
    _library().opus_encoder_ctl(ctypes.c_void_p(encoder._handle), 4011, ctypes.byref(value))
    return value.value


def test_the_page_chooses_the_complexity_and_8_kbit_s() -> None:
    """Owner's request 2026-11-15: 8 kbit/s, and the complexity from the page."""
    service = AudioService(SYNTHETIC)
    listener = service.subscribe("opus", 8, 3)
    assert listener.encoder is not None
    assert _complexity(listener.encoder) == 3
    service.unsubscribe(listener)
    listener = service.subscribe("opus", 16)
    assert listener.encoder is not None
    assert _complexity(listener.encoder) == 10  # the default
    service.unsubscribe(listener)
    for bad in (-1, 11):
        with pytest.raises(ValueError, match="complexity"):
            service.subscribe("opus", 16, bad)
    assert service.listener is None
