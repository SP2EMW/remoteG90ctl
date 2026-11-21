// SPDX-License-Identifier: GPL-3.0-or-later
//
// Transmit audio processing in the browser (stage 8, decisions.md D39, D49).
// The filters, the graphic equaliser and the compressor are Web Audio nodes;
// the noise gate is here. None of it is a safety measure: the server's limiter
// and "audio to the radio only during a lease" stay (AGENTS.md, stage 8).

// Noise gate: a peak envelope (fast attack, slow release) opens the gate above
// the threshold; it stays open for HOLD after the level falls, then closes
// smoothly down to RANGE. No look-ahead, so no added delay.
const ATTACK_S = 0.002;
const RELEASE_S = 0.15;
const HOLD_S = 0.1;
const CLOSE_S = 0.05;
const RANGE = 0.03; // -30 dB when closed

export class NoiseGate {
  private envelope = 0;
  private gain = 1;
  private holdLeft = 0;
  private readonly attack: number;
  private readonly release: number;
  private readonly close: number;
  private readonly hold: number;
  threshold = 0.01; // linear (-40 dBFS)
  enabled = false;

  constructor(sampleRate: number) {
    this.attack = 1 - Math.exp(-1 / (ATTACK_S * sampleRate));
    this.release = 1 - Math.exp(-1 / (RELEASE_S * sampleRate));
    this.close = 1 - Math.exp(-1 / (CLOSE_S * sampleRate));
    this.hold = Math.round(HOLD_S * sampleRate);
  }

  setThresholdDb(db: number): void {
    this.threshold = 10 ** (db / 20);
  }

  process(samples: Float32Array): void {
    if (!this.enabled) return;
    for (let i = 0; i < samples.length; i++) {
      const level = Math.abs(samples[i]);
      this.envelope += (level - this.envelope) * (level > this.envelope ? this.attack : this.release);
      let target: number;
      if (this.envelope >= this.threshold) {
        this.holdLeft = this.hold;
        target = 1;
      } else if (this.holdLeft > 0) {
        this.holdLeft--;
        target = 1;
      } else {
        target = RANGE;
      }
      // Open at once (no clipped syllable starts), close smoothly.
      this.gain = target > this.gain ? target : this.gain + (target - this.gain) * this.close;
      samples[i] *= this.gain;
    }
  }
}

// Graphic equaliser bands (owner's decision D49): octaves for SSB speech.
export const EQ_BANDS = [100, 200, 400, 800, 1600, 3200];
export const EQ_Q = 1.41; // one octave wide

export interface TxDspSettings {
  lowCut: number; // Hz; 0 = off
  highCut: number; // Hz; 0 = off
  eq: number[]; // dB per band of EQ_BANDS, -12..+12
  gate: boolean;
  gateDb: number; // threshold, dBFS
  compressor: boolean;
  thresholdDb: number; // compressor threshold, dBFS
  ratio: number;
  makeupDb: number; // gain after the compressor
  monitor: boolean; // hear the processed signal in this browser
}

export const TX_DSP_DEFAULTS: TxDspSettings = {
  lowCut: 100,
  highCut: 2900,
  eq: EQ_BANDS.map(() => 0),
  gate: false,
  gateDb: -45,
  compressor: false,
  thresholdDb: -24,
  ratio: 4,
  makeupDb: 6,
  monitor: false,
};

// The browser's compressor looks 6 ms ahead (Web Audio specification).
export const COMPRESSOR_LATENCY_MS = 6;

export function txDspLatencyMs(settings: TxDspSettings): number {
  return settings.compressor ? COMPRESSOR_LATENCY_MS : 0;
}

// ---- Presets of the transmit processing (owner's request 2026-11-10) ----
//
// Starting points, set by ear-less reasoning and still to be tuned on the air:
// the G90's own SSB filter limits what a microphone chain can
// do at both ends, and the server's limiter (audio.tx_peak_dbfs) still caps
// the peaks. The microphone gain slider is not part of a preset: it depends on
// the microphone. Set it so the meter stays green/yellow and the radio's ALC
// barely moves.

export type CustomSlot = "custom1" | "custom2";
export type TxPresetName = "dx" | "local" | CustomSlot;
// What the operator saved in each of the two own slots (null: still empty).
export type TxCustoms = Record<CustomSlot, TxPresetValues | null>;
export type TxPresetValues = Omit<TxDspSettings, "monitor">;

export const TX_PRESETS: Record<"dx" | "local", TxPresetValues> = {
  // The most range: all the power in the part of the speech that carries
  // intelligibility. Passband 300-2700 Hz (the radio's own SSB filter is about
  // that wide, so nothing is wasted outside it), lows cut hard (mud, and they
  // carry power but no words), 0.8-2 kHz lifted, the gate shuts the room
  // between words and a heavy compressor (6:1) with 10 dB of make-up raises the
  // average level under the limiter's ceiling.
  dx: {
    lowCut: 300,
    highCut: 2700,
    eq: [-12, -9, -3, 2, 5, 3],
    gate: true,
    gateDb: -50,
    compressor: true,
    thresholdDb: -30,
    ratio: 6,
    makeupDb: 10,
  },
  // A deep "broadcast" voice for local contacts: the whole passband (60 Hz to
  // 3.2 kHz), the low end lifted (proximity warmth, 100-200 Hz), the boxy
  // 400 Hz taken back a little, some air at 3 kHz, no gate (breaths and room
  // stay natural) and a gentle announcer's compressor (4:1) that evens the
  // level. How deep it gets depends on the radio's low-frequency cut-off.
  local: {
    lowCut: 60,
    highCut: 3200,
    eq: [8, 5, -1, 0, 2, 4],
    gate: false,
    gateDb: -50,
    compressor: true,
    thresholdDb: -26,
    ratio: 4,
    makeupDb: 8,
  },
};

// What a Custom slot holds until the operator saves something: the neutral chain.
export const TX_CUSTOM_EMPTY: TxPresetValues = (() => {
  const { monitor: _monitor, ...values } = TX_DSP_DEFAULTS;
  return { ...values, eq: [...values.eq] };
})();

function sameValues(a: TxPresetValues, b: TxPresetValues): boolean {
  return (
    a.lowCut === b.lowCut &&
    a.highCut === b.highCut &&
    a.gate === b.gate &&
    a.gateDb === b.gateDb &&
    a.compressor === b.compressor &&
    a.thresholdDb === b.thresholdDb &&
    a.ratio === b.ratio &&
    a.makeupDb === b.makeupDb &&
    a.eq.length === b.eq.length &&
    a.eq.every((v, i) => v === b.eq[i])
  );
}

// The values of a preset; a Custom slot is what the operator saved, else the neutral chain.
export function presetValues(name: TxPresetName, customs: TxCustoms): TxPresetValues {
  const values = name === "dx" || name === "local" ? TX_PRESETS[name] : (customs[name] ?? TX_CUSTOM_EMPTY);
  return { ...values, eq: [...values.eq] };
}

// The settings with a preset's values; the monitor switch stays as it is.
export function withPreset(
  settings: TxDspSettings,
  name: TxPresetName,
  customs: TxCustoms,
): TxDspSettings {
  return { ...presetValues(name, customs), monitor: settings.monitor };
}

// Which preset the settings are exactly, or null when edited since.
export function matchingPreset(settings: TxDspSettings, customs: TxCustoms): TxPresetName | null {
  for (const name of ["dx", "local", "custom1", "custom2"] as const) {
    if (sameValues(settings, presetValues(name, customs))) return name;
  }
  return null;
}

// What the preset list shows: the preset the operator chose or saved into, while
// the settings are still its values; "edited" once they differ. Without a
// remembered choice (an older page) the first preset with these values.
export function presetShown(
  settings: TxDspSettings,
  chosen: TxPresetName | null,
  customs: TxCustoms,
): TxPresetName | "edited" {
  if (chosen === null) return matchingPreset(settings, customs) ?? "edited";
  return sameValues(settings, presetValues(chosen, customs)) ? chosen : "edited";
}

export function isPresetName(value: unknown): value is TxPresetName {
  return value === "dx" || value === "local" || value === "custom1" || value === "custom2";
}

// Settings to remember in a Custom slot: the values without the monitor switch.
export function toCustom(settings: TxDspSettings): TxPresetValues {
  const { monitor: _monitor, ...values } = settings;
  return { ...values, eq: [...values.eq] };
}

// A saved Custom slot read back from its stored text: null for anything unusable.
export function parseCustom(text: string | null): TxPresetValues | null {
  if (text === null) return null;
  try {
    const v = JSON.parse(text) as Partial<TxPresetValues>;
    const num = (x: unknown, low: number, high: number): x is number =>
      typeof x === "number" && Number.isFinite(x) && x >= low && x <= high;
    if (
      !num(v.lowCut, 0, 500) ||
      !num(v.highCut, 0, 4000) ||
      !num(v.gateDb, -80, -20) ||
      !num(v.thresholdDb, -50, 0) ||
      !num(v.ratio, 1, 20) ||
      !num(v.makeupDb, 0, 20) ||
      typeof v.gate !== "boolean" ||
      typeof v.compressor !== "boolean" ||
      !Array.isArray(v.eq) ||
      v.eq.length !== EQ_BANDS.length ||
      !v.eq.every((g) => num(g, -12, 12))
    ) {
      return null;
    }
    return { ...v, eq: [...v.eq] } as TxPresetValues;
  } catch {
    return null;
  }
}
