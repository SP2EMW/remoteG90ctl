// SPDX-License-Identifier: GPL-3.0-or-later
//
// The setting blocks in columns, with bars between them that change the
// widths (owner's request 2026-11-22), like the handle below the waterfall.
// A bar moves width between its two neighbours only; the browser lays out
// the controls inside each block again by itself. Widths are kept per browser
// as proportions; a double click on a bar restores the defaults.

export const DEFAULT_COLUMNS = [1.05, 0.75, 1.45, 1.75];

// The narrowest a column may be dragged to, in pixels; the grid also never
// makes a column narrower than its widest single control.
export const MIN_COLUMN_PX = 120;

// Proportions after dragging the bar right of column `bar` by dx pixels,
// from the widths at the start of the drag. The pair keeps its total width;
// the result sums to the same total as DEFAULT_COLUMNS.
export function dragColumns(widths: readonly number[], bar: number, dx: number, minPx = MIN_COLUMN_PX): number[] {
  const next = [...widths];
  const pair = widths[bar] + widths[bar + 1];
  const low = Math.min(minPx, pair / 2);
  const left = Math.max(low, Math.min(pair - low, widths[bar] + dx));
  next[bar] = left;
  next[bar + 1] = pair - left;
  return normalise(next);
}

// Saved proportions, or null when they are not four positive numbers.
export function parseColumns(text: string | null): number[] | null {
  if (text === null) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!Array.isArray(value) || value.length !== DEFAULT_COLUMNS.length) return null;
    if (!value.every((v) => typeof v === "number" && Number.isFinite(v) && v > 0)) return null;
    return normalise(value as number[]);
  } catch {
    return null;
  }
}

// The grid's column template: each block at least as wide as its content,
// with a fixed track for each bar between them.
export function columnTemplate(fractions: readonly number[], barPx: number): string {
  return fractions.map((f) => `minmax(min-content, ${f.toFixed(4)}fr)`).join(` ${barPx}px `);
}

function normalise(values: readonly number[]): number[] {
  const total = DEFAULT_COLUMNS.reduce((a, b) => a + b, 0);
  const sum = values.reduce((a, b) => a + b, 0);
  return values.map((v) => (v / sum) * total);
}
