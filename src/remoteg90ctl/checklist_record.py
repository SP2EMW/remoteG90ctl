# SPDX-License-Identifier: GPL-3.0-or-later
"""The hardware checklist's result, kept in the server's state directory.

Written by ``remoteg90ctl setup`` (remoteg90ctl.setup.checklist), read by the
server: with backend "hardware" and no passed checklist for the configured
port, the server warns in its log at start and the page keeps a warning up
(docs/server-protocol.md, ``status.hardware_checklist``).
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

RECORD = "hardware-checklist.json"


@dataclass
class Record:
    result: str  # "passed" or "skipped"
    port: str  # /dev/serial/by-id/... it was done for
    time: str
    skipped: list[str] = field(default_factory=list)


def read_record(path: Path) -> Record | None:
    """None when there is none or it cannot be read."""
    try:
        data = json.loads(path.read_text())
        return Record(
            str(data["result"]), str(data["port"]), str(data["time"]), list(data["skipped"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_record(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=1) + "\n")
    path.chmod(0o644)  # the server reads it; nothing secret in it


def checklist_state(path: Path, port: str | None) -> dict[str, object]:
    """``status.hardware_checklist``: "passed", "skipped" or "missing" for this port."""
    record = read_record(path)
    if record is None or record.port != port:
        return {"result": "missing", "skipped": [], "time": None}
    return {"result": record.result, "skipped": record.skipped, "time": record.time}
