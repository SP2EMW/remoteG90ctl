# SPDX-License-Identifier: GPL-3.0-or-later
"""Stereo WAV files of I/Q recordings (16 or 24 bit), using only :mod:`wave` and numpy.

Frames are float32 arrays of shape ``(n, 2)`` in ``[-1, 1)``: column 0 is the
left channel, column 1 the right one, exactly as the card delivered them —
the I/Q mapping is applied later (:mod:`remoteg90ctl.iq.dsp`).
"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

Frames = NDArray[np.float32]

SUPPORTED_BITS = (16, 24)


@dataclass(frozen=True, slots=True)
class Recording:
    frames: Frames
    sample_rate: int
    bits: int


def write_wav(path: Path, frames: Frames, sample_rate: int, bits: int) -> None:
    """Write ``frames`` as a stereo PCM WAV file."""
    if bits not in SUPPORTED_BITS:
        raise ValueError(f"unsupported sample width: {bits} bits")
    if frames.ndim != 2 or frames.shape[1] != 2:
        raise ValueError("frames must have shape (n, 2)")
    full_scale = float(2 ** (bits - 1))
    ints = np.clip(np.round(frames * full_scale), -full_scale, full_scale - 1).astype("<i4")
    if bits == 16:
        data = ints.astype("<i2").tobytes()
    else:
        # Little-endian 24 bit: the three low bytes of each 32-bit sample.
        data = ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(bits // 8)
        out.setframerate(sample_rate)
        out.writeframes(data)


def read_wav(path: Path) -> Recording:
    """Read a stereo 16- or 24-bit PCM WAV file."""
    with wave.open(str(path), "rb") as src:
        channels, width, rate = src.getnchannels(), src.getsampwidth(), src.getframerate()
        raw = src.readframes(src.getnframes())
    if channels != 2:
        raise ValueError(f"{path}: expected 2 channels (I and Q), got {channels}")
    bits = width * 8
    if bits not in SUPPORTED_BITS:
        raise ValueError(f"{path}: unsupported sample width {bits} bits")
    if bits == 16:
        ints = np.frombuffer(raw, dtype="<i2").astype(np.int32)
    else:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
    frames = (ints.reshape(-1, 2) / float(2 ** (bits - 1))).astype(np.float32)
    return Recording(frames, rate, bits)
