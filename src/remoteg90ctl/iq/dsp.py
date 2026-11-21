# SPDX-License-Identifier: GPL-3.0-or-later
"""Signal processing for the waterfall: I/Q mapping, correction, spectrum lines.

Pure computation, no I/O. The pipeline per block of card frames:

1. :func:`frames_to_iq` — left/right channels to complex I/Q (mapping and sign
   are configurable: they depend on the radio and the wiring, AGENTS.md item 2);
2. :class:`IqCorrector` — DC removal, amplitude balance and phase correction
   (items 2b, 2c);
3. :class:`SpectrumLines` — Nuttall window, FFT, power in dBFS, averaged to a
   fixed number of lines per second regardless of the sample rate;
4. :func:`quantize` — 8-bit values for the wire.

Ideas from SDR++ (GPL-3.0; window, fixed line rate, DC removal before the
FFT) — see docs/third-party.md. No code is copied.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Frames = NDArray[np.float32]
Iq = NDArray[np.complex64]
Spectrum = NDArray[np.float32]

# Time constant of the DC estimate; long enough to keep real signals near 0 Hz.
DC_TIME_CONSTANT_S = 0.5
_TINY = 1e-20


def frames_to_iq(frames: Frames, *, swap_channels: bool, invert_q: bool) -> Iq:
    """Complex I/Q from card frames of shape ``(n, 2)`` (left, right)."""
    # Filled in place: ``i + 1j * q`` would go through complex128 (twice the work).
    iq = np.empty(frames.shape[0], dtype=np.complex64)
    iq.real = frames[:, 1] if swap_channels else frames[:, 0]
    iq.imag = frames[:, 0] if swap_channels else frames[:, 1]
    if invert_q:
        np.negative(iq.imag, out=iq.imag)
    return iq


@dataclass(slots=True)
class IqCorrector:
    """Removes DC and corrects amplitude and phase imbalance between I and Q.

    ``amplitude_balance`` multiplies Q; ``phase_correction_deg`` removes the part
    of Q that leaks in from I (Q is assumed to lag by that angle).
    """

    sample_rate: int
    dc_removal: bool = True
    amplitude_balance: float = 1.0
    phase_correction_deg: float = 0.0
    _dc: complex = 0j

    def correct(self, iq: Iq) -> Iq:
        if iq.size == 0:
            return iq
        out = iq
        if self.dc_removal:
            alpha = min(1.0, iq.size / (DC_TIME_CONSTANT_S * self.sample_rate))
            self._dc += alpha * (complex(np.mean(iq)) - self._dc)
            out = out - np.complex64(self._dc)
        if self.amplitude_balance != 1.0 or self.phase_correction_deg != 0.0:
            phi = np.deg2rad(self.phase_correction_deg)
            i = out.real
            q = out.imag * self.amplitude_balance
            q = (q - i * np.sin(phi)) / np.cos(phi)
            corrected = np.empty(out.shape, dtype=np.complex64)
            corrected.real, corrected.imag = i, q
            out = corrected
        return out.astype(np.complex64, copy=False)


def nuttall(n: int) -> NDArray[np.float64]:
    """Nuttall window (4 terms, continuous first derivative)."""
    k = np.arange(n) * (2 * np.pi / n)
    return 0.355768 - 0.487396 * np.cos(k) + 0.144232 * np.cos(2 * k) - 0.012604 * np.cos(3 * k)


class SpectrumLines:
    """Turns a continuous I/Q stream into waterfall lines at a fixed rate.

    Each line is the power spectrum in dBFS, ``fft_size`` bins from the lowest to
    the highest frequency (DC in the middle). A full-scale complex tone reads
    0 dBFS. When a line spans more samples than one FFT, the FFTs it covers are
    averaged (power); when it spans fewer, consecutive FFTs overlap.
    """

    def __init__(self, sample_rate: int, fft_size: int, line_rate: float) -> None:
        if fft_size < 2 or fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two")
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.line_rate = line_rate
        self._hop = sample_rate / line_rate  # samples per line, may be fractional
        # Single precision throughout: a float64 window would turn every block
        # (and the FFT) into complex128, about twice the CPU for the same picture.
        window = nuttall(fft_size)
        self._window = window.astype(np.float32)
        self._norm = float(np.sum(window)) ** 2
        self._buffer = np.zeros(0, dtype=np.complex64)
        self._next_line_at = self._hop  # position of the next line end in the stream
        self._position = 0  # samples consumed into the buffer so far

    @property
    def bin_hz(self) -> float:
        return self.sample_rate / self.fft_size

    def feed(self, iq: Iq) -> list[Spectrum]:
        """Add samples; return the lines completed by them (oldest first)."""
        self._buffer = np.concatenate((self._buffer, iq))
        self._position += iq.size
        lines: list[Spectrum] = []
        start_of_buffer = self._position - self._buffer.size
        while self._next_line_at <= self._position:
            end = round(self._next_line_at) - start_of_buffer
            begin = max(0, end - max(self.fft_size, round(self._hop)))
            lines.append(self._line(self._buffer[begin:end]))
            self._next_line_at += self._hop
        # Keep what the next line may still need.
        keep = max(self.fft_size, round(self._hop))
        if self._buffer.size > keep:
            self._buffer = self._buffer[-keep:]
        return lines

    def _line(self, samples: Iq) -> Spectrum:
        n = self.fft_size
        if samples.size < n:
            samples = np.concatenate((np.zeros(n - samples.size, np.complex64), samples))
        count = samples.size // n
        blocks = samples[samples.size - count * n :].reshape(count, n)
        spectrum = np.fft.fft(blocks * self._window, axis=1)
        power = spectrum.real * spectrum.real + spectrum.imag * spectrum.imag
        mean = (power[0] if count == 1 else np.mean(power, axis=0)) / np.float32(self._norm)
        return (10 * np.log10(np.fft.fftshift(mean) + np.float32(_TINY))).astype(
            np.float32, copy=False
        )


def bin_frequencies(fft_size: int, sample_rate: int) -> NDArray[np.float64]:
    """Offset from the I/Q centre of every bin of a line, in Hz."""
    return np.fft.fftshift(np.fft.fftfreq(fft_size, 1 / sample_rate))


def quantize(line: Spectrum, min_db: float, max_db: float) -> NDArray[np.uint8]:
    """Map dB values to 0..255 over ``[min_db, max_db]``; values outside are clipped."""
    if max_db <= min_db:
        raise ValueError("max_db must be greater than min_db")
    scaled = (line - min_db) * (255.0 / (max_db - min_db))
    return np.clip(np.round(scaled), 0, 255).astype(np.uint8)
