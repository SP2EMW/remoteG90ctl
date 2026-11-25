// SPDX-License-Identifier: GPL-3.0-or-later
//
// SSTV decoder (sstv.ts, decisions.md D60) in a Web Worker, so that decoding
// never stalls the page's audio and waterfall. Audio in; pictures and their
// lines out.
import { SstvDecoder } from "./sstv.js";
const scope = self;
const decoder = new SstvDecoder(48000);
decoder.onPicture = (picture) => scope.postMessage({ kind: "picture", ...picture });
decoder.onLines = (first, rows, width, rgba) => scope.postMessage({ kind: "lines", first, rows, width, rgba }, [rgba.buffer]);
decoder.onEnd = () => scope.postMessage({ kind: "end" });
scope.onmessage = (e) => {
    try {
        if (e.data.kind === "audio")
            decoder.push(e.data.samples);
        else
            decoder.endPicture();
    }
    catch (err) {
        scope.postMessage({ kind: "error", message: String(err) });
    }
};
