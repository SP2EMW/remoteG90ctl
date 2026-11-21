# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal systemd ``sd_notify`` client using only the standard library.

Without ``NOTIFY_SOCKET`` (not started by systemd, tests) every call is a no-op.
Which code path may send ``WATCHDOG=1`` is decided by the caller: only the
safety loop, after a complete iteration (AGENTS.md rule 12).
"""

import logging
import os
import socket
from collections.abc import Mapping

from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        address = env.get("NOTIFY_SOCKET", "")
        # A leading "@" denotes the Linux abstract namespace.
        self._address = "\0" + address[1:] if address.startswith("@") else address
        self.watchdog_interval_s = _watchdog_interval(env)

    @property
    def enabled(self) -> bool:
        return bool(self._address)

    def ready(self) -> None:
        self._send("READY=1")

    def watchdog(self) -> None:
        self._send("WATCHDOG=1")

    def stopping(self) -> None:
        self._send("STOPPING=1")

    def status(self, text: str) -> None:
        self._send("STATUS=" + text.replace("\n", " "))

    def _send(self, message: str) -> None:
        if not self._address:
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
                sock.sendto(message.encode(), self._address)
        except OSError as exc:
            # Not fatal: a missing watchdog ping makes systemd restart us, which is safe.
            logger.warning("sd_notify_failed", extra=kv(message=message, error=exc))


def _watchdog_interval(env: Mapping[str, str]) -> float | None:
    """Seconds between watchdog pings, or ``None`` if the watchdog is off."""
    usec = env.get("WATCHDOG_USEC")
    pid = env.get("WATCHDOG_PID")
    if not usec or (pid and pid != str(os.getpid())):
        return None
    try:
        value = int(usec)
    except ValueError:
        return None
    # Ping four times per timeout so a single delayed iteration is tolerated.
    return value / 1_000_000 / 4 if value > 0 else None
