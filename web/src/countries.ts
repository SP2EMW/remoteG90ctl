// SPDX-License-Identifier: GPL-3.0-or-later
//
// The country of a callsign (owner's request 2026-11-27): the state its call
// sign series is allocated to, from the ITU's table (countries-data.ts,
// Radio Regulations Appendix 42). Shown as "Country" next to spots and
// decodes, whatever the station log holds. It is the ITU allocation, not a
// DXCC entity: KH6 is the United States, VP8 the United Kingdom, BV China.
// No network: the table comes with the program.

import { CALL_SERIES, COUNTRY_NAMES } from "./countries-data.js";

type Entry = number | [from: string, to: string, name: number][];

// "SP" -> its country; a series split on the third character -> its parts.
const SERIES = new Map<string, Entry>();
// A first character all of whose series belong to one country (K, W, F, G…):
// calls like K1ABC or F5XYZ have that letter and a digit as their prefix.
const WHOLE_LETTER = new Map<string, number>();

for (const item of CALL_SERIES.split(" ")) {
  const [series, index] = item.split(":");
  const name = Number(index);
  if (series.length === 2) {
    SERIES.set(series, name);
  } else {
    const [from, to] = series.split("-");
    const key = from.slice(0, 2);
    const parts = SERIES.get(key);
    const list = Array.isArray(parts) ? parts : [];
    list.push([from, to, name]);
    SERIES.set(key, list);
  }
}
{
  const byLetter = new Map<string, Set<number>>();
  for (const [key, entry] of SERIES) {
    const set = byLetter.get(key[0]) ?? new Set<number>();
    if (typeof entry === "number") set.add(entry);
    else for (const [, , name] of entry) set.add(name);
    byLetter.set(key[0], set);
  }
  for (const [letter, names] of byLetter) if (names.size === 1) WHOLE_LETTER.set(letter, [...names][0]);
}

// Parts after "/" that say how, not where: /P, /M, /MM, /AM, /QRP, /A, /B
// (beacon), /LH, and a call area digit (W1ABC/4).
const NOT_A_PLACE = /^(P|M|MM|AM|QRP|QRPP|A|B|LH|\d)$/;

// The part of a call that carries its prefix: DL/SP2EMW -> DL, SP2EMW/P -> SP2EMW.
function prefixPart(call: string): string | null {
  const parts = call.split("/").filter((p) => p !== "" && !NOT_A_PLACE.test(p));
  if (parts.length === 0) return null;
  return parts.reduce((a, b) => (b.length < a.length ? b : a));
}

export function countryOf(call: string): string | null {
  const text = call.trim().toUpperCase();
  if (!/^[A-Z0-9/]+$/.test(text)) return null; // hashed FT8 calls, free text
  const prefix = prefixPart(text);
  if (prefix === null) return null;
  const entry = prefix.length >= 2 ? SERIES.get(prefix.slice(0, 2)) : undefined;
  if (typeof entry === "number") return COUNTRY_NAMES[entry];
  if (entry !== undefined) {
    // A series split on the third character (3DA-3DM, 3DN-3DZ): a call with a
    // digit there (3D2…) is not told apart by the table.
    const third = prefix.slice(0, 3);
    const part = entry.find(([from, to]) => third.length === 3 && from <= third && third <= to);
    return part === undefined ? null : COUNTRY_NAMES[part[2]];
  }
  // K1ABC, F5XYZ, or a lone "F/" prefix: the first letter's country, if one.
  if (prefix.length === 1 || /\d/.test(prefix[1])) {
    const whole = WHOLE_LETTER.get(prefix[0]);
    if (whole !== undefined) return COUNTRY_NAMES[whole];
  }
  return null;
}
