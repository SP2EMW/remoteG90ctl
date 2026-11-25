# Third-party code and ideas

The project is licensed under GPL-3.0-or-later (`LICENSE`).
This file records everything taken from other projects: code (with its licence and author) and ideas and facts (without copying code).

## Code copied or adapted

| Our file | Source | Author | Licence | Changes |
|---|---|---|---|---|
| `src/remoteg90ctl/server/static/vendor/ft8ts/ft8ts.mjs` (copied by `web/scripts/vendor.mjs` from the npm package `@e04/ft8ts` 0.0.18, unchanged except for the removed source-map reference); licence next to it (`LICENSE`) | [ft8ts](https://github.com/e04/ft8ts) — a port of the FT8/FT4 decoder and encoder from [WSJT-X](https://wsjt.sourceforge.io/wsjtx.html) 3.0.1 (K1JT, K9AN and others) | e04 | GPL-3.0 | unchanged; TypeScript types are our own (`web/src/vendor/ft8ts/ft8ts.d.mts`) (D52) |

## Possibly derived code

| Our code | Similar code | Licence | Note |
|---|---|---|---|
| `fft` in `web/src/rxdsp.ts` (in-place radix-2 FFT) | `fftInPlace` in [no-sdr](https://github.com/gbozo/no-sdr) `client/src/engine/noise-reduction.ts`, by gbozo | GPL-3.0-or-later | The textbook Cooley–Tukey algorithm with the usual bit-reversal loop, but nearly the same as no-sdr's routine line by line; treated as possibly derived from it (found by the check below) |

## Patterns and facts (no code copied)

| What | Source | Where it is used |
|---|---|---|
| Measured properties of the G90's I/Q output (48 kHz, `Z = L − jR`, image rejection, DC, the offset model, ground loops) | [AetherSDR RFC #5972](https://github.com/aethersdr/AetherSDR/issues/5972) | [g90-measurements.md](g90-measurements.md) (as hypotheses) |
| The I/Q output carries the signal during TX | [pdn-soundmodem #694](https://github.com/packet-net/pdn-soundmodem/issues/694) | [g90-measurements.md](g90-measurements.md) |
| CI-V speed 19200, address 0x70 | [G90SDR](https://github.com/lukaszs1942/G90SDR) | [g90-measurements.md](g90-measurements.md) |
| CI-V socket on the panel (User) | [g90-workbench](https://github.com/NanamiKite/g90-workbench) | [g90-measurements.md](g90-measurements.md) |
| A list of working CI-V commands (its pattern of sharing the radio through rigctld no longer applies — D29) | [go-g90proxy](https://github.com/logocomune/go-g90proxy) | [g90-measurements.md](g90-measurements.md); `docs/decisions.md` D20 |
| Decoding 0–255 levels with the G90's garbled last BCD digit (idea; code written for this project, not copied) | [Hamlib](https://github.com/Hamlib/Hamlib) `rigs/icom/xiegu.c`, `g90_get_af_quirk` (LGPL-2.1-or-later) | `src/remoteg90ctl/civ/commands.py` (`g90_level_from_bytes`) |
| A playback buffer in an `AudioWorklet` with a start threshold and clock-drift correction (idea; code written for this project, not copied) | [no-sdr](https://github.com/gbozo/no-sdr) `client/src/engine/audio.ts` (GPL-3.0-or-later) | `web/src/audio-worklet.ts` |
| A separate audio stream over WebSocket with `nodelay` (idea; UHRR sends uncompressed 8 kHz PCM) | [UHRR](https://github.com/F4HTB/Universal_HamRadio_Remote_HTML5) (GPL-3.0) | `src/remoteg90ctl/server/audio_service.py` |
| The voice transmit path: microphone in the browser → Opus → WebSocket → server → sound card (idea only; the safety measures and the code were written for this project, not copied) | [UHRR](https://github.com/F4HTB/Universal_HamRadio_Remote_HTML5) (GPL-3.0) | `web/src/audio.ts`, `src/remoteg90ctl/server/tx_audio.py` |
| A summary of Xiegu CI-V commands (command, mode and radio-ID codes, firmware notes) | [Xiegu Radio CI-V Reference](https://xiegu.eu/wp-content/uploads/sites/4/2023/08/XIEGU-RADIO-CI-V-REFERENCE.pdf) | `docs/civ-g90.md` |
| The default CI-V address 0x70 and answers to other addresses, VOX versus PTT over CAT, a command table | [Radioddity, Xiegu G90 CAT and Digital modes V1.0](https://radioddity.s3.amazonaws.com/Radioddity%20-%20Xiegu%20G90,%20CAT%20and%20Digital%20modes%20V1.0_20210623.pdf) | `docs/civ-g90.md`; [g90-measurements.md](g90-measurements.md) |
| The RX filter `1A 05`, commands from firmware 1.8, the `26 01` bug, the COMM socket warning, no remote power-on | [Xiegu G90 User Manual V4.32](https://radioddity.s3.amazonaws.com/Xiegu_G90_User_Manual_V4.32_20240403.pdf) | `docs/civ-g90.md`; the hardware checklist in [install.md](install.md) |
| No TOT, SWR THR protection, menu items | [Xiegu G90 User Manual V4.32](https://radioddity.s3.amazonaws.com/Xiegu_G90_User_Manual_V4.32_20240403.pdf), [G90 cheat sheet V1.79b03](https://xiegu.eu/wp-content/uploads/sites/4/2024/08/XIEGU-G90-Cheat-Sheet-V1.79b03-102622.pdf) | [g90-measurements.md](g90-measurements.md); `docs/decisions.md` D23 |
| An example `26` frame (USB-D) | [dcasati/xiegu-g90](https://github.com/dcasati/xiegu-g90) | `docs/civ-g90.md` |
| Waterfall in the browser: binary WebSocket frames with a type byte, a stack of canvases with one line appended at a time, automatic levels from minimum and maximum, dragging and the wheel to pan and zoom — **ideas only (AGPL-3.0)** | [OpenWebRX](https://github.com/jketterl/openwebrx) | the waterfall |
| FFT: Nuttall/Blackman-Harris window, a fixed number of lines per second, DC removal before the FFT, zoom as a slice of the band taking the maximum of merged bins | [SDR++](https://github.com/AlexandreRouma/SDRPlusPlus) (GPL-3.0) | the waterfall |
| Receive processing: an adaptive predictive filter (LMS) as noise reduction and as an automatic notch; spectral subtraction with a noise-floor estimate — **ideas only; code written for this project, not copied** | [WDSP](https://github.com/TAPR/OpenHPSDR-wdsp) (GPL; Thetis, piHPSDR), [no-sdr](https://github.com/gbozo/no-sdr) (GPL-3.0-or-later), [OpenWebRX+](https://github.com/luarvique/openwebrx) (AGPL-3.0, only the idea and the warning against too strong NR) | `web/src/rxdsp.ts` |
| Logbook: a form filled in from CAT, a duplicate warning, "worked before" — **ideas only** | [Wavelog](https://wavelog.org/) / [Cloudlog](https://magicbug.co.uk/cloudlog/) (MIT) | `web/src/app.ts` (`StationLog`) |
| A logbook in SQLite, mapping modes and submodes — **ideas only** | [QLog](https://github.com/foldynl/QLog) (GPL-3.0) | `src/remoteg90ctl/server/logbook.py` |
| The FT8/FT4 encoder for transmitting — **a dependency**, `ft8lib==1.3.2` (a Python port of WSJT-X); one function, `chkcall`, copied with a correction (the call area digit as in WSJT-X's chkcall.f90) and put in its place | [ft8lib](https://pypi.org/project/ft8lib/) (GPL-3.0-or-later) | `src/remoteg90ctl/server/ft8_tx.py` (D53) |
| The sequence of QSO messages (Tx1–Tx6), the "Tx watchdog", transmitting in the opposite cycles — **ideas only** | [WSJT-X](https://wsjt.sourceforge.io/wsjtx.html) (GPL-3.0) | `web/src/ft8.ts` (`Sequencer`) |
| RTTY reception: a matched filter per tone, a threshold that follows each tone's strength separately (selective fading), unshift on space — **ideas only; code written for this project, not copied** | [fldigi](https://github.com/w1hkj/fldigi) (W1HKJ and others, GPL-3.0-or-later) | `web/src/rtty.ts` (D61) |
| CW reading: an envelope per signal, a threshold between the signal and the noise that follow each by itself, the speed tracked from the lengths of the elements — **ideas only; code written for this project, not copied** | [fldigi](https://github.com/w1hkj/fldigi) (W1HKJ and others, GPL-3.0-or-later) | `web/src/cw.ts` (D62) |
| RTTY framing: start bit checked in its middle, data bits sampled one bit apart, the stop bit must be mark — **ideas only** | [minimodem](https://github.com/kamalmostafa/minimodem) (Kamal Mostafa, GPL-3.0-or-later) | `web/src/rtty.ts` (D61) |
| The Baudot (ITA2) code with the US figures used by amateurs — **data** | ITU-T S.1 (ITA2); the US figures as fldigi and minimodem use them | `web/src/rtty.ts`, `src/remoteg90ctl/server/rtty_tx.py` (D61) |
| The Morse code and the usual prosigns — **data** | ITU-R M.1677-1 | `web/src/cw.ts` (D62) |
| IARU band plans (segments, usage, notes) — **data** | [IARU R1 HF band plan](https://www.iaru-r1.org/reference/band-plans) (from 16.10.2020), [IARU R2 band plan](https://www.iaru.org/wp-content/uploads/2020/10/IARU-Region-2-Band-plan.pdf) (September 2020), [IARU R3 R3-004 Rev. 2](https://iaru-r3.org/wp-content/uploads/2025/01/R3-004-Band-Plans-IARU-Region-3.pdf) (November 2024) | `src/remoteg90ctl/bandplan.py` (D54) |
| Amateur band allocations in the regions and the ITU region lines A, B, C — **data** | ITU Radio Regulations, article 5 and Nos. 5.3–5.9, footnote 5.96 (through [Wikipedia: Amateur radio frequency allocations](https://en.wikipedia.org/wiki/Amateur_radio_frequency_allocations), [ITU Region](https://en.wikipedia.org/wiki/ITU_Region), [BAKOM: RR footnotes](https://www.bakom.admin.ch/bakom/en/home/frequenzen-antennen/nationaler-frequenzzuweisungsplan/fussnoten-rr.html)) | `src/remoteg90ctl/bandplan.py` (D54) |
| The ADIF 3.1.5 format (fields, `MODE`/`SUBMODE`, FT4 as MFSK), the fields LoTW requires | [ADIF 3.1.5](https://adif.org/315/ADIF_315.htm), [LoTW: Submitting QSOs](https://lotw.arrl.org/lotw-help/developer-submit-qsos/) | `src/remoteg90ctl/server/logbook.py` |

## How "not copied" was checked

The code was written by an AI model, which was given the ideas above, not the
other projects' code. The model may still have seen these projects' public
code during its training, so on 12.11.2026 this repository's code
(`src/`, `web/src/`) was compared with the current code of the projects listed
above as ideas: UHRR, no-sdr, OpenWebRX, OpenWebRX+, WDSP, SDR++, Hamlib,
QLog, Wavelog and Cloudlog. The comparison looked for runs of 25 or more
tokens in common, ignoring comments, string contents and whitespace. Apart
from generic idioms (a loop over a sample buffer, a dictionary of strings),
the only finding is the FFT listed under "Possibly derived code". WSJT-X
(Fortran and C++) was not compared; only its QSO sequence is used, as an idea.

On 25.11.2026 the RTTY and CW code (`web/src/rtty.ts`, `web/src/cw.ts`,
`src/remoteg90ctl/server/rtty_tx.py`) was compared the same way with the current
code of fldigi (`src/`) and minimodem (`src/`). Runs of 25 tokens in common were
looked for as they are and also with every name replaced by a placeholder
(to catch code translated from C/C++ and renamed). There were no runs as they
are; with names replaced, the only runs were lists of name pairs separated by
commas (a generic idiom). The Baudot and Morse tables are data from the
standards listed above.
