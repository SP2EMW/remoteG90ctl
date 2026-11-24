// SPDX-License-Identifier: GPL-3.0-or-later
//
// Receive audio in the browser (stage 3): binary frames from the server
// (type 0x02: raw PCM or Opus, 20 ms, 48 kHz mono), Opus decoded with the
// browser's WebCodecs (owner's choice: no WebAssembly decoder), played by the
// "rx-player" AudioWorklet with a 150 ms buffer.
import { FRAME_CLOCK, parseClock } from "./ft8.js";
import { rxDspLatency } from "./rxdsp.js";
import { EQ_BANDS, EQ_Q, TX_DSP_DEFAULTS, txDspLatencyMs } from "./txdsp.js";
// A fresh copy of the worklet each time an audio context opens: a copy kept
// by the browser from before an update ran the old receive buffer for hours
// (owner's report 2026-11-18). About 10 kB once per start of listening.
const WORKLET_URL = () => `/static/audio-worklet.js?t=${Date.now()}`;
export const FRAME_AUDIO = 0x02;
const HEADER_BYTES = 8;
const CODEC_RAW = 0;
const SAMPLE_RATE = 48000;
// Receive chain (stage 8, D49): player -> band-pass (two 2nd-order high-pass and
// two low-pass sections, 24 dB/octave, Butterworth) -> manual notch -> "rx-dsp"
// (automatic notch, noise reduction) -> analyser (the page's audio spectrum) ->
// volume. The band follows the passband sliders under the waterfall.
const BUTTERWORTH_Q = [0.5412, 1.3066];
const CHUNK_SAMPLES = 960; // 20 ms, as posted by the "tx-capture" worklet
// A recorded call is at most this long (owner's decision 2026-11-09).
export const MAX_RECORDING_S = 15;
const NOTCH_Q = 12;
// The page reads the spectra 15 times a second (it was 60): 0.6 ** 4 keeps the
// trace's former reaction time.
const SPECTRUM_SMOOTHING = 0.13;
// The server's numbers of the Opus frames given to the decoder, in order, so
// that each decoded frame keeps its own. The decoder's own timestamps cannot
// be used: Chrome takes only the first chunk's and counts on by 20 ms, so a
// new subscription (numbered from 0 again) or a frame lost on the way moved
// the audio against the server's clock frames for good, and FT8/FT4 decoding
// stopped (the receiver drops audio far from the clock).
export class FrameNumbers {
    constructor() {
        this.queue = [];
    }
    given(seq) {
        this.queue.push(seq);
        if (this.queue.length > FrameNumbers.MAX_PENDING)
            this.queue.shift();
    }
    // The number of the frame the decoder put out now; null if none is known.
    decoded() {
        return this.queue.shift() ?? null;
    }
    clear() {
        this.queue.length = 0;
    }
}
FrameNumbers.MAX_PENDING = 50; // 1 s of frames: more means the decoder lost some
// Receive: the whole delay from the radio to the speaker kept within this
// (owner's decision 2026-11-21: 250 ms; 2026-11-23: 350 ms, with the playback
// buffer allowed up to 250 ms); the link's usual delay is its smallest in the
// last AGE_WINDOW_MS.
const LATENCY_BUDGET_MS = 350;
const AGE_WINDOW_MS = 5000;
export class RxAudio {
    constructor() {
        this.numbers = new FrameNumbers();
        this.context = null;
        this.node = null;
        this.gain = null;
        this.decoder = null;
        this.volume = 0.8;
        this.highPass = [];
        this.lowPass = [];
        this.notchFilter = null;
        this.dspNode = null;
        // Graphic equaliser of what is heard, dB per band of EQ_BANDS (no delay).
        this.eqNodes = [];
        this.eqGains = EQ_BANDS.map(() => 0);
        this.analyser = null;
        this.band = null;
        this.notchHz = null;
        this.dsp = { anf: false, nr: "off", level: 0.5 };
        // FT8/FT4 on: the automatic notch would take out the signals (AGENTS.md stage 8).
        this.digital = false;
        // Called with the playback buffer, its target and the underrun count, and
        // the whole delay from the radio to the speaker (null until the server's
        // clock frames date the audio).
        this.onStats = () => { };
        // Latency budget (2026-11-21): the age of each frame on arrival, from the
        // server's clock frames (both clocks set by NTP: tens of ms of error).
        this.clock = null;
        this.ages = [];
        this.lastAgeMs = null;
        // Decoded audio as it arrived, before any processing (FT8/FT4, stage 6), and
        // the server's clock frames that date it.
        this.onPcm = null;
        // The same for RTTY reception (D61), which may run beside FT8/FT4.
        this.onRttyPcm = null;
        // And for reading CW (D62).
        this.onCwPcm = null;
        this.onClock = () => { };
    }
    // The FT8/FT4 receiver's tap on the audio: whatever it does, playback goes on
    // (a fault there once silenced the listening for good).
    tap(seq, samples) {
        for (const listener of [this.onPcm, this.onRttyPcm, this.onCwPcm]) {
            if (listener === null)
                continue;
            try {
                listener(seq, samples.slice());
            }
            catch (err) {
                console.warn("audio tap:", err);
            }
        }
    }
    static supported() {
        return typeof AudioDecoder !== "undefined" && typeof AudioWorkletNode !== "undefined";
    }
    // Must run from a click: browsers start audio only after a user gesture.
    async start() {
        const context = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
        await context.audioWorklet.addModule(WORKLET_URL());
        const node = new AudioWorkletNode(context, "rx-player", { outputChannelCount: [1] });
        node.port.onmessage = (e) => {
            const data = e.data;
            this.report(data.bufferedMs, data.underruns, data.targetMs);
        };
        const gain = context.createGain();
        gain.gain.value = this.volume;
        this.highPass = BUTTERWORTH_Q.map((q) => new BiquadFilterNode(context, { type: "highpass", Q: q }));
        this.lowPass = BUTTERWORTH_Q.map((q) => new BiquadFilterNode(context, { type: "lowpass", Q: q }));
        this.notchFilter = new BiquadFilterNode(context, { type: "allpass" });
        this.dspNode = new AudioWorkletNode(context, "rx-dsp", { outputChannelCount: [1] });
        this.eqNodes = EQ_BANDS.map((hz) => new BiquadFilterNode(context, { type: "peaking", frequency: hz, Q: EQ_Q }));
        this.analyser = new AnalyserNode(context, { fftSize: 4096, smoothingTimeConstant: SPECTRUM_SMOOTHING });
        let last = node;
        for (const stage of [
            ...this.highPass,
            ...this.lowPass,
            this.notchFilter,
            this.dspNode,
            ...this.eqNodes,
            this.analyser,
        ]) {
            last = last.connect(stage);
        }
        last.connect(gain).connect(context.destination);
        const decoder = new AudioDecoder({
            output: (data) => {
                const samples = new Float32Array(data.numberOfFrames);
                data.copyTo(samples, { planeIndex: 0, format: "f32-planar" });
                const seq = this.numbers.decoded() ?? Math.round(data.timestamp / 20000);
                data.close();
                this.dated(seq);
                this.tap(seq, samples);
                node.port.postMessage({ type: "push", samples }, [samples.buffer]);
            },
            error: (e) => console.warn("audio decoder:", e.message),
        });
        decoder.configure({ codec: "opus", sampleRate: SAMPLE_RATE, numberOfChannels: 1 });
        this.context = context;
        this.node = node;
        this.gain = gain;
        this.decoder = decoder;
        this.apply();
    }
    // Audio band in Hz (null: no filter), from the passband sliders.
    setBand(band) {
        this.band = band;
        this.apply();
    }
    // Manual notch frequency in Hz, or null.
    setNotch(hz) {
        this.notchHz = hz;
        this.apply();
    }
    setDsp(settings) {
        this.dsp = settings;
        this.apply();
    }
    // dB per band of EQ_BANDS.
    setEq(gains) {
        this.eqGains = [...gains];
        this.apply();
    }
    get digitalOn() {
        return this.digital;
    }
    setDigital(on) {
        this.digital = on;
        this.apply();
    }
    // Added by the processing (not the playback buffer), in ms.
    get dspLatencyMs() {
        return (rxDspLatency(this.dsp) / SAMPLE_RATE) * 1000;
    }
    apply() {
        const time = this.context?.currentTime ?? 0;
        const [low, high] = this.band ?? [0, SAMPLE_RATE / 2];
        for (const f of this.highPass)
            f.frequency.setValueAtTime(Math.max(10, low), time);
        for (const f of this.lowPass)
            f.frequency.setValueAtTime(Math.min(SAMPLE_RATE / 2 - 100, high), time);
        if (this.notchFilter !== null) {
            this.notchFilter.type = this.notchHz === null ? "allpass" : "notch";
            this.notchFilter.frequency.setValueAtTime(this.notchHz ?? 1000, time);
            this.notchFilter.Q.setValueAtTime(NOTCH_Q, time);
        }
        this.eqNodes.forEach((f, i) => f.gain.setValueAtTime(this.eqGains[i] ?? 0, time));
        this.dspNode?.port.postMessage({ ...this.dsp, anf: this.dsp.anf && !this.digital });
    }
    stop() {
        if (this.decoder !== null && this.decoder.state !== "closed")
            this.decoder.close();
        void this.context?.close();
        this.context = this.node = this.gain = this.decoder = null;
        this.notchFilter = this.dspNode = this.analyser = null;
        this.highPass = this.lowPass = this.eqNodes = [];
    }
    get running() {
        return this.context !== null;
    }
    setVolume(volume) {
        this.volume = volume;
        if (this.gain !== null)
            this.gain.gain.value = volume;
    }
    // A new subscription: the server numbers its frames from 0 again, and frames
    // of the old one still in the decoder must not come out with new numbers.
    newStream() {
        this.numbers.clear();
        const decoder = this.decoder;
        if (decoder !== null && decoder.state === "configured") {
            decoder.reset();
            decoder.configure({ codec: "opus", sampleRate: SAMPLE_RATE, numberOfChannels: 1 });
        }
    }
    // The age of frame `seq` now: the end of its audio on the server's clock.
    dated(seq) {
        const clock = this.clock;
        if (clock === null || clock.synchronized === false)
            return;
        const ms = Date.now() - (clock.utcMs + (seq - clock.seq + 1) * 20);
        if (!Number.isFinite(ms) || Math.abs(ms) > 60000)
            return;
        const now = performance.now();
        this.lastAgeMs = ms;
        this.ages.push({ at: now, ms });
        while (this.ages.length > 0 && now - this.ages[0].at > AGE_WINDOW_MS)
            this.ages.shift();
    }
    // The stats for the page, and the worklet's limit: the buffer may hold what
    // is left of the 250 ms after the link's usual delay and the output's.
    report(bufferedMs, underruns, targetMs) {
        const outMs = this.outputLatencyMs();
        let latencyMs = null;
        if (this.lastAgeMs !== null && this.ages.length > 0) {
            latencyMs = this.lastAgeMs + bufferedMs + outMs;
            const usual = Math.min(...this.ages.map((a) => a.ms));
            const limitMs = LATENCY_BUDGET_MS - Math.max(0, usual) - outMs;
            this.node?.port.postMessage({ type: "limit", limit: (Math.max(0, limitMs) / 1000) * SAMPLE_RATE });
        }
        this.onStats({ bufferedMs, targetMs, underruns, latencyMs });
    }
    outputLatencyMs() {
        const context = this.context;
        if (context === null)
            return 0;
        const out = context.outputLatency ?? 0;
        return (context.baseLatency + out) * 1000;
    }
    // Forget buffered audio (after retuning, so the old frequency is not heard).
    flush() {
        this.node?.port.postMessage({ type: "flush" });
    }
    handle(buffer) {
        if (this.node === null || buffer.byteLength < HEADER_BYTES)
            return;
        const view = new DataView(buffer);
        if (view.getUint8(0) === FRAME_CLOCK) {
            const clock = parseClock(buffer);
            if (clock !== null) {
                this.clock = clock;
                this.onClock(clock);
            }
            return;
        }
        if (view.getUint8(0) !== FRAME_AUDIO)
            return;
        const codec = view.getUint8(1);
        const seq = view.getUint32(4, true);
        const payload = new Uint8Array(buffer, HEADER_BYTES);
        if (codec === CODEC_RAW) {
            const pcm = new Int16Array(buffer.slice(HEADER_BYTES));
            const samples = Float32Array.from(pcm, (v) => v / 32768);
            this.dated(seq);
            this.tap(seq, samples);
            this.node.port.postMessage({ type: "push", samples }, [samples.buffer]);
        }
        else if (this.decoder !== null && this.decoder.state === "configured") {
            this.numbers.given(seq);
            this.decoder.decode(new EncodedAudioChunk({ type: "key", timestamp: seq * 20000, data: payload }));
        }
    }
}
export const FRAME_TX_AUDIO = 0x03;
const TX_BACKLOG_FRAMES = 5; // 100 ms
const TX_BACKLOG_SLACK = 512; // the page's small requests on the same connection
const CODEC_OPUS = 1;
const TX_HEADER_BYTES = 8;
// Voice to the radio (stage 4). The microphone is open once prepared, but a
// frame leaves the browser only while `sending` is true, i.e. while the page
// holds a transmit lease; the server plays it only during that lease anyway,
// through its own limiter. The browser's own speech processing is off (D39).
export class TxAudio {
    constructor() {
        this.context = null;
        this.stream = null;
        this.encoder = null;
        this.codec = "opus";
        this.seq = 0;
        this.sending = false;
        // Processing chain (stage 8, D49): microphone -> gain -> low cut -> high cut
        // -> 6-band equaliser -> noise gate -> compressor and make-up gain (when on)
        // -> analyser -> capture (meter, encoder). The monitor plays the end of the
        // chain in this browser. Clipped at full scale before encoding; the server's
        // limiter still applies.
        this.micGain = 1;
        this.inGain = null;
        this.lowCuts = [];
        this.highCuts = [];
        this.eq = [];
        this.gateNode = null;
        this.compressor = null;
        this.makeup = null;
        this.monitorGain = null;
        this.analyser = null;
        this.dsp = TX_DSP_DEFAULTS;
        // Recorder (owner's request 2026-11-09): the processed microphone, kept in
        // this page only, played locally to judge the settings, or sent instead of
        // the microphone while a lease is held (automatic calling).
        this.recording = null;
        this.replay = null;
        this.onRecordEnd = () => { };
        // A silent copy of the recording being sent, for the spectrum (the replayed
        // chunks go to the encoder, not through the Web Audio graph).
        this.replayAnalyser = null;
        this.localAnalyser = null;
        this.localPlay = null;
        this.shadow = null;
        this.onReplayEnd = () => { };
        // Set by the page: sends a binary frame to the server.
        this.send = () => { };
        // Bytes the browser has not sent yet on the connection (WebSocket
        // bufferedAmount): over TX_BACKLOG_FRAMES of voice waiting, new frames are
        // skipped rather than sent late in a burst (latency budget, 2026-11-21).
        this.backlog = () => 0;
        this.skipped = 0;
        this.frameBytes = 0;
        // Peak level of the microphone in dBFS, for the meter.
        this.onLevel = () => { };
    }
    // Microphone gain set on the page (linear), at the start of the chain.
    set gain(value) {
        this.micGain = value;
        this.inGain?.gain.setValueAtTime(value, this.context?.currentTime ?? 0);
    }
    get gain() {
        return this.micGain;
    }
    setDsp(settings) {
        this.dsp = settings;
        this.applyDsp();
    }
    get dspLatencyMs() {
        return txDspLatencyMs(this.dsp);
    }
    // The voice waiting in the browser to be sent, in ms (from the bytes).
    get backlogMs() {
        return this.frameBytes > 0 ? (this.backlog() / this.frameBytes) * 20 : 0;
    }
    static supported() {
        return typeof AudioEncoder !== "undefined" && navigator.mediaDevices !== undefined;
    }
    get ready() {
        return this.context !== null;
    }
    async prepare(codec, bitrateKbps) {
        if (this.ready) {
            this.configure(codec, bitrateKbps);
            return;
        }
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
        });
        const context = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
        await context.audioWorklet.addModule(WORKLET_URL());
        const node = new AudioWorkletNode(context, "tx-capture", { numberOfInputs: 1, numberOfOutputs: 0 });
        this.inGain = new GainNode(context, { gain: this.micGain });
        this.lowCuts = BUTTERWORTH_Q.map((q) => new BiquadFilterNode(context, { type: "highpass", Q: q }));
        this.highCuts = BUTTERWORTH_Q.map((q) => new BiquadFilterNode(context, { type: "lowpass", Q: q }));
        this.eq = EQ_BANDS.map((hz) => new BiquadFilterNode(context, { type: "peaking", frequency: hz, Q: EQ_Q }));
        this.gateNode = new AudioWorkletNode(context, "tx-gate", { outputChannelCount: [1] });
        this.compressor = new DynamicsCompressorNode(context, { knee: 6, attack: 0.005, release: 0.15 });
        this.makeup = new GainNode(context);
        this.analyser = new AnalyserNode(context, { fftSize: 4096, smoothingTimeConstant: SPECTRUM_SMOOTHING });
        this.monitorGain = new GainNode(context, { gain: 0 });
        let last = context.createMediaStreamSource(stream);
        for (const stage of [this.inGain, ...this.lowCuts, ...this.highCuts, ...this.eq, this.gateNode]) {
            last = last.connect(stage);
        }
        this.makeup.connect(this.analyser).connect(node);
        this.analyser.connect(this.monitorGain).connect(context.destination);
        node.port.onmessage = (e) => {
            const data = e.data;
            const mic = clip(data.samples);
            if (this.recording !== null) {
                this.recording.push(mic);
                if (this.recording.length * CHUNK_SAMPLES >= MAX_RECORDING_S * SAMPLE_RATE) {
                    this.onRecordEnd();
                }
            }
            // The microphone's 20 ms chunks are the clock: during a replay each one
            // is replaced by the next 20 ms of the recording.
            const replayed = this.sending ? this.nextReplayChunk() : null;
            const chunk = replayed ?? mic;
            let peak = data.peak;
            if (replayed !== null)
                peak = replayed.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
            this.onLevel(20 * Math.log10(Math.max(Math.min(1, peak), 1e-6)));
            if (this.sending)
                this.sendChunk(chunk);
        };
        this.context = context;
        this.stream = stream;
        this.applyDsp();
        this.configure(codec, bitrateKbps);
    }
    startRecording() {
        this.recording = [];
    }
    // The recording so far (null if none was running).
    stopRecording() {
        const chunks = this.recording;
        this.recording = null;
        if (chunks === null || chunks.length === 0)
            return null;
        const samples = new Float32Array(chunks.length * CHUNK_SAMPLES);
        chunks.forEach((chunk, i) => samples.set(chunk, i * CHUNK_SAMPLES));
        return samples;
    }
    get recordingActive() {
        return this.recording !== null;
    }
    // Played in this browser only (headphones), never to the radio.
    playLocally(samples) {
        const context = this.context;
        if (context === null)
            return null;
        const buffer = context.createBuffer(1, samples.length, SAMPLE_RATE);
        buffer.copyToChannel(samples, 0);
        const source = new AudioBufferSourceNode(context, { buffer });
        this.localAnalyser ?? (this.localAnalyser = new AnalyserNode(context, { fftSize: 4096, smoothingTimeConstant: SPECTRUM_SMOOTHING }));
        source.connect(this.localAnalyser).connect(context.destination);
        source.onended = () => {
            if (this.localPlay === source)
                this.localPlay = null;
        };
        source.start();
        this.localPlay = source;
        return source;
    }
    // The local playback, shown in the transmit spectrum in another colour and
    // marked "not sent" (owner's exception to the stage 8 rule, D51).
    get localSpectrumAnalyser() {
        return this.localPlay !== null && !this.sending ? this.localAnalyser : null;
    }
    // While sending, the recording goes out instead of the microphone.
    startReplay(samples) {
        this.replay = { samples, position: 0 };
    }
    stopReplay() {
        this.replay = null;
        this.stopShadow();
    }
    // What the transmit spectrum shows: only what goes to the radio, so nothing
    // outside a lease (AGENTS.md, stage 8) — the microphone chain, or the
    // recording during an automatic call.
    get spectrumAnalyser() {
        if (!this.sending)
            return null;
        return this.shadow !== null ? this.replayAnalyser : this.analyser;
    }
    startShadow(samples) {
        const context = this.context;
        if (context === null)
            return;
        this.stopShadow();
        if (this.replayAnalyser === null) {
            // Pulled through a muted gain: some browsers only run nodes that reach the output.
            this.replayAnalyser = new AnalyserNode(context, { fftSize: 4096, smoothingTimeConstant: SPECTRUM_SMOOTHING });
            this.replayAnalyser.connect(new GainNode(context, { gain: 0 })).connect(context.destination);
        }
        const buffer = context.createBuffer(1, samples.length, SAMPLE_RATE);
        buffer.copyToChannel(samples, 0);
        const shadow = new AudioBufferSourceNode(context, { buffer });
        shadow.connect(this.replayAnalyser); // silent: the analyser's output is muted
        shadow.onended = () => {
            if (this.shadow === shadow)
                this.shadow = null;
        };
        shadow.start();
        this.shadow = shadow;
    }
    stopShadow() {
        try {
            this.shadow?.stop();
        }
        catch {
            // already ended
        }
        this.shadow = null;
    }
    nextReplayChunk() {
        const replay = this.replay;
        if (replay === null)
            return null;
        if (replay.position === 0)
            this.startShadow(replay.samples);
        const chunk = new Float32Array(CHUNK_SAMPLES);
        chunk.set(replay.samples.subarray(replay.position, replay.position + CHUNK_SAMPLES));
        replay.position += CHUNK_SAMPLES;
        if (replay.position >= replay.samples.length) {
            this.replay = null;
            window.setTimeout(() => this.onReplayEnd(), 0);
        }
        return chunk;
    }
    applyDsp() {
        const context = this.context;
        if (context === null || this.gateNode === null || this.compressor === null || this.makeup === null)
            return;
        const time = context.currentTime;
        const d = this.dsp;
        for (const f of this.lowCuts)
            f.frequency.setValueAtTime(d.lowCut > 0 ? d.lowCut : 10, time);
        for (const f of this.highCuts) {
            f.frequency.setValueAtTime(d.highCut > 0 ? d.highCut : SAMPLE_RATE / 2 - 100, time);
        }
        this.eq.forEach((f, i) => f.gain.setValueAtTime(d.eq[i] ?? 0, time));
        this.gateNode.port.postMessage({ enabled: d.gate, thresholdDb: d.gateDb });
        // The compressor delays by its look-ahead even at ratio 1: off = not in the chain.
        this.gateNode.disconnect();
        this.compressor.disconnect();
        if (d.compressor) {
            this.compressor.threshold.setValueAtTime(d.thresholdDb, time);
            this.compressor.ratio.setValueAtTime(d.ratio, time);
            this.gateNode.connect(this.compressor).connect(this.makeup);
            this.makeup.gain.setValueAtTime(10 ** (d.makeupDb / 20), time);
        }
        else {
            this.gateNode.connect(this.makeup);
            this.makeup.gain.setValueAtTime(1, time);
        }
        this.monitorGain?.gain.setValueAtTime(d.monitor ? 1 : 0, time);
    }
    configure(codec, bitrateKbps) {
        this.codec = codec;
        if (this.encoder !== null && this.encoder.state !== "closed")
            this.encoder.close();
        this.encoder = null;
        if (codec !== "opus")
            return;
        const encoder = new AudioEncoder({
            output: (chunk) => {
                const frame = new Uint8Array(TX_HEADER_BYTES + chunk.byteLength);
                chunk.copyTo(frame.subarray(TX_HEADER_BYTES));
                this.frame(frame, CODEC_OPUS);
            },
            error: (e) => console.warn("audio encoder:", e.message),
        });
        encoder.configure({ codec: "opus", sampleRate: SAMPLE_RATE, numberOfChannels: 1, bitrate: bitrateKbps * 1000 });
        this.encoder = encoder;
    }
    sendChunk(samples) {
        if (this.codec === "raw") {
            const frame = new Uint8Array(TX_HEADER_BYTES + samples.length * 2);
            const pcm = new DataView(frame.buffer, TX_HEADER_BYTES);
            samples.forEach((v, i) => pcm.setInt16(i * 2, Math.max(-1, Math.min(1, v)) * 32767, true));
            this.frame(frame, CODEC_RAW);
        }
        else if (this.encoder !== null && this.encoder.state === "configured") {
            const timestamp = this.seq * 20000;
            this.encoder.encode(new AudioData({
                format: "f32-planar",
                sampleRate: SAMPLE_RATE,
                numberOfFrames: samples.length,
                numberOfChannels: 1,
                timestamp,
                data: samples,
            }));
        }
    }
    frame(frame, codec) {
        const view = new DataView(frame.buffer);
        view.setUint8(0, FRAME_TX_AUDIO);
        view.setUint8(1, codec);
        view.setUint16(2, 960, true);
        view.setUint32(4, this.seq++, true);
        this.frameBytes = frame.byteLength;
        if (!this.sending)
            return;
        if (this.backlog() > TX_BACKLOG_FRAMES * frame.byteLength + TX_BACKLOG_SLACK) {
            this.skipped++;
            return;
        }
        this.send(frame.buffer);
    }
    close() {
        this.sending = false;
        this.stream?.getTracks().forEach((t) => t.stop());
        void this.context?.close();
        if (this.encoder !== null && this.encoder.state !== "closed")
            this.encoder.close();
        this.context = this.stream = this.encoder = null;
        this.inGain = this.gateNode = this.compressor = this.makeup = this.monitorGain = this.analyser = null;
        this.lowCuts = this.highCuts = this.eq = [];
    }
}
function clip(samples) {
    return samples.map((v) => Math.max(-1, Math.min(1, v)));
}
