// SPDX-License-Identifier: GPL-3.0-or-later
//
// FT8/FT4 decoder in a Web Worker (stage 6, decisions.md D52), so that a busy
// band does not stall the page's audio and waterfall. One cycle in, its
// decodes out. ft8ts keeps the hashed callsigns and, for FT8, the previous
// cycles (a7 decoding) between calls, as WSJT-X does.

import { DECODE_BAND_HZ } from "./ft8.js";
import { decodeFT4, decodeFT8, FT8History, HashCallBook } from "./vendor/ft8ts/ft8ts.mjs";

export interface DecodeRequest {
  mode: "FT8" | "FT4";
  startMs: number;
  samples: Float32Array;
  sampleRate: number;
  depth: number;
}

const book = new HashCallBook();
const history = new FT8History();
const scope = self as unknown as {
  onmessage: ((e: MessageEvent<DecodeRequest>) => void) | null;
  postMessage(message: unknown): void;
};

scope.onmessage = (e) => {
  const { mode, startMs, samples, sampleRate, depth } = e.data;
  const started = performance.now();
  try {
    const options = { sampleRate, depth, freqLow: DECODE_BAND_HZ[0], freqHigh: DECODE_BAND_HZ[1], hashCallBook: book };
    const decodes =
      mode === "FT8"
        ? decodeFT8(samples, { ...options, history, slotStart: startMs })
        : decodeFT4(samples, options);
    scope.postMessage({
      mode,
      startMs,
      depth,
      ms: performance.now() - started,
      decodes: decodes.map((d) => ({ freq: d.freq, dt: d.dt, snr: d.snr, msg: d.msg, ap: d.ap ?? 0 })),
    });
  } catch (err) {
    scope.postMessage({ mode, startMs, ms: performance.now() - started, error: String(err) });
  }
};
