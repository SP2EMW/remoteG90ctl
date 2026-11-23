// SPDX-License-Identifier: GPL-3.0-or-later
//
// The receive player in the AudioWorklet (web/src/audio-worklet.ts), run in
// node with the worklet's globals stubbed: after a stall and a burst of audio
// the buffer goes back to its target at once (note #189, 2026-11-18).

import assert from "node:assert/strict";
import { test } from "node:test";

const processors = new Map();
globalThis.sampleRate = 48000;
globalThis.AudioWorkletProcessor = class {
  constructor() {
    this.port = { onmessage: null, postMessage() {} };
  }
};
globalThis.registerProcessor = (name, ctor) => processors.set(name, ctor);
const { nextTarget } = await import("../../src/remoteg90ctl/server/static/audio-worklet.js");

const QUANTUM = 128;

function player() {
  const p = new (processors.get("rx-player"))();
  const push = (samples) => p.port.onmessage({ data: { type: "push", samples } });
  const play = () => {
    const out = [new Float32Array(QUANTUM), new Float32Array(QUANTUM)];
    p.process([], [out]);
    return out[0];
  };
  return { p, push, play };
}

test("a burst after a stall is taken back to the target at once, without a jump", () => {
  const { p, push, play } = player();
  push(new Float32Array(4800).fill(0.5)); // 100 ms, the first target: playback starts
  play();
  push(new Float32Array(48000).fill(0.5)); // a 1 s burst after a stall
  const out = play();
  assert.ok(p.buffered <= 4800, `buffered ${p.buffered}`);
  // A constant signal stays constant across the jump: no click.
  for (const v of out) assert.ok(Math.abs(v - 0.5) < 1e-6);
});

test("the page's limit sets where a burst is cut, never closer than 40 ms above the target", () => {
  const { p, push, play } = player();
  p.port.onmessage({ data: { type: "limit", limit: 0 } });
  push(new Float32Array(4800 + 1920 + 1000).fill(0.2)); // just over target + 40 ms
  play();
  assert.ok(p.buffered <= 4800);
  const second = player();
  second.push(new Float32Array(9000).fill(0.2)); // 188 ms: under the default 200 ms
  second.play();
  assert.ok(second.p.buffered > 9000 - 3 * QUANTUM);
});

test("the target grows after an underrun and starts small", () => {
  const { p, push, play } = player();
  assert.equal(p.target, 4800);
  push(new Float32Array(4800).fill(0.1));
  for (let i = 0; i < 40; i++) play(); // plays it all, then runs dry
  assert.equal(p.underruns, 1);
  assert.equal(p.target, 4800 + 960);
});

test("after an underrun the target grows by 20 ms up to 200 ms, then by 5 ms up to 250 ms", () => {
  const ms = (samples) => (samples / 48000) * 1000;
  let target = 4800; // 100 ms
  const steps = [];
  while (true) {
    const next = nextTarget(target);
    if (next === target) break;
    steps.push([ms(target), ms(next)]);
    target = next;
  }
  assert.deepEqual(steps.slice(0, 5).map(([a, b]) => b - a), [20, 20, 20, 20, 20]);
  assert.equal(ms(target), 250);
  assert.ok(steps.filter(([a]) => a >= 200).every(([a, b]) => b - a === 5));
  assert.equal(nextTarget(9600), 9840); // 200 ms -> 205 ms
});
