# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource window: CPU and memory of the radio processes from /proc.

Readings run on a fake /proc tree; the WebSocket tests read the real /proc of
the test process (the guard runs as a thread of it). No radio, no sound card.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient

import remoteg90ctl.server.web as web_module
from remoteg90ctl.server.sysmon import (
    SystemMonitor,
    cpu_temperature,
    disk_usage,
    find_process,
    read_process,
    read_system,
)
from server_helpers import System
from test_web import OURS, ask, web  # noqa: F401  (pytest fixture)

TICKS = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def write_process(
    proc: Path, pid: int, ticks: int, pages: int, name: str = "remote g90 (x)"
) -> None:
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    # Fields after the name: state, then 10 more, utime (14), stime (15), ... threads (20).
    after = ["S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 4 + ["3"] + ["0"] * 30
    (directory / "stat").write_text(f"{pid} ({name}) {' '.join(after)}\n")
    (directory / "statm").write_text(f"1000 {pages} 50 1 0 100 0\n")


def write_system(proc: Path, busy: int, idle: int) -> None:
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n")
    (proc / "meminfo").write_text("MemTotal:  2048 kB\nMemFree: 100 kB\nMemAvailable:  1024 kB\n")
    (proc / "loadavg").write_text("0.50 0.25 0.10 1/100 4242\n")


def test_read_process_handles_spaces_and_parentheses_in_the_name(tmp_path: Path) -> None:
    write_process(tmp_path, 42, ticks=250, pages=10)
    sample = read_process(42, tmp_path)
    assert sample.cpu_ticks == 250
    assert sample.rss_bytes == 10 * PAGE
    assert sample.threads == 3


def test_read_system(tmp_path: Path) -> None:
    write_system(tmp_path, busy=300, idle=700)
    sample = read_system(tmp_path)
    assert sample.total_ticks == 1000
    assert sample.busy_ticks == 300
    assert sample.mem_total_bytes == 2048 * 1024
    assert sample.mem_available_bytes == 1024 * 1024
    assert sample.load == (0.5, 0.25, 0.1)


def test_cpu_appears_from_the_second_reading(tmp_path: Path) -> None:
    now = [100.0]
    write_process(tmp_path, 7, ticks=0, pages=5)
    write_system(tmp_path, busy=0, idle=0)
    monitor = SystemMonitor(lambda: [("server", 7)], proc=tmp_path, clock=lambda: now[0])
    first = monitor.sample()
    assert first["processes"][0]["cpu_percent"] is None
    assert first["processes"][0]["rss_bytes"] == 5 * PAGE
    assert first["system"]["cpu_percent"] is None
    # Two seconds later the process has used half a second of CPU.
    now[0] += 2.0
    write_process(tmp_path, 7, ticks=TICKS // 2, pages=5)
    write_system(tmp_path, busy=250, idle=750)
    second = monitor.sample()
    assert second["processes"][0]["cpu_percent"] == pytest.approx(25.0, abs=0.1)
    assert second["system"]["cpu_percent"] == 25.0


def test_missing_or_unconnected_processes_are_reported(tmp_path: Path) -> None:
    write_system(tmp_path, busy=1, idle=1)
    monitor = SystemMonitor(lambda: [("guard", None), ("server", 999)], proc=tmp_path)
    guard, server = monitor.sample()["processes"]
    assert guard["error"] == "not connected"
    assert server["error"] == "cannot read /proc"
    assert server["rss_bytes"] is None


def test_unreadable_system_files_do_not_break_the_processes(tmp_path: Path) -> None:
    write_process(tmp_path, 7, ticks=1, pages=1)
    monitor = SystemMonitor(lambda: [("server", 7)], proc=tmp_path)
    stats = monitor.sample()
    assert stats["system"] is None
    assert stats["processes"][0]["error"] is None


async def next_stats(ws: Any, timeout: float = 5) -> dict[str, Any]:
    while True:
        message = await ws.receive(timeout=timeout)
        if message.type is WSMsgType.TEXT and message.json().get("event") == "system_stats":
            data: dict[str, Any] = message.json()
            return data


async def test_browser_gets_stats_of_server_and_guard(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_module, "INTERVAL_S", 0.05)
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "system_subscribe"))["ok"]
    await next_stats(ws)
    stats = await next_stats(ws)
    names = {p["name"]: p for p in stats["processes"]}
    assert names["server"]["pid"] == os.getpid()
    # The guard runs as a thread of the test process: the same pid over SO_PEERCRED.
    assert names["guard"]["pid"] == os.getpid()
    assert names["server"]["rss_bytes"] > 0
    assert names["server"]["cpu_percent"] is not None
    assert stats["system"]["mem_total_bytes"] > 0
    assert stats["system"]["disk"]["free_bytes"] > 0  # the state directory's file system
    assert "clock_synchronized" in stats["system"]
    await ws.close()


async def test_closing_the_window_stops_monitoring(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_module, "INTERVAL_S", 0.05)
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "system_subscribe"))["ok"]
    await next_stats(ws)
    assert (await ask(ws, "system_unsubscribe"))["ok"]
    # Drain anything sent before the answer, then nothing more arrives.
    with pytest.raises(asyncio.TimeoutError):
        await next_stats(ws, timeout=0.3)
    await ws.close()


async def test_stats_are_not_served_on_the_unix_socket(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
) -> None:
    system, _ = web
    cli = await system.client()
    response = await cli.request("system_subscribe")
    assert response["error"] == "unknown_type"
    await cli.close()


def test_disk_and_clock_are_reported(tmp_path: Path) -> None:
    """Space on the small disk by the radio, and whether FT8/FT4 may transmit (D53)."""
    write_system(tmp_path / "proc", busy=0, idle=0)
    state = tmp_path / "state" / "not-yet-created"
    monitor = SystemMonitor(
        lambda: [],
        proc=tmp_path / "proc",
        disk_path=state,
        clock_synchronized=lambda: False,
    )
    system = monitor.sample()["system"]
    stat = os.statvfs(tmp_path)
    assert system["disk"]["path"] == str(state)
    assert system["disk"]["total_bytes"] == stat.f_blocks * stat.f_frsize
    assert system["clock_synchronized"] is False
    assert SystemMonitor(lambda: [], proc=tmp_path / "proc").sample()["system"]["disk"] is None
    assert disk_usage(Path("/")) is not None


def write_zone(sys: Path, number: int, kind: str, millidegrees: int) -> None:
    zone = sys / "class" / "thermal" / f"thermal_zone{number}"
    zone.mkdir(parents=True)
    (zone / "type").write_text(kind + "\n")
    (zone / "temp").write_text(f"{millidegrees}\n")


def test_cpu_temperature_prefers_the_cpu_package(tmp_path: Path) -> None:
    write_zone(tmp_path, 0, "acpitz", 40_000)
    write_zone(tmp_path, 1, "x86_pkg_temp", 57_500)
    assert cpu_temperature(tmp_path) == {"celsius": 57.5, "source": "x86_pkg_temp"}


def test_cpu_temperature_on_a_raspberry_pi_and_through_coretemp(tmp_path: Path) -> None:
    write_zone(tmp_path / "pi", 0, "cpu-thermal", 61_000)
    assert cpu_temperature(tmp_path / "pi") == {"celsius": 61.0, "source": "cpu-thermal"}
    hwmon = tmp_path / "intel" / "class" / "hwmon" / "hwmon3"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text("coretemp\n")
    (hwmon / "temp1_input").write_text("49000\n")
    write_zone(tmp_path / "intel", 0, "acpitz", 40_000)  # a board sensor: not the CPU
    assert cpu_temperature(tmp_path / "intel") == {"celsius": 49.0, "source": "coretemp"}
    assert cpu_temperature(tmp_path / "none") is None


def test_find_process_by_name(tmp_path: Path) -> None:
    for pid, name in ((10, "bash"), (577, "tailscaled"), (900, "tailscale")):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "comm").write_text(name + "\n")
    assert find_process("tailscaled", tmp_path) == 577
    assert find_process("tailscaled", tmp_path, hint=577) == 577
    assert find_process("tailscaled", tmp_path, hint=10) == 577  # a reused pid is checked
    assert find_process("chronyd", tmp_path) is None


def test_temperature_is_in_the_system_stats(tmp_path: Path) -> None:
    write_system(tmp_path / "proc", busy=0, idle=0)
    write_zone(tmp_path / "sys", 0, "x86_pkg_temp", 50_000)
    monitor = SystemMonitor(lambda: [], proc=tmp_path / "proc", sys=tmp_path / "sys")
    assert monitor.sample()["system"]["cpu_temperature"]["celsius"] == 50.0


async def test_tailscaled_is_listed_while_it_runs(
    web: tuple[System, TestClient[Any, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_module, "INTERVAL_S", 0.05)
    monkeypatch.setattr(web_module, "find_process", lambda _name, hint=None: os.getpid())
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "system_subscribe"))["ok"]
    stats = await next_stats(ws)
    assert "tailscaled" in {p["name"] for p in stats["processes"]}
    await ws.close()
