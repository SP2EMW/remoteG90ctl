// SPDX-License-Identifier: GPL-3.0-or-later
//
// The continent of a place or a call (web/src/continents.ts), for the spots
// near this station in the Propagation window.

import assert from "node:assert/strict";
import { test } from "node:test";

import { continentAt, continentOfCall, spotterContinent } from "../../src/remoteg90ctl/server/static/continents.js";

test("places on each continent", () => {
  for (const [lat, lon, code, where] of [
    [53.5, 19, "EU", "JO93, Poland"],
    [55.75, 37.6, "EU", "Moscow"],
    [55, 82.9, "AS", "Novosibirsk (Russia east of 60 E)"],
    [35.7, 139.7, "AS", "Tokyo (an island: near the outline)"],
    [40.7, -74, "NA", "New York"],
    [-23.5, -46.6, "SA", "Sao Paulo"],
    [-33.9, 151.2, "OC", "Sydney"],
    [-1.3, 36.8, "AF", "Nairobi"],
    [28.1, -15.4, "AF", "Gran Canaria (the 110m outlines lack it: Africa is nearest)"],
    [39, 35, "AS", "Anatolia"],
  ]) {
    assert.equal(continentAt(lat, lon), code, where);
  }
  assert.equal(continentAt(-40, -120), null); // the open Pacific
});

test("calls by their ITU state; Russia by its call area", () => {
  assert.equal(continentOfCall("SP2EMW"), "EU");
  assert.equal(continentOfCall("UA3ABC"), "EU");
  assert.equal(continentOfCall("UA9ABC"), "AS");
  assert.equal(continentOfCall("R0FA"), "AS");
  assert.equal(continentOfCall("JA1ABC"), "AS");
  assert.equal(continentOfCall("K1ABC"), "NA");
  assert.equal(continentOfCall("PY2ABC"), "SA");
  assert.equal(continentOfCall("VK2ABC"), "OC");
  assert.equal(continentOfCall("ZS6ABC"), "AF");
  assert.equal(continentOfCall("TA1ABC"), "AS");
  assert.equal(continentOfCall("4U1ITU"), null); // United Nations
});

test("a spotter: its locator first, else its call", () => {
  // A W1 spotter giving a European locator (abroad) counts where it is.
  assert.equal(spotterContinent("W1AW", "JO93"), "EU");
  assert.equal(spotterContinent("W1AW", null), "NA");
  assert.equal(spotterContinent("DK9IP-#", null), "EU");
  assert.equal(spotterContinent("SP7ABC", "XX99"), "EU"); // a bad locator: the call
});
