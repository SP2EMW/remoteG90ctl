// SPDX-License-Identifier: GPL-3.0-or-later
//
// remoteG90ctl browser frontend, stage 2: spectrum and waterfall from the
// radio's I/Q output, following the VFO; click to tune, frequency and mode.
//
// Messages on /ws are the server protocol (docs/server-protocol.md); waterfall
// lines are binary frames, decoded by parseLine(). Ideas from OpenWebRX
// (AGPL-3.0, ideas only: one line drawn per frame, scrolled rather than
// redrawn) and SDR++ (zoom as a window of the band, max of merged bins), see
// docs/third-party.md. No code is copied.
import { FRAME_AUDIO, RxAudio, TxAudio } from "./audio.js";
import { CYCLE_MS, CycleCollector, cycleIndex, cycleDepth, cycleLabel, cycleNumber, DECODE_BAND_HZ, DIAL_HZ, atDial, fitRange, FRAME_CLOCK, kind, Sequencer, sender, gridOf, SIGNAL_WIDTH_HZ, viewRange, } from "./ft8.js";
import { EQ_BANDS, isPresetName, parseCustom, presetShown, TX_DSP_DEFAULTS, toCustom, withPreset, } from "./txdsp.js";
import { DEFAULT_COLUMNS, columnTemplate, dragColumns, parseColumns } from "./columns.js";
import { DEFAULT_MACROS, expandMacro, RTTY_MARK_HZ, RTTY_MARK_MAX_HZ, RTTY_MARK_MIN_HZ, RTTY_SHIFT_HZ, RttyDecoder, sendable, } from "./rtty.js";
import { CwSkimmer } from "./cw.js";
import { makeFloating } from "./floating.js";
import { numberField } from "./number-field.js";
import { passbandWithEdge } from "./passband.js";
import { dialFor, findSignals, maxHold, nextSignal, nextView } from "./seek.js";
import { SHORTCUTS, actionFor, adjacentBand, givesFocusBack, gridStep, nextAgc, rfGainStep, typingTarget, } from "./keys.js";
import { SpikeRemover } from "./spike.js";
const PROTOCOL_VERSION = 1;
const FRAME_WATERFALL = 0x01;
const HEADER_BYTES = 28;
const HISTORY_LINES = 1000;
// The spectrum above the waterfall is redrawn at most this often (ms); the
// waterfall itself takes every line. Under the 100 ms of 10 lines/s, so that
// jitter never skips lines there; at 20 lines/s it halves the work.
const SPECTRUM_LINE_MS = 80;
const MAX_ZOOM = 32;
// Fully zoomed out the view leaves out this much at each edge of the band:
// the card's filter makes the edges unreadable (AGENTS.md section 8, item 1).
// They can still be reached by dragging.
const EDGE_HZ = 10000;
// Span of the frequency scale without an I/Q input and without [iq] sample_rate.
const SCALE_ONLY_SPAN_HZ = 96000;
// While the I/Q input is not running, the page asks the server again this often.
const IQ_RETRY_MS = 5000;
// A pointer moving less than this (CSS pixels) between down and up is a click.
const CLICK_PX = 5;
const MODES = ["LSB", "USB", "L-D", "U-D", "CW", "CW-R", "AM", "FM"];
// Modes the page can switch to (owner's choice, 2026-11-08); the rest stay in
// the list, greyed out, so a mode set on the radio's panel is still shown.
const SELECTABLE_MODES = new Set(["LSB", "USB", "L-D", "U-D"]);
// Amateur bands the G90 transmits on, IARU Region 1 edges in Hz (Poland's 60 m
// allocation). Picking a band tunes to the last frequency used there in this
// browser, or to the middle of the band (owner's request).
const BANDS = [
    ["160m", 1810000, 2000000],
    ["80m", 3500000, 3800000],
    ["60m", 5351500, 5366500],
    ["40m", 7000000, 7200000],
    ["30m", 10100000, 10150000],
    ["20m", 14000000, 14350000],
    ["17m", 18068000, 18168000],
    ["15m", 21000000, 21450000],
    ["12m", 24890000, 24990000],
    ["10m", 28000000, 29700000],
];
const BAND_MEMORY_KEY = "remoteg90ctl.bandMemory";
// The last mode set on each band (owner's request 2026-11-15), next to the
// last frequency: a band change brings both back.
const BAND_MODES_KEY = "remoteg90ctl.bandModes";
// After a band change that sends the band's mode, readings of the old mode are
// not remembered for the new band until the radio reports the mode sent (a busy
// CAT queue may delay it past a fixed wait; audit 2026-11-15), and at most this
// long (the radio may refuse the mode, e.g. while transmitting).
const BAND_MODE_SETTLE_MS = 10000;
export function parseLine(buffer) {
    if (buffer.byteLength < HEADER_BYTES)
        return null;
    const view = new DataView(buffer);
    if (view.getUint8(0) !== FRAME_WATERFALL)
        return null;
    const count = view.getUint16(2, true);
    if (buffer.byteLength !== HEADER_BYTES + count)
        return null;
    const center = view.getFloat64(8, true);
    return {
        seq: view.getUint32(4, true),
        calibrated: (view.getUint8(1) & 0x01) !== 0,
        blanked: (view.getUint8(1) & 0x02) !== 0,
        centerHz: Number.isNaN(center) ? null : center,
        spanHz: view.getFloat32(16, true),
        minDb: view.getFloat32(20, true),
        maxDb: view.getFloat32(24, true),
        bins: new Uint8Array(buffer, HEADER_BYTES, count),
    };
}
// Palette: dark blue -> blue -> cyan -> yellow -> red -> white (our own stops).
const STOPS = [
    [0.0, 0, 0, 20],
    [0.2, 0, 20, 120],
    [0.45, 0, 140, 200],
    [0.65, 230, 220, 40],
    [0.85, 230, 40, 20],
    [1.0, 255, 255, 255],
];
function buildPalette() {
    const lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = i / 255;
        let k = 0;
        while (k < STOPS.length - 2 && t > STOPS[k + 1][0])
            k++;
        const [t0, r0, g0, b0] = STOPS[k];
        const [t1, r1, g1, b1] = STOPS[k + 1];
        const f = (t - t0) / (t1 - t0);
        lut[i * 3] = r0 + (r1 - r0) * f;
        lut[i * 3 + 1] = g0 + (g1 - g0) * f;
        lut[i * 3 + 2] = b0 + (b1 - b0) * f;
    }
    return lut;
}
const PALETTE = buildPalette();
function el(id) {
    const found = document.getElementById(id);
    if (found === null)
        throw new Error(`missing #${id}`);
    return found;
}
const PLAN_STRIP_PX = 5;
const PLAN_COLOURS = {
    cw: "#3d6fd1",
    narrow: "#9a5ad6",
    all: "#2f9a55",
    beacon: "#d03a3a",
    satellite: "#c99a2e",
    fm: "#2aa6a6",
    guard: "#d03a3a",
};
const PLAN_KINDS = {
    cw: "CW",
    narrow: "narrow band modes, digimodes",
    all: "all modes",
    beacon: "beacons only",
    satellite: "satellite",
    fm: "FM",
    guard: "guard band, no transmission",
};
const MARK_COLOURS = {
    mine: "#ff8080",
    cq: "#7dff9a",
    other: "#e8eef6",
    tx: "#ffb070",
    rtty: "#ffb070",
};
class Waterfall {
    get radioHz() {
        return this.dialHz;
    }
    set radioHz(hz) {
        this.dialHz = hz;
        if (this.scaleOnlySpan !== null)
            this.drawScaleOnly();
    }
    get passband() {
        return this.band_;
    }
    set passband(band) {
        this.band_ = band;
        if (this.scaleOnlySpan !== null)
            this.drawScaleOnly();
    }
    setDigitalBand(band) {
        this.digitalBand = band;
        if (this.scaleOnlySpan !== null)
            this.drawScaleOnly();
        else if (this.history.length)
            this.drawSpectrum(this.history[0]);
    }
    shownBand() {
        return this.digitalBand ?? this.band_;
    }
    // Scale only (spanHz), or back to the waterfall (null).
    setScaleOnly(spanHz) {
        if (spanHz === this.scaleOnlySpan)
            return;
        this.scaleOnlySpan = spanHz;
        document.body.classList.toggle("scale-only", spanHz !== null);
        this.history = [];
        this.resize(); // the canvases change size with the layout
        if (spanHz !== null)
            this.drawScaleOnly();
    }
    // Why the I/Q input does not run, over the waterfall; null hides it.
    showIqError(text) {
        this.iqError.hidden = text === null;
        this.iqError.textContent = text ?? "";
    }
    // The scale around the dial, from a line without bins.
    drawScaleOnly() {
        const span = this.scaleOnlySpan;
        if (span === null || this.dialHz === null)
            return;
        const line = {
            seq: 0,
            calibrated: true,
            blanked: false,
            centerHz: this.dialHz,
            spanHz: span,
            minDb: 0,
            maxDb: 1,
            bins: new Uint8Array(0),
        };
        this.useSpan(span);
        this.history = [line];
        this.drawScale(line);
    }
    // Fully zoomed out shows the band without EDGE_HZ at each side.
    useSpan(spanHz) {
        const minZoom = spanHz > 4 * EDGE_HZ ? spanHz / (spanHz - 2 * EDGE_HZ) : 1;
        if (minZoom === this.minZoom)
            return;
        // First line, or another span: start fully zoomed out, without the edges.
        const zoomedOut = this.zoom <= this.minZoom;
        this.minZoom = minZoom;
        if (zoomedOut || this.zoom < minZoom)
            this.setView(minZoom, this.pan);
    }
    constructor() {
        this.spectrum = el("spectrum");
        this.scale = el("scale");
        this.fall = el("waterfall");
        this.banner = el("uncalibrated");
        this.iqError = el("iq-error");
        this.history = [];
        // Without an I/Q input ([iq] source = "none" or no [iq]): the span of the
        // scale, which is then all that is shown; null with an I/Q input.
        this.scaleOnlySpan = null;
        this.zoom = 1; // visible part of the band = 1 / zoom
        // Smallest zoom: the band without EDGE_HZ at each side (set from the span).
        this.minZoom = 1;
        this.pan = 0; // centre of the view, as a fraction of the span from the I/Q centre
        this.lowDb = -100;
        this.highDb = -40;
        this.autoLevels = true;
        this.dialHz = null;
        // Receive passband of the current mode around the dial, from the server's
        // [iq.passband_hz] (the G90 does not report its filter over CAT).
        this.band_ = null;
        // While FT8/FT4 runs: the range the decoder searches, shown instead of the
        // receive filter (the radio's filter is not changed); null: the filter again.
        this.digitalBand = null;
        this.band = el("passband");
        this.txBand = el("tx-band");
        // RTTY: the mark and space tones as orange lines over the waterfall.
        this.toneLines = [el("rtty-line-0"), el("rtty-line-1")];
        // Called with the frequency under a click or tap.
        this.onTune = null;
        // Called with the frequency under a right click; true when it was used
        // (then the browser's menu does not open). RTTY sets its tones with it.
        this.onRightClickHz = null;
        // Removes the steady line at the centre (display only); null when off.
        this.spikeRemover = null;
        // FT8/FT4 senders of the last cycle, at their radio frequency (stage 6).
        this.marks = [];
        // Lines kept while the page was hidden, not drawn yet.
        this.stale = false;
        this.spectrumAt = 0;
        // What the scale was drawn for: it is redrawn only when that changes.
        this.scaleDrawn = "";
        this.bandplanVersion = 0;
        // Levels from the line itself: noise at the 20th percentile, top from the
        // peak. The bins are bytes and dB grows with them, so a count of each value
        // gives both without sorting.
        this.levelCounts = new Uint32Array(256);
        // Visible bins of a line, merged or stretched to `width` pixels (max of merged bins).
        this.visibleOf = null;
        this.row = null;
        // A range to zoom to once the radio is there (FT8/FT4 switched on): applied by the
        // first line whose centre is near it, cancelled by the operator's own gesture.
        this.fit = null;
        this.zoomBefore = null;
        // IARU band plan of the account's ITU region and the region's amateur bands
        // (D54), drawn as a strip at the top of the scale. Display only: the server
        // refuses transmissions outside the bands.
        this.bandplan = null;
        document.addEventListener("visibilitychange", () => {
            if (document.hidden || !this.stale)
                return;
            this.stale = false;
            this.redraw(); // the lines that came while hidden, at once
        });
        window.addEventListener("resize", () => this.resize());
        // The waterfall's height changes with the drag handle, not only the window.
        new ResizeObserver(() => this.resize()).observe(el("fall-wrap"));
        this.resize();
        this.installGestures();
        this.scale.addEventListener("mousemove", (e) => {
            const hz = this.scaleHz(e.offsetX);
            this.scale.title = hz === null ? "" : this.planAt(hz);
        });
    }
    setLevels(low, high) {
        this.lowDb = Math.min(low, high - 3);
        this.highDb = Math.max(high, low + 3);
        this.redraw();
    }
    levels() {
        return [this.lowDb, this.highDb];
    }
    // The newest received lines (newest first), for seek; none in scale-only mode.
    recentLines(count) {
        return this.history.slice(0, count);
    }
    add(received) {
        if (this.scaleOnlySpan !== null)
            return;
        this.showIqError(null); // lines come: the input runs
        const line = this.spikeRemover?.process(received) ?? received;
        this.useSpan(received.spanHz);
        this.history.unshift(line);
        this.applyFit(line);
        if (this.history.length > HISTORY_LINES)
            this.history.pop();
        // Blanked lines (TX, switching clicks) would pull the levels away.
        if (this.autoLevels && !line.blanked)
            this.autoLevel(line);
        this.banner.hidden = line.calibrated;
        // A hidden page keeps the lines and draws them all when it is shown again.
        if (document.hidden) {
            this.stale = true;
            return;
        }
        this.scrollAndDraw(line);
        // The spectrum at most SPECTRUM_LINE_MS apart; the waterfall gets every line.
        const now = performance.now();
        if (now - this.spectrumAt >= SPECTRUM_LINE_MS) {
            this.spectrumAt = now;
            this.drawSpectrum(line);
        }
        if (this.scaleKey(line) !== this.scaleDrawn)
            this.drawScale(line);
    }
    scaleKey(line) {
        const { width, height } = this.scale;
        return [line.centerHz, line.spanHz, this.zoom, this.pan, width, height, this.bandplanVersion].join("|");
    }
    autoLevel(line) {
        const counts = this.levelCounts;
        counts.fill(0);
        for (const v of line.bins)
            counts[v]++;
        const rank = Math.floor(line.bins.length * 0.2);
        let seen = 0;
        let noiseValue = 0;
        for (let v = 0; v < 256; v++) {
            seen += counts[v];
            if (seen > rank) {
                noiseValue = v;
                break;
            }
        }
        let peakValue = 255;
        while (peakValue > 0 && counts[peakValue] === 0)
            peakValue--;
        const noise = this.toDb(line, noiseValue);
        const peak = this.toDb(line, peakValue);
        const low = noise - 6;
        const high = Math.max(noise + 30, peak + 3);
        this.lowDb += (low - this.lowDb) * 0.05;
        this.highDb += (high - this.highDb) * 0.05;
    }
    toDb(line, value) {
        return line.minDb + (value / 255) * (line.maxDb - line.minDb);
    }
    visible(line, width) {
        // The waterfall and the spectrum ask for the same line: computed once.
        const last = this.visibleOf;
        if (last !== null && last.line === line && last.width === width && last.zoom === this.zoom && last.pan === this.pan) {
            return last.out;
        }
        const n = line.bins.length;
        const first = (0.5 + this.pan - 0.5 / this.zoom) * n;
        const per = n / this.zoom / width;
        const out = last !== null && last.out.length === width ? last.out : new Float32Array(width);
        this.visibleOf = { line, width, zoom: this.zoom, pan: this.pan, out };
        for (let x = 0; x < width; x++) {
            const a = Math.floor(first + x * per);
            const b = Math.max(a + 1, Math.floor(first + (x + 1) * per));
            let best = 0;
            for (let i = Math.max(0, a); i < Math.min(n, b); i++)
                best = Math.max(best, line.bins[i]);
            out[x] = this.toDb(line, best);
        }
        return out;
    }
    rowPixels(line, width) {
        // One row reused: putImageData copies it at once.
        if (this.row === null || this.row.width !== width)
            this.row = new ImageData(width, 1);
        const row = this.row;
        const values = this.visible(line, width);
        const range = this.highDb - this.lowDb;
        const dim = line.blanked ? 0.3 : 1;
        for (let x = 0; x < width; x++) {
            const t = Math.min(1, Math.max(0, (values[x] - this.lowDb) / range));
            const c = Math.round(t * 255) * 3;
            row.data[x * 4] = PALETTE[c] * dim;
            row.data[x * 4 + 1] = PALETTE[c + 1] * dim;
            row.data[x * 4 + 2] = PALETTE[c + 2] * dim;
            row.data[x * 4 + 3] = 255;
        }
        return row;
    }
    scrollAndDraw(line) {
        const ctx = this.fall.getContext("2d");
        if (ctx === null)
            return;
        const { width, height } = this.fall;
        ctx.drawImage(this.fall, 0, 0, width, height - 1, 0, 1, width, height - 1);
        ctx.putImageData(this.rowPixels(line, width), 0, 0);
    }
    redraw() {
        if (this.scaleOnlySpan !== null) {
            if (this.history.length)
                this.drawScale(this.history[0]);
            return;
        }
        const ctx = this.fall.getContext("2d");
        if (ctx === null)
            return;
        const { width, height } = this.fall;
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, width, height);
        this.history.slice(0, height).forEach((line, y) => ctx.putImageData(this.rowPixels(line, width), 0, y));
        if (this.history.length) {
            this.drawSpectrum(this.history[0]);
            this.drawScale(this.history[0]);
        }
    }
    drawSpectrum(line) {
        const ctx = this.spectrum.getContext("2d");
        if (ctx === null)
            return;
        const { width, height } = this.spectrum;
        ctx.fillStyle = "#05070d";
        ctx.fillRect(0, 0, width, height);
        const top = this.highDb + 10;
        const bottom = this.lowDb - 5;
        const y = (db) => ((top - db) / (top - bottom)) * height;
        ctx.strokeStyle = "#1f2a3a";
        ctx.fillStyle = "#6b7a90";
        ctx.font = `${10 * devicePixelRatio}px sans-serif`;
        for (let db = Math.ceil(bottom / 10) * 10; db <= top; db += 10) {
            ctx.beginPath();
            ctx.moveTo(0, y(db));
            ctx.lineTo(width, y(db));
            ctx.stroke();
            ctx.fillText(`${db}`, 2, y(db) - 2);
        }
        this.drawPassband(ctx, line, height);
        const values = this.visible(line, width);
        ctx.strokeStyle = line.blanked ? "#4a5566" : "#9fe3ff";
        ctx.beginPath();
        values.forEach((db, x) => (x === 0 ? ctx.moveTo(x, y(db)) : ctx.lineTo(x, y(db))));
        ctx.stroke();
        this.drawVfoMark(ctx, line, height);
        this.drawMarks(ctx, line, height);
    }
    setMarks(marks) {
        this.marks = marks;
        if (this.history.length)
            this.drawSpectrum(this.history[0]);
    }
    // Calls written upwards from the bottom of the spectrum, over their signal.
    drawMarks(ctx, line, height) {
        this.txBand.hidden = true;
        for (const tone of this.toneLines)
            tone.hidden = true;
        if (line.centerHz === null || this.marks.length === 0)
            return;
        const width = ctx.canvas.width;
        let toneLine = 0;
        for (const mark of this.marks) {
            if (mark.kind !== "rtty")
                continue;
            // A tone of RTTY: a line through the spectrum and the waterfall.
            const x = this.hzPixel(line, mark.hz, width);
            const tone = this.toneLines[toneLine++];
            if (tone === undefined || x < 0 || x > width)
                continue;
            ctx.fillStyle = MARK_COLOURS.rtty;
            ctx.fillRect(Math.round(x) - devicePixelRatio / 2, 0, devicePixelRatio, height);
            tone.hidden = false;
            tone.style.left = `${(x / width) * 100}%`;
        }
        for (const mark of this.marks) {
            if (mark.widthHz === undefined)
                continue;
            // The band the transmitted signal takes, on the spectrum and over the
            // waterfall; clipped to the view.
            const left = Math.max(0, this.hzPixel(line, mark.hz, width));
            const right = Math.min(width, this.hzPixel(line, mark.hz + mark.widthHz, width));
            if (right <= left)
                continue;
            ctx.fillStyle = "rgba(255, 176, 112, 0.30)";
            ctx.fillRect(left, 0, right - left, height);
            this.txBand.hidden = false;
            this.txBand.style.left = `${(left / width) * 100}%`;
            this.txBand.style.width = `${((right - left) / width) * 100}%`;
        }
        ctx.save();
        ctx.font = `${10 * devicePixelRatio}px sans-serif`;
        ctx.textBaseline = "middle";
        for (const mark of this.marks) {
            const x = this.hzPixel(line, mark.hz, width);
            if (x < 0 || x > width)
                continue;
            ctx.fillStyle = MARK_COLOURS[mark.kind];
            ctx.translate(x, height - 2);
            ctx.rotate(-Math.PI / 2);
            ctx.fillText(mark.text, 0, 0);
            ctx.setTransform(1, 0, 0, 1, 0, 0);
        }
        ctx.restore();
    }
    // Frequency of a pixel column: centre + offset of that column within the view.
    pixelHz(line, x, width) {
        const offset = (this.pan + (x / width - 0.5) / this.zoom) * line.spanHz;
        return (line.centerHz ?? 0) + offset;
    }
    hzPixel(line, hz, width) {
        const offset = (hz - (line.centerHz ?? 0)) / line.spanHz;
        return ((offset - this.pan) * this.zoom + 0.5) * width;
    }
    // Shaded passband on the spectrum, and the same band over the waterfall.
    drawPassband(ctx, line, height) {
        const passband = this.shownBand();
        if (this.radioHz === null || passband === null || line.centerHz === null) {
            this.band.hidden = true;
            return;
        }
        const width = ctx.canvas.width;
        // Clipped to the view, so a zoomed-in band never widens the page.
        const left = Math.max(0, this.hzPixel(line, this.radioHz + passband[0], width));
        const right = Math.min(width, this.hzPixel(line, this.radioHz + passband[1], width));
        if (right <= left) {
            this.band.hidden = true;
            return;
        }
        ctx.fillStyle = "rgba(255, 255, 255, 0.10)";
        ctx.fillRect(left, 0, right - left, height);
        this.band.hidden = false;
        this.band.style.left = `${(left / width) * 100}%`;
        this.band.style.width = `${((right - left) / width) * 100}%`;
    }
    drawVfoMark(ctx, line, height) {
        if (this.radioHz === null || line.centerHz === null)
            return;
        const x = this.hzPixel(line, this.radioHz, ctx.canvas.width);
        ctx.strokeStyle = "#ff5555";
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }
    showRange(low, high, dial) {
        this.zoomBefore ?? (this.zoomBefore = this.zoom);
        this.fit = { low, high, dial };
    }
    // Back to the zoom from before showRange (centred on the VFO).
    releaseRange() {
        this.fit = null;
        if (this.zoomBefore !== null)
            this.setView(this.zoomBefore, 0);
        this.zoomBefore = null;
    }
    applyFit(line) {
        const fit = this.fit;
        if (fit === null || line.centerHz === null)
            return;
        // The radio retunes first: lines of the old frequency must not be used.
        if (!atDial(line.centerHz, fit.dial))
            return;
        const view = fitRange(line.spanHz, line.centerHz, fit.low, fit.high, MAX_ZOOM, this.minZoom);
        this.fit = null;
        this.setView(view.zoom, view.pan);
    }
    setBandplan(plan) {
        this.bandplan = plan;
        this.bandplanVersion++;
        if (this.history.length)
            this.drawScale(this.history[0]);
    }
    // What the band plan says at a frequency, for the scale's tooltip.
    planAt(hz) {
        const plan = this.bandplan;
        if (plan === null || plan.region === null)
            return "";
        const segment = plan.segments.find((s) => s.low <= hz && hz < s.high);
        const inBand = plan.bands.some(([low, high]) => low <= hz && hz <= high);
        const parts = [`IARU Region ${plan.region}`];
        if (segment !== undefined)
            parts.push(`${PLAN_KINDS[segment.kind] ?? segment.kind}${segment.note ? ` (${segment.note})` : ""}`);
        parts.push(inBand ? "" : "outside the region's amateur bands: no transmission");
        return parts.filter((p) => p !== "").join(" — ");
    }
    scaleHz(x) {
        const line = this.history[0];
        if (line === undefined || line.centerHz === null)
            return null;
        return this.pixelHz(line, x * devicePixelRatio, this.scale.width);
    }
    drawPlan(ctx, line, width) {
        const plan = this.bandplan;
        if (plan === null || plan.region === null || line.centerHz === null)
            return;
        const strip = PLAN_STRIP_PX * devicePixelRatio;
        const x = (hz) => Math.min(width, Math.max(0, this.hzPixel(line, hz, width)));
        for (const s of plan.segments) {
            const left = x(s.low);
            const right = x(s.high);
            if (right <= left)
                continue;
            ctx.fillStyle = PLAN_COLOURS[s.kind] ?? "#888";
            ctx.fillRect(left, 0, Math.max(1, right - left), strip);
        }
        // Outside the bands: dimmed, with a red line under the strip ("no TX").
        const edges = [0, ...plan.bands.flatMap(([low, high]) => [x(low), x(high)]), width];
        for (let i = 0; i + 1 < edges.length; i += 2) {
            const left = edges[i];
            const right = edges[i + 1];
            if (right <= left)
                continue;
            ctx.fillStyle = "rgba(11, 16, 24, 0.7)";
            ctx.fillRect(left, 0, right - left, strip);
            ctx.fillStyle = "#c03030";
            ctx.fillRect(left, strip, right - left, 2 * devicePixelRatio);
        }
    }
    drawScale(line) {
        const ctx = this.scale.getContext("2d");
        if (ctx === null)
            return;
        this.scaleDrawn = this.scaleKey(line);
        const { width, height } = this.scale;
        ctx.fillStyle = "#0b1018";
        ctx.fillRect(0, 0, width, height);
        this.drawPlan(ctx, line, width);
        if (this.scaleOnlySpan !== null)
            this.drawDialOnScale(ctx, line, width, height);
        const viewHz = line.spanHz / this.zoom;
        const step = [100, 200, 500, 1000, 2000, 5000, 10000].find((s) => viewHz / s <= 10) ?? 10000;
        const start = Math.ceil(this.pixelHz(line, 0, width) / step) * step;
        ctx.fillStyle = "#c8d3e0";
        ctx.strokeStyle = "#c8d3e0";
        ctx.font = `${10 * devicePixelRatio}px sans-serif`;
        for (let hz = start; hz < this.pixelHz(line, width, width); hz += step) {
            const x = this.hzPixel(line, hz, width);
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height * 0.3);
            ctx.stroke();
            const label = line.centerHz === null
                ? `${(hz / 1000).toFixed(1)}k`
                : (hz / 1e6).toFixed(step >= 1000 ? 3 : 4);
            ctx.fillText(label, x + 2, height - 4);
        }
    }
    // Without the spectrum: the passband and the dial marked on the scale itself.
    drawDialOnScale(ctx, line, width, height) {
        if (this.dialHz === null)
            return;
        const band = this.shownBand();
        if (band !== null) {
            const left = Math.max(0, this.hzPixel(line, this.dialHz + band[0], width));
            const right = Math.min(width, this.hzPixel(line, this.dialHz + band[1], width));
            if (right > left) {
                ctx.fillStyle = "rgba(255, 220, 120, 0.22)";
                ctx.fillRect(left, 0, right - left, height);
            }
        }
        const x = this.hzPixel(line, this.dialHz, width);
        ctx.strokeStyle = "#ff5555";
        ctx.lineWidth = 2 * devicePixelRatio;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
        ctx.lineWidth = 1;
    }
    resize() {
        const ratio = devicePixelRatio;
        for (const canvas of [this.spectrum, this.scale, this.fall]) {
            const rect = canvas.getBoundingClientRect();
            canvas.width = Math.max(1, Math.round(rect.width * ratio));
            canvas.height = Math.max(1, Math.round(rect.height * ratio));
        }
        this.redraw();
    }
    setView(zoom, pan) {
        this.zoom = Math.min(MAX_ZOOM, Math.max(this.minZoom, zoom));
        // Keep the view inside the band; fully zoomed out it still moves by EDGE_HZ.
        const limit = 0.5 - 0.5 / this.zoom;
        this.pan = Math.min(limit, Math.max(-limit, pan));
        this.redraw();
    }
    // Frequency under a client x coordinate, from the newest line.
    hzAt(clientX) {
        const line = this.history[0];
        if (line === undefined || line.centerHz === null)
            return null;
        const rect = this.scale.getBoundingClientRect(); // as wide as the waterfall
        return this.pixelHz(line, clientX - rect.left, rect.width);
    }
    // Mouse: wheel = zoom around the cursor, drag = pan, click = tune.
    // Touch: drag = pan, pinch = zoom, tap = tune.
    installGestures() {
        const area = el("display");
        area.addEventListener("wheel", (e) => {
            e.preventDefault();
            this.fit = null; // the operator's own zoom wins
            const rect = area.getBoundingClientRect();
            const at = (e.clientX - rect.left) / rect.width - 0.5;
            const factor = e.deltaY < 0 ? 1.25 : 0.8;
            const zoom = Math.min(MAX_ZOOM, Math.max(this.minZoom, this.zoom * factor));
            // Keep the frequency under the cursor in place.
            this.setView(zoom, this.pan + at / this.zoom - at / zoom);
        }, { passive: false });
        const pointers = new Map();
        let startZoom = 1;
        let startPan = 0;
        let startSpread = 0;
        let startX = 0;
        // A gesture is a click until it moves or a second pointer joins.
        let click = null;
        area.addEventListener("contextmenu", (e) => {
            const hz = this.hzAt(e.clientX);
            if (hz !== null && this.onRightClickHz?.(hz) === true)
                e.preventDefault();
        });
        area.addEventListener("pointerdown", (e) => {
            this.fit = null;
            area.setPointerCapture(e.pointerId);
            pointers.set(e.pointerId, e.clientX);
            click = pointers.size === 1 && e.button === 0 ? { x: e.clientX } : null;
            startZoom = this.zoom;
            startPan = this.pan;
            const xs = [...pointers.values()];
            startX = xs.reduce((a, b) => a + b, 0) / xs.length;
            startSpread = xs.length === 2 ? Math.abs(xs[0] - xs[1]) : 0;
        });
        area.addEventListener("pointermove", (e) => {
            if (!pointers.has(e.pointerId))
                return;
            pointers.set(e.pointerId, e.clientX);
            if (click !== null) {
                if (Math.abs(e.clientX - click.x) < CLICK_PX)
                    return;
                click = null;
            }
            const width = area.getBoundingClientRect().width;
            const xs = [...pointers.values()];
            const x = xs.reduce((a, b) => a + b, 0) / xs.length;
            let zoom = startZoom;
            if (xs.length === 2 && startSpread > 10)
                zoom = startZoom * (Math.abs(xs[0] - xs[1]) / startSpread);
            this.setView(zoom, startPan - (x - startX) / width / zoom);
        });
        const end = (e) => {
            pointers.delete(e.pointerId);
            if (click !== null && e.type === "pointerup") {
                const hz = this.hzAt(e.clientX);
                if (hz !== null)
                    this.onTune?.(hz);
            }
            click = null;
            startZoom = this.zoom;
            startPan = this.pan;
            const xs = [...pointers.values()];
            startX = xs.length ? xs[0] : 0;
            startSpread = 0;
        };
        area.addEventListener("pointerup", end);
        area.addEventListener("pointercancel", end);
    }
}
class Connection {
    // The connection dot: its colour from data-state, the words in its tooltip.
    showConnection(state, text) {
        this.status.dataset.state = state;
        this.status.title = text;
        this.status.setAttribute("aria-label", text);
        if (state !== "connected") {
            window.clearInterval(this.blinkTimer);
            this.blinkTimer = undefined;
            this.blinkPeriod = null;
            this.status.classList.remove("dim");
        }
    }
    updateBlink() {
        const kbps = (this.received * 8) / 1000;
        this.received = 0;
        const connected = this.status.dataset.state === "connected";
        const blinking = connected && kbps >= 1;
        // Two states switched by a timer: a smooth CSS animation made the browser
        // and the desktop compositor produce a frame at display rate, always.
        // The timer runs on across these updates; restarted only for a new rate.
        const period = blinking
            ? Math.round(500 / Math.min(BLINK_MAX_HZ, Math.max(BLINK_MIN_HZ, kbps / KBPS_PER_HZ)))
            : null;
        if (period !== this.blinkPeriod) {
            window.clearInterval(this.blinkTimer);
            this.blinkTimer = undefined;
            this.blinkPeriod = period;
            if (period === null)
                this.status.classList.remove("dim");
            else
                this.blinkTimer = window.setInterval(() => this.status.classList.toggle("dim"), period);
        }
        if (connected)
            this.status.title = `connected, ${kbps.toFixed(0)} kbit/s from the server`;
    }
    constructor(view, radio, resources, systemLog) {
        this.view = view;
        this.radio = radio;
        this.resources = resources;
        this.systemLog = systemLog;
        this.ws = null;
        this.nextId = 1;
        this.delay = 1000;
        this.status = el("connection");
        // Bytes received since the last blink update.
        this.received = 0;
        this.blinkPeriod = null;
        // Receive audio frames, and what to redo after (re)connecting.
        this.onAudio = () => { };
        this.onOpen = () => { };
        // Every text message, before the radio display handles it (transmit state).
        this.onText = () => { };
        this.onClose = () => { };
        // Waterfall speed (fast ... slowest, shown as lines per second): the server
        // merges lines for slower speeds, so less data crosses a slow link. Kept in
        // this browser only.
        this.speedSelect = el("waterfall-speed");
        // Connects only while logged in (decisions.md D48): the server refuses /ws
        // without a session. Before each reconnection the session is checked again.
        this.running = false;
        // Set by the account UI: true while logged in, false when not (it shows the
        // login); null when the server does not answer, so the page tries again.
        this.checkSession = async () => true;
        // Paused from the header (owner's request 2026-11-14): like "off", without
        // changing the chosen speed.
        this.paused = false;
        // A request whose answer goes to the caller only (not to the radio display).
        this.pending = new Map();
        // Called with events the page's other parts do not handle (e.g. log_changed).
        this.onEvent = () => { };
        // More listeners of events (FT8/FT4 transmission).
        this.eventHandlers = [];
        this.speedSelect.value = loadSetting(SPEED_KEY) ?? "fast";
        if (this.speedSelect.value === "")
            this.speedSelect.value = "fast";
        this.speedSelect.addEventListener("change", () => {
            saveSetting(SPEED_KEY, this.speedSelect.value);
            this.subscribe();
        });
        window.setInterval(() => this.updateBlink(), 1000);
    }
    start() {
        if (this.running)
            return;
        this.running = true;
        this.delay = 1000;
        this.open();
    }
    stop() {
        this.running = false;
        this.ws?.close();
    }
    setWaterfallPaused(paused) {
        this.paused = paused;
        el("waterfall-paused").hidden = !paused;
        if (this.ws?.readyState === WebSocket.OPEN)
            this.subscribe();
    }
    // "off" (to save the link): no subscription, so the server sends no lines.
    subscribe() {
        const off = this.speedSelect.value === "off";
        el("waterfall-off").hidden = !off || this.paused;
        if (off || this.paused) {
            this.send("waterfall_unsubscribe");
            return;
        }
        const width = el("waterfall").width;
        let bins = 256;
        while (bins < width && bins < 4096)
            bins *= 2;
        this.send("waterfall_subscribe", { bins, speed: this.speedSelect.value });
    }
    open() {
        // wss behind an HTTPS proxy (tailscale serve, D38), ws on loopback.
        const scheme = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${scheme}://${location.host}/ws`);
        ws.binaryType = "arraybuffer";
        this.ws = ws;
        this.showConnection("connecting", "connecting…");
        ws.onopen = () => {
            this.delay = 1000;
            this.showConnection("connected", "connected");
            this.subscribe();
            this.send("status");
            this.send("get_frequency");
            this.send("get_mode");
            this.send("get_atu");
            this.send("get_vfo");
            this.send("get_front_end");
            this.send("get_agc");
            this.send("get_nb");
            this.send("get_rf_gain");
            this.send("get_s_meter");
            void this.request("get_bandplan").then((answer) => {
                if (answer.ok === true)
                    this.view.setBandplan(answer);
            });
            this.resources.connected();
            this.systemLog.connected();
            this.onOpen();
        };
        ws.onmessage = (e) => {
            this.received += e.data instanceof ArrayBuffer ? e.data.byteLength : String(e.data).length;
            if (e.data instanceof ArrayBuffer) {
                const type = new DataView(e.data).getUint8(0);
                if (type === FRAME_AUDIO || type === FRAME_CLOCK) {
                    this.onAudio(e.data);
                    return;
                }
                const line = parseLine(e.data);
                if (line !== null)
                    this.view.add(line);
                return;
            }
            const message = JSON.parse(String(e.data));
            const waiting = typeof message.id === "number" ? this.pending.get(message.id) : undefined;
            if (waiting !== undefined) {
                this.pending.delete(message.id);
                waiting(message);
                return;
            }
            if (typeof message.event === "string") {
                this.onEvent(message);
                for (const handler of this.eventHandlers)
                    handler(message);
            }
            this.onText(message);
            if (this.systemLog.handle(message))
                return;
            if (message.event === "system_stats")
                this.resources.show(message);
            else
                this.radio.handle(message);
        };
        ws.onclose = () => {
            this.onClose();
            this.ws = null;
            for (const resolve of this.pending.values()) {
                resolve({ ok: false, error: "disconnected", message: "the connection to the server broke" });
            }
            this.pending.clear();
            if (!this.running) {
                this.showConnection("disconnected", "logged out");
                return;
            }
            this.showConnection("disconnected", "disconnected — retrying");
            this.reconnectLater();
        };
    }
    // After the delay: check the session, then open again. A server that does not
    // answer (a phone just unlocked, its network not back yet) is not a logout:
    // try again later instead of asking for the password.
    reconnectLater() {
        setTimeout(() => {
            void this.checkSession().then((loggedIn) => {
                if (!this.running)
                    return;
                if (loggedIn === null)
                    this.reconnectLater();
                else if (loggedIn)
                    this.open();
                else
                    this.running = false;
            });
        }, this.delay);
        this.delay = Math.min(10000, this.delay * 2);
    }
    // Bytes queued in the browser for this connection, not yet sent.
    get bufferedAmount() {
        return this.ws?.bufferedAmount ?? 0;
    }
    sendBinary(data) {
        if (this.ws?.readyState === WebSocket.OPEN)
            this.ws.send(data);
    }
    // Returns the request's id (0 when the connection is not open).
    send(type, fields = {}) {
        if (this.ws?.readyState !== WebSocket.OPEN)
            return 0;
        const id = this.nextId++;
        this.ws.send(JSON.stringify({ v: PROTOCOL_VERSION, id, type, ...fields }));
        return id;
    }
    request(type, fields = {}) {
        return new Promise((resolve) => {
            if (this.ws?.readyState !== WebSocket.OPEN) {
                resolve({ ok: false, error: "disconnected", message: "not connected to the server" });
                return;
            }
            const id = this.nextId++;
            this.pending.set(id, resolve);
            this.ws.send(JSON.stringify({ v: PROTOCOL_VERSION, id, type, ...fields }));
        });
    }
}
class Radio {
    get modeName() {
        return this.currentMode;
    }
    get frequencyHz() {
        return this.view.radioHz;
    }
    constructor(view) {
        this.view = view;
        this.frequency = el("frequency");
        this.mode = el("mode");
        this.passbands = {};
        this.currentMode = null;
        this.serverPassband = null;
        // Passbands set with the sliders, per mode, kept in this browser only. The
        // G90 neither reports nor accepts its filter over CAT (decisions.md D37).
        this.overrides = loadOverrides();
        this.filterControls = el("filter-controls");
        this.filterLow = el("filter-low");
        this.filterHigh = el("filter-high");
        this.filterValue = el("filter-value");
        this.freqInput = el("freq-input");
        this.modeSelect = el("mode-select");
        this.bandSelect = el("band");
        this.fineTune = el("fine-tune");
        this.fineTuneValue = el("fine-tune-value");
        this.bandMemory = loadBandMemory();
        this.bandModes = loadBandModes();
        this.awaitedMode = null;
        this.stepSelect = el("tune-step");
        this.atuOn = el("atu-on");
        // VFO A/B and split (stage 5): the selected VFO's letter, the other VFO below.
        this.vfoName = el("vfo-name");
        this.vfoOther = el("vfo-other");
        this.vfoSwitch = el("vfo-switch");
        this.splitOn = el("split-on");
        this.splitBadge = el("split-badge");
        this.vfo = null;
        this.otherHz = null;
        this.split = false;
        this.transmitting = false;
        // Preamplifier / attenuator: "off", "pre" or "att" ("" until the radio answers).
        this.frontEnd = el("front-end");
        this.frontEndBadge = el("front-end-badge");
        this.agc = el("agc");
        this.agcBadge = el("agc-badge");
        this.rfGain = el("rf-gain-input");
        this.rfGainShown = null;
        this.rfGainInfo = el("rf-gain");
        this.sMeter = el("s-meter");
        this.sMeterText = el("s-meter-text");
        this.sMeterBar = el("s-meter-bar");
        this.nbOn = el("nb-on");
        this.nbLevel = el("nb-level");
        this.nbBadge = el("nb-badge");
        // Tuner state above the waterfall: on, off, or unknown until the radio answers.
        this.atuBadge = el("atu-badge");
        this.atuTune = el("atu-tune");
        this.txBar = el("tx-bar");
        this.txWarning = el("tx-warning");
        this.txSuspect = el("tx-suspect");
        this.power = el("power");
        // Setting the RF power (D44): enabled when the server has safety.max_rf_power_w.
        this.powerInput = el("power-input");
        // Watts the radio reported last, and watts sent since; neither is sent again.
        this.powerShown = null;
        this.powerSent = null;
        this.powerSet = el("power-set");
        this.swrWarning = el("swr-warning");
        this.lastAlc = null; // display, level; while transmitting
        // Set by Connection: sends a request to the server.
        this.send = () => { };
        // The CW skimmer (D62): a narrow receive filter around an audio tone; the
        // filter from before is kept for restoreWide. Only in the sideband modes.
        this.beforeNarrow = null;
        // A click or tap on the waterfall or scale: FT8/FT4 may take it (the TX
        // tone) while it runs; otherwise the radio is tuned there.
        this.onClickHz = null;
        // A band chosen on the page (list, Alt + arrows): true when FT8/FT4 reception
        // took it over (owner's report 2026-11-15: the radio went to the band's last
        // frequency and mode, and the FT8 window stayed on the old band).
        this.onBandChange = null;
        // Whether FT8/FT4 reception runs (set by its window).
        this.digitalActive = () => false;
        // USB/LSB on the radio (owner's request 2026-11-14): the DE-19 carries audio
        // only in U-D/L-D, so the page asks whether the radio's own microphone and
        // speaker will be used (keep USB/LSB) or this computer (switch to U-D/L-D).
        // Asked again only when the mode has left USB/LSB in between.
        this.ssbDialog = el("ssb-dialog");
        this.ssbAnswered = null;
        // The receive passband (relative to the dial, Hz) and mode, for the audio filter.
        this.onPassband = () => { };
        // Called when the radio's frequency changes (the audio buffer is flushed).
        this.onRetune = () => { };
        // Further listeners of a retune, kept whatever replaces onRetune.
        this.retuneListeners = [];
        // Called when the server refuses audio (another browser listens, no card).
        this.onAudioRefused = () => { };
        view.onTune = (hz) => this.tune(hz);
        el("ssb-computer").addEventListener("click", () => this.answerSsb(true));
        el("ssb-radio").addEventListener("click", () => this.answerSsb(false));
        // Escape keeps the mode, as "The radio's own".
        this.ssbDialog.addEventListener("cancel", () => {
            if (this.currentMode === "USB" || this.currentMode === "LSB")
                this.ssbAnswered = this.currentMode;
        });
        this.installFineTune();
        this.bandSelect.add(new Option("—", ""));
        this.bandSelect.options[0].hidden = true; // shown when outside every band
        for (const [name] of BANDS)
            this.bandSelect.add(new Option(name, name));
        this.bandSelect.addEventListener("change", () => {
            this.goToBand(this.bandSelect.value);
            this.bandSelect.blur();
        });
        for (const mode of MODES) {
            const option = new Option(mode, mode);
            option.disabled = !SELECTABLE_MODES.has(mode);
            this.modeSelect.add(option);
        }
        this.modeSelect.value = "";
        this.modeSelect.addEventListener("change", () => {
            if (this.modeSelect.value !== "")
                this.send("set_mode", { mode: this.modeSelect.value });
        });
        onEnter(this.freqInput, () => {
            const mhz = Number(this.freqInput.value.trim().replace(",", "."));
            if (!Number.isFinite(mhz) || mhz <= 0) {
                this.showError("invalid frequency (MHz, e.g. 21.074)");
                return false;
            }
            this.setFrequency(Math.round(mhz * 1e6));
            return true;
        }, () => {
            const hz = this.view.radioHz;
            if (hz !== null)
                this.freqInput.value = (hz / 1e6).toFixed(6);
        });
        this.stepSelect.value = loadSetting(STEP_KEY) ?? "auto";
        this.stepSelect.addEventListener("change", () => saveSetting(STEP_KEY, this.stepSelect.value));
        this.atuOn.addEventListener("change", () => this.send("set_atu", { on: this.atuOn.checked }));
        this.vfoSwitch.addEventListener("click", () => {
            if (this.vfo !== null)
                this.send("set_vfo", { vfo: this.vfo === "A" ? "B" : "A" });
        });
        this.splitOn.addEventListener("change", () => this.send("set_split", { on: this.splitOn.checked }));
        // Typed values are sent on Enter only (owner's request); Escape or leaving the
        // field without Enter puts back what the radio reports.
        onEnter(this.rfGain, () => {
            const percent = Number(this.rfGain.value.trim());
            if (!Number.isInteger(percent) || percent < 1 || percent > 100) {
                this.showError("RF gain in whole percent, 1 to 100");
                return false;
            }
            if (percent !== this.rfGainShown)
                this.send("set_rf_gain", { percent });
            return true;
        }, () => {
            this.rfGain.value = this.rfGainShown === null ? "" : String(this.rfGainShown);
        });
        this.nbOn.addEventListener("change", () => this.send("set_nb", { on: this.nbOn.checked }));
        this.nbLevel.addEventListener("change", () => {
            if (this.nbLevel.value !== "")
                this.send("set_nb", { level: Number(this.nbLevel.value) });
        });
        this.agc.addEventListener("change", () => {
            if (this.agc.value !== "")
                this.send("set_agc", { agc: this.agc.value });
        });
        this.frontEnd.addEventListener("change", () => {
            if (this.frontEnd.value !== "")
                this.send("set_front_end", { state: this.frontEnd.value });
        });
        // The power is set on Enter only; the value the radio has is not sent again.
        onEnter(this.powerInput, () => {
            const watts = Number(this.powerInput.value.trim().replace(",", "."));
            if (!Number.isInteger(watts) || watts < 1 || watts > Number(this.powerInput.max)) {
                this.showError(`power in whole watts, 1 to ${this.powerInput.max} W`);
                return false;
            }
            if (watts === this.powerShown || watts === this.powerSent)
                return true;
            this.powerSent = watts;
            this.send("set_rf_power", { watts });
            return true;
        }, () => {
            this.powerInput.value = this.powerShown === null ? "" : String(this.powerShown);
        });
        // Tuning transmits a carrier the guard cannot stop (decisions.md D43): ask first.
        this.atuTune.addEventListener("click", () => {
            const where = this.frequency.textContent ?? "the current frequency";
            if (window.confirm(`Tune: the radio will transmit a carrier on ${where} (1–1.5 s with a matched antenna, about 10 s with a poor one) and the tuning cannot be stopped. Tune?`)) {
                this.send("atu_tune");
            }
        });
        for (const field of [this.filterLow, this.filterHigh]) {
            numberField(field, () => this.setFromSliders(field));
        }
        el("filter-reset").addEventListener("click", () => {
            if (this.currentMode !== null)
                delete this.overrides[this.currentMode];
            saveOverrides(this.overrides);
            this.updatePassband();
        });
    }
    narrowTo(audioHz, widthHz) {
        const mode = this.currentMode;
        const below = mode === "LSB" || mode === "L-D";
        const above = mode === "USB" || mode === "U-D";
        if (mode === null || (!below && !above))
            return false;
        if (this.beforeNarrow === null || this.beforeNarrow.mode !== mode) {
            this.beforeNarrow = { mode, band: this.overrides[mode] ?? null };
        }
        const low = Math.max(50, Math.round(audioHz - widthHz / 2));
        const high = low + Math.round(widthHz);
        this.overrides[mode] = below ? [-high, -low] : [low, high];
        saveOverrides(this.overrides);
        this.updatePassband();
        return true;
    }
    // A new width for the narrow filter, around its present centre; false when
    // the filter is not narrowed (nothing to change).
    renarrow(widthHz) {
        const mode = this.currentMode;
        const band = mode === null ? undefined : this.overrides[mode];
        if (this.beforeNarrow === null || this.beforeNarrow.mode !== mode || band === undefined)
            return false;
        const centre = Math.abs(band[0] + band[1]) / 2;
        return this.narrowTo(centre, widthHz);
    }
    restoreWide() {
        const before = this.beforeNarrow;
        if (before === null)
            return;
        if (before.band === null)
            delete this.overrides[before.mode];
        else
            this.overrides[before.mode] = before.band;
        this.beforeNarrow = null;
        saveOverrides(this.overrides);
        this.updatePassband();
    }
    // A click on the receive audio spectrum: passband.ts.
    setAudioEdge(edge, audioHz) {
        const mode = this.currentMode;
        const band = this.passbandHz;
        if (band === null || mode === null)
            return;
        const next = passbandWithEdge(mode, band, edge, audioHz);
        if (next === null)
            return;
        this.overrides[mode] = next;
        saveOverrides(this.overrides);
        this.updatePassband();
    }
    setFromSliders(moved) {
        if (this.currentMode === null)
            return;
        let low = Number(this.filterLow.value);
        let high = Number(this.filterHigh.value);
        if (low >= high) {
            if (moved === this.filterLow)
                high = low + 50;
            else
                low = high - 50;
        }
        this.overrides[this.currentMode] = [low, high];
        saveOverrides(this.overrides);
        this.updatePassband();
    }
    // Tuning grid: chosen, or 10 Hz in CW and 100 Hz in the other modes.
    step() {
        const chosen = Number(this.stepSelect.value);
        if (chosen > 0)
            return chosen;
        return this.currentMode === "CW" || this.currentMode === "CW-R" ? 10 : 100;
    }
    tune(hz) {
        if (this.onClickHz?.(hz) === true)
            return;
        const step = this.step();
        this.setFrequency(Math.round(hz / step) * step);
    }
    // Fine tuning (±500 Hz): in 10 Hz steps from the
    // radio's frequency when the slider is grabbed; it returns to the middle when
    // released and the radio stays where it was tuned. Arrow keys: ±10 Hz each.
    installFineTune() {
        let base = null;
        const label = (offset) => {
            this.fineTuneValue.textContent = `${offset > 0 ? "+" : ""}${offset} Hz`;
        };
        const release = () => {
            base = null;
            this.fineTune.value = "0";
            label(0);
        };
        this.fineTune.addEventListener("pointerdown", () => {
            base = this.view.radioHz;
        });
        this.fineTune.addEventListener("input", () => {
            if (base === null) {
                this.fineTune.value = "0"; // only dragging tunes; keys are handled below
                return;
            }
            const offset = fineTuneOffset(Number(this.fineTune.value) / 1000);
            label(offset);
            const hz = Math.round((base + offset) / 10) * 10;
            if (hz !== this.view.radioHz)
                this.setFrequency(hz);
        });
        for (const end of ["pointerup", "pointercancel", "change", "blur"]) {
            this.fineTune.addEventListener(end, release);
        }
        this.fineTune.addEventListener("keydown", (e) => {
            const step = { ArrowLeft: -10, ArrowDown: -10, ArrowRight: 10, ArrowUp: 10 }[e.key];
            if (step === undefined)
                return;
            e.preventDefault();
            if (this.view.radioHz !== null)
                this.setFrequency(Math.round(this.view.radioHz / 10) * 10 + step);
        });
    }
    // A band: the last frequency used on it in this browser, or its middle.
    goToBand(name) {
        const band = BANDS.find(([n]) => n === name);
        if (band === undefined)
            return;
        if (this.onBandChange?.(name) === true)
            return; // FT8/FT4 moves to its dial there
        const [, low, high] = band;
        const middle = Math.round((low + high) / 2 / 1000) * 1000;
        this.setFrequency(this.bandMemory[name] ?? middle);
        const mode = this.bandModes[name];
        this.awaitedMode = null;
        if (mode !== undefined && mode !== this.currentMode) {
            this.awaitedMode = { mode, since: performance.now() };
            this.send("set_mode", { mode });
        }
    }
    // The mode on the band of the radio's frequency now.
    rememberMode(mode) {
        const hz = this.view.radioHz;
        if (hz === null || !SELECTABLE_MODES.has(mode))
            return;
        // Not FT8/FT4's U-D (owner's request 2026-11-16: after FT8 the next voice
        // QSO on 80 or 40 m came back in U-D): not while its reception runs, and
        // not U-D on one of its dial frequencies (readings around Start and Stop).
        if (this.digitalActive())
            return;
        if (mode === "U-D" && Object.values(DIAL_HZ).some((dials) => Object.values(dials).includes(hz)))
            return;
        const awaited = this.awaitedMode;
        if (awaited !== null) {
            if (mode !== awaited.mode && performance.now() - awaited.since < BAND_MODE_SETTLE_MS)
                return;
            this.awaitedMode = null;
        }
        const band = BANDS.find(([, low, high]) => hz >= low && hz <= high);
        if (band === undefined || this.bandModes[band[0]] === mode)
            return;
        this.bandModes[band[0]] = mode;
        saveSetting(BAND_MODES_KEY, JSON.stringify(this.bandModes));
    }
    // Keyboard shortcuts (keys.ts): what the controls do, without the controls.
    tuneBy(direction, stepHz) {
        const hz = this.view.radioHz;
        if (hz !== null)
            this.setFrequency(gridStep(hz, stepHz ?? this.step(), direction));
    }
    tuneTo(hz) {
        this.setFrequency(hz);
    }
    get tuningStep() {
        return this.step();
    }
    get passbandHz() {
        const mode = this.currentMode;
        return mode === null ? null : (this.overrides[mode] ?? this.serverPassband ?? this.passbands[mode] ?? null);
    }
    bandBy(direction) {
        const hz = this.view.radioHz;
        const band = hz === null ? null : adjacentBand(BANDS, hz, direction);
        if (band !== null)
            this.goToBand(band[0]);
    }
    // The voice mode for `hz` after FT8/FT4: the one remembered for its band
    // (never U-D since #179), or L-D below 10 MHz and U-D above, as on the air.
    voiceModeFor(hz) {
        const band = BANDS.find(([, low, high]) => hz >= low && hz <= high);
        const kept = band === undefined ? undefined : this.bandModes[band[0]];
        if (kept !== undefined)
            return kept;
        return hz < 10000000 ? "L-D" : "U-D";
    }
    get bandName() {
        const hz = this.view.radioHz;
        return hz === null ? null : (BANDS.find(([, low, high]) => hz >= low && hz <= high)?.[0] ?? null);
    }
    setFrontEnd(state) {
        this.send("set_front_end", { state });
    }
    nextAgc() {
        this.send("set_agc", { agc: nextAgc(this.agc.value) });
    }
    toggleNb() {
        this.send("set_nb", { on: !this.nbOn.checked });
    }
    rfGainBy(direction) {
        if (this.rfGainShown !== null)
            this.send("set_rf_gain", { percent: rfGainStep(this.rfGainShown, direction) });
    }
    setFrequency(hz) {
        this.showFrequency(hz); // at once; radio_state or the answer corrects it
        this.send("set_frequency", { hz });
    }
    showError(text) {
        errorBox.show(text);
    }
    // S-meter: S0 (-54 dB from S9) to S9+60 on the bar; the server converts the
    // radio's level with the measured scale (server/smeter.py).
    showSMeter(meter) {
        if (typeof meter.db !== "number")
            return;
        this.sMeter.title = S_METER_TITLE;
        this.sMeterText.textContent = String(meter.text);
        const fraction = Math.min(1, Math.max(0, (meter.db + 54) / 114));
        this.sMeterBar.style.width = `${(fraction * 100).toFixed(1)}%`;
        this.sMeterBar.classList.toggle("over", meter.db > 0);
    }
    // ALC in transmit, in the S-meter's place: the server reads it only then and
    // sends radio_s_meter again after the transmission.
    showAlc(display, level) {
        this.lastAlc = [display, level];
        this.sMeter.title = `${ALC_TITLE} Level now: ${level}.`;
        this.sMeterText.textContent = `ALC ${display}`;
        this.sMeterBar.style.width = `${((Math.min(ALC_DISPLAY_MAX, Math.max(0, display)) / ALC_DISPLAY_MAX) * 100).toFixed(1)}%`;
        this.sMeterBar.classList.remove("over");
    }
    showTransmitting(active, leased) {
        this.txBar.hidden = !active;
        this.transmitting = active;
        this.renderVfo();
        // In transmit the server does not read the S-meter. The ALC is sent only
        // when it changes: a steady one (an FT8 tone, the ALC at its end) may have
        // come before this event and must stay shown.
        if (active && this.lastAlc !== null) {
            this.showAlc(...this.lastAlc);
        }
        else if (active) {
            this.sMeterText.textContent = "TX";
            this.sMeterBar.style.width = "0";
        }
        else {
            this.lastAlc = null; // the server sends the ALC again in the next transmission
        }
        // Power on the radio's meter without a lease from us: microphone PTT, TUNE or VOX.
        this.txWarning.hidden = !(active && !leased);
    }
    // The radio's power setting (14 0A) in watts. The power meter is not shown:
    // on the G90 it ends its scale at about 3 W; it only tells that the radio emits.
    showPower(power) {
        const watts = power.setting_watts;
        // Whole watts, as the radio sets and shows them (measured, D44).
        this.power.textContent = `Power: ${typeof watts === "number" ? Math.round(watts) : "—"} W`;
        if (typeof watts === "number") {
            this.powerShown = Math.round(watts);
            this.powerSent = null; // the radio answered: the same value may be sent again
            if (document.activeElement !== this.powerInput) {
                this.powerInput.value = String(this.powerShown);
            }
        }
        // No SWR scale: the G90's meter ends it at about SWR 2 (measured).
        this.swrWarning.hidden = power.swr_high !== true;
    }
    // In split the radio transmits on the other VFO (measured): shown in orange as "TX".
    showVfo(message) {
        const vfo = String(message.vfo);
        const other = vfo === "A" ? "B" : "A";
        this.vfo = vfo;
        this.vfoSwitch.textContent = `${vfo}>${other}`;
        if (typeof message.other_hz === "number")
            this.otherHz = message.other_hz;
        if (typeof message.split === "boolean") {
            this.split = message.split;
            this.splitOn.checked = this.split;
            this.splitBadge.dataset.state = this.split ? "on" : "off";
            this.splitBadge.title = this.split ? `Split on: transmitting on VFO ${other}` : "Split off";
        }
        this.renderVfo();
    }
    // The two frequency lines. In split, while the radio transmits, they change
    // places: the top line shows where the radio transmits (owner's request).
    renderVfo() {
        if (this.vfo === null)
            return;
        const mhz = (hz) => (hz === null ? "—" : `${(hz / 1e6).toFixed(6)} MHz`);
        const other = this.vfo === "A" ? "B" : "A";
        const swapped = this.split && this.transmitting;
        this.vfoName.textContent = swapped ? other : this.vfo;
        this.frequency.textContent = mhz(swapped ? this.otherHz : this.view.radioHz);
        this.frequency.classList.toggle("split-tx", swapped);
        if (swapped) {
            this.vfoOther.textContent = `RX ${this.vfo} ${mhz(this.view.radioHz)}`;
            this.vfoOther.title = `Split: the radio receives here, on VFO ${this.vfo}, and transmits on the line above`;
        }
        else {
            this.vfoOther.textContent = `${this.split ? "TX " : ""}${other} ${mhz(this.otherHz)}`;
            this.vfoOther.title = this.split
                ? `Split: the radio transmits here, on VFO ${other}, in that VFO's mode`
                : `The other VFO (${other})`;
        }
        this.vfoOther.classList.toggle("split", this.split && !swapped);
    }
    showAtu(on) {
        this.atuOn.checked = on;
        this.atuBadge.dataset.state = on ? "on" : "off";
        this.atuBadge.title = on ? "Antenna tuner on" : "Antenna tuner off";
    }
    showRfGain(percent) {
        this.rfGainShown = percent;
        this.rfGainInfo.textContent = `RF gain: ${percent} %`;
        if (document.activeElement !== this.rfGain)
            this.rfGain.value = String(percent);
    }
    showNb(on, level) {
        this.nbOn.checked = on;
        this.nbLevel.value = Number.isInteger(level) ? String(level) : "";
        this.nbBadge.dataset.state = on ? "on" : "off";
        this.nbBadge.title = `Noise blanker ${on ? "on" : "off"}, level ${level}`;
    }
    // As on the radio's display: AGC-F, AGC-S, AGC-A; AGC-- when off.
    showAgc(agc) {
        this.agc.value = agc;
        const letters = { fast: "F", slow: "S", auto: "A", off: "-" };
        const letter = letters[agc];
        this.agcBadge.dataset.state = letter === undefined ? "unknown" : agc === "off" ? "off" : "on";
        this.agcBadge.textContent = letter === undefined ? "AGC" : `AGC-${letter}`;
        this.agcBadge.title = letter === undefined ? "AGC: state unknown" : `AGC ${agc}`;
    }
    showFrontEnd(state) {
        this.frontEnd.value = state;
        const badge = this.frontEndBadge;
        badge.dataset.state = state === "pre" || state === "att" ? "on" : "off";
        badge.textContent = state === "pre" ? "PRE" : state === "att" ? "ATT" : "P/A";
        badge.title =
            state === "pre"
                ? "Preamp on"
                : state === "att"
                    ? "Attenuator on (−12 dB)"
                    : "Preamp and attenuator off";
    }
    // Without safety.max_rf_power_w the server refuses to set the power.
    showPowerLimit(limit) {
        const allowed = typeof limit === "number";
        this.powerInput.disabled = !allowed;
        if (allowed)
            this.powerInput.max = String(limit);
        this.powerSet.title = allowed
            ? `Transmit power set in the radio (1–${limit} W, not while transmitting)`
            : "Setting the power is off: no safety.max_rf_power_w in the server configuration";
    }
    handle(message) {
        if (message.event === "radio_transmitting") {
            this.showTransmitting(message.active === true, message.leased === true);
            return;
        }
        if (message.event === "radio_tx_suspected") {
            this.txSuspect.hidden = message.active !== true;
            return;
        }
        if (message.event === "radio_vfo") {
            this.showVfo(message);
            return;
        }
        if (message.event === "radio_atu") {
            this.showAtu(message.on === true);
            return;
        }
        if (message.event === "radio_alc") {
            this.showAlc(Number(message.display), Number(message.level));
            return;
        }
        if (message.event === "radio_s_meter") {
            this.showSMeter(message);
            return;
        }
        if (message.event === "radio_rf_gain") {
            this.showRfGain(Number(message.rf_gain));
            return;
        }
        if (message.event === "radio_nb") {
            this.showNb(message.nb_on === true, Number(message.nb_level));
            return;
        }
        if (message.event === "radio_agc") {
            this.showAgc(String(message.agc ?? ""));
            return;
        }
        if (message.event === "radio_front_end") {
            this.showFrontEnd(String(message.front_end ?? ""));
            return;
        }
        if (message.event === "radio_power") {
            this.showPower(message);
            return;
        }
        if ("max_rf_power_w" in message)
            this.showPowerLimit(message.max_rf_power_w);
        if (typeof message.setting_watts === "number")
            this.showPower(message); // set_rf_power answer
        const power = message.power;
        if (power !== undefined && power !== null) {
            this.showPower(power);
            const leased = message.tx_active === true;
            this.showTransmitting(power.transmitting === true || leased, leased);
        }
        if (typeof message.hz === "number")
            this.showFrequency(message.hz);
        if (Array.isArray(message.passband_hz)) {
            this.serverPassband = message.passband_hz;
        }
        if (typeof message.mode === "string")
            this.showMode(message.mode);
        if (typeof message.on === "boolean")
            this.showAtu(message.on); // get_atu answer
        if (typeof message.vfo === "string")
            this.showVfo(message); // get_vfo, set_vfo, set_split
        if (typeof message.front_end === "string")
            this.showFrontEnd(message.front_end);
        if (typeof message.agc === "string")
            this.showAgc(message.agc);
        if (typeof message.nb_on === "boolean")
            this.showNb(message.nb_on, Number(message.nb_level));
        if (typeof message.rf_gain === "number")
            this.showRfGain(message.rf_gain);
        const sMeter = message.s_meter;
        if (sMeter !== undefined && sMeter !== null)
            this.showSMeter(sMeter); // get_s_meter answer
        if ("iq" in message)
            this.showIq(message.iq ?? null); // the status answer
        if ("hardware_checklist" in message)
            this.showChecklist(message.hardware_checklist);
        // waterfall_subscribe without an I/Q input: the status answer tells it.
        if (message.error === "iq_unavailable")
            return;
        if (message.error === "audio_busy" || message.error === "audio_unavailable") {
            this.onAudioRefused(String(message.message ?? message.error));
            return;
        }
        if (message.ok === false) {
            this.showError(String(message.message ?? message.error ?? "error"));
            this.send("get_frequency"); // undo the optimistic display
            this.send("get_mode");
            this.send("get_power_meter"); // and the power field
            this.send("get_front_end");
            this.send("get_agc");
            this.send("get_nb");
            this.send("get_rf_gain");
        }
    }
    // The hardware checklist (sudo remoteg90ctl setup) not done or skipped for
    // this radio: a warning that stays up (owner's decision 2026-11-11).
    showChecklist(state) {
        const box = el("checklist-warning");
        const checklist = state;
        if (checklist === null || checklist.result === "passed") {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        box.textContent =
            checklist.result === "skipped"
                ? `Hardware checklist skipped (${(checklist.skipped ?? []).join(", ")}): check the radio and run sudo remoteg90ctl setup`
                : "Hardware checklist not done for this radio: run sudo remoteg90ctl setup on the server";
    }
    // No I/Q input: the scale only. An input that does not run: why, over the
    // waterfall, and the status asked again until it runs.
    showIq(iq) {
        const off = iq === null || iq.source === "none";
        this.view.setScaleOnly(off ? (iq?.sample_rate ?? SCALE_ONLY_SPAN_HZ) : null);
        const failing = !off && iq.running !== true;
        this.view.showIqError(failing ? `I/Q: ${iq.error ?? "off"}` : null);
        if (failing && this.iqRetry === undefined) {
            this.iqRetry = window.setTimeout(() => {
                this.iqRetry = undefined;
                this.send("status");
            }, IQ_RETRY_MS);
        }
        if (iq === null)
            return;
        if (typeof iq.line_rate === "number")
            showSpeedLabels(iq.line_rate);
        this.passbands = iq.passband_hz ?? {};
        this.updatePassband();
    }
    askSsb(mode) {
        if (mode !== "USB" && mode !== "LSB") {
            this.ssbAnswered = null;
            if (this.ssbDialog.open)
                this.ssbDialog.close();
            return;
        }
        if (this.ssbAnswered === mode || this.transmitting)
            return;
        // Opened, or still open: the texts follow the mode read now (USB may have
        // become LSB meanwhile), and the buttons act on the mode at their click.
        el("ssb-title").textContent = `The radio is in ${mode}`;
        el("ssb-computer").textContent =
            `This computer (switch to ${mode === "USB" ? "U-D" : "L-D"})`;
        if (!this.ssbDialog.open)
            this.ssbDialog.showModal();
    }
    // The answer holds for the mode it was given in (also "This computer": the
    // radio has not switched yet, or refused — the error shows then, and the
    // dialog does not come back with every reading).
    answerSsb(computer) {
        const mode = this.currentMode;
        this.ssbDialog.close();
        if (mode !== "USB" && mode !== "LSB")
            return;
        this.ssbAnswered = mode;
        if (computer)
            this.send("set_mode", { mode: mode === "USB" ? "U-D" : "L-D" });
    }
    showMode(mode) {
        this.currentMode = mode;
        this.rememberMode(mode);
        this.askSsb(mode);
        this.mode.textContent = mode;
        if (document.activeElement !== this.modeSelect)
            this.modeSelect.value = mode;
        this.updatePassband();
    }
    updatePassband() {
        const mode = this.currentMode;
        const band = mode === null
            ? null
            : (this.overrides[mode] ?? this.serverPassband ?? this.passbands[mode] ?? null);
        this.view.passband = band;
        this.onPassband(band, mode);
        this.filterControls.hidden = mode === null;
        if (band !== null) {
            if (document.activeElement !== this.filterLow)
                this.filterLow.value = String(band[0]);
            if (document.activeElement !== this.filterHigh)
                this.filterHigh.value = String(band[1]);
            this.filterValue.textContent = "Hz";
        }
        else {
            this.filterValue.textContent = "none";
        }
    }
    refreshPassband() {
        this.updatePassband();
    }
    showFrequency(hz) {
        if (this.view.radioHz !== null && this.view.radioHz !== hz) {
            this.onRetune();
            for (const listener of this.retuneListeners)
                listener();
        }
        this.view.radioHz = hz;
        this.frequency.textContent = `${(hz / 1e6).toFixed(6)} MHz`;
        const band = BANDS.find(([, low, high]) => hz >= low && hz <= high);
        if (band !== undefined && this.bandMemory[band[0]] !== hz) {
            this.bandMemory[band[0]] = hz; // the last frequency on this band
            saveSetting(BAND_MEMORY_KEY, JSON.stringify(this.bandMemory));
        }
        if (document.activeElement !== this.bandSelect)
            this.bandSelect.value = band?.[0] ?? "";
        this.renderVfo();
        if (document.activeElement !== this.freqInput)
            this.freqInput.value = (hz / 1e6).toFixed(6);
    }
}
const OVERRIDES_KEY = "remoteg90ctl.passband";
const STEP_KEY = "remoteg90ctl.tuneStep";
const S_METER_TITLE = "The radio's S-meter (CI-V 15 02), read every 0.5 s in receive";
const ALC_DISPLAY_MAX = 82; // the radio's display ends there (measured)
const ALC_TITLE = "ALC (CI-V 15 13), read every 0.5 s while transmitting, as the number on the radio's display " +
    "(converted from the radio's level 0-255 with points measured on a steady sound: " +
    "7 → 5-6, 40 → 30, 95 → 82; the display ends at about 82). " +
    "With speech it can differ from the display: the reading is one instant every 0.5 s.";
// Pause after the last keystroke in the power field before it is sent.
const SPEED_KEY = "remoteg90ctl.waterfallSpeed";
// Connection dot: one blink per second for every KBPS_PER_HZ kbit/s received,
// kept between a slow pulse and a flicker the eye can still follow.
const KBPS_PER_HZ = 100;
const BLINK_MIN_HZ = 0.5;
const BLINK_MAX_HZ = 6;
// Lines merged into one sent line per speed (server/waterfall.py SPEEDS).
const SPEED_MERGE = { fast: 1, medium: 2, slow: 4, slower: 10, slowest: 20 };
// Speed options as lines per second, from the server's iq.line_rate.
function showSpeedLabels(lineRate) {
    for (const option of Array.from(el("waterfall-speed").options)) {
        const merge = SPEED_MERGE[option.value];
        if (merge === undefined)
            continue;
        const rate = lineRate / merge;
        const text = Number.isInteger(rate) ? String(rate) : rate.toFixed(1);
        option.text = `${text} ${rate === 1 ? "line" : "lines"}/s`;
    }
}
// Page settings live on the account (decisions.md D48 part C): loaded once
// after login, before the controls are built, and saved a moment after a change.
const SETTINGS_PREFIX = "remoteg90ctl.";
const SETTINGS_SAVE_MS = 1000;
const settings = new Map();
let settingsTimer;
function loadSetting(key) {
    return settings.get(key) ?? null;
}
// A typed field: `apply` runs on Enter (false keeps the field for a correction);
// Escape or leaving the field without a valid Enter calls `restore`.
// On a phone the keyboard often has no Enter for a number (iOS) or moves on
// to the next field ("Next" on Android) without one, so a value typed after a
// touch is set when the field is left, if it changed (owner's report
// 2026-11-16: power and RF gain went back to the old value). With a mouse or
// keyboard: Enter only, as before; leaving the field restores.
function onEnter(input, apply, restore) {
    let applied = false;
    let touched = false;
    let before = "";
    input.addEventListener("pointerdown", (e) => (touched = e.pointerType !== "mouse"));
    input.addEventListener("focus", () => {
        applied = false;
        before = input.value;
    });
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && apply()) {
            applied = true;
            input.blur();
        }
        else if (e.key === "Escape") {
            touched = false;
            input.blur();
        }
    });
    input.addEventListener("blur", () => {
        if (!applied && touched && input.value !== before && apply())
            return;
        if (!applied)
            restore();
    });
}
// Fine tuning: the offset grows exponentially from the middle of the slider to
// its ends (owner's request): fine steps near the middle, up to ±500 Hz at the
// ends; always a multiple of 10 Hz. `position` is -1 to 1.
const FINE_TUNE_MAX_HZ = 500;
const FINE_TUNE_CURVE = 4; // e^4: about 0.5 Hz per pixel in the middle, 27 at the ends
function fineTuneOffset(position) {
    const p = Math.min(1, Math.abs(position));
    const hz = (FINE_TUNE_MAX_HZ * Math.expm1(FINE_TUNE_CURVE * p)) / Math.expm1(FINE_TUNE_CURVE);
    return Math.sign(position) * Math.round(hz / 10) * 10;
}
function loadBandMemory() {
    try {
        const stored = JSON.parse(loadSetting(BAND_MEMORY_KEY) ?? "{}");
        const memory = {};
        for (const [name, low, high] of BANDS) {
            const hz = stored[name];
            if (typeof hz === "number" && hz >= low && hz <= high)
                memory[name] = hz;
        }
        return memory;
    }
    catch {
        return {};
    }
}
function loadBandModes() {
    try {
        const stored = JSON.parse(loadSetting(BAND_MODES_KEY) ?? "{}");
        const modes = {};
        for (const [name] of BANDS) {
            const mode = stored[name];
            if (typeof mode === "string" && SELECTABLE_MODES.has(mode))
                modes[name] = mode;
        }
        return modes;
    }
    catch {
        return {};
    }
}
function saveSetting(key, value) {
    if (settings.get(key) === value)
        return;
    settings.set(key, value);
    window.clearTimeout(settingsTimer);
    settingsTimer = window.setTimeout(() => {
        void api("PUT", "/api/settings", { page: Object.fromEntries(settings) });
    }, SETTINGS_SAVE_MS);
}
// After login: the account's settings; the first time, those this browser kept
// before accounts existed (localStorage) are taken over.
async function loadSettings() {
    const answer = await api("GET", "/api/settings");
    const page = (answer.page ?? {});
    for (const [key, value] of Object.entries(page)) {
        if (typeof value === "string")
            settings.set(key, value);
    }
    if (settings.size > 0)
        return;
    try {
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            const value = key === null ? null : localStorage.getItem(key);
            if (key !== null && value !== null && key.startsWith(SETTINGS_PREFIX))
                settings.set(key, value);
        }
    }
    catch {
        return;
    }
    if (settings.size > 0)
        void api("PUT", "/api/settings", { page: Object.fromEntries(settings) });
}
// Browser storage may be unavailable (private window, blocked site data):
// the sliders then work for this page only.
function loadOverrides() {
    try {
        const raw = loadSetting(OVERRIDES_KEY);
        return raw === null ? {} : JSON.parse(raw);
    }
    catch {
        return {};
    }
}
function saveOverrides(overrides) {
    saveSetting(OVERRIDES_KEY, JSON.stringify(overrides));
}
const HEIGHT_KEY = "remoteg90ctl.waterfallHeight";
// Drag the handle under the waterfall to change its height; remembered in the
// browser (best effort).
function installResizeHandle() {
    const handle = el("resize-handle");
    const wrap = el("fall-wrap");
    const apply = (px) => {
        const max = window.innerHeight - 200;
        const height = Math.round(Math.min(max, Math.max(60, px)));
        document.documentElement.style.setProperty("--fall-height", `${height}px`);
    };
    const saved = Number(loadSetting(HEIGHT_KEY));
    if (saved > 0)
        apply(saved);
    let startY = 0;
    let startHeight = 0;
    handle.addEventListener("pointerdown", (e) => {
        handle.setPointerCapture(e.pointerId);
        startY = e.clientY;
        startHeight = wrap.getBoundingClientRect().height;
    });
    handle.addEventListener("pointermove", (e) => {
        if (handle.hasPointerCapture(e.pointerId))
            apply(startHeight + e.clientY - startY);
    });
    handle.addEventListener("pointerup", (e) => {
        handle.releasePointerCapture(e.pointerId);
        saveSetting(HEIGHT_KEY, String(Math.round(wrap.getBoundingClientRect().height)));
    });
}
const COLUMNS_KEY = "remoteg90ctl.controlColumns";
const COLUMN_BAR_PX = 12;
// The bars between the setting blocks (columns.ts): dragging one changes the
// widths of its two neighbours; a double click restores the defaults. On a
// narrow window the blocks wrap as before and the bars are hidden (style.css).
function installColumnHandles() {
    const controls = el("controls");
    const blocks = ["radio-block", "waterfall-settings", "rx-dsp", "tx-dsp"].map((id) => el(id));
    let fractions = parseColumns(loadSetting(COLUMNS_KEY)) ?? [...DEFAULT_COLUMNS];
    const apply = () => {
        controls.style.gridTemplateColumns = columnTemplate(fractions, COLUMN_BAR_PX);
    };
    apply();
    for (const bar of Array.from(controls.querySelectorAll(".col-handle"))) {
        const index = Number(bar.dataset.bar);
        let startX = 0;
        let widths = null;
        bar.addEventListener("pointerdown", (e) => {
            bar.setPointerCapture(e.pointerId);
            bar.classList.add("dragging");
            startX = e.clientX;
            widths = blocks.map((b) => b.getBoundingClientRect().width);
        });
        bar.addEventListener("pointermove", (e) => {
            if (widths === null || !bar.hasPointerCapture(e.pointerId))
                return;
            fractions = dragColumns(widths, index, e.clientX - startX);
            apply();
        });
        const end = (e) => {
            if (widths === null)
                return;
            widths = null;
            bar.classList.remove("dragging");
            if (bar.hasPointerCapture(e.pointerId))
                bar.releasePointerCapture(e.pointerId);
            saveSetting(COLUMNS_KEY, JSON.stringify(fractions));
        };
        bar.addEventListener("pointerup", end);
        bar.addEventListener("pointercancel", end);
        bar.addEventListener("dblclick", () => {
            fractions = [...DEFAULT_COLUMNS];
            apply();
            saveSetting(COLUMNS_KEY, JSON.stringify(fractions));
        });
    }
}
function installLevelControls(view) {
    const auto = el("auto-levels");
    const low = el("low-db");
    const high = el("high-db");
    auto.addEventListener("change", () => {
        view.autoLevels = auto.checked;
    });
    const manual = () => {
        auto.checked = false;
        view.autoLevels = false;
        view.setLevels(Number(low.value), Number(high.value));
    };
    numberField(low, manual);
    numberField(high, manual);
    setInterval(() => {
        if (view.autoLevels) {
            const [l, h] = view.levels();
            if (document.activeElement !== low)
                low.value = String(Math.round(l));
            if (document.activeElement !== high)
                high.value = String(Math.round(h));
        }
    }, 500);
}
const SPIKE_KEY = "remoteg90ctl.removeCentreLine";
// "DC fix" (was "remove artefacts"): switching it on starts learning the centre line anew, so
// the remover adapts to the conditions of that moment. Remembered per browser.
function installSpikeControl(view) {
    const toggle = el("remove-artefacts");
    const state = el("artefacts-state");
    const apply = () => {
        if (toggle.checked) {
            const remover = new SpikeRemover();
            remover.reset();
            view.spikeRemover = remover;
        }
        else {
            view.spikeRemover = null;
        }
        saveSetting(SPIKE_KEY, toggle.checked ? "on" : "off");
    };
    toggle.checked = loadSetting(SPIKE_KEY) === "on";
    toggle.addEventListener("change", apply);
    apply();
    setInterval(() => {
        const remover = view.spikeRemover;
        const [mode, help] = remover === null
            ? ["off", "Artefact removal off"]
            : remover.learning
                ? ["learning", "Learning the current conditions (a few seconds)…"]
                : remover.lastWidth > 0
                    ? ["on", "Removing the fixed line in the middle of the waterfall"]
                    : ["idle", "On, but no fixed line to remove now"];
        state.dataset.state = mode;
        state.title = help;
    }, 500);
}
const MONTH_S = 30 * 24 * 3600;
function total(t) {
    return t.waterfall + t.audio + t.messages + t.received;
}
function kbps(bytes, seconds) {
    return seconds > 0 ? `${((bytes * 8) / seconds / 1000).toFixed(0)} kbit/s` : "…";
}
// From this CPU temperature on, the line turns orange (Intel's limit is near 100 °C).
const CPU_HOT_C = 85;
// Below this the disk line turns orange (the machine by the radio has 8 GB).
const DISK_LOW_BYTES = 500 * 1024 * 1024;
const PROCESS_NAMES = {
    server: "server",
    guard: "guard",
    tailscaled: "Tailscale",
};
// Small closable window with CPU and memory of the radio processes. The
// server reads them only while the window is open (system_subscribe); closing
// it unsubscribes, so nothing is monitored.
// "System log": the server's recent log lines (INFO and above), then each new one,
// sent only while the window is open (server/logbuffer.py).
const LOG_LINES_KEPT = 500;
class LogWindow {
    constructor() {
        this.box = el("system-log");
        this.list = el("system-log-lines");
        this.open = false;
        this.send = () => { };
        el("system-log-open").addEventListener("click", () => this.setOpen(!this.open));
        el("system-log-close").addEventListener("click", () => this.setOpen(false));
    }
    setOpen(open) {
        this.open = open;
        this.box.hidden = !open;
        this.list.replaceChildren();
        this.send(open ? "log_subscribe" : "log_unsubscribe");
    }
    // After (re)connecting: the server forgot the subscription with the old socket.
    connected() {
        if (this.open) {
            this.list.replaceChildren();
            this.send("log_subscribe");
        }
    }
    handle(message) {
        if (message.event === "log_line") {
            if (this.open)
                this.add([String(message.line)]);
            return true;
        }
        if (Array.isArray(message.lines)) {
            this.add(message.lines.map(String)); // the log_subscribe answer
            return true;
        }
        return false;
    }
    add(lines) {
        const atEnd = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 20;
        for (const line of lines) {
            const row = document.createElement("div");
            row.textContent = line;
            if (/ level=(WARNING|ERROR|CRITICAL) /.test(line))
                row.className = "warn";
            this.list.append(row);
        }
        while (this.list.children.length > LOG_LINES_KEPT)
            this.list.firstElementChild?.remove();
        if (atEnd)
            this.list.scrollTop = this.list.scrollHeight; // follow, unless scrolled back
    }
}
let installPrompt = null;
let onInstallPrompt = () => { };
window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); // the button in the about window offers it instead
    installPrompt = e;
    onInstallPrompt();
});
// The about window: static text in the page; the version comes from the server.
class AboutWindow {
    constructor() {
        this.box = el("about");
        this.version = el("about-version");
        this.install = el("about-install");
        this.installHint = el("about-install-hint");
        // Set by the page: asks the server.
        this.request = () => Promise.resolve({});
        el("about-open").addEventListener("click", () => this.setOpen(this.box.hidden));
        el("about-close").addEventListener("click", () => this.setOpen(false));
        // Install as an app: a button when the browser offers it (Chrome, Edge);
        // otherwise how to do it from the browser's menu (Safari, Firefox, or
        // when the page is already installed).
        onInstallPrompt = () => this.showInstall();
        this.showInstall();
        this.install.addEventListener("click", () => {
            const offer = installPrompt;
            if (offer === null)
                return;
            installPrompt = null; // an offer can be used once
            this.showInstall();
            void offer.prompt();
        });
        window.addEventListener("appinstalled", () => {
            installPrompt = null;
            this.showInstall();
        });
    }
    showInstall() {
        const standalone = window.matchMedia("(display-mode: standalone)").matches;
        this.install.hidden = installPrompt === null || standalone;
        this.installHint.textContent = standalone
            ? "Running as an installed app."
            : installPrompt !== null
                ? "An icon and a window without the browser's bar."
                : "To install as an app: the browser's menu → \"Install\" or \"Add to home screen\" " +
                    "(Safari: Share → \"Add to Home Screen\").";
    }
    setOpen(open) {
        this.box.hidden = !open;
        if (!open)
            return;
        void this.request("status").then((answer) => {
            this.version.textContent =
                typeof answer.version === "string" ? `version ${answer.version}` : "version unknown (not connected)";
        });
    }
}
class ResourceWindow {
    constructor() {
        this.box = el("resources");
        this.body = el("resources-body");
        this.system = el("resources-system");
        this.network = el("resources-network");
        this.open = false;
        // Previous sample, for rates over the last interval.
        this.last = null;
        // Set by the page: sends a request to the server.
        this.send = () => { };
        // Set by the page: asks the server and waits for the answer.
        this.request = () => Promise.resolve({});
        el("server-restart").addEventListener("click", () => void this.restart());
        el("resources-open").addEventListener("click", () => this.setOpen(!this.open));
        el("resources-close").addEventListener("click", () => this.setOpen(false));
    }
    setOpen(open) {
        this.open = open;
        this.box.hidden = !open;
        if (open) {
            this.last = null;
            this.network.textContent = "";
            this.body.replaceChildren();
            this.system.textContent = "measuring…";
            this.send("system_subscribe");
        }
        else {
            this.send("system_unsubscribe");
        }
    }
    // After (re)connecting: the server forgot the subscription with the old socket.
    // The server program only (owner's decision 2026-11-13): systemd starts it
    // again and the page reconnects. The guard keeps running.
    async restart() {
        const question = "Restart the server? Listening, the waterfall and FT8/FT4 reception stop for a few seconds; " +
            "the page reconnects by itself. The guard and the radio are not restarted.";
        if (!window.confirm(question))
            return;
        const answer = await this.request("server_restart");
        if (answer.ok !== true) {
            errorBox.show(`Restart refused: ${String(answer.message ?? answer.error)}`);
            return;
        }
        this.system.textContent = "server restarting…";
    }
    connected() {
        if (this.open)
            this.send("system_subscribe");
    }
    show(message) {
        if (!this.open)
            return;
        const rows = message.processes ?? [];
        this.body.replaceChildren(...rows.map((p) => {
            const row = document.createElement("tr");
            const cells = p.error !== null
                ? [PROCESS_NAMES[p.name] ?? p.name, p.error === "not connected" ? "not connected" : "no access", "", ""]
                : [
                    PROCESS_NAMES[p.name] ?? p.name,
                    p.cpu_percent === null ? "…" : `${p.cpu_percent.toFixed(1)} %`,
                    p.rss_bytes === null ? "—" : formatBytes(p.rss_bytes),
                    p.threads === null ? "—" : String(p.threads),
                ];
            for (const text of cells) {
                const cell = document.createElement("td");
                cell.textContent = text;
                row.append(cell);
            }
            row.title = p.pid === null ? "" : `pid ${p.pid}`;
            return row;
        }));
        const sys = message.system;
        if (sys === null || sys === undefined) {
            this.system.textContent = "system: no data";
            return;
        }
        const cpu = sys.cpu_percent === null ? "…" : `${sys.cpu_percent.toFixed(0)} %`;
        const used = sys.mem_total_bytes - sys.mem_available_bytes;
        this.system.textContent =
            `system: CPU ${cpu} (${sys.cpus ?? "?"} cores), load ${sys.load.map((v) => v.toFixed(2)).join(" ")}, ` +
                `memory ${formatBytes(used)} / ${formatBytes(sys.mem_total_bytes)}`;
        const temperature = sys.cpu_temperature;
        if (temperature !== null && temperature !== undefined) {
            const line = document.createElement("div");
            line.textContent = `CPU temperature: ${temperature.celsius.toFixed(0)} °C`;
            line.title = `sensor: ${temperature.source}`;
            if (temperature.celsius >= CPU_HOT_C)
                line.className = "warning";
            this.system.append(line);
        }
        const disk = sys.disk;
        if (disk !== null && disk !== undefined) {
            const line = document.createElement("div");
            line.textContent = `disk: ${formatBytes(disk.free_bytes)} free of ${formatBytes(disk.total_bytes)}`;
            line.title = `the file system of ${disk.path} (accounts, settings, the station log)`;
            if (disk.free_bytes < DISK_LOW_BYTES)
                line.className = "warning";
            this.system.append(line);
        }
        const clock = document.createElement("div");
        clock.textContent =
            sys.clock_synchronized === true
                ? "clock: synchronised (NTP)"
                : sys.clock_synchronized === false
                    ? "clock: NOT synchronised — no FT8/FT4 transmission"
                    : "clock: synchronisation unknown — no FT8/FT4 transmission";
        if (sys.clock_synchronized !== true)
            clock.className = "warning";
        clock.title = "The server's clock; FT8/FT4 transmits only with it synchronised (D53)";
        this.system.append(clock);
        const net = message.network;
        if (net !== undefined)
            this.showNetwork(net);
    }
    // Rates from the difference to the previous sample; the monthly figure assumes
    // the current rate all month (what matters on a metered mobile link).
    showNetwork(net) {
        const now = performance.now() / 1000;
        const last = this.last;
        this.last = { at: now, browser: net.browser, server: net.server };
        if (last === null) {
            this.network.textContent = "network: measuring…";
            return;
        }
        const dt = now - last.at;
        const b = net.browser;
        const lb = last.browser;
        const rate = (total(b) - total(lb)) / dt; // bytes/s
        this.network.textContent =
            `network, this browser: ${kbps(total(b) - total(lb), dt)} ` +
                `(waterfall ${kbps(b.waterfall - lb.waterfall, dt)}, audio ${kbps(b.audio - lb.audio, dt)}, ` +
                `other ${kbps(b.messages + b.received - lb.messages - lb.received, dt)}); ` +
                `since connecting ${formatBytes(total(b))}; about ${formatBytes(rate * MONTH_S)}/month. ` +
                `Whole server: ${kbps(total(net.server) - total(last.server), dt)}, total ${formatBytes(total(net.server))}`;
    }
}
function formatBytes(bytes) {
    if (bytes >= 1024 ** 3)
        return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
// Errors in a window in the middle of the page (owner's request): it opens on a
// new message and stays until closed; the latest ERRORS_KEPT, newest first.
// A confirmation ("info", e.g. an account added) uses the same window, but it is
// headed "Info" and green unless an error is also listed.
const ERRORS_KEPT = 6;
const errorBox = {
    box: el("errors"),
    list: el("errors-list"),
    heading: el("errors-heading"),
    show(text, kind = "error") {
        const now = new Date().toLocaleTimeString([], { hour12: false });
        // The same message again (e.g. a lost port reported by every request):
        // one line with a count and the latest time, not a list of copies
        // (owner's report 2026-11-17: six identical lines).
        const last = this.list.firstElementChild;
        if (last instanceof HTMLElement && last.className === kind && last.dataset.text === text) {
            const count = Number(last.dataset.count ?? "1") + 1;
            last.dataset.count = String(count);
            const time = last.querySelector("time");
            if (time !== null)
                time.textContent = now;
            const times = last.querySelector(".count");
            if (times !== null)
                times.textContent = ` (×${count})`;
            this.box.hidden = false;
            return;
        }
        const item = document.createElement("li");
        item.className = kind;
        item.dataset.text = text;
        const time = document.createElement("time");
        time.textContent = now;
        const times = document.createElement("span");
        times.className = "count";
        item.append(time, text, times);
        this.list.prepend(item);
        while (this.list.children.length > ERRORS_KEPT)
            this.list.lastElementChild?.remove();
        const anyError = this.list.querySelector("li.error") !== null;
        this.heading.textContent = anyError ? "Error" : "Info";
        this.box.classList.toggle("info", !anyError);
        this.box.hidden = false;
    },
    close() {
        this.box.hidden = true;
        this.list.replaceChildren();
    },
};
el("errors-close").addEventListener("click", () => errorBox.close());
const AUDIO_BITRATE_KEY = "remoteg90ctl.audioBitrate";
// The Opus encoder's complexity on the server (owner's request 2026-11-15).
const AUDIO_COMPLEXITY_KEY = "remoteg90ctl.audioComplexity";
// FT8/FT4 reception: full quality for weak signals (owner's decision 2026-11-12).
const FT8_COMPLEXITY = 10;
const AUDIO_VOLUME_KEY = "remoteg90ctl.audioVolume";
// The browser's own part of the transmit delay: capture, processing, one frame.
const TX_PAGE_MS = 30;
const TX_BITRATE_KEY = "remoteg90ctl.txBitrate";
const TX_GAIN_KEY = "remoteg90ctl.txGain";
function installAudioControls(connection, radio) {
    const button = el("listen");
    const bitrate = el("audio-bitrate");
    const complexity = el("audio-complexity");
    const volume = el("audio-volume");
    const state = el("audio-state");
    const audio = new RxAudio();
    installRxDsp(audio, radio);
    bitrate.value = loadSetting(AUDIO_BITRATE_KEY) ?? "24";
    if (bitrate.value === "")
        bitrate.value = "24";
    complexity.value = loadSetting(AUDIO_COMPLEXITY_KEY) ?? "10";
    if (complexity.value === "")
        complexity.value = "10";
    volume.value = loadSetting(AUDIO_VOLUME_KEY) ?? "80";
    audio.setVolume(Number(volume.value) / 100);
    if (!RxAudio.supported()) {
        button.disabled = true;
        errorBox.show("Listen: browser without WebCodecs (AudioDecoder) — listening unavailable");
        return null;
    }
    let override = null;
    const subscribe = () => {
        const chosen = override ?? bitrate.value;
        const raw = chosen === "raw";
        audio.newStream();
        const level = override !== null ? FT8_COMPLEXITY : Number(complexity.value);
        connection.send("audio_subscribe", raw ? { codec: "raw" } : { codec: "opus", bitrate: Number(chosen), complexity: level });
    };
    const stop = () => {
        connection.send("audio_unsubscribe");
        audio.stop();
        button.textContent = "Listen";
        state.textContent = "";
    };
    const listen = async () => {
        await audio.start();
        button.textContent = "Stop";
        subscribe();
    };
    button.addEventListener("click", () => {
        if (audio.running) {
            stop();
            return;
        }
        void listen();
    });
    bitrate.addEventListener("change", () => {
        saveSetting(AUDIO_BITRATE_KEY, bitrate.value);
        complexity.disabled = bitrate.value === "raw";
        if (audio.running) {
            audio.flush();
            subscribe();
        }
    });
    complexity.disabled = bitrate.value === "raw";
    complexity.addEventListener("change", () => {
        saveSetting(AUDIO_COMPLEXITY_KEY, complexity.value);
        if (audio.running && override === null) {
            audio.flush();
            subscribe();
        }
    });
    volume.addEventListener("input", () => {
        audio.setVolume(Number(volume.value) / 100);
        saveSetting(AUDIO_VOLUME_KEY, volume.value);
    });
    // Short enough for its place; a fixed explanation (a title changed several
    // times a second never showed; owner's report 2026-11-21).
    state.title =
        "delay: from the radio to the speaker (both clocks by NTP: tens of ms of error); " +
            "buf: the playback buffer now / its target; drop: times the audio ran out";
    audio.onStats = ({ bufferedMs, targetMs, underruns, latencyMs }) => {
        const delay = latencyMs === null ? "delay —" : `delay ${Math.max(0, latencyMs).toFixed(0)} ms`;
        const buffer = `buf ${bufferedMs.toFixed(0)}/${targetMs.toFixed(0)}`;
        state.textContent = `${delay} · ${buffer}${underruns ? ` · ${underruns} drop` : ""}`;
    };
    connection.onAudio = (frame) => audio.handle(frame);
    connection.onOpen = () => {
        if (audio.running)
            subscribe();
    };
    radio.onRetune = () => audio.flush();
    radio.onAudioRefused = (text) => {
        audio.stop();
        button.textContent = "Listen";
        state.textContent = "";
        errorBox.show(`Listen: ${text}`);
    };
    return {
        audio,
        async ensureListening() {
            if (!audio.running)
                await listen();
            return audio.running;
        },
        pause() {
            if (!audio.running)
                return false;
            stop();
            return true;
        },
        async resume() {
            if (!audio.running)
                await listen();
        },
        setBitrateOverride(value) {
            if (value === override)
                return;
            override = value;
            bitrate.disabled = value !== null;
            complexity.disabled = value !== null || bitrate.value === "raw";
            bitrate.title = value === null ? "" : `${value} kbit/s while FT8/FT4 is on`;
            if (audio.running) {
                audio.flush();
                subscribe();
            }
        },
    };
}
// A graphic equaliser: one vertical slider per band of EQ_BANDS, ±12 dB, set
// into gains() (read each time: presets replace the array). Returns what
// shows the gains again after they were changed elsewhere.
function buildEq(container, gains, changed) {
    const shows = [];
    EQ_BANDS.forEach((hz, i) => {
        const label = document.createElement("label");
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = "-12";
        slider.max = "12";
        slider.step = "1";
        const name = hz >= 1000 ? `${hz / 1000}k` : String(hz);
        const show = () => {
            const db = gains()[i];
            slider.value = String(db);
            slider.title = `${name} Hz: ${db > 0 ? "+" : ""}${db} dB (double-click: 0)`;
        };
        show();
        shows.push(show);
        slider.addEventListener("input", () => {
            gains()[i] = Number(slider.value);
            show();
            changed();
        });
        slider.addEventListener("dblclick", () => {
            gains()[i] = 0;
            show();
            changed();
        });
        label.append(slider, name);
        container.append(label);
    });
    return () => shows.forEach((show) => show());
}
// Receive audio processing (stage 8, D39, D49): the audio band follows the
// passband sliders; a click on the audio spectrum sets the manual notch;
// automatic notch and noise reduction run in the "rx-dsp" worklet. Settings
// are kept on the account.
const RX_DSP_KEY = "remoteg90ctl.rxDsp";
const SPECTRUM_MAX_HZ = 4000;
// The stored equaliser, or flat when missing or not valid.
function rxEq(value) {
    const valid = Array.isArray(value) &&
        value.length === EQ_BANDS.length &&
        value.every((g) => typeof g === "number" && Number.isFinite(g) && g >= -12 && g <= 12);
    return valid ? [...value] : EQ_BANDS.map(() => 0);
}
// Passband relative to the dial (LSB negative) -> audio band; null: no filter.
function audioBand(band) {
    if (band === null)
        return null;
    const [a, b] = band;
    if (a < 0 && b > 0)
        return [0, Math.max(-a, b)]; // AM, FM: both sides
    return [Math.min(Math.abs(a), Math.abs(b)), Math.max(Math.abs(a), Math.abs(b))];
}
function installRxDsp(audio, radio) {
    const anf = el("rx-anf");
    const nr = el("rx-nr");
    const level = el("rx-nr-level");
    const notchText = el("rx-notch");
    const notchClear = el("rx-notch-clear");
    const latency = el("rx-latency");
    const canvas = el("rx-spectrum");
    let stored = { anf: false, nr: "off", level: 0.5, notch: null, eq: rxEq(null) };
    try {
        stored = { ...stored, ...JSON.parse(loadSetting(RX_DSP_KEY) ?? "{}") };
    }
    catch {
        // defaults
    }
    stored.eq = rxEq(stored.eq);
    let band = null;
    let cw = false;
    // Set below: one drawing of the spectrum (band, notch) while not listening.
    let redrawRx = () => { };
    const apply = () => {
        redrawRx();
        anf.checked = stored.anf;
        anf.disabled = cw;
        nr.value = stored.nr;
        level.value = String(Math.round(stored.level * 100));
        level.disabled = stored.nr === "off";
        notchText.textContent = stored.notch === null ? "notch: right-click the spectrum" : `notch ${stored.notch} Hz`;
        notchClear.hidden = stored.notch === null;
        // The automatic notch would remove the CW signal itself (AGENTS.md stage 8).
        audio.setDsp({ anf: stored.anf && !cw, nr: stored.nr, level: stored.level });
        audio.setNotch(stored.notch);
        audio.setEq(stored.eq);
        audio.setBand(band);
        saveSetting(RX_DSP_KEY, JSON.stringify(stored));
    };
    radio.onPassband = (passband, mode) => {
        band = audioBand(passband);
        cw = mode === "CW" || mode === "CW-R";
        anf.title = cw ? "Automatic notch: off in CW (it would remove the signal)" : "Automatic notch: removes steady tones (carriers, heterodynes)";
        apply();
    };
    radio.refreshPassband();
    anf.addEventListener("change", () => {
        stored.anf = anf.checked;
        apply();
    });
    nr.addEventListener("change", () => {
        stored.nr = nr.value;
        apply();
    });
    level.addEventListener("input", () => {
        stored.level = Number(level.value) / 100;
        apply();
    });
    notchClear.addEventListener("click", () => {
        stored.notch = null;
        apply();
    });
    const showEq = buildEq(el("rx-eq"), () => stored.eq, () => apply());
    el("rx-eq-flat").addEventListener("click", () => {
        stored.eq = rxEq(null);
        showEq();
        apply();
    });
    // Click: the filter's lower edge, Shift + click: its upper edge; right-click:
    // the notch (owner's choice 2026-11-15).
    const audioHz = (e) => {
        const rect = canvas.getBoundingClientRect();
        return Math.max(0, ((e.clientX - rect.left) / rect.width) * SPECTRUM_MAX_HZ);
    };
    canvas.addEventListener("click", (e) => radio.setAudioEdge(e.shiftKey ? "high" : "low", audioHz(e)));
    canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        stored.notch = Math.round(audioHz(e) / 10) * 10;
        apply();
    });
    apply();
    // The spectrum of what is played (after every block), drawn while listening.
    let latencyText = "";
    const draw = () => {
        // Not during FT8/FT4 reception (owner's request 2026-11-21): the decoder
        // needs the CPU, and the spectrum of FT8 tones says little.
        const ctx = drawAudioSpectrum(canvas, audio.digitalOn ? null : audio.analyser);
        if (ctx === null)
            return;
        const x = (hz) => (hz / SPECTRUM_MAX_HZ) * canvas.width;
        if (band !== null) {
            ctx.fillStyle = "rgba(255, 255, 255, 0.08)";
            ctx.fillRect(x(band[0]), 0, x(Math.min(band[1], SPECTRUM_MAX_HZ)) - x(band[0]), canvas.height);
        }
        if (stored.notch !== null) {
            ctx.strokeStyle = "#ff6060";
            ctx.beginPath();
            ctx.moveTo(x(stored.notch), 0);
            ctx.lineTo(x(stored.notch), canvas.height);
            ctx.stroke();
        }
        const text = audio.running ? `processing ${audio.dspLatencyMs.toFixed(0)} ms` : "";
        if (text !== latencyText)
            latency.textContent = latencyText = text;
    };
    redrawRx = spectrumLoop(draw, () => audio.analyser !== null && !audio.digitalOn);
}
// Transmit audio processing (stage 8, D49): settings kept on the account; the
// spectrum shows only what is sent, so it is empty outside a transmission
// (AGENTS.md, stage 8).
const TX_DSP_KEY = "remoteg90ctl.txDsp";
const TX_PRESET_KEYS = {
    custom1: "remoteg90ctl.txPresetCustom1",
    custom2: "remoteg90ctl.txPresetCustom2",
};
const TX_PRESET_LEGACY_KEY = "remoteg90ctl.txPresetCustom"; // the single slot of the first version
const TX_PRESET_CHOSEN_KEY = "remoteg90ctl.txPreset"; // the preset chosen or saved into last
// Draws a 0-4 kHz audio spectrum of `analyser` (none: an empty frame).
// One buffer per analyser, reused frame after frame.
const SPECTRUM_BINS = new WeakMap();
// The audio spectra are redrawn at most this often (a spectrum needs no 60 fps:
// every frame the page presents makes the browser and the desktop work).
const SPECTRUM_FPS = 15;
// Runs `draw` at most SPECTRUM_FPS times a second while `active()` holds and the
// page is visible, and once more when it stops (to show the empty grid); no
// frames at all otherwise.
// The returned function asks for one drawing while inactive (a setting, the
// filter or the size changed).
function spectrumLoop(draw, active) {
    let shown = true; // the last drawing showed an active spectrum
    let dirty = true;
    window.setInterval(() => {
        if (document.hidden)
            return;
        const now = active();
        if (now || shown || dirty)
            draw();
        shown = now;
        dirty = false;
    }, 1000 / SPECTRUM_FPS);
    window.addEventListener("resize", () => (dirty = true));
    return () => {
        dirty = true;
    };
}
function drawAudioSpectrum(canvas, analyser, color = "#7fc8ff") {
    const ctx = canvas.getContext("2d");
    if (ctx === null)
        return null;
    // Setting the size reallocates the canvas even when it is the same.
    if (canvas.width !== canvas.clientWidth)
        canvas.width = canvas.clientWidth;
    if (canvas.height !== canvas.clientHeight)
        canvas.height = canvas.clientHeight;
    const { width, height } = canvas;
    ctx.fillStyle = "#05070d";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#6b7a90";
    ctx.font = "10px sans-serif";
    for (let hz = 1000; hz < SPECTRUM_MAX_HZ; hz += 1000) {
        ctx.fillText(`${hz / 1000}k`, (hz / SPECTRUM_MAX_HZ) * width + 2, 10);
    }
    if (analyser === null)
        return ctx;
    let bins = SPECTRUM_BINS.get(analyser);
    if (bins === undefined) {
        bins = new Float32Array(analyser.frequencyBinCount);
        SPECTRUM_BINS.set(analyser, bins);
    }
    analyser.getFloatFrequencyData(bins);
    const hzPerBin = analyser.context.sampleRate / analyser.fftSize;
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (let px = 0; px < width; px++) {
        const db = bins[Math.floor(((px / width) * SPECTRUM_MAX_HZ) / hzPerBin)];
        const y = height - ((Math.max(-110, db) + 110) / 90) * height;
        if (px === 0)
            ctx.moveTo(px, y);
        else
            ctx.lineTo(px, y);
    }
    ctx.stroke();
    return ctx;
}
function installTxDsp(tx) {
    let stored = { ...TX_DSP_DEFAULTS, eq: [...TX_DSP_DEFAULTS.eq] };
    try {
        const saved = JSON.parse(loadSetting(TX_DSP_KEY) ?? "{}");
        stored = { ...stored, ...saved };
        if (!Array.isArray(stored.eq) || stored.eq.length !== EQ_BANDS.length)
            stored.eq = [...TX_DSP_DEFAULTS.eq];
    }
    catch {
        // defaults
    }
    // What the controls show again after a preset was loaded.
    const refreshers = [];
    const range = (id, key, unit) => {
        const input = el(id);
        const value = el(`${id}-value`);
        const show = () => {
            value.textContent = key === "lowCut" && stored.lowCut === 0 ? `${unit} (off)` : unit;
        };
        input.value = String(stored[key]);
        show();
        refreshers.push(() => {
            input.value = String(stored[key]);
            show();
        });
        numberField(input, (v) => {
            stored[key] = v;
            show();
            apply();
        });
    };
    const checkbox = (id, key) => {
        const input = el(id);
        input.checked = stored[key];
        refreshers.push(() => (input.checked = stored[key]));
        input.addEventListener("change", () => {
            stored[key] = input.checked;
            apply();
        });
    };
    // Presets: DX and Local are fixed starting points; Custom 1 and Custom 2 are
    // the operator's own, empty (the neutral chain) until a Save puts the current
    // settings in one of them.
    const customs = {
        custom1: parseCustom(loadSetting(TX_PRESET_KEYS.custom1) ?? loadSetting(TX_PRESET_LEGACY_KEY)),
        custom2: parseCustom(loadSetting(TX_PRESET_KEYS.custom2)),
    };
    const presetSelect = el("tx-preset");
    // The preset chosen or saved into last: two presets may hold the same values.
    const remembered = loadSetting(TX_PRESET_CHOSEN_KEY);
    let chosen = isPresetName(remembered) ? remembered : null;
    const choose = (name) => {
        chosen = name;
        saveSetting(TX_PRESET_CHOSEN_KEY, name);
    };
    const apply = () => {
        tx.setDsp({ ...stored, eq: [...stored.eq] });
        saveSetting(TX_DSP_KEY, JSON.stringify(stored));
        // After any edit the selector says which preset this still is, or "edited".
        presetSelect.value = presetShown(stored, chosen, customs);
    };
    presetSelect.addEventListener("change", () => {
        const name = presetSelect.value;
        choose(name);
        const next = withPreset(stored, name, customs);
        Object.assign(stored, next, { eq: [...next.eq] });
        for (const refresh of refreshers)
            refresh();
        apply();
    });
    for (const slot of ["custom1", "custom2"]) {
        const button = el(`tx-preset-save-${slot.slice(-1)}`);
        const label = button.textContent;
        button.addEventListener("click", () => {
            customs[slot] = toCustom(stored);
            saveSetting(TX_PRESET_KEYS[slot], JSON.stringify(customs[slot]));
            choose(slot);
            apply(); // the selector now reads this Custom slot
            button.textContent = "Saved";
            window.setTimeout(() => (button.textContent = label), 1200);
        });
    }
    range("tx-lowcut", "lowCut", "Hz");
    range("tx-highcut", "highCut", "Hz");
    range("tx-gate-db", "gateDb", "dBFS");
    range("tx-comp-db", "thresholdDb", "dBFS");
    range("tx-makeup", "makeupDb", "dB");
    checkbox("tx-gate", "gate");
    checkbox("tx-comp", "compressor");
    checkbox("tx-monitor", "monitor");
    const ratio = el("tx-comp-ratio");
    ratio.value = String(stored.ratio);
    refreshers.push(() => (ratio.value = String(stored.ratio)));
    ratio.addEventListener("change", () => {
        stored.ratio = Number(ratio.value);
        apply();
    });
    refreshers.push(buildEq(el("tx-eq"), () => stored.eq, apply));
    apply();
    const canvas = el("tx-spectrum");
    // Click: low cut, Shift + click: high cut, at that audio frequency, within
    // the sliders' ranges (owner's request 2026-11-15).
    canvas.addEventListener("click", (e) => {
        const rect = canvas.getBoundingClientRect();
        const hz = ((e.clientX - rect.left) / rect.width) * SPECTRUM_MAX_HZ;
        if (e.shiftKey)
            stored.highCut = Math.max(1500, Math.min(4000, Math.round(hz / 50) * 50));
        else
            stored.lowCut = Math.max(0, Math.min(500, Math.round(hz / 10) * 10));
        for (const refresh of refreshers)
            refresh();
        apply();
    });
    const draw = () => {
        const local = tx.localSpectrumAnalyser;
        const ctx = drawAudioSpectrum(canvas, local ?? tx.spectrumAnalyser, local !== null ? "#8a8f98" : undefined);
        if (local !== null && ctx !== null) {
            ctx.fillStyle = "#c8d3e0";
            ctx.fillText("playback — not sent", 4, canvas.height - 4);
        }
        // The processing delay stays visible (AGENTS.md, stage 8), inside the
        // spectrum instead of beside the controls (owner's request 2026-11-09).
        if (ctx !== null && tx.ready) {
            const text = `delay ${tx.dspLatencyMs} ms`;
            ctx.fillStyle = "#8fa3bb";
            ctx.fillText(text, canvas.width - ctx.measureText(text).width - 4, canvas.height - 4);
        }
    };
    // Only while something goes to the radio, or a recording plays in the browser;
    // once more when the delay to show changes (settings, the chain getting ready).
    let shownDelay = "";
    spectrumLoop(draw, () => {
        const delay = tx.ready ? String(tx.dspLatencyMs) : "";
        const changed = delay !== shownDelay;
        shownDelay = delay;
        return changed || tx.localSpectrumAnalyser !== null || tx.spectrumAnalyser !== null;
    });
}
const RENEW_MS = 500;
const TX_CALL_INTERVAL_KEY = "remoteg90ctl.callInterval";
// Automatic calling stops after this long without the operator touching the
// page (owner's decision 2026-11-09).
const CALL_WATCHDOG_MS = 2 * 60 * 1000; // AGENTS.md rule 4: the lease is renewed every 0.5 s
// "Transmit": push to talk, held (owner's decision). The page asks once per
// session before transmitting on the antenna (D46); the server allows it only
// in U-D/L-D, and the guard enforces the lease and every limit.
function installTxControls(connection) {
    const button = el("ptt");
    const level = el("tx-level");
    const bitrate = el("tx-bitrate");
    bitrate.value = loadSetting(TX_BITRATE_KEY) ?? "24";
    bitrate.addEventListener("change", () => saveSetting(TX_BITRATE_KEY, bitrate.value));
    const tx = new TxAudio();
    installTxDsp(tx);
    tx.send = (frame) => connection.sendBinary(frame);
    tx.backlog = () => connection.bufferedAmount;
    const gain = el("tx-gain");
    const gainValue = el("tx-gain-value");
    const setGain = (db) => {
        gain.value = String(db);
        gainValue.textContent = "dB";
        tx.gain = 10 ** (db / 20);
        saveSetting(TX_GAIN_KEY, String(db));
    };
    const savedGain = Number(loadSetting(TX_GAIN_KEY) ?? "0");
    setGain(Number.isFinite(savedGain) ? Math.max(-20, Math.min(20, Math.round(savedGain))) : 0);
    numberField(gain, setGain);
    gain.addEventListener("dblclick", () => setGain(0));
    const bar = el("tx-meter-bar");
    // The number shows the peak of the last 0.5 s, the bar the peak of the last
    // 0.25 s (owner's choice), so neither flickers.
    let peak = -Infinity;
    let barPeak = -Infinity;
    let shownAt = 0;
    let barAt = 0;
    tx.onLevel = (dbfs) => {
        peak = Math.max(peak, dbfs);
        barPeak = Math.max(barPeak, dbfs);
        const now = performance.now();
        if (now - shownAt >= 500) {
            level.textContent = `${Math.max(-60, peak).toFixed(0)} dBFS`;
            peak = -Infinity;
            shownAt = now;
        }
        if (now - barAt >= 250) {
            // The bar spans -25..0 dBFS (owner's choice); green, yellow above -12, red above -3.
            bar.style.width = `${(Math.max(0, barPeak + 25) / 25) * 100}%`;
            bar.style.background = barPeak > -3 ? "#c01010" : barPeak > -12 ? "#d6b400" : "#2e9e44";
            barPeak = -Infinity;
            barAt = now;
        }
    };
    if (!TxAudio.supported()) {
        button.disabled = true;
        errorBox.show("Transmit: browser without WebCodecs (AudioEncoder) or without a microphone");
        return;
    }
    let confirmed = false; // once per page session
    let held = false;
    let lease = null;
    let renew;
    // The id of our last tx_request: its refusal, whatever the code, ends the
    // transmission and an automatic call (code review 2026-11-10, point 9).
    let txRequestId = 0;
    // The voice path while transmitting (2026-11-21), next to the meter: the
    // delay from the microphone to the radio, estimated from its parts.
    const txState = el("tx-audio-state");
    txState.title =
        "delay: microphone to radio, estimated: the browser's processing (about 30 ms) + voice waiting " +
            "in the browser + half the renewal's round trip + voice waiting on the server + the card; " +
            "margin: the server's adaptive margin; gaps: times the voice ran out on the server; " +
            "skip: frames the browser left out (link too slow)";
    let renewId = 0;
    let renewSentAt = 0;
    const showTxState = (message) => {
        const a = message.audio;
        if (a === undefined)
            return;
        const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
        const oneWay = (performance.now() - renewSentAt) / 2;
        const delay = TX_PAGE_MS + tx.backlogMs + oneWay + num(a.queued_ms) + num(a.card_ms);
        txState.textContent =
            `delay ~${delay.toFixed(0)} ms · margin ${num(a.margin_ms)} · ` +
                `${num(a.ran_dry)} gaps · ${tx.skipped} skip`;
    };
    const end = (why) => {
        window.clearInterval(renew);
        renew = undefined;
        txState.textContent = "";
        tx.sending = false;
        if (lease !== null)
            connection.send("tx_release", { lease });
        lease = null;
        button.classList.remove("transmitting");
        if (why !== "")
            errorBox.show(why);
    };
    // Where and how strong the radio would transmit, for the confirmations.
    const onAir = () => {
        // In split the radio transmits on the other VFO: name that frequency.
        const split = el("split-badge").dataset.state === "on";
        const where = split
            ? `${el("vfo-other").textContent ?? ""} (split)`
            : (el("frequency").textContent ?? "");
        return `${where}, ${el("power").textContent ?? ""}`;
    };
    // Once per page session for PTT (D46).
    const confirmAntenna = (then) => {
        if (confirmed)
            return true;
        confirmed = window.confirm(`You are transmitting ON THE ANTENNA: ${onAir()}. Continue? ${then}`);
        return confirmed;
    };
    const prepareMic = async () => {
        const raw = bitrate.value === "raw";
        await tx.prepare(raw ? "raw" : "opus", raw ? 0 : Number(bitrate.value));
    };
    const press = async () => {
        if (calling)
            stopCall(""); // PTT takes over from an automatic call
        if (!confirmed) {
            // The dialog blocks the page, so a release during it may go unseen: the
            // confirmation only unlocks PTT, and transmitting needs a fresh press.
            confirmAntenna("(Then press and hold PTT again.)");
            held = false;
            return;
        }
        held = true;
        await prepareMic();
        if (!held)
            return; // released while the microphone was opening
        txRequestId = connection.send("tx_request", { antenna_confirmed: true });
    };
    const release = () => {
        held = false;
        if (lease !== null || tx.sending)
            end("");
    };
    // Recorder and automatic call (owner's decisions 2026-11-09): the recording
    // (at most 15 s) lives in this page only. A call is an ordinary transmission
    // — a new lease each time, renewed by this page, with every limit — that
    // sends the recording instead of the microphone; the next one starts after
    // the pause. Calling stops on Stop, PTT, a hidden page, a lost connection,
    // any refusal or ended lease, and after CALL_WATCHDOG_MS without the
    // operator touching the page (like WSJT-X's Tx watchdog; cannot be turned off).
    const recordButton = el("rec-record");
    const playButton = el("rec-play");
    const callButton = el("rec-call");
    const interval = el("rec-interval");
    let recorded = null;
    let localPlay = null;
    let calling = false;
    let callTimer;
    let lastActivity = performance.now();
    for (const kind of ["pointerdown", "keydown"]) {
        document.addEventListener(kind, () => (lastActivity = performance.now()), true);
    }
    interval.value = loadSetting(TX_CALL_INTERVAL_KEY) ?? "15";
    const intervalS = () => Math.min(120, Math.max(5, Math.round(Number(interval.value) || 15)));
    interval.addEventListener("change", () => {
        interval.value = String(intervalS());
        saveSetting(TX_CALL_INTERVAL_KEY, interval.value);
    });
    const show = () => {
        recordButton.classList.toggle("recording", tx.recordingActive);
        callButton.classList.toggle("calling", calling);
        playButton.disabled = recorded === null || calling || tx.recordingActive;
        callButton.disabled = recorded === null || calling || tx.recordingActive;
        recordButton.disabled = calling;
    };
    const finishRecording = () => {
        const samples = tx.stopRecording();
        if (samples !== null)
            recorded = samples;
        show();
    };
    tx.onRecordEnd = finishRecording;
    const stopCall = (why) => {
        if (!calling)
            return;
        calling = false;
        window.clearTimeout(callTimer);
        tx.stopReplay();
        if (lease !== null || tx.sending || held)
            release();
        if (why !== "")
            errorBox.show(why);
        show();
    };
    const sendCall = () => {
        if (!calling || recorded === null)
            return;
        held = true;
        tx.startReplay(recorded); // played only once the lease is granted (tx.sending)
        txRequestId = connection.send("tx_request", { antenna_confirmed: true });
    };
    tx.onReplayEnd = () => {
        if (!calling)
            return;
        release(); // this call is over: give the lease back
        callTimer = window.setTimeout(sendCall, intervalS() * 1000);
    };
    window.setInterval(() => {
        if (!calling)
            return;
        if (performance.now() - lastActivity > CALL_WATCHDOG_MS) {
            stopCall("Automatic call stopped: 2 minutes without operator activity");
        }
    }, 1000);
    recordButton.addEventListener("click", () => {
        if (tx.recordingActive) {
            finishRecording();
            return;
        }
        void prepareMic().then(() => {
            tx.startRecording();
            show();
        });
    });
    playButton.addEventListener("click", () => {
        if (recorded === null)
            return;
        localPlay?.stop();
        localPlay = tx.playLocally(recorded);
    });
    callButton.addEventListener("click", () => {
        if (recorded === null)
            return;
        // Every automatic call is approved on its own (owner's request 2026-11-09).
        const approved = window.confirm(`Start the AUTOMATIC CALL on the antenna: ${onAir()}.\n\n` +
            `The recording (${(recorded.length / 48000).toFixed(1)} s) is transmitted, then again every ` +
            `${intervalS()} s, until you press Stop — or 2 minutes without you touching the page. Start?`);
        if (!approved)
            return;
        confirmed = true; // also covers the per-session antenna confirmation
        calling = true;
        lastActivity = performance.now();
        show();
        void prepareMic().then(sendCall);
    });
    el("rec-stop").addEventListener("click", () => {
        if (tx.recordingActive)
            finishRecording();
        localPlay?.stop();
        localPlay = null;
        stopCall("");
    });
    connection.onText = (message) => {
        if (typeof message.lease === "string" && typeof message.lease_timeout_s === "number") {
            if (!held) {
                lease = message.lease;
                end(""); // released before the answer came: give the lease back at once
                return;
            }
            lease = message.lease;
            tx.skipped = 0;
            tx.sending = true;
            button.classList.add("transmitting");
            renew = window.setInterval(() => {
                if (lease === null)
                    return;
                renewSentAt = performance.now();
                renewId = connection.send("tx_renew", { lease });
            }, RENEW_MS);
        }
        else if (renewId !== 0 && message.id === renewId && message.ok === true) {
            showTxState(message);
        }
        else if (message.event === "tx_ended" && message.lease === lease) {
            lease = null; // the server or guard ended it (limit, silence, error)
            stopCall("");
            end(`transmission ended: ${String(message.reason)}`);
        }
        else if (message.ok === false && txRequestId !== 0 && message.id === txRequestId) {
            stopCall("");
            end(""); // the error window shows the refusal (Radio.handle)
        }
    };
    connection.onClose = () => {
        lease = null; // the server ends a lease whose connection is gone
        stopCall("Automatic call stopped: the connection to the server broke");
        end("");
    };
    button.addEventListener("pointerdown", (e) => {
        button.setPointerCapture(e.pointerId);
        void press();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
        button.addEventListener(name, release);
    }
    // Keyboard: left Ctrl + left Alt held together (owner's choice); letting
    // go of either one ends the transmission, like releasing the button.
    const hotkey = ["ControlLeft", "AltLeft"];
    const down = new Set();
    window.addEventListener("keydown", (e) => {
        if (!hotkey.includes(e.code))
            return;
        e.preventDefault();
        down.add(e.code);
        if (!e.repeat && !held && hotkey.every((k) => down.has(k)))
            void press();
    });
    window.addEventListener("keyup", (e) => {
        if (!hotkey.includes(e.code))
            return;
        e.preventDefault();
        down.delete(e.code);
        release();
    });
    window.addEventListener("blur", () => {
        down.clear(); // keys released while the window was elsewhere are never reported
        if (!calling)
            release(); // a call may go on while another window has focus
    });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden)
            return;
        stopCall("Automatic call stopped: the page was hidden");
        release();
    });
}
async function api(method, path, body) {
    try {
        const response = await fetch(path, {
            method,
            headers: body === undefined ? {} : { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
            credentials: "same-origin",
        });
        return (await response.json());
    }
    catch {
        return { ok: false, error: "network", message: "the server does not answer" };
    }
}
class AccountUi {
    constructor() {
        this.login = el("login-dialog");
        this.create = el("create-dialog");
        this.menu = el("account-dialog");
        this.takeOver = el("takeover-dialog");
        this.button = el("account-open");
        this.firstAccount = false;
        this.connection = null;
        for (const dialog of [this.login, this.create, this.takeOver]) {
            dialog.addEventListener("cancel", (e) => {
                if (dialog !== this.create || this.firstAccount)
                    e.preventDefault(); // nothing behind it
            });
        }
        el("takeover-wait").addEventListener("click", () => location.reload());
        el("login-form").addEventListener("submit", (e) => {
            e.preventDefault();
            void this.submitLogin();
        });
        el("create-form").addEventListener("submit", (e) => {
            e.preventDefault();
            void this.submitCreate();
        });
        el("password-form").addEventListener("submit", (e) => {
            e.preventDefault();
            void this.submitPassword();
        });
        el("create-cancel").addEventListener("click", () => this.create.close());
        el("account-close").addEventListener("click", () => this.menu.close());
        el("logout").addEventListener("click", () => void this.logout());
        el("add-account").addEventListener("click", () => {
            this.menu.close();
            this.openCreate(false);
        });
        this.button.addEventListener("click", () => {
            el("account-error").textContent = "";
            this.menu.showModal();
        });
    }
    // The page's connection, once logged in: checked again before reconnecting.
    attach(connection) {
        this.connection = connection;
        connection.checkSession = async () => {
            const session = await this.fetchSession();
            if (session.error === "network")
                return null; // no answer: not a logout
            return this.evaluate(session) !== null;
        };
    }
    // On load: the account, or null (a dialog shows).
    async check() {
        return this.evaluate(await this.fetchSession());
    }
    async fetchSession() {
        return (await api("GET", "/api/session"));
    }
    // The account, or null: then the dialog that fits (login, first account,
    // the station in use) shows.
    evaluate(session) {
        if (session.accounts_unusable) {
            errorBox.show("The accounts file on the server is unusable: nobody can log in (see the system log).");
            return null;
        }
        if (session.first_account_needed) {
            this.openCreate(true);
            return null;
        }
        if (session.account && session.busy) {
            // Another account has the station: this page cannot open. The
            // administrator sees who it is and may close its page (owner's decision
            // 2026-11-13); nobody is thrown out without that click.
            this.connection?.stop();
            if (session.account.admin) {
                this.showTakeOver(session.busy, session.busy_since ?? null, session.busy_transmitting === true);
            }
            else {
                el("login-callsign").value = session.account.callsign;
                this.showLogin(`${session.busy} is logged in`);
            }
            return null;
        }
        if (session.account) {
            this.loggedIn(session.account);
            return session.account;
        }
        this.connection?.stop();
        this.showLogin(session.busy ? `${session.busy} is logged in` : "");
        return null;
    }
    showTakeOver(busy, since, transmitting) {
        this.button.hidden = true;
        const time = since === null ? "" : ` since ${new Date(since * 1000).toISOString().slice(11, 16)} UTC`;
        el("takeover-text").textContent =
            `${busy} is using the station${time}${transmitting ? " and is transmitting now" : ""}.`;
        const go = el("takeover-go");
        go.textContent = `Close ${busy}'s page and take over`;
        go.onclick = () => void this.submitTakeOver(busy, transmitting);
        el("takeover-error").textContent = "";
        if (!this.takeOver.open)
            this.takeOver.showModal();
    }
    async submitTakeOver(busy, transmitting) {
        const question = `Close ${busy}'s page and log ${busy} out?` +
            (transmitting ? ` ${busy} is transmitting: the transmission ends at once.` : "");
        if (!window.confirm(question))
            return;
        const answer = await api("POST", "/api/take-over", {});
        if (answer.ok !== true) {
            el("takeover-error").textContent = String(answer.message ?? answer.error);
            return;
        }
        location.reload(); // the station is free: the page starts with this account
    }
    showLogin(message) {
        this.button.hidden = true;
        el("login-error").textContent = message;
        if (!this.login.open)
            this.login.showModal();
    }
    openCreate(first) {
        this.firstAccount = first;
        el("create-title").textContent = first ? "Create the administrator account" : "Add an account";
        el("create-note").hidden = !first;
        el("create-cancel").hidden = first;
        el("create-error").textContent = "";
        el("create-form").reset();
        this.create.showModal();
    }
    loggedIn(account) {
        if (this.login.open)
            this.login.close();
        this.button.textContent = account.callsign;
        this.button.hidden = false;
        el("account-title").textContent = account.callsign;
        el("account-info").textContent =
            `QTH locator ${account.locator}${account.admin ? " · administrator: manages the accounts, restarts the server" : ""}`;
        el("add-account").hidden = !account.admin;
        el("server-restart").hidden = !account.admin;
        el("system-log-open").hidden = !account.admin; // the server's log: administrator only
    }
    async submitLogin() {
        const answer = await api("POST", "/api/login", {
            callsign: el("login-callsign").value,
            password: el("login-password").value,
        });
        el("login-password").value = "";
        if (answer.ok !== true) {
            el("login-error").textContent = String(answer.message ?? answer.error);
            return;
        }
        location.reload(); // starts the page with the account's settings
    }
    async submitCreate() {
        const error = el("create-error");
        const password = el("create-password").value;
        if (password !== el("create-password2").value) {
            error.textContent = "the passwords differ";
            return;
        }
        const body = {
            callsign: el("create-callsign").value,
            locator: el("create-locator").value,
            password,
        };
        const answer = await api("POST", this.firstAccount ? "/api/first-account" : "/api/accounts", body);
        if (answer.ok !== true) {
            error.textContent = String(answer.message ?? answer.error);
            return;
        }
        this.create.close();
        const account = answer.account;
        if (this.firstAccount)
            location.reload();
        else
            errorBox.show(`Account ${account.callsign} added.`, "info");
    }
    async submitPassword() {
        const error = el("account-error");
        const password = el("new-password").value;
        if (password !== el("new-password2").value) {
            error.textContent = "the new passwords differ";
            return;
        }
        const answer = await api("POST", "/api/password", {
            old_password: el("old-password").value,
            new_password: password,
        });
        if (answer.ok !== true) {
            error.textContent = String(answer.message ?? answer.error);
            return;
        }
        el("password-form").reset();
        this.menu.close(); // the server closes the page; it reconnects with the new session
    }
    async logout() {
        this.menu.close();
        this.connection?.stop();
        window.clearTimeout(settingsTimer); // a pending save would be refused after logout
        await api("PUT", "/api/settings", { page: Object.fromEntries(settings) });
        await api("POST", "/api/logout", {});
        location.reload();
    }
}
// Station log (stage 9, D30, D50): this account's own log. The form takes the
// frequency and mode from the radio; the time is the moment of saving (owner's
// decision 2026-11-09). Saving never touches the radio.
const LOG_MODES = ["SSB", "USB", "LSB", "CW", "AM", "FM", "FT8", "FT4", "RTTY"];
const RADIO_TO_LOG = {
    USB: "USB",
    "U-D": "USB",
    LSB: "LSB",
    "L-D": "LSB",
    CW: "CW",
    "CW-R": "CW",
    AM: "AM",
    FM: "FM",
};
function defaultRst(mode) {
    if (mode === "CW" || mode === "RTTY")
        return "599";
    if (mode === "FT8" || mode === "FT4")
        return "";
    return "59";
}
class StationLog {
    constructor(connection, radio, callsign) {
        this.connection = connection;
        this.radio = radio;
        this.box = el("logbook");
        this.form = el("qso-form");
        this.call = el("qso-call");
        this.sent = el("qso-rst-sent");
        this.rcvd = el("qso-rst-rcvd");
        this.mode = el("qso-mode");
        this.freq = el("qso-freq");
        this.date = el("qso-date");
        this.time = el("qso-time");
        this.error = el("qso-error");
        this.worked = el("qso-worked");
        this.search = el("log-search");
        this.rows = el("log-rows");
        this.editing = null;
        el("logbook-name").textContent = `Log — ${callsign}`;
        for (const mode of LOG_MODES)
            this.mode.add(new Option(mode, mode));
        el("log-open").addEventListener("click", () => this.toggle());
        el("logbook-close").addEventListener("click", () => (this.box.hidden = true));
        el("qso-cancel").addEventListener("click", () => this.clear());
        this.form.addEventListener("submit", (e) => {
            e.preventDefault();
            void this.save();
        });
        this.mode.addEventListener("change", () => {
            // Reports still at the old mode's default follow the new mode.
            for (const input of [this.sent, this.rcvd]) {
                if (["59", "599", ""].includes(input.value))
                    input.value = defaultRst(this.mode.value);
            }
        });
        this.call.addEventListener("input", () => {
            window.clearTimeout(this.workedTimer);
            this.workedTimer = window.setTimeout(() => void this.showWorked(), 300);
        });
        this.search.addEventListener("input", () => {
            window.clearTimeout(this.searchTimer);
            this.searchTimer = window.setTimeout(() => void this.refresh(), 300);
        });
        el("log-import").addEventListener("change", (e) => void this.importFile(e));
        connection.onEvent = (message) => {
            if (message.event === "log_changed" && !this.box.hidden)
                void this.refresh();
        };
    }
    toggle() {
        this.box.hidden = !this.box.hidden;
        if (!this.box.hidden) {
            if (this.editing === null)
                this.clear();
            void this.refresh();
            this.call.focus();
        }
    }
    // A QSO from another window (RTTY): the log opens with the call and the mode.
    startQso(call, mode) {
        this.box.hidden = false;
        this.clear();
        if (LOG_MODES.includes(mode)) {
            this.mode.value = mode;
            this.sent.value = this.rcvd.value = defaultRst(mode);
        }
        this.call.value = call;
        void this.refresh();
        this.call.focus();
    }
    // A new QSO: frequency and mode from the radio, default reports.
    clear() {
        this.editing = null;
        this.box.classList.remove("editing");
        this.form.reset();
        const mode = RADIO_TO_LOG[this.radio.modeName ?? ""] ?? "SSB";
        this.mode.value = mode;
        this.sent.value = this.rcvd.value = defaultRst(mode);
        const hz = this.radio.frequencyHz;
        this.freq.value = hz === null ? "" : (hz / 1e6).toFixed(6);
        this.error.textContent = this.worked.textContent = "";
        el("qso-save").textContent = "Save";
        this.call.focus();
    }
    fields() {
        const value = (id) => el(id).value;
        const fields = {
            call: this.call.value,
            rst_sent: this.sent.value,
            rst_rcvd: this.rcvd.value,
            name: value("qso-name"),
            qth: value("qso-qth"),
            gridsquare: value("qso-grid"),
            comment: value("qso-comment"),
            mode: this.mode.value,
            freq_hz: Math.round(Number(this.freq.value.replace(",", ".")) * 1e6),
        };
        if (this.editing !== null) {
            fields.qso_date = this.date.value;
            fields.time_on = this.time.value;
            fields.qso_id = this.editing;
        }
        return fields;
    }
    async save() {
        const answer = await this.connection.request(this.editing === null ? "log_add" : "log_update", this.fields());
        if (answer.ok !== true) {
            this.error.textContent = String(answer.message ?? answer.error);
            return;
        }
        const qso = answer.qso;
        this.clear();
        if (qso.dupe)
            this.worked.textContent = `${qso.call} saved — a dupe (same band and mode today)`;
        else
            this.worked.textContent = `${qso.call} saved`;
        await this.refresh();
    }
    async showWorked() {
        const call = this.call.value.trim();
        if (call.length < 3 || this.editing !== null) {
            this.worked.textContent = "";
            return;
        }
        const answer = await this.connection.request("log_worked", { call });
        const worked = (answer.worked ?? []);
        this.worked.textContent =
            worked.length === 0
                ? "new"
                : `worked: ${worked.map((q) => `${formatDate(q.qso_date)} ${q.band} ${q.mode}`).join(", ")}`;
    }
    async refresh() {
        const answer = await this.connection.request("log_list", { search: this.search.value, limit: 200 });
        if (answer.ok !== true) {
            this.error.textContent = String(answer.message ?? answer.error);
            return;
        }
        el("log-total").textContent = `${String(answer.total)} QSOs`;
        this.rows.replaceChildren(...answer.qsos.map((qso) => this.row(qso)));
    }
    row(qso) {
        const tr = document.createElement("tr");
        if (qso.dupe) {
            tr.className = "dupe";
            tr.title = "Dupe: the same call, band and mode on the same UTC day";
        }
        const cells = [
            formatDate(qso.qso_date),
            `${qso.time_on.slice(0, 2)}:${qso.time_on.slice(2, 4)}`,
            qso.call,
            qso.band,
            (qso.freq_hz / 1e6).toFixed(4),
            qso.mode,
            qso.rst_sent,
            qso.rst_rcvd,
            qso.name,
            qso.gridsquare,
            qso.comment,
        ];
        for (const text of cells) {
            const td = document.createElement("td");
            td.textContent = text;
            tr.append(td);
        }
        const remove = document.createElement("button");
        remove.textContent = "×";
        remove.title = "Delete this QSO";
        remove.addEventListener("click", (e) => {
            e.stopPropagation();
            if (window.confirm(`Delete the QSO with ${qso.call} on ${formatDate(qso.qso_date)}?`)) {
                void this.connection.request("log_delete", { qso_id: qso.id }).then(() => this.refresh());
            }
        });
        const td = document.createElement("td");
        td.append(remove);
        tr.append(td);
        tr.addEventListener("click", () => this.edit(qso));
        return tr;
    }
    edit(qso) {
        this.editing = qso.id;
        this.box.classList.add("editing");
        this.call.value = qso.call;
        this.sent.value = qso.rst_sent;
        this.rcvd.value = qso.rst_rcvd;
        el("qso-name").value = qso.name;
        el("qso-qth").value = qso.qth;
        el("qso-grid").value = qso.gridsquare;
        el("qso-comment").value = qso.comment;
        this.mode.value = qso.mode;
        this.freq.value = (qso.freq_hz / 1e6).toFixed(6);
        this.date.value = qso.qso_date;
        this.time.value = qso.time_on;
        el("qso-save").textContent = "Save changes";
        this.error.textContent = "";
        this.worked.textContent = `editing the QSO with ${qso.call}`;
    }
    async importFile(e) {
        const input = e.target;
        const file = input.files?.[0];
        input.value = "";
        if (file === undefined)
            return;
        try {
            const response = await fetch("/api/logbook/import", {
                method: "POST",
                body: await file.text(),
                credentials: "same-origin",
            });
            const answer = (await response.json());
            if (answer.ok !== true)
                throw new Error(String(answer.message ?? answer.error));
            this.worked.textContent =
                `import: ${String(answer.added)} added, ${String(answer.skipped)} already in the log` +
                    (Number(answer.failed) > 0 ? `, ${String(answer.failed)} not readable` : "");
        }
        catch (err) {
            errorBox.show(`ADIF import: ${String(err instanceof Error ? err.message : err)}`);
        }
        await this.refresh();
    }
}
// FT8/FT4 reception (stage 6, decisions.md D52): the radio goes to the mode's
// dial frequency in U-D, the audio to Opus 48 kbit/s, and every whole cycle of
// that audio, before the page's processing, goes to ft8ts in a Web Worker.
// Stopping brings back the frequency, mode and quality from before.
const FT8_KEY = "remoteg90ctl.ft8";
const FT8_ROWS_KEPT = 300;
const FT8_BITRATE = "48";
// FT8/FT4 transmission (stage 7, D53).
const FT8_WATCHDOG_MS = 6 * 60 * 1000; // D53: as WSJT-X's Tx watchdog
// Grids remembered from the decodes (one entry per station).
const GRIDS_KEPT = 5000;
const FT8_LEASE_AHEAD_MS = 1500; // the lease is taken this long before the cycle
// The message goes out once the previous cycle is decoded, at the latest this
// long after the cycle start (the server starts it 0.5 s in, or at once if
// later): FT8 is still decoded when started 1 s late, FT4 only a few tenths
// (review 2026-11-15; an answer decoded later waits a cycle).
const FT8_MESSAGE_DEADLINE_MS = { FT8: 1000, FT4: 300 };
// After tuning to a dial: how long the radio may take to report it before
// automatic transmissions are halted as being off the dial.
const FT8_TUNE_SETTLE_MS = 5000;
const FT8_JOIN_LATE_MS = 1000;
// TX tone limits: the whole signal in 200-2800 Hz (server/ft8_tx.py; FT4 is 83 Hz wide).
const FT8_TONE_MIN_HZ = 200;
const FT8_TONE_MAX_HZ = 2716; // a cycle already this far in is not joined
// Server time at a clock frame's arrival: the frame's time plus roughly the
// audio's way here (card block, queue, network).
const FT8_AUDIO_DELAY_MS = 150;
// The server confirms a played signal (ft8_sent) when it is over; without it
// the page gives the lease back this long after the signal should have ended,
// instead of renewing it until the hard limit (code review 2026-11-10, point 1).
const FT8_CONFIRM_GRACE_MS = 3000;
const FT8_SERVER_STALL_MS = 1000; // MAX_STALL_S of the server's player (ft8_tx.py)
// One of the FT8/FT4, RTTY and CW windows at a time (owner's request
// 2026-11-24): opening one closes the others, with their reception and any
// transmission of theirs.
const modeWindows = new Map();
function openingModeWindow(name) {
    for (const [other, close] of modeWindows)
        if (other !== name)
            close();
}
class DigitalReceiver {
    constructor(connection, radio, view, listening, mycall, locator) {
        this.connection = connection;
        this.radio = radio;
        this.view = view;
        this.listening = listening;
        this.mycall = mycall;
        this.locator = locator;
        this.box = el("ft8");
        this.modeSelect = el("ft8-mode");
        this.bandSelect = el("ft8-band");
        this.depthSelect = el("ft8-depth");
        this.run = el("ft8-run");
        this.state = el("ft8-state");
        this.clockWarning = el("ft8-clock");
        this.cycleNow = el("ft8-cycle-now");
        this.rows = el("ft8-rows");
        this.stored = { mode: "FT8", band: "20m", depth: 3, tone: 1500, level: -10, parity: 0, onlyCq: false };
        // Transmission (stage 7).
        this.toneInput = el("ft8-tone");
        this.levelInput = el("ft8-level");
        this.parityInput = el("ft8-parity");
        this.enableButton = el("ft8-enable");
        this.cqButton = el("ft8-cq");
        this.next = el("ft8-next");
        this.sequencer = null;
        this.txEnabled = false;
        this.lease = null;
        this.sentMessage = null; // what the lease's transmission sends
        this.txStart = null; // cycle being transmitted (UTC ms)
        this.txCycles = new Set();
        this.lastDecoded = null; // last cycle start with decodes in
        this.serverOffsets = [];
        this.lastActivity = performance.now();
        this.lastMarks = [];
        this.collector = null;
        this.worker = null;
        this.busy = false;
        this.busySince = 0;
        this.before = null;
        // Dial frequency of each cycle still being decoded, for the waterfall marks.
        this.dialOf = new Map();
        // The share of each cycle's audio that arrived (shown when some was lost).
        this.coverageOf = new Map();
        // Set by the page: called before reception starts (it ends a pause).
        this.beforeStart = async () => { };
        // ---- Transmission (stage 7, D53) ----
        // Each transmission is an ordinary lease taken by this page shortly before
        // its cycle and renewed every 0.5 s; the server makes and plays the signal
        // (ft8_transmit) within it. Everything stops on Halt TX, PTT, the end of
        // the QSO's sequence, a hidden page, a lost connection, any refusal or
        // ended lease, stopping the reception, and after FT8_WATCHDOG_MS without
        // the operator touching the page (cannot be turned off).
        this.tunedAt = -Infinity;
        // After RR73 or 73: the QSO in a dialog, saved to the log on the operator's word.
        // Grids heard from every station since the page opened (owner's report
        // 2026-11-14: a station answering a CQ with a report straight away never
        // sends its grid in the QSO, but it usually did in its own CQ before).
        // A priori decodes are left out: they are more likely to be false.
        this.grids = new Map();
        el("ft8-name").textContent = `FT8/FT4 — ${mycall}`;
        try {
            this.stored = { ...this.stored, ...JSON.parse(loadSetting(FT8_KEY) ?? "{}") };
        }
        catch {
            // keep the defaults
        }
        this.modeSelect.value = this.stored.mode;
        this.depthSelect.value = String(this.stored.depth);
        this.fillBands();
        modeWindows.set("ft8", () => {
            if (this.collector !== null)
                this.stop();
            this.box.hidden = true;
        });
        el("ft8-open").addEventListener("click", () => {
            if (this.box.hidden)
                openingModeWindow("ft8");
            this.box.hidden = !this.box.hidden;
        });
        el("ft8-close").addEventListener("click", () => (this.box.hidden = true));
        this.run.addEventListener("click", () => void (this.collector === null ? this.start() : this.stop()));
        this.modeSelect.addEventListener("change", () => {
            this.stored.mode = this.modeSelect.value;
            this.fillBands();
            this.changed();
        });
        this.bandSelect.addEventListener("change", () => {
            this.stored.band = this.bandSelect.value;
            this.changed();
        });
        this.depthSelect.addEventListener("change", () => {
            this.stored.depth = Number(this.depthSelect.value);
            saveSetting(FT8_KEY, JSON.stringify(this.stored));
        });
        // Only CQ: a view of the list; nothing is dropped, unticking shows all again.
        const onlyCq = el("ft8-only-cq");
        const table = el("ft8-table");
        onlyCq.checked = this.stored.onlyCq === true;
        table.classList.toggle("only-cq", onlyCq.checked);
        onlyCq.addEventListener("change", () => {
            this.stored.onlyCq = onlyCq.checked;
            table.classList.toggle("only-cq", onlyCq.checked);
            saveSetting(FT8_KEY, JSON.stringify(this.stored));
        });
        if (listening === null)
            this.run.disabled = true;
        this.installTx();
        // The cycle now, by the server's clock: 1st or 2nd, to choose the TX cycles by.
        window.setInterval(() => this.showCycle(), 250);
    }
    showCycle() {
        const now = this.collector === null ? null : this.serverNow();
        if (now === null) {
            this.cycleNow.textContent = "";
            return;
        }
        const number = cycleNumber(now, this.stored.mode);
        this.cycleNow.textContent = `cycle ${number.slot} (${number.position}/${number.perMinute})`;
    }
    fillBands() {
        const bands = DIAL_HZ[this.stored.mode];
        this.bandSelect.replaceChildren(...Object.entries(bands).map(([name, hz]) => new Option(`${name} ${(hz / 1e6).toFixed(4)}`, name)));
        if (!(this.stored.band in bands))
            this.stored.band = "20m" in bands ? "20m" : Object.keys(bands)[0];
        this.bandSelect.value = this.stored.band;
    }
    // Another mode or band while running: retune and start the cycles anew.
    changed() {
        saveSetting(FT8_KEY, JSON.stringify(this.stored));
        if (this.collector === null)
            return;
        this.halt("");
        this.stopCollecting();
        this.startCollecting();
        this.tune();
    }
    tune() {
        this.tunedAt = performance.now();
        const dial = DIAL_HZ[this.stored.mode][this.stored.band];
        this.connection.send("set_mode", { mode: "U-D" });
        this.connection.send("set_frequency", { hz: dial });
        // The waterfall zooms to the mode's signals once the radio is on the dial.
        const [low, high] = viewRange(this.stored.mode, dial);
        this.view.showRange(low, high, dial);
    }
    get receiving() {
        return this.collector !== null;
    }
    // F8/F4 (keys.ts): reception of `mode` on `band` (the radio's band), started
    // or switched over while it runs.
    startOn(mode, band) {
        openingModeWindow("ft8");
        this.box.hidden = false;
        if (band === null || !(band in DIAL_HZ[mode])) {
            errorBox.show(`${mode}: no frequency on ${band ?? "this frequency (outside the bands)"}`);
            return;
        }
        this.stored.mode = mode;
        this.modeSelect.value = mode;
        this.fillBands();
        this.stored.band = band;
        this.bandSelect.value = band;
        if (this.collector === null) {
            saveSetting(FT8_KEY, JSON.stringify(this.stored));
            void this.start();
        }
        else {
            this.changed();
        }
    }
    async start() {
        if (this.listening === null)
            return;
        await this.beforeStart();
        this.before = { hz: this.radio.frequencyHz, mode: this.radio.modeName };
        this.listening.setBitrateOverride(FT8_BITRATE);
        if (!(await this.listening.ensureListening())) {
            this.listening.setBitrateOverride(null);
            return;
        }
        this.listening.audio.setDigital(true);
        this.view.setDigitalBand(DECODE_BAND_HZ);
        this.tune();
        this.startCollecting();
        this.run.textContent = "Stop";
        this.run.classList.add("active");
    }
    stop() {
        this.halt("");
        this.stopCollecting();
        this.listening?.audio.setDigital(false);
        this.listening?.setBitrateOverride(null);
        const before = this.before;
        if (before !== null) {
            // Back to the voice mode: U-D at the start was often FT8/FT4's own (a
            // page closed while it ran), which left 80 and 40 m in U-D afterwards
            // (owner's report 2026-11-21).
            const hz = before.hz ?? this.radio.frequencyHz;
            const mode = before.mode !== null && before.mode !== "U-D"
                ? before.mode
                : hz !== null
                    ? this.radio.voiceModeFor(hz)
                    : null;
            if (mode !== null)
                this.connection.send("set_mode", { mode });
            if (before.hz !== null)
                this.connection.send("set_frequency", { hz: before.hz });
        }
        this.before = null;
        this.worker?.terminate();
        this.worker = null;
        this.busy = false;
        this.view.setMarks([]);
        this.view.setDigitalBand(null);
        this.view.releaseRange();
        this.run.textContent = "Start";
        this.run.classList.remove("active");
        this.state.textContent = "";
        this.clockWarning.hidden = true;
    }
    startCollecting() {
        const audio = this.listening?.audio;
        if (audio === undefined)
            return;
        const collector = new CycleCollector(this.stored.mode);
        collector.onCycle = (cycle) => this.decode(cycle);
        audio.onClock = (clock) => {
            collector.setClock(clock);
            this.serverOffsets.push(clock.utcMs + FT8_AUDIO_DELAY_MS - Date.now());
            if (this.serverOffsets.length > 10)
                this.serverOffsets.shift();
            this.clockWarning.hidden = clock.synchronized !== false;
        };
        audio.onPcm = (seq, samples) => collector.push(seq, samples);
        const previous = this.radio.onRetune;
        this.radio.onRetune = () => {
            previous();
            collector.reset(); // a cycle with two frequencies decodes neither well
        };
        this.collector = collector;
        this.state.textContent = `waiting for the first whole ${this.stored.mode} cycle`;
        this.view.setMarks([]);
    }
    stopCollecting() {
        const audio = this.listening?.audio;
        if (audio !== undefined) {
            audio.onPcm = null;
            audio.onClock = () => { };
        }
        this.radio.onRetune = () => this.listening?.audio.flush();
        this.collector = null;
    }
    decode(cycle) {
        const label = cycleLabel(cycle.startMs);
        if (this.txCycles.has(cycle.startMs)) {
            this.txCycles.delete(cycle.startMs);
            return; // our own transmission: nothing to hear
        }
        if (this.busy && performance.now() - this.busySince < 2 * CYCLE_MS[cycle.mode]) {
            this.state.textContent = `${label}: skipped — the previous cycle is still being decoded`;
            return;
        }
        if (this.busy) {
            // No answer for two cycles: the decoder hangs; a new one takes over.
            this.worker?.terminate();
            this.worker = null;
            console.warn("FT8/FT4 decoder did not answer for two cycles; restarted");
        }
        if (this.worker === null) {
            this.worker = new Worker("/static/ft8-worker.js", { type: "module" });
            this.worker.onmessage = (e) => this.decoded(e.data);
            this.worker.onerror = (e) => {
                this.busy = false;
                errorBox.show(`FT8/FT4 decoder: ${e.message}`);
            };
        }
        this.busy = true;
        this.busySince = performance.now();
        this.dialOf.set(cycle.startMs, this.radio.frequencyHz);
        this.coverageOf.set(cycle.startMs, cycle.coverage);
        // Before one of our transmissions: a quicker depth, so that the answer
        // goes out in the very next cycle.
        const sequencer = this.sequencer;
        const txParity = this.txEnabled && sequencer !== null && sequencer.step !== null ? sequencer.parity : null;
        const depth = cycleDepth(this.stored.depth, cycle.startMs, cycle.mode, txParity);
        this.state.textContent = `${label}: decoding…`;
        this.worker.postMessage({ mode: cycle.mode, startMs: cycle.startMs, samples: cycle.samples, sampleRate: 48000, depth }, [cycle.samples.buffer]);
    }
    decoded(answer) {
        this.busy = false;
        const label = cycleLabel(answer.startMs);
        const dial = this.dialOf.get(answer.startMs) ?? null;
        this.dialOf.delete(answer.startMs);
        const coverage = this.coverageOf.get(answer.startMs) ?? 1;
        this.coverageOf.delete(answer.startMs);
        const lost = coverage < 0.995 ? `, ${(100 * (1 - coverage)).toFixed(0)}% of the audio missing` : "";
        if (this.collector === null)
            return; // stopped meanwhile
        if (answer.error !== undefined) {
            this.state.textContent = `${label}: decoder error`;
            errorBox.show(`FT8/FT4 decoder: ${answer.error}`);
            return;
        }
        const decodes = (answer.decodes ?? []).sort((a, b) => a.freq - b.freq);
        const quick = answer.depth !== undefined && answer.depth < this.stored.depth ? `, depth ${answer.depth} before our TX` : "";
        this.state.textContent = `${label}: ${decodes.length} decoded in ${(answer.ms / 1000).toFixed(1)} s${quick}${lost}`;
        const first = this.rows.firstChild;
        const separator = document.createElement("tr");
        separator.className = "ft8-cycle";
        const cell = document.createElement("td");
        cell.colSpan = 5;
        const number = cycleNumber(answer.startMs, answer.mode);
        cell.textContent =
            `${label.slice(0, 2)}:${label.slice(2, 4)}:${label.slice(4)} ${answer.mode} — ` +
                `cycle ${number.slot} (${number.position}/${number.perMinute})`;
        separator.append(cell);
        const fragment = document.createDocumentFragment();
        fragment.append(separator);
        for (const d of decodes) {
            this.learnGrid(d.msg, d.ap);
            const tr = document.createElement("tr");
            tr.className = `ft8-${kind(d.msg, this.mycall)}`;
            tr.addEventListener("click", () => this.answer({ msg: d.msg, snr: d.snr, startMs: answer.startMs }));
            if (d.ap)
                tr.title = `a priori decode (a${d.ap}): more likely to be false`;
            for (const text of [label, d.snr.toFixed(0), d.dt.toFixed(1), d.freq.toFixed(0), d.msg + (d.ap ? ` a${d.ap}` : "")]) {
                const td = document.createElement("td");
                td.textContent = text;
                tr.append(td);
            }
            fragment.append(tr);
        }
        this.rows.insertBefore(fragment, first);
        while (this.rows.childElementCount > FT8_ROWS_KEPT)
            this.rows.lastElementChild?.remove();
        const marks = [];
        if (dial !== null) {
            for (const d of decodes) {
                const call = sender(d.msg);
                if (call !== null)
                    marks.push({ hz: dial + d.freq, text: call, kind: kind(d.msg, this.mycall) });
            }
        }
        this.lastMarks = marks;
        this.showMarks();
        this.lastDecoded = answer.startMs;
        this.sequencer?.heard(decodes.map((d) => ({ msg: d.msg, snr: d.snr, startMs: answer.startMs })));
        this.showNext();
    }
    showMarks() {
        const dial = this.radio.frequencyHz;
        const tx = this.collector !== null && dial !== null
            ? [{ hz: dial + this.stored.tone, text: "TX", kind: "tx", widthHz: SIGNAL_WIDTH_HZ[this.collector.mode] }]
            : [];
        this.view.setMarks([...this.lastMarks, ...tx]);
    }
    installTx() {
        this.toneInput.value = String(this.stored.tone);
        this.levelInput.value = String(this.stored.level);
        this.parityInput.value = String(this.stored.parity);
        const keep = () => {
            const tone = Math.round(Number(this.toneInput.value));
            const level = Math.round(Number(this.levelInput.value));
            if (Number.isFinite(tone))
                this.stored.tone = Math.min(FT8_TONE_MAX_HZ, Math.max(FT8_TONE_MIN_HZ, tone));
            if (Number.isFinite(level))
                this.stored.level = Math.min(0, Math.max(-40, level));
            this.stored.parity = Number(this.parityInput.value) === 1 ? 1 : 0;
            this.toneInput.value = String(this.stored.tone);
            this.levelInput.value = String(this.stored.level);
            saveSetting(FT8_KEY, JSON.stringify(this.stored));
            this.showMarks();
        };
        for (const input of [this.toneInput, this.levelInput, this.parityInput])
            input.addEventListener("change", keep);
        // While receiving, a click on the waterfall or scale sets the TX tone
        // there (as in WSJT-X), instead of tuning the radio away from the dial.
        this.radio.onClickHz = (hz) => {
            const dial = this.radio.frequencyHz;
            if (this.collector === null || dial === null)
                return false;
            this.toneInput.value = String(Math.round(hz - dial));
            keep();
            return true;
        };
        for (const kind of ["pointerdown", "keydown"]) {
            document.addEventListener(kind, () => (this.lastActivity = performance.now()), true);
        }
        this.cqButton.addEventListener("click", () => {
            const sequencer = this.newSequencer();
            if (sequencer === null || !this.enableTx())
                return;
            sequencer.callCq(this.stored.parity);
            this.showNext();
        });
        this.enableButton.addEventListener("click", () => {
            if (this.txEnabled)
                this.halt("");
            else
                this.enableTx();
        });
        el("ft8-halt").addEventListener("click", () => this.halt(""));
        // Voice PTT takes over: the button and the keyboard (left Ctrl + left Alt).
        el("ptt").addEventListener("pointerdown", () => this.halt(""));
        const keys = new Set();
        window.addEventListener("keydown", (e) => {
            keys.add(e.code);
            if (keys.has("ControlLeft") && keys.has("AltLeft"))
                this.halt("");
        });
        window.addEventListener("keyup", (e) => keys.delete(e.code));
        window.addEventListener("blur", () => keys.clear());
        document.addEventListener("visibilitychange", () => {
            if (document.hidden && this.txEnabled)
                this.halt("FT8/FT4 transmission stopped: the page was hidden");
        });
        const closed = this.connection.onClose;
        this.connection.onClose = () => {
            closed();
            this.lease = null; // the server ends a lease whose connection is gone
            if (this.txEnabled)
                this.halt("FT8/FT4 transmission stopped: the connection to the server broke");
        };
        this.connection.eventHandlers.push((message) => {
            if (this.lease === null || message.lease !== this.lease)
                return;
            if (message.event === "ft8_sent") {
                const sent = this.sentMessage;
                this.releaseLease();
                if (sent !== null)
                    this.sequencer?.transmitted(sent);
                this.showNext();
            }
            else if (message.event === "tx_ended") {
                this.lease = null;
                this.halt(`FT8/FT4 transmission ended: ${String(message.reason)}`);
            }
        });
        this.radio.digitalActive = () => this.collector !== null;
        this.radio.onBandChange = (band) => {
            if (this.collector === null)
                return false;
            if (!(band in DIAL_HZ[this.stored.mode])) {
                this.halt("");
                return false; // no dial there: an ordinary band change
            }
            this.startOn(this.stored.mode, band);
            return true;
        };
        window.setInterval(() => this.tick(), 100);
    }
    newSequencer() {
        if (this.collector === null) {
            errorBox.show("FT8/FT4: press Start first (transmitting needs the reception)");
            return null;
        }
        const grid = this.locator.slice(0, 4).toUpperCase();
        if (!/^[A-R]{2}\d{2}$/.test(grid)) {
            errorBox.show("FT8/FT4: the account has no QTH locator, needed in the messages");
            return null;
        }
        if (this.sequencer === null || this.sequencer.mode !== this.stored.mode) {
            this.sequencer = new Sequencer(this.mycall, grid, this.stored.mode);
            this.sequencer.onFinished = (qso) => this.offerLog(qso);
        }
        return this.sequencer;
    }
    // Allowing transmissions asks every time (as the automatic call, D51).
    enableTx() {
        if (this.txEnabled)
            return true;
        if (this.collector === null) {
            errorBox.show("FT8/FT4: press Start first (transmitting needs the reception)");
            return false;
        }
        const where = `${el("frequency").textContent ?? ""}, ${el("power").textContent ?? ""}`;
        const approved = window.confirm(`Enable ${this.stored.mode} TRANSMISSIONS ON THE ANTENNA: ${where}, tone ${this.stored.tone} Hz.\n\n` +
            "The page answers stations and finishes QSOs by itself until you press Halt TX — " +
            "or 6 minutes without you touching the page. Enable?");
        if (!approved)
            return false;
        this.txEnabled = true;
        this.lastActivity = performance.now();
        this.enableButton.classList.add("enabled");
        this.enableButton.textContent = "TX enabled";
        this.showNext();
        return true;
    }
    answer(heard) {
        const sequencer = this.newSequencer();
        if (sequencer === null)
            return;
        if (!sequencer.answer(heard)) {
            errorBox.show(`FT8/FT4: nothing to answer in "${heard.msg}"`);
            return;
        }
        if (!this.enableTx())
            sequencer.stop();
        this.showNext();
    }
    halt(why) {
        this.sequencer?.stop();
        this.txEnabled = false;
        this.releaseLease();
        this.enableButton.classList.remove("enabled", "transmitting");
        this.enableButton.textContent = "Enable TX";
        this.showNext();
        if (why !== "")
            errorBox.show(why);
    }
    releaseLease() {
        window.clearInterval(this.renewTimer);
        this.renewTimer = undefined;
        window.clearTimeout(this.confirmTimer);
        this.confirmTimer = undefined;
        if (this.lease !== null)
            void this.connection.request("tx_release", { lease: this.lease });
        this.lease = null;
        this.sentMessage = null;
        this.txStart = null;
        this.enableButton.classList.remove("transmitting");
    }
    serverNow() {
        if (this.serverOffsets.length === 0)
            return null;
        return Date.now() + Math.max(...this.serverOffsets);
    }
    showNext() {
        const message = this.txEnabled ? (this.sequencer?.message() ?? null) : null;
        this.next.textContent = message === null ? "" : `next: ${message}`;
        this.showCq();
    }
    // The CQ button stays lit while CQ is being called (owner's request
    // 2026-11-13): until a station answers, or transmitting is halted.
    showCq() {
        this.cqButton.classList.toggle("active", this.txEnabled && this.sequencer?.step === 6);
    }
    // Every 100 ms: take a lease before our next cycle.
    tick() {
        this.showCq(); // the sequence also moves on with the decodes
        const sequencer = this.sequencer;
        if (!this.txEnabled || sequencer === null || sequencer.step === null)
            return;
        if (performance.now() - this.lastActivity > FT8_WATCHDOG_MS) {
            this.halt("FT8/FT4 transmission stopped: 6 minutes without operator activity");
            return;
        }
        // Tuned away (frequency box, keys, seek) or another mode: no more automatic
        // transmissions there (owner's report 2026-11-15).
        const dial = DIAL_HZ[this.stored.mode][this.stored.band];
        if (this.radio.frequencyHz !== dial || this.radio.modeName !== "U-D") {
            // Just tuned there: the radio has not reported the dial yet. Neither
            // transmit nor halt until it has, or FT8_TUNE_SETTLE_MS have passed.
            if (performance.now() - this.tunedAt < FT8_TUNE_SETTLE_MS)
                return;
            this.halt(`FT8/FT4 transmission stopped: the radio left ${this.stored.band} ${this.stored.mode} (${dial / 1e6} MHz U-D)`);
            return;
        }
        if (this.txStart !== null)
            return; // a transmission is under way
        const now = this.serverNow();
        if (now === null)
            return; // no time from the server yet
        const cycle = CYCLE_MS[this.stored.mode];
        let start = Math.floor(now / cycle) * cycle;
        if (now > start + FT8_JOIN_LATE_MS)
            start += cycle;
        if (cycleIndex(start, this.stored.mode) % 2 !== sequencer.parity)
            start += cycle;
        if (now < start - FT8_LEASE_AHEAD_MS)
            return;
        void this.transmit(start);
    }
    async transmit(start) {
        this.txStart = start;
        const granted = await this.connection.request("tx_request", { antenna_confirmed: true });
        if (granted.ok !== true || typeof granted.lease !== "string") {
            this.txStart = null;
            this.halt(`FT8/FT4 transmission refused: ${String(granted.message ?? granted.error)}`);
            return;
        }
        if (!this.txEnabled || this.txStart !== start) {
            void this.connection.request("tx_release", { lease: granted.lease });
            return; // halted while waiting
        }
        this.lease = granted.lease;
        this.enableButton.classList.add("transmitting");
        const lease = granted.lease;
        this.renewTimer = window.setInterval(() => {
            if (this.lease === lease)
                void this.connection.request("tx_renew", { lease });
        }, RENEW_MS);
        // Wait for the previous cycle's decodes (an answer may change the message).
        const previous = start - CYCLE_MS[this.stored.mode];
        while (this.lease === lease && this.lastDecoded !== previous && (this.serverNow() ?? 0) < start + FT8_MESSAGE_DEADLINE_MS[this.stored.mode]) {
            await new Promise((resolve) => window.setTimeout(resolve, 50));
        }
        const message = this.sequencer?.message() ?? null;
        if (this.lease !== lease)
            return;
        if (message === null) {
            this.releaseLease(); // the QSO ended with the last decodes
            return;
        }
        this.txCycles.add(start);
        const answer = await this.connection.request("ft8_transmit", {
            lease,
            mode: this.stored.mode,
            message,
            tone_hz: this.stored.tone,
            level_db: this.stored.level,
            start_ms: start,
        });
        if (answer.ok !== true) {
            this.halt(`FT8/FT4 transmission refused: ${String(answer.message ?? answer.error)}`);
            return;
        }
        if (this.lease !== lease)
            return; // halted meanwhile: the signal was cut, not sent
        this.sentMessage = message;
        this.addSent(start, message);
        // The signal starts 0.5 s into the cycle, or at once when the request came
        // later (the server allows that), lasts duration_s and may be held up by the
        // card for up to FT8_SERVER_STALL_MS before the server gives it up.
        const duration = typeof answer.duration_s === "number" ? answer.duration_s : CYCLE_MS[this.stored.mode] / 1000;
        const now = this.serverNow() ?? start;
        const endsAt = Math.max(start + 500, now) + duration * 1000;
        const wait = endsAt - now + FT8_SERVER_STALL_MS + FT8_CONFIRM_GRACE_MS;
        this.confirmTimer = window.setTimeout(() => {
            if (this.lease === lease)
                this.halt("FT8/FT4 transmission stopped: the server did not confirm the signal");
        }, wait);
    }
    // Our own messages in the list, in orange.
    addSent(start, message) {
        const tr = document.createElement("tr");
        tr.className = "ft8-tx";
        for (const text of [cycleLabel(start), "", "", String(this.stored.tone), `TX: ${message}`]) {
            const td = document.createElement("td");
            td.textContent = text;
            tr.append(td);
        }
        this.rows.insertBefore(tr, this.rows.firstChild);
    }
    learnGrid(msg, ap) {
        if (ap)
            return;
        const found = gridOf(msg);
        if (found === null)
            return;
        this.grids.delete(found[0]); // the newest last, for the size limit below
        this.grids.set(found[0], found[1]);
        if (this.grids.size > GRIDS_KEPT)
            this.grids.delete(this.grids.keys().next().value);
    }
    offerLog(qso) {
        const dialog = el("ft8-log-dialog");
        const dial = this.radio.frequencyHz;
        const freq = dial === null ? null : dial + this.stored.tone;
        el("ft8-log-call").value = qso.call;
        const gridField = el("ft8-log-grid");
        gridField.value = qso.grid || (this.grids.get(qso.call) ?? "");
        if (gridField.value === "") {
            // Not heard this session: the grid of an earlier QSO in the log, if any.
            void this.connection.request("log_worked", { call: qso.call }).then((answer) => {
                const earlier = answer.worked ?? [];
                const grid = earlier.find((q) => q.gridsquare)?.gridsquare ?? "";
                if (gridField.value === "" && el("ft8-log-call").value === qso.call)
                    gridField.value = grid;
            });
        }
        el("ft8-log-sent").value = qso.sent;
        el("ft8-log-rcvd").value = qso.rcvd;
        el("ft8-log-comment").value = "";
        el("ft8-log-info").textContent =
            `${this.stored.mode}${freq === null ? "" : `, ${(freq / 1e6).toFixed(6)} MHz`}; the time is that of saving (UTC)`;
        const form = el("ft8-log-form");
        form.onsubmit = (e) => {
            e.preventDefault();
            void this.connection
                .request("log_add", {
                call: el("ft8-log-call").value,
                gridsquare: el("ft8-log-grid").value,
                rst_sent: el("ft8-log-sent").value,
                rst_rcvd: el("ft8-log-rcvd").value,
                comment: el("ft8-log-comment").value,
                mode: this.stored.mode,
                ...(freq === null ? {} : { freq_hz: freq }),
            })
                .then((answer) => {
                if (answer.ok === true)
                    dialog.close();
                else
                    errorBox.show(`Log: ${String(answer.message ?? answer.error)}`);
            });
        };
        el("ft8-log-cancel").onclick = () => dialog.close();
        if (!dialog.open)
            dialog.showModal();
    }
}
function formatDate(adif) {
    return `${adif.slice(0, 4)}-${adif.slice(4, 6)}-${adif.slice(6, 8)}`;
}
// The page starts after login: the account's settings first, then the controls.
async function main() {
    const accounts = new AccountUi();
    const account = await accounts.check();
    if (account === null || account === undefined)
        return; // a login or first-account dialog shows
    await loadSettings();
    const view = new Waterfall();
    const radio = new Radio(view);
    const resources = new ResourceWindow();
    const systemLog = new LogWindow();
    const connection = new Connection(view, radio, resources, systemLog);
    systemLog.send = (type) => connection.send(type);
    radio.send = (type, fields) => connection.send(type, fields);
    resources.send = (type) => connection.send(type);
    resources.request = (type) => connection.request(type);
    new AboutWindow().request = (type) => connection.request(type);
    accounts.attach(connection);
    const stationLog = new StationLog(connection, radio, account.callsign);
    installLevelControls(view);
    installSpikeControl(view);
    const listening = installAudioControls(connection, radio);
    installTxControls(connection);
    // After the transmit controls: it chains their connection handlers.
    const digital = new DigitalReceiver(connection, radio, view, listening, account.callsign.toUpperCase(), account.locator);
    // After FT8/FT4: it chains its click on the waterfall.
    const rtty = new RttyReceiver(connection, radio, view, listening, digital, stationLog, account.callsign.toUpperCase(), account.locator);
    new CwWindow(radio, view, listening, digital, rtty);
    new SstvWindow(radio, listening);
    installResizeHandle();
    installColumnHandles();
    const paused = installPause(connection, listening, digital);
    installTxOutput(connection);
    installTuneWarnings(connection);
    // Seek looks at the waterfall's lines: none fresh while paused (old ones would
    // take it to a station from before the pause).
    installShortcuts(radio, digital, new Seeker(radio, view, () => digital.receiving || paused()));
    // Movable, resizable windows; place and size kept on the account.
    for (const id of ["logbook", "ft8", "rtty", "cw", "sstv", "system-log", "resources", "errors", "about"]) {
        const key = `${SETTINGS_PREFIX}window.${id}`;
        makeFloating(el(id), el(`${id}-title`), () => loadSetting(key), (text) => saveSetting(key, text ?? ""));
    }
    connection.start();
}
// RTTY (decisions.md D61; AGENTS.md section 10). Reception: the decoder
// (rtty.ts) on the received audio before any processing, as FT8/FT4. Opening
// the window starts listening if it is off and decoding; closing it stops
// decoding. The two tones are orange lines over the waterfall; a right click
// there sets them around the click (a left click tunes the radio). Transmission: an ordinary lease of this page, renewed by it; the
// server keys the text out (rtty_start / rtty_text / rtty_end) and only
// within the lease. Macros transmit only on a click; no automation.
const RTTY_KEY = "rtty";
const RTTY_TEXT_KEPT = 20000; // characters shown; older text is dropped
const RTTY_WATCHDOG_MS = 6 * 60 * 1000; // as FT8/FT4 (AGENTS.md section 10)
const RTTY_TYPE_BATCH_MS = 100; // typed characters go to the server this often
class RttyReceiver {
    get receiving() {
        return this.running;
    }
    constructor(connection, radio, view, listening, digital, log, mycall, locator) {
        this.connection = connection;
        this.radio = radio;
        this.view = view;
        this.listening = listening;
        this.digital = digital;
        this.log = log;
        this.mycall = mycall;
        this.locator = locator;
        this.box = el("rtty");
        this.openButton = el("rtty-open");
        this.markInput = el("rtty-mark");
        this.reverseBox = el("rtty-reverse");
        this.afcBox = el("rtty-afc");
        this.afcShown = el("rtty-afc-offset");
        this.state = el("rtty-state");
        this.text = el("rtty-text");
        this.callInput = el("rtty-call");
        this.levelInput = el("rtty-level");
        this.txButton = el("rtty-txbutton");
        this.txState = el("rtty-txstate");
        this.typeArea = el("rtty-type");
        this.decoder = new RttyDecoder(48000);
        this.stored = {
            mark: RTTY_MARK_HZ,
            reverse: false,
            afc: true,
            level: -10,
            name: "",
            qth: "",
            nr: 1,
            macros: DEFAULT_MACROS.map((m) => ({ ...m })),
        };
        this.running = false;
        this.shown = 0; // characters in the text pane
        // Transmission.
        this.lease = null;
        this.starting = false;
        this.ending = false; // RX asked: the rest goes out, then the lease ends
        this.typed = ""; // typed during a transmission, not yet given to the server
        this.given = ""; // given to the server in this transmission
        this.keyed = 0; // of which this many are out (rtty_sent)
        this.confirmed = false; // transmitting on the antenna, once per page (D46)
        this.lastActivity = performance.now();
        this.load();
        this.markInput.value = String(this.stored.mark);
        this.reverseBox.checked = this.stored.reverse;
        this.afcBox.checked = this.stored.afc;
        this.decoder.afc = this.stored.afc;
        this.levelInput.value = String(this.stored.level);
        el("rtty-myname").value = this.stored.name;
        el("rtty-myqth").value = this.stored.qth;
        el("rtty-nr").value = String(this.stored.nr);
        this.decoder.setTones(this.stored.mark, this.stored.reverse);
        this.decoder.onText = (text) => {
            if (this.lease === null && !this.starting)
                this.append(text, false); // not our own signal
        };
        modeWindows.set("rtty", () => this.stop());
        this.openButton.addEventListener("click", () => void this.start());
        el("rtty-close").addEventListener("click", () => this.stop());
        el("rtty-clear").addEventListener("click", () => {
            this.text.replaceChildren();
            this.shown = 0;
        });
        numberField(this.markInput, (hz) => this.setMark(hz));
        numberField(this.levelInput, () => this.keep());
        this.reverseBox.addEventListener("change", () => this.keep());
        this.afcBox.addEventListener("change", () => this.keep());
        for (const id of ["rtty-myname", "rtty-myqth", "rtty-nr"]) {
            el(id).addEventListener("change", () => this.keep());
        }
        // A left click tunes the radio as always; a right click sets the tones
        // around it (owner's choice 2026-11-24).
        this.view.onRightClickHz = (hz) => {
            const dial = this.radio.frequencyHz;
            if (!this.running || dial === null || this.sideband() === null)
                return false;
            this.setMark(Math.abs(hz - dial) - RTTY_SHIFT_HZ / 2);
            return true;
        };
        // A double click on a callsign in the received text puts it in the call field.
        this.text.addEventListener("dblclick", () => {
            const word = (window.getSelection()?.toString() ?? "").trim().toUpperCase();
            if (/^[A-Z0-9/]{3,12}$/.test(word) && /\d/.test(word) && /[A-Z]/.test(word))
                this.callInput.value = word;
        });
        this.txButton.addEventListener("click", () => {
            // The typed text goes out and the transmission ends after it (owner's
            // request 2026-11-24): no idle carrier while nothing is typed.
            if (this.lease !== null || this.starting)
                return;
            if (sendable(this.typeArea.value).trim() === "") {
                errorBox.show("RTTY: type the text to send first");
                return;
            }
            void this.transmit(this.typeArea.value, true);
        });
        el("rtty-rx").addEventListener("click", () => this.endTx());
        el("rtty-stop").addEventListener("click", () => this.stopTx(""));
        el("rtty-log").addEventListener("click", () => this.logQso());
        el("rtty-macro-defaults").addEventListener("click", () => {
            this.stored.macros = DEFAULT_MACROS.map((m) => ({ ...m }));
            this.keep();
            this.showMacros();
        });
        this.typeArea.addEventListener("beforeinput", (e) => this.typing(e));
        for (const kind of ["pointerdown", "keydown"]) {
            document.addEventListener(kind, () => (this.lastActivity = performance.now()), true);
        }
        this.connection.eventHandlers.push((message) => this.onEvent(message));
        const closed = this.connection.onClose;
        this.connection.onClose = () => {
            closed();
            if (this.lease !== null)
                this.cleanup("RTTY transmission stopped: the connection to the server broke");
            this.lease = null; // the server ends a lease whose connection is gone
        };
        document.addEventListener("visibilitychange", () => {
            if (document.hidden && this.lease !== null)
                this.stopTx("RTTY transmission stopped: the page was hidden");
        });
        this.showMacros();
    }
    load() {
        try {
            const saved = JSON.parse(loadSetting(RTTY_KEY) ?? "{}");
            if (typeof saved.mark === "number")
                this.stored.mark = this.clampMark(saved.mark);
            if (typeof saved.reverse === "boolean")
                this.stored.reverse = saved.reverse;
            if (typeof saved.afc === "boolean")
                this.stored.afc = saved.afc;
            if (typeof saved.level === "number")
                this.stored.level = Math.min(0, Math.max(-40, saved.level));
            if (typeof saved.name === "string")
                this.stored.name = saved.name;
            if (typeof saved.qth === "string")
                this.stored.qth = saved.qth;
            if (typeof saved.nr === "number" && saved.nr >= 1)
                this.stored.nr = Math.floor(saved.nr);
            if (Array.isArray(saved.macros) && saved.macros.every((m) => typeof m?.name === "string" && typeof m?.text === "string")) {
                this.stored.macros = saved.macros.slice(0, 12);
            }
        }
        catch {
            // keep the defaults
        }
    }
    clampMark(hz) {
        return Math.round(Math.min(RTTY_MARK_MAX_HZ, Math.max(RTTY_MARK_MIN_HZ, hz)));
    }
    setMark(hz) {
        this.markInput.value = String(this.clampMark(hz));
        this.keep();
    }
    keep() {
        this.stored.mark = this.clampMark(Number(this.markInput.value) || RTTY_MARK_HZ);
        this.stored.reverse = this.reverseBox.checked;
        this.stored.afc = this.afcBox.checked;
        const level = Number(this.levelInput.value);
        this.stored.level = Number.isFinite(level) ? Math.min(0, Math.max(-40, Math.round(level))) : -10;
        this.stored.name = el("rtty-myname").value.trim();
        this.stored.qth = el("rtty-myqth").value.trim();
        const nr = Number(el("rtty-nr").value);
        this.stored.nr = Number.isFinite(nr) && nr >= 1 ? Math.floor(nr) : 1;
        this.markInput.value = String(this.stored.mark);
        this.levelInput.value = String(this.stored.level);
        el("rtty-nr").value = String(this.stored.nr);
        saveSetting(RTTY_KEY, JSON.stringify(this.stored));
        this.decoder.setTones(this.stored.mark, this.stored.reverse);
        this.decoder.afc = this.stored.afc;
        this.showMarks();
    }
    // -1 when audio frequencies lie below the dial (LSB, L-D), +1 above (USB,
    // U-D), null in modes RTTY cannot be received in.
    sideband() {
        const mode = this.radio.modeName;
        if (mode === "LSB" || mode === "L-D")
            return -1;
        if (mode === "USB" || mode === "U-D")
            return 1;
        return null;
    }
    async start() {
        openingModeWindow("rtty");
        this.box.hidden = false;
        if (this.running)
            return;
        if (this.listening === null) {
            this.state.textContent = "receive audio does not work in this browser";
            return;
        }
        if (!(await this.listening.ensureListening())) {
            this.state.textContent = "listening could not start";
            return;
        }
        this.decoder.reset();
        this.listening.audio.onRttyPcm = (_seq, samples) => this.decoder.push(samples);
        this.running = true;
        this.openButton.classList.add("active");
        this.check();
        this.timer = window.setInterval(() => this.check(), 1000);
    }
    stop() {
        this.box.hidden = true;
        if (this.lease !== null || this.starting)
            this.stopTx("");
        if (!this.running)
            return;
        if (this.listening !== null)
            this.listening.audio.onRttyPcm = null;
        this.running = false;
        this.openButton.classList.remove("active");
        window.clearInterval(this.timer);
        if (!this.digital.receiving)
            this.view.setMarks([]);
        this.state.textContent = "";
    }
    // Once a second: the mode hint and the marks (the dial may have moved).
    check() {
        const sideband = this.sideband();
        this.state.textContent =
            sideband === null ? "set the radio to L-D (or LSB) for RTTY" : sideband === 1 ? "U-D: tones reversed? try REV" : "";
        this.showMarks();
    }
    showMarks() {
        if (!this.running || this.digital.receiving)
            return; // FT8/FT4 owns the marks then
        const dial = this.radio.frequencyHz;
        const sideband = this.sideband();
        if (dial === null || sideband === null) {
            this.view.setMarks([]);
            return;
        }
        // The tones decoded now: the set ones, moved by the AFC.
        const { mark, space } = this.decoder.tones;
        const low = Math.min(mark, space);
        const off = Math.round(low - this.stored.mark);
        const shift = Math.round(Math.abs(space - mark));
        this.afcShown.textContent =
            this.stored.afc && (off !== 0 || shift !== RTTY_SHIFT_HZ) ? `${off > 0 ? "+" : ""}${off} Hz, shift ${shift}` : "";
        this.view.setMarks([
            { hz: dial + sideband * mark, text: "M", kind: "rtty" },
            { hz: dial + sideband * space, text: "S", kind: "rtty" },
        ]);
    }
    // Received text plain, our own (as it is keyed out) in orange.
    append(text, sent) {
        if (text === "")
            return;
        const atEnd = this.text.scrollTop + this.text.clientHeight >= this.text.scrollHeight - 4;
        const last = this.text.lastChild;
        if (last !== null && (last instanceof HTMLElement ? sent && last.classList.contains("rtty-sent") : !sent)) {
            last.textContent = (last.textContent ?? "") + text;
        }
        else if (sent) {
            const span = document.createElement("span");
            span.className = "rtty-sent";
            span.textContent = text;
            this.text.append(span);
        }
        else {
            this.text.append(document.createTextNode(text));
        }
        this.shown += text.length;
        while (this.shown > RTTY_TEXT_KEPT && this.text.firstChild !== null) {
            this.shown -= this.text.firstChild.textContent?.length ?? 0;
            this.text.firstChild.remove();
        }
        if (atEnd)
            this.text.scrollTop = this.text.scrollHeight; // follow, unless scrolled back
    }
    // ---- Macros
    showMacros() {
        const buttons = el("rtty-macros");
        const fields = el("rtty-macro-fields");
        buttons.replaceChildren();
        fields.replaceChildren();
        this.stored.macros.forEach((macro, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = macro.name;
            button.title = macro.text.trim();
            button.addEventListener("click", () => this.macro(index));
            buttons.append(button);
            const label = document.createElement("label");
            const name = document.createElement("input");
            name.className = "rtty-macro-name";
            name.value = macro.name;
            const text = document.createElement("input");
            text.className = "rtty-macro-text";
            text.spellcheck = false;
            text.value = macro.text.replace(/\n/g, "\\n");
            const save = () => {
                this.stored.macros[index] = { name: name.value.trim() || `M${index + 1}`, text: text.value.replace(/\\n/g, "\n") };
                this.keep();
                button.textContent = this.stored.macros[index].name;
                button.title = this.stored.macros[index].text.trim();
            };
            name.addEventListener("change", save);
            text.addEventListener("change", save);
            label.append(name, " ", text);
            fields.append(label);
        });
    }
    macro(index) {
        const macro = this.stored.macros[index];
        const text = expandMacro(macro.text, {
            MYCALL: this.mycall,
            MYLOC: this.locator,
            MYNAME: this.stored.name,
            MYQTH: this.stored.qth,
            CALL: this.callInput.value,
            NR: this.stored.nr,
        });
        if (text === null) {
            errorBox.show(`RTTY macro "${macro.name}": fill in the call (or your name and QTH under "edit macros") first`);
            return;
        }
        if (this.lease !== null && !this.ending)
            this.give(text);
        else if (this.lease === null && !this.starting)
            void this.transmit(text, false);
    }
    // ---- Transmission
    // Typing: before a transmission the text area just holds it; during one,
    // each character goes to the server (in batches of RTTY_TYPE_BATCH_MS) and
    // cannot be taken back.
    typing(e) {
        if (this.lease === null)
            return; // ordinary editing until TX
        e.preventDefault();
        let add = "";
        if (e.inputType === "insertText" || e.inputType === "insertReplacementText")
            add = e.data ?? "";
        else if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph")
            add = "\n";
        else if (e.inputType === "insertFromPaste")
            add = e.dataTransfer?.getData("text/plain") ?? e.data ?? "";
        const text = sendable(add);
        if (text === "" || this.ending)
            return;
        this.typeArea.value += text;
        this.typed += text;
        if (this.typeTimer === undefined) {
            this.typeTimer = window.setTimeout(() => {
                this.typeTimer = undefined;
                const batch = this.typed;
                this.typed = "";
                if (batch !== "")
                    this.give(batch);
            }, RTTY_TYPE_BATCH_MS);
        }
    }
    give(text) {
        const lease = this.lease;
        if (lease === null || text === "")
            return;
        this.given += text;
        void this.connection.request("rtty_text", { lease, text }).then((answer) => {
            if (answer.ok !== true && this.lease === lease) {
                this.stopTx(`RTTY: ${String(answer.message ?? answer.error)}`);
            }
        });
    }
    // Sends `first` and ends the transmission after it. `fromTypeArea`: the
    // text was typed in the window, which is emptied once it is on its way.
    async transmit(first, fromTypeArea) {
        const text = sendable(first);
        if (!this.running)
            await this.start();
        if (!this.running)
            return;
        if (!this.confirmed) {
            const where = `${el("frequency").textContent ?? ""}, ${el("power").textContent ?? ""}`;
            this.confirmed = window.confirm(`You are transmitting RTTY ON THE ANTENNA: ${where}, mark ${this.stored.mark} Hz. Continue?`);
            if (!this.confirmed)
                return;
        }
        this.starting = true;
        this.txButton.classList.add("transmitting");
        this.txState.textContent = "starting…";
        this.lastActivity = performance.now();
        const granted = await this.connection.request("tx_request", { antenna_confirmed: true });
        if (granted.ok !== true || typeof granted.lease !== "string") {
            this.starting = false;
            this.cleanup(`RTTY transmission refused: ${String(granted.message ?? granted.error)}`);
            return;
        }
        const lease = granted.lease;
        if (!this.starting) {
            void this.connection.request("tx_release", { lease }); // stopped while waiting
            return;
        }
        this.lease = lease;
        this.renewTimer = window.setInterval(() => this.renew(lease), RENEW_MS);
        const answer = await this.connection.request("rtty_start", {
            lease,
            mark_hz: this.stored.mark,
            reverse: this.stored.reverse,
            level_db: this.stored.level,
            text,
        });
        this.starting = false;
        if (this.lease !== lease)
            return; // stopped meanwhile
        if (answer.ok !== true) {
            this.stopTx(`RTTY transmission refused: ${String(answer.message ?? answer.error)}`);
            return;
        }
        this.given = text;
        this.keyed = 0;
        this.typed = "";
        if (fromTypeArea)
            this.typeArea.value = ""; // what was typed is on its way
        this.txState.textContent = "transmitting";
        this.endTx();
    }
    renew(lease) {
        if (this.lease !== lease)
            return;
        if (performance.now() - this.lastActivity > RTTY_WATCHDOG_MS) {
            this.stopTx("RTTY transmission stopped: 6 minutes without operator activity");
            return;
        }
        void this.connection.request("tx_renew", { lease });
    }
    // RX: what waits goes out, then the server says rtty_done and the lease ends.
    endTx() {
        const lease = this.lease;
        if (lease === null || this.ending)
            return;
        window.clearTimeout(this.typeTimer);
        this.typeTimer = undefined;
        if (this.typed !== "")
            this.give(this.typed);
        this.typed = "";
        this.ending = true;
        this.txState.textContent = "finishing…";
        void this.connection.request("rtty_end", { lease }).then((answer) => {
            if (answer.ok !== true && this.lease === lease)
                this.stopTx(`RTTY: ${String(answer.message ?? answer.error)}`);
        });
    }
    // Stop: the lease is given back at once; the sound stops, the rest is dropped.
    stopTx(why) {
        const lease = this.lease;
        this.starting = false;
        if (lease !== null)
            void this.connection.request("tx_release", { lease });
        this.cleanup(why);
    }
    cleanup(why) {
        window.clearInterval(this.renewTimer);
        this.renewTimer = undefined;
        window.clearTimeout(this.typeTimer);
        this.typeTimer = undefined;
        this.lease = null;
        this.ending = false;
        this.typed = "";
        this.given = "";
        this.keyed = 0;
        this.txButton.classList.remove("transmitting");
        this.txState.textContent = "";
        this.decoder.reset(); // our own signal is not to be decoded
        if (why !== "")
            errorBox.show(why);
    }
    onEvent(message) {
        if (this.lease === null || message.lease !== this.lease)
            return;
        if (message.event === "rtty_sent" && typeof message.sent === "number") {
            const out = this.given.slice(this.keyed, message.sent);
            this.keyed = message.sent;
            this.append(out, true);
        }
        else if (message.event === "rtty_done") {
            const lease = this.lease;
            void this.connection.request("tx_release", { lease });
            this.append(this.given.slice(this.keyed), true);
            this.cleanup("");
        }
        else if (message.event === "tx_ended") {
            this.lease = null;
            this.cleanup(`RTTY transmission ended: ${String(message.reason)}`);
        }
    }
    // The QSO into the station log: call and RTTY; the contest number goes up.
    logQso() {
        this.log.startQso(this.callInput.value.trim().toUpperCase(), "RTTY");
        this.stored.nr += 1;
        el("rtty-nr").value = String(this.stored.nr);
        this.keep();
    }
}
// The CW skimmer (decisions.md D62): the decoder (cw.ts) on the received
// audio before any processing, as FT8/FT4. Opening the window starts
// listening if it is off and reading; closing it stops reading. A click on a
// signal narrows the browser's receive filter around its tone (the width is
// chosen in the window); "wide" brings the filter from before back. The
// calls read are shown over the waterfall (not while FT8/FT4 or RTTY own it).
const CW_KEY = "cw";
const CW_TEXT_SHOWN = 90; // characters of each signal's text in its row
class CwWindow {
    constructor(radio, view, listening, digital, rtty) {
        this.radio = radio;
        this.view = view;
        this.listening = listening;
        this.digital = digital;
        this.rtty = rtty;
        this.box = el("cw");
        this.openButton = el("cw-open");
        this.widthSelect = el("cw-width");
        this.state = el("cw-state");
        this.rows = el("cw-rows");
        this.skimmer = new CwSkimmer(48000);
        this.running = false;
        this.chosen = null; // the signal whose tone the filter is on
        this.last = [];
        const saved = loadSetting(CW_KEY);
        if (saved !== null && ["200", "300", "500"].includes(saved))
            this.widthSelect.value = saved;
        this.widthSelect.addEventListener("change", () => {
            saveSetting(CW_KEY, this.widthSelect.value);
            // The filter (and its band on the waterfall) takes the new width at
            // once: around the chosen signal, or around where it was narrowed.
            const width = Number(this.widthSelect.value);
            const signal = this.last.find((s) => s.id === this.chosen);
            if (signal === undefined || !this.radio.narrowTo(signal.toneHz, width))
                this.radio.renarrow(width);
        });
        // A right click on the waterfall narrows the filter around the clicked
        // place (owner's choice 2026-11-24); RTTY, when it runs, keeps the right
        // click for its tones.
        const before = this.view.onRightClickHz;
        this.view.onRightClickHz = (hz) => before?.(hz) === true || this.narrowAt(hz);
        // After a retune the tones found belong to the old frequency: start over,
        // so no call is left drawn where its station no longer is.
        this.radio.retuneListeners.push(() => {
            if (!this.running)
                return;
            this.skimmer.reset();
            this.last = [];
            this.chosen = null;
            this.render();
            this.showMarks();
        });
        modeWindows.set("cw", () => this.stop());
        this.openButton.addEventListener("click", () => void this.start());
        el("cw-close").addEventListener("click", () => this.stop());
        el("cw-wide").addEventListener("click", () => this.wide());
        el("cw-clear").addEventListener("click", () => {
            this.skimmer.reset();
            this.last = [];
            this.chosen = null;
            this.render();
        });
        this.skimmer.onUpdate = (signals) => {
            this.last = signals;
            this.render();
            this.showMarks();
        };
    }
    async start() {
        openingModeWindow("cw");
        this.box.hidden = false;
        if (this.running)
            return;
        if (this.listening === null) {
            this.state.textContent = "receive audio does not work in this browser";
            return;
        }
        if (!(await this.listening.ensureListening())) {
            this.state.textContent = "listening could not start";
            return;
        }
        this.skimmer.reset();
        this.listening.audio.onCwPcm = (_seq, samples) => this.skimmer.push(samples);
        this.running = true;
        this.openButton.classList.add("active");
        this.state.textContent = "looking for signals…";
    }
    stop() {
        this.box.hidden = true;
        if (!this.running)
            return;
        if (this.listening !== null)
            this.listening.audio.onCwPcm = null;
        this.running = false;
        this.openButton.classList.remove("active");
        if (this.ownsMarks())
            this.view.setMarks([]);
        this.state.textContent = "";
    }
    narrowAt(hz) {
        const dial = this.radio.frequencyHz;
        const sideband = this.sideband();
        if (!this.running || dial === null || sideband === null)
            return false;
        const toneHz = sideband * (hz - dial);
        if (toneHz <= 0)
            return false; // on the other side of the dial: nothing to hear
        if (!this.radio.narrowTo(toneHz, Number(this.widthSelect.value)))
            return false;
        this.chosen = null;
        this.render();
        return true;
    }
    wide() {
        this.chosen = null;
        this.radio.restoreWide();
        this.render();
    }
    sideband() {
        const mode = this.radio.modeName;
        if (mode === "LSB" || mode === "L-D")
            return -1;
        if (mode === "USB" || mode === "U-D")
            return 1;
        return null;
    }
    render() {
        const sideband = this.sideband();
        const dial = this.radio.frequencyHz;
        this.state.textContent = sideband === null ? "set the radio to U-D or L-D for the skimmer" : this.last.length === 0 ? "looking for signals…" : "";
        const rows = this.last.map((signal) => {
            const tr = document.createElement("tr");
            if (!signal.active)
                tr.classList.add("cw-quiet");
            if (signal.id === this.chosen)
                tr.classList.add("cw-chosen");
            const rf = dial !== null && sideband !== null ? ((dial + sideband * signal.toneHz) / 1000).toFixed(2) : "";
            const text = signal.text.slice(-CW_TEXT_SHOWN);
            const cells = [String(Math.round(signal.toneHz)), rf, String(signal.wpm)];
            for (const value of cells) {
                const td = document.createElement("td");
                td.textContent = value;
                tr.append(td);
            }
            const td = document.createElement("td");
            // The call read last, in green.
            const call = signal.call;
            const at = call === null ? -1 : text.lastIndexOf(call);
            if (call !== null && at >= 0) {
                const mark = document.createElement("span");
                mark.className = "cw-call";
                mark.textContent = call;
                td.append(text.slice(0, at), mark, text.slice(at + call.length));
            }
            else {
                td.textContent = text;
            }
            tr.append(td);
            tr.title = "Click: narrow the receive filter around this signal";
            tr.addEventListener("click", () => {
                if (this.radio.narrowTo(signal.toneHz, Number(this.widthSelect.value))) {
                    this.chosen = signal.id;
                    this.render();
                }
                else {
                    errorBox.show("Narrowing works in U-D, L-D, USB and LSB");
                }
            });
            return tr;
        });
        this.rows.replaceChildren(...rows);
    }
    ownsMarks() {
        return !this.digital.receiving && !this.rtty.receiving;
    }
    showMarks() {
        if (!this.running || !this.ownsMarks())
            return;
        const dial = this.radio.frequencyHz;
        const sideband = this.sideband();
        if (dial === null || sideband === null) {
            this.view.setMarks([]);
            return;
        }
        this.view.setMarks(this.last
            .filter((signal) => signal.call !== null)
            .map((signal) => ({
            hz: dial + sideband * signal.toneHz,
            text: signal.call ?? "",
            kind: signal.text.includes("CQ") ? "cq" : "other",
        })));
    }
}
// SSTV reception (decisions.md D60): the decoder (sstv.ts, a port of Robot36)
// in a Web Worker, on the received audio before any processing, as FT8/FT4.
// Opening the window starts listening if it is off and decoding; closing it
// stops decoding. Each picture is drawn line by line; the last ones stay in a
// list for this page (not kept on the server) and can be saved as PNG.
// Reception only: nothing here transmits or changes the radio.
const SSTV_KEPT = 12; // pictures in the list
class SstvWindow {
    constructor(radio, listening) {
        this.listening = listening;
        this.box = el("sstv");
        this.openButton = el("sstv-open");
        this.state = el("sstv-state");
        this.canvas = el("sstv-picture");
        this.list = el("sstv-list");
        this.worker = null;
        this.pictures = []; // newest first
        this.current = null; // being received
        this.shown = null; // in the big view
        modeWindows.set("sstv", () => this.stop());
        this.openButton.addEventListener("click", () => void this.start());
        el("sstv-close").addEventListener("click", () => this.stop());
        el("sstv-save").addEventListener("click", () => this.save());
        // After a retune the picture under way belongs to the old frequency.
        radio.retuneListeners.push(() => this.worker?.postMessage({ kind: "end" }));
    }
    async start() {
        openingModeWindow("sstv");
        this.box.hidden = false;
        if (this.worker !== null)
            return;
        if (this.listening === null) {
            this.state.textContent = "receive audio does not work in this browser";
            return;
        }
        if (!(await this.listening.ensureListening())) {
            this.state.textContent = "listening could not start";
            return;
        }
        const worker = new Worker("/static/sstv-worker.js", { type: "module" });
        worker.onmessage = (e) => this.answer(e.data);
        worker.onerror = (e) => errorBox.show(`SSTV decoder: ${e.message}`);
        this.worker = worker;
        // A copy per listener (RxAudio.tap): it can be handed over.
        this.listening.audio.onSstvPcm = (_seq, samples) => worker.postMessage({ kind: "audio", samples }, [samples.buffer]);
        this.openButton.classList.add("active");
        this.showState();
    }
    stop() {
        this.box.hidden = true;
        if (this.worker === null)
            return;
        if (this.listening !== null)
            this.listening.audio.onSstvPcm = null;
        this.worker.terminate();
        this.worker = null;
        if (this.current !== null)
            this.finish();
        this.openButton.classList.remove("active");
    }
    answer(message) {
        switch (message.kind) {
            case "picture": {
                if (this.current !== null)
                    this.finish();
                const thumb = document.createElement("canvas");
                thumb.width = message.width;
                thumb.height = message.height;
                const picture = {
                    mode: message.mode,
                    at: new Date(),
                    vis: message.vis,
                    image: new ImageData(message.width, message.height),
                    lines: 0,
                    done: false,
                    thumb,
                };
                // Black until its lines come.
                for (let i = 3; i < picture.image.data.length; i += 4)
                    picture.image.data[i] = 255;
                thumb.getContext("2d")?.putImageData(picture.image, 0, 0);
                thumb.title = this.describe(picture);
                thumb.addEventListener("click", () => this.show(picture));
                this.pictures.unshift(picture);
                this.pictures.length = Math.min(this.pictures.length, SSTV_KEPT);
                this.list.replaceChildren(...this.pictures.map((p) => p.thumb));
                this.current = picture;
                this.show(picture);
                break;
            }
            case "lines": {
                const picture = this.current;
                if (picture === null || message.width !== picture.image.width)
                    return;
                picture.image.data.set(message.rgba, message.first * message.width * 4);
                picture.lines = Math.max(picture.lines, message.first + message.rows);
                const rows = new ImageData(message.rgba, message.width, message.rows);
                picture.thumb.getContext("2d")?.putImageData(rows, 0, message.first);
                if (this.shown === picture)
                    this.canvas.getContext("2d")?.putImageData(rows, 0, message.first);
                this.showState();
                break;
            }
            case "end":
                if (this.current !== null)
                    this.finish();
                break;
            case "error":
                errorBox.show(`SSTV decoder: ${message.message}`);
                break;
        }
    }
    finish() {
        const picture = this.current;
        if (picture === null)
            return;
        picture.done = true;
        picture.thumb.title = this.describe(picture);
        this.current = null;
        this.showState();
    }
    show(picture) {
        this.shown = picture;
        this.canvas.width = picture.image.width;
        this.canvas.height = picture.image.height;
        this.canvas.getContext("2d")?.putImageData(picture.image, 0, 0);
        for (const p of this.pictures)
            p.thumb.classList.toggle("sstv-shown", p === picture);
        this.showState();
    }
    describe(picture) {
        const time = picture.at.toISOString().slice(11, 19);
        const lines = picture.done ? `${picture.lines} lines` : `line ${picture.lines}/${picture.image.height}`;
        return `${time} UTC  ${picture.mode}${picture.vis ? "" : " (joined late)"}  ${lines}`;
    }
    showState() {
        const picture = this.shown;
        if (this.worker === null && picture === null)
            this.state.textContent = "";
        else if (picture === null)
            this.state.textContent = "waiting for a picture…";
        else
            this.state.textContent = this.describe(picture);
    }
    save() {
        const picture = this.shown;
        if (picture === null) {
            errorBox.show("SSTV: no picture to save");
            return;
        }
        const stamp = picture.at.toISOString().slice(0, 19).replace(/[-:]/g, "").replace("T", "-");
        const name = `sstv-${stamp}Z-${picture.mode.replace(/\s+/g, "")}.png`;
        // The picture as received, at its own size (not the window's scale).
        const canvas = document.createElement("canvas");
        canvas.width = picture.image.width;
        canvas.height = picture.image.height;
        canvas.getContext("2d")?.putImageData(picture.image, 0, 0);
        canvas.toBlob((blob) => {
            if (blob === null)
                return;
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = name;
            link.click();
            window.setTimeout(() => URL.revokeObjectURL(link.href), 10000);
        }, "image/png");
    }
}
// Seek (seek.ts, owner's request 2026-11-13): the next station in the
// waterfall's spectrum; with none in view the radio moves STEP_HZ and the
// search goes on, also outside the amateur bands (receiving only). Off while
// FT8/FT4 reception runs. A second press, a retune by anything else or the
// start of FT8/FT4 reception stops it.
const SEEK_LINES = 60; // newest lines held (3 s at 20 lines/s)
const SEEK_MIN_LINES = 3; // fewer at the start: fresh lines are awaited first
const SEEK_DWELL_MS = 2500; // after a retune: listen this long before looking
const SEEK_WAIT_MS = 6000; // no line of the new frequency by then: give up
const SEEK_POLL_MS = 100;
// The radio may report the frequency rounded (its step is not measured): a
// smaller difference is not someone else retuning.
const SEEK_RETUNED_HZ = 100;
class Seeker {
    constructor(radio, view, blocked) {
        this.radio = radio;
        this.view = view;
        this.blocked = blocked;
        this.up = el("seek-up");
        this.down = el("seek-down");
        this.running = null;
        this.up.addEventListener("click", () => this.toggle(1));
        this.down.addEventListener("click", () => this.toggle(-1));
        window.setInterval(() => {
            const off = this.blocked();
            this.up.disabled = off;
            this.down.disabled = off;
            if (off)
                this.stop();
        }, 500);
    }
    toggle(direction) {
        if (this.running !== null) {
            this.stop();
            return;
        }
        if (this.blocked()) {
            errorBox.show("Seek is off during FT8/FT4 reception and while paused");
            return;
        }
        void this.run(direction);
    }
    stop() {
        if (this.running !== null)
            this.running.cancelled = true;
        this.running = null;
        this.up.classList.remove("active");
        this.down.classList.remove("active");
    }
    // Lines of one I/Q centre, newest first, without transmit-blanked ones.
    held() {
        const lines = this.view.recentLines(SEEK_LINES).filter((line) => !line.blanked && line.centerHz !== null);
        const center = lines[0]?.centerHz;
        return lines.filter((line) => line.centerHz === center);
    }
    async run(direction) {
        const run = { cancelled: false };
        this.running = run;
        (direction > 0 ? this.up : this.down).classList.add("active");
        try {
            let lines = this.held();
            if (lines.length < SEEK_MIN_LINES)
                lines = (await this.collect(null, run, this.radio.frequencyHz)) ?? [];
            while (!run.cancelled) {
                const dial = this.radio.frequencyHz;
                const hold = maxHold(lines);
                if (dial === null || hold === null) {
                    if (!run.cancelled)
                        errorBox.show("Seek needs the waterfall (it is off or shows no lines)");
                    return;
                }
                const passband = this.radio.passbandHz ?? [-1500, 1500];
                const from = dial + (direction > 0 ? Math.max(...passband) : Math.min(...passband));
                const target = nextSignal(findSignals(hold), from, direction);
                if (target !== null) {
                    this.radio.tuneTo(dialFor(target, this.radio.modeName, this.radio.tuningStep));
                    return;
                }
                const next = nextView(dial, direction);
                if (next === null) {
                    errorBox.show(`Seek: no station ${direction > 0 ? "up to 30 MHz" : "down to 0.5 MHz"}`);
                    return;
                }
                this.radio.tuneTo(next);
                const fresh = await this.collect(hold.centerHz, run, next);
                if (fresh === null) {
                    if (!run.cancelled)
                        errorBox.show("Seek needs the waterfall (no lines after retuning)");
                    return;
                }
                lines = fresh;
            }
        }
        finally {
            if (this.running === run)
                this.stop();
        }
    }
    // Waits for lines of another I/Q centre than `oldCenter`, then SEEK_DWELL_MS
    // more; null if none come, or when cancelled or retuned by something else.
    async collect(oldCenter, run, dial) {
        const start = performance.now();
        let firstAt = null;
        while (!run.cancelled) {
            await new Promise((resolve) => window.setTimeout(resolve, SEEK_POLL_MS));
            const now = this.radio.frequencyHz;
            if (dial !== null && (now === null || Math.abs(now - dial) > SEEK_RETUNED_HZ)) {
                this.stop(); // the operator (or another page) tuned elsewhere
                return null;
            }
            const lines = this.held();
            const fresh = lines.length > 0 && lines[0].centerHz !== oldCenter;
            if (fresh && firstAt === null)
                firstAt = performance.now();
            if (firstAt !== null && performance.now() - firstAt >= SEEK_DWELL_MS)
                return lines;
            if (firstAt === null && performance.now() - start > SEEK_WAIT_MS)
                return null;
        }
        return null;
    }
}
// Antenna tuning results in the red message window (owner's decision
// 2026-11-14): a tuning in which the radio's meter never showed power means
// the radio did not tune, so the antenna is not tuned; with its screen blanked
// the G90 keeps the tuning and transmits it when the screen comes on
// (section 8 item 13). A tuning that does not end locks transmitting.
function installTuneWarnings(connection) {
    connection.eventHandlers.push((message) => {
        if (message.event === "atu_tune_end" && message.reason === "meter_zero" && message.emitted !== true) {
            errorBox.show("The radio did not tune: the antenna is not tuned. If the G90's screen is blanked, " +
                "it tunes (transmits a carrier) only when the screen comes on.");
        }
        else if (message.event === "atu_tune_overrun") {
            errorBox.show(`Antenna tuning: ${String(message.message)}`);
        }
    });
}
// The sound card's output level (owner's request 2026-11-14, server/mixer.py):
// it drives the radio after the server's limiter, for voice and FT8/FT4.
// Shown when the server has a card for the radio; changed also while
// transmitting, so it can be tuned watching the ALC. Kept on the server.
function installTxOutput(connection) {
    const label = el("tx-output-label");
    const slider = el("tx-output");
    const value = el("tx-output-value");
    const show = (db) => {
        if (typeof db !== "number")
            return;
        if (document.activeElement !== slider)
            slider.value = String(Math.round(db));
        value.textContent = "dB";
    };
    numberField(slider, (db) => {
        void connection.request("set_tx_output", { db }).then((answer) => {
            if (answer.ok === true)
                show(answer.db);
            else
                errorBox.show(`Card out: ${String(answer.message ?? answer.error)}`);
        });
    });
    connection.eventHandlers.push((message) => {
        if (message.event === "tx_output")
            show(message.db);
    });
    const opened = connection.onOpen;
    connection.onOpen = () => {
        opened();
        void connection.request("get_tx_output").then((answer) => {
            // Hidden also where the card has no such control (db: null; audit
            // 2026-11-15): every change would only fail.
            label.hidden = answer.ok !== true || typeof answer.db !== "number";
            show(answer.db);
        });
    };
}
// Pause (owner's request 2026-11-14): the header button stops listening and
// the waterfall, so no audio or spectrum crosses the link, and starts them
// again. Not while FT8/FT4 reception runs (it needs the audio); starting it
// ends a pause. Transmitting and the radio are not touched.
function installPause(connection, listening, digital) {
    const button = el("pause");
    let paused = false;
    let wasListening = false;
    const show = () => {
        button.classList.toggle("active", paused);
        button.textContent = paused ? "paused" : "pause";
        connection.setWaterfallPaused(paused);
    };
    const goOn = async (listen) => {
        if (!paused)
            return;
        paused = false;
        show();
        if (listen && wasListening)
            await listening?.resume();
    };
    button.addEventListener("click", () => {
        if (paused) {
            void goOn(true);
            return;
        }
        if (digital.receiving) {
            errorBox.show("Pause: stop the FT8/FT4 reception first (it needs the audio)");
            return;
        }
        wasListening = listening?.pause() ?? false;
        paused = true;
        show();
    });
    // The reception subscribes the audio itself, at its own bitrate.
    digital.beforeStart = () => goOn(false);
    return () => paused;
}
// Keyboard shortcuts (keys.ts). Held arrows repeat at most every TUNE_REPEAT_MS,
// so that a held key does not queue CAT commands faster than the radio takes them.
const TUNE_REPEAT_MS = 100;
// The focus back to the page after a control was used with the mouse (owner's
// choice 2026-11-15): a list after its choice, a slider when released, a box
// or a button after the click. Controls reached with the keyboard (Tab) keep
// it. Escape leaves any field outside a dialog.
function installFocusReturn() {
    let pointed = null;
    document.addEventListener("pointerdown", (e) => (pointed = e.target), true);
    // A key press makes the next click one of the keyboard (Space or Enter on a
    // control clicked earlier with the mouse; audit 2026-11-15).
    document.addEventListener("keydown", () => (pointed = null), true);
    const giveBack = (e) => {
        const target = e.target;
        if (!givesFocusBack(target) || !(target instanceof HTMLElement))
            return;
        // The control itself, or its label (a click on "NB" ticks the box).
        const label = target.closest("label");
        const byMouse = target === pointed || (label !== null && pointed instanceof Node && label.contains(pointed));
        if (!byMouse)
            return;
        // After the control's own handlers have run.
        window.setTimeout(() => {
            if (document.activeElement === target)
                target.blur();
        });
    };
    document.addEventListener("change", giveBack);
    document.addEventListener("click", (e) => {
        // Lists change on "change"; a click only opens them.
        if (!(e.target instanceof HTMLSelectElement))
            giveBack(e);
    });
    document.addEventListener("keydown", (e) => {
        const target = e.target;
        if (e.key !== "Escape" || !(target instanceof HTMLElement) || target.closest("dialog") !== null)
            return;
        if (typingTarget(target) || givesFocusBack(target))
            target.blur();
    });
}
function installShortcuts(radio, digital, seeker) {
    installFocusReturn();
    let lastRepeat = 0;
    const run = (action) => {
        switch (action.kind) {
            case "tune":
                radio.tuneBy(action.direction, action.stepHz);
                break;
            case "band":
                radio.bandBy(action.direction);
                break;
            case "digital":
                digital.startOn(action.mode, radio.bandName);
                break;
            case "log":
                el("log-open").click();
                break;
            case "frontEnd":
                radio.setFrontEnd(action.state);
                break;
            case "agc":
                radio.nextAgc();
                break;
            case "nb":
                radio.toggleNb();
                break;
            case "rfGain":
                radio.rfGainBy(action.direction);
                break;
            case "seek":
                seeker.toggle(action.direction);
                break;
        }
    };
    window.addEventListener("keydown", (e) => {
        if (typingTarget(e.target))
            return;
        const action = actionFor(e);
        if (action === null)
            return;
        e.preventDefault(); // Alt + arrows would go back or forward in the history
        if (e.repeat) {
            if (action.kind !== "tune" && action.kind !== "rfGain")
                return;
            if (e.timeStamp - lastRepeat < TUNE_REPEAT_MS)
                return;
            lastRepeat = e.timeStamp;
        }
        run(action);
    });
    const list = el("about-keys");
    list.replaceChildren(...SHORTCUTS.map(([keys, what]) => {
        const row = document.createElement("tr");
        for (const text of [keys, what]) {
            const cell = document.createElement("td");
            cell.textContent = text;
            row.append(cell);
        }
        return row;
    }));
}
void main();
