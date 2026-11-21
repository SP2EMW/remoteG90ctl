# SPDX-License-Identifier: GPL-3.0-or-later
"""Is the system clock synchronised? (FT8/FT4 reception, stage 6, decisions.md D52.)

FT8 and FT4 cycles start at fixed UTC seconds, so the page needs the time of
the audio it decodes, from this server's clock (frame 0x04, docs/server-
protocol.md). AGENTS.md (stage 7): reception works with an unsynchronised
clock, with a warning; transmission will not.

Asked in this order, the first that answers counts (decisions.md D53,
2026-11-11):

1. chrony (``chronyc -c tracking``, over localhost): the clock's actual error
   bound, |offset| + root delay / 2 + root dispersion, must be within
   MAX_ERROR_S. A flag alone is not enough: on 2026-11-11 a laptop's clock was
   2.3 s off while nothing warned, and every FT8 decode showed DT 2.3 s.
2. systemd-timedated (``timedatectl``), which needs D-Bus: the kernel's flag.
3. The file systemd-timesyncd creates after its first successful
   synchronisation, on systems without D-Bus and without chrony.

Reading the kernel's state directly (``adjtimex``) is not possible here: the
service runs with ProtectClock=yes, which forbids that system call.
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

CHECK_EVERY_S = 60.0
TIMEOUT_S = 5.0
# Largest clock error FT8/FT4 transmission accepts (owner's decision
# 2026-11-11): far within what the other stations' decoders take (about
# +-2.5 s), well above what a working NTP client keeps (milliseconds).
MAX_ERROR_S = 0.2

TIMESYNCD_SYNCHRONIZED = Path("/run/systemd/timesync/synchronized")

Check = Callable[[], Awaitable[bool | None]]


async def clock_synchronized(timesyncd_flag: Path = TIMESYNCD_SYNCHRONIZED) -> bool | None:
    """chrony's answer; else timedated's; else True if timesyncd has synchronised, else None."""
    for ask in (chrony_synchronized, timedated_synchronized):
        answer = await ask()
        if answer is not None:
            return answer
    return True if timesyncd_flag.exists() else None


async def chrony_synchronized() -> bool | None:
    """True when chrony is synchronised within MAX_ERROR_S; None when it cannot be asked."""
    out = await _output("chronyc", "-c", "-n", "tracking")
    return None if out is None else chrony_verdict(out)


def chrony_verdict(csv: str) -> bool | None:
    """One line of ``chronyc -c tracking``; None when it cannot be read."""
    fields = csv.strip().split(",")
    if len(fields) < 14:  # the tracking report has 14 fields
        return None
    if fields[13] == "Not synchronised":
        return False
    try:
        offset, delay, dispersion = float(fields[4]), float(fields[10]), float(fields[11])
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in (offset, delay, dispersion)):
        return None
    error = abs(offset) + delay / 2 + dispersion
    if error > MAX_ERROR_S:
        logger.warning("clock_error", extra=kv(error_s=round(error, 3), limit_s=MAX_ERROR_S))
        return False
    return True


async def timedated_synchronized() -> bool | None:
    """True or False from timedated; None when it cannot be asked."""
    out = await _output("timedatectl", "show", "--property=NTPSynchronized", "--value")
    answer = None if out is None else out.strip()
    if answer not in ("yes", "no"):
        return None
    return answer == "yes"


async def _output(*argv: str) -> str | None:
    """The program's standard output, or None when it is missing, fails or hangs."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT_S)
    except TimeoutError:
        await _reap(process)
        return None
    except asyncio.CancelledError:
        await _reap(process)  # no stray process when the server stops
        raise
    return out.decode(errors="replace") if process.returncode == 0 else None


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.shield(process.wait())


class ClockWatch:
    """The last answer, refreshed every minute."""

    def __init__(self, check: Check = clock_synchronized) -> None:
        self._check = check
        self.synchronized: bool | None = None

    async def run(self) -> None:
        while True:
            synchronized = await self._check()
            if synchronized != self.synchronized:
                log = logger.info if synchronized else logger.warning
                log("clock_synchronized", extra=kv(synchronized=synchronized))
            self.synchronized = synchronized
            await asyncio.sleep(CHECK_EVERY_S)
