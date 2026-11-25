# Design decisions

This file explains **why** remoteG90ctl is built the way [AGENTS.md](../AGENTS.md)
describes. When a situation is not covered by any rule, follow the reasoning
here rather than only the letter of the rules. When a rule and its reasoning
seem to contradict each other, ask the maintainer.

Decisions keep the numbers they had during development, because the code
refers to them (`D24.7`, `D43` …). Numbers that are missing were about project
organisation and are not relevant to users or contributors.

Facts about the radio quoted here were measured on one G90 with firmware 1.81
([g90-measurements.md](g90-measurements.md)).

---

## D8. The PTT guard is a separate process

The safety-critical transmit logic lives in a small separate process, the
**guard**, not in the server. If the server — with audio, numpy, networking and
the UI — hangs or crashes, the guard still releases PTT. What matters is the
**isolation**, not the language: the guard is Python too, but with the standard
library and `pyserial` only (rule 11). No safety path in the guard waits for an
answer from the server; its code is short and fully tested.

## D10. The guard owns the whole serial port

The DE-19 carries CI-V and the PTT line on the same serial port, and only one
process can own the port. Since PTT belongs to the guard, the guard owns the
whole port and the server sends its CI-V frames through it. For the same
reason the project uses its own CI-V layer instead of Hamlib: Hamlib would
have to own the port itself.

## D11. CI-V allowlist, deny by default

The guard forwards only explicitly allowed CI-V commands, judged by addresses,
command and sub-command; it never interprets the data. Blocking "transmit
commands" (a denylist) would need complete knowledge of the G90's commands,
which nobody has; an allowlist is safe with incomplete knowledge. Not
interpreting the data keeps the guard simple. Every new command is a deliberate
allowlist change, with a test.

Gluing a second, forbidden frame behind an allowed one must not work, which is
why a message must be **exactly one valid frame** (rule 3), tested with random
data (`hypothesis`).

## D12. Lease timing: 0.5 s / 2 s / 180 s

The client renews every 0.5 s; 2 s without a renewal releases PTT; a single
transmission ends after 180 s and leaves a lockout that needs an explicit
unlock. Two seconds react quickly to a failure but survive a short network
hiccup over a VPN. The hard limit protects the radio (20 W, it gets hot) and
prevents a "forgotten" transmission; the lockout forces a conscious decision.
The configuration may shorten these values, never lengthen them.

## D13. The lease chain reaches the end client

The lease is renewed by the end client (browser, CLI) → server → guard. The
server never renews by itself. A closed browser tab, a sleeping phone or a
dropped VPN must end the transmission within 2 s; a server renewing "on behalf
of" its client would keep transmitting. Clients never talk to the guard
directly.

## D14. Devices by stable identifiers

With two USB sound cards the numbering can swap after a reboot. Transmit audio
on the wrong card, or the microphone taken from the I/Q input, is a safety
problem, not just an inconvenience. Devices are therefore named by
`/dev/serial/by-id/...` and stable card names, and a missing or mismatching
device blocks transmitting.

## D15. Simulator first; a double gate for transmitting on hardware

Development and tests run against the simulator; tests never touch a radio.
Hardware needs `radio.backend = "hardware"`; transmitting on hardware
additionally needs `safety.allow_transmit = true`; the CLI transmit test on
hardware needs a flag confirming either a dummy load or, with the limits of
D56 (SSB, at most 1 W), the antenna.

## D20. Our own core, with knowledge and patterns from others

The guard and the server are written for this project. A review of existing
projects found none that combined browser control of the G90, two-way audio, an
I/Q waterfall and transmit protection; none had a PTT guard with a lease and
time limits. Facts measured by other projects are taken as hypotheses
([g90-measurements.md](g90-measurements.md)), and patterns or GPL-compatible
code are reused where they help ([third-party.md](third-party.md)) — never for
the transmit paths.

## D23. No hardware PTT timer; the G90 has no TOT

There is no independent hardware time limit on the PTT line. The G90 has no
transmit time-out (TOT) either: neither the manual nor the menu has one. All
protection against a long transmission is therefore software, with a common
weak point in the USB adapter and the kernel. Rules 12–14 and their tests are
the last line of defence and must not be simplified.

Consequences:
- **PTT over CI-V is not acceptable as the main keying method.** With CI-V the
  radio keeps transmitting until a release command arrives; a dead guard or a
  lost USB link would leave it transmitting forever. Keying a line instead
  means that closing the port (HUPCL, rule 2) drops PTT.
- **Residual risk:** if the guard hangs inside the USB driver in the kernel
  (uninterruptible state), the watchdog cannot kill it, the descriptor stays
  open and HUPCL does not act. Software cannot remove this risk.
- **Port-open pulse (measured):** every open of the port keys the G90 for a
  fraction of a second — the kernel raises RTS in `open()` and the guard can
  lower it only about 1 ms later. Plugging in the cable and booting the
  computer do not key the radio. The risk is accepted: the pulse happens only
  when the guard starts or the port is reopened; start the guard with the
  radio in SSB, on a dummy load or an antenna.

## D24. Guard behaviour in detail

1. **Port loss:** the guard does not reopen the port by itself; only an
   explicit `port_open` does, after which the "port lost" block ends. The first
   open at service start is automatic.
2. **No port at start:** the guard starts (`READY=1`) with TX blocked and a
   reason; otherwise systemd would restart it in a loop.
3. **Simulator:** transmissions are only simulated, whatever `allow_transmit` says.
4. **`ptt_line = "civ"`:** starts with TX blocked (D23).
5. **One source at a time:** a second `tx_request` during a lease is refused,
   also from the same client. A second server connection to the guard is
   refused and logged.
6. **Server ↔ guard connection lost:** PTT is released immediately.
7. **Changing frequency, mode and other radio state during TX is refused**
   (reads are allowed): a band change while transmitting could take the
   signal out of the band and stress the final stage.
8. **Unclean restart during TX** (crash, watchdog kill with an active lease) →
   TX locked until `tx_unlock`. This needs a durable "TX active" record before
   PTT is raised.
9. **TX log:** if the start-of-transmission entry cannot be written, the
   transmission is refused.
10. **Line mismatch after raising PTT** (the read-back does not confirm the
    requested state) → lines lowered, lease ended, treated as a lost port.
11. **`tx_unlock` and `port_open`** need the `transmit` permission.
12. **Network barrier:** there is no `auth.enabled`-style switch; such a
    switch would be a loophole. Only the literal `127.0.0.1` and `[::1]` are
    accepted as listen addresses.

## D25. Hardening after a code review

1. **Another process could key the radio**, because the kernel raises DTR/RTS
   on every open of the tty and pyserial's `exclusive` is only an advisory
   `flock`. Three layers: `TIOCEXCL` after open (blocks others, except root);
   the lines are checked on every loop pass also without a transmission — an
   active line without a lease is lowered at once on the still **open** port
   (closing it would not drop the line while another process holds the
   device), the state becomes `line_fault`, the TX log gets an entry and TX is
   blocked until `port_open`; and the install guide disables ModemManager and
   brltty for the device.
3. **A stop signal while keying:** after PTT has been raised and confirmed
   the guard checks again whether it is stopping and releases at once.
4. **Narrow allowlist entries:** setting the frequency only with exactly 5 data bytes.
5. **A lockout that cannot be saved** is written into the run marker
   (`running.json`, `pending_lockout`), which survives even a clean shutdown,
   so the next start is locked.
6. **CAT:** after a reply time-out, late frames are discarded for a moment, so
   they are not taken as the answer to the next question.

## D26. VOX and the antenna tuner

- **VOX off is a hardware precondition.** With VOX on, sound at the radio's
  audio input becomes a transmission that bypasses the guard, and the G90
  probably cannot report its VOX state over CI-V.
- **The server sends no sound to the radio outside an active lease** — no
  silence, no test tones, no monitoring. Without sound, an accidentally
  enabled VOX cannot key the radio. The playback stream opens with the lease
  and closes with it.
- **Antenna tuning is transmitting** (it sends a carrier), and so is PTT over
  CI-V (`1C 00`); see D43 for the only way tuning is possible.
- [civ-g90.md](civ-g90.md) is the starting point for every allowlist change:
  categories A (read), B (changes state without transmitting), C (transmits)
  and N (not supported). Category C never goes on the allowlist.

## D29. FT8/FT4 built in instead of WSJT-X

The goal is operating from a browser, also from a phone and remotely. WSJT-X
on the server needs a desktop and a remote desktop, and it keys PTT by itself,
which does not fit the lease. Built-in FT8/FT4 gives one interface, and the
server knows exactly when each transmit window starts and ends, so the lease
fits naturally. Cost: QSO sequencing, time keeping and the log are our code.
External digital-mode programs get no access to the radio through the server.

## D30. Own station log; LoTW through TQSL

The log is part of the server: SQLite, ADIF import and export, working without
internet. Signing for LoTW is not reimplemented: ARRL's TQSL will run as a
separate program in a separate service with network access and without access
to the radio; the certificate and its password are available only to that
service. Uploads happen only on the operator's request. The log has no path to
transmitting.

The part about a separate upload service is replaced by D59: TQSL runs on the
operator's computer, not on the server.

## D31. Duty cycle in the guard

At most **300 s of transmission in any sliding 600 s window**. The 180 s limit
applies to one transmission, so releasing and immediately requesting again
would bypass it, while the reason for the limit — the radio heating up — is
about the total. With FT8 every window is a separate lease; without a window
limit, 180 s would never trigger. A limit in the server alone would not do:
the guard is the last line of defence (D8).

- When the limit is reached during a transmission, it ends at once
  (`duty_limit`); new requests are refused until the window has room. The
  block clears by itself (the radio cools down); no unlock is needed.
- The history is persistent and atomic like the lockout. An unreadable file
  means a **full window** for 600 s.
- If saving the history fails, new transmissions and tunings are refused until
  a save succeeds, and the file is emptied — an empty file is unreadable, so a
  restart in that state also sees a full window, rather than an old file
  without the latest transmissions.

## D32. Radio identity check at port open

After every safe port open the guard asks the radio for its identity (`1D 19`;
the G90 answers `1D 19 00 90`) and compares the raw reply with
`radio.identity` from the configuration, which has no default. Until it
matches, transmitting is blocked; CAT keeps working.

Why: the DE-19's `/dev/serial/by-id` name is the same for every CH340 adapter
(it has no serial number). A second such adapter could take the name, and the
guard would key RTS on someone else's device. `19 00` is not enough: it only
returns the CI-V address.

## D33. Detecting transmissions the guard did not key (informational)

The guard only knows about transmissions it keyed. The microphone's PTT, the
TUNE button and VOX key the radio without it. The server reads the radio's
power meter (`15 11`) every second and reports power without a lease from this
server as `tx_without_lease` with a red bar in the browser. A first attempt
that detected transmissions from the I/Q level was withdrawn: band changes and
other level changes caused false alarms, and false alarms teach people to
ignore warnings. The power meter shows real emission (zero with PTT and no
modulation), which is the quantity that matters. This is information only, not
a safety path (D8).

The power meter cannot see a transmission without output (PTT from the
microphone in U-D/L-D, or SSB without speech). While transmitting, the G90's
S-meter reads a constant 242, its below-scale value. When the S-meter falls
from at least S1 to 242 for 1 s while this server is not transmitting, the
browser shows a yellow "radio may be transmitting" bar
(`radio_tx_suspected`). This is information only, too: a disconnected
antenna or RF gain turned right down gives the same reading.

## D34. The web server accepts only its own origin

Every HTTP request and WebSocket must carry our own `Host`, and a WebSocket
(and every non-GET request) our own `Origin`. A browser lets any open page
connect to a WebSocket on `127.0.0.1`, and WebSockets are not covered by the
same-origin policy; without this check a page in another tab could retune the
radio or try to transmit. Checking `Host` also defeats DNS rebinding.
Authentication complements this check, it does not replace it.

## D35. The I/Q card through ALSA, opened only by the server

The server opens the I/Q sound card directly through ALSA by a stable name set
by a udev rule, never by number, and the card is excluded from PipeWire by a
WirePlumber rule. One owner per device, as with the guard and the port: two
processes fighting over a card give dropouts and unpredictable state.

## D36. TypeScript frontend, compiled JavaScript committed

The frontend is TypeScript without a framework, drawing on canvases. The
compiled JavaScript is committed, so the machine next to the radio needs no
Node.js or build tools; CI checks that it matches the source. `typescript` is a
development dependency only.

## D37. Radio menu items on the allowlist

For the menu command `1A 05` the two item bytes count as part of the
sub-command; the allowlist names items, separately for reading and writing,
and values are not interpreted. Allowing the whole `1A 05` by length would
allow reading and writing any item, including ones whose effect is unknown.
Items that could cause a transmission or disable a protection (VOX, tuning,
SWR threshold, PTT settings) are never writable, and the menu is never scanned
blindly. On the G90 with firmware 1.81 the one item tried (RX filter `00 10`)
is refused by the radio, so no item is currently on the list.

## D38. Remote access through a loopback HTTPS proxy

The server listens on loopback only (rule 15). For remote access a proxy such
as `tailscale serve` publishes the page over HTTPS inside the operator's
private network and forwards to `127.0.0.1`. Host names listed in
`server.allowed_hosts` are accepted only with an `https://` origin. Listening
on `0.0.0.0` was rejected: it would expose the page on every network the
machine joins. Browsers need HTTPS anyway to use the microphone.

## D39. Audio processing in the browser

Filters, notch, noise reduction and the transmit compressor and equaliser run
in the browser: they cost nothing on the small machine next to the radio, each
operator has their own settings, and the G90 does not expose its DSP filters
over CAT. Browser processing is never a safety measure: the server applies its
own level limiter and "audio only during a lease" whatever the client did.

## D40. Waterfall speed for slow links

The waterfall is almost all of the traffic (about 170 kbit/s at 1024 points and
20 lines per second). The browser can choose fewer lines per second, or switch
the waterfall off; the server merges lines by taking the **maximum** of each
point, so a short signal between sent lines is not lost. The spectrum is
computed only while a browser watches it.

## D43. Remote antenna tuning as a guard function

Remote tuning is essential for a remote station, so it is allowed — but only
as the guard function `atu_tune`, never as a forwarded frame. The allowlist
contains only the exact frames for reading the tuner state and switching it
off and on (`1C 01`, `1C 01 00`, `1C 01 01`); `1C 01 02` (start tuning) and
the rest of the `1C` family, including PTT `1C 00`, are rejected.

Tuning cannot be interrupted: on the G90 it lasts about 1–1.5 s with a matched
antenna and about 9 s with a poor one (below), and a stop command during it is
acknowledged and ignored. The guard can therefore only prevent it from
starting. Conditions: its own switch `safety.allow_atu_tune` (independent of
`allow_transmit`), a confirmed radio identity, a safe port, no lockout and no
active lease; the TX log entry is written before the frame is sent. How long a
tuning holds everything else and counts towards the duty cycle is set by the
power meter (next paragraph); the first design, before that measurement,
assumed a fixed 3 s. Browsers need the `tune`
permission, ask for confirmation, and are subject to the band check (D54).


**Tuning until the meter reads zero (13 November 2026).** Measured: with a
poorly matched antenna a tuning lasted about 9 s, found no match (SWR above
3.5) without the display saying so, and the power meter (`15 11`) showed the
carrier — above 2 W whatever the power setting — for the whole tuning and 0
after it; the radio answers CI-V meanwhile. So the guard reads the power meter
replies that pass through it (the server asks every second; the guard sends
nothing itself and takes nothing from the server) and ends a tuning at two
zero readings in a row, not before 3 s. Until then no other transmission
starts and nothing changes the radio. A tuning still going after 30 s (the
radio still emitting, or nobody asking for the meter) is a fault: transmit is
locked until `tx_unlock`, durably. 30 s must fit into the duty cycle before a
tuning starts; the history holds 30 s until the end is seen, then the real
duration (at least 3 s). The next tuning is allowed 10 s after the end of the
previous one. The server's readings can only shorten the hold, never fake its
end: only frames from the radio count.

**A screen-blanked G90 does not tune (14 November 2026).** It acknowledges the
command and carries the tuning out only when its screen comes on, so the guard
ends the "tuning" at the zero readings with no power seen (`emitted: false`).
The owner chose a warning only, no lockout: the guard sends the end of every
tuning to the server, and the page shows in red that the radio did not tune
and the antenna is not tuned. Screen blanking off is a hardware precondition.
A very short real tuning (under about 0.5 s) can fall between meter readings
and give the same warning.

## D44. Setting the RF power over CAT, limited by the server

`14 0A` with exactly 2 data bytes is on the allowlist and refused during a
transmission. The guard does not read the value (rule 3), so the limit lives
in the server: only 1 W … `safety.max_rf_power_w` is accepted, and without
that key setting the power is disabled — there is no silent default. After
every write the server reads the setting back; on a mismatch the request
fails, and a setting above the limit is lowered to the minimum at once. The
G90 sets whole watts and rounds down, so the server writes the lowest level
for the requested watts and compares watts, not levels.

## D46. Transmitting from the browser on an antenna

A browser confirms once per page session, in a dialog that names the
frequency and power, that it transmits on the antenna; the CLI test confirms a
dummy load or a low-power antenna test instead (D15, D56). The server accepts voice from the browser only in
U-D/L-D (the G90 takes audio from the DE-19 only in the data modes; in USB it
would transmit its own microphone), and in split checks the transmit VFO.

## D48. Login: station accounts

- **An account is a station:** callsign (also the login), the QTH locator of
  the remote station, a password of at least 10 characters.
- **The first account** is created on the page of a fresh installation and is
  the only one that adds and deletes accounts. Lost access is recovered with
  `remoteg90ctl user passwd` on the server.
- **Permissions:** every account has `receive`, `control`, `tune`, `transmit`
  and `log`; account management over the server protocol is CLI-only. Without
  a session a browser has no permissions at all (rule 16).
- **Sessions:** 30 days, `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS;
  only a SHA-256 hash of the token is stored. Logging out, a new password or a
  deleted account ends the sessions and closes the account's pages — and with
  them the transmit lease.
- **One account at a time** may have pages open (one radio, one operator);
  the same account may have several pages.
- **The administrator** is the first account. It also has `admin` (the
  "restart server" button; the server only, refused while transmitting), and
  since a security review on 14.11.2026 the "System log" window (the server's
  log shows who logged in and when) and I/Q recording (it fills the disk).
  Without a session, `/api/session` says only "another account". It
  **may take the station over**: while another account works, the
  administrator can log in but its page does not open; it shows who uses the
  station, since when and whether it transmits, with "Close …'s page and take
  over" and "Wait". Only that button (after a confirmation) closes the other
  account's pages and logs it out (sessions revoked, transmit lease ended);
  the administrator's own sessions on other devices stay. Logging in or a
  page reconnecting by itself throws nobody out. No other account can log the
  administrator out. Why: the owner must be able to get the station back
  remotely without a terminal, and taking over only by a deliberate choice,
  knowing who works and whether they transmit, protects a working operator.
- **Settings on the account:** the radio settings are restored after login
  through the normal requests (permissions, refusal during TX, power not above
  `max_rf_power_w`).
- **Security:** `scrypt` password hashes, growing delays after 5 failed logins,
  `POST` without `Origin` refused (D34), and an unreadable accounts file locks
  the browser out instead of offering a new first account (which would let
  anyone take over the station). Accepted risk: until the first account
  exists, any device that can reach the page can create it.

## D49. Browser audio processing in detail

Two noise reductions written for this project, without extra libraries: an
**LMS** adaptive predictor (no latency, works on CW too) and a **spectral**
one (minimum-statistics noise estimate, Wiener-type gain, one frame ≈ 21 ms of
latency). The receive band-pass follows the passband sliders on the waterfall.
Manual notches are set by clicking the audio spectrum; the automatic notch is
switched off in CW and CW-R. The transmit equaliser has 6 bands (100–3200 Hz).
RNNoise was rejected for now (good for speech, harmful to CW and weak signals).

## D50. One station log per account

Each account sees and exports only its own QSOs; ADIF gets `STATION_CALLSIGN`
and `MY_GRIDSQUARE` from the account. A duplicate (same call, band and mode on
the same UTC day) is a warning, not a block; ADIF import skips QSOs already
present. The `log` permission guards the log; tests check that log requests
never send a frame that changes the radio state.

## D51. Recording and automatic calling

A recording of the processed microphone (at most 15 s) lives only in the
browser tab. "Play" plays it locally, without transmitting; the transmit
spectrum shows it greyed out and marked "not sent". "Call" transmits it, waits
5–120 s and repeats: every call is an ordinary transmission with its own
lease, renewed by the page, with all guard limits. Each automatic call needs
its own confirmation and stops on Stop, PTT, a hidden page, a lost connection,
a refusal or an ended lease, and after 2 minutes without operator activity.

## D52. FT8/FT4 reception in the browser

The browser decodes, using `ft8ts` (GPL-3.0, a TypeScript port of the WSJT-X
decoder) in a Web Worker: a small machine next to the radio could not decode a
full band in time. The server adds clock frames to the audio stream (the UTC
time of a sample and whether the clock is synchronised), so the browser cuts
cycles by the server's clock regardless of network delay. Decoding runs on the
audio before any browser processing.

## D53. FT8/FT4 transmission: automation in the browser, signal from the server

The page sequences QSOs like WSJT-X (CQ, answering by clicking a decode,
Tx1–Tx6) because it has the decodes. The **server** generates the signal from
the message text with `ft8lib`: it starts exactly on its synchronised clock and
knows what it transmits, so it can check the message and the tone. Sending
FT8 audio from the browser through the voice path would bypass these checks.

Every window is an ordinary page lease taken about 1.5 s before the cycle and
returned after the `ft8_sent` event. `ft8_transmit` requires the session's
lease, a synchronised clock, a message that encodes completely, the whole
signal within 200–2800 Hz, a level of −40…0 dBFS and a cycle not more than 2 s
late. The audio goes through the same limiter and the same "audio only during
a lease" path as voice. The page stops on Halt TX, voice PTT, a hidden tab, a
lost connection, any refusal, an ended lease, and after **6 minutes** without
operator activity (as WSJT-X's Tx watchdog); enabling transmission needs a
confirmation that names the frequency and power.

"Synchronised clock" means chrony's error bound (|offset| + root delay / 2 +
root dispersion) is at most **0.2 s**; without chrony the operating system's
flag counts. A flag alone was not enough: a laptop's clock once drifted 2.3 s
while it still reported itself synchronised, and every decode showed DT 2.3 s.

## D54. Transmitting only inside the ITU region's amateur bands

A remote transmission is easy to start on a wrong frequency (a click on the
waterfall, a remembered band). The server therefore refuses a browser's
`tx_request` when the **whole signal** — 3 kHz above (U-D) or below (L-D) the
transmit frequency, on the other VFO in split — is not inside one amateur band
of the account's ITU region, and refuses `atu_tune` when the tuning carrier is
outside. No locator or no frequency reading means refusal.

- The region is computed from the account's QTH locator using the ITU lines
  A, B and C (Radio Regulations No. 5.3–5.9) and their exceptions; borders are
  approximate (a 4-character square is 2° × 1°).
- Only the **regional** allocations of Article 5 count, without national
  footnotes: the block may be narrower than a licence, never wider. In Region 1
  this means 160 m is 1810–1850 kHz only.
- The IARU band plans (Region 1 2020, Region 2 2020, Region 3 2024) are
  displayed above the waterfall as a recommendation, not enforced.
- Accepted risk: a locator that does not match the transmitter's location
  gives the region of another place.

## D62. Reading CW in the browser (a skimmer)

The page finds CW tones in the U-D/L-D passband and decodes up to 8 signals
at once, from the audio before the browser's processing (as D52). A click on
a signal narrows the browser's receive filter around its tone (200, 300 or
500 Hz, the operator's choice); decoded calls are shown over the waterfall.
The code is our own and classical: for each signal an envelope, a threshold
that follows the signal and the noise, the speed tracked by itself, and dots,
dashes and gaps told apart.

**Why:** a neural decoder (DeepCW, MIT) copies better at weak signals, but
needs about 12 MB downloaded when first opened and a new dependency,
`onnxruntime-web`. The maintainer chose a light decoder of our own first;
DeepCW stays an option for later.

**Consequences:** reception only, with no path to transmitting or to CAT; the
radio's filter and mode are not changed. CW transmission stays out of scope
(D47).

## D61. RTTY: reception in the browser, transmission from the server

RTTY at 45.45 baud with a 170 Hz shift, as AFSK in L-D (mark 2125 Hz,
space 2295 Hz, with a REV button). The decoder runs in the browser on the
audio before the browser's processing (as D52); the server generates the
transmitted signal from the text (as D53). The code is our own, modelled on
fldigi and minimodem (both GPL-3.0-or-later).

**Why:** RTTY is simple (Baudot code, two tones), and there is no library for
the browser under a suitable licence; transmit paths are always our own code
(AGENTS.md section 6).

**Consequences:** the same safeguards as FT8 — the page's lease, the operator
watchdog, the duty cycle, the band check — but unlike FT8 no automation of the
contact: macros (for ordinary contacts and for contests) transmit only on the
operator's click.

## D60. SSTV reception in the browser

The page decodes SSTV pictures from the receive audio, in the browser, before
the browser's processing, as FT8/FT4 (D52); the server decodes nothing. The
decoder finds the mode by itself (VIS code and line length) and draws the
picture line by line.

**Why:** pictures can be watched without a separate program; reception has
no path to transmitting or to CAT.

**Code:** our own TypeScript port of the decoder of `xdsopl/robot36`
(0BSD, Ahmet Inan), with the author, licence and source in the files and in
`docs/third-party.md`.

**Out of this decision:** SSTV transmission; it needs a separate decision.

## D59. LoTW through TQSL on the operator's computer

The operator uploads to LoTW: the page exports the log to ADIF, and TQSL signs
and uploads it on the operator's own computer. The server has no LoTW
service, no certificate and no network access.

**Why:** Debian's `trustedqsl` is a graphical program even in its batch mode:
on the computer by the radio (DietPi) it would pull in 84 packages (GTK, X11,
D-Bus), about 150 MB, and change the system the radio depends on. TQSL on the
operator's computer skips QSOs it has already signed, and LoTW rejects
duplicates, so uploading the whole log is safe and simple.

**Consequences:** no separate LoTW service or user on the server; the
certificate never reaches the computer by the radio; the log shows no
"uploaded" mark per QSO; no upload from a phone (TQSL has no Android version).

## D58. The sound card's output level ("card out") on the page

The DE-19 sound card's output (`amixer` "Speaker", -37..0 dB) is set from the
page ("card out" in the Transmit section): also while transmitting, kept on
the server for the station (`audio-output.json` in the server's state
directory, set again whenever the card opens), by any account with
`transmit`. Before, the program left the level where the system had it (-20 dB
on a fresh DietPi, FT8 drove the ALC to 18 instead of about 60); the drive is
tuned watching the ALC while transmitting.

Consequence for the transmit path: the server's limiter (`audio.tx_peak_dbfs`)
still caps the digital peak, but the absolute drive into the radio is now set
by "card out" (up to the card's 0 dB), which comes after it. Power stays
bounded by the radio's setting and `safety.max_rf_power_w`, time by the guard;
no sound reaches the radio outside a lease (changing the level plays nothing).

## D57. The page stays in the tailnet: no Tailscale identity, no access

Under a name from `server.allowed_hosts` the server answers only requests that
carry `Tailscale-User-Login`. `tailscale serve` adds that header for tailnet
users and removes any copy a client sends; **Tailscale Funnel** serves the same
name to the whole internet without it, as do tagged devices and other proxies.
Without it the answer is `403`, logged as `web_request_refused`. A proxied
request is told by `X-Forwarded-Host`, which `tailscale serve` sets on every
request it passes on, overwriting a client's copy, while it passes the
client's `Host` on unchanged: such a request needs a name from
`allowed_hosts` and the identity, so a client that is not a browser cannot
put `Host: 127.0.0.1:…` through Funnel. A request marked
`Tailscale-Funnel-Request` (Tailscale removes a client's copy) is always
refused. The computer itself (`127.0.0.1`, no proxy headers) is not affected. `server.require_tailnet_identity`
(default `true`) is turned off only deliberately, for tagged devices. The setup
checks `tailscale serve status --json` for `AllowFunnel` and warns loudly.

Why: one command (`tailscale funnel`) would put the page on the internet under
the same name as `tailscale serve`, so the `Host` check (D34, D38) would not
stop it, and the login would be the transmitter's only protection. Tailscale
marks Funnel traffic with `Tailscale-Funnel-Request` and without the identity.
The first version checked the identity only under the tailnet name; a
security review on 14.11.2026 found that a forged `Host` skipped it. A local user of the computer
could forge the header by connecting to `127.0.0.1` directly, but already has
access to the computer, and the station login still applies.

## D56. One-command installation and a setup with the hardware checklist

`curl … | sudo bash` installs the program (Debian, Ubuntu and derivatives,
Raspberry Pi OS 64-bit, DietPi; x86_64 and aarch64). `sudo remoteg90ctl
setup` then finds the DE-19 (its serial adapter, and its sound card as the one
on the same USB hub) and the I/Q card, writes udev rules and the configuration,
and walks the operator through the hardware checklist once per radio port:
VOX off, the DE-19's jack cable in the front panel's IO/User socket (not the
rear COMM), SWR threshold, no keying when USB is plugged in, the
guard's start, the radio's identity, CAT against the display, a 2 s PTT test
and one ended by stopping the server. Transmission goes on only after an
explicit yes at the end. Any item can be skipped by typing SKIP; the skip is
recorded and the page shows a warning until the checklist is done.

The PTT tests use the ordinary `tx-test` lease, on a dummy load or on the
antenna at low power. On the antenna the server allows the CLI's test only in
SSB, with at most 1 W set, and with the whole signal inside a band that all
three ITU regions have (the CLI has no account, so no locator and no region).

## D55. Transmit audio presets

Four presets of the browser's microphone processing — DX, Local and two
operator slots — change only browser settings. They are starting points chosen
without listening tests and will be tuned on the air. Safety is unaffected:
the server's limiter and "audio only during a lease" apply as always.
