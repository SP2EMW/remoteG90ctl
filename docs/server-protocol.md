# Server protocol (version 1)

Clients (the `remoteg90ctl` CLI and the browser) talk only to the
server. The server is the only client of the guard (see
[guard-protocol.md](guard-protocol.md)) and relays the transmit lease.

## Transport

- Stage 1: Unix stream socket at `server.socket_path`, mode `0660`. Members of
  the service group can connect. The peer's uid and pid (`SO_PEERCRED`) identify
  the client and are recorded as the transmission source in the TX log, e.g.
  `cli uid=1000 pid=4242 session=3fa2b19c`.
- JSON Lines, UTF-8, lines up to 4096 bytes; a longer line closes the
  connection. Requests on one connection are answered in order. Events may be
  interleaved between responses. Exception, on the browsers' WebSocket only:
  `tx_renew` and `tx_release` are answered at once, ahead of the requests still
  waiting (e.g. for the radio), so a slow request never holds a renewal back
  past the lease timeout; match responses by `id`.
- A client that stops reading for 2 s is disconnected.
- Stage 2b: HTTP and WebSocket for browsers on the TCP addresses in
  `server.listen` — loopback only until authentication exists (AGENTS.md
  rule 15); see "Browsers" below.

Messages have the same envelope as the guard protocol:

```json
{"v": 1, "id": 1, "type": "get_frequency"}
{"v": 1, "id": 1, "ok": true, "hz": 7074000}
{"v": 1, "id": 2, "ok": false, "error": "radio_timeout", "message": "the radio did not answer"}
```

## Requests

| Type | Fields | Response fields |
|---|---|---|
| `status` | — | `version` (server program version), `backend`, `guard_connected`, `guard` (guard status including its `version`, or `null`), `tx_active`, `tx_yours`, `iq` (I/Q input, below, or `null` without an `[iq]` section), `max_rf_power_w` (highest power `set_rf_power` accepts, or `null` when setting the power is off), `hardware_checklist` (`null` with the simulator; else `result`: `passed`, `skipped` or `missing` for the configured port, `skipped`: the items left out, `time`; written by `remoteg90ctl setup`, decisions.md D56) |
| `get_frequency` | — | `hz` |
| `set_frequency` | `hz` (positive integer) | — |
| `get_mode` | — | `mode`, with `U-D`/`L-D` for the data modes (read with `26 00`) |
| `get_atu` | — | `on` (antenna tuner on) |
| `set_atu` | `on` (boolean) | —; refused during a transmission (`tx_active`) |
| `get_front_end` | — | `front_end`: `off`, `pre` (preamplifier) or `att` (12 dB attenuator); one front-panel button on the G90, read with `11` and `16 02` |
| `set_front_end` | `state`: `off`, `pre` or `att` | `front_end` (read back from the radio); `front_end_mismatch` when the radio reports another state; refused during a transmission (`tx_active`) |
| `get_agc` | — | `agc`: `off`, `fast`, `slow` or `auto` (`16 12`; AGC-F, AGC-S, AGC-A on the radio) |
| `set_agc` | `agc`: `off`, `fast`, `slow` or `auto` | `agc` (read back); refused during a transmission (`tx_active`) |
| `get_nb` | — | `nb_on`, `nb_level` (menu step 0–10; `16 22` and `14 12`) |
| `set_nb` | `on` (boolean) and/or `level` (integer 0–10) | `nb_on`, `nb_level` (read back); `nb_mismatch` when the radio reports another state; refused during a transmission (`tx_active`) |
| `get_vfo` | — | `vfo` (`A`/`B`, selected), `other_hz` (the other VFO), `split` (boolean) |
| `set_vfo` | `vfo` (`A` or `B`) | as `get_vfo`, read back; with `radio.ssb_data_mode` USB/LSB on the newly selected VFO become U-D/L-D; `vfo_mismatch` when the radio reports another VFO; refused during a transmission |
| `set_split` | `on` (boolean) | as `get_vfo`, read back; switching on with `radio.ssb_data_mode` puts the other VFO from USB/LSB into U-D/L-D (it is selected for a moment for that); `vfo_mismatch`; refused during a transmission |
| `get_rf_gain` | — | `rf_gain` (1–100 %, `14 02`) |
| `set_rf_gain` | `percent` (integer 1–100) | `rf_gain` (read back); `rf_gain_mismatch` when the radio reports another value; refused during a transmission (`tx_active`) |
| `get_alc` | — | `alc`: `level` (0–255, `15 13`; 0 in receive), `display` (the number on the radio's display, from the measured scale in `server/alc.py`) |
| `get_s_meter` | — | `s_meter`: `level` (0–255, `15 02`), `db` (relative to S9, from the measured scale), `text` (`S0`–`S9`, `S9+10` …) |
| `atu_tune` | — | `max_s`, `min_interval_s`; the guard's `atu_tune` (D43): it lasts until the power meter reads zero twice (at least 3 s; over 30 s: lockout), the real time counts towards the duty cycle, the next one 10 s after the end, logged in the TX log; errors as for `tx_request`; from a browser `out_of_band` when the selected VFO is outside the bands of the account's ITU region (D54) |
| `get_bandplan` | — | `region` (ITU region of the account's locator, or `null`), `bands` (`[[low_hz, high_hz], …]`: the region's amateur allocations, the transmit limits), `segments` (`[{low, high, kind, note}, …]`: the IARU band plan, display only; `kind` one of `cw`, `narrow`, `all`, `beacon`, `satellite`, `fm`, `guard`) (D54) |
| `set_rf_power` | `watts` (number, 1 to `safety.max_rf_power_w`) | `setting`, `setting_watts` (read back from the radio); `rf_power_disabled` without `safety.max_rf_power_w`; `rf_power_mismatch` when the radio reports another level than the one set (a setting above the limit is then lowered to the minimum at once, D44); refused during a transmission (`tx_active`) |
| `get_power_meter` | — | `value` (meter level 0–255; 0 = no RF output), `watts` (from `radio.power_meter_watts`, or `null` without calibration), `above` (level beyond the last calibration point: power is more than `watts`), `raw` (reply frame, hex), `setting` (RF power setting level 0–255, `14 0A`), `setting_watts`, `setting_raw`, `swr_level` (SWR meter level 0–255, `15 12`), `swr_raw` |
| `set_mode` | `mode`: `LSB`, `USB`, `AM`, `CW`, `FM`, `CW-R`, `L-D`, `U-D` (case-insensitive); with `radio.ssb_data_mode` USB/LSB are set as U-D/L-D | — |
| `tx_request` | `dummy_load_confirmed` (boolean, default `false`); `antenna_confirmed` (boolean: browsers, and the CLI's test on the antenna, D56) | `lease`, `lease_renew_interval_s`, `lease_timeout_s`, `hard_limit_s` |
| `tx_renew` | `lease` | `remaining_s`; for a browser's lease with the sound card open also `audio`: `margin_ms` (the server's adaptive margin), `queued_ms` (voice waiting for the card), `card_ms` (the card's output latency, or `null`), `ran_dry`, `dropped`, `played` (counted for this lease) |
| `tx_release` | `lease` | — |
| `ft8_transmit` | `lease` (this browser's), `mode` (`FT8`/`FT4`), `message` (the whole message, e.g. `DL1XYZ SP1ABC R-07`), `tone_hz` (lowest tone; the signal must stay within 200–2800 Hz), `level_db` (−40…0 dBFS, default −10), `start_ms` (UTC start of the cycle to send in) | `message` (as sent), `duration_s`; the server makes the signal (ft8lib) and plays it from 0.5 s into the cycle, through the limiter, only within the lease; then `ft8_sent`. Browser only (`forbidden` otherwise); `clock_not_synchronized`, `too_late` (later than the others could still decode it: 1.5 s after the transmission's usual start for FT8, 0.8 s for FT4), `tx_busy`, `invalid_value` (decisions.md D53) |
| `rtty_start` | `lease` (this browser's), `mark_hz` (the mark tone; space is 170 Hz above it; 300–2530 Hz, so that the whole signal stays within 200–2800 Hz), `reverse` (boolean, default `false`: mark is then the higher tone), `level_db` (−40…0 dBFS, default −10), optional `text` | `waiting` (characters queued); the server keys RTTY (45.45 baud, 1.5 stop bits, Baudot) through the limiter, only within the lease: 0.3 s of mark, two LTRS, the text, and LTRS while nothing waits, until `rtty_end`. Browser only (`forbidden` otherwise); `tx_busy` (FT8/FT4 or RTTY already playing), `invalid_value` (decisions.md D61) |
| `rtty_text` | `lease`, `text` (A–Z 0–9, space, new line and `- ? : ( ) . , ' / " $ ! & # ;`; lower case is raised) | `waiting`; at most 1000 characters may wait. `invalid_value` for other characters (nothing of the text is taken), with no RTTY transmission on this lease, or after `rtty_end` |
| `rtty_end` | `lease` | `waiting`; what waits is sent, then 0.2 s of mark, then `rtty_done` (the page then releases the lease). A released or ended lease stops the sound at once and drops what waits |
| `tx_unlock` | — | `was_locked` |
| `get_tx_output` | — | `db` (the sound card's output level now, or `null` if `amixer` did not answer), `min_db`, `max_db`; `audio_unavailable` without a card for the radio |
| `set_tx_output` | `db` | the level the card then has (`db`, `min_db`, `max_db`), kept on the server and set again whenever the card opens; allowed while transmitting (it drives the radio after the limiter: tuned watching the ALC); a `tx_output` event (`db`) goes to every page |
| `server_restart` | — | `restart_in_s`; then the server ends every session (a transmission would end as `server_shutdown`) and exits with status 75, and systemd starts it again (`Restart=on-failure`). Refused with `tx_busy` while a lease is active or the radio transmits. The page's "restart server" button in the resources window |
| `port_open` | — | `port` |
| `iq_record` | `seconds` (0 < s ≤ 30) | `path`, `sample_rate`, `sample_bits`; records raw I/Q to a WAV file on the server (measurement tool, `AGENTS.md` section 8) |

Mode codes and the set of modes the G90 accepts are UNVERIFIED (AGENTS.md
section 8). Changing frequency or mode during a transmission is refused
(`tx_active`).

`set_frequency` requests are coalesced: one CAT command is sent at a time, and
requests arriving meanwhile are replaced by the newest one, which is always
sent. Every request is answered with the outcome of the command that carried
its value or a newer one.

## Transmit lease

- **The radio is held while a transmission starts** (AGENTS.md rule 17, code
  review 2026-11-10): from the check of the frequency and mode in `tx_request`
  until after the lease is granted — and for `atu_tune` from its reading of the
  frequency to the guard's start of the tuning — `set_frequency`, `set_mode`,
  `set_vfo` and `set_split` are answered `tx_busy` ("held for a moment"). After
  the grant the server reads the transmit frequency and mode once more (the
  guard refuses every change from then on); a browser's lease that is outside
  the band, not in U-D/L-D, or whose radio cannot be read at that moment is
  ended at once and the request answered with the refusal.

- `tx_request` is granted to at most one client at a time. The `lease` is a
  server token; the guard's own lease id is never exposed.
- On `backend = "hardware"`, `tx_request` requires `dummy_load_confirmed: true`
  (the CLI test, decisions.md D15) or `antenna_confirmed: true`, and the guard
  additionally requires `safety.allow_transmit = true`. From the CLI,
  `antenna_confirmed` without a dummy load is the PTT test on the antenna
  (D56): refused unless the radio is in SSB (`not_data_mode`), the whole signal
  (3 kHz) lies in a band all three ITU regions have (`out_of_band`) and the set
  power is at most 1 W (`power_too_high`); checked before the guard grants the
  lease and again right after, like a browser's request.
- **Split** (measured 2026-11-08): the G90 then transmits on the
  unselected VFO, in that VFO's mode. Before every `tx_request` the server
  reads split from the radio (never from its cache); in split it takes the
  transmit frequency from `25 01` and the mode from `26 01`. That frequency
  goes to the TX log (AGENTS.md rule 7), and a browser is refused with
  `not_data_mode` unless the transmit VFO is in U-D/L-D. A failed read refuses
  a browser's request.
- The client must send `tx_renew` at least every `lease_renew_interval_s`. The
  server renews at the guard **only** when it receives a renewal from the
  client that owns the lease (AGENTS.md rule 5).
- A client that stays connected but stops renewing loses the transmission after
  `lease_timeout_s` (the server and the guard each enforce this independently).
- A client that disconnects loses the transmission immediately.
- The transmission never lasts longer than `hard_limit_s`; then transmit is
  locked until `tx_unlock`.

## Events

| Event | Fields | Sent to |
|---|---|---|
| `tx_ended` | `lease`, `reason` | the owner of the transmission |
| `ft8_sent` | `lease`, `start_ms` | the owner, when the whole FT8/FT4 signal has been played (the page then releases the lease) |
| `rtty_sent` | `lease`, `sent` (characters of the text keyed out so far), `waiting` | the owner, whenever another RTTY character is out |
| `rtty_done` | `lease`, `sent` | the owner, when the RTTY transmission ended as asked by `rtty_end` |
| `port_lost` | `message` | all clients |
| `line_fault` | `message` | all clients |
| `atu_tune_end` | `reason`, `duration_s`, `emitted` | all clients; an antenna tuning ended for the guard. The page warns in red when `emitted` is false with `reason` `meter_zero`: the radio did not tune (owner's decision 2026-11-14) |
| `atu_tune_overrun` | `message` | all clients; transmit locked until `tx_unlock` |
| `guard_disconnected` | — | all clients |
| `guard_connected` | — | all clients |
| `radio_atu` | `on` | all clients; when the antenna tuner is switched (read every 1 s while a browser watches) |
| `radio_front_end` | `front_end` | all clients; when the preamplifier/attenuator state changes (read every 1 s while a browser watches) |
| `radio_agc` | `agc` | all clients; when the AGC setting changes (read every 1 s while a browser watches) |
| `radio_alc` | `level`, `display` (as in `get_alc`) | all clients; when the level changes (read every 0.5 s with the VFO while a browser watches, only during a transmission — ours or seen on the power meter) |
| `radio_s_meter` | `level`, `db`, `text` (as in `get_s_meter`) | all clients; when the level changes (read every 0.5 s with the VFO while a browser watches, not during a transmission) |
| `radio_vfo` | `vfo`, `other_hz`, `split` (as in `get_vfo`) | all clients; when one changes (the VFOs every 0.5 s, split every 1 s while a browser watches, and before every transmission) |
| `radio_rf_gain` | `rf_gain` | all clients; when the RF gain changes (read every 1 s while a browser watches) |
| `radio_nb` | `nb_on`, `nb_level` | all clients; when the noise blanker state or level changes (read every 1 s while a browser watches) |
| `radio_power` | `value`, `watts`, `above`, `setting`, `setting_watts`, `swr_level` (as in `get_power_meter`), `swr_high` (two readings in a row at the end of the SWR scale while emitting — SWR about 2 or more; kept until a later transmission shows less; also logged as `high_swr`) | all clients; when the meter level or the power setting changes (both read every 1 s) |
| `radio_transmitting` | `active` (a lease is active or the power meter shows output), `leased` (a transmission through this server is active) | all clients; when a lease starts or ends, when the radio's power meter starts showing output, and 3 s after it stops (decisions.md D33, informational only); `active` without `leased` is also logged as `tx_without_lease`; output within 2 s after this server's lease ends counts as that lease's (the meter lags) |
| `radio_tx_suspected` | `active` | all clients; when the S-meter, read every 0.5 s while a browser watches and this server does not transmit, falls from S1 or more to 242 (below its scale) on the same frequency and stays there 1 s — the G90 mutes it while transmitting, also without output (measured 2026-11-08) — and when that ends. Informational: a removed antenna or the RF gain turned down look the same |
| `radio_state` | `hz`, `mode`, `passband_hz` (configured `[low, high]` Hz around the dial for the mode, or `null`) | all clients; when the known frequency or mode changes (CAT requests, and every 0.5 s while a browser is connected, with or without the waterfall) |

`reason` is one of the guard's reasons (`released`, `lease_expired`,
`hard_limit`, `port_lost`, `line_mismatch`, `shutdown`, `error`, …) or a
server reason: `client_silent`, `guard_disconnected`, `server_shutdown`,
`ft8_playback_failed` (an FT8/FT4 signal could not be played to the end: the card took no audio for a second) and `audio_unavailable` (the sound card did not open for a browser's lease).

## Error codes

Server codes: `bad_request`, `unsupported_version`, `unknown_type`,
`out_of_band` (a browser's signal would leave the bands of its account's ITU
region, or the region is unknown, D54),
`invalid_value`, `guard_unavailable`, `dummy_load_required`, `unknown_lease`,
`tx_busy`, `clock_not_synchronized`, `too_late`, `rf_power_disabled`, `rf_power_mismatch`, `front_end_mismatch`, `nb_mismatch`, `rf_gain_mismatch`, `vfo_mismatch`, `radio_timeout`, `radio_rejected` (radio answered NG),
`radio_bad_reply`, `forbidden` (the client lacks the permission the request
needs), `iq_unavailable` (no `[iq]` section, the I/Q input is not running, or a
recording is already in progress). Guard codes are passed through unchanged (`tx_locked`,
`tx_blocked`, `tx_active`, `port_unavailable`, `tx_log_failed`, …; see
[guard-protocol.md](guard-protocol.md)).

## Permissions

Every request type needs one permission (AGENTS.md rule 16; `REQUEST_PERMISSIONS`
in `server/protocol.py`); a request without it is answered with `forbidden`:

| Permission | Requests |
|---|---|
| `receive` | `status`, `get_frequency`, `get_mode`, `get_power_meter`, `get_atu`, `get_front_end`, `get_agc`, `get_nb`, `get_rf_gain`, `get_s_meter`, `get_alc`, `get_vfo`, `get_tx_output`, `cluster_subscribe`, `cluster_unsubscribe` |
| `control` | `set_frequency`, `set_mode`, `set_atu`, `set_front_end`, `set_agc`, `set_nb`, `set_rf_gain`, `set_rf_power`, `set_vfo`, `set_split` |
| `accounts` | `account_list`, `account_password`, `account_delete` (CLI only, D48) |
| `log` | `log_list`, `log_worked`, `log_add`, `log_update`, `log_delete` (an account's own log, D50) |
| `tune` | `atu_tune` (an antenna tuning transmits a short carrier, decisions.md D43) |
| `transmit` | `tx_request`, `tx_renew`, `tx_release`, `ft8_transmit`, `rtty_start`, `rtty_text`, `rtty_end`, `tx_unlock`, `port_open`, `set_tx_output` |
| `admin` | `server_restart`, `iq_record` (fills the disk), `log_subscribe`, `log_unsubscribe` (the server's log shows logins), `cluster_config`, `cluster_set` (which DX cluster, and with which callsign) — the administrator, i.e. the first account, and the CLI |

Clients of the Unix socket (members of group `remoteg90ctl`) have every
permission, `accounts` included (decisions.md D24.11). A browser has none until
it logs in to an account, then `receive`, `control`, `tune` and `transmit`
(D48, "Accounts and login" below). Each session carries a client context
(`kind`, session id, uid/pid of the CLI process, the account, permissions); the
account goes into the TX log's `source`.

## I/Q input (`status.iq`)

`source`, `running`, `error` (why it is not running, or `null`), `sample_rate`,
`sample_bits`, `fft_size`, `line_rate`, `lines` (waterfall lines computed so
far), `idle` (`true` while no browser watches the waterfall: the spectrum is
then not computed, to save CPU), `level_dbfs` (mean power of the last block,
after correction; while idle straight from the card),
`overruns` (card overflows), `dropped_blocks` (blocks dropped because the
server fell behind), `center_offset_hz` and `calibrated` (`false` until the
centre offset has been measured and configured). A source that cannot be
opened, or stops delivering, is retried every 5 s; the page shows `error` over
the waterfall and asks for `status` again until lines come.

With `[iq] source = "none"` there is no I/Q input and no waterfall
(`waterfall_subscribe` answers `iq_unavailable`); `status.iq` is then only
`source` (`"none"`), `running` (`false`), `error` (`null`), `sample_rate` and
`passband_hz`, and the page shows the frequency scale alone, `sample_rate` wide.
Without an `[iq]` section `status.iq` is `null` and the page does the same,
96 kHz wide.

## Browsers (HTTP and WebSocket)

`GET /` serves the page, `/static/` its files, `/ws` is the WebSocket.

**Same origin only (decisions.md D34).** Every request must carry a `Host` that
addresses this server (`127.0.0.1:PORT`, `[::1]:PORT` or `localhost:PORT` for a
port in `server.listen`); a request with an `Origin` must carry exactly
`http://<that host>`; a WebSocket without `Origin` is refused. Refusals answer
`403` and are logged (`web_request_refused`). Any page open in the browser could
otherwise open `ws://127.0.0.1:…`, and a foreign `Host` is the mark of DNS
rebinding.
Under an HTTPS proxy on loopback (`tailscale serve`, decisions.md D38) the names
in `server.allowed_hosts` are accepted as `Host`, only with an `https://` `Origin`
and only with Tailscale's identity header `Tailscale-User-Login` (decisions.md
D57): `tailscale serve` adds it for tailnet users and removes a client's copy,
while Tailscale Funnel serves the same name to the internet without it. Without
it the answer is `403` (`web_request_refused`, `reason="no Tailscale identity"`)
unless `server.require_tailnet_identity = false`. A request with
`X-Forwarded-Host` (set by `tailscale serve` on everything it proxies, while the
client's `Host` passes unchanged) is a proxied one: under a loopback `Host` it is
refused (`reason="proxied request without the tailnet name"`), and it needs the
identity like the tailnet name. A request with `Tailscale-Funnel-Request` is
always refused.

**Permissions.** A browser session has `receive` and `control` — never
`transmit`: every transmit request from a browser is answered with
`forbidden`.

**Text messages** on `/ws` are the requests, responses and events of this
protocol, as JSON (one message per WebSocket frame). These requests exist only
here:

| Type | Fields | Response fields |
|---|---|---|
| `waterfall_subscribe` | `bins`: power of two from 256 to `iq.fft_size`; optional `speed`: `fast` (default), `medium`, `slow`, `slower` or `slowest` | `bins`, `speed`, `line_rate` (lines per second actually sent); binary waterfall frames follow |
| `audio_subscribe` | `codec` (`opus` or `raw`), `bitrate` (8, 16, 24, 32 or 48 kbit/s for Opus), optional `complexity` (the Opus encoder's 0-10, default 10: lower costs the server less CPU) | `codec`, `bitrate`, `sample_rate` (48000); WebSocket only; one listener for the whole server, a second gets `audio_busy` |
| `audio_unsubscribe` | — | — |
| `waterfall_unsubscribe` | — | — |
| `system_subscribe` | — | `interval_s`; `system_stats` events follow, the first at once |
| `system_unsubscribe` | — | — |
| `log_subscribe` | — | (permission `admin`) `lines`: the server's last 500 log lines of level INFO and above, oldest first (key=value, as in the journal); then a `log_line` event (`line`) for each new one |
| `log_unsubscribe` | — | — |

## Station log (decisions.md D50)

Each account has its own log; a client without an account (the CLI) gets
`no_log`. The log never talks to the radio. A QSO has `call`, `freq_hz`
(hertz), `mode` (`SSB`, `USB`, `LSB`, `CW`, `AM`, `FM`, `FT8`, `FT4`),
`rst_sent`, `rst_rcvd`, `name`, `qth`, `gridsquare`, `comment`, and `qso_date`
(`YYYYMMDD`) and `time_on` (`HHMM[SS]`), both UTC and set to the moment of
saving when left out; the server adds `id`, `band` and `dupe` (same call, band
and mode on the same UTC day).

| Request | Fields | Answer |
|---|---|---|
| `log_list` | `search` (part of a call), `limit` (≤ 500) | `qsos` (newest first), `total` |
| `log_worked` | `call` | `worked`: the last five QSOs with it |
| `log_add` | the QSO fields | `qso` |
| `log_update` | `qso_id` and the QSO fields | `qso` |
| `log_delete` | `qso_id` | — (`unknown_qso` if not in this log) |

After a change the account's pages get a `log_changed` event. ADIF over HTTP,
with the session cookie: `GET /api/logbook.adi` (ADIF 3.1.5, with
`STATION_CALLSIGN` and `MY_GRIDSQUARE` from the account) and
`POST /api/logbook/import` (the file as the body, ≤ 5 MB) → `added`,
`skipped` (already in the log), `failed` (not readable).

## DX cluster (decisions.md D63)

The spots come from the `remoteg90ctl-cluster` process
([cluster-protocol.md](cluster-protocol.md)); the server itself never reaches
the network. A spot has `spotter`, `dx`, `freq_khz`, `comment`, `utc`
(`HHMM`), `locator` (or `null`) and `received_ms`; the server keeps the last
hour. A spot never tunes the radio by itself: the page tunes with
`set_frequency` and `set_mode`.

| Request | Fields | Answer |
|---|---|---|
| `cluster_subscribe` | — | `spots` (the last hour, oldest first), `status` (`state`, `message`), `solar` (the last WWV/WCY report of the last six hours, kept across restarts, or `null`); then `cluster_spot` (`spot`), `cluster_status` and `cluster_solar` (`solar`: `kind`, `source`, `hour`, `sfi`, `a`, `k`, `received_ms`) events |
| `cluster_unsubscribe` | — | — |
| `cluster_config` | — | (permission `admin`) `host`, `port`, `call` (all `null` when none is set), `status` |
| `cluster_set` | `host`, `port`, `call`; `host` `null` or empty: none | (permission `admin`) — |

`status.state` is the process's (`off`, `connecting`, `connected`,
`retrying`), or `unavailable` while the server cannot reach the process.

## Accounts and login (decisions.md D48)

A browser logs in to a station account over plain HTTP JSON routes, then opens
`/ws` with the session cookie (`remoteg90ctl_session`; 30 days, `HttpOnly`,
`SameSite=Strict`, `Secure` over HTTPS). `POST` and `DELETE` need our own
`Origin` (D34). Errors: `{"ok": false, "error", "message"}` with an HTTP status.

| Route | Body | Answer |
|---|---|---|
| `GET /api/session` | — | `first_account_needed`, `accounts_unusable`, `account` (`callsign`, `locator`, `admin`) or null, `busy` (the callsign with pages open, when it is another account); for the administrator also `busy_since` (UTC seconds since that account's pages are open) and `busy_transmitting` (whether it holds the transmit lease) |
| `POST /api/first-account` | `callsign`, `locator`, `password` | `account`, session cookie; only while there is no account (else 403 `forbidden`) |
| `POST /api/login` | `callsign`, `password` | `account`, session cookie; 401 `login_failed`, 429 `login_throttled` (after 5 failures, up to 60 s), 409 `account_busy` (another account has pages open; not for the administrator, who logs in but whose page does not open until it takes over) |
| `POST /api/take-over` | — | administrator only (403 `forbidden` otherwise): logs out every other account with a session, revokes its sessions and closes its pages (their leases end); the administrator's own sessions stay |
| `POST /api/logout` | — | the session ends, its pages close (and their lease) |
| `POST /api/password` | `old_password`, `new_password` | new session cookie; every other session of the account ends |
| `GET /api/accounts` | — | `accounts` (administrator only) |
| `POST /api/accounts` | `callsign`, `locator`, `password` | `account` (administrator only, 403 `forbidden` otherwise) |
| `DELETE /api/accounts/{callsign}` | — | deletes it and closes its pages (administrator only; it cannot delete itself) |
| `GET /api/settings` | — | `page`: the page's own settings kept on the account (string keys and values) |
| `PUT /api/settings` | `page` (at most 100 strings, 32 KiB) | — |

**Radio settings on the account.** While an account has pages open, the server
saves its radio settings every 2 s when they changed (VFO, frequency, mode,
split, PRE/ATT, AGC, NB, RF gain, ATU, power in whole watts). When the
account's first page opens, they are set again through the ordinary requests
of that page — with its permissions, refused during a transmission, the power
never above `safety.max_rf_power_w` — and the page gets a
`radio_settings_restored` event (`failed`: the steps that did not work).
Nothing is saved before the restore ends, so logging in does not overwrite them.

`/ws` without a valid session is refused with 401, and with 409 while another
account has pages open. A logged-in page gets `receive`, `control`, `tune` and
`transmit`. Open pages are checked against their session every 30 s; an ended
session closes them, which ends their transmit lease (AGENTS.md rule 16).

The CLI (Unix socket) manages accounts with the `accounts` permission, which
browsers never have:

| Request | Fields | Answer |
|---|---|---|
| `account_list` | — | `accounts`, `broken` (why the accounts file is unusable, or null) |
| `account_password` | `callsign`, `password` | — (every session of the account ends) |
| `account_delete` | `callsign` | — (not the administrator) |

**System log window.** The server keeps its last 500 log lines of level INFO
and above in memory (`server/logbuffer.py`; DEBUG lines with every CI-V frame
stay out) and sends new ones only to browsers with the window open. Permission:
`admin` (only the administrator's account; AGENTS.md rule 16, D48).

**Resource window.** While a browser is subscribed, the server reads `/proc`
every `interval_s` (2 s) and sends it a `system_stats` event; nothing is read
while no browser is subscribed, and a closed connection ends the subscription.
Reading only — no process is signalled or changed.

| Field | Content |
|---|---|
| `processes` | list of `{name, pid, cpu_percent, rss_bytes, threads, error}` for `server` and `guard`, and `tailscaled` while Tailscale's daemon runs (found by name in `/proc`); `cpu_percent` is the share of **one** core since the previous event (`null` in the first event), `rss_bytes` the resident memory; `error` is `"not connected"` (guard pid unknown) or `"cannot read /proc"`, and the other values are then `null`. The guard's pid comes from the credentials of the server's guard connection (`SO_PEERCRED`) |
| `system` | `{cpu_percent, cpus, load, mem_total_bytes, mem_available_bytes, disk, clock_synchronized, cpu_temperature}` for the whole machine (`cpu_percent` over all cores, `null` in the first event; `load` = 1, 5, 15 min load average; `disk` = `{path, free_bytes, total_bytes}` of the file system holding the server's state directory; `clock_synchronized` as in `status.audio`; `cpu_temperature` = `{celsius, source}` from the CPU's thermal zone — `x86_pkg_temp`, `cpu-thermal` and similar — or the `coretemp` sensor, `null` without one), or `null` if unreadable |

**Binary frames**, version 1, little-endian, one per waterfall line:

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | frame type, `0x01` = waterfall line |
| 1 | u8 | flags: bit 0 = centre frequency calibrated (offset known for the mode); bit 1 = blanked (taken while the radio transmits or just after, not a receive line) |
| 2 | u16 | number of bins `n` |
| 4 | u32 | line sequence number |
| 8 | f64 | centre frequency in Hz (CAT frequency + `iq.center_offset_hz` of the mode), NaN if unknown |
| 16 | f32 | span in Hz (the sample rate) |
| 20 | f32 | dB value of byte 0 |
| 24 | f32 | dB value of byte 255 |
| 28 | u8[n] | bins from the lowest to the highest frequency |

Blanked lines are still sent; the page draws them dimmed and leaves them out of
its automatic levels.

Bins are merged by their maximum when fewer are requested than the FFT has, so
narrow carriers stay visible.

`speed` sets how many consecutive lines become one sent line: `fast` every line
(`iq.line_rate` per second), `medium` 2, `slow` 4, `slower` 10, `slowest` 20
(the page shows them as lines per second). Merged lines take the
maximum of each bin over the merged lines, so a short signal is not lost; a
merged line is blanked when any of its lines was, and carries the sequence
number and centre frequency of its last line. Sending `waterfall_subscribe`
again replaces the subscription (e.g. to change the speed); `waterfall_unsubscribe`
stops the lines altogether (the page's "off" speed, to save the link). A client that does not keep up loses lines: each
has a queue of a few lines and the oldest is dropped when it is full.



## Receive audio frames

Binary WebSocket messages after `audio_subscribe`, one per 20 ms, 48 kHz mono:

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | frame type, `0x02` = audio |
| 1 | u8 | codec: 0 raw PCM (signed 16-bit little-endian), 1 Opus |
| 2 | u16 | samples in the frame (960) |
| 4 | u32 | sequence number |
| 8 | bytes | payload: 960 samples, or one Opus packet |

Before frame 0 and then every 50 frames (once a second) comes a **clock frame**
(decisions.md D52), 16 bytes:

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | frame type, `0x04` = clock |
| 1 | u8 | flags: bit 0 the server's clock is synchronised (NTP), bit 1 unknown |
| 2 | u16 | 0 |
| 4 | u32 | sequence number of the next audio frame |
| 8 | f64 | UTC time (seconds) of that frame's first sample, by the server's clock |

The time is when the card's block reached the server, less its length, smoothed
over the arrival jitter (the card's own delay is not included). The page cuts
FT8/FT4 cycles out of the audio by it. `status.audio.clock_synchronized` is the
same flag, checked once a minute. It comes from chrony first
(`chronyc -c tracking`): synchronised only while the clock's error bound,
|offset| + root delay / 2 + root dispersion, is at most 0.2 s; without chrony
from `timedatectl`, and without D-Bus from the file systemd-timesyncd creates
after its first synchronisation; `null` when none of them answers.
`ft8_transmit` is refused (`clock_not_synchronized`) unless it is `true`.

The listener's queue holds 0.5 s and drops the oldest frame when full. Errors:
`audio_busy` (another browser listens), `audio_unavailable` (no `[audio]`
section, or no libopus), `invalid_value`.


`system_stats` also carries `network`: bytes over the WebSocket for this
browser (`browser`) and for all browsers since the server started (`server`),
each split into `waterfall`, `audio`, `messages` (sent) and `received`, plus
`connected_s`. The page computes rates from consecutive samples.


## Voice from the browser

A browser's `tx_request` is also refused with `out_of_band` unless the whole
signal — 3 kHz above (U-D) or below (L-D) the transmit frequency — lies in one
amateur band of the ITU region of its account's locator (D54).
A browser's `tx_request` is answered `audio_unavailable` when the sound card did not open (the lease is not kept: it would key the radio without sound).
`tx_request` takes `antenna_confirmed` (browsers, instead of
`dummy_load_confirmed` on hardware); a browser's request is refused with
`not_data_mode` unless the radio is in U-D or L-D. While it holds the lease
the browser sends binary frames with the receive-audio header and type
`0x03`: codec 0 raw PCM s16le or 1 Opus, 960 samples (20 ms at 48 kHz). The
server plays them, through its limiter, only during that browser's lease;
other frames are dropped. Permission: `transmit` (D48).
