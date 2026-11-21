// SPDX-License-Identifier: GPL-3.0-or-later
//
// Tests of FT8/FT4 reception (stage 6, D52) on the compiled JavaScript: cycles
// cut from 20 ms audio frames by the server's clock frames, and ft8ts decoding
// what comes out.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CLOCK_BYTES,
  CYCLE_MS,
  CycleCollector,
  FRAME_CLOCK,
  MIN_COVERAGE,
  Sequencer,
  standardCall,
  cycleLabel,
  kind,
  parseClock,
  report,
  sender,
} from "../../src/remoteg90ctl/server/static/ft8.js";
import { decodeFT4, decodeFT8, encodeFT4, encodeFT8 } from "../../src/remoteg90ctl/server/static/vendor/ft8ts/ft8ts.mjs";

const RATE = 48000;

// As the server: a clock frame before every 50th audio frame (once a second).
function pushWithClock(collector, seq, utcS, samples) {
  if (seq % 50 === 0) collector.setClock(parseClock(clockFrame(seq, utcS + seq * 0.02)));
  collector.push(seq, samples);
}
const FRAME = 960;
const T0 = Date.UTC(2026, 10, 9, 12, 0, 0); // a cycle boundary for both modes

function clockFrame(seq, utcS, flags = 1) {
  const buffer = new ArrayBuffer(CLOCK_BYTES);
  const view = new DataView(buffer);
  view.setUint8(0, FRAME_CLOCK);
  view.setUint8(1, flags);
  view.setUint32(4, seq, true);
  view.setFloat64(8, utcS, true);
  return buffer;
}

// Audio of `seconds` starting at `startMs`, sent as numbered 20 ms frames with
// a clock frame every 50, through a collector; returns the cycles it gave.
function collect(mode, audio, startMs, { skip = () => false, clockErrorMs = 0 } = {}) {
  const collector = new CycleCollector(mode, RATE);
  const cycles = [];
  collector.onCycle = (cycle) => cycles.push({ ...cycle, samples: cycle.samples.slice() });
  for (let seq = 0; seq * FRAME < audio.length; seq++) {
    const utcMs = startMs + seq * 20;
    if (seq % 50 === 0) collector.setClock(parseClock(clockFrame(seq, (utcMs + clockErrorMs) / 1000)));
    if (skip(seq)) continue;
    collector.push(seq, audio.subarray(seq * FRAME, (seq + 1) * FRAME));
  }
  return cycles;
}

test("clock frames are read", () => {
  assert.deepEqual(parseClock(clockFrame(7, 1.5, 1)), { seq: 7, utcMs: 1500, synchronized: true });
  assert.equal(parseClock(clockFrame(7, 1.5, 0)).synchronized, false);
  assert.equal(parseClock(clockFrame(7, 1.5, 2)).synchronized, null);
  assert.equal(parseClock(new ArrayBuffer(8)), null);
});

test("audio is cut at the cycle boundaries of UTC", () => {
  // Starts 5 s into a cycle: that cycle is partial and not decoded.
  const audio = new Float32Array(RATE * 41).map((_, i) => i);
  const cycles = collect("FT8", audio, T0 + 5000);
  assert.deepEqual(
    cycles.map((c) => c.startMs),
    [T0 + 15000, T0 + 30000],
  );
  const first = cycles[0];
  assert.equal(first.samples.length, (CYCLE_MS.FT8 * RATE) / 1000);
  // The sample at the start of the cycle is the one 10 s after the audio began.
  assert.equal(first.samples[0], 10 * RATE);
  assert.equal(first.coverage, 1);
});

test("gaps leave silence and too little audio is not decoded", () => {
  const audio = new Float32Array(RATE * 31).fill(1);
  // Frames lost: 1 s in the first cycle, 4 s in the second.
  const skip = (seq) => (seq >= 100 && seq < 150) || (seq >= 850 && seq < 1050);
  const cycles = collect("FT8", audio, T0, { skip });
  assert.equal(cycles.length, 1);
  assert.ok(cycles[0].coverage > MIN_COVERAGE && cycles[0].coverage < 1);
  assert.equal(cycles[0].samples[2 * RATE + 10], 0);
});

test("FT4 cycles are 7.5 s", () => {
  const cycles = collect("FT4", new Float32Array(RATE * 16), T0);
  assert.deepEqual(
    cycles.map((c) => c.startMs),
    [T0, T0 + 7500],
  );
});

test("senders and how a message relates to this station", () => {
  assert.equal(sender("CQ SP1ABC JO82"), "SP1ABC");
  assert.equal(sender("CQ DX DL8YHR JO41"), "DL8YHR");
  assert.equal(sender("SP1ABC DL1XYZ -12"), "DL1XYZ");
  assert.equal(sender("<PJ4/K1ABC> W9XYZ RR73"), "W9XYZ");
  assert.equal(sender("TNX BOB 73 GL"), null);
  assert.equal(kind("SP1ABC DL1XYZ -12", "SP1ABC"), "mine");
  assert.equal(kind("CQ DL1XYZ JO62", "SP1ABC"), "cq");
  assert.equal(kind("F5AAA DL1XYZ R-07", "SP1ABC"), "other");
  assert.equal(cycleLabel(T0 + 15000), "120015");
});

// Gaussian noise from a small deterministic generator.
function noise(n, sigma, seed = 1) {
  const out = new Float32Array(n);
  let s = seed;
  const uniform = () => {
    s = (s * 1103515245 + 12345) % 2147483648;
    return (s + 1) / 2147483649;
  };
  for (let i = 0; i < n; i++) out[i] = sigma * Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
  return out;
}

// 12 kHz to 48 kHz by linear interpolation (good enough below 3 kHz).
function upsample(x) {
  const out = new Float32Array(x.length * 4);
  for (let i = 0; i < out.length; i++) {
    const k = Math.floor(i / 4);
    const f = (i % 4) / 4;
    out[i] = x[k] * (1 - f) + (x[k + 1] ?? 0) * f;
  }
  return out;
}

for (const [mode, encode, decode, tx] of [
  ["FT8", encodeFT8, decodeFT8, "CQ SP1ABC JO82"],
  ["FT4", encodeFT4, decodeFT4, "DL1XYZ SP1ABC R-07"],
]) {
  test(`${mode} sent 0.5 s into a cycle is decoded from 20 ms frames`, () => {
    const cycleS = CYCLE_MS[mode] / 1000;
    // The audio starts 1 s before the cycle at T0 and ends 1 s after it.
    const audio = noise(Math.round(RATE * (cycleS + 2)), 0.05);
    const wave = upsample(encode(tx, { baseFrequency: 1200 })); // ft8ts encodes at 12 kHz
    const at = Math.round(RATE * 1.5); // 0.5 s into the cycle, as stations send
    for (let i = 0; i < wave.length; i++) audio[at + i] += 0.1 * wave[i];
    // The server's time is 40 ms ahead: the signal looks 40 ms late.
    const cycles = collect(mode, audio, T0 - 1000, { clockErrorMs: 40 });
    assert.deepEqual(
      cycles.map((c) => c.startMs),
      [T0],
    );
    const decodes = decode(cycles[0].samples, { sampleRate: RATE, depth: 2 });
    const found = decodes.find((d) => d.msg === tx);
    assert.ok(found, JSON.stringify(decodes));
    assert.ok(Math.abs(found.freq - 1200) < 5, `freq ${found.freq}`);
    assert.ok(Math.abs(found.dt - 0.04) < 0.1, `dt ${found.dt}`);
  });
}

// ---- The QSO sequence (stage 7) ----

const EVEN = T0; // cycle index even
const ODD = T0 + 15000;

function qso() {
  const seq = new Sequencer("SP1ABC", "JO82", "FT8");
  const finished = [];
  seq.onFinished = (q) => finished.push(q);
  return { seq, finished };
}

test("reports look like WSJT-X's", () => {
  assert.equal(report(-7.4), "-07");
  assert.equal(report(3), "+03");
  assert.equal(report(-40), "-30");
});

test("answering a CQ runs to RR73 and the QSO is finished", () => {
  const { seq, finished } = qso();
  assert.ok(seq.answer({ msg: "CQ DL1XYZ JO62", snr: -12, startMs: EVEN }));
  assert.equal(seq.parity, 1); // the other cycles
  assert.equal(seq.message(), "DL1XYZ SP1ABC JO82");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC DL1XYZ -05", snr: -11, startMs: EVEN + 30000 }]);
  assert.equal(seq.message(), "DL1XYZ SP1ABC R-12");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC DL1XYZ RR73", snr: -10, startMs: EVEN + 60000 }]);
  assert.equal(seq.message(), "DL1XYZ SP1ABC 73");
  seq.transmitted(seq.message());
  assert.equal(seq.step, null);
  assert.deepEqual(finished, [{ call: "DL1XYZ", grid: "JO62", sent: "-12", rcvd: "-05" }]);
});

test("clicking the partner's answer keeps the QSO's reports and grid", () => {
  const { seq, finished } = qso();
  assert.ok(seq.answer({ msg: "CQ IW4DNI JN44", snr: 0, startMs: EVEN }));
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC IW4DNI -07", snr: 2, startMs: EVEN + 30000 }]);
  assert.equal(seq.message(), "IW4DNI SP1ABC R+00");
  // The operator clicks the partner's RR73 instead of waiting.
  assert.ok(seq.answer({ msg: "SP1ABC IW4DNI RR73", snr: 5, startMs: EVEN + 60000 }));
  assert.equal(seq.message(), "IW4DNI SP1ABC 73");
  seq.transmitted(seq.message());
  assert.deepEqual(finished, [{ call: "IW4DNI", grid: "JN44", sent: "+00", rcvd: "-07" }]);
  // A later QSO with the same station starts afresh.
  assert.ok(seq.answer({ msg: "SP1ABC IW4DNI RR73", snr: 5, startMs: EVEN }));
  assert.equal(seq.rcvd, "");
  assert.equal(seq.dxGrid, "");
  // After a halt the QSO goes on when clicked soon; much later it starts afresh.
  assert.ok(seq.answer({ msg: "CQ IW4DNI JN44", snr: 0, startMs: EVEN }));
  seq.heard([{ msg: "SP1ABC IW4DNI -07", snr: 2, startMs: EVEN + 30000 }]);
  seq.stop();
  assert.ok(seq.answer({ msg: "SP1ABC IW4DNI RR73", snr: -3, startMs: EVEN + 60000 }));
  assert.equal(seq.rcvd, "-07");
  assert.equal(seq.sent, "+00");
  seq.stop();
  assert.ok(seq.answer({ msg: "SP1ABC IW4DNI RR73", snr: -3, startMs: EVEN + 600000 }));
  assert.equal(seq.rcvd, "");
  assert.equal(seq.sent, "-03");
});

test("a DXpedition's double message ends our QSO or starts the next one", () => {
  const { seq, finished } = qso();
  assert.ok(seq.answer({ msg: "CQ YO2LIM KN05", snr: -3, startMs: EVEN }));
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC YO2LIM -10", snr: 4, startMs: EVEN + 30000 }]);
  assert.equal(seq.message(), "YO2LIM SP1ABC R-03");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC RR73; F4NPE <YO2LIM> -22", snr: 3, startMs: EVEN + 60000 }]);
  assert.equal(seq.message(), "YO2LIM SP1ABC 73");
  seq.transmitted(seq.message());
  assert.deepEqual(finished, [{ call: "YO2LIM", grid: "KN05", sent: "-03", rcvd: "-10" }]);
  // The report part is for the next station: we are F4NPE here.
  const next = new Sequencer("F4NPE", "JN18", "FT8");
  assert.ok(next.answer({ msg: "CQ YO2LIM KN05", snr: -3, startMs: EVEN }));
  next.heard([{ msg: "SP1ABC RR73; F4NPE <YO2LIM> -22", snr: 3, startMs: EVEN + 30000 }]);
  assert.equal(next.message(), "YO2LIM F4NPE R-03");
  assert.equal(sender("SP1ABC RR73; F4NPE <YO2LIM> -22"), "YO2LIM");
});

test("free text addressed to us is not answered", () => {
  const { seq } = qso();
  assert.equal(seq.answer({ msg: "SP1ABC TNX 73", snr: 0, startMs: EVEN }), false);
  assert.equal(seq.answer({ msg: "SP1ABC <...> RR73", snr: 0, startMs: EVEN }), false);
});

test("standard calls go in full, the others as their hash", () => {
  for (const call of ["SP2EMW", "K1ABC", "2E0ABC", "E73M", "A71AB", "SP2EMW/P", "DL1XYZ/R"]) {
    assert.ok(standardCall(call), call);
  }
  for (const call of ["SX200H", "HF10SQ8W", "F4ELU/QRP", "PJ4/K1ABC", "9A60CBM"]) {
    assert.ok(!standardCall(call), call);
  }
});

test("a nonstandard call is answered with its hash, as WSJT-X does", () => {
  const { seq, finished } = qso();
  assert.ok(seq.answer({ msg: "CQ SX200H", snr: -9, startMs: EVEN }));
  assert.equal(seq.message(), "<SX200H> SP1ABC JO82");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC <SX200H> -05", snr: -8, startMs: EVEN + 30000 }]);
  assert.equal(seq.message(), "<SX200H> SP1ABC R-09");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "<SP1ABC> SX200H RR73", snr: -8, startMs: EVEN + 60000 }]);
  assert.equal(seq.message(), "<SX200H> SP1ABC 73");
  seq.transmitted(seq.message());
  assert.deepEqual(finished, [{ call: "SX200H", grid: "", sent: "-09", rcvd: "-05" }]);
  assert.ok(seq.answer({ msg: "CQ F4ELU/QRP", snr: -12, startMs: EVEN }));
  assert.equal(seq.message(), "<F4ELU/QRP> SP1ABC JO82");
});

test("an own nonstandard call: hashed to a standard one, CQ without the grid", () => {
  const seq = new Sequencer("SX200H", "JO93", "FT8");
  seq.callCq(0);
  assert.equal(seq.message(), "CQ SX200H");
  assert.ok(seq.answer({ msg: "CQ DL1XYZ JO62", snr: -3, startMs: EVEN }));
  assert.equal(seq.message(), "DL1XYZ <SX200H> JO93");
});

test("calling CQ: the answering station gets a report, then RR73", () => {
  const { seq, finished } = qso();
  seq.callCq(0);
  assert.equal(seq.message(), "CQ SP1ABC JO82");
  seq.heard([
    { msg: "CQ F5AAA JN18", snr: 0, startMs: ODD },
    { msg: "SP1ABC DL1XYZ JO62", snr: -15, startMs: ODD },
  ]);
  assert.equal(seq.message(), "DL1XYZ SP1ABC -15");
  seq.transmitted(seq.message());
  seq.heard([{ msg: "SP1ABC F5AAA R-01", snr: 1, startMs: ODD + 30000 }]); // not our partner
  assert.equal(seq.message(), "DL1XYZ SP1ABC -15");
  seq.heard([{ msg: "SP1ABC DL1XYZ R-09", snr: -14, startMs: ODD + 30000 }]);
  assert.equal(seq.message(), "DL1XYZ SP1ABC RR73");
  seq.transmitted(seq.message());
  assert.deepEqual(finished, [{ call: "DL1XYZ", grid: "JO62", sent: "-15", rcvd: "-09" }]);
});

// Owner's report 2026-11-11: the log dialog opened twice. The partner's R-09
// was decoded only after our next message (the report) had gone out; that
// transmission's end then finished the QSO, before RR73 was sent at all.
test("only a sent RR73 or 73 finishes the QSO, once", () => {
  const { seq, finished } = qso();
  seq.callCq(0);
  seq.heard([{ msg: "SP1ABC DL1XYZ JO62", snr: -15, startMs: ODD }]);
  const report = seq.message();
  assert.equal(report, "DL1XYZ SP1ABC -15");
  seq.transmitted(report);
  // The report goes out again; R-09 is decoded while it is on the air.
  seq.heard([{ msg: "SP1ABC DL1XYZ R-09", snr: -14, startMs: ODD + 30000 }]);
  seq.transmitted(report);
  assert.deepEqual(finished, []); // RR73 not sent yet
  assert.equal(seq.message(), "DL1XYZ SP1ABC RR73");
  seq.transmitted(seq.message());
  assert.deepEqual(finished, [{ call: "DL1XYZ", grid: "JO62", sent: "-15", rcvd: "-09" }]);
  assert.equal(seq.step, null);
});

test("no answer: the same message again; RR73 is not a grid", () => {
  const { seq } = qso();
  seq.answer({ msg: "CQ DL1XYZ JO62", snr: -3, startMs: ODD });
  assert.equal(seq.parity, 0);
  seq.transmitted(seq.message());
  seq.heard([]);
  assert.equal(seq.message(), "DL1XYZ SP1ABC JO82");
  assert.equal(seq.answer({ msg: "CQ SP1ABC JO82", snr: 0, startMs: ODD }), false); // ourselves
  seq.answer({ msg: "SP1ABC DL1XYZ RR73", snr: -3, startMs: ODD });
  assert.equal(seq.message(), "DL1XYZ SP1ABC 73");
});

test("a cycle goes to the decoder before it ends", () => {
  const collector = new CycleCollector("FT8", RATE);
  const at = [];
  let seq = 0;
  collector.onCycle = () => at.push(seq * 20);
  for (; seq < 15 * 50; seq++) pushWithClock(collector, seq, T0 / 1000, new Float32Array(FRAME));
  assert.deepEqual(at, [13600 - 20]); // the frame that completes 13.6 s
});

// The page hands each cycle to the decoder worker by transfer, which empties the
// array (owner's report 2026-11-10: the listening went silent about 15 s after Start).
test("a cycle handed over by transfer does not break the collector", () => {
  const collector = new CycleCollector("FT8", RATE);
  const cycles = [];
  collector.onCycle = (cycle) => {
    cycles.push({ ...cycle, samples: structuredClone(cycle.samples, { transfer: [cycle.samples.buffer] }) });
    assert.equal(cycle.samples.length, 0); // detached, as after postMessage(..., [buffer])
  };
  for (let seq = 0; seq < 3 * 15 * 50; seq++) {
    pushWithClock(collector, seq, T0 / 1000, new Float32Array(FRAME).fill(1)); // must never throw
  }
  assert.deepEqual(
    cycles.map((c) => c.startMs),
    [T0, T0 + 15000, T0 + 30000],
  );
  assert.ok(cycles.every((c) => c.samples.length === (CYCLE_MS.FT8 * RATE) / 1000));
  assert.ok(cycles.every((c) => c.coverage === 1));
});

// The safeguard MAX_FROM_CLOCK_MS: audio far from the clock is dropped, not
// taken as a cycle minutes away. (The cause of decoding stopping after a new
// subscription, 11.11 and 14.11, was the frame numbering: test/audio.test.mjs.)
test("late frames of an earlier audio stream do not stop the decoding", () => {
  const collector = new CycleCollector("FT8", RATE);
  const cycles = [];
  collector.onCycle = (cycle) => cycles.push(cycle.startMs);
  // The old stream had run for ten minutes.
  collector.setClock(parseClock(clockFrame(30000, T0 / 1000 - 600)));
  collector.push(30000, new Float32Array(FRAME));
  // Subscribed anew: its first clock frame, then stale frames of the old one.
  collector.setClock(parseClock(clockFrame(0, T0 / 1000)));
  for (let seq = 30001; seq < 30005; seq++) collector.push(seq, new Float32Array(FRAME));
  for (let seq = 0; seq < 2 * 15 * 50; seq++) pushWithClock(collector, seq, T0 / 1000, new Float32Array(FRAME).fill(1));
  assert.deepEqual(cycles, [T0, T0 + 15000]);
});

// Owner's choice 2026-11-11: depth 3 (about 1.8 s) made every answer a cycle
// late; the cycle right before ours is decoded at depth 2.
import { cycleDepth } from "../../src/remoteg90ctl/server/static/ft8.js";

test("the cycle before our transmission is decoded at depth 2", () => {
  // EVEN is an even FT8 cycle: the next one is odd.
  assert.equal(cycleDepth(3, EVEN, "FT8", 1), 2); // we transmit next
  assert.equal(cycleDepth(3, EVEN, "FT8", 0), 3); // we transmitted in it ourselves
  assert.equal(cycleDepth(3, EVEN, "FT8", null), 3); // not transmitting
  assert.equal(cycleDepth(1, EVEN, "FT8", 1), 1); // never deeper than chosen
  assert.equal(cycleDepth(3, ODD, "FT8", 0), 2);
});

// ---- The waterfall's view for a mode (owner's request 2026-11-10) ----

import { fitRange, viewRange } from "../../src/remoteg90ctl/server/static/ft8.js";

test("the view of a mode starts at the dial and covers the audio band of the signals", () => {
  for (const mode of ["FT8", "FT4"]) {
    const [low, high] = viewRange(mode, 14_074_000);
    assert.equal(low, 14_074_000);
    assert.ok(high - low >= 3000 && high - low <= 3500, `${mode} width ${high - low}`);
  }
});

test("a range is fitted by zoom and pan, within the limits", () => {
  // Span 96 kHz centred on the dial + 1.6 kHz (the radio's centre follows the VFO).
  const span = 96_000;
  const view = fitRange(span, 14_075_600, 14_074_000, 14_077_200, 32, 1);
  assert.ok(Math.abs(view.zoom - span / 3200) < 1e-9); // 30x
  assert.ok(Math.abs(view.pan) < 1e-9); // the range is the middle of the span
  // A range left of the centre pans left; zoom never goes beyond the limits.
  assert.ok(fitRange(span, 14_080_000, 14_074_000, 14_077_200, 32, 1).pan < 0);
  assert.equal(fitRange(span, 14_075_600, 14_074_000, 14_074_100, 32, 1).zoom, 32); // too narrow: max zoom
  assert.equal(fitRange(span, 14_075_600, 13_000_000, 15_000_000, 32, 1.2).zoom, 1.2); // too wide: min zoom
  // Never panned out of the band.
  const edge = fitRange(span, 14_000_000, 14_074_000, 14_077_200, 32, 1);
  assert.ok(Math.abs(edge.pan) <= 0.5 - 0.5 / edge.zoom + 1e-12);
});

import { atDial } from "../../src/remoteg90ctl/server/static/ft8.js";

test("the zoom waits for the radio on the dial, not near it", () => {
  // The waterfall's centre is the VFO plus a small offset of the mode (tens of Hz).
  assert.ok(atDial(14_074_000, 14_074_000));
  assert.ok(atDial(14_073_978, 14_074_000));
  // Still on the old frequency 2 kHz away: the view would end up 2 kHz off.
  assert.ok(!atDial(14_072_000, 14_074_000));
  assert.ok(!atDial(14_076_000, 14_074_000));
});

// ---- The cycle's number (owner's request 2026-11-10) ----

import { cycleNumber } from "../../src/remoteg90ctl/server/static/ft8.js";

test("the cycle number is 1st or 2nd, as in the TX choice, from UTC", () => {
  // FT8: 00 and 30 s of a minute are the 1st cycles, 15 and 45 s the 2nd.
  assert.deepEqual(cycleNumber(T0, "FT8"), { slot: "1st", position: 1, perMinute: 4 });
  assert.equal(cycleNumber(T0 + 15_000, "FT8").slot, "2nd");
  assert.equal(cycleNumber(T0 + 30_000, "FT8").slot, "1st");
  assert.deepEqual(cycleNumber(T0 + 45_000, "FT8"), { slot: "2nd", position: 4, perMinute: 4 });
  // Any time inside a cycle gives that cycle's number.
  assert.equal(cycleNumber(T0 + 16_999, "FT8").slot, "2nd");
  // FT4: eight cycles a minute, alternating.
  assert.deepEqual(cycleNumber(T0 + 7_500, "FT4"), { slot: "2nd", position: 2, perMinute: 8 });
  assert.deepEqual(cycleNumber(T0 + 52_500, "FT4"), { slot: "2nd", position: 8, perMinute: 8 });
  assert.equal(cycleNumber(T0 + 60_000, "FT4").position, 1); // the next minute starts again
});


// Owner's report 2026-11-14: the log lacked the grid of a station that answered
// with a report straight away. The page keeps the grids heard in any message.
import { gridOf } from "../../src/remoteg90ctl/server/static/ft8.js";

test("the sender's grid is read from CQs and from replies", () => {
  assert.deepEqual(gridOf("CQ EA7AAF IM76"), ["EA7AAF", "IM76"]);
  assert.deepEqual(gridOf("CQ DX OK1UOZ JO70"), ["OK1UOZ", "JO70"]);
  assert.deepEqual(gridOf("SP2EMW DO8JB JO32"), ["DO8JB", "JO32"]);
  assert.equal(gridOf("SP2EMW EA7AAF -03"), null); // a report, no grid
  assert.equal(gridOf("SP2EMW EA7AAF RR73"), null); // looks like a grid, is not
  assert.equal(gridOf("SP2EMW EA7AAF 73"), null);
});
