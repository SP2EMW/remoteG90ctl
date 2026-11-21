// SPDX-License-Identifier: GPL-3.0-or-later
//
// Plays received audio from a ring buffer (stage 3). Playback starts once
// the target is in (100-200 ms, adaptive); the buffer is held near
// that level by dropping or repeating a sample now and then, which follows the
// small difference between the radio's and the browser's clocks. Idea from
// no-sdr (MIT, ideas only), see docs/third-party.md.
import { RxDsp } from "./rxdsp.js";
import { NoiseGate } from "./txdsp.js";
const CAPACITY = 2 * 48000;
// The buffer's target adapts (latency budget, 2026-11-21): it starts at
// TARGET_MIN, grows by TARGET_STEP after each underrun up to TARGET_MAX, and
// shrinks by half a step after CALM_QUANTA without one.
const TARGET_MIN = 4800; // 100 ms at 48 kHz (owner's choice 2026-11-21, was 80)
const TARGET_MAX = 9600; // 200 ms (was 150): fewer dropouts, a little more delay
const TARGET_STEP = 960; // 20 ms
const CALM_QUANTA = Math.round((60 * 48000) / 128); // a minute
// After a stall of the link (LTE, a relay) the audio arrives in a burst (note
// #189, 2026-11-18). Above the limit it goes back to the target at once, the
// old and the new place crossfaded over one quantum (no click). The page sets
// the limit so that the whole delay stays within 250 ms; until then 200 ms.
const LIMIT_DEFAULT = 9600;
const LIMIT_ABOVE_TARGET = 1920; // never closer to the target than 40 ms
const REPORT_EVERY = 64; // quanta of 128 samples: about 0.17 s
class RxPlayer extends AudioWorkletProcessor {
    constructor() {
        super();
        this.ring = new Float32Array(CAPACITY);
        this.read = 0;
        this.write = 0;
        this.buffered = 0;
        this.playing = false;
        this.quanta = 0;
        this.underruns = 0;
        this.catchUps = 0;
        this.target = TARGET_MIN;
        this.limit = LIMIT_DEFAULT;
        this.calm = 0;
        this.port.onmessage = (e) => {
            const data = e.data;
            if (data.type === "limit" && typeof data.limit === "number") {
                this.limit = Math.max(0, Math.min(CAPACITY, Math.round(data.limit)));
            }
            else if (data.type === "flush") {
                this.read = this.write = this.buffered = 0;
                this.playing = false;
            }
            else if (data.type === "push" && data.samples !== undefined) {
                this.push(data.samples);
            }
        };
    }
    push(samples) {
        for (let i = 0; i < samples.length; i++) {
            if (this.buffered === CAPACITY) {
                this.read = (this.read + 1) % CAPACITY; // full: drop the oldest
                this.buffered--;
            }
            this.ring[this.write] = samples[i];
            this.write = (this.write + 1) % CAPACITY;
            this.buffered++;
        }
    }
    // Drop the oldest audio down to the target: this quantum fades from where
    // playback was to the new place.
    catchUp(out) {
        const n = out.length;
        const jump = this.buffered - this.target;
        const from = this.read;
        const to = (this.read + jump) % CAPACITY;
        for (let i = 0; i < n; i++) {
            const k = (i + 1) / n;
            out[i] = this.ring[(from + i) % CAPACITY] * (1 - k) + this.ring[(to + i) % CAPACITY] * k;
        }
        this.read = (to + n) % CAPACITY;
        this.buffered -= jump + n;
        this.catchUps++;
    }
    process(_inputs, outputs) {
        const out = outputs[0][0];
        if (!this.playing && this.buffered >= this.target)
            this.playing = true;
        if (++this.calm > CALM_QUANTA && this.target > TARGET_MIN) {
            this.target = Math.max(TARGET_MIN, this.target - TARGET_STEP / 2);
            this.calm = 0;
        }
        const catchUp = Math.max(this.limit, this.target + LIMIT_ABOVE_TARGET);
        if (this.playing && this.buffered > catchUp + out.length) {
            this.catchUp(out);
        }
        else if (this.playing) {
            // Above twice the target: skip one sample; below half of it: repeat one.
            const skip = this.buffered > 2 * this.target ? 1 : 0;
            const hold = this.buffered < this.target / 2 ? 1 : 0;
            this.read = (this.read + skip) % CAPACITY;
            this.buffered -= skip;
            for (let i = 0; i < out.length; i++) {
                if (this.buffered === 0) {
                    this.playing = false; // underrun: wait for the buffer to fill again
                    this.underruns++;
                    this.target = Math.min(TARGET_MAX, this.target + TARGET_STEP);
                    this.calm = 0;
                    out.fill(0, i);
                    break;
                }
                out[i] = this.ring[this.read];
                if (!(hold && i === 0)) {
                    this.read = (this.read + 1) % CAPACITY;
                    this.buffered--;
                }
            }
        }
        else {
            out.fill(0);
        }
        for (let ch = 1; ch < outputs[0].length; ch++)
            outputs[0][ch].set(out);
        if (++this.quanta % REPORT_EVERY === 0) {
            this.port.postMessage({
                bufferedMs: (this.buffered / sampleRate) * 1000,
                underruns: this.underruns,
                targetMs: (this.target / sampleRate) * 1000,
            });
        }
        return true;
    }
}
registerProcessor("rx-player", RxPlayer);
// Microphone capture for transmitting (stage 4): 20 ms chunks (960 samples at
// 48 kHz) with their peak level, posted to the page, which encodes and sends
// them only while it holds a transmit lease.
const CHUNK = 960;
class TxCapture extends AudioWorkletProcessor {
    constructor() {
        super(...arguments);
        this.chunk = new Float32Array(CHUNK);
        this.filled = 0;
    }
    process(inputs) {
        const input = inputs[0]?.[0];
        if (input === undefined)
            return true;
        for (let i = 0; i < input.length; i++) {
            this.chunk[this.filled++] = input[i];
            if (this.filled === CHUNK) {
                let peak = 0;
                for (const v of this.chunk)
                    peak = Math.max(peak, Math.abs(v));
                const samples = this.chunk;
                this.port.postMessage({ samples, peak }, [samples.buffer]);
                this.chunk = new Float32Array(CHUNK);
                this.filled = 0;
            }
        }
        return true;
    }
}
registerProcessor("tx-capture", TxCapture);
// Receive audio processing (stage 8): automatic notch and noise reduction
// (rxdsp.ts), configured by the page; the filters around it are Web Audio nodes.
class RxDspProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.dsp = new RxDsp();
        this.port.onmessage = (e) => this.dsp.configure(e.data);
    }
    process(inputs, outputs) {
        const input = inputs[0]?.[0];
        const out = outputs[0][0];
        if (input === undefined) {
            out.fill(0);
            return true;
        }
        out.set(input);
        this.dsp.process(out);
        return true;
    }
}
registerProcessor("rx-dsp", RxDspProcessor);
// Noise gate of the transmit chain (stage 8), before the compressor.
class TxGate extends AudioWorkletProcessor {
    constructor() {
        super();
        this.gate = new NoiseGate(sampleRate);
        this.port.onmessage = (e) => {
            const data = e.data;
            this.gate.enabled = data.enabled;
            this.gate.setThresholdDb(data.thresholdDb);
        };
    }
    process(inputs, outputs) {
        const input = inputs[0]?.[0];
        const out = outputs[0][0];
        if (input === undefined) {
            out.fill(0);
            return true;
        }
        out.set(input);
        this.gate.process(out);
        return true;
    }
}
registerProcessor("tx-gate", TxGate);
