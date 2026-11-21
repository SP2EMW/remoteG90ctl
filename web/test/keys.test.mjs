// SPDX-License-Identifier: GPL-3.0-or-later
//
// Keyboard shortcuts (owner's request 2026-11-13): which key does what, and
// that keys with Ctrl or Meta (PTT is left Ctrl + left Alt; zoom, tabs) stay
// with the browser.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  actionFor,
  adjacentBand,
  gridStep,
  nextAgc,
  rfGainStep,
} from "../../src/remoteg90ctl/server/static/keys.js";

const key = (code, extra = {}) => ({
  code,
  key: extra.key ?? "",
  altKey: false,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  ...extra,
});

test("arrows tune by the waterfall's step, Alt by 10 Hz, Shift by 100 Hz", () => {
  assert.deepEqual(actionFor(key("ArrowRight")), { kind: "tune", direction: 1, stepHz: null });
  assert.deepEqual(actionFor(key("ArrowLeft")), { kind: "tune", direction: -1, stepHz: null });
  assert.deepEqual(actionFor(key("ArrowLeft", { altKey: true })), { kind: "tune", direction: -1, stepHz: 10 });
  assert.deepEqual(actionFor(key("ArrowRight", { shiftKey: true })), { kind: "tune", direction: 1, stepHz: 100 });
});

test("no shortcut uses Ctrl or Meta: the PTT keys and the browser's own stay free", () => {
  for (const code of ["ArrowLeft", "ArrowRight", "ArrowUp", "KeyA", "KeyN", "KeyL", "F8", "F4"]) {
    assert.equal(actionFor(key(code, { ctrlKey: true })), null, code);
    assert.equal(actionFor(key(code, { ctrlKey: true, altKey: true })), null, code);
    assert.equal(actionFor(key(code, { metaKey: true })), null, code);
  }
  assert.equal(actionFor(key("Equal", { key: "=", ctrlKey: true })), null); // zoom
  assert.equal(actionFor(key("F4", { altKey: true })), null); // closes the window
});

test("Alt + up/down changes the band; plain up/down scrolls the page", () => {
  assert.deepEqual(actionFor(key("ArrowUp", { altKey: true })), { kind: "band", direction: 1 });
  assert.deepEqual(actionFor(key("ArrowDown", { altKey: true })), { kind: "band", direction: -1 });
  assert.deepEqual(actionFor(key("PageUp", { altKey: true })), { kind: "seek", direction: 1 });
  assert.deepEqual(actionFor(key("PageDown", { altKey: true })), { kind: "seek", direction: -1 });
  assert.equal(actionFor(key("PageUp")), null); // plain PgUp scrolls the page
  for (const shiftKey of [false, true]) {
    assert.deepEqual(actionFor(key("Period", { shiftKey })), { kind: "seek", direction: 1 });
    assert.deepEqual(actionFor(key("Comma", { shiftKey })), { kind: "seek", direction: -1 });
  }
  assert.equal(actionFor(key("Period", { ctrlKey: true })), null);
  assert.equal(actionFor(key("ArrowUp")), null);
  assert.equal(actionFor(key("ArrowDown")), null);
});

test("letters, F keys and plus/minus", () => {
  assert.deepEqual(actionFor(key("F8")), { kind: "digital", mode: "FT8" });
  assert.deepEqual(actionFor(key("F4")), { kind: "digital", mode: "FT4" });
  assert.deepEqual(actionFor(key("KeyL")), { kind: "log" });
  assert.deepEqual(actionFor(key("KeyP")), { kind: "frontEnd", state: "pre" });
  assert.deepEqual(actionFor(key("KeyA")), { kind: "frontEnd", state: "att" });
  assert.deepEqual(actionFor(key("KeyN")), { kind: "frontEnd", state: "off" });
  assert.deepEqual(actionFor(key("KeyA", { altKey: true })), { kind: "agc" });
  assert.deepEqual(actionFor(key("KeyN", { altKey: true })), { kind: "nb" });
  for (const k of ["+", "="]) assert.deepEqual(actionFor(key("Equal", { key: k })), { kind: "rfGain", direction: 1 });
  assert.deepEqual(actionFor(key("Equal", { key: "+", shiftKey: true })), { kind: "rfGain", direction: 1 });
  for (const k of ["-", "_"]) assert.deepEqual(actionFor(key("Minus", { key: k })), { kind: "rfGain", direction: -1 });
  assert.equal(actionFor(key("KeyL", { shiftKey: true })), null);
  assert.equal(actionFor(key("KeyT")), null); // nothing transmits from the keyboard here
  assert.equal(actionFor(key("Space")), null);
});

test("a grid step goes to the next point; from between points to the nearest that way", () => {
  assert.equal(gridStep(21_134_000, 100, 1), 21_134_100);
  assert.equal(gridStep(21_134_000, 100, -1), 21_133_900);
  assert.equal(gridStep(21_134_050, 100, 1), 21_134_100);
  assert.equal(gridStep(21_134_050, 100, -1), 21_134_000);
  assert.equal(gridStep(21_134_005, 10, -1), 21_134_000);
});

const BANDS = [
  ["40m", 7_000_000, 7_200_000],
  ["20m", 14_000_000, 14_350_000],
  ["15m", 21_000_000, 21_450_000],
];

test("the next band, also from outside every band; none past the ends", () => {
  assert.equal(adjacentBand(BANDS, 14_074_000, 1)[0], "15m");
  assert.equal(adjacentBand(BANDS, 14_074_000, -1)[0], "40m");
  assert.equal(adjacentBand(BANDS, 10_000_000, 1)[0], "20m");
  assert.equal(adjacentBand(BANDS, 10_000_000, -1)[0], "40m");
  assert.equal(adjacentBand(BANDS, 21_100_000, 1), null);
  assert.equal(adjacentBand(BANDS, 7_100_000, -1), null);
});

test("AGC goes round; unknown starts at off", () => {
  assert.deepEqual(["off", "fast", "slow", "auto"].map(nextAgc), ["fast", "slow", "auto", "off"]);
  assert.equal(nextAgc(""), "off");
});

test("RF gain steps by 5 % within 1 to 100", () => {
  assert.equal(rfGainStep(50, 1), 55);
  assert.equal(rfGainStep(52, -1), 50);
  assert.equal(rfGainStep(100, 1), 100);
  assert.equal(rfGainStep(5, -1), 1);
  assert.equal(rfGainStep(1, -1), 1);
});
