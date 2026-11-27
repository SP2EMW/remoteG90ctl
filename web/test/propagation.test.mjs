// SPDX-License-Identifier: GPL-3.0-or-later
//
// The Propagation window's arithmetic (web/src/propagation.ts): the Sun's
// place and height for the day, night and grey line map.

import assert from "node:assert/strict";
import { test } from "node:test";

import { darkness, locatorCentre, subsolarPoint, sunElevation } from "../../src/remoteg90ctl/server/static/propagation.js";

const near = (a, b, tolerance, what) => assert.ok(Math.abs(a - b) <= tolerance, `${what}: ${a} vs ${b}`);

test("the Sun is overhead where the almanac says", () => {
  // June solstice: over the Tropic of Cancer; at 12:00 UTC near Greenwich
  // (the equation of time moves it under 1 degree).
  let sun = subsolarPoint(Date.UTC(2026, 5, 21, 12, 0));
  near(sun.lat, 23.44, 0.1, "June declination");
  near(sun.lon, 0, 1, "June noon longitude");
  // December solstice, 00:00 UTC: Tropic of Capricorn, near the date line.
  sun = subsolarPoint(Date.UTC(2026, 11, 21, 0, 0));
  near(sun.lat, -23.44, 0.1, "December declination");
  near(Math.abs(sun.lon), 180, 1.5, "December midnight longitude");
  // Early November: the equation of time is about +16 min (solar noon at
  // Greenwich 11:44 UTC), so at 12:00 UTC the Sun is about 4 degrees west.
  sun = subsolarPoint(Date.UTC(2026, 10, 3, 12, 0));
  near(sun.lon, -4.1, 0.3, "November noon longitude");
  near(sun.lat, -15.2, 0.3, "November declination");
});

test("day, grey line and night", () => {
  const sun = { lat: 0, lon: 0 };
  near(sunElevation(0, 0, sun), 90, 1e-9, "overhead");
  near(sunElevation(0, 90, sun), 0, 1e-9, "on the horizon");
  near(sunElevation(0, 180, sun), -90, 1e-9, "opposite");
  assert.equal(darkness(10), 0);
  assert.equal(darkness(-3), 0.5);
  assert.equal(darkness(-20), 1);
});

test("the middle of a locator", () => {
  const jo93 = locatorCentre("JO93");
  near(jo93.lon, 19, 1e-9, "JO93 lon");
  near(jo93.lat, 53.5, 1e-9, "JO93 lat");
  const jo93aa = locatorCentre("jo93aa");
  near(jo93aa.lon, 18 + 2.5 / 60, 1e-9, "JO93aa lon");
  near(jo93aa.lat, 53 + 1.25 / 60, 1e-9, "JO93aa lat");
  assert.equal(locatorCentre("ZZ99"), null);
});

test("the FT8 band scan: who was heard, with a locator when sent", async () => {
  const { heardFrom, addHeard, keptScan, SCAN_KEPT_MS } = await import("../../src/remoteg90ctl/server/static/propagation.js");
  assert.deepEqual(heardFrom("CQ SP2EMW JO93", "SP2EMW"), { call: "SP2EMW", grid: "JO93" });
  assert.deepEqual(heardFrom("CQ POTA EI6IF IO63", "EI6IF"), { call: "EI6IF", grid: "IO63" });
  assert.deepEqual(heardFrom("DL1XYZ SP2EMW -12", "SP2EMW"), { call: "SP2EMW", grid: null });
  assert.deepEqual(heardFrom("DL1XYZ SP2EMW RR73", "SP2EMW"), { call: "SP2EMW", grid: null });
  assert.equal(heardFrom("TNX 73", null), null);
  const stations = new Map();
  addHeard(stations, { call: "SP2EMW", band: "20m", snr: -12, grid: null, at: 1 });
  addHeard(stations, { call: "SP2EMW", band: "20m", snr: -5, grid: "JO93", at: 2 });
  addHeard(stations, { call: "SP2EMW", band: "40m", snr: -20, grid: null, at: 3 });
  assert.equal(stations.size, 2);
  assert.deepEqual(stations.get("SP2EMW 20m"), { call: "SP2EMW", band: "20m", snr: -5, grid: "JO93", at: 2 });
  const saved = JSON.stringify({ at: 1000, stations: [...stations.values()] });
  assert.equal(keptScan(saved, 1000 + SCAN_KEPT_MS).stations.length, 2);
  assert.equal(keptScan(saved, 1001 + SCAN_KEPT_MS), null); // older than three hours
  assert.equal(keptScan("broken", 0), null);
});
