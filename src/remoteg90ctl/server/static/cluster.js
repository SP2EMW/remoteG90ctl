// SPDX-License-Identifier: GPL-3.0-or-later
//
// DX cluster spots on the page (decisions.md D63): which band and mode a spot
// is in, and where the radio goes when it is clicked. Pure functions; app.ts
// wires them to the window and the waterfall. A spot has no mode of its own:
// it is taken from a word in its comment (as web clusters do), else from the
// FT8/FT4 calling frequencies, else from the IARU band plan segment it is in.
export const SPOT_MODES = ["CW", "SSB", "FT8", "FT4", "RTTY", "SSTV", "DIGI"];
// The bands a spot may be in (the G90's, as wide as any ITU region has them), kHz.
export const SPOT_BANDS = [
    ["160m", 1800, 2000],
    ["80m", 3500, 4000],
    ["60m", 5250, 5450],
    ["40m", 7000, 7300],
    ["30m", 10100, 10150],
    ["20m", 14000, 14350],
    ["17m", 18068, 18168],
    ["15m", 21000, 21450],
    ["12m", 24890, 24990],
    ["10m", 28000, 29700],
];
export function spotBand(khz) {
    return SPOT_BANDS.find(([, low, high]) => khz >= low && khz <= high)?.[0] ?? null;
}
// Words in a comment and the mode they name, first match wins.
const MODE_WORDS = [
    [/\bFT8\b/, "FT8"],
    [/\bFT4\b/, "FT4"],
    [/\bRTTY\b/, "RTTY"],
    [/\bSSTV\b/, "SSTV"],
    [/\b(PSK\d*|JT65|JT9|JS8|OLIVIA|MFSK\d*|FT2|Q65|MSK144|WSPR|VARA|PACKET|DIGI)\b/, "DIGI"],
    [/\b(CW|WPM)\b/, "CW"],
    [/\b(SSB|USB|LSB|PHONE)\b/, "SSB"],
];
// FT8/FT4 signals lie this far above their calling frequency (the audio passband).
const DIAL_SPAN_KHZ = 3;
// The calling frequency a spot's FT8/FT4 signal belongs to, or null.
function dialOf(khz, dials) {
    const hz = khz * 1000;
    return dials.find((dial) => hz >= dial && hz < dial + DIAL_SPAN_KHZ * 1000) ?? null;
}
export function spotMode(spot, segments, dials) {
    const comment = spot.comment.toUpperCase();
    for (const [word, mode] of MODE_WORDS)
        if (word.test(comment))
            return mode;
    if (dialOf(spot.freq_khz, dials.FT8) !== null)
        return "FT8";
    if (dialOf(spot.freq_khz, dials.FT4) !== null)
        return "FT4";
    const hz = spot.freq_khz * 1000;
    const segment = segments?.find((s) => s.low <= hz && hz < s.high);
    if (segment?.kind === "cw" || segment?.kind === "beacon")
        return "CW";
    if (segment?.kind === "narrow")
        return "DIGI";
    return "SSB";
}
// Where the radio goes for a spot (owner's decisions 2026-11-26). The DE-19
// carries the radio's audio only in U-D and L-D, so every spot tunes one of
// them: CW and SSB in L-D below 10 MHz and U-D above, CW with its tone at
// CW_TONE_HZ; FT8/FT4 on their calling frequency in U-D; RTTY in L-D with the
// mark tone on the spot; SSTV in U-D on the spot; other digital modes in U-D
// with the signal at 1.5 kHz.
export const DIGI_AUDIO_HZ = 1500;
export const CW_TONE_HZ = 700;
const DATA_LSB_BELOW_HZ = 10000000;
export function tuneFor(spot, mode, dials, rttyMarkHz) {
    const hz = Math.round(spot.freq_khz * 1000);
    const lower = hz < DATA_LSB_BELOW_HZ;
    switch (mode) {
        case "CW":
            // In L-D the tone is the dial minus the signal; in U-D the other way.
            return lower ? { hz: hz + CW_TONE_HZ, mode: "L-D" } : { hz: hz - CW_TONE_HZ, mode: "U-D" };
        case "SSB":
            return { hz, mode: lower ? "L-D" : "U-D" };
        case "FT8":
        case "FT4": {
            // The band's calling frequency, wherever in the band the spot is
            // (owner's request 2026-11-26); the spot itself only without one.
            const band = spotBand(spot.freq_khz);
            const dial = dialOf(spot.freq_khz, dials[mode]) ?? dials[mode].find((d) => spotBand(d / 1000) === band);
            return { hz: dial ?? hz, mode: "U-D" };
        }
        case "RTTY":
            return { hz: hz + rttyMarkHz, mode: "L-D" };
        case "SSTV":
            // Spotted on its dial frequency, as voice: 1.5 kHz lower would put its
            // tones (1.2-2.3 kHz) out of the passband.
            return { hz, mode: "U-D" };
        case "DIGI":
            return { hz: hz - DIGI_AUDIO_HZ, mode: "U-D" };
    }
}
// The spots for the waterfall: those of the last `ageMs`, newest first, one
// per station and band.
export function recentSpots(spots, nowMs, ageMs) {
    const seen = new Set();
    const out = [];
    for (let i = spots.length - 1; i >= 0; i--) {
        const spot = spots[i];
        if (nowMs - spot.received_ms > ageMs)
            break;
        const key = `${spot.dx} ${spotBand(spot.freq_khz)}`;
        if (seen.has(key))
            continue;
        seen.add(key);
        out.push(spot);
    }
    return out;
}
