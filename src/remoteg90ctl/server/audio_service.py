# SPDX-License-Identifier: GPL-3.0-or-later
"""Receive audio for one browser (stage 3): the DE-19 card, framed and encoded.

The card is opened for capture only; nothing here plays sound to the radio.
Audio reaches the DE-19 only in the data modes U-D/L-D (measured), so the
server sets USB/LSB as data modes with ``radio.ssb_data_mode``.

One listener at a time (owner's decision): a second subscription is refused.
Frames of 20 ms go to the listener's queue, which drops the oldest frame when
full, like the waterfall. Each frame is a binary WebSocket message::

    u8 type (0x02), u8 codec (0 raw PCM s16le, 1 Opus), u16 samples, u32 seq,
    then the payload.

Before frame 0 and then once a second comes a clock frame (stage 6, D52): the
UTC time of the first sample of the next audio frame, by this server's clock,
so the page can cut FT8/FT4 cycles out of the audio whatever the network
delay::

    u8 type (0x04), u8 flags (bit 0: clock synchronised, bit 1: unknown),
    u16 0, u32 seq of that audio frame, f64 UTC seconds.

The time is when the block of samples reached the server, less its length
(the card's own delay is not known), smoothed so that 20 ms frames stay 20 ms
apart.
"""

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from remoteg90ctl.audio.opus import COMPLEXITY, OpusEncoder, OpusUnavailableError
from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE, make_audio_source
from remoteg90ctl.config import AudioConfig
from remoteg90ctl.iq.source import FrameSource, SourceError
from remoteg90ctl.iq.wav import Frames
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.clock import ClockWatch
from remoteg90ctl.server.mixer import OutputLevel, Runner, card_of, run_amixer

logger = logging.getLogger(__name__)

FRAME_AUDIO = 0x02
FRAME_TX_AUDIO = 0x03  # browser -> server, same header (stage 4)
CODEC_RAW = 0
CODEC_OPUS = 1
HEADER = struct.Struct("<BBHI")
FRAME_CLOCK = 0x04
CLOCK = struct.Struct("<BBHId")
CLOCK_SYNCHRONIZED = 0x01
CLOCK_UNKNOWN = 0x02
CLOCK_EVERY_FRAMES = 50  # once a second
# A measured start further than this from the running one restarts the count
# (a gap in the card's audio); smaller differences are smoothed out.
CLOCK_JUMP_S = 0.1
CLOCK_SMOOTHING = 0.02
FRAME_SAMPLES = AUDIO_SAMPLE_RATE // 50  # 20 ms
# Bitrates offered in the browser (owner's choice, 2026-11-07), kbit/s.
# 8 kbit/s for a slow mobile link (owner's request 2026-11-15).
OPUS_BITRATES = (8, 16, 24, 32, 48)
# Frames waiting for a browser: a stall of the link up to 2 s loses nothing
# (FT8/FT4 decodes whole cycles; the page trims what is late for listening).
# Longer, the page is disconnected anyway (it stopped reading).
QUEUE_FRAMES = 100  # 2 s
# Card blocks waiting for the event loop: 2.5 s (blocks of 20 ms since #195).
MAX_PENDING_BLOCKS = 125
RETRY_S = 2.0
STALL_S = 2.0

SourceFactory = Callable[[AudioConfig], FrameSource]


class AudioBusyError(Exception):
    """Another browser is listening."""


@dataclass
class Listener:
    codec: int
    bitrate_kbps: int | None
    encoder: OpusEncoder | None
    queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(QUEUE_FRAMES))
    seq: int = 0
    dropped: int = 0

    def offer(self, frame: bytes) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest: late audio is worse than a gap
            self.dropped += 1
        self.queue.put_nowait(frame)


def encode_frame(codec: int, seq: int, payload: bytes, samples: int = FRAME_SAMPLES) -> bytes:
    return HEADER.pack(FRAME_AUDIO, codec, samples, seq & 0xFFFFFFFF) + payload


def encode_clock(seq: int, utc_s: float, synchronized: bool | None) -> bytes:
    flags = CLOCK_UNKNOWN if synchronized is None else CLOCK_SYNCHRONIZED * synchronized
    return CLOCK.pack(FRAME_CLOCK, flags, 0, seq & 0xFFFFFFFF, utc_s)


class AudioService:
    def __init__(  # noqa: PLR0913  (keyword-only seams: clocks, state directory, amixer)
        self,
        config: AudioConfig,
        source_factory: SourceFactory = make_audio_source,
        *,
        clock: ClockWatch | None = None,
        wall_clock: Callable[[], float] = time.time,
        state_dir: Path | None = None,
        mixer: Runner = run_amixer,
    ) -> None:
        self.config = config
        # The card's output level: drives the radio (server/mixer.py).
        self.output = OutputLevel(card_of(config.device), state_dir, mixer)
        self._source_factory = source_factory
        # FT8/FT4 cycles are cut from the audio by this clock (stage 6, D52).
        self.clock = clock if clock is not None else ClockWatch()
        self._wall_clock = wall_clock
        # UTC time of the first sample in _buffer (None until the first block).
        self._buffer_utc: float | None = None
        self._source: FrameSource | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending = 0
        self._buffer = np.zeros(0, dtype=np.int16)
        self._last_frames_at = 0.0
        self._error: str | None = "not started"
        self.listener: Listener | None = None

    # Lifecycle

    async def run(self) -> None:
        """Keep the card open until cancelled; reopen it when it fails or stalls."""
        self._loop = asyncio.get_running_loop()
        clock_task = asyncio.create_task(self.clock.run())
        try:
            while True:
                source = self._source
                stalled = time.monotonic() - self._last_frames_at > STALL_S
                if source is not None and (not source.alive or stalled):
                    logger.error("audio_source_failed")
                    self._close_source()
                    self._error = "audio input stopped; retrying"
                if self._source is None:
                    await self._open()
                await asyncio.sleep(RETRY_S)
        finally:
            clock_task.cancel()
            self._close_source()

    async def _open(self) -> None:
        try:
            source = self._source_factory(self.config)
            source.start(self._from_source_thread)
        except SourceError as exc:
            if self._error != str(exc):
                logger.error("audio_source_unavailable", extra=kv(error=exc))
            self._error = str(exc)
            return
        self._source = source
        self._error = None
        self._last_frames_at = time.monotonic()
        logger.info("audio_source_started", extra=kv(source=self.config.source))
        await self._set_mixer()

    async def _set_mixer(self) -> None:
        """The kept output level; capture gain and the card's own AGC off
        (clipping measured 2026-11-07)."""
        await self.output.restore()
        gain, device = self.config.capture_gain_db, self.config.device
        if gain is None or device is None:
            return
        card = device.split("CARD=", 1)[1].split(",", 1)[0]
        for args in (
            ("sset", "Mic", "capture", f"{gain:g}dB"),
            ("sset", "Auto Gain Control", "off"),
        ):
            try:
                process = await asyncio.create_subprocess_exec(
                    "amixer",
                    "-q",
                    "-c",
                    card,
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, err = await process.communicate()
            except OSError as exc:
                logger.warning("audio_mixer_failed", extra=kv(error=exc))
                return
            if process.returncode != 0:
                logger.warning(
                    "audio_mixer_failed", extra=kv(args=" ".join(args), error=err.decode().strip())
                )

    def _close_source(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            source.stop()

    def status(self) -> dict[str, object]:
        listener = self.listener
        return {
            "running": self._source is not None,
            "error": self._error,
            "listening": listener is not None,
            "dropped": listener.dropped if listener is not None else 0,
            "clock_synchronized": self.clock.synchronized,
        }

    # Listener

    def subscribe(
        self, codec: str, bitrate_kbps: int | None, complexity: int | None = None
    ) -> Listener:
        """Raises AudioBusyError, ValueError or OpusUnavailableError.

        ``complexity``: the Opus encoder's 0-10 (the page's choice, owner's
        request 2026-11-15: lower costs the server less CPU); None = 10.
        """
        if self.listener is not None:
            raise AudioBusyError("another browser is listening")
        self.check_listen(codec, bitrate_kbps, complexity)
        level = COMPLEXITY if complexity is None else complexity
        if codec == "raw":
            listener = Listener(CODEC_RAW, None, None)
        elif codec == "opus" and bitrate_kbps in OPUS_BITRATES:
            listener = Listener(
                CODEC_OPUS,
                bitrate_kbps,
                OpusEncoder(AUDIO_SAMPLE_RATE, bitrate_kbps * 1000, level),
            )
        else:
            raise ValueError(f"codec must be 'raw' or 'opus' with a bitrate in {OPUS_BITRATES}")
        self.listener = listener
        logger.info(
            "audio_listener", extra=kv(codec=codec, bitrate_kbps=bitrate_kbps, complexity=level)
        )
        return listener

    @staticmethod
    def check_listen(codec: str, bitrate_kbps: int | None, complexity: int | None) -> None:
        """Raises ValueError for what ``subscribe`` would refuse."""
        if complexity is not None and not 0 <= complexity <= COMPLEXITY:
            raise ValueError(f"complexity must be 0 to {COMPLEXITY}")
        if codec != "raw" and not (codec == "opus" and bitrate_kbps in OPUS_BITRATES):
            raise ValueError(f"codec must be 'raw' or 'opus' with a bitrate in {OPUS_BITRATES}")

    def unsubscribe(self, listener: Listener) -> None:
        if self.listener is listener:
            self.listener = None
            if listener.encoder is not None:
                listener.encoder.close()

    # Frames

    def _from_source_thread(self, frames: Frames) -> None:
        self._last_frames_at = time.monotonic()
        received_utc = self._wall_clock()
        loop = self._loop
        if loop is None or self._pending >= MAX_PENDING_BLOCKS:
            return
        self._pending += 1
        loop.call_soon_threadsafe(self._process, frames, received_utc)

    def _process(self, frames: Frames, received_utc: float) -> None:
        self._pending -= 1
        mono = frames[:, 0] if frames.ndim == 2 else frames
        pcm = np.clip(mono * 32767.0, -32768, 32767).astype(np.int16)
        self._track_time(received_utc - (len(self._buffer) + len(pcm)) / AUDIO_SAMPLE_RATE)
        self._buffer = np.concatenate((self._buffer, pcm))
        while len(self._buffer) >= FRAME_SAMPLES:
            frame, self._buffer = self._buffer[:FRAME_SAMPLES], self._buffer[FRAME_SAMPLES:]
            frame_utc = self._buffer_utc or 0.0
            self._buffer_utc = frame_utc + FRAME_SAMPLES / AUDIO_SAMPLE_RATE
            listener = self.listener
            if listener is not None:
                self._send(listener, frame, frame_utc)

    def _track_time(self, measured_utc: float) -> None:
        """Follow the measured start of the buffer, smoothing the jitter of arrival."""
        running = self._buffer_utc
        if running is None or abs(measured_utc - running) > CLOCK_JUMP_S:
            self._buffer_utc = measured_utc
        else:
            self._buffer_utc = running + (measured_utc - running) * CLOCK_SMOOTHING

    def _send(self, listener: Listener, frame: npt.NDArray[np.int16], frame_utc: float) -> None:
        if listener.encoder is None:
            payload = frame.astype("<i2").tobytes()
        else:
            try:
                payload = listener.encoder.encode(frame)
            except OpusUnavailableError as exc:
                logger.error("audio_encode_failed", extra=kv(error=exc))
                return
        if listener.seq % CLOCK_EVERY_FRAMES == 0:
            listener.offer(encode_clock(listener.seq, frame_utc, self.clock.synchronized))
        listener.offer(encode_frame(listener.codec, listener.seq, payload))
        listener.seq += 1
