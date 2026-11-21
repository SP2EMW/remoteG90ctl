# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent guard state: transmit lockout and the "running" marker.

AGENTS.md rule 13 and decisions.md D24.8:

* The lockout survives restarts. It is written atomically (temporary file,
  ``fsync``, ``rename``, ``fsync`` of the directory) and only ``tx_unlock``
  clears it. A file that cannot be read or parsed means *locked*.
* The running marker exists while the guard runs and records whether a
  transmission is active. Finding it at start-up means the previous run ended
  uncleanly; if it says a transmission was active (or cannot be read), the
  guard starts locked.
* The transmission history (decisions.md D31) keeps the recent transmissions
  for the duty cycle across restarts. A file that cannot be read means the
  window is treated as full.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

STATE_VERSION = 1
LOCKOUT_FILE = "lockout.json"
RUNNING_FILE = "running.json"
HISTORY_FILE = "tx_history.json"

# Lockout reasons.
HARD_LIMIT = "hard_limit"
# An antenna tuning still emitting after ATU_TUNE_MAX_S (guard/core.py, D43).
ATU_TUNE_OVERRUN = "atu_tune_overrun"
STATE_UNREADABLE = "state_unreadable"
UNCLEAN_SHUTDOWN_DURING_TX = "unclean_shutdown_during_tx"


def write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` so that a crash leaves the old or the new content."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class Lockout:
    reason: str
    since: str


class LockoutStore:
    """Reads and writes ``lockout.json`` in the state directory."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / LOCKOUT_FILE

    def load(self) -> Lockout | None:
        """Return the stored lockout, ``None`` if unlocked; unreadable means locked."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            if self.path.parent.is_dir():
                return None
            return Lockout(STATE_UNREADABLE, "state directory is missing")
        except OSError as exc:
            return Lockout(STATE_UNREADABLE, f"cannot read state file: {exc}")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
                raise ValueError("unsupported state file format")
            locked = data["locked"]
            if locked is False:
                return None
            reason, since = data["reason"], data["since"]
            if locked is not True or not isinstance(reason, str) or not isinstance(since, str):
                raise ValueError("invalid lockout record")
        except (ValueError, KeyError, TypeError) as exc:
            return Lockout(STATE_UNREADABLE, f"corrupt state file: {exc}")
        return Lockout(reason, since)

    def save(self, lockout: Lockout | None) -> None:
        """Persist the lockout (``None`` = unlocked). Raises ``OSError`` on failure."""
        record: dict[str, object] = {"version": STATE_VERSION, "locked": lockout is not None}
        if lockout is not None:
            record |= {"reason": lockout.reason, "since": lockout.since}
        write_atomic(self.path, (json.dumps(record) + "\n").encode())


@dataclass(frozen=True, slots=True)
class PreviousRun:
    """What the running marker says about the previous run.

    ``pending_lockout`` is a lockout that could not be saved to ``lockout.json``
    and was recorded in the marker instead.
    """

    unclean: bool
    tx_active: bool
    detail: str
    pending_lockout: str | None = None
    # The transmission that was active: lease, source, frequency_hz, started.
    lease: dict[str, object] | None = None


class RunMarker:
    """``running.json``: present while the guard runs, records active transmission."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / RUNNING_FILE

    def previous_run(self) -> PreviousRun:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return PreviousRun(unclean=False, tx_active=False, detail="clean")
        except OSError as exc:
            # Cannot tell; assume the worst.
            return PreviousRun(unclean=True, tx_active=True, detail=f"marker unreadable: {exc}")
        try:
            data = json.loads(raw)
            tx_active = data["tx_active"]
            pending = data.get("pending_lockout")
            if not isinstance(tx_active, bool):
                raise TypeError("tx_active is not a boolean")
            if pending is not None and not isinstance(pending, str):
                raise TypeError("pending_lockout is not a string")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return PreviousRun(unclean=True, tx_active=True, detail=f"marker corrupt: {exc}")
        lease = data.get("lease")
        return PreviousRun(
            unclean=True,
            tx_active=tx_active,
            detail=f"pid {data.get('pid')}",
            pending_lockout=pending,
            # Informational only (for the TX log); a malformed value is ignored.
            lease=lease if isinstance(lease, dict) else None,
        )

    def write(
        self,
        *,
        tx_active: bool,
        pending_lockout: str | None = None,
        lease: dict[str, object] | None = None,
    ) -> None:
        """Record the current state. Raises ``OSError`` on failure."""
        record = {
            "version": STATE_VERSION,
            "pid": os.getpid(),
            "tx_active": tx_active,
            "pending_lockout": pending_lockout,
            "lease": lease,
        }
        write_atomic(self.path, (json.dumps(record) + "\n").encode())

    def remove(self) -> None:
        """Mark a clean exit."""
        self.path.unlink(missing_ok=True)
        _fsync_dir(self.path.parent)


class HistoryUnreadableError(Exception):
    """The transmission history exists but cannot be used; treat the window as full."""


class TxHistory:
    """``tx_history.json``: recent transmissions as ``[start, end]`` in UNIX seconds."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / HISTORY_FILE

    def load(self) -> list[tuple[float, float]]:
        """Return the stored intervals; raises :class:`HistoryUnreadableError`."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            if self.path.parent.is_dir():
                return []
            raise HistoryUnreadableError("state directory is missing") from None
        except OSError as exc:
            raise HistoryUnreadableError(f"cannot read {self.path.name}: {exc}") from exc
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
                raise ValueError("unsupported history file format")
            intervals = []
            for item in data["intervals"]:
                start, end = item
                if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in item):
                    raise TypeError("interval bounds must be numbers")
                if end < start:
                    raise ValueError("interval ends before it starts")
                intervals.append((float(start), float(end)))
        except (ValueError, KeyError, TypeError) as exc:
            raise HistoryUnreadableError(f"corrupt {self.path.name}: {exc}") from exc
        return intervals

    def save(self, intervals: list[tuple[float, float]]) -> None:
        """Persist the intervals. Raises ``OSError`` on failure."""
        record = {"version": STATE_VERSION, "intervals": [[a, b] for a, b in intervals]}
        write_atomic(self.path, (json.dumps(record) + "\n").encode())

    def invalidate(self) -> None:
        """Empty the file after a failed save: it lacks the latest transmissions.

        An empty file is unreadable, and an unreadable history means a full
        window at the next start (AGENTS.md rule 4), so a restart cannot forget
        what was not saved. Truncating needs no free space. Raises ``OSError``.
        """
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
