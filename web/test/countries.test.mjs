// SPDX-License-Identifier: GPL-3.0-or-later
//
// The country of a callsign from the ITU's call sign series (web/src/countries.ts).

import assert from "node:assert/strict";
import { test } from "node:test";

import { countryOf, shortCountry } from "../../src/remoteg90ctl/server/static/countries.js";

test("calls get the country of their series", () => {
  for (const [call, country] of [
    ["SP2EMW", "Poland"],
    ["SQ9DKS", "Poland"],
    ["3Z0X", "Poland"],
    ["DL1XYZ", "Germany"],
    ["9A1A", "Croatia"],
    ["A61AB", "United Arab Emirates"],
    ["2E0ABC", "United Kingdom of Great Britain and Northern Ireland"],
    ["JA1ABC", "Japan"],
    ["VK9XY", "Australia"],
    ["4X1AB", "Israel"],
    ["E73A", "Bosnia and Herzegovina"],
    ["PH40EUDXF", "Netherlands"],
    ["PJ4A", "Netherlands"], // PJ: three parts of the Kingdom, one series
    ["ua9abc", "Russian Federation"],
  ]) {
    assert.equal(countryOf(call), country, call);
  }
});

test("one-letter prefixes with a digit", () => {
  assert.equal(countryOf("K1ABC"), "United States of America");
  assert.equal(countryOf("W1AW"), "United States of America");
  assert.equal(countryOf("N9CO"), "United States of America");
  assert.equal(countryOf("F5XYZ"), "France");
  assert.equal(countryOf("G4ABC"), "United Kingdom of Great Britain and Northern Ireland");
  assert.equal(countryOf("M0XYZ"), "United Kingdom of Great Britain and Northern Ireland");
  assert.equal(countryOf("I2ABC"), "Italy");
  assert.equal(countryOf("R1ANF"), "Russian Federation");
});

test("portable and other parts after a slash", () => {
  assert.equal(countryOf("SP2EMW/P"), "Poland");
  assert.equal(countryOf("DL/SP2EMW"), "Germany");
  assert.equal(countryOf("SP2EMW/DL"), "Germany");
  assert.equal(countryOf("F/SP2EMW/P"), "France");
  assert.equal(countryOf("W1ABC/4"), "United States of America");
  assert.equal(countryOf("SP2EMW/MM"), "Poland");
});

test("the ITU allocation, not a DXCC entity; unknown calls give nothing", () => {
  assert.equal(countryOf("KH6ABC"), "United States of America");
  assert.equal(countryOf("VP8ABC"), "United Kingdom of Great Britain and Northern Ireland");
  // 3D is split: 3DA-3DM Eswatini, 3DN-3DZ Fiji; a digit there is not told apart.
  assert.equal(countryOf("3DA0XX"), "Eswatini");
  assert.equal(countryOf("3D2AG"), null);
  assert.equal(countryOf("<...>"), null);
  assert.equal(countryOf(""), null);
  assert.equal(countryOf("1A0KM"), null); // no ITU series
  assert.equal(countryOf("CQ DX"), null);
});

test("short names for the column; the full name stays for the tooltip", () => {
  assert.equal(shortCountry("United Kingdom of Great Britain and Northern Ireland"), "United Kingdom");
  assert.equal(shortCountry("Russian Federation"), "Russia");
  assert.equal(shortCountry("United States of America"), "United States");
  assert.equal(shortCountry("Netherlands - Aruba"), "Aruba");
  assert.equal(shortCountry("China - Hong Kong"), "Hong Kong");
  assert.equal(shortCountry("Poland"), "Poland");
});
