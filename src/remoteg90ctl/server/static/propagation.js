// SPDX-License-Identifier: GPL-3.0-or-later
//
// The Propagation window's arithmetic (owner's request 2026-11-27): where the
// Sun is overhead, and how high it stands anywhere, for the map of day, night
// and the grey line. Pure functions; app.ts draws them. Low-precision solar
// position (the usual almanac approximation, good to about a hundredth of a
// degree), more than the map needs.
const DEG = Math.PI / 180;
// The point on the Earth where the Sun is overhead at `ms` (Unix time).
export function subsolarPoint(ms) {
    const n = ms / 86400000 - 10957.5; // days since 2000-01-01 12:00 UTC
    const meanLongitude = 280.46 + 0.9856474 * n;
    const anomaly = (357.528 + 0.9856003 * n) * DEG;
    const lambda = (meanLongitude + 1.915 * Math.sin(anomaly) + 0.02 * Math.sin(2 * anomaly)) * DEG;
    const epsilon = (23.439 - 0.0000004 * n) * DEG;
    const declination = Math.asin(Math.sin(epsilon) * Math.sin(lambda));
    const rightAscension = Math.atan2(Math.cos(epsilon) * Math.sin(lambda), Math.cos(lambda)) / DEG; // degrees
    const gmst = 280.46061837 + 360.98564736629 * n; // degrees
    return { lat: declination / DEG, lon: normalizeLon(rightAscension - gmst) };
}
export function normalizeLon(lon) {
    return ((((lon + 180) % 360) + 360) % 360) - 180;
}
// The Sun's elevation above the horizon at (lat, lon), degrees.
export function sunElevation(lat, lon, sun) {
    const cosZenith = Math.sin(lat * DEG) * Math.sin(sun.lat * DEG) + Math.cos(lat * DEG) * Math.cos(sun.lat * DEG) * Math.cos((lon - sun.lon) * DEG);
    return Math.asin(Math.max(-1, Math.min(1, cosZenith))) / DEG;
}
// How dark the map is at an elevation: 0 by day, 1 by night, the grey line
// (civil twilight, the Sun from 0 to 6 degrees below the horizon) between.
export function darkness(elevation) {
    if (elevation >= 0)
        return 0;
    if (elevation <= -6)
        return 1;
    return -elevation / 6;
}
// The middle of a Maidenhead locator (4 or 6 characters), or null.
export function locatorCentre(locator) {
    const l = locator.trim().toUpperCase();
    if (!/^[A-R]{2}\d{2}([A-X]{2})?$/.test(l))
        return null;
    let lon = (l.charCodeAt(0) - 65) * 20 - 180 + Number(l[2]) * 2;
    let lat = (l.charCodeAt(1) - 65) * 10 - 90 + Number(l[3]);
    if (l.length === 6) {
        lon += ((l.charCodeAt(4) - 65) * 5) / 60 + 2.5 / 60;
        lat += ((l.charCodeAt(5) - 65) * 2.5) / 60 + 1.25 / 60;
    }
    else {
        lon += 1;
        lat += 0.5;
    }
    return { lat, lon };
}
// Results stay until the next scan, at most three hours (owner's decision
// 2026-11-27).
export const SCAN_KEPT_MS = 3 * 60 * 60 * 1000;
const GRID = /^[A-R]{2}\d{2}$/;
// The sender of an FT8 message and the locator it carries, if any: "CQ DX
// SP2EMW JO93" and "DL1XYZ SP2EMW JO93" give SP2EMW in JO93; reports and
// RR73 give no locator.
export function heardFrom(msg, from) {
    if (from === null)
        return null;
    const words = msg.trim().toUpperCase().split(/\s+/);
    const last = words[words.length - 1];
    const grid = words.length >= 3 && GRID.test(last) && last !== "RR73" ? last : null;
    return { call: from, grid };
}
// One station per call and band: the best SNR, the newest time, a locator once sent.
export function addHeard(stations, station) {
    const key = `${station.call} ${station.band}`;
    const old = stations.get(key);
    if (old === undefined) {
        stations.set(key, station);
        return;
    }
    old.snr = Math.max(old.snr, station.snr);
    old.at = Math.max(old.at, station.at);
    old.grid = station.grid ?? old.grid;
}
// Saved results that are still to be shown: not older than SCAN_KEPT_MS.
export function keptScan(text, now) {
    if (text === null)
        return null;
    try {
        const saved = JSON.parse(text);
        if (typeof saved.at !== "number" || !Array.isArray(saved.stations) || now - saved.at > SCAN_KEPT_MS)
            return null;
        return { at: saved.at, stations: saved.stations };
    }
    catch {
        return null;
    }
}
// How good a solar figure is for HF (owner's choice 2026-11-27): SFI 120 and
// more good, 80-119 fair, below 80 poor; A below 10 good, 10-29 fair, 30 and
// more poor; K 0-2 good, 3-4 fair, 5 and more poor.
export function solarGrade(name, value) {
    if (name === "SFI")
        return value >= 120 ? "good" : value >= 80 ? "fair" : "poor";
    if (name === "A")
        return value < 10 ? "good" : value < 30 ? "fair" : "poor";
    return value <= 2 ? "good" : value <= 4 ? "fair" : "poor";
}
