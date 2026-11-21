# SPDX-License-Identifier: GPL-3.0-or-later
"""The station log (stage 9, decisions.md D30 and D50).

One SQLite database in the server's state directory; every account has its
own log (owner's decision 2026-11-09), so a QSO always belongs to the account
that made it, and the ADIF export carries the account's callsign and locator
(``STATION_CALLSIGN``, ``MY_GRIDSQUARE``), as LoTW will need (stage 10).

The log has no path to the radio: nothing here sends a CI-V frame, changes
the frequency or mode, or asks for a transmission (AGENTS.md, stage 9). The
frequency and mode of a new QSO are what the page shows, sent with it.

Standard library only (``sqlite3``). ADIF 3.1.5: https://adif.org/315/ADIF_315.htm
"""

import contextlib
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGBOOK_FILE = "logbook.sqlite"
SCHEMA_VERSION = 1
ADIF_VERSION = "3.1.5"
PROGRAM_ID = "remoteG90ctl"
MAX_IMPORT_BYTES = 5 * 1024 * 1024
# The import runs in the server's event loop (SQLite connections stay in their
# thread), so its size is bounded; and one account cannot fill the disk
# (security review 2026-11-14, Z3).
MAX_IMPORT_RECORDS = 20_000
MAX_QSOS_PER_ACCOUNT = 100_000
MAX_LIST = 500

# Amateur bands the G90 covers (ADIF band enumeration, IARU edges as in ADIF).
BANDS: tuple[tuple[str, int, int], ...] = (
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_060_000, 5_450_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
)
# Log modes and their ADIF MODE / SUBMODE. FT4 is a submode of MFSK in ADIF
# (LoTW records a bare FT4 as DATA).
MODES: dict[str, tuple[str, str]] = {
    "SSB": ("SSB", ""),
    "USB": ("SSB", "USB"),
    "LSB": ("SSB", "LSB"),
    "CW": ("CW", ""),
    "AM": ("AM", ""),
    "FM": ("FM", ""),
    "FT8": ("FT8", ""),
    "FT4": ("MFSK", "FT4"),
}
# The radio's modes as logged by default (owner's station: U-D/L-D are voice).
RADIO_TO_LOG = {
    "USB": "USB",
    "U-D": "USB",
    "LSB": "LSB",
    "L-D": "LSB",
    "CW": "CW",
    "CW-R": "CW",
    "AM": "AM",
    "FM": "FM",
}

CALL = re.compile(r"[A-Z0-9]{1,8}(/[A-Z0-9]{1,8}){0,2}")
LOCATOR = re.compile(r"[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?")
RST = re.compile(r"[0-9+\-]{0,4}")
DATE = re.compile(r"[0-9]{8}")
TIME = re.compile(r"[0-9]{4}([0-9]{2})?")
TEXT_LIMITS = {"name": 60, "qth": 60, "comment": 300}


class LogbookError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def band_of(hz: int) -> str:
    return next((name for name, low, high in BANDS if low <= hz <= high), "")


def _text(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key, "")
    if not isinstance(value, str):
        raise LogbookError("invalid_value", f"{key}: text expected")
    value = value.strip()
    if len(value) > TEXT_LIMITS.get(key, 20):
        raise LogbookError("invalid_value", f"{key}: at most {TEXT_LIMITS.get(key, 20)} characters")
    return value


def clean_qso(fields: dict[str, Any], now: float) -> dict[str, Any]:
    """A validated QSO from the page's fields; date and time default to now (UTC)."""
    call = _text(fields, "call").upper()
    if not CALL.fullmatch(call):
        raise LogbookError(
            "invalid_value", "call: letters, digits and /, e.g. SP1ABC or DL/SP1ABC/P"
        )
    freq = fields.get("freq_hz")
    if isinstance(freq, bool) or not isinstance(freq, int) or not 100_000 <= freq <= 60_000_000:
        raise LogbookError("invalid_value", "freq_hz: whole hertz, 0.1 to 60 MHz")
    mode = _text(fields, "mode").upper()
    if mode not in MODES:
        raise LogbookError("invalid_value", f"mode: one of {', '.join(MODES)}")
    stamp = datetime.fromtimestamp(now, UTC)
    qso_date = _text(fields, "qso_date") or stamp.strftime("%Y%m%d")
    time_on = _text(fields, "time_on") or stamp.strftime("%H%M%S")
    if not DATE.fullmatch(qso_date) or not TIME.fullmatch(time_on):
        raise LogbookError("invalid_value", "qso_date YYYYMMDD and time_on HHMM[SS], UTC")
    try:
        datetime.strptime(qso_date + time_on.ljust(6, "0"), "%Y%m%d%H%M%S")
    except ValueError:
        raise LogbookError("invalid_value", "qso_date / time_on: not a real date or time") from None
    grid = _text(fields, "gridsquare")
    grid = grid[:4].upper() + grid[4:6].lower() + grid[6:]
    if grid and not LOCATOR.fullmatch(grid[:4] + grid[4:6].upper() + grid[6:]):
        raise LogbookError("invalid_value", "gridsquare: e.g. JO82 or JO82lj")
    rst = {key: _text(fields, key) for key in ("rst_sent", "rst_rcvd")}
    if not all(RST.fullmatch(v) for v in rst.values()):
        raise LogbookError("invalid_value", "RST: digits, e.g. 59 or 599")
    return {
        "call": call,
        "qso_date": qso_date,
        "time_on": time_on,
        "freq_hz": freq,
        "band": band_of(freq),
        "mode": mode,
        **rst,
        "name": _text(fields, "name"),
        "qth": _text(fields, "qth"),
        "gridsquare": grid,
        "comment": _text(fields, "comment"),
    }


@dataclass(frozen=True, slots=True)
class Station:
    """The account whose log it is: written into the ADIF export."""

    callsign: str
    locator: str


class Logbook:
    def __init__(self, state_dir: Path, *, clock: Any = time.time) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / LOGBOOK_FILE
        self._clock = clock
        if not self._path.exists():
            # Created private: SQLite gives its -wal and -shm files the same mode.
            os.close(os.open(self._path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._db = sqlite3.connect(self._path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS qso (
                id INTEGER PRIMARY KEY,
                account TEXT NOT NULL,
                call TEXT NOT NULL,
                qso_date TEXT NOT NULL,
                time_on TEXT NOT NULL,
                freq_hz INTEGER NOT NULL,
                band TEXT NOT NULL,
                mode TEXT NOT NULL,
                rst_sent TEXT NOT NULL DEFAULT '',
                rst_rcvd TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                qth TEXT NOT NULL DEFAULT '',
                gridsquare TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                lotw_sent TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL,
                updated REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS qso_by_time ON qso (account, qso_date, time_on);
            CREATE INDEX IF NOT EXISTS qso_by_call ON qso (account, call);
            """
        )
        self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        with contextlib.suppress(OSError):
            self._path.chmod(0o600)

    def close(self) -> None:
        self._db.close()

    # QSOs

    def add(self, account: str, fields: dict[str, Any]) -> dict[str, Any]:
        qso = clean_qso(fields, self._clock())
        now = self._clock()
        cursor = self._db.execute(
            "INSERT INTO qso (account, call, qso_date, time_on, freq_hz, band, mode, rst_sent,"
            " rst_rcvd, name, qth, gridsquare, comment, created, updated)"
            " VALUES (:account, :call, :qso_date, :time_on, :freq_hz, :band, :mode, :rst_sent,"
            " :rst_rcvd, :name, :qth, :gridsquare, :comment, :now, :now)",
            {**qso, "account": account, "now": now},
        )
        return self.get(account, int(cursor.lastrowid or 0))

    def update(self, account: str, qso_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        self.get(account, qso_id)  # raises if not this account's
        qso = clean_qso(fields, self._clock())
        self._db.execute(
            "UPDATE qso SET call=:call, qso_date=:qso_date, time_on=:time_on, freq_hz=:freq_hz,"
            " band=:band, mode=:mode, rst_sent=:rst_sent, rst_rcvd=:rst_rcvd, name=:name,"
            " qth=:qth, gridsquare=:gridsquare, comment=:comment, updated=:now"
            " WHERE id=:id AND account=:account",
            {**qso, "id": qso_id, "account": account, "now": self._clock()},
        )
        return self.get(account, qso_id)

    def delete(self, account: str, qso_id: int) -> None:
        if (
            self._db.execute("DELETE FROM qso WHERE id=? AND account=?", (qso_id, account)).rowcount
            == 0
        ):
            raise LogbookError("unknown_qso", f"no QSO {qso_id} in this log")

    def get(self, account: str, qso_id: int) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT id, call, qso_date, time_on, freq_hz, band, mode, rst_sent, rst_rcvd,"
            " name, qth, gridsquare, comment, lotw_sent"
            " FROM qso WHERE id=? AND account=?",
            (qso_id, account),
        ).fetchone()
        if row is None:
            raise LogbookError("unknown_qso", f"no QSO {qso_id} in this log")
        return self._with_dupe(account, dict(row))

    def _with_dupe(self, account: str, qso: dict[str, Any]) -> dict[str, Any]:
        """``dupe``: another QSO with the same call, band and mode on the same UTC day."""
        other = self._db.execute(
            "SELECT 1 FROM qso WHERE account=? AND call=? AND band=? AND mode=? AND qso_date=?"
            " AND id<>? LIMIT 1",
            (account, qso["call"], qso["band"], qso["mode"], qso["qso_date"], qso["id"]),
        ).fetchone()
        return {**qso, "dupe": other is not None}

    def qsos(
        self, account: str, search: str = "", limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest first, optionally only calls containing ``search``; and the total."""
        pattern = f"%{search.strip().upper()}%"
        rows = self._db.execute(
            "SELECT id, call, qso_date, time_on, freq_hz, band, mode, rst_sent, rst_rcvd,"
            " name, qth, gridsquare, comment, lotw_sent"
            " FROM qso WHERE account=? AND call LIKE ?"
            " ORDER BY qso_date DESC, time_on DESC, id DESC LIMIT ?",
            (account, pattern, max(1, min(MAX_LIST, limit))),
        ).fetchall()
        total = self._db.execute("SELECT COUNT(*) FROM qso WHERE account=?", (account,)).fetchone()[
            0
        ]
        return [self._with_dupe(account, dict(r)) for r in rows], int(total)

    def worked_before(self, account: str, call: str) -> list[dict[str, Any]]:
        """The last QSOs with ``call`` (the form shows them while typing)."""
        rows = self._db.execute(
            "SELECT id, call, qso_date, time_on, freq_hz, band, mode, rst_sent, rst_rcvd,"
            " name, qth, gridsquare, comment, lotw_sent"
            " FROM qso WHERE account=? AND call=?"
            " ORDER BY qso_date DESC, time_on DESC LIMIT 5",
            (account, call.strip().upper()),
        ).fetchall()
        return [dict(r) for r in rows]

    # ADIF

    def export_adif(self, station: Station) -> str:
        rows = self._db.execute(
            "SELECT id, call, qso_date, time_on, freq_hz, band, mode, rst_sent, rst_rcvd,"
            " name, qth, gridsquare, comment, lotw_sent"
            " FROM qso WHERE account=? ORDER BY qso_date, time_on, id",
            (station.callsign,),
        ).fetchall()
        stamp = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y%m%d %H%M%S")
        parts = [
            f"remoteG90ctl log of {station.callsign}\n",
            _field("ADIF_VER", ADIF_VERSION),
            _field("PROGRAMID", PROGRAM_ID),
            _field("CREATED_TIMESTAMP", stamp),
            "<EOH>\n",
        ]
        for row in rows:
            mode, submode = MODES.get(row["mode"], (row["mode"], ""))
            fields = [
                ("CALL", row["call"]),
                ("QSO_DATE", row["qso_date"]),
                ("TIME_ON", row["time_on"]),
                ("FREQ", f"{row['freq_hz'] / 1e6:.6f}"),
                ("BAND", row["band"]),
                ("MODE", mode),
                ("SUBMODE", submode),
                ("RST_SENT", row["rst_sent"]),
                ("RST_RCVD", row["rst_rcvd"]),
                ("NAME", row["name"]),
                ("QTH", row["qth"]),
                ("GRIDSQUARE", row["gridsquare"]),
                ("COMMENT", row["comment"]),
                ("STATION_CALLSIGN", station.callsign),
                ("MY_GRIDSQUARE", station.locator),
            ]
            parts.append("".join(_field(k, v) for k, v in fields if v) + "<EOR>\n")
        return "".join(parts)

    def import_adif(self, account: str, text: str) -> dict[str, int]:
        """Add the records of an ADIF file; QSOs already in the log (same call,
        date, time to the minute, band and mode) are skipped."""
        if len(text.encode()) > MAX_IMPORT_BYTES:
            raise LogbookError("invalid_value", "ADIF file larger than 5 MB")
        records = parse_adif(text)
        if len(records) > MAX_IMPORT_RECORDS:
            raise LogbookError(
                "invalid_value", f"more than {MAX_IMPORT_RECORDS} records in one ADIF file"
            )
        (held,) = self._db.execute(
            "SELECT count(*) FROM qso WHERE account=?", (account,)
        ).fetchone()
        if held + len(records) > MAX_QSOS_PER_ACCOUNT:
            raise LogbookError(
                "invalid_value",
                f"the log would hold more than {MAX_QSOS_PER_ACCOUNT} QSOs",
            )
        added = skipped = failed = 0
        self._db.execute("BEGIN")
        try:
            for record in records:
                try:
                    fields = _from_adif(record)
                    qso = clean_qso(fields, self._clock())
                except LogbookError:
                    failed += 1
                    continue
                existing = self._db.execute(
                    "SELECT 1 FROM qso WHERE account=? AND call=? AND qso_date=?"
                    " AND substr(time_on, 1, 4)=? AND band=? AND mode=? LIMIT 1",
                    (
                        account,
                        qso["call"],
                        qso["qso_date"],
                        qso["time_on"][:4],
                        qso["band"],
                        qso["mode"],
                    ),
                ).fetchone()
                if existing is not None:
                    skipped += 1
                    continue
                self.add(account, fields)
                added += 1
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return {"added": added, "skipped": skipped, "failed": failed}


def _field(name: str, value: str) -> str:
    return f"<{name}:{len(value.encode())}>{value} "


# Lengths of up to 9 digits: a longer one is no tag (int() of thousands of
# digits raises, security review 2026-11-14).
_TAG = re.compile(r"<([A-Za-z0-9_]+)(?::(\d{1,9})(?::[A-Za-z])?)?>")


def parse_adif(text: str) -> list[dict[str, str]]:
    """Records of an ADIF (.adi) file as {FIELD: value}; the header is skipped."""
    data = text.encode()
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    position = 0
    if not text.lstrip().startswith("<"):
        eoh = re.search(rb"<eoh>", data, re.IGNORECASE)
        position = eoh.end() if eoh else 0
    pattern = re.compile(_TAG.pattern.encode())
    while (match := pattern.search(data, position)) is not None:
        name = match.group(1).decode().upper()
        position = match.end()
        if name == "EOH":
            current = {}
        elif name == "EOR":
            if current:
                records.append(current)
            current = {}
        elif match.group(2) is not None:
            length = int(match.group(2))
            current[name] = data[position : position + length].decode(errors="replace")
            position += length
    return records


def _from_adif(record: dict[str, str]) -> dict[str, Any]:
    mode = record.get("MODE", "").upper()
    submode = record.get("SUBMODE", "").upper()
    log_mode = submode if submode in MODES else mode
    if mode == "SSB" and submode not in ("USB", "LSB"):
        log_mode = "SSB"
    freq_text = record.get("FREQ", "")
    try:
        value = float(freq_text) if freq_text else 0.0
        freq = round(value * 1e6) if math.isfinite(value) else 0
    except (ValueError, OverflowError):
        freq = 0
    if not freq:
        band = record.get("BAND", "").lower()
        freq = next((low for name, low, _ in BANDS if name == band), 0)
    return {
        "call": record.get("CALL", ""),
        "qso_date": record.get("QSO_DATE", ""),
        "time_on": record.get("TIME_ON", ""),
        "freq_hz": freq,
        "mode": log_mode,
        "rst_sent": record.get("RST_SENT", "")[:4],
        "rst_rcvd": record.get("RST_RCVD", "")[:4],
        "name": record.get("NAME", "")[:60],
        "qth": record.get("QTH", "")[:60],
        "gridsquare": record.get("GRIDSQUARE", "")[:8],
        "comment": record.get("COMMENT", "")[:300],
    }
