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
        const stations = saved.stations;
        // Results saved before the bands were: the bands that had a station.
        const bands = Array.isArray(saved.bands)
            ? saved.bands.filter((b) => typeof b === "string")
            : [...new Set(stations.map((st) => st.band))];
        return { at: saved.at, bands, stations };
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
// Spots or stations on a band (owner's thresholds 2026-11-27): 10 and more
// good, 3-9 fair, 0-2 poor.
export function countGrade(count) {
    return count >= 10 ? "good" : count >= 3 ? "fair" : "poor";
}
// The middle of each band, MHz, for the estimate.
export const BAND_MHZ = {
    "160m": 1.85, "80m": 3.6, "60m": 5.36, "40m": 7.1, "30m": 10.12,
    "20m": 14.15, "17m": 18.1, "15m": 21.2, "12m": 24.9, "10m": 28.5,
};
const F2_LAG_MS = 60 * 60 * 1000;
const HOP_MIDPOINT_KM = 1500;
// How much of the day's F2 ionisation there is at a point: it follows the Sun
// with about an hour's lag and changes over the Sun's climb from 12 degrees
// below the horizon to 10 above (a winter noon at 50-55 N counts as full day),
// so it decays over hours after sunset instead of at once.
export function f2Daylight(lat, lon, ms) {
    const elevation = sunElevation(lat, lon, subsolarPoint(ms - F2_LAG_MS));
    const x = Math.min(1, Math.max(0, (elevation + 12) / 22));
    return x * x * (3 - 2 * x); // smooth at both ends
}
// The point `km` away from (lat, lon) along `bearing` degrees (great circle).
export function destination(lat, lon, bearing, km) {
    const d = km / 6371;
    const p1 = lat * DEG;
    const b = bearing * DEG;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
    const l2 = lon * DEG + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return { lat: p2 / DEG, lon: normalizeLon(l2 / DEG) };
}
export function daylight(home, ms) {
    let path = 0;
    for (let bearing = 0; bearing < 360; bearing += 45) {
        const p = destination(home.lat, home.lon, bearing, HOP_MIDPOINT_KM);
        path = Math.max(path, f2Daylight(p.lat, p.lon, ms));
    }
    const sunLat = subsolarPoint(ms).lat;
    const winter = Math.max(-1, Math.min(1, (-sunLat / 23.44) * (home.lat < 0 ? -1 : 1)));
    // The D layer, which absorbs the low bands by day, goes within minutes after
    // sunset: no lag, unlike the F2 layer.
    const sunUp = sunElevation(home.lat, home.lon, subsolarPoint(ms)) > 0;
    return { path, station: f2Daylight(home.lat, home.lon, ms), sunUp, winter };
}
// How much a disturbed field lowers the MUF: K is the last three hours, A the
// day; the ionosphere recovers more slowly than K after a storm (owner's
// request 2026-11-28: use the new figures, as close to reality as we can).
function stormFactor(k, a) {
    const byK = k !== null && k >= 5 ? 0.85 : k === 4 ? 0.92 : 1;
    const byA = a !== null && a >= 30 ? 0.85 : a !== null && a >= 20 ? 0.92 : 1;
    return Math.min(byK, byA);
}
export function estimateMuf(sfi, k, light, a = null) {
    const s = Math.min(250, Math.max(60, sfi));
    const g = (Math.sqrt(s) - Math.sqrt(70)) / (Math.sqrt(200) - Math.sqrt(70));
    const day = (17 + 20 * g) * (1 + 0.15 * light.winter);
    const night = (9 + 7 * g) * (1 - 0.25 * light.winter);
    return (night + (day - night) * light.path) * stormFactor(k, a);
}
const LOWER = { good: "fair", fair: "poor", poor: "poor" };
// The short path. Over the MUF poor, within its top fifth fair, below good.
// By day at the station 160 and 80 m are poor and 60-40 m at most fair (D-layer
// absorption). A flare (SA act or eru) on a daylit path lowers the bands below
// 15 MHz a step, a major one (maj) the bands below 21 MHz, and below 10 MHz to
// poor: sudden fade-outs hit the lower frequencies hardest.
export function estimateDetail(mhz, sfi, light, f) {
    const reasons = [];
    const muf = estimateMuf(sfi, f.k, light, f.a ?? null);
    if (stormFactor(f.k, f.a ?? null) < 1)
        reasons.push(`disturbed field (K ${f.k ?? "?"}, A ${f.a ?? "?"})`);
    let grade = mhz > muf ? "poor" : mhz > 0.8 * muf ? "fair" : "good";
    if (light.sunUp && mhz < 4)
        grade = "poor";
    else if (light.sunUp && mhz < 8 && grade === "good")
        grade = "fair";
    const daylit = light.path >= 0.5;
    if (daylit && f.sa === "maj" && mhz < 21) {
        grade = mhz < 10 ? "poor" : LOWER[grade];
        reasons.push("major solar flare: fade-outs on the daylit side");
    }
    else if (daylit && (f.sa === "act" || f.sa === "eru") && mhz < 15) {
        grade = LOWER[grade];
        reasons.push(`solar activity (${f.sa}): fade-outs on the daylit side`);
    }
    return { grade, reasons };
}
export function estimateGrade(mhz, sfi, k, light) {
    return estimateDetail(mhz, sfi, light, { k }).grade;
}
// The long path (owner's choice 2026-11-28): a longer way with more hops, so a
// step below the short path; with aurora (Au=yes) the polar part absorbs, the
// lower frequencies most: below 14 MHz poor, above another step down.
export function longPathDetail(shortPath, mhz, au) {
    const reasons = [...shortPath.reasons, "longer path, more hops"];
    let grade = LOWER[shortPath.grade];
    if (au === true) {
        grade = mhz < 14 ? "poor" : LOWER[grade];
        reasons.push("aurora: absorption on the polar part");
    }
    return { grade, reasons };
}
// Up, down or level against the report before.
export function trend(now, before) {
    if (now === null || before === null)
        return "";
    return now > before ? "↑" : now < before ? "↓" : "→";
}
// An expected K this high, and above the K now, warns of a storm.
export const STORM_EXP_K = 4;
const ACTIVE_SUN = new Set(["act", "eru", "maj"]);
export function solarWarnings(r) {
    const out = [];
    if (r.exp_k != null && r.k !== null && r.exp_k > r.k && r.exp_k >= STORM_EXP_K) {
        out.push(`Geomagnetic storm likely: expected K ${r.exp_k} (now ${r.k})`);
    }
    if (r.sa != null && ACTIVE_SUN.has(r.sa)) {
        out.push(`Solar activity: ${r.sa} — sudden fade-outs possible on the daylit side`);
    }
    if (r.au === true)
        out.push("Aurora: paths near the poles (e.g. to North America, Japan) may be weak");
    return out;
}
// The K the band estimate uses: the expected one when a storm is likely.
export function estimateK(r) {
    if (r.exp_k != null && r.k !== null && r.exp_k > r.k && r.exp_k >= STORM_EXP_K)
        return r.exp_k;
    return r.k;
}
