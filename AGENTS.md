# AGENTS.md — remoteG90ctl

Rules for everyone who changes this project: human contributors and AI coding
agents alike. Read this file before any change; [docs/decisions.md](docs/decisions.md)
explains *why* the rules are what they are.

**Name:** `remoteG90ctl` in documentation and the user interface; `remoteg90ctl`
where lower case is the convention (Python package, CLI command, systemd units,
paths under `/etc`, `/run`, `/var/lib`).

Section and rule numbers in this file are stable: the code refers to them
(e.g. "AGENTS.md rule 4", "section 8").

## 0. Before you start

1. Read this file and [docs/decisions.md](docs/decisions.md).
2. Anything that touches transmitting — the guard, the lease, PTT, the CI-V
   allowlist, audio to the radio, band limits — is decided by the maintainer.
   Open an issue and describe the change before writing code.
3. When a rule here seems to conflict with a task, stop and ask; do not
   resolve the conflict yourself.

## 1. Goal

A Linux server for the **Xiegu G90** HF transceiver, used locally or over a
private network (e.g. Tailscale), with a browser interface:

- radio control (CAT over CI-V),
- two-way audio (receive, and voice transmission from the browser's microphone),
- a wideband waterfall from the radio's factory I/Q output (UX model: HDSDR),
- FT8/FT4 reception and transmission built in (no WSJT-X needed),
- RTTY reception and transmission (45.45 baud, 170 Hz shift),
- reading CW in the browser (a skimmer: several signals at once in the U-D/L-D passband),
- SSTV reception (pictures) in the browser, without transmission,
- DX cluster spots (telnet) in a browser window and over the waterfall; read only,
- audio processing in the browser (filters, notch, noise reduction, compressor),
- a station log per account (SQLite, ADIF); the operator uploads its ADIF export to LoTW with TQSL on their own computer.

## 2. Safety first

**The safety of the operator and the equipment wins over every other property**
— convenience, performance, deadlines. When in doubt, choose the behaviour that
does not transmit.

### Hard rules

These must not be broken or weakened without an explicit decision of the
maintainer, recorded in `docs/decisions.md`.

1. **Only the guard process opens the radio's serial port.** No other code opens that device.
2. **DTR and RTS are lowered immediately after opening the port**, on TX release, on signals (SIGTERM, SIGINT), on every exception and on close. The port has HUPCL set, so the kernel drops the lines if the process dies.
3. **PTT exists only as a guard function.** CI-V frames from the server pass a **deny-by-default allowlist**: the guard reads only the addresses, the command byte and the sub-command, compares them with the list and does not interpret the data. Everything else is rejected and logged. For the menu command `0x1A 0x05` the two item bytes count as part of the sub-command: the allowlist names items, with separate entries for reading and writing; values are still not interpreted (D37).
   - **One message = exactly one valid frame.** Before the allowlist, the guard validates the whole message: exactly one `FE FE` preamble at the start, exactly one `FD` and only at the very end, no `FE`/`FD` inside, the destination equal to the configured radio address, the source equal to the controller address, the length within limits, valid hex. Anything else (two frames glued together, garbage before or after, a truncated frame) is rejected in full and logged — never trimmed or "repaired".
4. **Transmitting works as a lease:**
   - the client renews the lease every **0.5 s**,
   - no renewal for **2 s** → PTT released at once,
   - **hard limit of one transmission: 180 s**; then PTT is released unconditionally and the next transmission needs an explicit unlock (it cannot be waited out or bypassed by renewing),
   - **duty cycle: at most 300 s of transmission in any sliding 600 s window** (D31); when reached, PTT is released at once and new transmissions are refused until the window has room (no unlock needed). The history is persistent; unreadable = window full; not saved = no transmitting until a save succeeds,
   - the values are configurable, but the guard **refuses** a configuration that exceeds these limits (shorter is allowed, longer is not),
   - **exception: antenna tuning** (D43) transmits without a lease and cannot be interrupted (the radio stops by itself: 1–1.5 s with a matched antenna, about 9 s with a poor match — [measurements](docs/g90-measurements.md), item 13). It exists only as the guard function `atu_tune`: the same conditions as `tx_request`, but its own switch `safety.allow_atu_tune` instead of `allow_transmit`; **for the guard a tuning lasts until the radio's power meter reads zero twice in a row** (at least 3 s): until then no other transmission and no change of the radio; still going after **30 s**, transmit is locked until `tx_unlock`; the real duration counts towards the duty cycle (30 s must fit before it starts); the next tuning at the earliest 10 s after the end of the previous one; recorded in the TX log (D43).
5. **The lease chain includes the end client.** The server renews the guard's lease only when it receives renewals from the client (browser/CLI). The server never renews "on behalf of" a client that went silent. **Clients never connect to the guard directly** — the server is the guard's only client.
6. **At most one source transmits at a time.** A second TX request while a lease is active is refused.
7. **Every transmission is written to the TX log** (a separate JSON Lines file): start, end, duration, source, end reason (released / not renewed / hard limit / error), the frequency given by the server.
8. **Devices are named by stable identifiers**: the port as `/dev/serial/by-id/...`, sound cards by stable names (never by number). A missing or mismatching device **blocks transmitting**, and the program says so clearly. After every port open the guard checks the **radio's identity** (`1D 19`, expected value from the configuration); until it is confirmed, transmitting is blocked while CAT works (D32).
9. **Automated tests never touch a real radio.** The default backend is the simulator. Hardware needs an explicit `radio.backend = "hardware"`; transmitting on hardware additionally needs `safety.allow_transmit = true`.
10. **Do not guess facts about the radio.** CI-V address, baud rate, supported commands, the DE-19 PTT line — unless confirmed (section 8), keep them in the configuration and mark them in the code as UNVERIFIED. Do not invent CI-V commands.
11. **The guard has minimal dependencies**: the standard library and `pyserial`. No numpy, no TCP networking, no audio, no frameworks.
12. **The systemd watchdog "alive" signal comes only from the guard's safety loop** — the same loop that checks lease expiry and the hard limit — and only after a complete pass. Never from a separate thread, timer or event loop that would keep running while the safety loop is stuck. A stuck loop = no signal = systemd kills the process and the kernel (HUPCL) drops the lines.
13. **A guard restart never restores a transmission and never clears a lockout.**
    - After a start the guard has no active lease and accepts no "continuation" of a previous one.
    - The lockout after the hard limit is **persistent**: written to disk (atomically, `fsync`) before PTT is released for the limit, and read at start. Only an explicit `tx_unlock` clears it.
    - If the state file cannot be read or is corrupt, the guard starts **locked**.
    - Every start after an unclean end (crash, watchdog kill) is recorded in the TX log and the log.
14. **Transmitting requires a confirmed safe port state.** The guard accepts `tx_request` only when the port is open and DTR/RTS have been set inactive and that state is confirmed. Losing the port (USB unplugged, I/O error) → the lease ends at once, TX is blocked until the port has been reopened safely, and the TX log gets an entry.
15. **Network barrier.** The server listens only on loopback (`127.0.0.1`, `::1`) or a Unix socket and refuses to start otherwise; there is no switch to bypass this. Remote access goes through a loopback HTTPS proxy such as `tailscale serve` (D38).
16. **Permissions.** Separate permissions for receiving (audio, waterfall, reading state), control (frequency, mode, settings), antenna tuning (`tune`), transmitting and writing the station log. No authenticated session = no permissions at all. Losing the session during TX ends the lease like a dropped connection. Browsers log in to a station account (D48); account management over the server protocol is available only to the local CLI. The first account is the administrator: it also has `admin` (restarting the server from the page, the server's log in "System log", I/Q recording) and may take the station over: while another account works, the administrator's page shows who it is, and only an explicit click closes that account's pages and logs it out (its lease ends); logging in or reconnecting alone throws nobody out. No other account can log the administrator out. Without a session the page says only that the station is in use, not by whom. Do not design anything that assumes a single all-powerful user.
17. **A browser transmits only inside the amateur bands of its ITU region** (D54). The region follows from the QTH locator of the logged-in account; the limits are the regional ITU allocations (Radio Regulations Article 5), without national footnotes. Before every browser `tx_request` and `atu_tune` the server checks that the **whole signal** (3 kHz from the dial in U-D/L-D; the tuning carrier) is inside a band; no locator, no region or no frequency reading = refusal (`out_of_band`). The IARU band plan is only displayed. The CLI transmit test into a dummy load (D15) is not subject to this rule; the **CLI test on the antenna** (D56, `tx-test --antenna-low-power`) is allowed only in SSB, with at most 1 W set, and with the whole signal inside a band that all three ITU regions have (the CLI has no account and no locator).
18. **The program does not change the radio's settings by itself** (D64). The radio's settings (frequency, mode, power, VFO, split, ATU, preamp/attenuator, AGC, NB, RF gain and any other) are changed only by the operator, through their own action on the page or in the CLI, or by automation the maintainer has named; the list is in `docs/decisions.md` (D64). Any new change of a radio setting without the operator's direct action — scanning, band hopping, measurements, restoring or matching a mode — needs the maintainer's decision before code is written. Audio processing in the browser does not change the radio and is not covered by this rule.

## 3. Hardware

- Radio: Xiegu G90, CAT over CI-V (an Icom dialect with a reduced command set).
- Interface: **DE-19** — an internal USB hub with a CH340 serial port (CI-V, and the RTS line for PTT) and a C-Media sound card (card 1: RX/TX audio).
- The radio's factory **I/Q output** → a second sound card (card 2, stereo I and Q) → waterfall.
- PTT is keyed by **RTS** (DTR does not key). Every port open keys the radio for a fraction of a second; this is a known and accepted risk (section 8 item 8, D23).
- Published CI-V commands of the G90, categorised for the allowlist: [docs/civ-g90.md](docs/civ-g90.md). Check the category before adding anything to the allowlist. Category C (anything that transmits, **including antenna tuning**) never goes on the allowlist; the only exception is tuning as the guard function `atu_tune` (D43).
- **VOX in the radio bypasses the guard.** With VOX on, any sound at the radio's audio input becomes a transmission without a lease or limit, and the G90 probably cannot report its VOX state over CI-V. VOX off is therefore a hardware precondition. The server **never sends any sound to the radio's sound card outside an active TX lease** — not even silence, test tones or monitoring.

## 4. Architecture

```
browser / CLI
      │  CLI: the server's Unix socket; browser: HTTPS (via a loopback proxy) + WebSocket
      ▼
┌──────────────┐    Unix socket     ┌──────────────┐   /dev/serial/by-id/...
│    server    │ ◄────────────────► │    guard     │ ◄───────────────────────► G90 (via DE-19)
│ (CI-V logic, │    JSON Lines      │ (port, PTT,  │
│ audio, web)  │                    │ lease,       │
└──────────────┘                    │ allowlist)   │
                                    └──────────────┘
```

- **guard** — a small, critical process. Owns the port, forwards CI-V frames (checking only addresses and command/sub-command against the allowlist), manages the TX lease, writes the TX log. A systemd service with `WatchdogSec` (sd_notify implemented with the standard library; signal source: rule 12). Persistent lockout state in the service's state directory (rule 13). No safety path in the guard depends on an answer from the server.
- **server** — CI-V logic (frame encoding/decoding, frequency, mode, meters), relaying the lease, audio, waterfall, WebSocket, accounts, the browser UI.
- **cluster** (`remoteg90ctl-cluster`) — a small process that keeps one telnet connection to the DX cluster set by the administrator and passes the spots to the server over a Unix socket. The only process with network access beyond loopback; it does not connect to the guard, the radio or the station log.
- **guard ↔ server**: a Unix socket with file permissions (service owner/group only), versioned JSON Lines protocol, CI-V frames as hex: [docs/guard-protocol.md](docs/guard-protocol.md). Server protocol: [docs/server-protocol.md](docs/server-protocol.md).
- **Simulator**: a serial port implementation that behaves like a G90 (CI-V replies) and records the DTR/RTS state; used by the tests and the `simulator` backend.

## 5. Technology

| Area | Choice |
|---|---|
| Server language | Python 3.12+ |
| Project and dependencies | `uv`, `pyproject.toml` |
| Lint / format | `ruff` |
| Types | `mypy --strict` |
| Tests | `pytest` (+ `pytest-asyncio`), `hypothesis` for property tests of the CI-V parser and the guard's frame check (development only) |
| Serial port | `pyserial` |
| CAT | own CI-V layer (no Hamlib, no `rigctld` emulation) |
| Audio | `sounddevice` on ALSA/PipeWire, Opus over the network |
| FFT / DSP | `numpy` |
| Web server | `aiohttp` |
| Frontend | TypeScript without a framework; the compiled JavaScript is committed and CI checks it |
| FT8/FT4 | decoder in the browser: `ft8ts` (GPL-3.0) in a Web Worker; encoder on the server: `ft8lib` (GPL-3.0-or-later) |
| RTTY | decoder in the browser, encoder on the server; our own code (models: fldigi, minimodem, GPL-3.0) |
| CW (reception) | decoder in the browser; our own code (ideas: fldigi, GPL-3.0) |
| SSTV | decoder in the browser: our own port of the `robot36` decoder (0BSD) in a Web Worker; reception only |
| DX cluster | a separate process `remoteg90ctl-cluster` (standard library, telnet over TCP); spots to the server as JSON Lines over a Unix socket |
| Station log | SQLite (`sqlite3`), ADIF import/export; for LoTW, ARRL's TQSL signs and uploads the ADIF export on the operator's computer, not on the server |
| Configuration | TOML (`tomllib`) |
| Services | systemd: `remoteg90ctl-guard.service`, `remoteg90ctl-server.service` |

A new dependency needs a reason in the pull request. For the guard, see rule 11.

## 6. Conventions

- **Code, identifiers, comments, commit messages and documentation: English.**
- **Licence: GPL-3.0-or-later** ([LICENSE](LICENSE)). Every source file starts with `# SPDX-License-Identifier: GPL-3.0-or-later` (or the equivalent comment syntax).
- **Code from other projects** only under a GPL-3.0-compatible licence, always keeping the author, licence and source (a comment in the file and an entry in [docs/third-party.md](docs/third-party.md)). Ideas taken without code are recorded there too.
  - Code may be copied from GPL-3.0, GPL-2.0-or-later, LGPL, MIT, BSD, Apache-2.0 and other GPL-3.0-compatible licences. Check the licence in the source repository itself.
  - AGPL-3.0: ideas only, unless the maintainer agrees.
  - No licence, GPL-2.0-only or incompatible licences: ideas only.
  - **Transmit paths are always our own code:** the guard, the lease, PTT, the allowlist and anything that can cause a transmission are never copied from other projects, even with a compatible licence.
- Repository layout:
  ```
  src/remoteg90ctl/guard/      # guard process
  src/remoteg90ctl/server/     # server
  src/remoteg90ctl/civ/        # CI-V frame encoding/decoding (pure functions, shared)
  src/remoteg90ctl/sim/        # radio simulator
  src/remoteg90ctl/config.py   # configuration loading and validation
  web/                         # TypeScript frontend
  tests/
  docs/
  deploy/                      # install scripts, systemd, udev
  config/example.toml
  ```
- Types everywhere, `mypy --strict` without exceptions unless justified in a comment.
- Logging through `logging`, structured (key=value), no `print` in production code.
- The configuration is validated at start-up; a bad configuration means refusing to start with a readable message, never a silent default for a safety parameter.
- Every change to the guard or to a safety rule comes with tests of the failure paths (client disconnect, exception, signal, limit exceeded).

## 7. Commands

```
uv sync                 # install dependencies
uv run pytest           # tests (simulator only)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy src         # types
cd web && npm ci && npm run build && npm test   # frontend
```

All of them must pass before a pull request is merged.

## 8. Facts about the radio

Measured facts about the G90 — the CI-V address and speed, the PTT line, I/Q
format and offsets, meters, the port-open keying pulse, and what remains
unmeasured — are in [docs/g90-measurements.md](docs/g90-measurements.md),
with the item numbers the code refers to. They come from **one radio with
firmware 1.81** and one DE-19; check them on your own radio before relying on
them. Facts reported by other projects are hypotheses: they may be used as
configuration defaults or in the simulator (marked UNVERIFIED), never as the
basis of a safety path.

The hardware checklist before connecting a radio is in
[docs/install.md](docs/install.md).

## 9. Out of scope

- Digital modes other than FT8, FT4, RTTY and SSTV reception (no JS8, PSK, WSPR…). SSTV transmission needs a separate decision.
- Integration of external digital-mode programs (WSJT-X, JTDX, fldigi) or exposing the radio through `rigctld`/flrig.
- Radios other than the G90 (abstractions only when there is a real need).
- Windows, macOS or Android as the server platform.
- Exposing the server directly to the internet — remote access goes through a VPN and HTTPS.
- Unattended operation: there is no mode that transmits without an operator's open session.

## 10. Roadmap

Done (0.x releases, see [CHANGELOG.md](CHANGELOG.md)): guard, CAT and CLI;
I/Q waterfall with click-to-tune; receive audio; voice transmission from the
browser; further CI-V commands (power, ATU, preamp/attenuator, AGC, NB, RF gain,
VFO A/B, split); browser audio processing; station log; accounts and login;
FT8/FT4 reception and transmission (used on the air).

Planned for 1.0.0: longer tests at the remote station.

The requirements below apply to the features already built and remain binding.

### Requirements for FT8/FT4 transmission (stage 7)

All rules of section 2 apply without exception, and in addition:

- **Each transmit window is a separate lease**, held by the operator's browser page and renewed by it (rule 5). A closed tab, a sleeping phone or a dropped VPN stops transmitting and the automation.
- **Operator watchdog**: after **6 minutes** without a click or key press on the page the automation stops transmitting; it cannot be turned off.
- **Clock:** the server refuses FT8/FT4 transmission while the system clock is not synchronised. Reception may continue, with a warning.
- **Duty cycle and power:** consecutive windows are limited by the guard's duty cycle (D31); the power is the operator's setting within `safety.max_rf_power_w`.
- **Tone:** the whole signal must lie within 200–2800 Hz of the radio's audio passband; other values are refused.
- **The server generates the signal** (`ft8lib`) from the message text, within the page's lease, through the limiter and the audio path to the radio (D53); the browser never sends FT8/FT4 audio to the radio.
- **Audio to the radio only during a lease** (section 3).
- **No unattended mode:** nothing transmits without an open operator session. Compliance of the automation with the operator's licence conditions is the operator's responsibility.

### Requirements for RTTY transmission (D61)

All rules of section 2 apply without exception, and in addition:

- **Each transmission is a separate lease**, held and renewed by the operator's browser page (rule 5). A closed tab, a sleeping phone or a dropped VPN ends the transmission.
- **Operator watchdog**: after **6 minutes** without a click or key press on the page the transmission ends; it cannot be turned off.
- **Duty cycle and power:** consecutive transmissions are limited by the guard's duty cycle (D31); the power is the operator's setting within `safety.max_rf_power_w`.
- **Tone:** the whole signal (mark, space and their sidebands) must lie within 200–2800 Hz of the radio's audio passband; other values are refused.
- **The server generates the signal** from the text, within the page's lease, through the limiter and the audio path to the radio; the browser never sends RTTY audio to the radio. Idle characters while the operator types slowly are sent only during the lease.
- **Audio to the radio only during a lease** (section 3).
- **No automation:** macros transmit only on the operator's click; there is no automatic answering and no CQ in a loop.

### Requirements for the DX cluster (D63)

- **The cluster has no path to transmitting.** The cluster process does not connect to the guard or the radio; no spot causes a `tx_request`. A click on a spot only tunes the radio and sets its mode (control permission).
- **Network:** only `remoteg90ctl-cluster` reaches the internet, and only the configured cluster; the server stays on loopback. The server can pass the cluster process only the address, the port and the callsign.
- **Read only:** the process sends the cluster only the callsign at login; it sends no spots or messages.
- **Data from the cluster is untrusted:** length-limited, every line parsed, shown as text (never HTML); a malformed line is dropped.
- **Configuration** (address, port, callsign) only with the `admin` permission; one connection for the station. Reading spots needs the receive permission.

### Requirements for the station log and LoTW (D30, D59)

- **The log is local:** SQLite on the server, working without internet; ADIF import and export according to the ADIF specification.
- **The log has no path to transmitting.** Saving, exporting or uploading must never cause a `tx_request` or change the frequency or mode.
- **The server reaches only loopback** (systemd `IPAddressDeny=any`, `IPAddressAllow=localhost`).
- **LoTW is outside the server.** The server neither signs nor uploads anything to LoTW and holds no LoTW certificate or password. The operator exports the log to ADIF and uploads it with TQSL on their own computer. TQSL skips QSOs it has already signed and LoTW rejects duplicates, so the whole log may be uploaded.

### Requirements for audio processing in the browser (stage 8, D39)

- **Browser processing serves quality and convenience, never safety.** The server does not trust client processing: every protection of the transmit path (the level limiter — it caps the digital peak, while the absolute drive into the radio is set after it by the card's output level, "card out", D58 — and audio to the radio only during a lease) works on the server, whatever the client did and whatever kind of client it is.
- **The audio spectrum is a display only.** The transmit spectrum shows only what actually goes to the radio, so outside a lease it is empty; local playback of a recording is shown greyed out and marked "not sent" (D51).
- **Settings belong to the operator's account** and never change radio settings over CAT.
- **The automatic notch** is switched off in CW and CW-R and for digital modes.
- **FT8/FT4 decoding and the log do not depend on browser processing:** the decoder works on the audio before the processing, right after the Opus decoder (D52).
- **The browser's built-in voice processing** (echo cancellation, noise suppression, automatic gain control in `getUserMedia`) is off while our transmit chain is active.
- **Latency:** every processing block states its latency; the total is shown in the interface.

## 11. Versions and workflow

**Branches**
- The integration branch is **`main`**. Changes only through pull requests, merged only with green CI.
- One change per branch: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- CI (`.github/workflows/ci.yml`) runs the commands of section 7 on every pull request, including one that changes only documentation.
- There is no separate development branch: `main` *is* the development branch, and releases protect users. The installer (`install.sh`) installs the newest release (a tag), never `main`, unless `REMOTEG90CTL_BRANCH` asks for a branch.

**Numbering (SemVer, PEP 440)**
- The only source of the version: `src/remoteg90ctl/__init__.py` (`__version__`); `pyproject.toml` reads it from there.
- `0.x` until 1.0.0; the minor version follows the development stage. Bug-fix releases increment the last digit.
- **A version without a pre-release suffix only after the hardware checklist has been passed on a real G90.** Until then only candidates (`0.4.0rc1`, …): the version number tells whether a release has transmitted on real hardware.
- A release is a tag `vX.Y.Z` on `main` plus a GitHub release with the notes from `CHANGELOG.md`. Releases are made by the maintainer.
- **Which release `install.sh` installs:** the newest one without a suffix; while there is none, the newest candidate (`rcN`), saying it has not been checked on the radio. A candidate newer than the newest plain release is installed only when `REMOTEG90CTL_RELEASE` names it. An installation that follows a branch keeps following it.

**Changelog**
- [CHANGELOG.md](CHANGELOG.md), *Keep a Changelog* format. Every user-visible change goes into `[Unreleased]` in the same pull request.
- Every change touching the guard, the lease, PTT or the transmit rules has an entry under **Safety**.

**Format versions** (independent of the program version, changed only on incompatible changes): the guard protocol, the server protocol, the lockout state file. The program version is written into every TX log entry and returned by `status` of the guard and the server.
