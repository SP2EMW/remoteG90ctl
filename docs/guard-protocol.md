# Guard protocol (version 1)

The guard (`remoteg90ctl-guard`) is the only process that opens the radio's
serial port. Its only client is the server; the CLI and the browser never talk
to the guard (AGENTS.md rule 5).

## Transport

- Unix stream socket at `guard.socket_path`, mode `0660` (owner and group of the
  service only).
- **At most one client.** A second connection receives one `busy` error and is
  closed; the first connection is unaffected.
- JSON Lines: one UTF-8 JSON object per line, terminated by `\n`. A line longer
  than 4096 bytes closes the connection.
- **Closing the connection ends any active transmission immediately** (reason
  `client_disconnected`). So does a client that stops reading until 256 KiB
  of output are queued.

## Requests and responses

Every request carries the protocol version, a client-chosen `id` (integer or
string of up to 64 characters) and a `type`. Fields not listed for a type are
rejected.

```json
{"v": 1, "id": 7, "type": "status"}
```

Success:

```json
{"v": 1, "id": 7, "ok": true, ...}
```

Failure:

```json
{"v": 1, "id": 7, "ok": false, "error": "tx_locked", "message": "human-readable reason"}
```

Requests are answered in order. Events (below) may be interleaved between
responses.

### `status`

No fields. Response fields:

| Field | Meaning |
|---|---|
| `version` | program version of the running guard |
| `backend` | `simulator` or `hardware` |
| `ptt_line` | `dtr`, `rts` or `civ` |
| `port` | `open` (lines confirmed inactive), `unavailable`, `lost`, `line_fault` |
| `port_detail` | reason for the port state |
| `identity` | `{state, detail, reply}` — result of the radio identity check (see below); `reply` is the hex data the radio sent, or `null` |
| `tx` | `null`, or `{lease, source, frequency_hz, elapsed_s, remaining_s}` |
| `lockout` | `null`, or `{reason, since}`; cleared only by `tx_unlock` |
| `blockers` | list of reasons other than a lockout why TX cannot start |
| `duty` | `{used_s, available_in_s}` — transmission time in the current duty window and the wait until a new transmission fits |
| `limits` | `{lease_renew_interval_s, lease_timeout_s, hard_limit_s, duty_window_s, duty_max_tx_s}` |

### `civ`

```json
{"v": 1, "id": 1, "type": "civ", "frame": "fefe88e003fd"}
```

`frame` is one CI-V frame in hex, without whitespace. The guard accepts it only
if the message is **exactly one valid frame** (one `FE FE` preamble at the
start, one `FD` at the end, no `FE`/`FD` inside, destination = radio address,
source = controller address, length within bounds) **and** the command is on
the allowlist. Anything else is rejected in full and logged; it is never
trimmed or repaired.

Allowlist (stage 1; Icom numbering, UNVERIFIED on the G90):

| Command | Meaning | Body bytes | Refused during TX |
|---|---|---|---|
| `03` | read frequency | 0 | no |
| `04` | read mode | 0 | no |
| `05` | set frequency | 5 | **yes** |
| `06` | set mode | 1–2 | **yes** |
| `14 0A` | read RF power setting (sub-command only) | 1 | no |
| `14 0A` + 2 bytes | set RF power setting (D44); the value is not read — the limit `safety.max_rf_power_w` is the server's | 3 | yes |
| `14 01` | read / set the speaker volume (sub-command alone, or + 2 bytes) | 1 / 3 | no / **yes** |
| `15 02` | read S-meter (sub-command only, no data) | 1 | no |
| `15 13` | read ALC meter (sub-command only, no data; also during TX) | 1 | no |
| `15 11` | read RF power meter (sub-command only, no data) | 1 | no |
| `15 12` | read SWR meter (sub-command only, no data) | 1 | no |
| `26 00` | read the mode with its data flag; set it with exactly mode, data flag and filter | 1 / 4 | no / **yes** |
| `26 01` | read the unselected VFO's mode with its data flag | 1 | no |
| `07 00`, `07 01` | select VFO A, B (exact frames; no swap or copy) | 1 | **yes** |
| `0F` | read split | 0 | no |
| `0F 00`, `0F 01` | split off, on (in split the radio transmits on the other VFO) | 1 | **yes** |
| `25 00`, `25 01` | read the frequency of the selected / unselected VFO | 1 | no |
| `11` | read / set the attenuator (no data, or 1 data byte; the G90 answers a setting with `11 <state>`) | 0 / 1 | no / **yes** |
| `16 02` | read / set the preamplifier (sub-command alone, or + 1 byte) | 1 / 2 | no / **yes** |
| `16 12` | read / set the AGC (sub-command alone, or + 1 byte) | 1 / 2 | no / **yes** |
| `16 22` | read / set the noise blanker (sub-command alone, or + 1 byte) | 1 / 2 | no / **yes** |
| `14 02` | read / set the RF gain (sub-command alone, or + 2 bytes) | 1 / 3 | no / **yes** |
| `14 12` | read / set the noise blanker level (sub-command alone, or + 2 bytes) | 1 / 3 | no / **yes** |
| `1C 01` | read antenna tuner state | 1 | no |
| `1C 01 00`, `1C 01 01` | antenna tuner off, on (exact frames; `1C 01 02`, tuning, only through `atu_tune`) | 2 | **yes** |

`ok: true` means the frame was written to the port, not that the radio
answered. Replies arrive as `civ_rx` events. The echo of the frame just sent
(single-wire CI-V bus) is dropped by the guard.

A frame marked **yes** above (it changes the radio's state) is refused with `tx_active` while a lease is active **and while an antenna tuning lasts (until the radio's power meter reads zero twice, at least 3 s, at most 30 s; `atu_tune` below)**: the radio may still be sending its carrier, which cannot be stopped (D43; code review 2026-11-10, point 3). Reads are never refused.

Errors: `frame_invalid`, `command_not_allowed`, `tx_active`, `port_unavailable`.

### `atu_tune`

Starts an antenna tuning (`1C 01 02`, decisions.md D43). The tuning transmits a
carrier (1–1.5 s with a matched antenna, about 9 s with a poor one) and **cannot
be stopped**, so the guard checks first, as for `tx_request` (no lease, no
lockout, confirmed identity, duty cycle), with `safety.allow_atu_tune` instead
of `allow_transmit` on hardware, and also that the previous tuning **ended** at
least 10 s ago and that the longest tuning (30 s) fits into the duty cycle. It
then writes an `atu_tune` record to the TX log (`source`, `frequency_hz`,
`max_s`), counts 30 s in the duty history and sends the frame.

The tuning lasts until the guard sees **two power meter replies in a row with
the value 0** (`15 11 00 00`) from the radio, and at least 3 s. The guard reads
the replies to the server's own meter requests as they pass through (it sends
nothing itself; the replies still reach the server as `civ_rx`). Meanwhile no
other transmission starts (`tx_busy`) and frames that change the radio are
refused (`tx_active`); `status` shows `tuning: true`. At the end it writes
`atu_tune_end` (`duration_s`, `reason` `meter_zero`, `emitted`: a reading above
zero was seen) and replaces the 30 s in the duty history with the real
duration (at least 3 s). Still going after **30 s** (the radio still emitting,
or no meter replies — no server, port lost): `atu_tune_end` with `reason`
`overrun` and a durable lockout `atu_tune_overrun` until `tx_unlock`, and an
`atu_tune_overrun` event.

Fields: `source`, `frequency_hz` (as for `tx_request`). Answer: `max_s`,
`min_interval_s`. Errors: `bad_request`, `tx_busy`, `tx_locked`, `tx_blocked`,
`tx_log_failed`, `port_unavailable`.

### `tx_request`

```json
{"v": 1, "id": 2, "type": "tx_request", "source": "cli uid=1000 pid=4242", "frequency_hz": 7074000}
```

`source` (1–200 characters) and `frequency_hz` (integer or `null`) are recorded
in the TX log; the guard does not verify the frequency.

The guard keys PTT only if all of these hold:

- no transmission is active (otherwise `tx_busy`, also for the same source);
- no lockout (otherwise `tx_locked`);
- the port is open with lines confirmed inactive, the radio identity is
  confirmed, `ptt_line` is not `civ`, on hardware `safety.allow_transmit = true`,
  the duty cycle has room, and the transmission history was saved — after a
  failed save each request tries again first (otherwise `tx_blocked`; the
  history file is then emptied, so a restart also sees a full window);
- "transmission active" was durably written to the state directory
  (otherwise `state_write_failed`);
- the `tx_start` record was durably written to the TX log
  (otherwise `tx_log_failed`).

After keying, the line state is read back. If it does not match, the lines are
dropped, the lease ends with `line_mismatch` and the port is treated as lost.

Response:

```json
{"v": 1, "id": 2, "ok": true, "lease": "9f2c4e1a7b3d5e60",
 "lease_renew_interval_s": 0.5, "lease_timeout_s": 2.0, "hard_limit_s": 180.0,
 "remaining_s": 180.0}
```

`remaining_s` is the time until the hard limit or the duty cycle, whichever is
nearer, ends the transmission.

### `tx_renew`

```json
{"v": 1, "id": 3, "type": "tx_renew", "lease": "9f2c4e1a7b3d5e60"}
```

Renew at least every `lease_renew_interval_s`. A lease not renewed for
`lease_timeout_s` ends with `lease_expired`. Renewal never extends a
transmission past `hard_limit_s` or past the duty cycle (`duty_limit`). The server must renew only in response to a
renewal from the end client (AGENTS.md rule 5).

Response: `{"ok": true, "remaining_s": 172.5}` (as in `tx_request`). Error: `unknown_lease`
(wrong id, or the lease has already ended).

### `tx_release`

```json
{"v": 1, "id": 4, "type": "tx_release", "lease": "9f2c4e1a7b3d5e60"}
```

Ends the transmission (reason `released`). Error: `unknown_lease`.

### `tx_unlock`

No fields. Clears a lockout (hard limit, crash during TX, unreadable state
file). Refused during a transmission (`tx_busy`). Fails with
`state_write_failed` if the cleared state cannot be saved, in which case the
lockout stays. Response: `{"ok": true, "was_locked": true}`.

### `port_open`

No fields. After the port was lost (or was missing at start-up) the guard does
**not** reopen it by itself. This request opens it with the safe sequence
(lines low before and after opening, read back and confirmed). In the
`line_fault` state the port is still open; the request confirms the lines are
inactive again. Response: `{"ok": true, "port": "open"}`, or
`port_unavailable` with the reason. Opening the port includes the radio
identity check below.

## Radio identity

After every safe port open (at start-up and on `port_open`) the guard itself
sends `1D 19` (Xiegu model identity) and waits up to 1 s for the answer,
before it handles any other request (decisions.md D32). It compares the whole
body of the answer (everything after the command byte, as raw bytes) with
`radio.identity` from the configuration. The owner's G90 (firmware 1.81)
answers `1D 19 00 90`, so `radio.identity = "190090"`:

| `identity.state` | Meaning | Transmitting |
|---|---|---|
| `confirmed` | the answer equals `radio.identity` | allowed (if nothing else blocks it) |
| `mismatch` | another answer — another device or radio | blocked |
| `rejected` | the radio answered NG (firmware before 1.8?) | blocked |
| `no_reply` | no answer within 1 s | blocked |
| `not_configured` | `radio.identity` is not set; `reply` shows what the radio answered | blocked |
| `not_checked` | the port is not open | blocked |

CAT (`civ`) is not affected by the identity state. The answer is consumed by
the guard and not forwarded as `civ_rx`. After a port loss the state returns to
`not_checked`, and the next `port_open` checks again.

## Line watch

On every loop iteration the guard reads DTR/RTS back from the driver:

- **during a transmission** they must equal the PTT state; any difference ends
  the lease (`line_mismatch`) and the port is treated as lost;
- **without a transmission** both must be inactive. If one becomes active
  (for example because another process opened the device — the kernel raises
  DTR/RTS on open), the guard lowers it at once, keeps the port open so it can
  keep lowering it on every iteration (closing would not drop the lines while
  another descriptor is open), reports `line_fault` and blocks transmission
  until `port_open`.

The device is opened with `TIOCEXCL`, so other opens fail with `EBUSY`; the
kernel exempts root, hence the line watch.

## Events

Sent by the guard without a request:

| Event | Fields | When |
|---|---|---|
| `civ_rx` | `frame` (hex) | a valid frame from the radio to the controller or broadcast (`00`) |
| `tx_ended` | `lease`, `reason` | the transmission ended for any reason |
| `port_lost` | `message` | I/O error, device unplugged, or line mismatch |
| `line_fault` | `message` | a line became active without a transmission |
| `atu_tune_end` | `reason` (`meter_zero`, `overrun`), `duration_s`, `emitted` | an antenna tuning ended, for the guard; `emitted: false` — the power meter never showed power, so the radio did not tune (a G90 with its screen blanked tunes only when the screen comes on). Information only: no safety path depends on it. Added 2026-11-14 without a protocol version change (an older server logs it as unknown) |
| `atu_tune_overrun` | `message` | a tuning did not end within 30 s; transmit locked until `tx_unlock` |

`reason` is one of `released`, `lease_expired`, `hard_limit`, `duty_limit`,
`client_disconnected`, `port_lost`, `line_mismatch`, `shutdown`, `error`.

```json
{"v": 1, "event": "tx_ended", "lease": "9f2c4e1a7b3d5e60", "reason": "lease_expired"}
```

## Error codes

| Code | Meaning |
|---|---|
| `bad_request` | not JSON, not an object, missing/invalid `id`, unknown or invalid field |
| `unsupported_version` | `v` is not `1` |
| `unknown_type` | unknown `type` |
| `busy` | another client is already connected |
| `frame_invalid` | the message is not exactly one valid frame to the radio |
| `command_not_allowed` | valid frame, command not on the allowlist |
| `tx_active` | radio state change refused during transmission or an antenna tuning |
| `tx_busy` | a transmission is already active |
| `tx_locked` | lockout; `tx_unlock` required |
| `tx_blocked` | port not open or in `line_fault`, radio identity not confirmed, PTT via CI-V selected, transmit disabled, duty cycle full, or the guard is stopping |
| `unknown_lease` | no active lease with this id |
| `port_unavailable` | serial port not open or failed |
| `tx_log_failed` | TX log record could not be written; transmission refused |
| `state_write_failed` | state could not be saved |

## TX log

`guard.tx_log_path`, JSON Lines, each record flushed and fsynced:
`guard_start` (with `unclean_previous_shutdown`), `tx_start`, `tx_end`
(`source`, `frequency_hz`, `started`, `duration_s`, `reason`), `lockout`,
`tx_unlock`, `port_lost`, `line_fault`, `port_open`, `guard_stop`.
Every record has `ts`, `event` and `version` (the program version of the guard
that wrote it).

Every `tx_start` gets a matching `tx_end`. If the guard died during a
transmission (crash, SIGKILL, watchdog), the next start writes that `tx_end`
from the run marker, with `reason: "unclean_shutdown"` and
`duration_s: null` (the end time is unknown), before its `guard_start`.

## Duty cycle

The guard counts the transmission time within a sliding window of
`safety.duty_window_s` (at least 600 s). When it reaches `safety.duty_max_tx_s`
(at most 300 s), the active lease ends with `duty_limit` and `tx_request` is
refused with `tx_blocked` until enough old transmission time has left the
window — no `tx_unlock` is needed (decisions.md D31). The 180 s hard limit and
its lockout apply independently.

The history is kept in `tx_history.json` in the state directory (written
atomically with `fsync` after every transmission and at start-up, as UNIX
seconds). The window is treated as **full** — no transmission for
`duty_window_s` from start-up — if the file cannot be read, if it lies in the
future (the wall clock went back), if it cannot be saved, or if the start of a
transmission interrupted by a crash is unknown. A transmission interrupted by a
crash is counted until the restart.

## State directory

`guard.state_dir` holds `tx_history.json` (duty cycle, above), `lockout.json` (persistent lockout, written atomically
before PTT is released on the hard limit) and `running.json` (present while
the guard runs; records whether a transmission is active and, if so, its
lease, source, frequency and start time). Finding
`running.json` at start-up means an unclean shutdown; if it says a
transmission was active, or cannot be read, the guard starts locked.
If `lockout.json` cannot be written when a lockout arises, the lockout is
recorded in `running.json` (`pending_lockout`) and that file is kept even on a
clean shutdown, so the next start is still locked.
