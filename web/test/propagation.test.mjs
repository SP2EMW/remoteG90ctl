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

test("solar figures graded good, fair or poor", async () => {
  const { solarGrade } = await import("../../src/remoteg90ctl/server/static/propagation.js");
  assert.deepEqual([150, 120, 119, 87, 80, 79, 65].map((v) => solarGrade("SFI", v)), ["good", "good", "fair", "fair", "fair", "poor", "poor"]);
  assert.deepEqual([0, 9, 10, 29, 30].map((v) => solarGrade("A", v)), ["good", "good", "fair", "fair", "poor"]);
  assert.deepEqual([0, 2, 3, 4, 5, 9].map((v) => solarGrade("K", v)), ["good", "good", "fair", "fair", "poor", "poor"]);
});

test("band conditions: counts and the rough estimate", async () => {
  const { countGrade, estimateGrade } = await import("../../src/remoteg90ctl/server/static/propagation.js");
  assert.deepEqual([0, 2, 3, 9, 10].map(countGrade), ["poor", "poor", "fair", "fair", "good"]);
  // light: path/station 0 night .. 1 day; winter -1 summer .. 1 winter.
  const night = { path: 0, station: 0, sunUp: false, winter: 0 };
  const day = { path: 1, station: 1, sunUp: true, winter: 0 };
  // Low sun activity at night: 10 m closed, 30 m too high, 40 m and 80 m good.
  assert.equal(estimateGrade(28.5, 75, 1, night), "poor");
  assert.equal(estimateGrade(10.12, 75, 1, night), "poor");
  assert.equal(estimateGrade(7.1, 75, 1, night), "good");
  assert.equal(estimateGrade(3.6, 75, 1, night), "good");
  // A winter night at low activity: 40 m goes over the MUF for a 3000 km hop.
  assert.equal(estimateGrade(7.1, 75, 1, { ...night, winter: 1 }), "poor");
  // High activity by day: 10 m good, 80 m poor (absorption).
  assert.equal(estimateGrade(28.5, 200, 1, day), "good");
  assert.equal(estimateGrade(3.6, 200, 1, day), "poor");
  // A storm lowers it: 20 m by day at SFI 85 goes from good to fair.
  assert.equal(estimateGrade(14.15, 85, 1, day), "good");
  assert.equal(estimateGrade(14.15, 85, 6, day), "fair");
  // The MUF grows more slowly than SFI, and is higher by day in winter.
  const { estimateMuf, f2Daylight, destination, daylight } = await import(
    "../../src/remoteg90ctl/server/static/propagation.js"
  );
  const low = estimateMuf(70, null, day);
  const mid = estimateMuf(135, null, day);
  const high = estimateMuf(200, null, day);
  assert.ok(Math.abs(low - 17) < 0.01 && Math.abs(high - 37) < 0.01);
  assert.ok(mid - low > high - mid); // concave in SFI
  assert.ok(estimateMuf(120, null, { ...day, winter: 1 }) > estimateMuf(120, null, { ...day, winter: -1 }));
  assert.ok(estimateMuf(120, null, { ...night, winter: 1 }) < estimateMuf(120, null, { ...night, winter: -1 }));
  // The F2 layer lags: at JO93 on 21 December (sunset about 14:25 UTC) there
  // is still some day ionisation an hour after sunset, none at midnight.
  const at = (h, m) => Date.UTC(2026, 11, 21, h, m);
  const jo93 = { lat: 53.5, lon: 17 };
  assert.ok(f2Daylight(jo93.lat, jo93.lon, at(15, 30)) > 0.2);
  assert.equal(f2Daylight(jo93.lat, jo93.lon, at(0, 0)), 0);
  assert.equal(f2Daylight(jo93.lat, jo93.lon, at(11, 0)), 1);
  // 1500 km starting due east of 53.5 N 17 E: a great circle, so it ends a
  // little south of the parallel, near 51.4 N 39.0 E.
  const east = destination(53.5, 17, 90, 1500);
  assert.ok(Math.abs(east.lon - 38.96) < 0.05 && Math.abs(east.lat - 51.41) < 0.05);
  // In December the northern hemisphere has winter; the best direction is at
  // least as light as the station.
  const dec = daylight(jo93, at(15, 30));
  assert.equal(dec.winter > 0.9, true);
  assert.ok(dec.path >= dec.station);
  // An hour after sunset at JO93 (28 November, 15:30 UTC) the F2 layer still
  // has day ionisation, but the D layer is gone: 80 m is not absorbed.
  const dusk = daylight({ lat: 53.5, lon: 19 }, Date.UTC(2026, 10, 28, 15, 30));
  assert.equal(dusk.sunUp, false);
  assert.ok(dusk.station > 0.3);
  assert.equal(estimateGrade(3.6, 87, 1, dusk), "good");
  assert.equal(estimateGrade(3.6, 87, 1, daylight({ lat: 53.5, lon: 19 }, Date.UTC(2026, 10, 28, 11, 0))), "poor");
});

test("WCY extras: trend, warnings and the K for the estimate", async () => {
  const { trend, solarWarnings, estimateK } = await import("../../src/remoteg90ctl/server/static/propagation.js");
  assert.deepEqual([trend(90, 87), trend(87, 90), trend(87, 87), trend(87, null)], ["↑", "↓", "→", ""]);
  assert.deepEqual(solarWarnings({ sfi: 87, k: 1, exp_k: 2, sa: "qui", au: false }), []);
  assert.deepEqual(solarWarnings({ sfi: 87, k: 2, exp_k: 5, sa: "eru", au: true }), [
    "Geomagnetic storm likely: expected K 5 (now 2)",
    "Solar activity: eru — sudden fade-outs possible on the daylit side",
    "Aurora: paths near the poles (e.g. to North America, Japan) may be weak",
  ]);
  assert.equal(estimateK({ sfi: 87, k: 2, exp_k: 5 }), 5);
  assert.equal(estimateK({ sfi: 87, k: 2, exp_k: 3 }), 2); // below the storm level
  assert.equal(estimateK({ sfi: 87, k: 2 }), 2);
});

test("the estimate with A, SA and the long path", async () => {
  const { estimateDetail, estimateMuf, longPathDetail } = await import("../../src/remoteg90ctl/server/static/propagation.js");
  const day = { path: 1, station: 1, sunUp: true, winter: 0 };
  const night = { path: 0, station: 0, sunUp: false, winter: 0 };
  // A day of storm lowers the MUF even when K has calmed down.
  assert.ok(estimateMuf(120, 1, day, 35) < estimateMuf(120, 1, day, 5));
  assert.equal(estimateMuf(120, 5, day, 5), estimateMuf(120, 1, day, 35));
  // 20 m by day at SFI 85: good; an eruptive Sun lowers it (below 15 MHz), not 17 m.
  assert.equal(estimateDetail(14.15, 85, day, { k: 1 }).grade, "good");
  const eru = estimateDetail(14.15, 85, day, { k: 1, sa: "eru" });
  assert.equal(eru.grade, "fair");
  assert.match(eru.reasons.join(), /solar activity \(eru\)/);
  assert.equal(estimateDetail(18.1, 85, day, { k: 1, sa: "eru" }).grade, estimateDetail(18.1, 85, day, { k: 1 }).grade);
  // A major flare: below 10 MHz poor.
  assert.equal(estimateDetail(7.1, 120, day, { k: 1, sa: "maj" }).grade, "poor");
  // At night flares do nothing.
  assert.equal(estimateDetail(7.1, 75, night, { k: 1, sa: "maj" }).grade, estimateDetail(7.1, 75, night, { k: 1 }).grade);
  // The long path: a step below; with aurora the low bands poor.
  const sp = estimateDetail(14.15, 120, day, { k: 1 });
  assert.equal(sp.grade, "good");
  assert.equal(longPathDetail(sp, 14.15, false).grade, "fair");
  assert.equal(longPathDetail(sp, 14.15, true).grade, "poor");
  const sp40 = estimateDetail(7.1, 75, night, { k: 1 });
  assert.equal(longPathDetail(sp40, 7.1, true).grade, "poor");
  assert.match(longPathDetail(sp40, 7.1, true).reasons.join(), /aurora/);
});
