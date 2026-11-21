# Xiegu G90 CI-V commands — what the sources say

Collected on 31.10.2026 and updated with measurements since. A summary of the
published information about the G90's CI-V commands, sorted for the guard's
allowlist (`AGENTS.md` rule 3).

**Unless a row says it was measured, nothing here has been checked on a radio**
(`AGENTS.md` rule 10). This is a list of candidates and warnings, not a
specification. A command goes on the allowlist only with a source from this
file, a confirmation on the hardware, a test and the maintainer's decision.
Measurements are from one G90 with firmware 1.81
([g90-measurements.md](g90-measurements.md)).

## Sources

| Short | Document |
|---|---|
| **XREF** | Xiegu, *Xiegu Radio CI-V Reference* — [xiegu.eu](https://xiegu.eu/wp-content/uploads/sites/4/2023/08/XIEGU-RADIO-CI-V-REFERENCE.pdf) (one table for all Xiegu radios; rows without a radio named apply to every model, the G90 included). The extracted text was unreadable in places — see "Gaps in the sources". |
| **RDD** | Radioddity, *Xiegu G90, CAT and Digital modes V1.0* (2021-06-23) — [radioddity.s3.amazonaws.com](https://radioddity.s3.amazonaws.com/Radioddity%20-%20Xiegu%20G90,%20CAT%20and%20Digital%20modes%20V1.0_20210623.pdf) |
| **MAN** | *Xiegu G90 User Manual V4.32* (2024-04-03) — [radioddity.s3.amazonaws.com](https://radioddity.s3.amazonaws.com/Xiegu_G90_User_Manual_V4.32_20240403.pdf) |
| **EXT** | *Xiegu G90 Extended Manual* (Radioddity, CI-V tables for APP V1.80 / BASE V1.80b01, pp. 110–114) — [manualslib.com](https://www.manualslib.com/manual/4024788/Xiegu-G90.html?page=111#manual). Confirms the AGC codes (0 off, 1 fast, 2 slow, 3 auto), `16 02`, `16 22`, `16 50`, `14 12` (NB level); has `14 02` (RF gain); does **not** have `14 06` (NR), `16 44` (compressor), `1A 03` or the NB width |
| **FW180** | Firmware 1.80 notes — [xiegu.eu](https://xiegu.eu/2025/05/29/g90-firmware-v1-80-complete-digital-mode-audio-enhancements/) |
| **LAW** | cjlawson02/xiegu-g90-research, `docs/protocol/civ.md` (MIT, measured 27.07.2026, radio firmware not stated) — [github.com](https://github.com/cjlawson02/xiegu-g90-research/blob/main/docs/protocol/civ.md). Reviewed 08.11.2026: only the command groups, without sub-commands or formats; agrees with our measurements on the address 0x88/0x70, the attenuator `0C` and the reply `11 <state>` instead of `FB`, `14` levels not in plain BCD, and the filter byte in `06`. Nothing on the NB width, `1A`, the compressor or switching the ATU (`1C` blocked there). **Disagreement:** according to LAW (C-Q4) `25 01`/`26 01` return the **selected** VFO; our measurement (firmware 1.81) says otherwise — see `0x25` and `0x26` below |

## Port and addressing

| Parameter | Value | Source | Notes |
|---|---|---|---|
| Speed | 19200, 8N1 | RDD, MAN | 115200 is for firmware updates only |
| Radio address | **0x70** by default; the radio also answers other addresses, e.g. **0x88** | RDD | Explains the 0x70 / 0x88 conflict between sources ([measurements](g90-measurements.md), item 7a: the radio answers from 0x70) |
| CAT socket | the IO/User socket on the **panel** | RDD, MAN | According to MAN the main unit's rear COMM socket is for firmware updates only |
| Warning | switching the radio on with a plain USB-serial cable in the COMM socket can put it into firmware update mode and leave it unusable until firmware is loaded again | MAN p. 58 | Hardware checklist in [install.md](install.md) |
| Warning | software that polls the radio may be taken for a firmware update trigger; RDD recommends unplugging the cable after use | RDD | Not yet known whether this applies to the panel socket or only to COMM |

## Categories

- **A — read.** Does not change the radio's state. A candidate for the allowlist.
- **B — changes state without transmitting.** A candidate for the allowlist, but blocked during TX (like frequency and mode, `docs/decisions.md` D24 point 7), and commands that affect transmission (power, microphone gain, TX frequency) need value limits on the server side.
- **C — causes a transmission.** **Never through the allowlist.** If ever needed, only as a guard function with a lease (rule 3).
- **N — not supported** according to the sources.

The allowlist itself is in `src/remoteg90ctl/guard/framecheck.py`.

## Frequency, mode, VFO

| Command | Sub-command / data | Function | Cat. | Source | Notes |
|---|---|---|---|---|---|
| 0x00 | frequency, BCD | set the active VFO's frequency | B | XREF, RDD | On Icom radios also the "transceive" broadcast without acknowledgement. Treated in the code as a frame coming from the radio |
| 0x01 | mode code | set the mode | B | XREF, RDD | As above |
| 0x02 | — | read the band edges (table 2-3) | A | XREF, RDD | |
| 0x03 | — | read the frequency | A | XREF, RDD | **On the allowlist** |
| 0x04 | — | read the mode | A | XREF, RDD, measured | **On the allowlist.** **Measured (02.11.2026, firmware 1.81):** reply `<mode> <filter>`, e.g. `01 01` (USB, filter 1); **U-D gives the same reply as USB** — the data mode shows only through 0x26 or 0x1A 0x06 |
| 0x05 | frequency, BCD | set the frequency | B | XREF, RDD | **On the allowlist.** The `FB` reply is confirmed by AetherSDR RFC #5972 |
| 0x06 | mode code + filter byte | set the mode | B | XREF, RDD, measured | **On the allowlist.** XREF does not describe the filter byte for the G90. **Measured (02.11.2026, firmware 1.81):** without the filter byte (`06 00`, `06 01`) the radio answers NG; the server sends the filter read before through 0x04. **0x06 does not change the data mode:** a radio in U-D stays in U-D after `06 01 01` (USB) and goes to L-D after `06 00 01` (LSB) — the data flag carries over to the new mode |
| 0x07 | 0x00 VFO-A, 0x01 VFO-B, 0xB0 swap A/B | VFO selection | B | XREF, RDD | Changes the transmit frequency. **Measured 08.11.2026 (firmware 1.81):** `07 00`/`07 01` select A/B (OK), `07 B0` swaps A and B; each VFO keeps its own mode, so switching also changes the mode. **On the allowlist since 08.11.2026: only `07 00` and `07 01`**, blocked during TX (the swap is not allowed) |
| 0x0F | 0x00 off, 0x01 on | split | B | XREF, RDD | Changes the transmit frequency — blocked during TX. **Measured 08.11.2026 (firmware 1.81):** reading `0F` → `00`/`01` matches the SPLIT indicator; writing `0F 00`/`0F 01` works (OK). **In split the radio transmits on the unselected VFO, in that VFO's mode** (PTT from the microphone: A = U-D, B = USB → transmits in USB). **On the allowlist since 08.11.2026** (read; write blocked during TX). Before every transmission the server reads split and, in split, takes the frequency (`25 01`) and mode (`26 01`) of the transmit VFO. **ATU tuning (Tune) in split happens on the selected (receive) VFO**, not the transmit VFO (measured 08.11.2026) — the TX log records the selected VFO's frequency for `atu_tune` |
| 0x25 | — | frequency of the selected / unselected VFO | A/B | FW180, MAN | From firmware 1.8. **Measured 08.11.2026 (firmware 1.81):** `25 00` → selected VFO, `25 01` → unselected, reply `25 <x> <5 bytes BCD>`; **the byte `<x>` in the reply does not repeat the question but says which VFO is selected** (`00` A, `01` B). **On the allowlist since 08.11.2026: read only.** LAW (C-Q4) says `25 01` gives the selected VFO — here, with A selected, `25 01` gave B's frequency (21.145 with A = 21.141) and the other way round, so the data is the unselected VFO's; LAW most likely took the `00` byte in the reply for the VFO number. Checking the transmit VFO in split depends on this (`docs/server-protocol.md`, "Transmit lease") — measure again after a firmware change |
| 0x26 | VFO, mode, data, filter; e.g. `26 00 00 01 01` = selected VFO, LSB, data on, filter 1 | mode with data mode | A/B | XREF, FW180, MAN; example `FE FE 70 E0 26 00 01 01 FD` (USB-D) — [dcasati/xiegu-g90](https://github.com/dcasati/xiegu-g90) | From firmware 1.8. **Known bug (MAN):** for the unselected VFO it returns USB/LSB instead of USB-D/LSB-D; for the selected one it works. **Measured 07.11.2026 (firmware 1.81):** reading `26 00` → `26 00 <mode> <data> <filter>` (L-D = `00 01 01`, U-D = `01 01 01`); setting with 3 bytes works (USB ↔ U-D, LSB ↔ L-D); without the filter byte — NG. **On the allowlist since 07.11.2026** (read, and write with exactly 3 bytes). **`26 01` (unselected VFO), measured 08.11.2026:** in firmware 1.81 the data flag is reported correctly (B in USB `01 00 01`, in U-D `01 01 01`) — the bug described in MAN does not occur; on the allowlist, read only |

**Mode codes (XREF table 2-2):** 0x00 LSB, 0x01 USB, 0x02 AM, 0x03 CW, 0x05 NFM, 0x07 CWR. They match `src/remoteg90ctl/civ/commands.py`.

**Frequency format (XREF table 2-1):** 5 bytes BCD, least significant digits first: 10 Hz/1 Hz, 1 kHz/100 Hz, 100 kHz/10 kHz, 10 MHz/1 MHz, 1 GHz/100 MHz.

## Levels — 0x14

BCD values 0000–0255 stand for 0–100 % (XREF). **The G90 (firmware 1.81) garbles the last digit:** hundreds and tens are BCD, but the low nibble of the last byte carries the low 4 bits of the whole number (171 arrives as `01 7B`, 255 as `02 5F`); decoding: `g90_level_from_bytes` (the idea comes from Hamlib). The `0x15` meters do the same.

| Sub-command | Function | Read | Write | Write cat. | Notes |
|---|---|---|---|---|---|
| 0x01 | AF (volume) | yes | yes | B | |
| 0x02 | RF gain | yes (EXT) | yes (EXT) | B | Not in the XREF extract; in the extended manual (firmware 1.80). **On the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): radio 1–100 % ↔ 0–255; a plain BCD write is truncated to a whole percent (0 → 1 %, 126 → 49 %, 127 → 50 %, 255 → 100 %), the read is ⌊(p − 1) · 255/99⌋ (50 % → 126), as on the radio. The server writes ⌈(p − 1) · 255/99⌉ |
| 0x03 | SQL | yes (RDD) | yes (RDD) | B | Not in the XREF extract for the G90. **Left out** (maintainer's decision, 07.11.2026): of no use on HF |
| 0x06 | NR | yes | — | — | **The G90 has no NR** (neither in the extended manual nor in the menu). Measured 07.11.2026: `14 06` answers `0000`, a write answers OK, but nothing changes — not added |
| 0x09 | CW sidetone | yes | yes | B | |
| 0x0A | TX power | yes | yes | B | **Affects transmission** — limited on the server side. **Read (no data) on the allowlist since 03.11.2026.** **Write (2 data bytes) on the allowlist since 03.11.2026** (D44): the guard does not read the value, the server checks the `safety.max_rf_power_w` limit, the write is refused during TX. **Measured 04.11.2026:** a plain BCD write works; the radio sets whole watts and rounds down (write 53 → 4 W, read 40; write 54 → 5 W, read 53), so the server writes the lowest level for the requested watts and compares watts, not levels |
| 0x0B | microphone gain | yes | yes | B | **Affects transmission** (overdrive, splatter). Measured 08.11.2026 (firmware 1.81): menu MIC GAIN 0–20 ↔ 0–255, read in G90 BCD = ⌊step · 12.75⌋ (0, 63, 127, 255 at 0, 5, 10, 20); a plain BCD write is truncated to a whole step (127 → 9, 128 → 10, 255 → 20). **Does not affect the sound from the DE-19 in U-D/L-D** (transmitting from the browser: ALC and power unchanged at 0 and 20) — only the radio's own microphone. **Not implemented** (maintainer's decision, 08.11.2026): transmit gain, filters and compression are done in the browser (stage 8) |
| 0x0C | CW speed | yes | yes | B | |
| 0x0F | QSK time | yes | yes | B | |
| 0x12 | NB | yes | yes | B | **NB level (not width) on the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): menu 0–10 ↔ 0–255; a plain BCD write is truncated to a whole step (25 → 0, 26 → 1, 128 → 5, 255 → 10), the read in G90 BCD = ⌊step · 25.5⌋ (25, 127, 178, 255), as in the menu. The server writes ⌈step · 25.5⌉ and rounds the read to a step. **The NB width (menu 0–10) is not available over CAT** — not in the sources |
| 0x15 | MONI | yes | — | — | |
| 0x16 | VOX gain | — | yes | B | About VOX — see the warning below |
| 0x17 | ANTI-VOX | yes | yes | B | |
| 0x19 | backlight | yes | — | — | |

## Meters — 0x15 (read, cat. A)

| Sub-command | Meter | Notes |
|---|---|---|
| 0x01 | squelch (00 closed, 01 open) | |
| 0x02 | S-meter | **Read on the allowlist since 07.11.2026 (stage 5).** Level 0–255 in G90 BCD (`01 0A` = 106 shows the garbled digit). Measured (firmware 1.81, tinySA, RF gain to move the reading): S1 22, S3 44, about S5 73, S7 106, S9 136, S9+20 160, S9+40 174 — non-linear, denser above S9; **below the scale (S0) a constant 242**, also without an antenna (probably −14 as an unsigned byte). **While transmitting the S-meter gives a constant 242** (measured 08.11.2026, U-D, PTT from the microphone without modulation, antenna connected: 10 of 10 reads; without PTT the antenna noise reads S5, level 66–77) — the same code as "below the scale" in receive, so on its own it does not tell a transmission from a very weak signal or a low RF gain. Conversion: `server/smeter.py` |
| 0x11 | output power | **On the allowlist since 03.11.2026** (read only, no data), to detect transmissions and show the power (D33). Measured (firmware 1.81, FM into a dummy load): level 0 in receive and with PTT but no modulation, 171 at 1 W, 241 at 2 W, **255 (end of scale) from about 3 W to 20 W** — real power only up to about 2–3 W |
| 0x12 | SWR | **Read on the allowlist since 03.11.2026** (read only). Level 0–255 in G90 BCD. Measured (firmware 1.81, FM 5 W, magnetic loop): 100 at SWR 1.1, 220 at 1.5, 255 (end of scale) at 2.0 — Icom's scale (Hamlib) would read far too high. Used only as a "high SWR" warning at the end of the scale. A candidate safety measure: release TX on high SWR — needs the maintainer's decision |
| 0x13 | ALC | **Read on the allowlist since 08.11.2026 (stage 5), also during TX.** Level 0–255 in G90 BCD; 0 in receive and in silence. Measured (firmware 1.81, U-D, transmitting from the browser, a steady "ooo" for a few seconds; radio display → level): 5–6 → 7, 30 → 40, 82 → 95, 82 → 99 (loudest; the display ends at about 82, ALC limits the signal). Earlier comparisons with ordinary speech were unreliable (reads at different moments), and the assumption "255 = ALC 100" proved wrong. The server converts the level to the number on the display (interpolating between the points, `server/alc.py`); it reads ALC only while transmitting (ours, or one detected by the power meter) and shows it in the S-meter's place |
| 0x15 | supply voltage | **Does not work on the G90** (firmware 1.81, measured 08.11.2026): always `02 5F` (255) — at 11.9 V in receive and 11.4–11.5 V while transmitting. Not implemented |

## Switches — 0x16

| Sub-command | Function | Values | Write cat. | Notes |
|---|---|---|---|---|
| 0x02 | preamplifier | 00 off, 01/02 on | B | **On the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): reading `16 02 00/01` matches the icon and the panel button, a write answers OK; `02` is accepted but sets `01` (one stage); switching it on switches the attenuator off |
| 0x12 | AGC | 00 off, 01 fast, 02 medium, 03 slow (Icom) | B | **On the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): **00 off, 01 F (fast), 02 S (slow), 03 A (auto)** — a different order from Icom's; a write answers OK and the read agrees; `04` also answers OK (the read does not change), so the server sends only 00–03 |
| 0x22 | NB | 00/01 | B | **On the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): a write answers OK and the read agrees |
| 0x44 | compressor | 00/01 | B | Affects the transmitted signal. **Does not work on the G90** (firmware 1.81, measured 08.11.2026): the read is always `00`, with the compressor on or off; writing `01`/`00` does not change the radio. Not implemented — the compressor is in the browser (stage 8) |
| 0x50 | dial lock | 00/01 | B | **Left out** (maintainer's decision, 08.11.2026): not needed when operating remotely |
| 0x40, 0x41, 0x46 | NR, DNF, **VOX** | — | N | According to XREF only on the X6100. **The G90's VOX state probably cannot be read** — to be checked |

## Filters and data mode — 0x1A

| Sub-command | Function | Data | Write cat. | Source |
|---|---|---|---|---|
| 0x03 | IF filter width | — | B | XREF; the value table is in EXT (Table 5: SSB/CW 0–9 = 50–500 Hz in steps of 50, 10–40 = 600–3600 Hz in steps of 100), EXT does not list the command itself. **Measured 07.11.2026 (firmware 1.81, U-D): writes → NG (read only).** The read is not the width: B3000 → 40, B2300 → 33 or 36 (depending on where the filter sits in the passband; the difference is audible), B1650 → 26. All points fit the hypothesis that it is the filter's **upper edge** per Table 5 (40 = 3600, 33 = 2900, 36 = 3200, 26 = 2200; lower = upper − B) — unconfirmed, the radio does not show the edges. **Not implemented** (maintainer's decision, 07.11.2026); the passband on the waterfall stays set by the sliders |
| 0x05 | RX filter (menu item `00 10`) | two bytes: HPF 01–20 (100–2000 Hz), LPF 05–24 (500–2400 Hz); HPF < LPF; example `FE FE 70 1A 05 00 10 <HPF> <LPF> FD` | B (write), A (read) | MAN p. 38. **Measured 02.11.2026, firmware 1.81: reading `1A 05 00 10` without a value → NG** — **writing also → NG** (BCD and binary, `04 05` included) — firmware 1.81 does not support this item; it is not on the allowlist (D37). The CAT range (LPF up to 2400 Hz) is narrower than the DSP filters in the menu (SSB 300–3000 Hz by default, two sets BPF1/BPF2 per mode, MAN p. 38) |
| 0x06 | data mode and filter group | the G90 answers with two bytes: data byte 1 = data mode, data byte 2 always 0 | B | XREF note 3 |

## Other

| Command | Function | Cat. | Source | Notes |
|---|---|---|---|---|
| 0x11 | attenuator: read (no data) / toggle (any data) | A / B | RDD, measured | **On the allowlist since 07.11.2026 (stage 5).** Measured (firmware 1.81): state `00` off, **`0C` = 12 dB (binary, not Icom's BCD)**; **every `11` with data toggles the attenuator** (`11 00` while it is off switches it on — found on the page on 07.11.2026), **the radio answers with a frame `11 <new state>`, not OK**; the server sends `11` only when the state has to change; switching it on switches the preamplifier off (the panel button cycles off → PRE → ATT) |
| 0x19 0x00 | read the radio ID | A | XREF, measured | XREF: G90/G90S **0x0090** (table 2-7). **Measured (02.11.2026, firmware 1.81):** reply `19 70` — the radio's CI-V address, without repeating the sub-command, as on Icom radios; it does not identify the model. Not used |
| 0x1D 0x19 | read the Xiegu radio ID | A | XREF, FW180, measured | From firmware 1.8. **Measured (02.11.2026, firmware 1.81):** reply `1D 19 00 90` (G90). **Sent by the guard itself** after every port open (D32); not on the allowlist for the server |
| RIT/XIT | set/read RIT, RIT on/off, XIT on/off | B | XREF | The command header is unreadable in the extract; on Icom radios 0x21. **XIT changes the transmit frequency** — to be blocked during TX |

## Commands that transmit (cat. C — never through the allowlist)

| Command | Function | Source | Notes |
|---|---|---|---|
| 0x1C 0x00 + 0x00 / 0x01 | PTT release / press | XREF, RDD | The radio answers with an ACK, not the state. Works only with VOX off (RDD). The `ptt_line = "civ"` option is blocked in the code (D24 point 4) |
| 0x1C 0x00 (no data) | read the TX state (0x00 = RX, other = TX) | XREF note 4 | A read in itself (cat. A), but it shares its header with PTT. **Not supported in firmware 1.79b03 and older** — probably the origin of "the G90 ignores PTT over CI-V". A candidate for confirming that the radio really went to TX after the line was raised — only with exactly 0 data bytes required, after checking on the hardware |
| ATU: 0x00 off, 0x01 on, **0x02 start tuning** | antenna tuner | XREF, RDD | The header is unreadable in the extract; on Icom radios 0x1C 0x01. **Tuning transmits a carrier.** The guard does not interpret data, so it cannot tell "on/off" from "tune" by the command and sub-command alone — the whole 0x1C family stays off the allowlist. **03.11.2026 (D43):** measured — `1C 01` as on Icom radios; read, on and off work (on the allowlist as exact frames), `1C 01 02` tunes, usually for 1–1.5 s with a matched antenna (longer with a poor match; the longest time is not known), and cannot be stopped. Tuning only as the guard function `atu_tune`: it lasts for the guard until the power meter `15 11` reads zero twice in a row (at least 3 s; about 9 s measured with a poor match on 2026-11-13), at most 30 s before transmit is locked; the real time counts towards the duty cycle, the next tuning comes 10 s after the end of the previous one, and each is in the TX log (D43). **08.11.2026 (from the server log, firmware 1.81): `1C 01 00` and `1C 01 01` TOGGLE the tuner** — with the tuner off `1C 01 00` switched it on, with it on `1C 01 01` switched it off (like the attenuator `11`). The server reads the state and sends a command only when it differs, then reads it back (`atu_mismatch`) |

## Not supported

| Command | Function | Source |
|---|---|---|
| 0x18 | power on/off | Not in XREF or MAN. According to MAN the radio switches on only with the panel button (short press) and off with a longer press. Switching it on remotely would need a hardware solution ([measurements](g90-measurements.md), item 14) |

## VOX — a way around the guard

RDD: with PTT from a computer VOX must be **off**; when keying by sound from the sound card (digital modes) — on.

If the G90's VOX is on, **any sound at the radio's audio input starts a transmission, bypassing the guard, the lease and the 180 s limit**. According to XREF the VOX switch over CI-V exists only on the X6100, so the program probably cannot check its state on the G90. Hence the item in the hardware checklist ([install.md](install.md)).

## Gaps in the sources (to be filled by measurement or a better source)

1. The command headers of RIT/XIT — the XREF extract is unreadable (ATU: measured, `1C 01`).
2. The full "Data Mode & Filter Group Tips" table for 0x26 / 0x1A 0x06.
3. The meaning of the filter byte in 0x06 on the G90.
4. Whether the G90 answers 0x16 0x46 (VOX state).
5. Which commands work on other firmware versions than 1.81.
6. The full command tables from the manuals (MAN pp. 49–51 and the G90 extended manual, pp. 110–115) — not available in a readable form.

## Note: USB/LSB on the page = U-D/L-D on the radio (maintainer's decision, 08.11.2026)

With `radio.ssb_data_mode = true`, choosing **USB** on the page sets the radio to **U-D**, and **LSB** to **L-D** — only in the data modes does the G90 take its audio from the DE-19 (not from the microphone). This applies to **both VFOs**: switching the VFO from the page (`set_vfo`) turns the new VFO's USB/LSB into U-D/L-D, and switching split on from the page (`set_split`) does the same to the transmit VFO (the server selects it for a moment and goes back). The mode can still be changed with the radio's knob, bypassing the program, so this is not a safety measure: transmitting from the browser depends on reading the transmit VFO's mode before every `tx_request` (`docs/server-protocol.md`, "Transmit lease").
