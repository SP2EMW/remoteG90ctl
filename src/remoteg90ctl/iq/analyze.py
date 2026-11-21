# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline analysis of an I/Q recording, for the measurements of AGENTS.md section 8.

Answers the questions of items 1 to 2c from a WAV file: level and DC of each
channel, channel balance, noise floor, the strongest signals and, for a known
signal, the image rejection (power at +f against -f).
"""

from dataclasses import dataclass

import numpy as np

from remoteg90ctl.iq.dsp import Frames, SpectrumLines, bin_frequencies, frames_to_iq

ANALYSIS_FFT_SIZE = 16_384
PEAK_COUNT = 5
_TINY = 1e-20


def _db(power: float) -> float:
    return float(10 * np.log10(power + _TINY))


@dataclass(frozen=True, slots=True)
class Peak:
    offset_hz: float
    level_dbfs: float


@dataclass(frozen=True, slots=True)
class Analysis:
    seconds: float
    rms_dbfs: tuple[float, float]  # left, right
    dc_dbfs: tuple[float, float]
    balance_db: float  # left minus right RMS
    noise_floor_dbfs: float  # median bin of the averaged spectrum
    peaks: tuple[Peak, ...]
    image_rejection_db: float | None  # for signal_hz, if given
    bin_hz: float


def analyze(
    frames: Frames,
    sample_rate: int,
    *,
    swap_channels: bool = False,
    invert_q: bool = False,
    signal_hz: float | None = None,
) -> Analysis:
    """Measure a recording; ``signal_hz`` is the offset of a known signal from the centre."""
    if frames.shape[0] < ANALYSIS_FFT_SIZE:
        raise ValueError(f"recording too short: need at least {ANALYSIS_FFT_SIZE} frames")
    left = frames[:, 0].astype(np.float64)
    right = frames[:, 1].astype(np.float64)
    rms = (_db(float(np.mean(left**2))), _db(float(np.mean(right**2))))
    dc = (_db(float(np.mean(left)) ** 2), _db(float(np.mean(right)) ** 2))

    iq = frames_to_iq(frames, swap_channels=swap_channels, invert_q=invert_q)
    # One line over the whole recording: the average of all FFTs it contains.
    lines = SpectrumLines(sample_rate, ANALYSIS_FFT_SIZE, sample_rate / frames.shape[0])
    spectrum = lines.feed(iq)[-1].astype(np.float64)
    freqs = bin_frequencies(ANALYSIS_FFT_SIZE, sample_rate)

    order = np.argsort(spectrum)[::-1]
    peaks: list[Peak] = []
    guard_bins = 8  # neighbours of a peak belong to it (window main lobe)
    for index in order:
        if all(
            abs(int(index) - round(p.offset_hz / lines.bin_hz + ANALYSIS_FFT_SIZE / 2)) > guard_bins
            for p in peaks
        ):
            peaks.append(Peak(float(freqs[index]), float(spectrum[index])))
        if len(peaks) == PEAK_COUNT:
            break

    image = None
    if signal_hz is not None:
        image = _level_near(spectrum, freqs, signal_hz) - _level_near(spectrum, freqs, -signal_hz)

    return Analysis(
        seconds=frames.shape[0] / sample_rate,
        rms_dbfs=rms,
        dc_dbfs=dc,
        balance_db=rms[0] - rms[1],
        noise_floor_dbfs=float(np.median(spectrum)),
        peaks=tuple(peaks),
        image_rejection_db=image,
        bin_hz=lines.bin_hz,
    )


def _level_near(spectrum: np.ndarray, freqs: np.ndarray, hz: float, bins: int = 4) -> float:
    """Total power of the bins around ``hz`` (the window spreads a tone over a few bins)."""
    centre = int(np.argmin(np.abs(freqs - hz)))
    part = spectrum[max(0, centre - bins) : centre + bins + 1]
    return _db(float(np.sum(10 ** (part / 10))))
