// SPDX-License-Identifier: GPL-3.0-or-later
//
// Tests of the receive audio processing (stage 8), on the compiled JavaScript.

import assert from "node:assert/strict";
import { test } from "node:test";

import { RxDsp, SPECTRAL_FRAME, SpectralNr, fft, rxDspLatency } from "../../src/remoteg90ctl/server/static/rxdsp.js";

const RATE = 48000;

// Deterministic noise (a small linear congruential generator), -1..1.
function noise(n, seed = 1) {
  const out = new Float32Array(n);
  let s = seed;
  for (let i = 0; i < n; i++) {
    s = (s * 1103515245 + 12345) % 2147483648;
    out[i] = (s / 2147483648) * 2 - 1;
  }
  return out;
}

function tone(n, hz, amplitude) {
  return Float32Array.from({ length: n }, (_, i) => amplitude * Math.sin((2 * Math.PI * hz * i) / RATE));
}

function add(a, b) {
  return a.map((v, i) => v + b[i]);
}

function rms(x, from = 0) {
  let sum = 0;
  for (let i = from; i < x.length; i++) sum += x[i] * x[i];
  return Math.sqrt(sum / (x.length - from));
}

// Level of one frequency in a signal (correlation with sine and cosine).
function level(x, hz, from) {
  let s = 0;
  let c = 0;
  for (let i = from; i < x.length; i++) {
    s += x[i] * Math.sin((2 * Math.PI * hz * i) / RATE);
    c += x[i] * Math.cos((2 * Math.PI * hz * i) / RATE);
  }
  return (2 * Math.hypot(s, c)) / (x.length - from);
}

test("fft and inverse give the signal back", () => {
  const re = noise(64);
  const im = new Float32Array(64);
  const original = Float32Array.from(re);
  fft(re, im);
  fft(re, im, true);
  for (let i = 0; i < 64; i++) assert.ok(Math.abs(re[i] - original[i]) < 1e-5);
});

test("spectral noise reduction passes a clean signal unchanged, one frame later", () => {
  const nr = new SpectralNr();
  nr.strength = 0;
  const input = tone(RATE, 1000, 0.5);
  const output = input.map((v) => nr.step(v));
  // Without noise the floor tracks the tone itself, so check only the delay and level.
  const delayed = output.subarray(SPECTRAL_FRAME);
  assert.ok(Math.abs(level(delayed, 1000, RATE / 2) - 0.5) < 0.25);
});

// A tone keyed 100 ms on, 100 ms off, like CW (speech also comes and goes).
function keyed(n, hz, amplitude) {
  return tone(n, hz, amplitude).map((v, i) => (Math.floor(i / 4800) % 2 === 0 ? v : 0));
}

test("spectral noise reduction lowers noise and keeps a keyed tone", () => {
  // A steady carrier is taken for noise and removed, like the floor; a keyed
  // tone (CW, speech) stands above the floor and stays.
  const n = 2 * RATE;
  const input = add(noise(n), keyed(n, 800, 0.5)).map((v) => v * 0.3);
  const dsp = new RxDsp();
  dsp.configure({ anf: false, nr: "spectral", level: 0.8 });
  const output = Float32Array.from(input);
  dsp.process(output);
  const from = RATE; // after the noise floor has settled
  const toneIn = level(input, 800, from);
  const toneOut = level(output, 800, from);
  const noiseIn = rms(input, from) ** 2 - (toneIn ** 2) / 2;
  const noiseOut = rms(output, from) ** 2 - (toneOut ** 2) / 2;
  assert.ok(toneOut > 0.6 * toneIn, `tone ${toneOut} of ${toneIn}`);
  assert.ok(noiseOut < 0.5 * noiseIn, `noise ${noiseOut} of ${noiseIn}`);
});

test("automatic notch removes a steady carrier and keeps the noise", () => {
  const n = 2 * RATE;
  const input = add(noise(n, 7).map((v) => v * 0.1), tone(n, 1500, 0.5));
  const dsp = new RxDsp();
  dsp.configure({ anf: true, nr: "off", level: 0.5 });
  const output = Float32Array.from(input);
  dsp.process(output);
  const from = RATE;
  assert.ok(level(output, 1500, from) < 0.1 * level(input, 1500, from));
  assert.ok(rms(output, from) > 0.03); // the noise (speech) is not removed
});

test("LMS noise reduction keeps a tone and lowers white noise", () => {
  const n = 2 * RATE;
  const input = add(noise(n, 3).map((v) => v * 0.3), tone(n, 700, 0.3));
  const dsp = new RxDsp();
  dsp.configure({ anf: false, nr: "lms", level: 1 });
  const output = Float32Array.from(input);
  dsp.process(output);
  const from = RATE;
  const toneOut = level(output, 700, from);
  assert.ok(toneOut > 0.15, `tone ${toneOut}`);
  const noiseOut = rms(output, from) ** 2 - toneOut ** 2 / 2;
  const noiseIn = rms(input, from) ** 2 - 0.3 ** 2 / 2;
  assert.ok(noiseOut < 0.5 * noiseIn, `noise ${noiseOut} of ${noiseIn}`);
});

test("latency: only the spectral noise reduction adds a frame", () => {
  assert.equal(rxDspLatency({ anf: true, nr: "lms", level: 1 }), 0);
  assert.equal(rxDspLatency({ anf: false, nr: "spectral", level: 1 }), SPECTRAL_FRAME);
});
