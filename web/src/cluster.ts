// SPDX-License-Identifier: GPL-3.0-or-later
//
// DX cluster spots on the page (decisions.md D63): which band and mode a spot
// is in, and where the radio goes when it is clicked. Pure functions; app.ts
// wires them to the window and the waterfall. A spot has no mode of its own:
// it is taken from a word in its comment (as web clusters do), else from the
// FT8/FT4 calling frequencies, else from the IARU band plan segment it is in.

export interface Spot {
  spotter: string;
  dx: string;
  freq_khz: number;
  comment: string;
  utc: string; // "HHMM"
  locator: string | null;
  received_ms: number;
}

export const SPOT_MODES = ["CW", "SSB", "FT8", "FT4", "RTTY", "SSTV", "DIGI"] as const;
export type SpotMode = (typeof SPOT_MODES)[number];

// The bands a spot may be in (the G90's, as wide as any ITU region has them), kHz.
export const SPOT_BANDS: readonly (readonly [string, number, number])[] = [
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

export function spotBand(khz: number): string | null {
  return SPOT_BANDS.find(([, low, high]) => khz >= low && khz <= high)?.[0] ?? null;
}

// Words in a comment and the mode they name, first match wins.
const MODE_WORDS: readonly (readonly [RegExp, SpotMode])[] = [
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

export interface Segment {
  low: number; // Hz
  high: number;
  kind: string; // cw, narrow, all, beacon, ...
}

export interface Dials {
  FT8: readonly number[]; // Hz
  FT4: readonly number[];
}

// The calling frequency a spot's FT8/FT4 signal belongs to, or null.
function dialOf(khz: number, dials: readonly number[]): number | null {
  const hz = khz * 1000;
  return dials.find((dial) => hz >= dial && hz < dial + DIAL_SPAN_KHZ * 1000) ?? null;
}

export function spotMode(spot: Spot, segments: readonly Segment[] | null, dials: Dials): SpotMode {
  const comment = spot.comment.toUpperCase();
  for (const [word, mode] of MODE_WORDS) if (word.test(comment)) return mode;
  if (dialOf(spot.freq_khz, dials.FT8) !== null) return "FT8";
  if (dialOf(spot.freq_khz, dials.FT4) !== null) return "FT4";
  const hz = spot.freq_khz * 1000;
  const segment = segments?.find((s) => s.low <= hz && hz < s.high);
  if (segment?.kind === "cw" || segment?.kind === "beacon") return "CW";
  if (segment?.kind === "narrow") return "DIGI";
  return "SSB";
}

// Where the radio goes for a spot (owner's decision 2026-11-26): CW on the
// spot; SSB on the spot in the voice mode of its band; FT8/FT4 on their
// calling frequency in U-D; RTTY in L-D with the mark tone on the spot; other
// digital modes in U-D with the signal at 1.5 kHz, SSTV in U-D on the spot.
export const DIGI_AUDIO_HZ = 1500;

export function tuneFor(
  spot: Spot,
  mode: SpotMode,
  dials: Dials,
  rttyMarkHz: number,
  voiceMode: (hz: number) => string,
): { hz: number; mode: string } {
  const hz = Math.round(spot.freq_khz * 1000);
  switch (mode) {
    case "CW":
      return { hz, mode: "CW" };
    case "SSB":
      return { hz, mode: voiceMode(hz) };
    case "FT8":
    case "FT4":
      return { hz: dialOf(spot.freq_khz, dials[mode]) ?? hz, mode: "U-D" };
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
export function recentSpots(spots: readonly Spot[], nowMs: number, ageMs: number): Spot[] {
  const seen = new Set<string>();
  const out: Spot[] = [];
  for (let i = spots.length - 1; i >= 0; i--) {
    const spot = spots[i];
    if (nowMs - spot.received_ms > ageMs) break;
    const key = `${spot.dx} ${spotBand(spot.freq_khz)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(spot);
  }
  return out;
}
