// SPDX-License-Identifier: GPL-3.0-or-later
//
// SSTV reception (decisions.md D60): pictures from the receive audio, decoded
// in the browser before any processing, as FT8/FT4 (D52). Reception only: no
// path to transmitting or to CAT.
//
// A TypeScript port of the SSTV decoder of Robot36 by Ahmet Inan:
//   https://github.com/xdsopl/robot36 (branch v2, commit 2af2390954aa)
//   Copyright 2024 Ahmet Inan <xdsopl@gmail.com>, licence 0BSD:
//   "Permission to use, copy, modify, and/or distribute this software for any
//   purpose with or without fee is hereby granted."
// Ported from Demodulator.java, Decoder.java, RGBModes.java, RGBDecoder.java,
// Robot_36_Color.java, Robot_72_Color.java, PaulDon.java, ColorConverter.java
// and the small DSP classes (ComplexConvolution, Delay, ExponentialMovingAverage,
// Filter, FrequencyModulation, Kaiser, Phasor, SchmittTrigger,
// SimpleMovingAverage/SimpleMovingSum). Our changes: the Android scope and
// HF fax are left out; decoded lines go to a callback instead of a bitmap; a
// picture also starts when the sync pulses alone show a mode (joined after
// the VIS code); no manual mode lock. Listed in docs/third-party.md.
// ---- Small DSP blocks
class SimpleMovingSum {
    constructor(length) {
        this.length = length;
        this.tree = new Float32Array(2 * length);
        this.leaf = length;
    }
    add(input) {
        const tree = this.tree;
        tree[this.leaf] = input;
        for (let child = this.leaf, parent = this.leaf >> 1; parent > 0; child = parent, parent >>= 1) {
            tree[parent] = tree[child] + tree[child ^ 1];
        }
        if (++this.leaf >= tree.length)
            this.leaf = this.length;
    }
    sum(input) {
        this.add(input);
        return this.tree[1];
    }
}
class SimpleMovingAverage extends SimpleMovingSum {
    avg(input) {
        return this.sum(input) / this.length;
    }
}
class Delay {
    constructor(length) {
        this.pos = 0;
        this.buf = new Float32Array(length);
    }
    push(input) {
        const out = this.buf[this.pos];
        this.buf[this.pos] = input;
        if (++this.pos >= this.buf.length)
            this.pos = 0;
        return out;
    }
}
class ExponentialMovingAverage {
    constructor() {
        this.alpha = 1;
        this.prev = 0;
    }
    avg(input) {
        return (this.prev = this.prev * (1 - this.alpha) + this.alpha * input);
    }
    cutoff(freq, rate, order) {
        const x = Math.cos((2 * Math.PI * freq) / rate);
        this.alpha = Math.pow(x - 1 + Math.sqrt(x * (x - 4) + 3), 1 / order);
    }
    reset() {
        this.prev = 0;
    }
}
class SchmittTrigger {
    constructor(low, high) {
        this.low = low;
        this.high = high;
        this.previous = false;
    }
    latch(input) {
        if (this.previous) {
            if (input < this.low)
                this.previous = false;
        }
        else if (input > this.high) {
            this.previous = true;
        }
        return this.previous;
    }
}
function sinc(x) {
    if (x === 0)
        return 1;
    x *= Math.PI;
    return Math.sin(x) / x;
}
function lowPass(cutoff, rate, n, N) {
    const f = (2 * cutoff) / rate;
    const x = n - (N - 1) / 2;
    return f * sinc(f * x);
}
// Zero-th order modified Bessel function of the first kind; the summands
// added smallest first.
function i0(x) {
    const summands = new Float64Array(35);
    summands[0] = 1;
    let val = 1;
    for (let n = 1; n < summands.length; ++n)
        summands[n] = (val *= x / (2 * n)) ** 2;
    summands.sort();
    let sum = 0;
    for (let n = summands.length - 1; n >= 0; --n)
        sum += summands[n];
    return sum;
}
function kaiser(a, n, N) {
    return i0(Math.PI * a * Math.sqrt(1 - ((2 * n) / (N - 1) - 1) ** 2)) / i0(Math.PI * a);
}
// ---- Colours (0xAARRGGBB)
function clamp255(value) {
    return Math.min(Math.max(value, 0), 255);
}
function levelToByte(level) {
    return clamp255(Math.round(255 * level));
}
function rgb(red, green, blue) {
    return (0xff000000 | (levelToByte(red) << 16) | (levelToByte(green) << 8) | levelToByte(blue)) >>> 0;
}
function yuvBytesToRgb(Y, U, V) {
    Y -= 16;
    U -= 128;
    V -= 128;
    const R = clamp255((298 * Y + 409 * V + 128) >> 8);
    const G = clamp255((298 * Y - 100 * U - 208 * V + 128) >> 8);
    const B = clamp255((298 * Y + 516 * U + 128) >> 8);
    return (0xff000000 | (R << 16) | (G << 8) | B) >>> 0;
}
function yuvToRgb(Y, U, V) {
    return yuvBytesToRgb(levelToByte(Y), levelToByte(U), levelToByte(V));
}
function packedYuvToRgb(yuv) {
    return yuvBytesToRgb((yuv >>> 16) & 0xff, (yuv >>> 8) & 0xff, yuv & 0xff);
}
// Demodulated value (-1..1 for 1500..2300 Hz) to a level 0..1.
function freqToLevel(frequency, offset) {
    return 0.5 * (frequency - offset + 1);
}
// ---- Demodulator: FM to a normalised frequency, and the sync pulses
const SYNC_PULSE_HZ = 1200;
const BLACK_HZ = 1500;
const WHITE_HZ = 2300;
class Demodulator {
    constructor(sampleRate) {
        this.pos = 0;
        this.oscRe = 1;
        this.oscIm = 0;
        this.prevPhase = 0;
        this.counter = 0;
        this.pulseWidth = 5;
        this.pulseOffset = 0;
        this.frequencyOffset = 0;
        this.bandwidthHz = WHITE_HZ - BLACK_HZ;
        this.fmScale = sampleRate / (this.bandwidthHz * Math.PI);
        this.min5ms = Math.round((0.005 / 2) * sampleRate);
        this.max5ms = Math.round(((0.005 + 0.009) / 2) * sampleRate);
        this.max9ms = Math.round(((0.009 + 0.02) / 2) * sampleRate);
        this.max20ms = Math.round((0.02 + 0.005) * sampleRate);
        const filterSamples = Math.round((0.005 / 2) * sampleRate) | 1;
        this.filterDelay = (filterSamples - 1) / 2;
        this.syncPulseFilter = new SimpleMovingAverage(filterSamples);
        this.syncPulseValueDelay = new Delay(filterSamples);
        const lowestHz = 1000;
        const highestHz = 2800;
        const cutoffHz = (highestHz - lowestHz) / 2;
        const length = Math.round(0.002 * sampleRate) | 1;
        this.taps = new Float32Array(length);
        for (let i = 0; i < length; ++i)
            this.taps[i] = kaiser(2, i, length) * lowPass(cutoffHz, sampleRate, i, length);
        this.bufRe = new Float32Array(length);
        this.bufIm = new Float32Array(length);
        this.centerHz = (lowestHz + highestHz) / 2;
        const omega = (2 * Math.PI * -this.centerHz) / sampleRate;
        this.deltaRe = Math.cos(omega);
        this.deltaIm = Math.sin(omega);
        this.syncValue = this.normalize(SYNC_PULSE_HZ);
        this.syncTolerance = (50 * 2) / this.bandwidthHz;
        const porchHz = 1500;
        const highHz = (SYNC_PULSE_HZ + porchHz) / 2;
        const lowHz = (SYNC_PULSE_HZ + highHz) / 2;
        this.syncPulseTrigger = new SchmittTrigger(this.normalize(lowHz), this.normalize(highHz));
    }
    normalize(hz) {
        return ((hz - this.centerHz) * 2) / this.bandwidthHz;
    }
    // Demodulates `input` into `output`; true when a sync pulse ended in it
    // (its place, width and frequency offset are then set).
    process(input, output) {
        let detected = false;
        const taps = this.taps;
        const length = taps.length;
        for (let i = 0; i < input.length; ++i) {
            // Mix down to baseband around the centre.
            const re = this.oscRe * this.deltaRe - this.oscIm * this.deltaIm;
            const im = this.oscRe * this.deltaIm + this.oscIm * this.deltaRe;
            const abs = Math.hypot(re, im);
            this.oscRe = re / abs;
            this.oscIm = im / abs;
            this.bufRe[this.pos] = input[i] * this.oscRe;
            this.bufIm[this.pos] = input[i] * this.oscIm;
            if (++this.pos >= length)
                this.pos = 0;
            let sumRe = 0;
            let sumIm = 0;
            let p = this.pos;
            for (let k = 0; k < length; ++k) {
                sumRe += taps[k] * this.bufRe[p];
                sumIm += taps[k] * this.bufIm[p];
                if (++p >= length)
                    p = 0;
            }
            // FM: the phase step.
            const phase = Math.atan2(sumIm, sumRe);
            let delta = phase - this.prevPhase;
            if (delta < -Math.PI)
                delta += 2 * Math.PI;
            else if (delta > Math.PI)
                delta -= 2 * Math.PI;
            this.prevPhase = phase;
            const frequency = this.fmScale * delta;
            const syncPulseValue = this.syncPulseFilter.avg(frequency);
            const delayed = this.syncPulseValueDelay.push(syncPulseValue);
            output[i] = frequency;
            if (!this.syncPulseTrigger.latch(syncPulseValue)) {
                ++this.counter;
            }
            else if (this.counter < this.min5ms ||
                this.counter > this.max20ms ||
                Math.abs(delayed - this.syncValue) > this.syncTolerance) {
                this.counter = 0;
            }
            else {
                this.pulseWidth = this.counter < this.max5ms ? 5 : this.counter < this.max9ms ? 9 : 20;
                this.pulseOffset = i - this.filterDelay;
                this.frequencyOffset = delayed - this.syncValue;
                detected = true;
                this.counter = 0;
            }
        }
        return detected;
    }
}
// ---- Modes
// Pixels of one or two decoded lines (0xAARRGGBB).
class PixelBuffer {
    constructor() {
        this.pixels = new Uint32Array(800 * 2);
        this.width = 0;
        this.height = 0;
    }
}
class RgbMode {
    constructor(name, visCode, width, height, firstSyncPulseSeconds, scanLineSeconds, beginSeconds, redBeginSeconds, redEndSeconds, greenBeginSeconds, greenEndSeconds, blueBeginSeconds, blueEndSeconds, endSeconds, sampleRate) {
        this.name = name;
        this.visCode = visCode;
        this.width = width;
        this.height = height;
        this.lowPassFilter = new ExponentialMovingAverage();
        this.firstSyncPulseIndex = Math.round(firstSyncPulseSeconds * sampleRate);
        this.scanLineSamples = Math.round(scanLineSeconds * sampleRate);
        this.firstPixelSampleIndex = Math.round(beginSeconds * sampleRate);
        this.redBegin = Math.round(redBeginSeconds * sampleRate) - this.firstPixelSampleIndex;
        this.redSamples = Math.round((redEndSeconds - redBeginSeconds) * sampleRate);
        this.greenBegin = Math.round(greenBeginSeconds * sampleRate) - this.firstPixelSampleIndex;
        this.greenSamples = Math.round((greenEndSeconds - greenBeginSeconds) * sampleRate);
        this.blueBegin = Math.round(blueBeginSeconds * sampleRate) - this.firstPixelSampleIndex;
        this.blueSamples = Math.round((blueEndSeconds - blueBeginSeconds) * sampleRate);
        this.endSamples = Math.round(endSeconds * sampleRate);
    }
    resetState() { }
    decodeScanLine(pixels, scratch, line, syncPulseIndex, _scanLineSamples, frequencyOffset) {
        const begin = this.firstPixelSampleIndex;
        if (syncPulseIndex + begin < 0 || syncPulseIndex + this.endSamples > line.length)
            return false;
        const filter = this.lowPassFilter;
        filter.cutoff(this.width, 2 * this.greenSamples, 2);
        filter.reset();
        const count = this.endSamples - begin;
        for (let i = 0; i < count; ++i)
            scratch[i] = filter.avg(line[syncPulseIndex + begin + i]);
        filter.reset();
        for (let i = count - 1; i >= 0; --i)
            scratch[i] = freqToLevel(filter.avg(scratch[i]), frequencyOffset);
        for (let i = 0; i < this.width; ++i) {
            const red = this.redBegin + Math.floor((i * this.redSamples) / this.width);
            const green = this.greenBegin + Math.floor((i * this.greenSamples) / this.width);
            const blue = this.blueBegin + Math.floor((i * this.blueSamples) / this.width);
            pixels.pixels[i] = rgb(scratch[red], scratch[green], scratch[blue]);
        }
        pixels.width = this.width;
        pixels.height = 1;
        return true;
    }
}
function martin(name, code, channelSeconds, sampleRate) {
    const syncPulseSeconds = 0.004862;
    const separatorSeconds = 0.000572;
    const scanLineSeconds = syncPulseSeconds + separatorSeconds + 3 * (channelSeconds + separatorSeconds);
    const greenBegin = separatorSeconds;
    const greenEnd = greenBegin + channelSeconds;
    const blueBegin = greenEnd + separatorSeconds;
    const blueEnd = blueBegin + channelSeconds;
    const redBegin = blueEnd + separatorSeconds;
    const redEnd = redBegin + channelSeconds;
    return new RgbMode(`Martin ${name}`, code, 320, 256, 0, scanLineSeconds, greenBegin, redBegin, redEnd, greenBegin, greenEnd, blueBegin, blueEnd, redEnd, sampleRate);
}
function scottie(name, code, channelSeconds, sampleRate) {
    const syncPulseSeconds = 0.009;
    const separatorSeconds = 0.0015;
    const firstSyncPulseSeconds = syncPulseSeconds + 2 * (separatorSeconds + channelSeconds);
    const scanLineSeconds = syncPulseSeconds + 3 * (channelSeconds + separatorSeconds);
    const blueEnd = -syncPulseSeconds;
    const blueBegin = blueEnd - channelSeconds;
    const greenEnd = blueBegin - separatorSeconds;
    const greenBegin = greenEnd - channelSeconds;
    const redBegin = separatorSeconds;
    const redEnd = redBegin + channelSeconds;
    return new RgbMode(`Scottie ${name}`, code, 320, 256, firstSyncPulseSeconds, scanLineSeconds, greenBegin, redBegin, redEnd, greenBegin, greenEnd, blueBegin, blueEnd, redEnd, sampleRate);
}
function wraaseSc2180(sampleRate) {
    const syncPulseSeconds = 0.0055225;
    const syncPorchSeconds = 0.0005;
    const channelSeconds = 0.235;
    const scanLineSeconds = syncPulseSeconds + syncPorchSeconds + 3 * channelSeconds;
    const redBegin = syncPorchSeconds;
    const redEnd = redBegin + channelSeconds;
    const greenBegin = redEnd;
    const greenEnd = greenBegin + channelSeconds;
    const blueBegin = greenEnd;
    const blueEnd = blueBegin + channelSeconds;
    return new RgbMode("Wraase SC2-180", 55, 320, 256, 0, scanLineSeconds, redBegin, redBegin, redEnd, greenBegin, greenEnd, blueBegin, blueEnd, blueEnd, sampleRate);
}
class Robot36 {
    constructor(sampleRate) {
        this.name = "Robot 36";
        this.visCode = 8;
        this.width = 320;
        this.height = 240;
        this.firstSyncPulseIndex = 0;
        this.lowPassFilter = new ExponentialMovingAverage();
        this.lastEven = false;
        const syncPulseSeconds = 0.009;
        const syncPorchSeconds = 0.003;
        const luminanceSeconds = 0.088;
        const separatorSeconds = 0.0045;
        const porchSeconds = 0.0015;
        const chrominanceSeconds = 0.044;
        this.scanLineSamples = Math.round((syncPulseSeconds + syncPorchSeconds + luminanceSeconds + separatorSeconds + porchSeconds + chrominanceSeconds) * sampleRate);
        this.luminanceSamples = Math.round(luminanceSeconds * sampleRate);
        this.separatorSamples = Math.round(separatorSeconds * sampleRate);
        this.chrominanceSamples = Math.round(chrominanceSeconds * sampleRate);
        const luminanceBegin = syncPorchSeconds;
        this.firstPixelSampleIndex = Math.round(luminanceBegin * sampleRate);
        const separatorBegin = luminanceBegin + luminanceSeconds;
        this.separatorBegin = Math.round(separatorBegin * sampleRate);
        const chrominanceBegin = separatorBegin + separatorSeconds + porchSeconds;
        this.chrominanceBegin = Math.round(chrominanceBegin * sampleRate);
        this.endSamples = Math.round((chrominanceBegin + chrominanceSeconds) * sampleRate);
    }
    resetState() {
        this.lastEven = false;
    }
    decodeScanLine(pixels, scratch, line, syncPulseIndex, _scanLineSamples, frequencyOffset) {
        const begin = this.firstPixelSampleIndex;
        if (syncPulseIndex + begin < 0 || syncPulseIndex + this.endSamples > line.length)
            return false;
        // The separator tells the line with V (1500 Hz) from the one with U (2300 Hz).
        let separator = 0;
        for (let i = 0; i < this.separatorSamples; ++i)
            separator += line[syncPulseIndex + this.separatorBegin + i];
        separator /= this.separatorSamples;
        separator -= frequencyOffset;
        let even = separator < 0;
        if (separator < -1.1 || (separator > -0.9 && separator < 0.9) || separator > 1.1)
            even = !this.lastEven;
        this.lastEven = even;
        const filter = this.lowPassFilter;
        filter.cutoff(this.width, 2 * this.luminanceSamples, 2);
        filter.reset();
        for (let i = begin; i < this.endSamples; ++i)
            scratch[i] = filter.avg(line[syncPulseIndex + i]);
        filter.reset();
        for (let i = this.endSamples - 1; i >= begin; --i)
            scratch[i] = freqToLevel(filter.avg(scratch[i]), frequencyOffset);
        for (let i = 0; i < this.width; ++i) {
            const luminance = begin + Math.floor((i * this.luminanceSamples) / this.width);
            const chrominance = this.chrominanceBegin + Math.floor((i * this.chrominanceSamples) / this.width);
            if (even) {
                // Kept as packed YUV until the odd line brings U.
                pixels.pixels[i] = rgb(scratch[luminance], 0, scratch[chrominance]);
            }
            else {
                const evenYuv = pixels.pixels[i];
                const oddYuv = rgb(scratch[luminance], scratch[chrominance], 0);
                pixels.pixels[i] = packedYuvToRgb((evenYuv & 0x00ff00ff) | (oddYuv & 0x0000ff00));
                pixels.pixels[i + this.width] = packedYuvToRgb((oddYuv & 0x00ffff00) | (evenYuv & 0x000000ff));
            }
        }
        pixels.width = this.width;
        pixels.height = 2;
        return !even;
    }
}
class Robot72 {
    constructor(sampleRate) {
        this.name = "Robot 72";
        this.visCode = 12;
        this.width = 320;
        this.height = 240;
        this.firstSyncPulseIndex = 0;
        this.lowPassFilter = new ExponentialMovingAverage();
        const syncPulseSeconds = 0.009;
        const syncPorchSeconds = 0.003;
        const luminanceSeconds = 0.138;
        const separatorSeconds = 0.0045;
        const porchSeconds = 0.0015;
        const chrominanceSeconds = 0.069;
        this.scanLineSamples = Math.round((syncPulseSeconds + syncPorchSeconds + luminanceSeconds + 2 * (separatorSeconds + porchSeconds + chrominanceSeconds)) * sampleRate);
        this.luminanceSamples = Math.round(luminanceSeconds * sampleRate);
        this.chrominanceSamples = Math.round(chrominanceSeconds * sampleRate);
        const yBegin = syncPorchSeconds;
        this.firstPixelSampleIndex = Math.round(yBegin * sampleRate);
        const vBegin = yBegin + luminanceSeconds + separatorSeconds + porchSeconds;
        this.vBegin = Math.round(vBegin * sampleRate);
        const uBegin = vBegin + chrominanceSeconds + separatorSeconds + porchSeconds;
        this.uBegin = Math.round(uBegin * sampleRate);
        this.endSamples = Math.round((uBegin + chrominanceSeconds) * sampleRate);
    }
    resetState() { }
    decodeScanLine(pixels, scratch, line, syncPulseIndex, _scanLineSamples, frequencyOffset) {
        const begin = this.firstPixelSampleIndex;
        if (syncPulseIndex + begin < 0 || syncPulseIndex + this.endSamples > line.length)
            return false;
        const filter = this.lowPassFilter;
        filter.cutoff(this.width, 2 * this.luminanceSamples, 2);
        filter.reset();
        for (let i = begin; i < this.endSamples; ++i)
            scratch[i] = filter.avg(line[syncPulseIndex + i]);
        filter.reset();
        for (let i = this.endSamples - 1; i >= begin; --i)
            scratch[i] = freqToLevel(filter.avg(scratch[i]), frequencyOffset);
        for (let i = 0; i < this.width; ++i) {
            const y = begin + Math.floor((i * this.luminanceSamples) / this.width);
            const u = this.uBegin + Math.floor((i * this.chrominanceSamples) / this.width);
            const v = this.vBegin + Math.floor((i * this.chrominanceSamples) / this.width);
            pixels.pixels[i] = yuvToRgb(scratch[y], scratch[u], scratch[v]);
        }
        pixels.width = this.width;
        pixels.height = 1;
        return true;
    }
}
// PD modes: two lines per sync pulse, sharing U and V.
class PaulDon {
    constructor(name, visCode, width, height, channelSeconds, sampleRate) {
        this.visCode = visCode;
        this.width = width;
        this.height = height;
        this.firstSyncPulseIndex = 0;
        this.lowPassFilter = new ExponentialMovingAverage();
        this.name = `PD ${name}`;
        const syncPulseSeconds = 0.02;
        const syncPorchSeconds = 0.00208;
        this.scanLineSamples = Math.round((syncPulseSeconds + syncPorchSeconds + 4 * channelSeconds) * sampleRate);
        this.channelSamples = Math.round(channelSeconds * sampleRate);
        const yEvenBegin = syncPorchSeconds;
        this.firstPixelSampleIndex = Math.round(yEvenBegin * sampleRate);
        const vAvgBegin = yEvenBegin + channelSeconds;
        this.vAvgBegin = Math.round(vAvgBegin * sampleRate);
        const uAvgBegin = vAvgBegin + channelSeconds;
        this.uAvgBegin = Math.round(uAvgBegin * sampleRate);
        const yOddBegin = uAvgBegin + channelSeconds;
        this.yOddBegin = Math.round(yOddBegin * sampleRate);
        this.endSamples = Math.round((yOddBegin + channelSeconds) * sampleRate);
    }
    resetState() { }
    decodeScanLine(pixels, scratch, line, syncPulseIndex, _scanLineSamples, frequencyOffset) {
        const begin = this.firstPixelSampleIndex;
        if (syncPulseIndex + begin < 0 || syncPulseIndex + this.endSamples > line.length)
            return false;
        const filter = this.lowPassFilter;
        filter.cutoff(this.width, 2 * this.channelSamples, 2);
        filter.reset();
        for (let i = begin; i < this.endSamples; ++i)
            scratch[i] = filter.avg(line[syncPulseIndex + i]);
        filter.reset();
        for (let i = this.endSamples - 1; i >= begin; --i)
            scratch[i] = freqToLevel(filter.avg(scratch[i]), frequencyOffset);
        for (let i = 0; i < this.width; ++i) {
            const position = Math.floor((i * this.channelSamples) / this.width);
            const u = scratch[position + this.uAvgBegin];
            const v = scratch[position + this.vAvgBegin];
            pixels.pixels[i] = yuvToRgb(scratch[position + begin], u, v);
            pixels.pixels[i + this.width] = yuvToRgb(scratch[position + this.yOddBegin], u, v);
        }
        pixels.width = this.width;
        pixels.height = 2;
        return true;
    }
}
// The names of the modes the decoder knows, for the page.
export const SSTV_MODES = [
    "Martin 1", "Martin 2", "Scottie 1", "Scottie 2", "Scottie DX", "Robot 36", "Robot 72",
    "PD 50", "PD 90", "PD 120", "PD 160", "PD 180", "PD 240", "PD 290", "Wraase SC2-180",
];
const BLOCK = 960; // samples demodulated at once (20 ms at 48 kHz)
export class SstvDecoder {
    constructor(sampleRate) {
        this.sampleRate = sampleRate;
        // A picture begins; its lines follow; it is complete (or replaced).
        this.onPicture = () => { };
        // `rows` lines from `first`, `width` pixels each, RGBA.
        this.onLines = () => { };
        this.onEnd = () => { };
        this.pixelBuffer = new PixelBuffer();
        this.frequencies = new Float32Array(BLOCK);
        this.visBitHz = new Float32Array(10);
        this.rawMode = null; // no mode: line lengths that fit none
        this.currentSample = 0;
        this.leaderBreakIndex = 0;
        this.lastSyncPulseIndex = 0;
        this.lastFrequencyOffset = 0;
        // The picture being drawn: its mode and the next line; null between pictures.
        this.picture = null;
        this.demodulator = new Demodulator(sampleRate);
        const pulseFilterSamples = Math.round(0.0025 * sampleRate) | 1;
        this.pulseFilterDelay = (pulseFilterSamples - 1) / 2;
        this.pulseFilter = new SimpleMovingAverage(pulseFilterSamples);
        this.scanLineBuffer = new Float32Array(Math.round(7 * sampleRate));
        this.scratchBuffer = new Float32Array(Math.round(1.1 * sampleRate));
        this.leaderToneSamples = Math.round(0.3 * sampleRate);
        this.leaderToneToleranceSamples = Math.round(0.3 * 0.2 * sampleRate);
        this.transitionSamples = Math.round(0.0005 * sampleRate);
        this.visCodeBitSamples = Math.round(0.03 * sampleRate);
        this.visCodeSamples = Math.round(0.3 * sampleRate);
        this.scanLineMinSamples = Math.round(0.05 * sampleRate);
        this.syncPulseToleranceSamples = Math.round(0.03 * sampleRate);
        this.scanLineToleranceSamples = Math.round(0.001 * sampleRate);
        const history = (modes) => ({
            modes,
            pulses: new Int32Array(5),
            lines: new Int32Array(4),
            offsets: new Float32Array(5),
        });
        const robot36 = new Robot36(sampleRate);
        this.history5ms = history([wraaseSc2180(sampleRate), martin("1", 44, 0.146432, sampleRate), martin("2", 40, 0.073216, sampleRate)]);
        this.history9ms = history([
            robot36,
            new Robot72(sampleRate),
            scottie("1", 60, 0.13824, sampleRate),
            scottie("2", 56, 0.088064, sampleRate),
            scottie("DX", 76, 0.3456, sampleRate),
        ]);
        this.history20ms = history([
            new PaulDon("50", 93, 320, 256, 0.09152, sampleRate),
            new PaulDon("90", 99, 320, 256, 0.17024, sampleRate),
            new PaulDon("120", 95, 640, 496, 0.1216, sampleRate),
            new PaulDon("160", 98, 512, 400, 0.195584, sampleRate),
            new PaulDon("180", 96, 640, 496, 0.18304, sampleRate),
            new PaulDon("240", 97, 640, 496, 0.24448, sampleRate),
            new PaulDon("290", 94, 800, 616, 0.2288, sampleRate),
        ]);
        this.currentMode = robot36;
        this.currentScanLineSamples = robot36.scanLineSamples;
    }
    // Audio at `sampleRate`, any length.
    push(samples) {
        for (let from = 0; from < samples.length; from += BLOCK)
            this.process(samples.subarray(from, Math.min(samples.length, from + BLOCK)));
    }
    // Ends the picture being drawn, if any (the operator's "clear", a retune).
    endPicture() {
        if (this.picture === null)
            return;
        this.picture = null;
        this.onEnd();
    }
    process(block) {
        const frequencies = this.frequencies.subarray(0, block.length);
        const detected = this.demodulator.process(block, frequencies);
        let syncPulseIndex = this.currentSample + this.demodulator.pulseOffset;
        for (let j = 0; j < frequencies.length; ++j) {
            this.scanLineBuffer[this.currentSample++] = frequencies[j];
            if (this.currentSample >= this.scanLineBuffer.length) {
                this.shiftSamples(this.currentScanLineSamples);
                syncPulseIndex -= this.currentScanLineSamples;
            }
        }
        if (detected) {
            switch (this.demodulator.pulseWidth) {
                case 5:
                    this.processSyncPulse(this.history5ms, syncPulseIndex);
                    break;
                case 9:
                    this.leaderBreakIndex = syncPulseIndex;
                    this.processSyncPulse(this.history9ms, syncPulseIndex);
                    break;
                case 20:
                    this.leaderBreakIndex = syncPulseIndex;
                    this.processSyncPulse(this.history20ms, syncPulseIndex);
                    break;
            }
        }
        else if (this.handleHeader()) {
            // a new picture, from its VIS code
        }
        else if (this.currentSample > this.lastSyncPulseIndex + Math.floor((this.currentScanLineSamples * 5) / 4)) {
            // No sync pulse where one was due: the line by the timing so far.
            this.decodeLine(this.lastSyncPulseIndex, this.currentScanLineSamples, this.lastFrequencyOffset);
            this.lastSyncPulseIndex += this.currentScanLineSamples;
        }
    }
    shiftSamples(shift) {
        if (shift <= 0 || shift > this.currentSample)
            return;
        this.currentSample -= shift;
        this.leaderBreakIndex -= shift;
        this.lastSyncPulseIndex -= shift;
        for (const history of [this.history5ms, this.history9ms, this.history20ms]) {
            for (let i = 0; i < history.pulses.length; ++i)
                history.pulses[i] -= shift;
        }
        this.scanLineBuffer.copyWithin(0, shift, shift + this.currentSample);
    }
    startPicture(mode, vis) {
        if (this.picture !== null)
            this.onEnd();
        this.picture = { mode, line: 0 };
        this.onPicture({ mode: mode.name, width: mode.width, height: mode.height, vis });
    }
    decodeLine(syncPulseIndex, scanLineSamples, frequencyOffset) {
        const mode = this.currentMode;
        if (mode === null)
            return;
        const okay = mode.decodeScanLine(this.pixelBuffer, this.scratchBuffer, this.scanLineBuffer, syncPulseIndex, scanLineSamples, frequencyOffset);
        if (okay)
            this.copyLines();
    }
    copyLines() {
        const picture = this.picture;
        const buffer = this.pixelBuffer;
        if (picture === null || picture.mode !== this.currentMode || buffer.width !== picture.mode.width)
            return;
        const rows = Math.min(buffer.height, picture.mode.height - picture.line);
        if (rows <= 0)
            return;
        const rgba = new Uint8ClampedArray(rows * buffer.width * 4);
        for (let i = 0; i < rows * buffer.width; ++i) {
            const p = buffer.pixels[i];
            rgba[4 * i] = (p >>> 16) & 0xff;
            rgba[4 * i + 1] = (p >>> 8) & 0xff;
            rgba[4 * i + 2] = p & 0xff;
            rgba[4 * i + 3] = 255;
        }
        this.onLines(picture.line, rows, buffer.width, rgba);
        picture.line += rows;
        if (picture.line >= picture.mode.height) {
            this.picture = null;
            this.onEnd();
        }
    }
    findMode(code) {
        for (const history of [this.history5ms, this.history9ms, this.history20ms]) {
            const mode = history.modes.find((m) => m.visCode === code);
            if (mode !== undefined)
                return [mode, history];
        }
        return null;
    }
    detectMode(modes, line) {
        let best = this.rawMode;
        let bestDist = Number.MAX_SAFE_INTEGER;
        for (const mode of modes) {
            const dist = Math.abs(line - mode.scanLineSamples);
            if (dist <= this.scanLineToleranceSamples && dist < bestDist) {
                bestDist = dist;
                best = mode;
            }
        }
        return best;
    }
    // The leader tone, the break, the leader again and the VIS code.
    handleHeader() {
        const line = this.scanLineBuffer;
        if (this.leaderBreakIndex < this.visCodeBitSamples + this.leaderToneToleranceSamples ||
            this.currentSample < this.leaderBreakIndex + this.leaderToneSamples + this.leaderToneToleranceSamples + this.visCodeSamples + this.visCodeBitSamples) {
            return false;
        }
        const breakPulseIndex = this.leaderBreakIndex;
        this.leaderBreakIndex = 0;
        const tolerance = this.leaderToneToleranceSamples;
        let preBreakFreq = 0;
        for (let i = 0; i < tolerance; ++i)
            preBreakFreq += line[breakPulseIndex - this.visCodeBitSamples - tolerance + i];
        const leaderToneHz = 1900;
        const centerHz = 1900;
        const toleranceHz = 50;
        const halfBandWidth = 400;
        preBreakFreq = (preBreakFreq * halfBandWidth) / tolerance + centerHz;
        if (Math.abs(preBreakFreq - leaderToneHz) > toleranceHz)
            return false;
        let leaderFreq = 0;
        for (let i = this.transitionSamples; i < this.leaderToneSamples - tolerance; ++i)
            leaderFreq += line[breakPulseIndex + i];
        const leaderFreqOffset = leaderFreq / (this.leaderToneSamples - this.transitionSamples - tolerance);
        leaderFreq = leaderFreqOffset * halfBandWidth + centerHz;
        if (Math.abs(leaderFreq - leaderToneHz) > toleranceHz)
            return false;
        const stopBitHz = 1200;
        const pulseThreshold = ((stopBitHz + leaderToneHz) / 2 - centerHz) / halfBandWidth;
        let visBegin = breakPulseIndex + this.leaderToneSamples - tolerance;
        let visEnd = breakPulseIndex + this.leaderToneSamples + tolerance + this.visCodeBitSamples;
        for (let i = 0; i < this.pulseFilter.length; ++i)
            this.pulseFilter.avg(line[visBegin++] - leaderFreqOffset);
        while (++visBegin < visEnd)
            if (this.pulseFilter.avg(line[visBegin] - leaderFreqOffset) < pulseThreshold)
                break;
        if (visBegin >= visEnd)
            return false;
        visBegin -= this.pulseFilterDelay;
        visEnd = visBegin + this.visCodeSamples;
        const bits = this.visBitHz;
        bits.fill(0);
        for (let j = 0; j < 10; ++j) {
            for (let i = this.transitionSamples; i < this.visCodeBitSamples - this.transitionSamples; ++i) {
                bits[j] += line[visBegin + this.visCodeBitSamples * j + i] - leaderFreqOffset;
            }
        }
        for (let i = 0; i < 10; ++i)
            bits[i] = (bits[i] * halfBandWidth) / (this.visCodeBitSamples - 2 * this.transitionSamples) + centerHz;
        if (Math.abs(bits[0] - stopBitHz) > toleranceHz || Math.abs(bits[9] - stopBitHz) > toleranceHz)
            return false;
        const oneBitHz = 1100;
        const zeroBitHz = 1300;
        for (let i = 1; i < 9; ++i) {
            if (Math.abs(bits[i] - oneBitHz) > toleranceHz && Math.abs(bits[i] - zeroBitHz) > toleranceHz)
                return false;
        }
        let visCode = 0;
        for (let i = 0; i < 8; ++i)
            visCode |= (bits[i + 1] < stopBitHz ? 1 : 0) << i;
        let check = true;
        for (let i = 0; i < 8; ++i)
            check = check !== ((visCode & (1 << i)) !== 0);
        visCode &= 127;
        if (!check)
            return false;
        const syncPorchHz = 1500;
        const syncThreshold = ((SYNC_PULSE_HZ + syncPorchHz) / 2 - centerHz) / halfBandWidth;
        let syncPulseIndex = visEnd - this.visCodeBitSamples;
        const syncPulseMaxIndex = visEnd + this.visCodeBitSamples;
        for (let i = 0; i < this.pulseFilter.length; ++i)
            this.pulseFilter.avg(line[syncPulseIndex++] - leaderFreqOffset);
        while (++syncPulseIndex < syncPulseMaxIndex)
            if (this.pulseFilter.avg(line[syncPulseIndex] - leaderFreqOffset) > syncThreshold)
                break;
        if (syncPulseIndex >= syncPulseMaxIndex)
            return false;
        syncPulseIndex -= this.pulseFilterDelay;
        const found = this.findMode(visCode);
        if (found === null)
            return false; // a mode we do not know
        const [mode, history] = found;
        mode.resetState();
        this.currentMode = mode;
        this.startPicture(mode, true);
        this.lastSyncPulseIndex = syncPulseIndex + mode.firstSyncPulseIndex;
        this.currentScanLineSamples = mode.scanLineSamples;
        this.lastFrequencyOffset = leaderFreqOffset;
        let oldest = this.lastSyncPulseIndex - (history.pulses.length - 1) * this.currentScanLineSamples;
        if (mode.firstSyncPulseIndex > 0)
            oldest -= this.currentScanLineSamples;
        for (let i = 0; i < history.pulses.length; ++i)
            history.pulses[i] = oldest + i * this.currentScanLineSamples;
        history.lines.fill(this.currentScanLineSamples);
        this.shiftSamples(this.lastSyncPulseIndex + mode.firstPixelSampleIndex);
        return true;
    }
    processSyncPulse(history, latestSyncIndex) {
        const { pulses, lines, offsets } = history;
        pulses.copyWithin(0, 1);
        pulses[pulses.length - 1] = latestSyncIndex;
        lines.copyWithin(0, 1);
        lines[lines.length - 1] = pulses[pulses.length - 1] - pulses[pulses.length - 2];
        offsets.copyWithin(0, 1);
        offsets[pulses.length - 1] = this.demodulator.frequencyOffset;
        if (lines[0] === 0)
            return;
        let mean = 0;
        for (const diff of lines)
            mean += diff;
        mean /= lines.length;
        const scanLineSamples = Math.round(mean);
        if (scanLineSamples < this.scanLineMinSamples || scanLineSamples > this.scratchBuffer.length)
            return;
        let variance = 0;
        for (const diff of lines)
            variance += (diff - mean) ** 2;
        if (Math.sqrt(variance / lines.length) > this.scanLineToleranceSamples)
            return;
        let pictureChanged = false;
        if (this.picture !== null) {
            // A picture under way keeps its mode.
            const mode = this.currentMode;
            if (mode !== null && Math.abs(scanLineSamples - mode.scanLineSamples) > this.scanLineToleranceSamples)
                return;
        }
        else {
            const previous = this.currentMode;
            this.currentMode = this.detectMode(history.modes, scanLineSamples);
            pictureChanged =
                this.currentMode !== previous ||
                    Math.abs(this.currentScanLineSamples - scanLineSamples) > this.scanLineToleranceSamples ||
                    Math.abs(this.lastSyncPulseIndex + scanLineSamples - pulses[pulses.length - 1]) > this.syncPulseToleranceSamples;
            // Joined after the VIS code: the sync pulses alone show the mode.
            if (pictureChanged && this.currentMode !== null) {
                this.currentMode.resetState();
                this.startPicture(this.currentMode, false);
            }
        }
        let frequencyOffset = 0;
        for (const offset of offsets)
            frequencyOffset += offset;
        frequencyOffset /= offsets.length;
        if (pulses[0] >= scanLineSamples && pictureChanged) {
            // The lines already in the buffer, before the pulses that showed the mode.
            const endPulse = pulses[0];
            const extrapolate = Math.floor(endPulse / scanLineSamples);
            const firstPulse = endPulse - extrapolate * scanLineSamples;
            for (let pulse = firstPulse; pulse < endPulse; pulse += scanLineSamples)
                this.decodeLine(pulse, scanLineSamples, frequencyOffset);
        }
        for (let i = pictureChanged ? 0 : lines.length - 1; i < lines.length; ++i)
            this.decodeLine(pulses[i], lines[i], frequencyOffset);
        this.lastSyncPulseIndex = pulses[pulses.length - 1];
        this.currentScanLineSamples = scanLineSamples;
        this.lastFrequencyOffset = frequencyOffset;
        const first = this.currentMode?.firstPixelSampleIndex ?? 0;
        this.shiftSamples(this.lastSyncPulseIndex + first);
    }
}
