# SPDX-License-Identifier: GPL-3.0-or-later
"""Receive audio sources: the DE-19 card (mono, ALSA) or a synthetic tone for tests."""

import numpy as np

from remoteg90ctl.config import AudioConfig, AudioSourceKind
from remoteg90ctl.iq.source import AlsaSource, FrameSource, ThreadedSource
from remoteg90ctl.iq.wav import Frames

AUDIO_SAMPLE_RATE = 48_000
AUDIO_BLOCK_S = 0.02  # one Opus frame


class SyntheticAudio(ThreadedSource):
    """A 1 kHz tone with some noise, as one channel."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE, tone_hz: float = 1000.0) -> None:
        super().__init__(sample_rate)
        self._tone_hz = tone_hz
        self._phase = 0
        self._rng = np.random.default_rng(1)

    def next_block(self, count: int) -> Frames:
        t = (self._phase + np.arange(count)) / self.sample_rate
        self._phase += count
        tone = 0.2 * np.sin(2 * np.pi * self._tone_hz * t)
        noise = 0.01 * self._rng.standard_normal(count)
        block: Frames = (tone + noise).astype(np.float32).reshape(-1, 1)
        return block


def make_audio_source(config: AudioConfig) -> FrameSource:
    """The source selected by ``[audio] source``; capture only, never playback."""
    if config.source is AudioSourceKind.ALSA and config.device is not None:
        # 20 ms blocks (one Opus frame) and PortAudio's low latency: about
        # 30-80 ms less delay than the I/Q card's settings (2026-11-21).
        return AlsaSource(
            config.device, AUDIO_SAMPLE_RATE, channels=1, block_s=AUDIO_BLOCK_S, latency="low"
        )
    return SyntheticAudio()
