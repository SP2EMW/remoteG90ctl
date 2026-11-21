# SPDX-License-Identifier: GPL-3.0-or-later
"""Sources of card frames: ALSA (the I/Q card), a synthetic generator and a WAV file.

Every source delivers frames of shape ``(n, 2)`` (left, right) as float32 to a
callback, from its own thread. Only the server opens the I/Q card (decisions.md
D35); tests use the synthetic and file sources and never touch a sound card
(AGENTS.md rule 9).
"""

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from remoteg90ctl.config import IqConfig, IqSourceKind
from remoteg90ctl.iq.wav import Frames, read_wav
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

FrameCallback = Callable[[Frames], None]

# Length of one delivered block: 50 ms is one waterfall line at the default 20
# lines per second, so each block wakes the server once (20 ms woke it 50 times
# a second for the same picture). The waterfall is 30 ms later, unnoticeable.
BLOCK_S = 0.05
_ALSA_NAME = re.compile(r"(?:plug)?hw:CARD=(?P<card>[A-Za-z0-9_]+),DEV=(?P<dev>[0-9]+)")
_PROC_CARDS = Path("/proc/asound/cards")


class SourceError(Exception):
    """The source cannot be opened or failed."""


class FrameSource(Protocol):
    sample_rate: int

    def start(self, callback: FrameCallback) -> None: ...

    def stop(self) -> None: ...

    @property
    def overruns(self) -> int: ...

    @property
    def alive(self) -> bool:
        """False once the source has stopped delivering (e.g. the card was unplugged)."""
        ...


# Synthetic


@dataclass(frozen=True, slots=True)
class Tone:
    offset_hz: float  # from the I/Q centre; negative = below
    level_dbfs: float


@dataclass(slots=True)
class SyntheticIq:
    """Generates card frames of known content, including the defects to correct.

    The ideal complex signal (tones plus noise) is turned into left/right
    channels the way a radio with these defects would deliver it: I on the left,
    Q on the right, Q scaled by ``q_gain`` and lagging by ``q_phase_deg``, plus
    ``dc`` on both channels.
    """

    sample_rate: int
    tones: tuple[Tone, ...] = ()
    noise_dbfs: float = -100.0
    dc: complex = 0j
    q_gain: float = 1.0
    q_phase_deg: float = 0.0
    seed: int = 1
    _n: int = 0
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def generate(self, count: int) -> Frames:
        t = (self._n + np.arange(count)) / self.sample_rate
        self._n += count
        z = np.zeros(count, dtype=np.complex128)
        for tone in self.tones:
            z += 10 ** (tone.level_dbfs / 20) * np.exp(2j * np.pi * tone.offset_hz * t)
        sigma = 10 ** (self.noise_dbfs / 20) / np.sqrt(2)
        z += sigma * (self._rng.standard_normal(count) + 1j * self._rng.standard_normal(count))
        phi = np.deg2rad(self.q_phase_deg)
        i = z.real + self.dc.real
        q = self.q_gain * (z.imag * np.cos(phi) + z.real * np.sin(phi)) + self.dc.imag
        return np.stack((i, q), axis=1).astype(np.float32)


class ThreadedSource:
    """Delivers blocks from ``next_block`` at real-time pace in a thread."""

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def overruns(self) -> int:
        return 0

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def next_block(self, count: int) -> Frames:
        raise NotImplementedError

    def start(self, callback: FrameCallback) -> None:
        self._stop.clear()

        def run() -> None:
            count = round(self.sample_rate * BLOCK_S)
            deadline = time.monotonic()
            while not self._stop.is_set():
                callback(self.next_block(count))
                deadline += BLOCK_S
                self._stop.wait(max(0.0, deadline - time.monotonic()))

        self._thread = threading.Thread(target=run, name="iq-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)
            self._thread = None


class SyntheticSource(ThreadedSource):
    def __init__(self, generator: SyntheticIq) -> None:
        super().__init__(generator.sample_rate)
        self.generator = generator

    def next_block(self, count: int) -> Frames:
        return self.generator.generate(count)


class FileSource(ThreadedSource):
    """Plays a WAV recording in a loop."""

    def __init__(self, path: Path) -> None:
        try:
            recording = read_wav(path)
        except (OSError, ValueError) as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        self._frames = recording.frames
        rate = recording.sample_rate
        if self._frames.shape[0] == 0:
            raise SourceError(f"{path} contains no frames")
        super().__init__(rate)
        self._pos = 0

    def next_block(self, count: int) -> Frames:
        idx = (self._pos + np.arange(count)) % self._frames.shape[0]
        self._pos = int((self._pos + count) % self._frames.shape[0])
        block: Frames = self._frames[idx]
        return block


# ALSA


def alsa_card_index(card_id: str, cards_file: Path = _PROC_CARDS) -> int:
    """Current number of the ALSA card with this id (the name set by udev)."""
    try:
        text = cards_file.read_text()
    except OSError as exc:
        raise SourceError(f"cannot read {cards_file}: {exc}") from exc
    for match in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]", text, re.MULTILINE):
        if match.group(2) == card_id:
            return int(match.group(1))
    raise SourceError(
        f"no ALSA card named {card_id!r} (see {cards_file}); is the I/Q card plugged in?"
    )


class AlsaSource:
    """The I/Q card through PortAudio's ALSA backend.

    PortAudio names ALSA devices by card *number* ("… (hw:2,0)"), so the stable
    card name from the configuration is resolved to the current number first.

    The stream is read with blocking reads in a thread of our own, not through a
    PortAudio callback: cffi callbacks need write+execute memory, which the
    server unit forbids (``MemoryDenyWriteExecute=yes``).
    """

    def __init__(
        self,
        device: str,
        sample_rate: int,
        channels: int = 2,
        *,
        block_s: float = BLOCK_S,
        latency: str | float = "high",
    ) -> None:
        """``block_s`` and ``latency`` (PortAudio's): the I/Q card keeps the
        defaults; receive audio asks for 20 ms and "low" (latency budget,
        2026-11-21)."""
        match = _ALSA_NAME.fullmatch(device)
        if match is None:
            raise SourceError(f"not an ALSA device by card name: {device!r}")
        self.sample_rate = sample_rate
        self.channels = channels
        self._block_s = block_s
        self._latency = latency
        self._card_id = match.group("card")
        self._dev = int(match.group("dev"))
        self._overruns = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def overruns(self) -> int:
        return self._overruns

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, callback: FrameCallback) -> None:
        # Imported here: tests never load PortAudio. No type stubs exist.
        import sounddevice  # type: ignore[import-untyped]  # noqa: PLC0415

        index = alsa_card_index(self._card_id)
        wanted = f"(hw:{index},{self._dev})"
        devices = [
            i
            for i, d in enumerate(sounddevice.query_devices())
            if wanted in d["name"] and d["max_input_channels"] >= self.channels
        ]
        if not devices:
            raise SourceError(f"PortAudio does not list {wanted} for card {self._card_id!r}")
        block = round(self.sample_rate * self._block_s)
        try:
            stream = sounddevice.InputStream(
                device=devices[0],
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype="float32",
                blocksize=block,
                latency=self._latency,
            )
            stream.start()
        except Exception as exc:  # PortAudioError and friends
            raise SourceError(
                f"cannot open {self._card_id} at {self.sample_rate} Hz: {exc}"
            ) from exc
        # What the card adds to the delay, for the latency budget (2026-11-21).
        logger.info(
            "alsa_input_opened",
            extra=kv(
                card=self._card_id,
                block_ms=round(self._block_s * 1000),
                latency_ms=round(float(stream.latency) * 1000, 1),
            ),
        )
        self._stop.clear()

        def run() -> None:
            try:
                while not self._stop.is_set():
                    data, overflowed = stream.read(block)
                    if overflowed:
                        self._overruns += 1
                    callback(data)
            except Exception:
                logger.exception("iq_alsa_read_failed")
            finally:
                stream.stop()
                stream.close()

        self._thread = threading.Thread(target=run, name="iq-alsa", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)
            self._thread = None


def make_source(config: IqConfig) -> FrameSource:
    """The source selected by ``[iq] source``."""
    match config.source:
        case IqSourceKind.ALSA if config.device is not None:
            return AlsaSource(config.device, config.sample_rate)
        case IqSourceKind.FILE if config.file is not None:
            return FileSource(config.file)
        case IqSourceKind.SYNTHETIC:
            # A demonstration signal: two tones and noise.
            tones = (Tone(5_000.0, -40.0), Tone(-12_000.0, -60.0))
            return SyntheticSource(SyntheticIq(config.sample_rate, tones, noise_dbfs=-110.0))
    # Config validation requires device/file for these sources.
    raise SourceError(f"[iq] source {config.source} is not fully configured")
