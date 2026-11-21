// SPDX-License-Identifier: GPL-3.0-or-later
//
// The passband from a click on the receive audio spectrum (owner's report
// 2026-11-15: in U-D with a passband slid across the dial, Shift + click
// widened it both ways and a click did nothing).

import assert from "node:assert/strict";
import { test } from "node:test";

import { passbandWithEdge } from "../../src/remoteg90ctl/server/static/passband.js";

test("USB and U-D: a click sets the lower edge, Shift + click the upper", () => {
  assert.deepEqual(passbandWithEdge("U-D", [300, 3000], "low", 210), [200, 3000]);
  assert.deepEqual(passbandWithEdge("U-D", [300, 3000], "high", 2480), [300, 2500]);
});

test("a passband slid across the dial follows the mode, not its signs", () => {
  assert.deepEqual(passbandWithEdge("U-D", [-1750, 2200], "low", 300), [300, 2200]);
  assert.deepEqual(passbandWithEdge("U-D", [-1750, 2200], "high", 2700), [0, 2700]);
});

test("LSB and L-D: audio edges become negative offsets", () => {
  assert.deepEqual(passbandWithEdge("L-D", [-2350, -100], "low", 300), [-2350, -300]);
  assert.deepEqual(passbandWithEdge("LSB", [-2350, -100], "high", 2700), [-2700, -100]);
});

test("an edge clicked beyond the other one moves it along", () => {
  assert.deepEqual(passbandWithEdge("USB", [300, 1000], "low", 1500), [1500, 1550]);
  assert.deepEqual(passbandWithEdge("USB", [1500, 3000], "high", 1000), [950, 1000]);
  assert.deepEqual(passbandWithEdge("LSB", [-1000, -300], "low", 1500), [-1550, -1500]);
});

test("AM and FM: Shift + click sets a symmetric width, a click nothing", () => {
  assert.deepEqual(passbandWithEdge("AM", [-3000, 3000], "high", 2000), [-2000, 2000]);
  assert.equal(passbandWithEdge("FM", [-3000, 3000], "low", 300), null);
});
