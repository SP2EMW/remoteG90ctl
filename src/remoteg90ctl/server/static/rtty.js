// SPDX-License-Identifier: GPL-3.0-or-later
//
// RTTY reception (decisions.md D61): 45.45 baud, 170 Hz shift, Baudot (ITA2
// with the US figures, as amateurs use them). Pure code, no browser APIs: the
// page feeds it the received audio before any processing (as FT8, D52).
//
// Our own code. Ideas, no code, taken from fldigi (W1HKJ and others, GPL-3.0:
// a matched filter per tone, a threshold that follows the strength of each
// tone separately, unshift on space) and minimodem (Kamal Mostafa,
// GPL-3.0-or-later: framing checked bit by bit, the stop bit must be mark).
// See docs/third-party.md.
import { fft } from "./rxdsp.js";
export const RTTY_BAUD = 45.45;
export const RTTY_SHIFT_HZ = 170;
// Audio frequency of the mark tone in L-D; the space tone is 170 Hz above it.
export const RTTY_MARK_HZ = 2125;
export const RTTY_MARK_MIN_HZ = 300;
export const RTTY_MARK_MAX_HZ = 2500;
// Baudot code, letters and figures; index = the 5 bits, the first bit sent
// lowest. "" for codes that print nothing here (NUL, CR, the shifts, the bell).
const LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x1bMXV\x1f";
const FIGS = "\x003\n- \x0787\r$4',!:(5\")2#6019?&\x1b./;\x1f";
const CODE_FIGS = 0x1b;
const CODE_LTRS = 0x1f;
const CODE_SPACE = 0x04;
export function baudotChar(code, figures) {
    const c = (figures ? FIGS : LTRS)[code & 0x1f];
    return c === "\n" || c === " " || /[A-Z0-9\-$',!:()"#?&./;]/.test(c) ? c : "";
}
// Squelch (owner's report 2026-11-24: noise printed between the overs).
const CLARITY_BITS = 12; // averaged over this many bits (about 1.6 characters)
// Noise stays under about 0.3; a signal readable at -6 dB in 3 kHz is above.
const SQUELCH = 0.38;
// AFC (owner's request 2026-11-24): stations a little off the set tones are
// followed, up to AFC_RANGE_HZ, and a shift of AFC_SHIFT_MIN..MAX Hz (170
// mostly, 200 by some) is taken as it comes.
const AFC_FFT = 4096; // at WORK_RATE: about 2 Hz a bin, 0.5 s
const AFC_EVERY = 2000; // samples at WORK_RATE between looks (0.25 s)
const AFC_RANGE_HZ = 100;
const AFC_SHIFT_MIN_HZ = 150;
const AFC_SHIFT_MAX_HZ = 220;
const AFC_WINDOW_HZ = 60; // each tone's power is looked for this far each side
const AFC_FIND = 0.5; // above the floor by this much a bin on average: a signal
const AFC_KEEP = 0.8; // the spectrum averaged over about 1.25 s
const AFC_GAIN = 0.5; // the part of the way moved at each look
// The audio is first brought down to this rate: plenty for tones under 3 kHz.
const WORK_RATE = 8000;
// A moving sum of complex values: the matched filter of one tone over one bit.
class MovingSum {
    constructor(length) {
        this.at = 0;
        this.sumRe = 0;
        this.sumIm = 0;
        this.re = new Float64Array(length);
        this.im = new Float64Array(length);
    }
    push(re, im) {
        this.sumRe += re - this.re[this.at];
        this.sumIm += im - this.im[this.at];
        this.re[this.at] = re;
        this.im[this.at] = im;
        this.at = (this.at + 1) % this.re.length;
        return Math.hypot(this.sumRe, this.sumIm);
    }
    clear() {
        this.re.fill(0);
        this.im.fill(0);
        this.sumRe = 0;
        this.sumIm = 0;
    }
}
export class RttyDecoder {
    constructor(sampleRate = 48000) {
        this.sampleRate = sampleRate;
        // Called with each piece of text as it is decoded.
        this.onText = () => { };
        this.gathered = 0;
        this.gatheredCount = 0;
        this.markPhase = 0;
        this.spacePhase = 0;
        this.markHz = RTTY_MARK_HZ;
        this.reverse = false;
        // AFC: on or off; the found offset of the lower tone and the shift.
        this.afcOn = true;
        this.offsetHz = 0;
        this.shiftHz = RTTY_SHIFT_HZ;
        this.ring = new Float32Array(AFC_FFT);
        this.ringAt = 0;
        this.sinceLook = 0;
        this.power = null;
        this.markEnv = 0;
        this.spaceEnv = 0;
        // Framing (one character: a start bit, 5 data bits, at least 1 stop bit).
        this.frame = "idle";
        this.wait = 0; // samples to the middle of the next bit
        this.bits = 0;
        this.bitCount = 0;
        this.lastBit = 1;
        this.figures = false;
        // Squelch: how clearly one tone stands over the other, averaged over a
        // few bits (a signal: near 1, one tone on at a time; noise: both alike).
        this.clarity = 0;
        this.decimation = Math.max(1, Math.round(sampleRate / WORK_RATE));
        this.bitSamples = this.rate / RTTY_BAUD;
        const length = Math.round(this.bitSamples);
        this.mark = new MovingSum(length);
        this.space = new MovingSum(length);
        this.attack = 1 / (this.bitSamples / 4);
        this.decay = 1 / (this.bitSamples * 16);
        this.clarityRate = 1 / (this.bitSamples * CLARITY_BITS);
    }
    get rate() {
        return this.sampleRate / this.decimation;
    }
    setTones(markHz, reverse) {
        this.markHz = markHz;
        this.reverse = reverse;
        this.offsetHz = 0;
        this.shiftHz = RTTY_SHIFT_HZ;
        this.power = null;
        this.reset();
    }
    get afc() {
        return this.afcOn;
    }
    set afc(on) {
        this.afcOn = on;
        if (!on) {
            this.offsetHz = 0;
            this.shiftHz = RTTY_SHIFT_HZ;
        }
    }
    // The tones decoded now (the set ones moved by the AFC), audio Hz.
    get tones() {
        const low = this.markHz + this.offsetHz;
        const high = low + this.shiftHz;
        return this.reverse ? { mark: high, space: low } : { mark: low, space: high };
    }
    reset() {
        this.mark.clear();
        this.space.clear();
        this.markEnv = 0;
        this.spaceEnv = 0;
        this.frame = "idle";
        this.lastBit = 1;
        this.figures = false;
        this.clarity = 0;
    }
    push(samples) {
        let text = "";
        for (const x of samples) {
            this.gathered += x;
            if (++this.gatheredCount < this.decimation)
                continue;
            const y = this.gathered / this.decimation;
            const c = this.step(y);
            this.ring[this.ringAt] = y;
            this.ringAt = (this.ringAt + 1) % AFC_FFT;
            if (this.afcOn && ++this.sinceLook >= AFC_EVERY) {
                this.sinceLook = 0;
                this.look();
            }
            this.gathered = 0;
            this.gatheredCount = 0;
            if (c !== "")
                text += c;
        }
        if (text !== "")
            this.onText(text);
    }
    // One sample at the working rate; the character it completes, or "".
    step(x) {
        const { mark: markHz, space: spaceHz } = this.tones;
        this.markPhase = (this.markPhase + (2 * Math.PI * markHz) / this.rate) % (2 * Math.PI);
        this.spacePhase = (this.spacePhase + (2 * Math.PI * spaceHz) / this.rate) % (2 * Math.PI);
        const m = this.mark.push(x * Math.cos(this.markPhase), -x * Math.sin(this.markPhase));
        const s = this.space.push(x * Math.cos(this.spacePhase), -x * Math.sin(this.spacePhase));
        // Each tone's own strength, followed fast upwards and slowly down, so a
        // fade of one tone (selective fading) does not shift the threshold.
        this.markEnv += (m - this.markEnv) * (m > this.markEnv ? this.attack : this.decay);
        this.spaceEnv += (s - this.spaceEnv) * (s > this.spaceEnv ? this.attack : this.decay);
        this.clarity += (Math.abs(m - s) / (m + s + 1e-12) - this.clarity) * this.clarityRate;
        const d = Math.min(m, this.markEnv) - Math.min(s, this.spaceEnv) - 0.5 * (this.markEnv - this.spaceEnv);
        const bit = d > 0 ? 1 : 0;
        const c = this.frameStep(bit);
        this.lastBit = bit;
        return c;
    }
    // AFC: each tone moves towards the centre of the power around it (above
    // the noise floor). Noise is flat, so it moves nothing; a signal pulls each
    // tone onto itself, also from up to AFC_WINDOW_HZ away.
    look() {
        const re = new Float32Array(AFC_FFT);
        const im = new Float32Array(AFC_FFT);
        for (let i = 0; i < AFC_FFT; i++) {
            const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / AFC_FFT);
            re[i] = this.ring[(this.ringAt + i) % AFC_FFT] * w;
        }
        fft(re, im);
        const bins = AFC_FFT / 2;
        if (this.power === null)
            this.power = new Float64Array(bins);
        const power = this.power;
        for (let k = 0; k < bins; k++)
            power[k] = AFC_KEEP * power[k] + (1 - AFC_KEEP) * (re[k] * re[k] + im[k] * im[k]);
        const perHz = AFC_FFT / this.rate;
        const band = Array.from(power.slice(Math.round(200 * perHz), Math.round(2900 * perHz))).sort((a, b) => a - b);
        const floor = band[Math.floor(band.length / 2)] || 1e-12;
        // The centre of the power above the floor within AFC_WINDOW_HZ of `hz`,
        // and how much stands above the floor there (in floors per bin).
        const centre = (hz) => {
            let sum = 0;
            let moment = 0;
            const from = Math.max(1, Math.round((hz - AFC_WINDOW_HZ) * perHz));
            const to = Math.min(bins - 1, Math.round((hz + AFC_WINDOW_HZ) * perHz));
            for (let k = from; k <= to; k++) {
                const p = Math.max(0, power[k] - floor);
                sum += p;
                moment += p * (k / perHz);
            }
            return { hz: sum > 0 ? moment / sum : hz, strength: sum / floor / (to - from + 1) };
        };
        const low = this.markHz + this.offsetHz;
        const lowFound = centre(low);
        const highFound = centre(low + this.shiftHz);
        if (lowFound.strength < AFC_FIND || highFound.strength < AFC_FIND)
            return; // no signal: stay
        const newLow = low + (lowFound.hz - low) * AFC_GAIN;
        const newHigh = low + this.shiftHz + (highFound.hz - low - this.shiftHz) * AFC_GAIN;
        this.shiftHz = Math.min(AFC_SHIFT_MAX_HZ, Math.max(AFC_SHIFT_MIN_HZ, newHigh - newLow));
        this.offsetHz = Math.min(AFC_RANGE_HZ, Math.max(-AFC_RANGE_HZ, newLow - this.markHz));
    }
    frameStep(bit) {
        switch (this.frame) {
            case "idle":
                if (this.lastBit === 1 && bit === 0) {
                    this.frame = "start";
                    this.wait = this.bitSamples / 2; // to the middle of the start bit
                }
                return "";
            case "start":
                if (--this.wait > 0)
                    return "";
                if (bit !== 0) {
                    this.frame = "idle"; // a spike, not a start bit
                    return "";
                }
                this.frame = "data";
                this.bits = 0;
                this.bitCount = 0;
                this.wait += this.bitSamples;
                return "";
            case "data":
                if (--this.wait > 0)
                    return "";
                this.bits |= bit << this.bitCount;
                this.wait += this.bitSamples;
                if (++this.bitCount === 5)
                    this.frame = "stop";
                return "";
            case "stop":
                if (--this.wait > 0)
                    return "";
                this.frame = "idle";
                return bit === 1 ? this.character(this.bits) : ""; // no stop bit: noise
        }
    }
    character(code) {
        // The shifts are followed whatever the squelch says; text is printed
        // only from a clear signal.
        if (code !== CODE_LTRS && code !== CODE_FIGS && this.clarity < SQUELCH) {
            if (code === CODE_SPACE)
                this.figures = false;
            return "";
        }
        if (code === CODE_LTRS) {
            this.figures = false;
            return "";
        }
        if (code === CODE_FIGS) {
            this.figures = true;
            return "";
        }
        const c = baudotChar(code, this.figures);
        if (code === CODE_SPACE)
            this.figures = false; // unshift on space
        return c;
    }
}
// ---- Transmission (D61): the text the page sends; the server keys it out.
// What can be sent: the Baudot characters, a space and a new line (the server
// refuses anything else; lower case goes in capitals).
const SENDABLE = new Set();
for (let code = 0; code < 32; code++) {
    for (const figures of [false, true]) {
        const c = baudotChar(code, figures);
        if (c !== "" && c !== "\n")
            SENDABLE.add(c);
    }
}
export function sendable(text) {
    let out = "";
    for (const c of text.toUpperCase().replace(/\r\n?/g, "\n")) {
        if (c === "\n" || SENDABLE.has(c))
            out += c;
    }
    return out;
}
// Default macros (owner's choice 2026-11-23: the usual ones for ordinary
// contacts and for contests). Each starts on a new line at the other end.
export const DEFAULT_MACROS = [
    { name: "CQ", text: "\nCQ CQ CQ DE {MYCALL} {MYCALL} {MYCALL} PSE K\n" },
    { name: "answer", text: "\n{CALL} {CALL} DE {MYCALL} {MYCALL} K\n" },
    {
        name: "QSO",
        text: "\n{CALL} DE {MYCALL} TNX FER CALL. UR RST 599 599. NAME {MYNAME} {MYNAME}. QTH {MYQTH}. LOC {MYLOC}. HW? {CALL} DE {MYCALL} K\n",
    },
    { name: "BTU", text: "\nBTU {CALL} DE {MYCALL} K\n" },
    { name: "73", text: "\n{CALL} TNX FER QSO 73 GL. {CALL} DE {MYCALL} SK\n" },
    { name: "CQ TEST", text: "\nCQ TEST {MYCALL} {MYCALL} TEST\n" },
    { name: "exchange", text: "\n{CALL} 599 {NR} {NR}\n" },
    { name: "TU", text: "\nTU {MYCALL} TEST\n" },
    { name: "AGN", text: "\nAGN AGN\n" },
    { name: "my call", text: "\n{MYCALL}\n" },
];
// The macro with its {NAMES} filled in; null when it needs a value that is
// empty (e.g. {CALL} with no call yet). The contest number has three digits.
export function expandMacro(text, values) {
    let missing = false;
    const filled = text.replace(/\{([A-Z]+)\}/g, (whole, name) => {
        if (name === "NR")
            return String(values.NR).padStart(3, "0");
        if (!(name in values))
            return whole;
        const value = String(values[name]).trim().toUpperCase();
        if (value === "")
            missing = true;
        return value;
    });
    return missing ? null : sendable(filled);
}
