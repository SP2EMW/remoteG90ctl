// SPDX-License-Identifier: GPL-3.0-or-later
//
// Reading CW (web/src/cw.ts): signals made here are decoded back, one alone,
// with noise, at other speeds, and several at once by the skimmer.

import assert from "node:assert/strict";
import { test } from "node:test";

import { CwChannel, CwSkimmer, MAX_CHANNELS, lastCall, wpm } from "../../src/remoteg90ctl/server/static/cw.js";

const RATE = 48000;
const CODES = {
  A: ".-", B: "-...", C: "-.-.", D: "-..", E: ".", F: "..-.", G: "--.", H: "....", I: "..", J: ".---",
  K: "-.-", L: ".-..", M: "--", N: "-.", O: "---", P: ".--.", Q: "--.-", R: ".-.", S: "...", T: "-",
  U: "..-", V: "...-", W: ".--", X: "-..-", Y: "-.--", Z: "--..", 0: "-----", 1: ".----", 2: "..---",
  3: "...--", 4: "....-", 5: ".....", 6: "-....", 7: "--...", 8: "---..", 9: "----.", "/": "-..-.", "?": "..--..",
};

let seed = 1;
const random = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31) * 2 - 1;

// Keying of the text at `speed` WPM: [on, ms] pairs, with silence around it.
// `jitter`: as a hand key, each element and gap off by up to this part, the
// dashes 3 dots give or take half of it.
// `spacing`: the gaps between characters and words this many times longer
// (Farnsworth spacing: the characters at speed, the text slower).
function keying(text, speed, jitter = 0, spacing = 1) {
  const dot = 1200 / speed;
  const j = (ms) => ms * (1 + jitter * random());
  const dash = 3 * (1 + (jitter / 2) * random());
  const out = [[false, 500]];
  for (const word of text.split(" ")) {
    for (const c of word) {
      for (const e of CODES[c]) out.push([true, j(e === "." ? dot : dash * dot)], [false, j(dot)]);
      out.push([false, j((3 * spacing - 1) * dot)]); // a character gap is 3 dots
    }
    out.push([false, j(4 * spacing * dot)]); // a word gap is 7 dots
  }
  out.push([false, 1500]);
  return out;
}

// Several signals summed: [{text, hz, speed, level}], plus noise.
function audio(signals, noise = 0) {
  const parts = signals.map((s) => ({ ...s, steps: keying(s.text, s.speed, s.jitter, s.spacing) }));
  const ms = Math.max(...parts.map((p) => p.steps.reduce((a, [, d]) => a + d, 0)));
  const out = new Float32Array(Math.ceil((ms * RATE) / 1000));
  for (const p of parts) {
    let t = 0;
    for (const [on, d] of p.steps) {
      const end = t + Math.round((d * RATE) / 1000);
      for (let i = t; i < end && i < out.length; i++) {
        // 4 ms rise and fall, as a real keyer
        const edge = Math.min(1, (i - t) / (0.004 * RATE), (end - i) / (0.004 * RATE));
        if (on) out[i] += p.level * edge * Math.sin((2 * Math.PI * p.hz * i) / RATE);
      }
      t = end;
    }
  }
  if (noise > 0) for (let i = 0; i < out.length; i++) out[i] += noise * random();
  return out;
}

function decodeOne(samples, hz) {
  const channel = new CwChannel(hz);
  const down = new Float32Array(Math.floor(samples.length / 6));
  for (let i = 0; i < down.length; i++) {
    let s = 0;
    for (let k = 0; k < 6; k++) s += samples[i * 6 + k];
    down[i] = s / 6;
  }
  channel.push(down);
  return channel;
}

test("one clean signal decodes back and its speed is found", () => {
  const text = "CQ CQ DE SP2EMW SP2EMW K";
  const channel = decodeOne(audio([{ text, hz: 700, speed: 22, level: 0.3 }]), 700);
  assert.equal(channel.text.trim(), text);
  assert.ok(Math.abs(channel.wpm - 22) < 3, `speed ${channel.wpm}`);
});

test("slow and fast signals decode", () => {
  for (const speed of [12, 35]) {
    const text = "TEST 599 5NN TU";
    const channel = decodeOne(audio([{ text, hz: 600, speed, level: 0.3 }]), 600);
    assert.equal(channel.text.trim(), text, `at ${speed} WPM`);
  }
});

test("noise about as strong as the signal still decodes", () => {
  seed = 3;
  const text = "DL1XYZ DE SP2EMW 73";
  const channel = decodeOne(audio([{ text, hz: 800, speed: 20, level: 0.2 }], 0.3), 800);
  assert.equal(channel.text.trim(), text);
});

test("the skimmer finds several signals and decodes each", () => {
  seed = 5;
  const signals = [
    { text: "CQ TEST SP2EMW SP2EMW", hz: 520, speed: 24, level: 0.2 },
    { text: "CQ DL1XYZ DL1XYZ TEST", hz: 900, speed: 28, level: 0.15 },
    { text: "OK1ABC 5NN 012", hz: 1400, speed: 18, level: 0.1 },
  ];
  const skimmer = new CwSkimmer(RATE);
  let last = [];
  skimmer.onUpdate = (channels) => (last = channels);
  const samples = audio(signals, 0.05);
  for (let i = 0; i < samples.length; i += 960) skimmer.push(samples.subarray(i, i + 960));
  for (const s of signals) {
    const found = last.find((c) => Math.abs(c.toneHz - s.hz) < 20);
    assert.ok(found !== undefined, `no channel at ${s.hz} Hz: ${JSON.stringify(last.map((c) => c.toneHz))}`);
    // The skimmer joins a signal some time after it starts: the end must be right.
    const tail = s.text.split(" ").slice(-2).join(" ");
    assert.ok(found.text.includes(tail), `${s.hz} Hz: "${found.text}" lacks "${tail}"`);
  }
  assert.equal(last.find((c) => Math.abs(c.toneHz - 520) < 20).call, "SP2EMW");
});

test("noise alone opens few channels and prints little", () => {
  seed = 9;
  const skimmer = new CwSkimmer(RATE);
  let last = [];
  skimmer.onUpdate = (channels) => (last = channels);
  const samples = new Float32Array(RATE * 10).map(() => 0.1 * random());
  for (let i = 0; i < samples.length; i += 960) skimmer.push(samples.subarray(i, i + 960));
  assert.ok(last.length <= MAX_CHANNELS);
  assert.ok(last.reduce((n, c) => n + c.text.replace(/ /g, "").length, 0) < 40);
});

test("a signal that comes after a while of noise still gets a channel", () => {
  seed = 13;
  const skimmer = new CwSkimmer(RATE);
  let last = [];
  skimmer.onUpdate = (channels) => (last = channels);
  const noise = new Float32Array(RATE * 4).map(() => 0.5 * random());
  const text = "CQ CQ DE SP2EMW SP2EMW SP2EMW K";
  const signal = audio([{ text, hz: 703, speed: 25, level: 0.1 }], 0.5);
  for (const samples of [noise, signal]) {
    for (let i = 0; i < samples.length; i += 960) skimmer.push(samples.subarray(i, i + 960));
  }
  const found = last.find((c) => Math.abs(c.toneHz - 703) < 20);
  assert.ok(found !== undefined, `no channel at 703 Hz: ${JSON.stringify(last.map((c) => c.toneHz))}`);
  assert.ok(found.text.includes("SP2EMW SP2EMW K"), `"${found.text}"`);
});

test("weak signals: the channel's filter narrows with the speed", () => {
  for (const speed of [14, 25]) {
    seed = 21;
    const text = "DL1XYZ DE SP2EMW 599 73";
    // About -12 dB in 2.5 kHz: read only with a filter narrower than 100 Hz.
    const channel = decodeOne(audio([{ text, hz: 700, speed, level: 0.1 }], 0.6), 700);
    assert.equal(channel.text.trim(), text, `${speed} WPM`);
  }
});

test("uneven keying, as by hand, decodes", () => {
  for (const speed of [15, 25]) {
    seed = 31;
    const text = "CQ CQ DE SP2EMW SP2EMW K UR RST 579 NAME DAN QTH TORUN 73";
    const channel = decodeOne(audio([{ text, hz: 700, speed, level: 0.3, jitter: 0.2 }], 0.05), 700);
    assert.equal(channel.text.trim(), text, `${speed} WPM`);
  }
});

test("long gaps between characters (slow sending) still give words", () => {
  for (const spacing of [2, 3]) {
    seed = 41;
    const text = "CQ CQ DE SP2EMW SP2EMW K UR RST 579 TU 73";
    const channel = decodeOne(audio([{ text, hz: 700, speed: 18, level: 0.3, spacing }], 0.05), 700);
    // A few words to learn the sender's spacing; then words as sent.
    assert.ok(channel.text.trim().endsWith("SP2EMW K UR RST 579 TU 73"), `x${spacing}: "${channel.text}"`);
    assert.equal(lastCall(channel.text), "SP2EMW");
  }
});

test("callsigns are picked out of the text", () => {
  assert.equal(lastCall("CQ CQ DE SP2EMW SP2EMW K"), "SP2EMW");
  assert.equal(lastCall("UR 599 TU"), null);
  assert.equal(lastCall("DL1XYZ DE OK1ABC/P"), "OK1ABC/P");
  assert.equal(Math.round(wpm(60)), 20);
});
