// SPDX-License-Identifier: GPL-3.0-or-later
//
// Tests of the transmit noise gate (stage 8), on the compiled JavaScript.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EQ_BANDS,
  NoiseGate,
  TX_CUSTOM_EMPTY,
  TX_DSP_DEFAULTS,
  TX_PRESETS,
  matchingPreset,
  parseCustom,
  presetShown,
  presetValues,
  toCustom,
  txDspLatencyMs,
  withPreset,
} from "../../src/remoteg90ctl/server/static/txdsp.js";

const RATE = 48000;

function peak(x, from, to) {
  let p = 0;
  for (let i = from; i < to; i++) p = Math.max(p, Math.abs(x[i]));
  return p;
}

test("the gate passes speech and mutes the quiet between words", () => {
  // 0.5 s of "speech" (a tone at -12 dBFS), then 1.5 s of room noise at -60 dBFS.
  // The envelope falls below -40 dBFS about 0.5 s after the speech (release
  // 0.15 s), then the gate holds 0.1 s and closes over a few 0.05 s.
  const n = 2 * RATE;
  const x = Float32Array.from({ length: n }, (_, i) =>
    i < RATE / 2 ? 0.25 * Math.sin((2 * Math.PI * 500 * i) / RATE) : 0.001 * Math.sin(i),
  );
  const gate = new NoiseGate(RATE);
  gate.enabled = true;
  gate.setThresholdDb(-40);
  gate.process(x);
  assert.ok(peak(x, 0, RATE / 2) > 0.24, "speech passes, from its first sample");
  assert.ok(peak(x, 1.5 * RATE, n) < 0.001 * 0.05, "noise is muted after the hold and close");
});

test("a disabled gate changes nothing", () => {
  const x = Float32Array.from({ length: 1000 }, (_, i) => 0.0001 * Math.sin(i));
  const copy = Float32Array.from(x);
  new NoiseGate(RATE).process(x);
  assert.deepEqual(x, copy);
});

test("only the compressor adds delay", () => {
  assert.equal(txDspLatencyMs(TX_DSP_DEFAULTS), 0);
  assert.equal(txDspLatencyMs({ ...TX_DSP_DEFAULTS, compressor: true }), 6);
});

// ---- Presets (owner's request 2026-11-10) ----

test("the presets stay inside what the controls can show", () => {
  for (const [name, p] of Object.entries(TX_PRESETS)) {
    assert.ok(p.lowCut >= 0 && p.lowCut <= 500 && p.lowCut % 10 === 0, `${name} lowCut`);
    assert.ok(p.highCut >= 1500 && p.highCut <= 4000 && p.highCut % 50 === 0, `${name} highCut`);
    assert.ok(p.gateDb >= -80 && p.gateDb <= -20, `${name} gateDb`);
    assert.ok(p.thresholdDb >= -50 && p.thresholdDb <= 0, `${name} thresholdDb`);
    assert.ok([2, 3, 4, 6, 10].includes(p.ratio), `${name} ratio`);
    assert.ok(p.makeupDb >= 0 && p.makeupDb <= 20, `${name} makeupDb`);
    assert.equal(p.eq.length, EQ_BANDS.length);
    assert.ok(p.eq.every((g) => Number.isInteger(g) && g >= -12 && g <= 12), `${name} eq`);
  }
});

test("DX is narrow and compressed for range, Local is full and deep", () => {
  const { dx, local } = TX_PRESETS;
  // Range: a narrower band, the lows cut, the gate on, harder compression, more make-up.
  assert.ok(dx.lowCut > local.lowCut && dx.highCut < local.highCut);
  assert.ok(dx.gate && !local.gate);
  assert.ok(dx.compressor && dx.ratio > local.ratio && dx.makeupDb > local.makeupDb);
  assert.ok(dx.eq[0] < 0 && dx.eq[1] < 0); // lows taken out
  assert.ok(dx.eq[3] > 0 && dx.eq[4] > 0); // 0.8-2 kHz lifted
  // Broadcast depth: 100 and 200 Hz lifted well above DX.
  assert.ok(local.eq[0] >= 6 && local.eq[1] >= 4);
  assert.ok(local.eq[0] - dx.eq[0] >= 15);
});

const EMPTY = { custom1: null, custom2: null };

test("a Custom slot is neutral until something is saved", () => {
  for (const slot of ["custom1", "custom2"]) {
    assert.deepEqual(presetValues(slot, EMPTY), TX_CUSTOM_EMPTY);
  }
  assert.ok(!TX_CUSTOM_EMPTY.gate && !TX_CUSTOM_EMPTY.compressor);
  assert.ok(TX_CUSTOM_EMPTY.eq.every((g) => g === 0));
  const mine = { ...TX_CUSTOM_EMPTY, lowCut: 200, eq: [0, 3, 0, 0, 0, 0] };
  assert.deepEqual(presetValues("custom1", { ...EMPTY, custom1: mine }), mine);
  assert.deepEqual(presetValues("custom2", { ...EMPTY, custom1: mine }), TX_CUSTOM_EMPTY); // its own slot
  assert.notEqual(presetValues("custom1", { ...EMPTY, custom1: mine }).eq, mine.eq); // a copy
});

test("selecting a preset keeps the monitor switch and tells which preset it is", () => {
  const settings = { ...TX_DSP_DEFAULTS, monitor: true };
  for (const name of ["dx", "local", "custom1", "custom2"]) {
    const next = withPreset(settings, name, EMPTY);
    assert.equal(next.monitor, true);
    // Both empty slots are the neutral chain: the first one is the name shown.
    assert.equal(matchingPreset(next, EMPTY), name === "custom2" ? "custom1" : name);
  }
  const edited = withPreset(settings, "dx", EMPTY);
  edited.eq[2] += 1;
  assert.equal(matchingPreset(edited, EMPTY), null); // "edited"
});

test("each Save fills its own slot, without the monitor switch", () => {
  const current = withPreset({ ...TX_DSP_DEFAULTS, monitor: true }, "local", EMPTY);
  current.highCut = 3000;
  const saved = toCustom(current);
  assert.equal("monitor" in saved, false);
  const customs = { custom1: null, custom2: saved }; // saved with "Save 2"
  assert.equal(matchingPreset(current, customs), "custom2");
  // Another slot, DX and Local are untouched and still selectable.
  assert.equal(matchingPreset(withPreset(current, "dx", customs), customs), "dx");
  assert.deepEqual(presetValues("custom1", customs), TX_CUSTOM_EMPTY);
  assert.deepEqual(parseCustom(JSON.stringify(saved)), saved); // survives the account's storage
});

test("a damaged saved Custom slot is ignored", () => {
  for (const text of [null, "", "{", "[]", '{"lowCut": 100}', JSON.stringify({ ...TX_CUSTOM_EMPTY, eq: [0] }), JSON.stringify({ ...TX_CUSTOM_EMPTY, makeupDb: 99 })]) {
    assert.equal(parseCustom(text), null, String(text));
  }
});

test("the list shows the preset chosen or saved, not the first one with the same values", () => {
  // DX saved unchanged into Custom 2: both hold the same values.
  const dx = withPreset({ ...TX_DSP_DEFAULTS, monitor: false }, "dx", EMPTY);
  const customs = { custom1: null, custom2: toCustom(dx) };
  assert.equal(presetShown(dx, "custom2", customs), "custom2");
  assert.equal(presetShown(dx, "dx", customs), "dx");
  // Changed since: "edited", whichever was chosen.
  const edited = { ...dx, eq: [...dx.eq] };
  edited.eq[0] += 1;
  assert.equal(presetShown(edited, "custom2", customs), "edited");
  // Nothing remembered (an older page): the first preset with these values.
  assert.equal(presetShown(dx, null, customs), "dx");
  assert.equal(presetShown(edited, null, customs), "edited");
});
