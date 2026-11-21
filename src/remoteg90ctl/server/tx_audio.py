# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio from the browser to the radio (stage 4), only during a transmit lease.

AGENTS.md section 3: no sound reaches the radio's audio input outside an
active lease, not even silence. The playback stream of the DE-19 card is
opened when a lease starts and closed when it ends, whatever ends it; frames
arriving outside a lease, or from a session that does not hold it, are
dropped. Every frame passes a limiter here, on the server, whatever the
browser did (D39): the peak never exceeds ``audio.tx_peak_dbfs``.
"""

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np
import numpy.typing as npt

from remoteg90ctl.audio.opus import OpusDecoder, OpusUnavailableError
from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE
from remoteg90ctl.config import AudioConfig, AudioSourceKind
from remoteg90ctl.iq.source import _ALSA_NAME, SourceError, alsa_card_index
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

CODEC_RAW = 0
CODEC_OPUS = 1
FRAME_MS = 20
FRAME_SAMPLES = AUDIO_SAMPLE_RATE * FRAME_MS // 1000
# Browser voice: an adaptive margin (latency budget, 2026-11-21). It starts at
# MARGIN_MIN_MS, grows by a frame after each time the queue ran dry, up to
# [audio] tx_jitter_ms, and shrinks by a frame after MARGIN_SHRINK_S without
# one. Frames beyond the margin plus TRIM_SLACK are dropped, the oldest first,
# so a burst after a stall does not add delay.
MARGIN_MIN_MS = 80
MARGIN_SHRINK_S = 60.0
TRIM_SLACK = 3
QUEUE_FRAMES = 10  # room above the largest margin
RELEASE = 0.05  # limiter gain recovery per frame (attack is immediate)
POLL_S = 0.02  # how often the playing thread looks for the stop event
JOIN_S = 1.0  # at most this long the event loop waits for a card that does not answer


class PlaybackSink(Protocol):
    def open(self) -> None: ...
    def write(self, pcm: npt.NDArray[np.int16]) -> None: ...
    def close(self) -> None: ...


SinkFactory = Callable[[AudioConfig], PlaybackSink]
FrameQueue = queue.Queue[npt.NDArray[np.int16]]


class AlsaSink:
    """The DE-19 card's playback side through PortAudio, blocking writes."""

    def __init__(self, device: str) -> None:
        match = _ALSA_NAME.fullmatch(device)
        if match is None:
            raise SourceError(f"not an ALSA device by card name: {device!r}")
        self._card_id = match.group("card")
        self._dev = int(match.group("dev"))
        self._stream: object = None
        self.latency_ms: float | None = None  # the card's, once open

    def open(self) -> None:
        import sounddevice  # type: ignore[import-untyped]  # noqa: PLC0415

        wanted = f"(hw:{alsa_card_index(self._card_id)},{self._dev})"
        devices = [
            i
            for i, d in enumerate(sounddevice.query_devices())
            if wanted in d["name"] and d["max_output_channels"] >= 1
        ]
        if not devices:
            raise SourceError(f"PortAudio does not list {wanted} for playback")
        # Blocks of one 20 ms frame and PortAudio's low latency: the card adds
        # tens of ms less to the voice (latency budget, 2026-11-21).
        stream = sounddevice.RawOutputStream(
            device=devices[0],
            channels=1,
            samplerate=AUDIO_SAMPLE_RATE,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            latency="low",
        )
        stream.start()
        self._stream = stream
        self.latency_ms = round(float(stream.latency) * 1000, 1)
        logger.info(
            "alsa_output_opened",
            extra=kv(card=self._card_id, latency_ms=round(float(stream.latency) * 1000, 1)),
        )

    def write(self, pcm: npt.NDArray[np.int16]) -> None:
        if self._stream is not None:
            self._stream.write(pcm.tobytes())  # type: ignore[attr-defined]

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()  # type: ignore[attr-defined]
            stream.close()  # type: ignore[attr-defined]


class NullSink:
    """For the synthetic audio source: frames are counted, nothing is played."""

    def __init__(self) -> None:
        self.frames = 0

    def open(self) -> None:
        self.frames = 0

    def write(self, pcm: npt.NDArray[np.int16]) -> None:
        self.frames += 1

    def close(self) -> None:
        pass


def make_sink(config: AudioConfig) -> PlaybackSink:
    if config.source is AudioSourceKind.ALSA and config.device is not None:
        return AlsaSink(config.device)
    return NullSink()


class Limiter:
    """Peak limiter: the gain drops at once to keep the ceiling and recovers slowly."""

    def __init__(self, ceiling_dbfs: float) -> None:
        self.ceiling = 32767.0 * 10 ** (ceiling_dbfs / 20)
        self.gain = 1.0

    def apply(self, pcm: npt.NDArray[np.int16]) -> npt.NDArray[np.int16]:
        peak = float(np.max(np.abs(pcm.astype(np.float64)))) if len(pcm) else 0.0
        target = min(1.0, self.ceiling / peak) if peak > 0 else 1.0
        if target < self.gain:
            self.gain = target
        else:
            self.gain += (target - self.gain) * RELEASE
        out: npt.NDArray[np.int16] = np.clip(pcm * self.gain, -self.ceiling, self.ceiling).astype(
            np.int16
        )
        return out


class TxAudio:
    """Plays browser audio to the radio while, and only while, a lease is active."""

    def __init__(self, config: AudioConfig, sink_factory: SinkFactory = make_sink) -> None:
        self.config = config
        self._sink_factory = sink_factory
        self._owner: object | None = None
        self._sink: PlaybackSink | None = None
        # Browser audio waits for `_margin` frames before it plays, and again
        # after the queue ran dry (note #189): late frames leave no gaps.
        self._margin_max = round(config.tx_jitter_ms / FRAME_MS)
        self._margin_min = min(self._margin_max, MARGIN_MIN_MS // FRAME_MS)
        self._margin = self._margin_min
        self._server_audio = False
        self._queue: FrameQueue = queue.Queue(QUEUE_FRAMES + self._margin_max)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._decoder: OpusDecoder | None = None
        self._limiter = Limiter(config.tx_peak_dbfs)
        self.dropped = 0
        self.played = 0
        self.ran_dry = 0
        self._card_ms: float | None = None

    @property
    def active(self) -> bool:
        return self._owner is not None

    def start(self, owner: object) -> None:
        """A lease started for ``owner`` (a session): open the card for playback."""
        self.stop()
        try:
            sink = self._sink_factory(self.config)
            sink.open()
            decoder = OpusDecoder(AUDIO_SAMPLE_RATE)
        except (SourceError, OpusUnavailableError, OSError) as exc:
            logger.error("tx_audio_unavailable", extra=kv(error=exc))
            return
        # Counted per lease (the page shows them while transmitting).
        self.played = self.dropped = self.ran_dry = 0
        self._sink, self._decoder, self._owner = sink, decoder, owner
        latency = getattr(sink, "latency_ms", None)
        self._card_ms = latency if isinstance(latency, int | float) else None
        self._limiter = Limiter(self.config.tx_peak_dbfs)
        self._queue = queue.Queue(QUEUE_FRAMES + self._margin_max)
        self._server_audio = False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._play, args=(sink, self._queue, self._stop_event), daemon=True
        )
        self._thread.start()
        logger.info("tx_audio_started")

    def stop(self, *, play_tail: bool = False) -> None:
        """The lease ended (any reason): close the card; nothing more reaches the radio.

        ``play_tail``: play what is queued (the tail of the last word) first —
        only when the server ends the lease itself and the guard still holds
        PTT until it is told. When the guard has already released PTT (expiry,
        hard limit, lost port, lost guard), the queue is thrown away: no sound
        to the radio outside a lease (AGENTS.md section 3; note #189, 18.11).
        """
        owner, self._owner = self._owner, None
        sink, self._sink = self._sink, None
        if self._thread is not None:
            if not play_tail:
                with contextlib.suppress(queue.Empty):
                    while True:
                        self._queue.get_nowait()
            # An event, not a marker in the queue: a full queue (a card that is
            # slow) has no room for a marker, which made this wait a whole second
            # and leave the thread behind (code review 2026-11-10, point 4). The
            # thread plays what is still queued, then closes the card itself.
            self._stop_event.set()
            self._thread.join(JOIN_S)
            if self._thread.is_alive() and sink is not None:
                # The card does not answer: what is still queued must not reach
                # the radio after the lease (AGENTS.md section 3), so it is thrown
                # away and the card closed from here, as before the event.
                logger.error("tx_audio_stuck", extra=kv(queued=self._queue.qsize()))
                with contextlib.suppress(queue.Empty):
                    while True:
                        self._queue.get_nowait()
                with contextlib.suppress(Exception):
                    sink.close()
            self._thread = None
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None
        if owner is not None:
            logger.info(
                "tx_audio_stopped",
                extra=kv(played=self.played, dropped=self.dropped, margin_ms=self.margin_ms),
            )

    def feed(self, owner: object, codec: int, payload: bytes) -> bool:
        """A frame from a browser; played only if ``owner`` holds the active lease."""
        if self._owner is None or owner is not self._owner:
            self.dropped += 1
            return False
        try:
            if codec == CODEC_OPUS and self._decoder is not None:
                pcm = self._decoder.decode(payload)
            elif codec == CODEC_RAW and len(payload) % 2 == 0:
                pcm = np.frombuffer(payload, dtype="<i2").astype(np.int16)
            else:
                self.dropped += 1
                return False
        except OpusUnavailableError:
            self.dropped += 1
            return False
        return self._queue_frame(pcm)

    def feed_pcm(self, owner: object, pcm: npt.NDArray[np.int16]) -> bool:
        """Audio made on the server (FT8/FT4, stage 7): the same lease check and limiter."""
        if self._owner is None or owner is not self._owner:
            self.dropped += 1
            return False
        self._server_audio = True  # paced here, no network jitter: no margin (FT8 timing)
        return self._queue_frame(pcm)

    def _queue_frame(self, pcm: npt.NDArray[np.int16]) -> bool:
        frame = self._limiter.apply(pcm)
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            if self._server_audio:
                # FT8/FT4 is paced here: a full queue is a card that does not
                # take audio, which ends the playback (ft8_playback_failed).
                self.dropped += 1
                return False
            # Browser voice, full after a burst: the oldest goes, so the delay
            # does not grow.
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
                self.dropped += 1
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                self.dropped += 1
                return False
        return True

    @property
    def margin_ms(self) -> int:
        return self._margin * FRAME_MS

    def stats(self) -> dict[str, object]:
        """This lease's voice path on the server, for the page (tx_renew)."""
        return {
            "margin_ms": self.margin_ms,
            "queued_ms": self._queue.qsize() * FRAME_MS,
            "card_ms": self._card_ms,
            "ran_dry": self.ran_dry,
            "dropped": self.dropped,
            "played": self.played,
        }

    def _play(
        self,
        sink: PlaybackSink,
        frames: FrameQueue,
        stop: threading.Event,
    ) -> None:
        primed = False
        calm_since = time.monotonic()
        try:
            while True:
                margin = 0 if self._server_audio else self._margin
                if not primed and frames.qsize() < margin and not stop.is_set():
                    stop.wait(POLL_S)  # collecting the margin; wakes at once on stop
                    continue
                primed = True
                if not self._server_audio and not stop.is_set():
                    # More than the margin waiting (a burst): drop the oldest.
                    while frames.qsize() > margin + TRIM_SLACK:
                        with contextlib.suppress(queue.Empty):
                            frames.get_nowait()
                            self.dropped += 1
                    if (
                        time.monotonic() - calm_since > MARGIN_SHRINK_S
                        and self._margin > self._margin_min
                    ):
                        self._margin -= 1
                        calm_since = time.monotonic()
                try:
                    frame = frames.get(timeout=POLL_S)
                except queue.Empty:
                    if stop.is_set():
                        return  # everything queued has been played
                    if not self._server_audio:
                        # Ran dry: a larger margin from now on, collected again.
                        self._margin = min(self._margin_max, self._margin + 1)
                        self.ran_dry += 1
                        calm_since = time.monotonic()
                        logger.info("tx_audio_ran_dry", extra=kv(margin_ms=self.margin_ms))
                    primed = False
                    continue
                try:
                    sink.write(frame)
                    self.played += 1
                except Exception:
                    logger.exception("tx_audio_write_failed")
                    return
        finally:
            with contextlib.suppress(Exception):
                sink.close()
