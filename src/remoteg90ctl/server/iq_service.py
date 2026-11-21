# SPDX-License-Identifier: GPL-3.0-or-later
"""The server's I/Q input: source → correction → waterfall lines, and recordings.

The server is the only process that opens the I/Q card (decisions.md D35).
Frames arrive on the source's thread and are handed to the event loop; when
the loop falls behind, blocks are dropped and counted instead of queueing
without bound. A source that cannot be opened (card unplugged), that stops,
or that stays open but delivers nothing (seen after a USB reset of the card)
is closed and retried.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from remoteg90ctl.config import IqConfig
from remoteg90ctl.iq.dsp import Frames, IqCorrector, Spectrum, SpectrumLines, frames_to_iq
from remoteg90ctl.iq.source import FrameSource, SourceError, make_source
from remoteg90ctl.iq.wav import write_wav
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

SourceFactory = Callable[[IqConfig], FrameSource]

MAX_RECORD_S = 30.0
MAX_PENDING_BLOCKS = 20  # about 1 s of 50 ms blocks
RETRY_S = 5.0
# No frames for this long from an open source = stalled.
STALL_S = 2.0
_TINY = 1e-20


class IqUnavailableError(Exception):
    """The I/Q input is not running or a recording is not possible."""


class IqService:
    def __init__(
        self,
        config: IqConfig,
        *,
        source_factory: SourceFactory = make_source,
        retry_s: float = RETRY_S,
        stall_s: float = STALL_S,
    ) -> None:
        self.config = config
        self._source_factory = source_factory
        self._retry_s = retry_s
        self._stall_s = stall_s
        self._last_frames_at = 0.0  # time.monotonic() of the last block or of the open
        self._source: FrameSource | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._corrector = IqCorrector(
            config.sample_rate,
            dc_removal=config.dc_removal,
            amplitude_balance=config.amplitude_balance,
            phase_correction_deg=config.phase_correction_deg,
        )
        self._lines = SpectrumLines(config.sample_rate, config.fft_size, config.line_rate)
        self._pending = 0
        self._dropped = 0
        self._line_count = 0
        self._last_line: Spectrum | None = None
        self._level_dbfs: float | None = None
        self._error: str | None = "not started"
        self._recording: list[Frames] | None = None
        self._record_frames = 0
        self._record_done: asyncio.Future[None] | None = None
        self.on_line: Callable[[Spectrum], None] | None = None
        # Whether anyone wants the lines (a browser watching the waterfall).
        # Without one the spectrum is not computed: on a small machine by the
        # radio every bit of CPU counts. The level and recordings go on regardless.
        self.wanted: Callable[[], bool] = lambda: True
        self._idle = False

    # Lifecycle

    async def run(self) -> None:
        """Keep the source running until cancelled; reopen it when it fails or stalls."""
        self._loop = asyncio.get_running_loop()
        next_open = 0.0
        try:
            while True:
                now = time.monotonic()
                problem = self._source_problem(now)
                if problem is not None:
                    logger.error("iq_source_failed", extra=kv(reason=problem))
                    self.close()
                    self._error = f"{problem}; retrying"
                    next_open = now + self._retry_s
                if self._source is None and now >= next_open:
                    self._open()
                    next_open = now + self._retry_s
                await asyncio.sleep(min(self._retry_s, self._stall_s / 2))
        finally:
            self.close()

    def _source_problem(self, now: float) -> str | None:
        source = self._source
        if source is None:
            return None
        if not source.alive:
            return "the I/Q input stopped"
        if now - self._last_frames_at > self._stall_s:
            return f"no I/Q data for {self._stall_s:g} s (card reset or stalled)"
        return None

    def _open(self) -> None:
        try:
            source = self._source_factory(self.config)
            source.start(self._from_source_thread)
        except SourceError as exc:
            if self._error != str(exc):
                logger.error("iq_source_unavailable", extra=kv(error=exc))
            self._error = str(exc)
            return
        self._source = source
        self._error = None
        self._last_frames_at = time.monotonic()  # grace period for the first block
        logger.info(
            "iq_source_started",
            extra=kv(source=self.config.source, sample_rate=self.config.sample_rate),
        )

    def close(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            source.stop()
        if self._record_done is not None and not self._record_done.done():
            self._record_done.set_exception(IqUnavailableError("the I/Q input stopped"))

    # Frames

    def _from_source_thread(self, frames: Frames) -> None:
        self._last_frames_at = time.monotonic()
        loop = self._loop
        if loop is None:
            return
        if self._pending >= MAX_PENDING_BLOCKS:
            self._dropped += 1
            return
        self._pending += 1
        loop.call_soon_threadsafe(self._process, frames)

    def _process(self, frames: Frames) -> None:
        self._pending -= 1
        self._record(frames)
        if not self.wanted():
            # I^2 + Q^2 straight from the card: the level without the conversion.
            power = np.mean(np.sum(np.square(frames, dtype=np.float64), axis=-1))
            self._level_dbfs = float(10 * np.log10(power + _TINY))
            self._idle = True
            return
        if self._idle:
            # Resuming: no line may join audio from before the pause.
            self._idle = False
            self._lines = SpectrumLines(
                self.config.sample_rate, self.config.fft_size, self.config.line_rate
            )
        iq = frames_to_iq(
            frames, swap_channels=self.config.swap_channels, invert_q=self.config.invert_q
        )
        iq = self._corrector.correct(iq)
        power = np.mean(iq.real * iq.real + iq.imag * iq.imag)  # |iq|^2 without the root
        self._level_dbfs = float(10 * np.log10(power + _TINY))
        for line in self._lines.feed(iq):
            self._line_count += 1
            self._last_line = line
            if self.on_line is not None:
                self.on_line(line)

    # Recording (measurement tool, AGENTS.md section 8)

    async def record(self, seconds: float) -> Path:
        """Record ``seconds`` of raw card frames to a WAV file; return its path."""
        if not 0 < seconds <= MAX_RECORD_S:
            raise ValueError(f"seconds must be between 0 and {MAX_RECORD_S:g}")
        if self._source is None or not self._source.alive:
            raise IqUnavailableError(f"the I/Q input is not running: {self._error}")
        if self._recording is not None:
            raise IqUnavailableError("a recording is already in progress")
        self._record_frames = round(seconds * self.config.sample_rate)
        self._recording = []
        self._record_done = asyncio.get_running_loop().create_future()
        try:
            await asyncio.wait_for(self._record_done, seconds + 5.0)
            frames = np.concatenate(self._recording)[: self._record_frames]
        except TimeoutError:
            raise IqUnavailableError("no I/Q data arrived during the recording") from None
        finally:
            self._recording = None
            self._record_done = None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self.config.recordings_dir / f"iq-{stamp}-{self.config.sample_rate}.wav"
        try:
            write_wav(path, frames, self.config.sample_rate, self.config.sample_bits)
        except OSError as exc:
            raise IqUnavailableError(f"cannot write {path}: {exc}") from exc
        logger.info("iq_recorded", extra=kv(path=path, seconds=seconds))
        return path

    def _record(self, frames: Frames) -> None:
        if self._recording is None or self._record_done is None:
            return
        self._recording.append(frames)
        collected = sum(f.shape[0] for f in self._recording)
        if collected >= self._record_frames and not self._record_done.done():
            self._record_done.set_result(None)

    # State

    @property
    def last_line(self) -> Spectrum | None:
        return self._last_line

    def status(self) -> dict[str, object]:
        source = self._source
        return {
            "source": str(self.config.source),
            "running": source is not None and source.alive and self._error is None,
            "error": self._error,
            "sample_rate": self.config.sample_rate,
            "sample_bits": self.config.sample_bits,
            "fft_size": self.config.fft_size,
            "line_rate": self.config.line_rate,
            "lines": self._line_count,
            "level_dbfs": round(self._level_dbfs, 1) if self._level_dbfs is not None else None,
            "idle": self._idle,
            "overruns": source.overruns if source is not None else 0,
            "dropped_blocks": self._dropped,
            "center_offset_hz": dict(self.config.center_offset_hz),
            "calibrated": bool(self.config.center_offset_hz),
            "passband_hz": {mode: list(edges) for mode, edges in self.config.passband_hz.items()},
        }
