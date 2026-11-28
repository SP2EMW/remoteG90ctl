// SPDX-License-Identifier: GPL-3.0-or-later
//
// The continent of a place or of a call (owner's request 2026-11-28: the
// Propagation window counts the spots of spotters on this station's
// continent). A place: inside the states' outlines (continents-data.ts,
// Natural Earth, Russia split at 60 E), or the nearest outline within
// NEAR_KM (islands, coasts). A call: the continent of its ITU state; Russian
// call areas 8, 9 and 0 are Asia. The ITU table gives the state, so EA8 counts
// as Europe and KH6 as North America (not DXCC).

import { CONTINENT_OF_COUNTRY, CONTINENT_RINGS } from "./continents-data.js";
import { countryOf } from "./countries.js";
import { locatorCentre } from "./propagation.js";

export const CONTINENT_NAMES: Readonly<Record<string, string>> = {
  EU: "Europe",
  AS: "Asia",
  AF: "Africa",
  NA: "North America",
  SA: "South America",
  OC: "Oceania",
  AN: "Antarctica",
};

const NEAR_KM = 600;

interface Ring {
  tag: string;
  points: Float64Array; // lon, lat, lon, lat… degrees
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
}

let rings: Ring[] | null = null;

function loaded(): Ring[] {
  if (rings !== null) return rings;
  rings = CONTINENT_RINGS.map((text) => {
    const [tag, data] = text.split(":");
    const points = Float64Array.from(data.split(",").map((v) => Number(v) / 10));
    let minLon = 180, maxLon = -180, minLat = 90, maxLat = -90;
    for (let i = 0; i < points.length; i += 2) {
      minLon = Math.min(minLon, points[i]);
      maxLon = Math.max(maxLon, points[i]);
      minLat = Math.min(minLat, points[i + 1]);
      maxLat = Math.max(maxLat, points[i + 1]);
    }
    return { tag, points, minLon, maxLon, minLat, maxLat };
  });
  return rings;
}

function resolve(tag: string, lon: number): string {
  if (tag !== "RU") return tag;
  return lon >= 60 ? "AS" : "EU";
}

function inside(ring: Ring, lon: number, lat: number): boolean {
  const p = ring.points;
  let hit = false;
  for (let i = 0, j = p.length - 2; i < p.length; j = i, i += 2) {
    const [xi, yi, xj, yj] = [p[i], p[i + 1], p[j], p[j + 1]];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

// The continent at a place, or null (open sea far from land).
export function continentAt(lat: number, lon: number): string | null {
  let nearest: { tag: string; km: number } | null = null;
  for (const ring of loaded()) {
    if (lon >= ring.minLon && lon <= ring.maxLon && lat >= ring.minLat && lat <= ring.maxLat && inside(ring, lon, lat)) {
      return resolve(ring.tag, lon);
    }
    for (let i = 0; i < ring.points.length; i += 2) {
      const dLat = ring.points[i + 1] - lat;
      const dLon = (ring.points[i] - lon) * Math.cos((lat * Math.PI) / 180);
      const km = Math.hypot(dLat, dLon) * 111.2;
      if (km <= NEAR_KM && (nearest === null || km < nearest.km)) nearest = { tag: ring.tag, km };
    }
  }
  return nearest === null ? null : resolve(nearest.tag, lon);
}

// The continent of a call's ITU state, or null.
export function continentOfCall(call: string): string | null {
  const country = countryOf(call);
  if (country === null) return null;
  if (country === "Russian Federation") {
    const digit = /\d/.exec(call)?.[0];
    return digit !== undefined && "890".includes(digit) ? "AS" : "EU";
  }
  return CONTINENT_OF_COUNTRY[country] ?? null;
}

// A spotter's continent: from the locator its spot carries (some cluster
// nodes add the spotter's), else from its call.
export function spotterContinent(spotter: string, locator: string | null): string | null {
  const at = locator === null ? null : locatorCentre(locator);
  const fromPlace = at === null ? null : continentAt(at.lat, at.lon);
  return fromPlace ?? continentOfCall(spotter.split("-")[0]);
}
