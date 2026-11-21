// SPDX-License-Identifier: GPL-3.0-or-later
//
// Removes the steady line at the centre of the waterfall (0 Hz of the I/Q:
// DC, hum and low-frequency leakage of the analogue path), for display only.
//
// The remover learns the shape of the line from the lines it is given: for
// each bin near the centre it follows the lower envelope of the level (it
// falls quickly and rises slowly, so a signal passing through, or tuning
// across the centre, barely moves it). The bins further out give the noise
// floor. Where the envelope near the centre stands above that floor, the bin
// belongs to the artefact. Such a bin is replaced by the background noise of
// the same line (a bin 1-2 kHz from the centre), so it looks like the rest of
// the band; a real signal clearly above the artefact's usual level, judged by
// how much the artefact itself flickers, is kept with the artefact
// subtracted as power.
//
// Learning starts again when the remover is switched on (it adapts to the
// conditions of that moment) and whenever the number of bins changes.
// Blanked lines (TX, decisions.md D33) are corrected but never learned from.
// Half-width of the region examined around the centre, in Hz.
const REGION_HZ = 1000;
// Never fewer bins than this on each side.
const MIN_REGION_BINS = 4;
// An envelope this far above the floor counts as an artefact.
const THRESHOLD_DB = 3;
// Envelope tracking per line: falls fast, rises slowly; faster while learning.
const FALL = 0.3;
const RISE_LEARNING = 0.15;
const RISE = 0.01;
// Learning lasts this many receive lines and at least this long.
const LEARN_LINES = 20;
const LEARN_MS = 2000;
// A bin holds a real signal when it stands above the artefact's usual level
// by this many times the artefact's own mean deviation, and at least by
// SIGNAL_MIN_DB. Only values below that mark teach the usual level, so a
// signal sitting on the centre is not learned as the artefact.
const SIGNAL_DEVIATIONS = 3;
const SIGNAL_MIN_DB = 3;
// While learning the first lines, anything up to this teaches the usual level.
const LEARNING_LIMIT_DB = 10;
// The artefact also jumps for single lines (about once a second on the G90,
// 10-15 dB), while a real signal lasts: a bin counts as a signal only after
// this many consecutive lines above the mark.
const SIGNAL_LINES = 2;
// Learning rates of the usual level and deviation per line (after learning:
// a time constant of about 10 s at 20 lines/s).
const TYPICAL_RATE_LEARNING = 0.15;
const TYPICAL_RATE = 0.005;
export class SpikeRemover {
    constructor(now = () => performance.now()) {
        this.now = now;
        this.envelope = null;
        // Usual (mean) level of each bin and its mean deviation, without signals.
        this.typical = null;
        this.deviation = null;
        // Consecutive lines each bin has stood above the signal mark.
        this.aboveRun = null;
        this.seen = 0;
        this.startedMs = 0;
        // Number of bins on each side of the centre that were corrected last time.
        this.lastWidth = 0;
    }
    // Forget what was learned; the next lines are learned from scratch.
    reset() {
        this.envelope = null;
        this.seen = 0;
        this.startedMs = this.now();
    }
    // True until enough receive lines have been seen since the last reset.
    get learning() {
        return this.seen < LEARN_LINES || this.now() - this.startedMs < LEARN_MS;
    }
    // How far above the usual level of bin k a value counts as a signal.
    signalMargin(k) {
        const deviation = this.deviation === null ? 0 : this.deviation[k];
        return Math.max(SIGNAL_MIN_DB, SIGNAL_DEVIATIONS * deviation);
    }
    process(line) {
        const n = line.bins.length;
        if (n < 4 * MIN_REGION_BINS)
            return line;
        const binHz = line.spanHz > 0 ? line.spanHz / n : 0;
        const half = Math.floor(n / 2);
        const region = Math.min(Math.floor(n / 4), Math.max(MIN_REGION_BINS, binHz > 0 ? Math.ceil(REGION_HZ / binHz) : MIN_REGION_BINS));
        const scale = (line.maxDb - line.minDb) / 255;
        if (!(scale > 0))
            return line;
        const db = (i) => line.minDb + line.bins[i] * scale;
        // The envelope covers the region and, on each side, a reference band of
        // the same width further out.
        const first = half - 2 * region;
        const count = 4 * region + 1;
        if (this.envelope === null ||
            this.typical === null ||
            this.deviation === null ||
            this.envelope.length !== count) {
            this.envelope = new Float32Array(count);
            this.typical = new Float32Array(count);
            this.deviation = new Float32Array(count).fill(SIGNAL_MIN_DB / SIGNAL_DEVIATIONS);
            for (let k = 0; k < count; k++)
                this.envelope[k] = this.typical[k] = db(first + k);
            this.seen = 0;
            this.startedMs = this.now();
        }
        else if (!line.blanked) {
            const rise = this.learning ? RISE_LEARNING : RISE;
            const learning = this.learning;
            const rate = learning ? TYPICAL_RATE_LEARNING : TYPICAL_RATE;
            for (let k = 0; k < count; k++) {
                const value = db(first + k);
                const old = this.envelope[k];
                this.envelope[k] = old + (value - old) * (value < old ? FALL : rise);
                const usual = this.typical[k];
                const limit = learning ? LEARNING_LIMIT_DB : this.signalMargin(k);
                if (value < usual + limit) {
                    this.typical[k] = usual + (value - usual) * rate;
                    this.deviation[k] += (Math.abs(value - usual) - this.deviation[k]) * rate;
                }
            }
        }
        if (!line.blanked)
            this.seen++;
        const env = this.envelope;
        const typical = this.typical;
        const reference = [];
        const current = [];
        for (let k = 0; k < region; k++) {
            reference.push(env[k], env[count - 1 - k]);
            current.push(db(first + k), db(first + count - 1 - k));
        }
        const floorEnv = median(reference);
        const floorNow = median(current);
        // The artefact: bins from the centre outwards while the envelope stays
        // above the floor; a gap ends it on that side.
        const excess = new Float32Array(2 * region + 1); // index region = centre
        let width = 0;
        for (const side of [-1, 1]) {
            for (let d = side < 0 ? 1 : 0; d <= region; d++) {
                const k = 2 * region + side * d;
                const above = env[k] - floorEnv;
                if (above < THRESHOLD_DB) {
                    if (d > 0)
                        break;
                    continue; // the centre bin alone may be clean while its neighbours are not
                }
                excess[region + side * d] = above;
                width = Math.max(width, d);
            }
        }
        this.lastWidth = width;
        if (width === 0)
            return line;
        if (this.aboveRun === null || this.aboveRun.length !== 2 * region + 1) {
            this.aboveRun = new Uint8Array(2 * region + 1);
        }
        const run = this.aboveRun;
        for (let j = 0; j <= 2 * region; j++) {
            const above = db(half - region + j) > typical[region + j] + this.signalMargin(region + j);
            run[j] = above ? Math.min(255, run[j] + 1) : 0;
        }
        const bins = new Uint8Array(line.bins);
        const floorLin = 10 ** (floorNow / 10);
        for (let j = 0; j <= 2 * region; j++) {
            if (excess[j] === 0)
                continue;
            const i = half - region + j;
            const d = j - region;
            // Background of this line at the same distance in the reference band;
            // a signal there is not copied: the median floor stands in for it.
            const source = half + (d < 0 ? -1 : 1) * (region + 1 + Math.abs(d));
            const sample = source >= 0 && source < n ? db(source) : floorNow;
            const background = sample > floorNow + 2 * SIGNAL_MIN_DB ? floorNow : sample;
            let value = background;
            const usual = typical[region + j];
            if (run[j] >= SIGNAL_LINES) {
                // A real signal: subtract the artefact's usual power, never below the background.
                const spike = 10 ** (usual / 10) - floorLin;
                value = 10 * Math.log10(Math.max(10 ** (background / 10), 10 ** (db(i) / 10) - spike));
            }
            bins[i] = Math.round(Math.min(255, Math.max(0, (value - line.minDb) / scale)));
        }
        return { ...line, bins };
    }
}
function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
