// SPDX-License-Identifier: GPL-3.0-or-later
//
// FT8/FT4 reception in the browser (stage 6, decisions.md D52): the receive
// audio, before any of the page's processing, is cut into the modes' cycles by
// the server's clock (clock frames 0x04, docs/server-protocol.md) and decoded by
// ft8ts in a Web Worker (ft8-worker.ts). Pure logic here, tested in node.

export type DigitalMode = "FT8" | "FT4";

// Cycle length, ms.
export const CYCLE_MS: Record<DigitalMode, number> = { FT8: 15000, FT4: 7500 };

// Dial frequencies (Hz) in USB data mode, as in WSJT-X, for the G90's bands.
export const DIAL_HZ: Record<DigitalMode, Record<string, number>> = {
  FT8: {
    "160m": 1_840_000,
    "80m": 3_573_000,
    "60m": 5_357_000,
    "40m": 7_074_000,
    "30m": 10_136_000,
    "20m": 14_074_000,
    "17m": 18_100_000,
    "15m": 21_074_000,
    "12m": 24_915_000,
    "10m": 28_074_000,
  },
  FT4: {
    "80m": 3_575_000,
    "40m": 7_047_500,
    "30m": 10_140_000,
    "20m": 14_080_000,
    "17m": 18_104_000,
    "15m": 21_140_000,
    "12m": 24_919_000,
    "10m": 28_180_000,
  },
};

// The part of the spectrum above the dial (U-D) the waterfall zooms to when a
// mode is switched on: the audio band the decoder listens to, 100-3000 Hz,
// with the dial's own frequency at the left edge (owner's request 2026-11-10).
const VIEW_ABOVE_DIAL_HZ: Record<DigitalMode, readonly [number, number]> = {
  FT8: [0, 3200],
  FT4: [0, 3200],
};

export function viewRange(mode: DigitalMode, dialHz: number): [number, number] {
  const [low, high] = VIEW_ABOVE_DIAL_HZ[mode];
  return [dialHz + low, dialHz + high];
}

// Zoom and pan (the page's view: the visible part is 1/zoom of the span, the pan
// is the middle of the view as a fraction of the span from the line's centre)
// that show [lowHz, highHz] of a line with the given span and centre.
export function fitRange(
  spanHz: number,
  centerHz: number,
  lowHz: number,
  highHz: number,
  maxZoom: number,
  minZoom: number,
): { zoom: number; pan: number } {
  const zoom = Math.min(maxZoom, Math.max(minZoom, spanHz / (highHz - lowHz)));
  const limit = 0.5 - 0.5 / zoom; // as the page keeps the view inside the band
  const pan = Math.min(limit, Math.max(-limit, ((lowHz + highHz) / 2 - centerHz) / spanHz));
  return { zoom, pan };
}

// The waterfall's centre is the VFO plus the mode's small offset (tens of Hz,
// AGENTS.md section 8 item 2a): a line is "on the dial" within this much, and a
// zoom is fitted only to such a line, not to one still from the old frequency.
const AT_DIAL_TOLERANCE_HZ = 500;

export function atDial(centerHz: number, dialHz: number): boolean {
  return Math.abs(centerHz - dialHz) <= AT_DIAL_TOLERANCE_HZ;
}

export const FRAME_CLOCK = 0x04;
export const CLOCK_BYTES = 16;
const CLOCK_SYNCHRONIZED = 0x01;
const CLOCK_UNKNOWN = 0x02;

export interface ClockFrame {
  seq: number;
  utcMs: number; // first sample of audio frame `seq`
  synchronized: boolean | null;
}

export function parseClock(buffer: ArrayBuffer): ClockFrame | null {
  if (buffer.byteLength < CLOCK_BYTES) return null;
  const view = new DataView(buffer);
  if (view.getUint8(0) !== FRAME_CLOCK) return null;
  const flags = view.getUint8(1);
  return {
    seq: view.getUint32(4, true),
    utcMs: view.getFloat64(8, true) * 1000,
    synchronized: flags & CLOCK_UNKNOWN ? null : (flags & CLOCK_SYNCHRONIZED) !== 0,
  };
}

export interface Cycle {
  mode: DigitalMode;
  startMs: number; // UTC
  samples: Float32Array;
  coverage: number; // share of the cycle's samples that arrived, 0..1
}

// A cycle with less audio than this is not decoded (joined late, long gap).
export const MIN_COVERAGE = 0.8;
const FRAME_MS = 20;
// Audio range the decoder searches (Hz above the dial in U-D), shown on the
// waterfall as the passband while FT8/FT4 runs.
export const DECODE_BAND_HZ: [number, number] = [100, 3000];

// Audio is dated from the latest clock frame (one a second). A frame dated
// further from it than this is not from the stream the clock belongs to.
// The real cause of decoding stopping after the audio was subscribed anew
// (11.11, 14.11) was the frame numbering in RxAudio: Chrome's Opus decoder
// takes only the first chunk's timestamp and counts on by itself, so the old
// numbering stayed for good; RxAudio now keeps the server's numbers
// (FrameNumbers) and resets the decoder for each subscription. This limit
// stays as a safeguard.
export const MAX_FROM_CLOCK_MS = 5000;

// A cycle goes to the decoder this far in, as soon as the signals are over
// (sent 0.5 s in: FT8 lasts 12.64 s, FT4 5.04 s), so that an answer can go out
// in the very next cycle, as in WSJT-X. Later audio of the cycle is not used.
export const DECODE_AT_MS: Record<DigitalMode, number> = { FT8: 13600, FT4: 6000 };

// Collects audio frames (sequence number + samples) into cycles for the decoder.
export class CycleCollector {
  private anchor: ClockFrame | null = null;
  private start: number | null = null; // UTC ms of the cycle being filled
  private buffer: Float32Array;
  // The cycle's length in samples, kept apart: a buffer handed to the worker by
  // transfer is emptied (length 0), so its own length cannot size the next one.
  private readonly cycleSamples: number;
  private filled = 0;
  private sent = false; // this cycle went to the decoder
  private readonly decodeAt: number; // samples
  onCycle: (cycle: Cycle) => void = () => {};

  constructor(
    readonly mode: DigitalMode,
    readonly sampleRate = 48000,
  ) {
    this.cycleSamples = (CYCLE_MS[mode] * sampleRate) / 1000;
    this.buffer = new Float32Array(this.cycleSamples);
    this.decodeAt = (DECODE_AT_MS[mode] * sampleRate) / 1000;
  }

  setClock(clock: ClockFrame): void {
    this.anchor = clock;
  }

  // Forget the cycle being filled (after retuning: it holds two frequencies).
  reset(): void {
    this.start = null;
  }

  push(seq: number, samples: Float32Array): void {
    const anchor = this.anchor;
    if (anchor === null) return; // no time yet
    const perMs = this.sampleRate / 1000;
    const cycleMs = CYCLE_MS[this.mode];
    let timeMs = anchor.utcMs + (seq - anchor.seq) * FRAME_MS;
    if (Math.abs(timeMs - anchor.utcMs) > MAX_FROM_CLOCK_MS) return; // an earlier stream
    let from = 0;
    while (from < samples.length) {
      const cycleStart = Math.floor(timeMs / cycleMs) * cycleMs;
      if (this.start !== null && cycleStart < this.start) return; // late audio of a finished cycle
      if (cycleStart !== this.start) this.begin(cycleStart);
      const offset = Math.min(this.cycleSamples - 1, Math.max(0, Math.round((timeMs - cycleStart) * perMs)));
      const count = Math.min(samples.length - from, this.cycleSamples - offset);
      if (!this.sent && offset < this.decodeAt) {
        const used = Math.min(count, this.decodeAt - offset);
        this.buffer.set(samples.subarray(from, from + used), offset);
        this.filled += used;
        if (offset + used >= this.decodeAt) this.emit();
      }
      from += count;
      timeMs += count / perMs;
    }
  }

  private emit(): void {
    this.sent = true;
    const coverage = Math.min(1, this.filled / this.decodeAt);
    if (coverage < MIN_COVERAGE || this.start === null) return;
    this.onCycle({ mode: this.mode, startMs: this.start, samples: this.buffer, coverage });
    this.buffer = new Float32Array(this.cycleSamples); // the old one goes to the worker
  }

  private begin(cycleStart: number): void {
    this.buffer.fill(0);
    this.start = cycleStart;
    this.filled = 0;
    this.sent = false;
  }
}

export interface Decode {
  utc: string; // HHMMSS of the cycle start
  snr: number;
  dt: number;
  freq: number; // audio Hz
  msg: string;
  ap: number; // 0: a normal decode
}

export function cycleLabel(startMs: number): string {
  const d = new Date(startMs);
  return [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()].map((v) => String(v).padStart(2, "0")).join("");
}

// The sender of a decoded message, for the waterfall label: the second word of
// "CALL1 CALL2 ..." or the call after CQ (and its optional modifier).
export function sender(msg: string): string | null {
  const words = msg.trim().split(/\s+/);
  if (words.length === 5 && words[1] === "RR73;") return clean(words[3]); // a Fox's double message
  if (words[0] === "CQ") {
    const rest = words.slice(1);
    if (rest.length >= 2 && /^([A-Z]{2,4}|\d{3})$/.test(rest[0])) return clean(rest[1]);
    return rest.length ? clean(rest[0]) : null;
  }
  return words.length >= 2 ? clean(words[1]) : null;
}

// The sender's grid in a message, as [call, grid]: "CQ [DX] CALL GRID" or
// "TO FROM GRID"; null without one ("RR73" looks like a grid and is not one).
export function gridOf(msg: string): [string, string] | null {
  const words = msg.trim().split(/\s+/);
  const last = words[words.length - 1];
  if (words.length < 3 || last === "RR73" || !GRID.test(last)) return null;
  const call = sender(msg);
  return call === null ? null : [call, last];
}

function clean(call: string): string | null {
  const c = call.replace(/^<|>$/g, "");
  return /^[A-Z0-9/]{3,}$/.test(c) && /\d/.test(c) ? c : null;
}

// How a message relates to this station: addressed to it, a CQ, or other.
export function kind(msg: string, mycall: string): "mine" | "cq" | "other" {
  const words = msg.trim().split(/\s+/);
  if (mycall !== "" && words.some((w) => w.replace(/^<|>$/g, "") === mycall)) return "mine";
  return words[0] === "CQ" ? "cq" : "other";
}

// ---- Transmission (stage 7, D53): the QSO sequence of WSJT-X ----
//
// Tx1 "DX MY GRID", Tx2 "DX MY -12", Tx3 "DX MY R-12", Tx4 "DX MY RR73",
// Tx5 "DX MY 73", Tx6 "CQ MY GRID". The page decides what to send; the
// server checks and transmits it (ft8_transmit), within the page's lease.

export type TxStep = 1 | 2 | 3 | 4 | 5 | 6;

// A clicked answer from the partner continues the QSO when it was last heard
// at most this many cycles before (a minute of FT8, half a minute of FT4).
const QSO_KEPT_CYCLES = 4;

// A callsign that fits the 28 bits of a standard FT8/FT4 message (WSJT-X's
// stdcall): a prefix of one or two characters with a letter, the call area
// digit, a suffix of one to three letters, and /P or /R. Others (SX200H,
// HF10SQ8W, F4ELU/QRP) are sent as their 22-bit hash, "<SX200H>".
export function standardCall(call: string): boolean {
  const base = call.replace(/\/[PR]$/, "");
  return /^(?:[A-Z]|[A-Z0-9][A-Z]|[A-Z][0-9])[0-9][A-Z]{1,3}$/.test(base);
}

const REPORT = /^[+-]\d{2}$/;
const R_REPORT = /^R[+-]\d{2}$/;
const GRID = /^[A-R]{2}\d{2}$/;

// A signal report as sent: sign and two digits, -30..+49 (WSJT-X).
export function report(snr: number): string {
  const value = Math.max(-30, Math.min(49, Math.round(snr)));
  return `${value < 0 ? "-" : "+"}${String(Math.abs(value)).padStart(2, "0")}`;
}

export function cycleIndex(startMs: number, mode: DigitalMode): number {
  return Math.floor(startMs / CYCLE_MS[mode]);
}

// Depth for the cycle right before one of our transmissions (owner's choice
// 2026-11-11): depth 3 takes about 1.8 s, so its decodes came after our next
// message had gone out and every answer was a cycle late. Depth 2 is done in
// time; the other cycles keep the chosen depth.
export const QUICK_DEPTH = 2;

// `txParity`: the cycles we transmit in (0 even, 1 odd), or null when we do not.
export function cycleDepth(chosen: number, cycleStartMs: number, mode: DigitalMode, txParity: number | null): number {
  if (txParity === null) return chosen;
  const next = cycleIndex(cycleStartMs, mode) + 1;
  return next % 2 === txParity ? Math.min(chosen, QUICK_DEPTH) : chosen;
}

// Which cycle a UTC time is in: "1st" or "2nd" (what the TX choice means: the
// even or odd cycles, FT8 00/30 s or 15/45 s), and its place in the minute, 1..4
// for FT8 and 1..8 for FT4.
export function cycleNumber(
  utcMs: number,
  mode: DigitalMode,
): { slot: "1st" | "2nd"; position: number; perMinute: number } {
  const perMinute = 60_000 / CYCLE_MS[mode];
  const index = cycleIndex(utcMs, mode);
  return { slot: index % 2 === 0 ? "1st" : "2nd", position: (index % perMinute) + 1, perMinute };
}

export interface Heard {
  msg: string;
  snr: number;
  startMs: number; // cycle it was heard in
}

// What one received message means for a QSO with `mycall`.
interface Parsed {
  to: string;
  from: string;
  grid: string | null;
  report: string | null; // their report of us, "-12"
  roger: boolean; // R-12, RR73, RRR
  final: boolean; // RR73, RRR or 73
  bye: boolean; // 73
}

function parse(msg: string, mycall: string): Parsed | null {
  const w = msg.trim().split(/\s+/).map((x) => x.replace(/^<|>$/g, ""));
  if (w.length < 2 || w[0] === "CQ") return null;
  // A DXpedition's (Fox's) double message, "CALL1 RR73; CALL2 <FOX> -12": RR73
  // to CALL1 and a report to CALL2, both from FOX (owner's report 2026-11-15:
  // our R+04 went on after "SP2EMW RR73; F4NPE <YO2LIM> -22").
  if (w.length === 5 && w[1] === "RR73;") {
    const from = w[3];
    if (clean(from) === null) return null;
    const p: Parsed = { to: w[0], from, grid: null, report: null, roger: true, final: true, bye: false };
    if (w[2] === mycall && REPORT.test(w[4])) return { ...p, to: w[2], report: w[4], roger: false, final: false };
    return p;
  }
  const last = w.length >= 3 ? w[2] : "";
  // Free text ("SP2EMW TNX 73") and unknown hashes ("<...>") have no sender.
  if (clean(w[1]) === null) return null;
  const p: Parsed = { to: w[0], from: w[1], grid: null, report: null, roger: false, final: false, bye: false };
  if (last === "RR73" || last === "RRR") {
    p.roger = p.final = true;
  } else if (last === "73") {
    p.final = p.bye = true;
  } else if (R_REPORT.test(last)) {
    p.roger = true;
    p.report = last.slice(1);
  } else if (REPORT.test(last)) {
    p.report = last;
  } else if (GRID.test(last)) {
    p.grid = last;
  }
  return p;
}

export interface FinishedQso {
  call: string;
  grid: string;
  sent: string; // our report of them
  rcvd: string; // their report of us
}

export class Sequencer {
  step: TxStep | null = null;
  dx = "";
  dxGrid = "";
  sent = ""; // report we send
  rcvd = ""; // report we got
  // The cycle the partner was last heard in (or answered from).
  private lastMs = -Infinity;
  // Cycle parity we transmit in: 0 the even cycles (00, 30 s for FT8), 1 the odd.
  parity = 0;
  onFinished: (qso: FinishedQso) => void = () => {};

  constructor(
    readonly mycall: string,
    readonly grid: string, // four characters
    readonly mode: DigitalMode,
  ) {}

  message(): string | null {
    // A call that does not fit a standard message goes as its hash, "<SX200H>"
    // (WSJT-X; its owner knows it, and the server refused the full call that
    // the encoder would shorten). One call of the two may be hashed.
    const dx = standardCall(this.dx) ? this.dx : `<${this.dx}>`;
    const my = standardCall(this.mycall) || !standardCall(this.dx) ? this.mycall : `<${this.mycall}>`;
    const grid = this.grid;
    switch (this.step) {
      case 1:
        return `${dx} ${my} ${grid}`;
      case 2:
        return `${dx} ${my} ${this.sent}`;
      case 3:
        return `${dx} ${my} R${this.sent}`;
      case 4:
        return `${dx} ${my} RR73`;
      case 5:
        return `${dx} ${my} 73`;
      case 6:
        // A nonstandard call goes in full, without the grid (a type 4 message).
        return standardCall(this.mycall) ? `CQ ${my} ${grid}` : `CQ ${this.mycall}`;
      default:
        return null;
    }
  }

  callCq(parity: number): void {
    this.reset();
    this.step = 6;
    this.parity = parity;
  }

  stop(): void {
    this.step = null;
  }

  private reset(): void {
    this.dx = this.dxGrid = this.sent = this.rcvd = "";
    this.lastMs = -Infinity;
  }

  // The operator picked a decoded message: answer its sender, in the other cycles.
  answer(heard: Heard): boolean {
    const words = heard.msg.trim().split(/\s+/);
    const parity = (cycleIndex(heard.startMs, this.mode) + 1) % 2;
    if (words[0] === "CQ") {
      const call = sender(heard.msg);
      if (call === null || call === this.mycall) return false;
      this.reset();
      this.dx = call;
      const grid = words[words.length - 1];
      this.dxGrid = GRID.test(grid) && grid !== "RR73" ? grid : "";
      this.sent = report(heard.snr);
      this.step = 1;
      this.parity = parity;
      this.lastMs = heard.startMs;
      return true;
    }
    const p = parse(heard.msg, this.mycall);
    if (p === null || p.from === this.mycall) return false;
    if (p.to !== this.mycall) {
      // Someone else's QSO: call its sender (its grid, if it sent one now).
      this.reset();
      this.dx = p.from;
      if (p.grid !== null && p.grid !== "RR73") this.dxGrid = p.grid;
      this.sent = report(heard.snr);
      this.step = 1;
      this.parity = parity;
      this.lastMs = heard.startMs;
      return true;
    }
    // Addressed to us. From the station of the QSO under way (the operator
    // clicked its answer to push the sequence on, also after a halt): what was
    // sent and heard so far stays, as the log needs it (owner's report
    // 2026-11-15: such QSOs were logged without the report received or grid).
    // A QSO left for longer starts afresh: its reports are out of date.
    const recent = heard.startMs - this.lastMs <= QSO_KEPT_CYCLES * CYCLE_MS[this.mode];
    if (p.from !== this.dx || !recent) {
      this.reset();
      this.dx = p.from;
      this.sent = report(heard.snr);
    } else if (this.sent === "") {
      this.sent = report(heard.snr);
    }
    this.parity = parity;
    this.lastMs = heard.startMs;
    this.advance(p);
    return this.step !== null;
  }

  // Decodes of a cycle we listened in: move on if the station answered.
  heard(decodes: Heard[]): void {
    if (this.step === null) return;
    for (const d of decodes) {
      const p = parse(d.msg, this.mycall);
      if (p === null || p.to !== this.mycall) continue;
      if (this.step === 6 && this.dx === "") {
        // Someone answers our CQ (grid or straight a report).
        if (p.grid === null && p.report === null) continue;
        this.dx = p.from;
        this.sent = report(d.snr);
        this.parity = (cycleIndex(d.startMs, this.mode) + 1) % 2;
      }
      if (p.from !== this.dx) continue;
      if (this.sent === "") this.sent = report(d.snr);
      this.lastMs = d.startMs;
      this.advance(p);
      return;
    }
  }

  private advance(p: Parsed): void {
    if (p.grid !== null) this.dxGrid = p.grid;
    if (p.report !== null) this.rcvd = p.report;
    if (p.bye) {
      // 73 from them: done; answer 73 unless we have said goodbye already.
      if (this.step === 4 || this.step === 5) this.finish();
      else this.step = 5;
    } else if (p.final) {
      this.step = 5; // RR73/RRR: our 73 ends it
    } else if (p.roger) {
      this.step = 4; // R-12: our RR73 ends it
    } else if (p.report !== null) {
      this.step = 3;
    } else if (p.grid !== null) {
      this.step = 2;
    }
  }

  // After each of our transmissions, with the message that went out: RR73 and
  // 73 end the QSO (WSJT-X logs then). The step may have moved on meanwhile
  // (the partner's R-12 decoded after our previous message had already gone
  // out): only a sent RR73 or 73 finishes it, not the one still to be sent.
  transmitted(sent: string): void {
    if ((this.step === 4 || this.step === 5) && sent === this.message()) this.finish();
  }

  private finish(): void {
    if (this.dx !== "") this.onFinished({ call: this.dx, grid: this.dxGrid, sent: this.sent, rcvd: this.rcvd });
    this.step = null;
    this.reset(); // a later QSO with the same station starts afresh
  }
}
