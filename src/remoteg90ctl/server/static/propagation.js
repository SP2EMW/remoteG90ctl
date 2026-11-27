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
