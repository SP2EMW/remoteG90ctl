# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard ↔ server wire protocol: versioned JSON Lines (docs/guard-protocol.md)."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 4096
MAX_ID_LENGTH = 64

RequestId = int | str | None


class ErrorCode(StrEnum):
    BAD_REQUEST = "bad_request"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_TYPE = "unknown_type"
    BUSY = "busy"
    FRAME_INVALID = "frame_invalid"
    COMMAND_NOT_ALLOWED = "command_not_allowed"
    TX_ACTIVE = "tx_active"
    TX_BUSY = "tx_busy"
    TX_LOCKED = "tx_locked"
    TX_BLOCKED = "tx_blocked"
    UNKNOWN_LEASE = "unknown_lease"
    PORT_UNAVAILABLE = "port_unavailable"
    TX_LOG_FAILED = "tx_log_failed"
    STATE_WRITE_FAILED = "state_write_failed"


# Allowed fields per request type, besides "v", "id" and "type".
REQUEST_FIELDS: dict[str, frozenset[str]] = {
    "civ": frozenset({"frame"}),
    "tx_request": frozenset({"source", "frequency_hz"}),
    "atu_tune": frozenset({"source", "frequency_hz"}),
    "tx_renew": frozenset({"lease"}),
    "tx_release": frozenset({"lease"}),
    "tx_unlock": frozenset(),
    "port_open": frozenset(),
    "status": frozenset(),
}


@dataclass(frozen=True, slots=True)
class Request:
    id: int | str
    type: str
    fields: dict[str, Any]


class RequestError(Exception):
    def __init__(self, code: ErrorCode, message: str, request_id: RequestId = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def parse_request(line: bytes) -> Request:
    """Decode one line into a :class:`Request`; raises :class:`RequestError`."""
    if len(line) > MAX_LINE_BYTES:
        raise RequestError(ErrorCode.BAD_REQUEST, f"line longer than {MAX_LINE_BYTES} bytes")
    try:
        data = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        raise RequestError(ErrorCode.BAD_REQUEST, "line is not valid JSON") from None
    if not isinstance(data, dict):
        raise RequestError(ErrorCode.BAD_REQUEST, "message must be a JSON object")
    request_id = _request_id(data.get("id"))
    if request_id is None:
        raise RequestError(ErrorCode.BAD_REQUEST, "missing or invalid 'id'")
    if data.get("v") != PROTOCOL_VERSION:
        raise RequestError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"protocol version {PROTOCOL_VERSION} required",
            request_id,
        )
    kind = data.get("type")
    if not isinstance(kind, str) or kind not in REQUEST_FIELDS:
        raise RequestError(ErrorCode.UNKNOWN_TYPE, f"unknown request type {kind!r}", request_id)
    fields = {k: v for k, v in data.items() if k not in ("v", "id", "type")}
    unknown = set(fields) - REQUEST_FIELDS[kind]
    if unknown:
        raise RequestError(
            ErrorCode.BAD_REQUEST, f"unknown field(s) {', '.join(sorted(unknown))}", request_id
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


def error(request_id: RequestId, code: ErrorCode, message: str) -> dict[str, object]:
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": str(code),
        "message": message,
    }


def event(name: str, **fields: object) -> dict[str, object]:
    return {"v": PROTOCOL_VERSION, "event": name, **fields}


def encode(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
