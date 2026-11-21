// SPDX-License-Identifier: GPL-3.0-or-later
//
// Movable windows (owner's request 2026-11-10): the place always keeps the title
// bar on the screen, and a damaged saved place is ignored.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MIN_HEIGHT_PX,
  MIN_WIDTH_PX,
  TITLE_BAR_PX,
  TITLE_KEEP_PX,
  clampPlace,
  parsePlace,
} from "../../src/remoteg90ctl/server/static/floating.js";

test("a place inside the view is kept as it is", () => {
  const place = { left: 100, top: 50, width: 400, height: 300 };
  assert.deepEqual(clampPlace(place, 1280, 800), place);
});

test("the title bar stays reachable whatever the place", () => {
  // Dragged far to the right and down.
  let p = clampPlace({ left: 5000, top: 5000, width: 400, height: 300 }, 1280, 800);
  assert.equal(p.left, 1280 - TITLE_KEEP_PX);
  assert.equal(p.top, 800 - TITLE_BAR_PX);
  // Far to the left and above the page.
  p = clampPlace({ left: -5000, top: -50, width: 400, height: 300 }, 1280, 800);
  assert.equal(p.left, TITLE_KEEP_PX - 400);
  assert.equal(p.top, 0);
});

test("the size stays between the minimum and the view", () => {
  const small = clampPlace({ left: 0, top: 0, width: 10, height: 10 }, 1280, 800);
  assert.deepEqual([small.width, small.height], [MIN_WIDTH_PX, MIN_HEIGHT_PX]);
  // Saved on a large screen, opened on a phone.
  const big = clampPlace({ left: 900, top: 600, width: 1200, height: 900 }, 390, 700);
  assert.deepEqual([big.width, big.height], [390, 700]);
  assert.ok(big.left <= 390 - TITLE_KEEP_PX && big.top <= 700 - TITLE_BAR_PX);
});

test("a saved place is read back, a damaged one ignored", () => {
  const place = { left: 10, top: 20, width: 300, height: 200 };
  assert.deepEqual(parsePlace(JSON.stringify(place)), place);
  for (const text of [null, "", "{", "[]", '{"left": 1}', JSON.stringify({ ...place, width: -3 }), JSON.stringify({ ...place, top: "x" })]) {
    assert.equal(parsePlace(text), null, String(text));
  }
});
