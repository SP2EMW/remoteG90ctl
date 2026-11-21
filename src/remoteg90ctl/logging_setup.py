# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared structured (key=value) logging setup.

Log calls pass an event name as the message and structured fields through
``extra=kv(...)``::

    logger.warning("civ_rejected", extra=kv(reason="not allowed", command="1c"))

renders as::

    2026-10-31T12:00:00.123+0000 level=WARNING logger=remoteg90ctl.guard event=civ_rejected
    reason="not allowed" command=1c

Standard library only, so the guard may use it.
"""

import logging
import sys
from collections.abc import Mapping
from typing import IO, Any

_FIELDS_ATTR = "kv_fields"


def kv(**fields: object) -> dict[str, Any]:
    """Wrap structured fields for the ``extra`` argument of a logging call."""
    return {_FIELDS_ATTR: fields}


def _format_value(value: object) -> str:
    text = str(value)
    if text and not any(c.isspace() or c in '"=\\' for c in text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


class KeyValueFormatter(logging.Formatter):
    """Render records as a single line of ``key=value`` pairs."""

    def __init__(self) -> None:
        super().__init__(datefmt="%Y-%m-%dT%H:%M:%S%z")

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            f"{self.formatTime(record, self.datefmt)}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"event={_format_value(record.getMessage())}",
        ]
        fields: Mapping[str, object] = getattr(record, _FIELDS_ATTR, {})
        parts.extend(f"{key}={_format_value(value)}" for key, value in fields.items())
        if record.exc_info:
            parts.append(f"exc={_format_value(self.formatException(record.exc_info))}")
        return " ".join(parts)


def setup_logging(level: str, stream: IO[str] | None = None) -> None:
    """Configure the root logger with the key=value formatter."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
