// SPDX-License-Identifier: GPL-3.0-or-later
//
// Reading CW in the browser (decisions.md D62): a skimmer. Pure code, no
// browser APIs: the page feeds it the received audio before any processing
// (as FT8, D52). It finds CW tones in the passband and decodes up to
// MAX_CHANNELS of them at once.
//
// Our own code. Ideas, no code, taken from fldigi (W1HKJ and others, GPL-3.0):
// an envelope per signal, a threshold between the signal and the noise that
// follow each by itself, the speed tracked from the lengths of the elements.
// See docs/third-party.md.

import { fft } from "./rxdsp.js";

export const MAX_CHANNELS = 8;
export const TONE_MIN_HZ = 250;
export const TONE_MAX_HZ = 2900;

const WORK_RATE = 8000; // the audio is brought down to this rate first
const ENVELOPE_RATE = 1000; // the envelope is followed at 1 ms steps
const WARM_UP_MS = 200; // a new channel first learns the noise
const MIN_DOT_MS = 20; // 60 WPM: shorter marks are not dots
// Morse code; a prosign shown as the characters it is usually written as.
const MORSE: Record<string, string> = {
  ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G",
  "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N",
  "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U",
  "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y", "--..": "Z",
  "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
  ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
  ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/", "-...-": "=",
  "-....-": "-", ".-.-.": "+", "-.--.": "(", "-.--.-": ")", ".----.": "'",
  "---...": ":", "...-.-": "<SK>", ".-...": "<AS>", "-.-.-": "<KA>",
};

export function morseChar(code: string): string {
  return MORSE[code] ?? "*";
}

// The speed in words per minute of a dot of `dotMs` (PARIS: 50 dots a word).
export function wpm(dotMs: number): number {
  return 1200 / dotMs;
}

// One signal: its envelope, the threshold, the timing and the text.
export class CwChannel {
  text = ""; // decoded so far (the page keeps what it shows)
  dotMs = 60; // 20 WPM until the signal shows its own speed
  lastMarkAt = 0; // ms of the end of the last mark (for "active")
  private phase = 0;
  private readonly length: number; // moving average of the mixer, samples
  private readonly re: Float64Array;
  private readonly im: Float64Array;
  private at = 0;
  private sumRe = 0;
  private sumIm = 0;
  private count = 0; // samples to the next envelope step
  private peak = 0;
  private noise = 0;
  private key = false;
  private since = 0; // ms in the current state
  private now = 0; // ms since the start
  // The marks of the word being received and the gap after each, in ms. A
  // word is decoded when it ends: then its own marks tell the speed, so the
  // first word at a new speed is read right too.
  private marks: number[] = [];
  private gaps: number[] = [];
  private cut = false; // decoded at a gap that may still turn out a word gap

  constructor(public toneHz: number) {
    // About 10 ms: a filter some 100 Hz wide, short against a dot at 40 WPM.
    this.length = Math.round(WORK_RATE / 100);
    this.re = new Float64Array(this.length);
    this.im = new Float64Array(this.length);
  }

  get wpm(): number {
    return wpm(this.dotMs);
  }

  // Samples at WORK_RATE; the characters decoded from them.
  push(samples: Float32Array): string {
    let out = "";
    const step = (2 * Math.PI * this.toneHz) / WORK_RATE;
    for (const x of samples) {
      this.phase = (this.phase + step) % (2 * Math.PI);
      const re = x * Math.cos(this.phase);
      const im = -x * Math.sin(this.phase);
      this.sumRe += re - this.re[this.at];
      this.sumIm += im - this.im[this.at];
      this.re[this.at] = re;
      this.im[this.at] = im;
      this.at = (this.at + 1) % this.length;
      if (++this.count < WORK_RATE / ENVELOPE_RATE) continue;
      this.count = 0;
      out += this.envelope(Math.hypot(this.sumRe, this.sumIm) / this.length);
    }
    this.text += out;
    return out;
  }

  // One envelope step (1 ms): the key state, and the text it ends.
  private envelope(level: number): string {
    this.now += 1;
    if (this.now <= WARM_UP_MS) {
      // The first moments set the noise level; nothing is keyed yet.
      this.noise += (level - this.noise) / this.now;
      this.peak = this.noise;
      return "";
    }
    // The signal's level rises fast and falls slowly; the noise's the other
    // way round, so each follows its own and fading moves both.
    this.peak += (level - this.peak) * (level > this.peak ? 0.1 : 0.0005);
    this.noise += (level - this.noise) * (level < this.noise ? 0.1 : 0.0005);
    const span = this.peak - this.noise;
    const audible = this.peak > 3 * this.noise; // about 10 dB: something to read
    const threshold = this.noise + span * (this.key ? 0.4 : 0.6); // hysteresis
    const down = audible && level > threshold;
    this.since += 1;
    if (down !== this.key) {
      this.cut = false; // the gap ended within the word
      if (this.key) {
        if (this.since >= 8) {
          this.marks.push(this.since); // shorter: a click, not an element
          this.lastMarkAt = this.now;
        }
      } else if (this.marks.length > this.gaps.length) {
        this.gaps.push(this.since);
      }
      this.key = down;
      this.since = 0;
      return "";
    }
    // A long silence ends the word: longer than a word gap at the speed
    // known so far, and in any case after 1.5 s.
    if (!this.key && this.cut && this.since > 5 * this.dotMs) {
      this.cut = false;
      return " ";
    }
    if (!this.key && this.marks.length > 0) {
      if (this.since > Math.min(1500, 7 * this.dotMs)) return this.word(true);
      // A long run of marks without a word gap at the speed known so far is
      // decoded at a character gap, without ending the word.
      if (this.marks.length >= 60 && this.since > 3 * this.dotMs) return this.word(false);
    }
    return "";
  }

  // The word received (or, not `ended`, a long part of one): the speed from
  // its marks, then its characters and spaces from the marks and the gaps.
  private word(ended: boolean): string {
    const heard = this.marks;
    const heardGaps = this.gaps;
    this.marks = [];
    this.gaps = [];
    this.dotMs = speedFrom(heard.filter((m) => m >= MIN_DOT_MS), this.dotMs);
    const dot = this.dotMs;
    // Cut at a gap that the speed now known shows to be a word gap.
    const wordEnds = ended || this.since > 5 * dot;
    // Marks much shorter than a dot are noise: dropped, the gaps around them
    // joined into one.
    const marks: number[] = [];
    const gaps: number[] = [];
    heard.forEach((mark, i) => {
      const gap = heardGaps[i] ?? Infinity;
      if (mark < 0.4 * dot) {
        if (gaps.length > 0) gaps[gaps.length - 1] += mark + gap;
        return;
      }
      marks.push(mark);
      gaps.push(gap);
    });
    let out = "";
    let code = "";
    marks.forEach((mark, i) => {
      code += mark > 2 * dot ? "-" : ".";
      const gap = gaps[i]; // after the last mark: Infinity, the word's end
      if (gap > 2 * dot) {
        out += code.length > 7 ? "*" : morseChar(code);
        code = "";
        if (gap > 5 * dot && i < marks.length - 1) out += " ";
      }
    });
    this.cut = !wordEnds;
    return wordEnds ? `${out} ` : out;
  }
}

// The dot length from the marks of a word: if they fall into two groups (a
// gap of more than twice between neighbours when sorted), the short ones are
// dots and the long ones dashes; if they are all alike, they are dots or
// dashes by the speed known before. Smoothed with the speed known before.
export function speedFrom(marks: readonly number[], dotMs: number): number {
  if (marks.length === 0) return dotMs;
  const sorted = [...marks].sort((a, b) => a - b);
  let split = -1;
  let ratio = 2;
  for (let i = 1; i < sorted.length; i++) {
    const r = sorted[i] / sorted[i - 1];
    if (r > ratio) {
      ratio = r;
      split = i;
    }
  }
  let estimate: number;
  if (split > 0) {
    const dots = sorted.slice(0, split);
    const dashes = sorted.slice(split);
    estimate = (dots.reduce((a, b) => a + b, 0) + dashes.reduce((a, b) => a + b, 0) / 3) / sorted.length;
  } else {
    const mean = sorted.reduce((a, b) => a + b, 0) / sorted.length;
    estimate = mean > 2 * dotMs ? mean / 3 : mean;
  }
  const weight = split > 0 ? 0.7 : 0.3; // two groups say more than one
  return Math.min(200, Math.max(20, dotMs * (1 - weight) + estimate * weight));
}

// A callsign in text: a prefix with a digit and a suffix of letters.
const CALL = /\b(?:[A-Z]{1,2}\d?|\d[A-Z])\d[A-Z]{1,4}(?:\/[A-Z0-9]{1,4})?\b/g;

export function lastCall(text: string): string | null {
  const calls = text.match(CALL);
  return calls === null ? null : calls[calls.length - 1];
}

export interface Skimmed {
  id: number;
  toneHz: number;
  wpm: number;
  text: string;
  call: string | null;
  active: boolean; // keyed in the last few seconds
}

const FFT_SIZE = 2048; // at WORK_RATE: about 4 Hz a bin, 0.26 s
const SCAN_EVERY = 4000; // samples at WORK_RATE between looks for signals (0.5 s)
const FIND_DB = 10; // a tone this far above the noise floor is a signal
const SEPARATION_HZ = 60; // two channels at least this far apart
const FOLLOW_HZ = 25; // a channel moves to its peak if it is this close
const IDLE_DROP_MS = 30000; // a channel silent this long is given up
const TEXT_KEPT = 200;

// Finds the signals and decodes each in its own channel.
export class CwSkimmer {
  onUpdate: (channels: Skimmed[]) => void = () => {};
  private readonly decimation: number;
  private gathered = 0;
  private gatheredCount = 0;
  private readonly ring = new Float32Array(FFT_SIZE);
  private ringAt = 0;
  private sinceScan = 0;
  private power: Float64Array | null = null; // averaged spectrum, by bin
  private channels: { id: number; channel: CwChannel; startedAt: number }[] = [];
  private nextId = 1;
  private nowMs = 0;
  private readonly batch: number[] = [];

  constructor(readonly sampleRate = 48000) {
    this.decimation = Math.max(1, Math.round(sampleRate / WORK_RATE));
  }

  reset(): void {
    this.channels = [];
    this.power = null;
    this.ring.fill(0);
  }

  push(samples: Float32Array): void {
    for (const x of samples) {
      this.gathered += x;
      if (++this.gatheredCount < this.decimation) continue;
      const y = this.gathered / this.decimation;
      this.gathered = 0;
      this.gatheredCount = 0;
      this.batch.push(y);
      this.ring[this.ringAt] = y;
      this.ringAt = (this.ringAt + 1) % FFT_SIZE;
      if (++this.sinceScan >= SCAN_EVERY) {
        this.sinceScan = 0;
        this.feed();
        this.scan();
      }
    }
  }

  private feed(): void {
    const block = Float32Array.from(this.batch);
    this.batch.length = 0;
    for (const { channel } of this.channels) channel.push(block);
    this.nowMs += (block.length / WORK_RATE) * 1000;
    this.onUpdate(this.list());
  }

  list(): Skimmed[] {
    return this.channels
      .map(({ id, channel, startedAt }) => ({
        id,
        toneHz: channel.toneHz,
        wpm: Math.round(channel.wpm),
        text: channel.text.slice(-TEXT_KEPT),
        call: lastCall(channel.text),
        active: channel.lastMarkAt > 0 && this.silentMs(channel, startedAt) < 5000,
      }))
      .sort((a, b) => a.toneHz - b.toneHz);
  }

  // Since the channel's last mark, or since it was opened without one; the
  // channel counts its own time from when it was opened.
  private silentMs(channel: CwChannel, startedAt: number): number {
    return this.nowMs - (startedAt + channel.lastMarkAt);
  }

  // The spectrum of the last FFT_SIZE samples, averaged; peaks above the
  // noise floor become channels, and channels follow their peak.
  private scan(): void {
    const re = new Float32Array(FFT_SIZE);
    const im = new Float32Array(FFT_SIZE);
    for (let i = 0; i < FFT_SIZE; i++) {
      const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / FFT_SIZE);
      re[i] = this.ring[(this.ringAt + i) % FFT_SIZE] * w;
    }
    fft(re, im);
    const bins = FFT_SIZE / 2;
    if (this.power === null) this.power = new Float64Array(bins);
    for (let k = 0; k < bins; k++) {
      // Max-hold that decays: CW keys on and off, a plain average would hide it.
      const p = re[k] * re[k] + im[k] * im[k];
      this.power[k] = Math.max(p, this.power[k] * 0.7);
    }
    const hz = (k: number): number => (k * WORK_RATE) / FFT_SIZE;
    const lowBin = Math.ceil((TONE_MIN_HZ * FFT_SIZE) / WORK_RATE);
    const highBin = Math.floor((TONE_MAX_HZ * FFT_SIZE) / WORK_RATE);
    const inBand = Array.from(this.power.slice(lowBin, highBin + 1)).sort((a, b) => a - b);
    const floor = inBand[Math.floor(inBand.length / 2)] || 1e-12;
    const peaks: { hz: number; power: number }[] = [];
    for (let k = lowBin + 1; k < highBin; k++) {
      const p = this.power[k];
      if (p > this.power[k - 1] && p >= this.power[k + 1] && 10 * Math.log10(p / floor) > FIND_DB) {
        peaks.push({ hz: hz(k), power: p });
      }
    }
    peaks.sort((a, b) => b.power - a.power);
    // Channels follow a close peak (the other station's drift or our retune).
    for (const { channel } of this.channels) {
      const near = peaks.find((p) => Math.abs(p.hz - channel.toneHz) <= FOLLOW_HZ);
      if (near !== undefined) channel.toneHz = near.hz;
    }
    // Silent channels are dropped; the strongest new peaks get one.
    this.channels = this.channels.filter(({ channel, startedAt }) => this.silentMs(channel, startedAt) < IDLE_DROP_MS);
    for (const peak of peaks) {
      if (this.channels.length >= MAX_CHANNELS) break;
      if (this.channels.some(({ channel }) => Math.abs(channel.toneHz - peak.hz) < SEPARATION_HZ)) continue;
      this.channels.push({ id: this.nextId++, channel: new CwChannel(peak.hz), startedAt: this.nowMs });
    }
  }
}
