// SPDX-License-Identifier: GPL-3.0-or-later
//
// Types of the parts of ft8ts (@e04/ft8ts 0.0.18, GPL-3.0, by e04; a port of
// WSJT-X) that the page uses. The library itself is copied next to the page by
// web/scripts/vendor.mjs (decisions.md D52, docs/third-party.md).

export interface DecodedMessage {
  freq: number; // audio frequency, Hz
  dt: number; // time offset from the nominal start, s
  snr: number; // dB in 2500 Hz
  msg: string;
  sync: number;
  ap?: number; // a priori decode: 7 for a7, 1 for "CQ ??? ???"
}

export interface DecodeOptions {
  sampleRate?: number;
  freqLow?: number;
  freqHigh?: number;
  depth?: number;
  hashCallBook?: HashCallBook;
  history?: FT8History;
  slotStart?: Date | number;
}

export declare class HashCallBook {
  constructor();
}

export declare class FT8History {
  constructor();
}

export declare function decodeFT8(samples: Float32Array | Float64Array, options?: DecodeOptions): DecodedMessage[];
export declare function decodeFT4(samples: Float32Array | Float64Array, options?: DecodeOptions): DecodedMessage[];
export declare function encodeFT8(message: string, options?: { sampleRate?: number; baseFrequency?: number }): Float32Array;
export declare function encodeFT4(message: string, options?: { sampleRate?: number; baseFrequency?: number }): Float32Array;
