// SPDX-License-Identifier: GPL-3.0-or-later
//
// Receive audio processing in the browser (stage 8, decisions.md D39 and D49):
// an adaptive line enhancer (LMS) used two ways — as noise reduction (it keeps
// what is predictable: speech harmonics, CW) and as an automatic notch (it
// removes what is predictable and steady: carriers, heterodynes) — and a
// spectral noise reduction (Wiener-like gain from a tracked noise floor).
// Our own code; the ideas are the classic ones, as in WDSP (Thetis, piHPSDR)
// and no-sdr (MIT), see docs/third-party.md. Pure functions of the samples, no
// Web Audio, so they run in the AudioWorklet and in the tests alike.

// Normalised LMS adaptive line enhancer. Each sample is predicted from the
// samples `delay` steps earlier; `predicted` holds the prediction (the noise
// reduction output) and `error` what was not predicted (the notch output).
export class LineEnhancer {
  // Ring of past samples: history[(position + j) % size] is x[n - 1 - j].
  private readonly history: Float32Array;
  private readonly weights: Float32Array;
  private position = 0;
  predicted = 0;
  error = 0;

  constructor(
    private readonly taps: number,
    private readonly delay: number, // >= 1
    public rate: number, // normalised adaptation step, 0..1
    private readonly leak = 1e-4,
  ) {
    this.history = new Float32Array(taps + delay);
    this.weights = new Float32Array(taps);
  }

  step(x: number): void {
    const size = this.history.length;
    const first = this.position + this.delay - 1; // x[n - delay]
    let y = 0;
    let power = 0;
    for (let i = 0; i < this.taps; i++) {
      const s = this.history[(first + i) % size];
      y += this.weights[i] * s;
      power += s * s;
    }
    const e = x - y;
    const mu = this.rate / (1e-6 + power);
    for (let i = 0; i < this.taps; i++) {
      const s = this.history[(first + i) % size];
      this.weights[i] = this.weights[i] * (1 - this.leak) + mu * e * s;
    }
    this.position = (this.position - 1 + size) % size;
    this.history[this.position] = x;
    this.predicted = y;
    this.error = e;
  }
}

// In-place radix-2 FFT (size a power of two); inverse when `inverse`.
// Nearly the same as fftInPlace in no-sdr (gbozo, GPL-3.0-or-later,
// https://github.com/gbozo/no-sdr): possibly derived from it, see
// docs/third-party.md.
export function fft(re: Float32Array, im: Float32Array, inverse = false): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = ((inverse ? 2 : -2) * Math.PI) / len;
    const wr = Math.cos(angle);
    const wi = Math.sin(angle);
    for (let i = 0; i < n; i += len) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k;
        const b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci;
        const ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
        [cr, ci] = [cr * wr - ci * wi, cr * wi + ci * wr];
      }
    }
  }
  if (inverse) {
    for (let i = 0; i < n; i++) {
      re[i] /= n;
      im[i] /= n;
    }
  }
}

// Spectral noise reduction: short-time Fourier transform with a square-root
// Hann window and 50 % overlap (perfect reconstruction), a noise floor per bin
// that follows the minimum of the smoothed power (rises slowly, falls at once),
// and a gain 1 - strength * noise / power, smoothed over time and never below
// a floor. Latency: one frame (SPECTRAL_FRAME samples, about 21 ms at 48 kHz).
export const SPECTRAL_FRAME = 1024;

export class SpectralNr {
  private readonly hop = SPECTRAL_FRAME / 2;
  private readonly window = new Float32Array(SPECTRAL_FRAME);
  private readonly input = new Float32Array(SPECTRAL_FRAME);
  private readonly output = new Float32Array(SPECTRAL_FRAME);
  private readonly re = new Float32Array(SPECTRAL_FRAME);
  private readonly im = new Float32Array(SPECTRAL_FRAME);
  private readonly smoothed = new Float32Array(SPECTRAL_FRAME / 2 + 1);
  private readonly noise = new Float32Array(SPECTRAL_FRAME / 2 + 1);
  private readonly gains = new Float32Array(SPECTRAL_FRAME / 2 + 1).fill(1);
  private filled = 0;
  private outPos = 0;
  private frames = 0;
  strength = 0.5; // 0..1: how much noise is taken out

  constructor() {
    for (let i = 0; i < SPECTRAL_FRAME; i++) {
      this.window[i] = Math.sqrt(0.5 - 0.5 * Math.cos((2 * Math.PI * i) / SPECTRAL_FRAME));
    }
  }

  // One sample in, one sample out (delayed by SPECTRAL_FRAME samples).
  step(x: number): number {
    this.input[this.hop + this.filled] = x;
    const y = this.output[this.outPos];
    this.outPos++;
    if (++this.filled === this.hop) {
      this.frame();
      this.filled = 0;
      this.outPos = 0;
    }
    return y;
  }

  private frame(): void {
    const n = SPECTRAL_FRAME;
    for (let i = 0; i < n; i++) {
      this.re[i] = this.input[i] * this.window[i];
      this.im[i] = 0;
    }
    fft(this.re, this.im);
    const floor = 0.1 + 0.25 * (1 - this.strength); // at most -20 dB
    const over = 1 + 3 * this.strength;
    this.frames++;
    for (let k = 0; k <= n / 2; k++) {
      const power = this.re[k] * this.re[k] + this.im[k] * this.im[k];
      this.smoothed[k] = this.frames === 1 ? power : 0.7 * this.smoothed[k] + 0.3 * power;
      // Minimum tracking: follow a fall at once, a rise by about 1.5 dB/s.
      const s = this.smoothed[k];
      this.noise[k] = this.frames === 1 || s < this.noise[k] ? s : this.noise[k] * 1.004;
      const gain = Math.max(floor, 1 - (over * this.noise[k]) / (s + 1e-12));
      this.gains[k] = 0.6 * this.gains[k] + 0.4 * gain;
      const g = this.gains[k];
      this.re[k] *= g;
      this.im[k] *= g;
      if (k > 0 && k < n / 2) {
        this.re[n - k] = this.re[k];
        this.im[n - k] = -this.im[k];
      }
    }
    fft(this.re, this.im, true);
    // Overlap-add: the first half completes the previous frame's second half.
    for (let i = 0; i < this.hop; i++) {
      this.output[i] = this.output[this.hop + i] + this.re[i] * this.window[i];
      this.output[this.hop + i] = this.re[this.hop + i] * this.window[this.hop + i];
    }
    this.input.copyWithin(0, this.hop);
  }
}

export type NrKind = "off" | "lms" | "spectral";

export interface RxDspSettings {
  anf: boolean; // automatic notch
  nr: NrKind;
  level: number; // 0..1, strength of the noise reduction
}

// Latency of each block in samples (shown on the page, AGENTS.md stage 8).
export function rxDspLatency(settings: RxDspSettings): number {
  return settings.nr === "spectral" ? SPECTRAL_FRAME : 0;
}

// The chain run by the "rx-dsp" AudioWorklet: notch first, then noise reduction.
export class RxDsp {
  private readonly notch = new LineEnhancer(96, 24, 0.002);
  private readonly lms = new LineEnhancer(64, 16, 0.002);
  private readonly spectral = new SpectralNr();
  settings: RxDspSettings = { anf: false, nr: "off", level: 0.5 };

  configure(settings: RxDspSettings): void {
    this.settings = settings;
    this.lms.rate = 0.0005 + 0.004 * settings.level;
    this.spectral.strength = settings.level;
  }

  process(samples: Float32Array): void {
    const { anf, nr } = this.settings;
    for (let i = 0; i < samples.length; i++) {
      let x = samples[i];
      if (anf) {
        this.notch.step(x);
        x = this.notch.error;
      }
      if (nr === "lms") {
        this.lms.step(x);
        // The prediction alone sounds thin: keep a little of the original.
        x = this.lms.predicted + 0.15 * (1 - this.settings.level) * x;
      } else if (nr === "spectral") {
        x = this.spectral.step(x);
      }
      samples[i] = x;
    }
  }
}
