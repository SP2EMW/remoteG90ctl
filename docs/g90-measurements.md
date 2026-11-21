# Xiegu G90: measured facts

What this project relies on about the G90, the DE-19 interface and the I/Q
sound card, with the item numbers the code refers to ("section 8 item 4").

**Scope:** one G90 (main unit and panel firmware **1.81**), one DE-19, one
StarTech.com ICUSBAUDIO2D USB sound card on the I/Q output (C-Media chip, USB ID
`0d8c:8810`; Linux names it "Speed Dragon USB Advanced Audio Device"), measured in November
2026 with a tinySA Basic and the radio's own display as references. Other
units, firmware versions and cabling may differ — check before you rely on a
value, above all on anything that can key the transmitter.

"Reported" values come from other projects and are hypotheses; they may serve
as configuration defaults or in the simulator (marked UNVERIFIED), never as
the basis of a safety path. Sources are listed in [third-party.md](third-party.md).

| No. | What | Reported by others | Measured here |
|---|---|---|---|
| 1 | Sample rate and resolution of the I/Q card | 48 kHz stereo | The card records stereo 16/24 bit at 8–48 kHz and 88.2/96 kHz. At 48 kHz the band is flat within ±2.3 dB over ±20 kHz and usable to about ±22 kHz (the card's filter). At 96 kHz it is flat within ±2 dB to ±30 kHz, −3.5 dB at ±40 kHz, about −9 dB at ±45 kHz: **the radio's I/Q output reaches about ±40 kHz**. The project uses 96 kHz. |
| 2 | Channel mapping and sign | `Z = L − jR` | **`Z = L − jR`** (`swap_channels = false`, `invert_q = true`), measured with a ground-loop isolator in the I/Q line: a signal 5 kHz above the dial appears at +5 kHz. Depends on the cabling. |
| 2a | I/Q centre relative to the CI-V frequency | constant signed offset | Depends on the mode. Relative to USB: LSB 0, **CW +800 Hz, CW-R −800 Hz** (the CW tone set in the menu), **AM and FM −100 Hz**. In USB the centre is about −22 Hz from the reference's nominal frequency (within the reference's own error). |
| 2b | Image rejection and channel balance | 30–40 dB uncorrected | 31–37 dB over ±23 kHz without correction (31.4 dB at +5 kHz, 33.8 dB at −5 kHz). |
| 2c | DC component | present at 0 Hz | Negligible (below −107 dBFS). |
| 3 | I/Q output while transmitting | carries the transmitted signal | It does **not** carry the transmitted signal. Unmodulated carrier into a dummy load: the level drops by about 10.5 dB (16 dB at 48 kHz), with clicks at RX→TX and TX→RX (up to +40 dB) and about 0.4 s to settle. Speech in SSB: a signal common to both channels (correlation 0.93), strongest below 100 Hz, plus speech at 100–3000 Hz — a symmetric spectrum around the dial. Detecting TX from I/Q proved unreliable. |
| 4 | PTT line of the DE-19; PTT over CI-V | conflicting sources; `1C 00 00/01` accepted by some firmware | **RTS keys PTT**; DTR does not. RTS high = transmit; RTS low or port closed = receive. PTT over CI-V is not used (decisions D23). |
| 5 | Power and SWR over CI-V | readable in firmware 1.81 | **Power meter `15 11`:** 0–255 (BCD, last digit unreliable); 0 in receive and with PTT but no modulation; 171 at 1 W, 241 at 2 W, **255 (end of scale) from about 3 W to 20 W** — real power above 2–3 W cannot be read over CI-V; use an external meter. **Power setting `14 0A`:** read and write work (plain BCD); the radio sets whole watts and rounds down (level 53 → 4 W, reads 40; 54 → 5 W, reads 53). **SWR `15 12`:** 100 at SWR 1.1, 220 at 1.5, **255 (end of scale) at 2.0** — usable only as a "high SWR" warning. |
| 6 | Stable device names | — | Port `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` — **the same name for every CH340** (no serial number), unique only without a second such adapter (hence the identity check, D32). Cards: `/dev/snd/by-id/usb-C-Media_Electronics_Inc._USB_Audio_Device-00` (DE-19) and `usb-Speed_Dragon_USB_Advanced_Audio_Device-00` (I/Q). ALSA card ids (`Device`, `Device_1`) are not stable. |
| 7 | CI-V speed | 19200 8N1 | 19200 works. |
| 7a | CI-V address | 0x88 or 0x70 | The radio answers from **0x70**. The line echoes our own frames; in the echo of 11-byte frames a byte is sometimes lost. |
| 7b | CI-V connection | the IO/User socket on the panel, not the rear COMM | The DE-19 connected as its manual shows works: the jack cable from the front panel's IO/User socket to the DE-19's CI-V socket, and the multi-pin DIN cable from the radio's rear ACC socket to the DE-19's ACC socket; no separate CAT cable. The manual warns that a plain USB-serial cable in COMM at power-on can put the radio into firmware update mode. |
| 7c | Radio identity | `19 00` or `1D 19` → 0x0090 | `19 00` → `19 70` (the CI-V address only); **`1D 19` → `1D 19 00 90` (G90)**, used by the guard (D32). |
| 8 | Keying pulse when the port is opened | known problem with the DE-19 and other interfaces | **Yes — every port open keys the radio for a fraction of a second** (the kernel raises RTS before the guard can lower it). Accepted risk; start the guard with the radio in SSB (D23). |
| 8a | DTR/RTS before the guard runs | — | No keying when plugging in the USB cable (several times) or when booting with the services disabled. Suspend: not measured. On Ubuntu brltty grabbed the port; it must be disabled for the device. |
| 8b | USB-serial chip | — | **CH340** `1a86:7523` behind a Genesys hub `05e3:0608`. The `ch341` driver reports the *requested*, not the physical, line state in `TIOCMGET`; the physical state was checked by watching the radio. |
| 9 | Hum from ground loops in the I/Q path | hum through the power supply and the CAT/USB cable | Laptop on battery, antenna connected: the **ground-loop isolator on the I/Q output was the source of the hum** (50/100/200 Hz about 30 dB above the noise with it, 0–7 dB without); unplugging the DE-19 USB made no difference. With mains-powered equipment the result may differ — not measured. |
| 10 | Firmware | 1.79–1.81 tested elsewhere; U-D/L-D from 1.79 | Main unit 1.81, panel 1.81. |
| 11 | Transmit time-out (TOT) | none in the manual or the menu | **The G90 has no TOT** (confirmed in the menu): no protection against a long transmission inside the radio. |
| 11a | High-SWR protection | SWR THR in the menu (default 3:1): above it the radio reduces power | Set to 2.5 here. |
| 12 | VOX state over CI-V | VOX switch `16 46` only on the X6100 | VOX off in the menu; reading it over CI-V not tested. VOX off is a hardware precondition. After a settings reset VOX is off (the default). |
| 13 | Antenna tuner and RIT/XIT | Icom: ATU `1C 01`, RIT/XIT `21` | ATU = **`1C 01`**: read returns `00`/`01`; `1C 01 00`/`01` switches off/on; **`1C 01 02` tunes** (a carrier for about 1–1.5 s with a matched antenna; **about 9 s with a poor match**, measured 13 November 2026 — the tuner found no match (SWR above 3.5 afterwards) and the radio's display did not say so; the power meter `15 11` shows the carrier during the whole tuning, at the end of its scale (above 2 W) **whatever the power setting** (1 W set), and returns to 0 at the end; the radio answers CI-V meanwhile, and a frequency change sent over CAT during a tuning was carried out only after the tuning ended — mode, power and VFO changes not checked); **tuning cannot be stopped** (`1C 01 00` during it is acknowledged and ignored). **With the screen blanked the G90 does not tune** (14 November 2026): it acknowledges `1C 01 02` and carries the tuning out only when the screen is switched on again — a carrier later, unknown to the guard (which ends the "tuning" after the meter reads zero, after about 3–4 s; `atu_tune_end` with `emitted: false` in the TX log), outside the duty cycle and on whatever frequency the radio is on then. RIT/XIT: unknown. |
| 14 | Remote power-on | no power command in CI-V | Not possible over CAT. With the power button held down permanently, the radio switches on by itself when DC is applied (encoders work, panel buttons do not). Not yet checked in that state: CAT, PTT, absence of a transmission at power-on. |
| 15 | CI-V traffic while the radio powers on | reports of the G90 hanging | Not measured. Until then, switch the radio on before starting the services. |
| 16 | Settings after a DC power loss | — | The radio loses its settings when DC is cut without switching it off first; VOX then comes back off (the default). |
| 17 | AF gain `14 01` and the DE-19 audio | read and write | Works (read, write); turning the speaker down **does not change** the level at the DE-19 sound card. There is no CI-V command to switch to headphones. |
