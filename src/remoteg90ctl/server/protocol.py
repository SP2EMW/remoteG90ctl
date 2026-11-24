# SPDX-License-Identifier: GPL-3.0-or-later
"""Server ↔ client wire protocol: versioned JSON Lines (docs/server-protocol.md).

Independent of the transport: the Unix socket (stage 1) and later the WebSocket
carry the same messages.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 4096
MAX_ID_LENGTH = 64

RequestId = int | str | None

# Errors produced by the server itself; guard error codes are passed through.
BAD_REQUEST = "bad_request"
UNSUPPORTED_VERSION = "unsupported_version"
UNKNOWN_TYPE = "unknown_type"
INVALID_VALUE = "invalid_value"
GUARD_UNAVAILABLE = "guard_unavailable"
DUMMY_LOAD_REQUIRED = "dummy_load_required"
NOT_DATA_MODE = "not_data_mode"
OUT_OF_BAND = "out_of_band"
# The CLI's PTT test on the antenna with the radio set above the low power (D56).
POWER_TOO_HIGH = "power_too_high"
CLOCK_NOT_SYNCHRONIZED = "clock_not_synchronized"
TOO_LATE = "too_late"
UNKNOWN_LEASE = "unknown_lease"
TX_BUSY = "tx_busy"
FORBIDDEN = "forbidden"
IQ_UNAVAILABLE = "iq_unavailable"
IQ_OFF = 'no I/Q input: no [iq] section, or [iq] source = "none"'
AUDIO_UNAVAILABLE = "audio_unavailable"
AUDIO_BUSY = "audio_busy"
# Setting the RF power (D44): no safety.max_rf_power_w, or the radio reports
# another level than the one set.
RF_POWER_DISABLED = "rf_power_disabled"
RF_POWER_MISMATCH = "rf_power_mismatch"
# The radio reports another preamplifier/attenuator state than the one set.
FRONT_END_MISMATCH = "front_end_mismatch"
# The radio reports another noise blanker state or level than the one set.
NB_MISMATCH = "nb_mismatch"
# The radio reports another RF gain than the one set.
RF_GAIN_MISMATCH = "rf_gain_mismatch"
# The radio reports another VFO or split state than the one set.
VFO_MISMATCH = "vfo_mismatch"
# The radio reports the tuner in another state than the one set.
ATU_MISMATCH = "atu_mismatch"
# No accounts on this server (no web server configured).
ACCOUNTS_UNAVAILABLE = "accounts_unavailable"
# A log request from a client without an account (the CLI), or no log here.
NO_LOG = "no_log"
# Preamplifier / attenuator states (one front-panel button on the G90).
FRONT_END_STATES = ("off", "pre", "att")

REQUEST_FIELDS: dict[str, frozenset[str]] = {
    "status": frozenset(),
    "get_frequency": frozenset(),
    "set_frequency": frozenset({"hz"}),
    "get_mode": frozenset(),
    "get_power_meter": frozenset(),
    "get_atu": frozenset(),
    "get_af": frozenset(),
    "set_af": frozenset({"level"}),
    "set_atu": frozenset({"on"}),
    "get_front_end": frozenset(),
    "set_front_end": frozenset({"state"}),
    "get_agc": frozenset(),
    "set_agc": frozenset({"agc"}),
    "get_nb": frozenset(),
    "set_nb": frozenset({"on", "level"}),
    "get_rf_gain": frozenset(),
    "get_s_meter": frozenset(),
    "get_alc": frozenset(),
    "set_rf_gain": frozenset({"percent"}),
    "get_vfo": frozenset(),
    "set_vfo": frozenset({"vfo"}),
    "set_split": frozenset({"on"}),
    "atu_tune": frozenset(),
    "set_rf_power": frozenset({"watts"}),
    "set_mode": frozenset({"mode"}),
    "tx_request": frozenset({"dummy_load_confirmed", "antenna_confirmed"}),
    "tx_renew": frozenset({"lease"}),
    "tx_release": frozenset({"lease"}),
    "ft8_transmit": frozenset({"lease", "mode", "message", "tone_hz", "level_db", "start_ms"}),
    "rtty_start": frozenset({"lease", "mark_hz", "reverse", "level_db", "text"}),
    "rtty_text": frozenset({"lease", "text"}),
    "rtty_end": frozenset({"lease"}),
    "tx_unlock": frozenset(),
    "server_restart": frozenset(),
    "get_tx_output": frozenset(),
    "set_tx_output": frozenset({"db"}),
    "port_open": frozenset(),
    "iq_record": frozenset({"seconds"}),
    # WebSocket only (docs/server-protocol.md): binary waterfall lines.
    "waterfall_subscribe": frozenset({"bins", "speed"}),
    "waterfall_unsubscribe": frozenset(),
    "audio_subscribe": frozenset({"codec", "bitrate", "complexity"}),
    "audio_unsubscribe": frozenset(),
    # WebSocket only: the resource window (CPU and memory of the radio processes).
    "system_subscribe": frozenset(),
    "system_unsubscribe": frozenset(),
    "log_subscribe": frozenset(),
    "log_unsubscribe": frozenset(),
    "account_list": frozenset(),
    "account_password": frozenset({"callsign", "password"}),
    "account_delete": frozenset({"callsign"}),
    "log_list": frozenset({"search", "limit"}),
    "log_worked": frozenset({"call"}),
    "log_add": frozenset(
        {
            "call",
            "freq_hz",
            "mode",
            "rst_sent",
            "rst_rcvd",
            "name",
            "qth",
            "gridsquare",
            "comment",
            "qso_date",
            "time_on",
        }
    ),
    "log_update": frozenset(
        {
            "call",
            "freq_hz",
            "mode",
            "rst_sent",
            "rst_rcvd",
            "name",
            "qth",
            "gridsquare",
            "comment",
            "qso_date",
            "time_on",
        }
        | {"qso_id"}
    ),
    "log_delete": frozenset({"qso_id"}),
    "get_bandplan": frozenset(),
}


class Permission(StrEnum):
    """What a client may do (AGENTS.md rule 16); enforced per request."""

    RECEIVE = "receive"  # listen, waterfall, read state
    CONTROL = "control"  # frequency, mode
    TRANSMIT = "transmit"  # transmit, unlock, reopen the port (D24.11)
    # Start an antenna tuning: a short carrier, but no PTT (decisions.md D43).
    TUNE = "tune"
    # List accounts, reset a password, delete an account: the CLI on this machine
    # only (members of the remoteg90ctl group); never a browser (D48).
    ACCOUNTS = "accounts"
    # Write to the account's own station log (rule 16, stage 9, D50).
    LOG = "log"
    # The administrator (the first account) and the CLI: restart the server
    # (owner's decision 2026-11-13).
    ADMIN = "admin"


ALL_PERMISSIONS = frozenset(Permission)
# A logged-in browser: every account has these (owner's decision 2026-11-08,
# decisions.md D48). Without a session a browser has none (AGENTS.md rule 16).
ACCOUNT_PERMISSIONS = frozenset(
    {Permission.RECEIVE, Permission.CONTROL, Permission.TUNE, Permission.TRANSMIT, Permission.LOG}
)
ADMIN_PERMISSIONS = ACCOUNT_PERMISSIONS | {Permission.ADMIN}

# Every request type needs exactly one permission; a type missing here is refused.
REQUEST_PERMISSIONS: dict[str, Permission] = {
    "status": Permission.RECEIVE,
    "get_frequency": Permission.RECEIVE,
    "get_mode": Permission.RECEIVE,
    "get_power_meter": Permission.RECEIVE,
    "get_atu": Permission.RECEIVE,
    "get_af": Permission.RECEIVE,
    "set_af": Permission.CONTROL,
    "set_atu": Permission.CONTROL,
    "get_front_end": Permission.RECEIVE,
    "set_front_end": Permission.CONTROL,
    "get_agc": Permission.RECEIVE,
    "set_agc": Permission.CONTROL,
    "get_nb": Permission.RECEIVE,
    "set_nb": Permission.CONTROL,
    "get_rf_gain": Permission.RECEIVE,
    "get_s_meter": Permission.RECEIVE,
    "get_bandplan": Permission.RECEIVE,
    "get_alc": Permission.RECEIVE,
    "set_rf_gain": Permission.CONTROL,
    "get_vfo": Permission.RECEIVE,
    "set_vfo": Permission.CONTROL,
    "set_split": Permission.CONTROL,
    "atu_tune": Permission.TUNE,
    "set_rf_power": Permission.CONTROL,
    # Fills the disk (17 MB per 30 s): the administrator and the CLI only
    # (security review 2026-11-14, Z9).
    "iq_record": Permission.ADMIN,
    "waterfall_subscribe": Permission.RECEIVE,
    "waterfall_unsubscribe": Permission.RECEIVE,
    "audio_subscribe": Permission.RECEIVE,
    "audio_unsubscribe": Permission.RECEIVE,
    "system_subscribe": Permission.RECEIVE,
    "system_unsubscribe": Permission.RECEIVE,
    # The server's log shows who logged in and when, and what was typed as a
    # callsign: the administrator and the CLI only (Z9).
    "log_subscribe": Permission.ADMIN,
    "log_unsubscribe": Permission.ADMIN,
    "account_list": Permission.ACCOUNTS,
    "account_password": Permission.ACCOUNTS,
    "account_delete": Permission.ACCOUNTS,
    "log_list": Permission.LOG,
    "log_worked": Permission.LOG,
    "log_add": Permission.LOG,
    "log_update": Permission.LOG,
    "log_delete": Permission.LOG,
    "set_frequency": Permission.CONTROL,
    # Ends the page's work, not the radio's: refused while anything transmits.
    "server_restart": Permission.ADMIN,
    # The sound card's output level drives the radio (owner's decision 2026-11-14).
    "get_tx_output": Permission.RECEIVE,
    "set_tx_output": Permission.TRANSMIT,
    "set_mode": Permission.CONTROL,
    "tx_request": Permission.TRANSMIT,
    "tx_renew": Permission.TRANSMIT,
    "tx_release": Permission.TRANSMIT,
    "ft8_transmit": Permission.TRANSMIT,
    "rtty_start": Permission.TRANSMIT,
    "rtty_text": Permission.TRANSMIT,
    "rtty_end": Permission.TRANSMIT,
    "tx_unlock": Permission.TRANSMIT,
    "port_open": Permission.TRANSMIT,
}


@dataclass(frozen=True, slots=True)
class Request:
    id: int | str
    type: str
    fields: dict[str, Any]


class RequestError(Exception):
    def __init__(self, code: str, message: str, request_id: RequestId = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def parse_request(line: bytes) -> Request:
    if len(line) > MAX_LINE_BYTES:
        raise RequestError(BAD_REQUEST, f"line longer than {MAX_LINE_BYTES} bytes")
    try:
        data = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        raise RequestError(BAD_REQUEST, "line is not valid JSON") from None
    if not isinstance(data, dict):
        raise RequestError(BAD_REQUEST, "message must be a JSON object")
    request_id = _request_id(data.get("id"))
    if request_id is None:
        raise RequestError(BAD_REQUEST, "missing or invalid 'id'")
    if data.get("v") != PROTOCOL_VERSION:
        raise RequestError(
            UNSUPPORTED_VERSION, f"protocol version {PROTOCOL_VERSION} required", request_id
        )
    kind = data.get("type")
    if not isinstance(kind, str) or kind not in REQUEST_FIELDS:
        raise RequestError(UNKNOWN_TYPE, f"unknown request type {kind!r}", request_id)
    fields = {k: v for k, v in data.items() if k not in ("v", "id", "type")}
    unknown = set(fields) - REQUEST_FIELDS[kind]
    if unknown:
        raise RequestError(
            BAD_REQUEST, f"unknown field(s) {', '.join(sorted(unknown))}", request_id
        )
    return Request(request_id, kind, fields)


def _request_id(value: object) -> int | str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and 0 < len(value) <= MAX_ID_LENGTH:
        return value
    return None


def ok(request_id: RequestId, **fields: object) -> dict[str, object]:
    return {"v": PROTOCOL_VERSION, "id": request_id, "ok": True, **fields}


def error(request_id: RequestId, code: str, message: str) -> dict[str, object]:
    return {"v": PROTOCOL_VERSION, "id": request_id, "ok": False, "error": code, "message": message}


def event(name: str, **fields: object) -> dict[str, object]:
    return {"v": PROTOCOL_VERSION, "event": name, **fields}


def encode(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
