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
