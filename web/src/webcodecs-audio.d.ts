// SPDX-License-Identifier: GPL-3.0-or-later
//
// The part of WebCodecs audio this frontend uses; TypeScript 5.6's lib.dom
// declares only the video half. Shapes follow the WebCodecs specification.

interface AudioData {
  readonly numberOfFrames: number;
  readonly timestamp: number; // microseconds, as given to the EncodedAudioChunk
  copyTo(destination: Float32Array, options: { planeIndex: number; format?: "f32-planar" }): void;
  close(): void;
}

interface AudioDecoderConfig {
  codec: string;
  sampleRate: number;
  numberOfChannels: number;
}

interface AudioDecoderInit {
  output: (data: AudioData) => void;
  error: (error: DOMException) => void;
}

declare class AudioDecoder {
  constructor(init: AudioDecoderInit);
  readonly state: "unconfigured" | "configured" | "closed";
  configure(config: AudioDecoderConfig): void;
  decode(chunk: EncodedAudioChunk): void;
  reset(): void;
  close(): void;
}

declare class EncodedAudioChunk {
  constructor(init: { type: "key" | "delta"; timestamp: number; data: BufferSource });
}

interface AudioDataInit {
  format: "f32-planar";
  sampleRate: number;
  numberOfFrames: number;
  numberOfChannels: number;
  timestamp: number;
  data: Float32Array;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
declare var AudioData: { new (init: AudioDataInit): AudioData };

interface EncodedAudioChunk {
  readonly byteLength: number;
  copyTo(destination: Uint8Array): void;
}

interface AudioEncoderConfig {
  codec: string;
  sampleRate: number;
  numberOfChannels: number;
  bitrate?: number;
}

interface AudioEncoderInit {
  output: (chunk: EncodedAudioChunk) => void;
  error: (error: DOMException) => void;
}

declare class AudioEncoder {
  constructor(init: AudioEncoderInit);
  readonly state: "unconfigured" | "configured" | "closed";
  configure(config: AudioEncoderConfig): void;
  encode(data: AudioData): void;
  close(): void;
}
