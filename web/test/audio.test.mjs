// SPDX-License-Identifier: GPL-3.0-or-later
//
// The server's frame numbers reach the FT8/FT4 receiver (owner's report
// 2026-11-14: decoding stopped while the audio played on). Chrome's Opus
// decoder takes only the first chunk's timestamp and counts on by 20 ms, so
// numbers from its output moved the audio against the server's clock after a
// new subscription or a lost frame. RxAudio keeps the numbers itself
// (FrameNumbers); here a decoder that numbers as Chrome does is simulated.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FrameNumbers } from "../../src/remoteg90ctl/server/static/audio.js";
import {
  CLOCK_BYTES,
  CycleCollector,
  FRAME_CLOCK,
  parseClock,
} from "../../src/remoteg90ctl/server/static/ft8.js";

const RATE = 48000;
const FRAME = 960;
const T0 = Date.UTC(2026, 10, 14, 12, 0, 0); // a cycle boundary

function clockFrame(seq, utcMs) {
  const buffer = new ArrayBuffer(CLOCK_BYTES);
  const view = new DataView(buffer);
  view.setUint8(0, FRAME_CLOCK);
  view.setUint8(1, 1);
  view.setUint32(4, seq, true);
  view.setFloat64(8, utcMs / 1000, true);
  return parseClock(buffer);
}

// One subscription as the server sends it: a clock frame every 50 frames,
// numbered from 0; `lost` frames never arrive. Each arriving frame goes to a
// Chrome-like decoder; the receiver gets the number `numberOf` gives.
function stream(collector, numbers, chrome, startMs, seconds, lost = new Set()) {
  for (let seq = 0; seq < seconds * 50; seq++) {
    if (seq % 50 === 0) collector.setClock(clockFrame(seq, startMs + seq * 20));
    if (lost.has(seq)) continue;
    numbers.given(seq);
    const chromeSeq = chrome.next++; // its own count, whatever came in
    const ours = numbers.decoded();
    collector.push(ours ?? chromeSeq, new Float32Array(FRAME).fill(0.1));
  }
}

function receiver() {
  const collector = new CycleCollector("FT8", RATE);
  const cycles = [];
  collector.onCycle = (cycle) => cycles.push(cycle.startMs);
  return { collector, cycles };
}

test("decoding goes on after the audio is subscribed anew", () => {
  const { collector, cycles } = receiver();
  const numbers = new FrameNumbers();
  const chrome = { next: 0 };
  stream(collector, numbers, chrome, T0, 60); // a minute of listening
  // Start FT8 while listening: a new subscription numbered from 0 again; the
  // page clears the numbers (RxAudio.newStream), Chrome's count goes on.
  numbers.clear();
  const before = cycles.length;
  stream(collector, numbers, chrome, T0 + 60_000, 45);
  assert.ok(cycles.length - before >= 2, `cycles after resubscribing: ${cycles.length - before}`);
});

test("a lost frame does not move the audio against the clock", () => {
  const { collector, cycles } = receiver();
  const numbers = new FrameNumbers();
  const lost = new Set(Array.from({ length: 120 }, (_, i) => 100 + i * 100)); // 1 in 100
  stream(collector, numbers, { next: 0 }, T0, 15 * 60, lost);
  // Every cycle of the quarter hour is cut, the last ones as on time as the first.
  assert.equal(cycles.length, 15 * 4);
  assert.equal(cycles.at(-1), T0 + (15 * 60 - 15) * 1000);
});

test("Chrome's own numbering alone loses the cycles (why the numbers are kept)", () => {
  const { collector, cycles } = receiver();
  const chrome = { next: 0 };
  const none = { given() {}, decoded: () => null, clear() {} };
  stream(collector, none, chrome, T0, 60);
  const before = cycles.length;
  stream(collector, none, chrome, T0 + 60_000, 45); // subscribed anew
  assert.equal(cycles.length - before, 0);
});

test("the numbers are kept for at most a second of frames", () => {
  const numbers = new FrameNumbers();
  for (let seq = 0; seq < 70; seq++) numbers.given(seq);
  assert.equal(numbers.decoded(), 70 - FrameNumbers.MAX_PENDING);
  numbers.clear();
  assert.equal(numbers.decoded(), null);
});
