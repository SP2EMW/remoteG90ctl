# SPDX-License-Identifier: GPL-3.0-or-later
"""The server's recent log lines, for the page's "System log" window.

A logging handler keeps the last CAPACITY records of level INFO and above,
formatted like the journal (key=value), and passes new ones to listeners (one
per browser with the window open). DEBUG lines (every CI-V frame) stay out.
"""

import logging
from collections import deque
from collections.abc import Callable

from remoteg90ctl.logging_setup import KeyValueFormatter

CAPACITY = 500
LEVEL = logging.INFO

Listener = Callable[[str], None]


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = CAPACITY, level: int = LEVEL) -> None:
        super().__init__(level)
        self.setFormatter(KeyValueFormatter())
        self._lines: deque[str] = deque(maxlen=capacity)
        self._listeners: list[Listener] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._lines.append(line)
        for listener in list(self._listeners):
            try:
                listener(line)
            except Exception:  # noqa: S112 - a closed browser must not break logging
                continue

    def subscribe(self, listener: Listener) -> list[str]:
        """Add a listener; return the lines kept so far, oldest first."""
        with self.lock if self.lock is not None else _NoLock():
            self._listeners.append(listener)
            return list(self._lines)

    def unsubscribe(self, listener: Listener) -> None:
        with self.lock if self.lock is not None else _NoLock():
            if listener in self._listeners:
                self._listeners.remove(listener)


class _NoLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


LOG_BUFFER = LogBuffer()


def install() -> None:
    """Attach the buffer to the root logger (after setup_logging)."""
    root = logging.getLogger()
    if LOG_BUFFER not in root.handlers:
        root.addHandler(LOG_BUFFER)
