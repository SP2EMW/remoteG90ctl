# SPDX-License-Identifier: GPL-3.0-or-later
"""I/Q mapping, correction, spectrum lines, WAV files and analysis (stage 2a).

Everything runs on the synthetic generator; no test opens a sound card
(AGENTS.md rule 9).
"""

from pathlib import Path

import numpy as np
import pytest

from remoteg90ctl.iq.analyze import analyze
from remoteg90ctl.iq.dsp import (
    IqCorrector,
    SpectrumLines,
    bin_frequencies,
    frames_to_iq,
    nuttall,
    quantize,
)
from remoteg90ctl.iq.source import SourceError, SyntheticIq, Tone, alsa_card_index
from remoteg90ctl.iq.wav import read_wav, write_wav

RATE = 48_000
FFT = 4096


def peak_offset(line: np.ndarray, fft_size: int = FFT, rate: int = RATE) -> float:
    return float(bin_frequencies(fft_size, rate)[int(np.argmax(line))])


def one_line(frames: np.ndarray, *, swap: bool = False, invert_q: bool = False) -> np.ndarray:
    iq = frames_to_iq(frames, swap_channels=swap, invert_q=invert_q)
    lines = SpectrumLines(RATE, FFT, RATE / frames.shape[0])
    return lines.feed(iq)[-1]


# Mapping and sign (AGENTS.md item 2)


@pytest.mark.parametrize("offset", [5_000.0, -7_500.0])
def test_tone_lands_in_the_right_bin_with_the_right_sign(offset: float) -> None:
    frames = SyntheticIq(RATE, (Tone(offset, -20),)).generate(RATE)
    assert peak_offset(one_line(frames)) == pytest.approx(offset, abs=RATE / FFT)


def test_swapped_or_inverted_mapping_mirrors_the_spectrum() -> None:
    frames = SyntheticIq(RATE, (Tone(5_000.0, -20),)).generate(RATE)
    assert peak_offset(one_line(frames, invert_q=True)) == pytest.approx(-5_000, abs=12)
    assert peak_offset(one_line(frames, swap=True)) == pytest.approx(-5_000, abs=12)
    assert peak_offset(one_line(frames, swap=True, invert_q=True)) == pytest.approx(5_000, abs=12)


def test_full_scale_tone_reads_zero_dbfs() -> None:
    frames = SyntheticIq(RATE, (Tone(3_000.0, 0.0),), noise_dbfs=-200).generate(RATE)
    assert float(np.max(one_line(frames))) == pytest.approx(0.0, abs=0.5)


# Correction (items 2b, 2c)


def image_rejection(frames: np.ndarray, corrector: IqCorrector | None, offset: float) -> float:
    iq = frames_to_iq(frames, swap_channels=False, invert_q=False)
    if corrector is not None:
        iq = corrector.correct(iq)
    result = analyze(
        np.stack((iq.real, iq.imag), axis=1).astype(np.float32), RATE, signal_hz=offset
    )
    assert result.image_rejection_db is not None
    return result.image_rejection_db


def test_balance_and_phase_correction_improve_image_rejection() -> None:
    gen = SyntheticIq(RATE, (Tone(6_000.0, -20),), noise_dbfs=-140, q_gain=1.05, q_phase_deg=3)
    frames = gen.generate(RATE)
    before = image_rejection(frames, None, 6_000.0)
    corrector = IqCorrector(
        RATE, dc_removal=False, amplitude_balance=1 / 1.05, phase_correction_deg=3
    )
    after = image_rejection(frames, corrector, 6_000.0)
    assert before < 35  # a few per cent of imbalance: roughly 30 dB
    assert after > before + 30


def test_dc_removal() -> None:
    gen = SyntheticIq(RATE, (Tone(4_000.0, -30),), noise_dbfs=-140, dc=0.05 + 0.02j)
    corrector = IqCorrector(RATE)
    for _ in range(50):  # let the estimate settle (time constant 0.5 s)
        out = corrector.correct(
            frames_to_iq(gen.generate(RATE // 10), swap_channels=False, invert_q=False)
        )
    assert abs(complex(np.mean(out))) < 1e-3


# Line rate


@pytest.mark.parametrize(("rate", "line_rate"), [(48_000, 20.0), (96_000, 20.0), (48_000, 5.0)])
def test_fixed_number_of_lines_per_second(rate: int, line_rate: float) -> None:
    gen = SyntheticIq(rate, (Tone(1_000.0, -30),))
    lines = SpectrumLines(rate, FFT, line_rate)
    produced = 0
    for _ in range(50):  # 1 s in 20 ms blocks
        iq = frames_to_iq(gen.generate(rate // 50), swap_channels=False, invert_q=False)
        produced += len(lines.feed(iq))
    assert produced == pytest.approx(line_rate, abs=1)


def test_lines_have_fft_size_bins() -> None:
    lines = SpectrumLines(RATE, 2048, 10.0)
    out = lines.feed(np.zeros(RATE, dtype=np.complex64))
    assert len(out) == 10
    assert all(line.shape == (2048,) for line in out)


# Quantization


def test_quantize_clips_instead_of_wrapping() -> None:
    line = np.array([-200.0, -130.0, -80.0, -30.0, 10.0], dtype=np.float32)
    assert quantize(line, -130.0, -30.0).tolist() == [0, 0, 128, 255, 255]


def test_quantize_rejects_empty_range() -> None:
    with pytest.raises(ValueError, match="max_db"):
        quantize(np.zeros(4, dtype=np.float32), -30.0, -30.0)


# WAV


@pytest.mark.parametrize("bits", [16, 24])
def test_wav_round_trip(tmp_path: Path, bits: int) -> None:
    frames = SyntheticIq(RATE, (Tone(2_000.0, -6),), noise_dbfs=-60).generate(4_800)
    path = tmp_path / "iq.wav"
    write_wav(path, frames, RATE, bits)
    recording = read_wav(path)
    assert (recording.sample_rate, recording.bits) == (RATE, bits)
    assert recording.frames.shape == frames.shape
    assert np.max(np.abs(recording.frames - frames)) <= 2.0 / 2 ** (bits - 1)


def test_wav_full_scale_is_clipped(tmp_path: Path) -> None:
    frames = np.array([[1.5, -1.5]], dtype=np.float32)
    write_wav(tmp_path / "clip.wav", frames, RATE, 24)
    back = read_wav(tmp_path / "clip.wav").frames
    assert back[0, 0] == pytest.approx(1.0, abs=1e-6)
    assert back[0, 1] == -1.0


# Analysis


def test_analysis_reports_known_parameters() -> None:
    gen = SyntheticIq(
        RATE, (Tone(8_000.0, -20), Tone(-3_000.0, -50)), noise_dbfs=-120, dc=0.01 + 0j, q_gain=0.9
    )
    result = analyze(gen.generate(2 * RATE), RATE, signal_hz=8_000.0)
    assert result.seconds == pytest.approx(2.0)
    assert result.peaks[0].offset_hz == pytest.approx(8_000.0, abs=result.bin_hz)
    assert result.peaks[0].level_dbfs == pytest.approx(-20.0, abs=1.5)
    assert any(abs(p.offset_hz + 3_000.0) <= result.bin_hz for p in result.peaks)
    assert result.dc_dbfs[0] == pytest.approx(-40.0, abs=0.5)  # 0.01 on the left channel
    assert result.balance_db == pytest.approx(-20 * np.log10(0.9), abs=0.3)
    assert result.image_rejection_db is not None
    assert 20 < result.image_rejection_db < 35  # 10 % gain error: about 26 dB


def test_analysis_needs_enough_frames() -> None:
    with pytest.raises(ValueError, match="too short"):
        analyze(np.zeros((100, 2), dtype=np.float32), RATE)


# ALSA card by name (AGENTS.md rule 8, D35)

CARDS = """\
 0 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0xec240000 irq 134
 1 [Device         ]: USB-Audio - USB Audio Device
 2 [G90IQ          ]: USB-Audio - USB Advanced Audio Device
"""


def test_card_name_resolves_to_current_number(tmp_path: Path) -> None:
    cards = tmp_path / "cards"
    cards.write_text(CARDS)
    assert alsa_card_index("G90IQ", cards) == 2


def test_missing_card_is_reported(tmp_path: Path) -> None:
    cards = tmp_path / "cards"
    cards.write_text(CARDS)
    with pytest.raises(SourceError, match="no ALSA card named 'G90'"):
        alsa_card_index("G90", cards)


# ---- Single precision (the server's CPU on a small computer) ----


def test_iq_mapping_is_single_precision_and_keeps_swap_and_sign() -> None:
    frames = np.array([[0.1, 0.2], [0.3, -0.4]], dtype=np.float32)
    iq = frames_to_iq(frames, swap_channels=False, invert_q=True)
    assert iq.dtype == np.complex64
    np.testing.assert_allclose(iq, [0.1 - 0.2j, 0.3 + 0.4j], rtol=1e-6)
    swapped = frames_to_iq(frames, swap_channels=True, invert_q=False)
    np.testing.assert_allclose(swapped, [0.2 + 0.1j, -0.4 + 0.3j], rtol=1e-6)


def test_spectrum_line_in_single_precision_matches_double() -> None:
    """The float32 path draws the same picture as the former float64 one."""
    rng = np.random.default_rng(7)
    n, rate = 4096, 48_000
    t = np.arange(3 * n) / rate
    iq = (0.3 * np.exp(2j * np.pi * 5_000 * t) + 1e-3 * rng.standard_normal(3 * n)).astype(
        np.complex64
    )
    lines = SpectrumLines(rate, n, rate / n).feed(iq)
    assert lines
    assert lines[0].dtype == np.float32
    window = nuttall(n)
    block = iq[:n].astype(np.complex128)  # the first line: the first fft_size samples
    reference = np.abs(np.fft.fft(block * window)) ** 2 / np.sum(window) ** 2
    expected = 10 * np.log10(np.fft.fftshift(reference) + 1e-20)
    strong = expected > -100  # where float32 rounding is far below the 8-bit display step
    np.testing.assert_allclose(lines[0][strong], expected[strong], atol=0.01)
