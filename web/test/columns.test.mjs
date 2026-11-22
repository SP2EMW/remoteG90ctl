// SPDX-License-Identifier: GPL-3.0-or-later
//
// Column widths of the setting blocks, changed by the bars between them.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_COLUMNS,
  columnTemplate,
  dragColumns,
  parseColumns,
} from "../../src/remoteg90ctl/server/static/columns.js";

const TOTAL = DEFAULT_COLUMNS.reduce((a, b) => a + b, 0);
const sum = (v) => v.reduce((a, b) => a + b, 0);
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("dragging moves width between the two neighbours only", () => {
  const widths = [300, 200, 400, 500];
  const next = dragColumns(widths, 1, 50);
  const scale = TOTAL / 1400;
  close(next[0], 300 * scale);
  close(next[1], 250 * scale);
  close(next[2], 350 * scale);
  close(next[3], 500 * scale);
  close(sum(next), TOTAL);
});

test("a column never goes below the minimum", () => {
  const next = dragColumns([300, 200, 400, 500], 0, -1000, 120);
  close(next[0] / sum(next), 120 / 1400);
  const other = dragColumns([300, 200, 400, 500], 0, 1000, 120);
  close(other[1] / sum(other), 120 / 1400);
});

test("a pair narrower than twice the minimum is split evenly at most", () => {
  const next = dragColumns([100, 100, 400, 500], 0, -500, 120);
  close(next[0], next[1]);
});

test("saved proportions are read back, anything else refused", () => {
  const saved = parseColumns(JSON.stringify([1, 1, 1, 1]));
  assert.ok(saved !== null);
  close(sum(saved), TOTAL);
  assert.equal(parseColumns(null), null);
  assert.equal(parseColumns("not json"), null);
  assert.equal(parseColumns("[1, 2, 3]"), null);
  assert.equal(parseColumns("[1, 2, 0, 3]"), null);
  assert.equal(parseColumns('[1, 2, "3", 4]'), null);
});

test("the template keeps each block at least as wide as its content", () => {
  assert.equal(
    columnTemplate([1, 2, 3, 4], 12),
    "minmax(min-content, 1.0000fr) 12px minmax(min-content, 2.0000fr) 12px " +
      "minmax(min-content, 3.0000fr) 12px minmax(min-content, 4.0000fr)",
  );
});
