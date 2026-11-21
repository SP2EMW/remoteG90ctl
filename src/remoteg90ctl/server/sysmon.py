# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU and memory of the radio processes, read from ``/proc`` (Linux).

A browser opens the resource window with ``system_subscribe`` and gets a
``system_stats`` event every :data:`INTERVAL_S` until it closes the window
(``system_unsubscribe``) or disconnects; nothing is read while nobody watches
(docs/server-protocol.md). Reading only: no process is signalled or changed.

The processes are the server itself and the guard; the guard's pid comes from
the credentials of the server's connection to the guard socket (``SO_PEERCRED``).
Tailscale's daemon is shown too when it runs (remote access goes through it);
it is found by its name in ``/proc``. The CPU temperature comes from ``/sys``.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INTERVAL_S = 2.0

_TICKS = os.sysconf("SC_CLK_TCK")
_PAGE = os.sysconf("SC_PAGE_SIZE")


@dataclass(frozen=True, slots=True)
class ProcessSample:
    cpu_ticks: int  # user + system time, in clock ticks
    rss_bytes: int
    threads: int


def find_process(name: str, proc: Path = Path("/proc"), hint: int | None = None) -> int | None:
    """The pid of a process by its command name (``/proc/<pid>/comm``); ``hint`` first."""

    def named(pid: str) -> bool:
        try:
            return (proc / pid / "comm").read_text().strip() == name
        except OSError:
            return False

    if hint is not None and named(str(hint)):
        return hint
    try:
        entries = [entry.name for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:
        return None
    return next((int(pid) for pid in entries if named(pid)), None)


# Thermal zone types that measure the CPU, best first: Intel's package sensor,
# then the names ARM boards (Raspberry Pi and others) use.
CPU_ZONES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal", "soc-thermal")


def cpu_temperature(sys: Path = Path("/sys")) -> dict[str, Any] | None:
    """``{celsius, source}`` of the CPU, or ``None`` when no sensor is readable."""
    zones: dict[str, Path] = {}
    for zone in sorted((sys / "class" / "thermal").glob("thermal_zone*")):
        try:
            zones.setdefault((zone / "type").read_text().strip(), zone / "temp")
        except OSError:
            continue
    candidates = [(kind, zones[kind]) for kind in CPU_ZONES if kind in zones]
    # Intel without the package zone: the coretemp sensor's first input (the package).
    for hwmon in sorted((sys / "class" / "hwmon").glob("hwmon*")):
        try:
            if (hwmon / "name").read_text().strip() == "coretemp":
                candidates.append(("coretemp", hwmon / "temp1_input"))
        except OSError:
            continue
    for source, path in candidates:
        try:
            return {"celsius": round(int(path.read_text()) / 1000, 1), "source": source}
        except (OSError, ValueError):
            continue
    return None


def read_process(pid: int, proc: Path = Path("/proc")) -> ProcessSample:
    """CPU time and resident memory of one process; ``OSError`` if it is gone or hidden."""
    stat = (proc / str(pid) / "stat").read_text()
    # The command name is in parentheses and may contain spaces: split after it.
    fields = stat[stat.rindex(")") + 2 :].split()
    utime, stime, threads = int(fields[11]), int(fields[12]), int(fields[17])
    resident = int((proc / str(pid) / "statm").read_text().split()[1])
    return ProcessSample(utime + stime, resident * _PAGE, threads)


@dataclass(frozen=True, slots=True)
class SystemSample:
    busy_ticks: int
    total_ticks: int
    mem_total_bytes: int
    mem_available_bytes: int
    load: tuple[float, float, float]


def read_system(proc: Path = Path("/proc")) -> SystemSample:
    cpu = (proc / "stat").read_text().splitlines()[0].split()[1:]
    ticks = [int(value) for value in cpu]
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)  # idle + iowait
    total = sum(ticks[:8])  # without guest time, already counted in user
    memory: dict[str, int] = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            memory[key] = int(rest.split()[0]) * 1024
    load = tuple(float(v) for v in (proc / "loadavg").read_text().split()[:3])
    return SystemSample(
        busy_ticks=total - idle,
        total_ticks=total,
        mem_total_bytes=memory.get("MemTotal", 0),
        mem_available_bytes=memory.get("MemAvailable", 0),
        load=(load[0], load[1], load[2]),
    )


class SystemMonitor:
    """Turns two consecutive readings into percentages; one per watching browser."""

    def __init__(  # noqa: PLR0913  (keyword-only seams for /proc, /sys, the clock and disk)
        self,
        processes: Callable[[], list[tuple[str, int | None]]],
        *,
        proc: Path = Path("/proc"),
        clock: Callable[[], float] = time.monotonic,
        disk_path: Path | None = None,
        clock_synchronized: Callable[[], bool | None] = lambda: None,
        sys: Path = Path("/sys"),
    ) -> None:
        self._sys = sys
        self._processes = processes
        # The disk holding the server's state (the machine by the radio has 8 GB),
        # and whether FT8/FT4 may transmit (the clock, D53).
        self._disk_path = disk_path
        self._clock_synchronized = clock_synchronized
        self._proc = proc
        self._clock = clock
        self._last: dict[tuple[str, int], tuple[float, ProcessSample]] = {}
        self._last_system: SystemSample | None = None

    def sample(self) -> dict[str, Any]:
        """The ``system_stats`` fields: CPU is ``None`` until a second reading exists."""
        now = self._clock()
        processes: list[dict[str, Any]] = []
        seen: dict[tuple[str, int], tuple[float, ProcessSample]] = {}
        for name, pid in self._processes():
            entry: dict[str, Any] = {
                "name": name,
                "pid": pid,
                "cpu_percent": None,
                "rss_bytes": None,
                "threads": None,
                "error": None,
            }
            if pid is None:
                entry["error"] = "not connected"
                processes.append(entry)
                continue
            try:
                current = read_process(pid, self._proc)
            except (OSError, ValueError, IndexError):
                entry["error"] = "cannot read /proc"
                processes.append(entry)
                continue
            entry["rss_bytes"] = current.rss_bytes
            entry["threads"] = current.threads
            previous = self._last.get((name, pid))
            if previous is not None and now > previous[0]:
                seconds = (current.cpu_ticks - previous[1].cpu_ticks) / _TICKS
                entry["cpu_percent"] = round(100 * seconds / (now - previous[0]), 1)
            seen[(name, pid)] = (now, current)
            processes.append(entry)
        self._last = seen
        return {"processes": processes, "system": self._system()}

    def _system(self) -> dict[str, Any] | None:
        try:
            current = read_system(self._proc)
        except (OSError, ValueError, IndexError):
            return None
        cpu: float | None = None
        previous = self._last_system
        if previous is not None and current.total_ticks > previous.total_ticks:
            busy = current.busy_ticks - previous.busy_ticks
            cpu = round(100 * busy / (current.total_ticks - previous.total_ticks), 1)
        self._last_system = current
        return {
            "cpu_percent": cpu,
            "cpus": os.cpu_count(),
            "load": list(current.load),
            "mem_total_bytes": current.mem_total_bytes,
            "mem_available_bytes": current.mem_available_bytes,
            "disk": disk_usage(self._disk_path) if self._disk_path is not None else None,
            "clock_synchronized": self._clock_synchronized(),
            "cpu_temperature": cpu_temperature(self._sys),
        }


def disk_usage(path: Path) -> dict[str, Any] | None:
    """Free and total bytes of the file system holding ``path`` (or its nearest parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        stat = os.statvfs(probe)
    except OSError:
        return None
    return {
        "path": str(path),
        "free_bytes": stat.f_bavail * stat.f_frsize,
        "total_bytes": stat.f_blocks * stat.f_frsize,
    }
