// SPDX-License-Identifier: GPL-3.0-or-later
//
// Seek (owner's request 2026-11-13): stations found in the waterfall's spectrum,
// 10 dB over the noise and 200 Hz wide; where the dial goes; the next view.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  HIGHEST_HZ,
  LOWEST_HZ,
  STEP_HZ,
  dialFor,
  findSignals,
  maxHold,
  nextSignal,
  nextView,
} from "../../src/remoteg90ctl/server/static/seek.js";

const SPAN = 96_000;
const BINS = 960; // 100 Hz per bin
const MIN_DB = -120;
const MAX_DB = -20;

// A line centred on `center` with noise at -100 dB and `signals` [lowHz, highHz, dB].
function line(center, signals = []) {
  const bins = new Uint8Array(BINS);
  for (let i = 0; i < BINS; i++) {
    const hz = center + ((i + 0.5) / BINS - 0.5) * SPAN;
    let db = -100 + ((i * 7) % 5) * 0.4; // a little ripple
    for (const [low, high, level] of signals) if (hz >= low && hz <= high) db = level;
    bins[i] = Math.round(((db - MIN_DB) / (MAX_DB - MIN_DB)) * 255);
  }
  return { centerHz: center, spanHz: SPAN, minDb: MIN_DB, maxDb: MAX_DB, bins };
}

const C = 14_200_000;

test("a station 10 dB over the noise and at least 200 Hz wide is found", () => {
  const hold = maxHold([line(C, [[C + 5_000, C + 7_500, -80]])]);
  const signals = findSignals(hold);
  assert.equal(signals.length, 1);
  assert.ok(Math.abs(signals[0][0] - (C + 5_000)) <= 100);
  assert.ok(Math.abs(signals[0][1] - (C + 7_500)) <= 100);
});

test("weak, narrow and edge signals are not stations", () => {
  const hold = maxHold([
    line(C, [
      [C + 5_000, C + 7_000, -93], // 7 dB over the noise
      [C + 10_000, C + 10_100, -60], // a carrier, 100 Hz
      [C + 45_000, C + 47_000, -60], // beyond the usable ±38 kHz
    ]),
  ]);
  assert.deepEqual(findSignals(hold), []);
});

test("speech with a gap is one signal; the hold keeps what any line had", () => {
  const first = line(C, [[C + 3_000, C + 3_800, -80]]);
  const second = line(C, [[C + 3_900, C + 5_000, -80]]);
  const signals = findSignals(maxHold([first, second]));
  assert.equal(signals.length, 1);
  assert.ok(signals[0][1] - signals[0][0] > 1_800);
});

test("the hold uses only lines of the newest I/Q centre", () => {
  const old = line(C - 40_000, [[C - 40_000 + 2_000, C - 40_000 + 4_000, -70]]);
  const hold = maxHold([line(C), old]);
  assert.equal(hold.centerHz, C);
  assert.deepEqual(findSignals(hold), []);
});

test("the next signal up or down skips the one being heard", () => {
  const signals = [
    [C - 20_000, C - 18_000],
    [C + 300, C + 2_700], // the one in the passband now (USB)
    [C + 9_000, C + 11_000],
  ];
  assert.deepEqual(nextSignal(signals, C + 3_000, 1), [C + 9_000, C + 11_000]);
  assert.deepEqual(nextSignal(signals, C + 300 - 1, -1), [C - 20_000, C - 18_000]);
  assert.equal(nextSignal(signals, C + 12_000, 1), null);
});

test("the dial puts the signal into the passband of the mode", () => {
  assert.equal(dialFor([C + 9_300, C + 11_800], "USB", 100), C + 9_000);
  assert.equal(dialFor([C + 9_300, C + 11_800], "LSB", 100), C + 12_100);
  assert.equal(dialFor([C + 9_900, C + 10_100], "CW", 10), C + 10_000);
  assert.equal(dialFor([C + 9_000, C + 11_000], "AM", 100), C + 10_000);
});

test("an empty view moves 40 kHz, within the radio's range", () => {
  assert.equal(nextView(C, 1), C + STEP_HZ);
  assert.equal(nextView(C, -1), C - STEP_HZ);
  assert.equal(nextView(HIGHEST_HZ - 1_000, 1), null);
  assert.equal(nextView(LOWEST_HZ + 1_000, -1), null);
});
