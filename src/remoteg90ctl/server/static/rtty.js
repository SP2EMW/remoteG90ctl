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
        this.markEnv = 0;
        this.spaceEnv = 0;
        // Framing (one character: a start bit, 5 data bits, at least 1 stop bit).
        this.frame = "idle";
        this.wait = 0; // samples to the middle of the next bit
        this.bits = 0;
        this.bitCount = 0;
        this.lastBit = 1;
        this.figures = false;
        this.decimation = Math.max(1, Math.round(sampleRate / WORK_RATE));
        this.bitSamples = this.rate / RTTY_BAUD;
        const length = Math.round(this.bitSamples);
        this.mark = new MovingSum(length);
        this.space = new MovingSum(length);
        this.attack = 1 / (this.bitSamples / 4);
        this.decay = 1 / (this.bitSamples * 16);
    }
    get rate() {
        return this.sampleRate / this.decimation;
    }
    setTones(markHz, reverse) {
        this.markHz = markHz;
        this.reverse = reverse;
        this.reset();
    }
    reset() {
        this.mark.clear();
        this.space.clear();
        this.markEnv = 0;
        this.spaceEnv = 0;
        this.frame = "idle";
        this.lastBit = 1;
        this.figures = false;
    }
    push(samples) {
        let text = "";
        for (const x of samples) {
            this.gathered += x;
            if (++this.gatheredCount < this.decimation)
                continue;
            const c = this.step(this.gathered / this.decimation);
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
        const markHz = this.reverse ? this.markHz + RTTY_SHIFT_HZ : this.markHz;
        const spaceHz = this.reverse ? this.markHz : this.markHz + RTTY_SHIFT_HZ;
        this.markPhase = (this.markPhase + (2 * Math.PI * markHz) / this.rate) % (2 * Math.PI);
        this.spacePhase = (this.spacePhase + (2 * Math.PI * spaceHz) / this.rate) % (2 * Math.PI);
        const m = this.mark.push(x * Math.cos(this.markPhase), -x * Math.sin(this.markPhase));
        const s = this.space.push(x * Math.cos(this.spacePhase), -x * Math.sin(this.spacePhase));
        // Each tone's own strength, followed fast upwards and slowly down, so a
        // fade of one tone (selective fading) does not shift the threshold.
        this.markEnv += (m - this.markEnv) * (m > this.markEnv ? this.attack : this.decay);
        this.spaceEnv += (s - this.spaceEnv) * (s > this.spaceEnv ? this.attack : this.decay);
        const d = Math.min(m, this.markEnv) - Math.min(s, this.spaceEnv) - 0.5 * (this.markEnv - this.spaceEnv);
        const bit = d > 0 ? 1 : 0;
        const c = this.frameStep(bit);
        this.lastBit = bit;
        return c;
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
