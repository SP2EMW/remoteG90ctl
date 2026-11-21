// SPDX-License-Identifier: GPL-3.0-or-later
//
// Number fields in place of sliders: what a typed value becomes.

import assert from "node:assert/strict";
import { test } from "node:test";

import { fieldValue } from "../../src/remoteg90ctl/server/static/number-field.js";

test("a value inside the range and on the step stays", () => {
  assert.equal(fieldValue("300", -6000, 6000, 50), 300);
  assert.equal(fieldValue("-12", -20, 20, 1), -12);
});

test("out of range is clamped", () => {
  assert.equal(fieldValue("9000", -6000, 6000, 50), 6000);
  assert.equal(fieldValue("-100", -80, -20, 1), -80);
});

test("off the step is rounded to it", () => {
  assert.equal(fieldValue("320", -6000, 6000, 50), 300);
  assert.equal(fieldValue("330", -6000, 6000, 50), 350);
  assert.equal(fieldValue("2.6", 0, 20, 1), 3);
});

test("a decimal comma is read", () => {
  assert.equal(fieldValue("2,4", 0, 20, 1), 2);
});

test("empty or not a number gives null", () => {
  assert.equal(fieldValue("", 0, 20, 1), null);
  assert.equal(fieldValue("  ", 0, 20, 1), null);
  assert.equal(fieldValue("-", 0, 20, 1), null);
  assert.equal(fieldValue("abc", 0, 20, 1), null);
});

test("rounding never leaves the range", () => {
  assert.equal(fieldValue("4000", 1500, 4000, 300), 3900);
  assert.equal(fieldValue("-37", -37, 0, 1), -37);
});
