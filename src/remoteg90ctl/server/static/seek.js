// SPDX-License-Identifier: GPL-3.0-or-later
//
// Seek (owner's request 2026-11-13): the next station up or down, found in the
// waterfall's own spectrum (the radio's I/Q output) and tuned with the normal
// set_frequency. Pure functions; app.ts runs the search and the retuning. It
// never transmits: it only chooses a frequency to listen on.
// A station: at least MARGIN_DB over the band's noise (the median of the
// spectrum), at least MIN_WIDTH_HZ wide (single carriers and the computer's
// spurs are narrower); pieces closer than JOIN_HZ are one signal (speech has
// gaps in its spectrum). Owner's choice 2026-11-13: 10 dB and 200 Hz.
export const MARGIN_DB = 10;
export const MIN_WIDTH_HZ = 200;
export const JOIN_HZ = 150;
// Searched around the I/Q centre: the radio's I/Q output reaches about
// ±40 kHz (measured, AGENTS.md section 8 item 1); its edges are weaker.
export const USABLE_HZ = 38000;
// No station there: the radio moves this far and the search goes on.
export const STEP_HZ = 40000;
// The G90's receive range (its manual: 0.5-30 MHz).
export const LOWEST_HZ = 500000;
export const HIGHEST_HZ = 30000000;
// Where the signal is put in the passband: speech starts about this far from
// the suppressed carrier.
export const SPEECH_START_HZ = 300;
// The highest level of each bin over `lines` (newest first); only the lines of
// the newest line's I/Q centre and bin count are used.
export function maxHold(lines) {
    const first = lines[0];
    if (first === undefined || first.bins.length === 0)
        return null;
    const db = new Float32Array(first.bins.length).fill(-Infinity);
    for (const line of lines) {
        if (line.centerHz !== first.centerHz || line.bins.length !== db.length)
            break;
        const scale = (line.maxDb - line.minDb) / 255;
        for (let i = 0; i < db.length; i++) {
            const value = line.minDb + line.bins[i] * scale;
            if (value > db[i])
                db[i] = value;
        }
    }
    return { centerHz: first.centerHz, spanHz: first.spanHz, db };
}
function binHz(hold, i) {
    return hold.centerHz + ((i + 0.5) / hold.db.length - 0.5) * hold.spanHz;
}
// The signals in a hold, as [lowHz, highHz], from low to high.
export function findSignals(hold) {
    const n = hold.db.length;
    const usable = [];
    for (let i = 0; i < n; i++)
        if (Math.abs(binHz(hold, i) - hold.centerHz) <= USABLE_HZ)
            usable.push(i);
    if (usable.length === 0)
        return [];
    const levels = usable.map((i) => hold.db[i]).sort((a, b) => a - b);
    const threshold = levels[Math.floor(levels.length / 2)] + MARGIN_DB;
    const binWidth = hold.spanHz / n;
    const pieces = [];
    for (const i of usable) {
        if (hold.db[i] < threshold)
            continue;
        const low = binHz(hold, i) - binWidth / 2;
        const high = low + binWidth;
        const last = pieces[pieces.length - 1];
        if (last !== undefined && low - last[1] <= JOIN_HZ)
            last[1] = high;
        else
            pieces.push([low, high]);
    }
    return pieces.filter(([low, high]) => high - low >= MIN_WIDTH_HZ);
}
// The first signal wholly beyond `fromHz` in `direction`, or null.
export function nextSignal(signals, fromHz, direction) {
    if (direction > 0)
        return signals.find(([low]) => low > fromHz) ?? null;
    for (let i = signals.length - 1; i >= 0; i--)
        if (signals[i][1] < fromHz)
            return signals[i];
    return null;
}
// The dial frequency that puts `signal` into the receive passband of `mode`.
export function dialFor(signal, mode, stepHz) {
    const [low, high] = signal;
    let hz;
    if (mode === "USB" || mode === "U-D")
        hz = low - SPEECH_START_HZ;
    else if (mode === "LSB" || mode === "L-D")
        hz = high + SPEECH_START_HZ;
    else
        hz = (low + high) / 2; // CW, AM, FM: the middle of the signal
    return Math.round(hz / stepHz) * stepHz;
}
// Where the search continues after an empty view, or null past the radio's range.
export function nextView(dialHz, direction) {
    const hz = dialHz + direction * STEP_HZ;
    return hz < LOWEST_HZ || hz > HIGHEST_HZ ? null : hz;
}
