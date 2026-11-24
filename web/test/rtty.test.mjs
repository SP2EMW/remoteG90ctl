// SPDX-License-Identifier: GPL-3.0-or-later
//
// RTTY reception (web/src/rtty.ts): signals made here (45.45 baud, 170 Hz,
// 1.5 stop bits, continuous phase) are decoded back, also with noise, off the
// default tones, reversed, with figures and after a fade of one tone.

import assert from "node:assert/strict";
import { test } from "node:test";

import { RttyDecoder, RTTY_MARK_HZ, baudotChar } from "../../src/remoteg90ctl/server/static/rtty.js";

const RATE = 48000;
const BAUD = 45.45;

// Text to Baudot codes with the shifts it needs (a test helper, not the encoder).
function codes(text) {
  const table = new Map();
  for (let code = 0; code < 32; code++) {
    for (const figures of [false, true]) {
      const c = baudotChar(code, figures);
      if (c !== "" && !table.has(`${c}`)) table.set(c, { code, figures });
      if (c === " " || c === "\n") table.set(c, { code, figures: null });
    }
  }
  const out = [0x1f, 0x1f]; // LTRS to start
  let figures = false;
  for (const c of text) {
    const t = table.get(c);
    assert.ok(t !== undefined, `no Baudot code for ${JSON.stringify(c)}`);
    if (t.figures !== null && t.figures !== figures) {
      out.push(t.figures ? 0x1b : 0x1f);
      figures = t.figures;
    }
    out.push(t.code);
    if (c === " ") figures = false; // the receiver unshifts on space
  }
  return out;
}

// Audio of the codes: mark idle before and after, continuous phase.
function signal(text, { markHz = RTTY_MARK_HZ, reverse = false, noise = 0, spaceGain = 1 } = {}) {
  const bits = [];
  const idle = Math.round(BAUD / 2);
  for (let i = 0; i < idle; i++) bits.push(1);
  for (const code of codes(text)) {
    bits.push(0);
    for (let b = 0; b < 5; b++) bits.push((code >> b) & 1);
    bits.push(1, 1.5); // 1.5 stop bits: 1.5 marks a half bit
  }
  for (let i = 0; i < idle; i++) bits.push(1);
  const samples = [];
  let phase = 0;
  let t = 0;
  let seed = 1;
  const random = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31) * 2 - 1;
  for (const bit of bits) {
    const length = bit === 1.5 ? 0.5 : 1;
    const isMark = bit !== 0;
    const tone = isMark !== reverse ? markHz : markHz + 170;
    const gain = isMark ? 1 : spaceGain;
    const end = t + (length * RATE) / BAUD;
    for (; t < end; t++) {
      phase += (2 * Math.PI * tone) / RATE;
      samples.push(0.3 * gain * Math.sin(phase) + noise * random());
    }
  }
  return Float32Array.from(samples);
}

function decode(audio, setup = (d) => d) {
  const decoder = setup(new RttyDecoder(RATE));
  let text = "";
  decoder.onText = (t) => (text += t);
  for (let i = 0; i < audio.length; i += 960) decoder.push(audio.subarray(i, i + 960)); // 20 ms frames
  return text;
}

test("a clean signal decodes back", () => {
  const text = "CQ CQ DE SP2EMW SP2EMW K";
  assert.equal(decode(signal(text)), text);
});

test("figures and letters switch, and unshift on space", () => {
  const text = "UR RST 599 599 NR 012 QTH JO82";
  assert.equal(decode(signal(text)), text);
});

test("noise about as strong as the signal still decodes", () => {
  const text = "RYRYRYRY THE QUICK BROWN FOX 73";
  const got = decode(signal(text, { noise: 0.3 }));
  assert.equal(got, text);
});

test("tones elsewhere than 2125 Hz decode when set", () => {
  const text = "TEST 1500 HZ";
  const got = decode(signal(text, { markHz: 1500 }), (d) => (d.setTones(1500, false), d));
  assert.equal(got, text);
});

test("a reversed signal decodes with REV and not without", () => {
  const text = "REVERSED SHIFT";
  const audio = signal(text, { reverse: true });
  assert.equal(decode(audio, (d) => (d.setTones(RTTY_MARK_HZ, true), d)), text);
  assert.notEqual(decode(audio), text);
});

test("a weak space tone (selective fading) still decodes", () => {
  const text = "FADING ON ONE TONE";
  assert.equal(decode(signal(text, { spaceGain: 0.25 })), text);
});

test("noise alone prints little", () => {
  const audio = new Float32Array(RATE * 5);
  let seed = 7;
  for (let i = 0; i < audio.length; i++) {
    seed = (seed * 1103515245 + 12345) % 2 ** 31;
    audio[i] = 0.3 * ((seed / 2 ** 31) * 2 - 1);
  }
  assert.ok(decode(audio).length < 60);
});
