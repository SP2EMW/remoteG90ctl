// SPDX-License-Identifier: GPL-3.0-or-later
//
// Tests of the centre-line remover, on the compiled JavaScript (node --test).

import assert from "node:assert/strict";
import { test } from "node:test";

import { SpikeRemover } from "../../src/remoteg90ctl/server/static/spike.js";

const N = 1024;
const SPAN = 96000;
const MIN_DB = -140;
const MAX_DB = -20;

// Deterministic noise around the floor, a spike at the centre, optional carrier.
function makeLine(seed, { spikeDb = 25, carrier = null, blanked = false } = {}) {
  let s = seed * 2654435761;
  const rnd = () => {
    s = (s * 1103515245 + 12345) >>> 0;
    return s / 2 ** 32;
  };
  const bins = new Uint8Array(N);
  for (let i = 0; i < N; i++) {
    let db = -110 + (rnd() - 0.5) * 6;
    const d = Math.abs(i - N / 2);
    if (spikeDb > 0 && d <= 3) db = 10 * Math.log10(10 ** (db / 10) + 10 ** ((-110 + spikeDb - 6 * d) / 10));
    if (carrier !== null && i === carrier.bin) db = Math.max(db, carrier.db);
    bins[i] = Math.round(((db - MIN_DB) / (MAX_DB - MIN_DB)) * 255);
  }
  return { blanked, spanHz: SPAN, minDb: MIN_DB, maxDb: MAX_DB, bins };
}

const toDb = (v) => MIN_DB + (v / 255) * (MAX_DB - MIN_DB);

function clock() {
  let t = 0;
  return { now: () => t, advance: (ms) => (t += ms) };
}

function learn(remover, c, lines, options) {
  let out = null;
  for (let k = 0; k < lines; k++) {
    out = remover.process(makeLine(k + 1, options));
    c.advance(50);
  }
  return out;
}

test("removes the steady line at the centre after learning", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  assert.equal(remover.learning, true);
  const out = learn(remover, c, 100);
  assert.equal(remover.learning, false);
  assert.ok(remover.lastWidth >= 1);
  const raw = makeLine(101);
  const cleaned = remover.process(raw);
  assert.ok(toDb(raw.bins[N / 2]) > -90, "the test line has a spike");
  assert.ok(toDb(cleaned.bins[N / 2]) < -100, `centre after removal: ${toDb(cleaned.bins[N / 2])}`);
  assert.ok(out !== null);
});

test("the cleaned centre looks like the background", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  const centre = [];
  const background = [];
  for (let k = 0; k < 200; k++) {
    const cleaned = remover.process(makeLine(1000 + k));
    c.advance(50);
    for (let d = -3; d <= 3; d++) centre.push(toDb(cleaned.bins[N / 2 + d]));
    for (let d = 100; d < 107; d++) background.push(toDb(cleaned.bins[N / 2 + d]));
  }
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const spread = (a) => Math.sqrt(mean(a.map((x) => (x - mean(a)) ** 2)));
  assert.ok(Math.abs(mean(centre) - mean(background)) < 1, `means ${mean(centre)} vs ${mean(background)}`);
  assert.ok(Math.abs(spread(centre) - spread(background)) < 0.5, `spreads ${spread(centre)} vs ${spread(background)}`);
});

test("a carrier sitting on the centre stays visible", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  // 10 s of a carrier only about 6 dB above the artefact's peak, right on it.
  let last = null;
  for (let k = 0; k < 200; k++) {
    last = remover.process(makeLine(800 + k, { carrier: { bin: N / 2, db: -79 } }));
    c.advance(50);
  }
  assert.ok(toDb(last.bins[N / 2]) > -85, `centre after 10 s: ${toDb(last.bins[N / 2])}`);
});

test("a jump of the artefact lasting one line is removed", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  // The centre bins jump by 15 dB for one line, as the G90's line does once a second.
  const raw = makeLine(900, { spikeDb: 40 });
  assert.ok(toDb(remover.process(raw).bins[N / 2]) < -100);
  // The same level lasting is a signal.
  remover.process(makeLine(901, { spikeDb: 40 }));
  assert.ok(toDb(remover.process(makeLine(902, { spikeDb: 40 })).bins[N / 2]) > -80);
});

test("a signal next to the centre is not copied into it", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  const region = Math.ceil(1000 / (SPAN / N));
  // A carrier exactly where the background of the centre bin is taken from.
  const raw = makeLine(707, { carrier: { bin: N / 2 + region + 1, db: -50 } });
  assert.ok(toDb(remover.process(raw).bins[N / 2]) < -100);
});

test("leaves bins away from the centre untouched", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  const raw = makeLine(202);
  const cleaned = remover.process(raw);
  for (let i = 0; i < N; i++) {
    if (Math.abs(i - N / 2) > remover.lastWidth) assert.equal(cleaned.bins[i], raw.bins[i]);
  }
});

test("a strong signal at the centre stays visible", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  remover.process(makeLine(302, { carrier: { bin: N / 2, db: -50 } }));
  const raw = makeLine(303, { carrier: { bin: N / 2, db: -50 } });
  const cleaned = remover.process(raw);
  assert.ok(toDb(cleaned.bins[N / 2]) > -55);
});

test("a signal passing through the centre is not learned as the artefact", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100, { spikeDb: 0 });
  // A carrier sits on the centre for a second (tuning across it).
  learn(remover, c, 20, { spikeDb: 0, carrier: { bin: N / 2, db: -60 } });
  const raw = makeLine(404, { spikeDb: 0, carrier: { bin: N / 2, db: -60 } });
  assert.ok(toDb(remover.process(raw).bins[N / 2]) > -65);
});

test("nothing is changed without a line at the centre", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100, { spikeDb: 0 });
  const raw = makeLine(505, { spikeDb: 0 });
  assert.equal(remover.process(raw), raw);
  assert.equal(remover.lastWidth, 0);
});

test("blanked lines are not learned from", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100, { spikeDb: 0 });
  // A long transmission with a big line at the centre.
  learn(remover, c, 200, { spikeDb: 40, blanked: true });
  assert.equal(remover.process(makeLine(606, { spikeDb: 0 })).bins.length, N);
  assert.equal(remover.lastWidth, 0);
});

test("a change in the number of bins starts learning again", () => {
  const c = clock();
  const remover = new SpikeRemover(c.now);
  remover.reset();
  learn(remover, c, 100);
  assert.equal(remover.learning, false);
  const small = { blanked: false, spanHz: SPAN, minDb: MIN_DB, maxDb: MAX_DB, bins: new Uint8Array(512).fill(80) };
  remover.process(small);
  assert.equal(remover.learning, true);
});
