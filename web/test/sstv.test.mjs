// SPDX-License-Identifier: GPL-3.0-or-later
//
// SSTV reception (web/src/sstv.ts, decisions.md D60): pictures made here in
// the common modes are decoded back, with their VIS code, joined after it, off
// frequency and in noise; noise alone gives no picture.

import assert from "node:assert/strict";
import { test } from "node:test";

import { SstvDecoder } from "../../src/remoteg90ctl/server/static/sstv.js";

const RATE = 48000;
// Four vertical bars: red, green, blue, white.
const BARS = [
  [255, 0, 0],
  [0, 255, 0],
  [0, 0, 255],
  [255, 255, 255],
];
const bar = (x, width) => BARS[Math.min(3, Math.floor((4 * x) / width))];

let seed = 7;
const random = () => {
  seed ^= seed << 13;
  seed ^= seed >>> 17;
  seed ^= seed << 5;
  return (seed >>> 0) / 2 ** 32 - 0.5;
};

// A value 0..255 as a frequency, 1500 (black) to 2300 Hz (white).
const hz = (v) => 1500 + (800 * v) / 255;
const yuv = ([r, g, b]) => [
  16 + (65.481 * r + 128.553 * g + 24.966 * b) / 255,
  128 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255,
  128 + (112.0 * r - 93.786 * g - 18.214 * b) / 255,
];

// A channel of `width` pixels over `ms`: one segment per pixel.
function channel(segments, width, ms, value) {
  for (let x = 0; x < width; x++) segments.push([hz(value(x)), ms / width]);
}

function vis(segments, code) {
  segments.push([1900, 300], [1200, 10], [1900, 300], [1200, 30]);
  let ones = 0;
  for (let i = 0; i < 7; i++) {
    const bit = (code >> i) & 1;
    ones += bit;
    segments.push([bit ? 1100 : 1300, 30]);
  }
  segments.push([ones % 2 ? 1100 : 1300, 30], [1200, 30]);
}

function martin1(segments, lines) {
  for (let y = 0; y < lines; y++) {
    segments.push([1200, 4.862], [1500, 0.572]);
    for (const c of [1, 2, 0]) {
      channel(segments, 320, 146.432, (x) => bar(x, 320)[c]);
      segments.push([1500, 0.572]);
    }
  }
}

function scottie1(segments, lines) {
  segments.push([1200, 9]);
  for (let y = 0; y < lines; y++) {
    segments.push([1500, 1.5]);
    channel(segments, 320, 138.24, (x) => bar(x, 320)[1]);
    segments.push([1500, 1.5]);
    channel(segments, 320, 138.24, (x) => bar(x, 320)[2]);
    segments.push([1200, 9], [1500, 1.5]);
    channel(segments, 320, 138.24, (x) => bar(x, 320)[0]);
  }
}

function robot36(segments, lines) {
  for (let y = 0; y < lines; y++) {
    const even = y % 2 === 0;
    segments.push([1200, 9], [1500, 3]);
    channel(segments, 320, 88, (x) => yuv(bar(x, 320))[0]);
    segments.push([even ? 1500 : 2300, 4.5], [1900, 1.5]);
    channel(segments, 320, 44, (x) => yuv(bar(x, 320))[even ? 2 : 1]);
  }
}

function pd120(segments, lines) {
  for (let y = 0; y < lines; y += 2) {
    segments.push([1200, 20], [1500, 2.08]);
    for (const c of [0, 2, 1, 0]) channel(segments, 640, 121.6, (x) => yuv(bar(x, 640))[c]);
  }
}

// FM synthesis of the segments, phase continuous; `offsetHz` moves it all.
function synth(segments, { offsetHz = 0, noise = 0, leadMs = 300 } = {}) {
  const total = leadMs + segments.reduce((a, [, ms]) => a + ms, 0) + 1500;
  const out = new Float32Array(Math.ceil((total * RATE) / 1000));
  let phase = 0;
  let index = 0;
  let segmentEnd = leadMs + segments[0][1];
  for (let i = Math.round((leadMs * RATE) / 1000); i < out.length; i++) {
    const t = (i * 1000) / RATE;
    while (index < segments.length && t >= segmentEnd) {
      index++;
      if (index < segments.length) segmentEnd += segments[index][1];
    }
    if (index >= segments.length) break;
    phase += (2 * Math.PI * (segments[index][0] + offsetHz)) / RATE;
    out[i] = 0.5 * Math.sin(phase);
  }
  if (noise > 0) for (let i = 0; i < out.length; i++) out[i] += noise * random();
  return out;
}

function decode(samples) {
  const decoder = new SstvDecoder(RATE);
  const pictures = [];
  decoder.onPicture = (p) => pictures.push({ ...p, rows: [], ended: false });
  decoder.onLines = (first, rows, width, rgba) => {
    const picture = pictures[pictures.length - 1];
    for (let r = 0; r < rows; r++) picture.rows[first + r] = rgba.slice(r * width * 4, (r + 1) * width * 4);
  };
  decoder.onEnd = () => (pictures[pictures.length - 1].ended = true);
  for (let i = 0; i < samples.length; i += 960) decoder.push(samples.subarray(i, i + 960));
  return pictures;
}

// The bars' colours in rows [from, to): each within `tolerance` per channel.
function checkBars(picture, from, to, tolerance = 40) {
  const width = picture.width;
  for (let b = 0; b < 4; b++) {
    const x0 = Math.floor(((b + 0.25) * width) / 4);
    const x1 = Math.floor(((b + 0.75) * width) / 4);
    const sum = [0, 0, 0];
    let n = 0;
    for (let y = from; y < to; y++) {
      const row = picture.rows[y];
      assert.ok(row !== undefined, `${picture.mode}: line ${y} missing`);
      for (let x = x0; x < x1; x++) {
        for (let c = 0; c < 3; c++) sum[c] += row[4 * x + c];
        n++;
      }
    }
    const mean = sum.map((s) => s / n);
    for (let c = 0; c < 3; c++) {
      assert.ok(Math.abs(mean[c] - BARS[b][c]) <= tolerance, `${picture.mode} bar ${b}: ${mean.map(Math.round)} vs ${BARS[b]}`);
    }
  }
}

test("a whole Robot 36 picture decodes from its VIS code and ends", () => {
  const segments = [];
  vis(segments, 8);
  robot36(segments, 240);
  const pictures = decode(synth(segments));
  assert.equal(pictures.length, 1);
  const [picture] = pictures;
  assert.deepEqual([picture.mode, picture.width, picture.height, picture.vis], ["Robot 36", 320, 240, true]);
  assert.ok(picture.ended);
  assert.equal(picture.rows.filter(Boolean).length, 240);
  checkBars(picture, 4, 236);
});

test("Martin 1, Scottie 1 and PD 120 decode from their VIS codes", () => {
  for (const [name, code, make, lines] of [
    ["Martin 1", 44, martin1, 30],
    ["Scottie 1", 60, scottie1, 30],
    ["PD 120", 95, pd120, 40],
  ]) {
    const segments = [];
    vis(segments, code);
    make(segments, lines);
    const pictures = decode(synth(segments));
    assert.equal(pictures.length, 1, name);
    assert.equal(pictures[0].mode, name);
    assert.ok(pictures[0].vis);
    checkBars(pictures[0], 2, lines - 2);
  }
});

test("joined after the VIS code: the sync pulses show the mode", () => {
  const segments = [];
  martin1(segments, 30); // no VIS: the picture was already under way
  const pictures = decode(synth(segments));
  assert.equal(pictures.length, 1);
  assert.equal(pictures[0].mode, "Martin 1");
  assert.equal(pictures[0].vis, false);
  checkBars(pictures[0], 2, 20);
});

test("a signal 30 Hz off and in noise still gives the picture", () => {
  seed = 11;
  const segments = [];
  vis(segments, 8);
  robot36(segments, 60);
  const pictures = decode(synth(segments, { offsetHz: 30, noise: 0.5 }));
  assert.equal(pictures.length, 1);
  assert.equal(pictures[0].mode, "Robot 36");
  checkBars(pictures[0], 4, 56, 60);
});

test("noise alone gives no picture", () => {
  seed = 5;
  const noise = new Float32Array(RATE * 20).map(() => random());
  assert.equal(decode(noise).length, 0);
});
