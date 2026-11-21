# SPDX-License-Identifier: GPL-3.0-or-later
"""Transmit log: one JSON object per line, append-only (AGENTS.md rule 7).

Every record is flushed and fsynced before :meth:`TxLog.write` returns, so a
record that was reported as written survives a crash. Events are rare (a few
per transmission), so the cost does not matter.

Every record carries the program ``version`` that wrote it, so each
transmission can be traced to the guard release that keyed it.
"""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from remoteg90ctl import __version__

WallClock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TxLog:
    def __init__(self, path: Path, wall_clock: WallClock = _utc_now) -> None:
        self.path = path
        self._wall_clock = wall_clock

    def now(self) -> datetime:
        """Current wall-clock time (UTC) of this log."""
        return self._wall_clock()

    def timestamp(self) -> str:
        return self.now().isoformat(timespec="milliseconds")

    def write(self, event: str, **fields: object) -> None:
        """Append one record stamped now. Raises ``OSError`` if not made durable."""
        self.write_at(self.timestamp(), event, **fields)

    def write_at(self, ts: str, event: str, **fields: object) -> None:
        """Append one record with the given time.

        Used where the same instant is referenced elsewhere (``tx_start`` and the
        lease's ``started``). Raises ``OSError`` if not made durable.
        """
        record = {"ts": ts, "event": event, "version": __version__, **fields}
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            written = os.write(fd, line)
            if written != len(line):
                raise OSError(f"short write to TX log ({written} of {len(line)} bytes)")
            os.fsync(fd)
        finally:
            os.close(fd)
