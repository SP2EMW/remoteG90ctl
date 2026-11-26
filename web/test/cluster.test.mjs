// SPDX-License-Identifier: GPL-3.0-or-later
//
// DX cluster spots on the page (web/src/cluster.ts, decisions.md D63): the
// band and mode of a spot, and where the radio goes when it is clicked.

import assert from "node:assert/strict";
import { test } from "node:test";

import { recentSpots, spotBand, spotMode, tuneFor } from "../../src/remoteg90ctl/server/static/cluster.js";

const DIALS = { FT8: [7_074_000, 14_074_000], FT4: [7_047_500, 14_080_000] };
// A piece of the IARU Region 1 plan (as the server sends it).
const PLAN = [
  { low: 14_000_000, high: 14_070_000, kind: "cw" },
  { low: 14_070_000, high: 14_099_000, kind: "narrow" },
  { low: 14_099_000, high: 14_101_000, kind: "beacon" },
  { low: 14_101_000, high: 14_350_000, kind: "all" },
];
const spot = (freq_khz, comment = "", extra = {}) => ({
  spotter: "SP7ABC",
  dx: "DL1XYZ",
  freq_khz,
  comment,
  utc: "1234",
  locator: null,
  received_ms: 0,
  ...extra,
});

test("the band of a spot", () => {
  assert.equal(spotBand(14074), "20m");
  assert.equal(spotBand(1836), "160m");
  assert.equal(spotBand(7250), "40m"); // Region 2's 40 m
  assert.equal(spotBand(50313), null); // not a G90 band
});

test("the mode from a word in the comment first", () => {
  assert.equal(spotMode(spot(14025, "CW 12 dB 25 WPM CQ"), PLAN, DIALS), "CW");
  assert.equal(spotMode(spot(14080.5, "rtty cq contest"), PLAN, DIALS), "RTTY");
  assert.equal(spotMode(spot(14200, "FT8 -12 dB"), PLAN, DIALS), "FT8"); // the word wins over the segment
  assert.equal(spotMode(spot(14230, "CQ SSTV NA"), PLAN, DIALS), "SSTV"); // its own filter (owner's request 2026-11-26)
  assert.equal(spotMode(spot(14070.15, "PSK31"), PLAN, DIALS), "DIGI");
  assert.equal(spotMode(spot(14025, "usb"), PLAN, DIALS), "SSB");
  assert.equal(spotMode(spot(14200, "CWOPS member"), PLAN, DIALS), "SSB"); // a word, not a part of one
});

test("then the FT8/FT4 calling frequencies, then the band plan", () => {
  assert.equal(spotMode(spot(14075.6), PLAN, DIALS), "FT8");
  assert.equal(spotMode(spot(14081.2), PLAN, DIALS), "FT4");
  assert.equal(spotMode(spot(14023), PLAN, DIALS), "CW");
  assert.equal(spotMode(spot(14100), PLAN, DIALS), "CW"); // beacons
  assert.equal(spotMode(spot(14090), PLAN, DIALS), "DIGI");
  assert.equal(spotMode(spot(14205), PLAN, DIALS), "SSB");
  assert.equal(spotMode(spot(14023), null, DIALS), "SSB"); // no plan (no region)
});

test("where the radio goes for each mode", () => {
  const voice = (hz) => (hz < 10_000_000 ? "L-D" : "U-D");
  assert.deepEqual(tuneFor(spot(14023.5), "CW", DIALS, 458, voice), { hz: 14_023_500, mode: "CW" });
  assert.deepEqual(tuneFor(spot(7150), "SSB", DIALS, 458, voice), { hz: 7_150_000, mode: "L-D" });
  assert.deepEqual(tuneFor(spot(14075.6), "FT8", DIALS, 458, voice), { hz: 14_074_000, mode: "U-D" });
  assert.deepEqual(tuneFor(spot(7049.1), "FT4", DIALS, 458, voice), { hz: 7_047_500, mode: "U-D" });
  // RTTY in L-D: the mark tone of the RTTY window lands on the spot.
  assert.deepEqual(tuneFor(spot(14085), "RTTY", DIALS, 458, voice), { hz: 14_085_458, mode: "L-D" });
  assert.deepEqual(tuneFor(spot(14070.15), "DIGI", DIALS, 458, voice), { hz: 14_068_650, mode: "U-D" });
  // SSTV is spotted on its dial frequency.
  assert.deepEqual(tuneFor(spot(14230, "CQ SSTV"), "SSTV", DIALS, 458, voice), { hz: 14_230_000, mode: "U-D" });
});

test("the waterfall gets the newest spot of each station and band, of the last minutes", () => {
  const spots = [
    spot(14074, "", { dx: "A1A", received_ms: 1_000 }),
    spot(14075, "", { dx: "A1A", received_ms: 500_000 }),
    spot(7074, "", { dx: "A1A", received_ms: 600_000 }),
    spot(14200, "", { dx: "B2B", received_ms: 700_000 }),
  ];
  const now = 1_000_000;
  const recent = recentSpots(spots, now, 15 * 60 * 1000);
  assert.deepEqual(
    recent.map((s) => [s.dx, s.freq_khz]),
    [
      ["B2B", 14200],
      ["A1A", 7074],
      ["A1A", 14075],
    ],
  );
  assert.equal(recentSpots(spots, now, 450_000).length, 2); // the 20 m A1A spot at 500 s is too old
});
