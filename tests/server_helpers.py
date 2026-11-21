# SPDX-License-Identifier: GPL-3.0-or-later
"""A running guard (thread) plus server (asyncio task) on the simulator, for tests."""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guard_helpers import TIMEOUT, Harness, RunningGuard
from remoteg90ctl.config import Config
from remoteg90ctl.server.core import ServerCore
from remoteg90ctl.server.main import run_server


async def async_wait_for(condition: Callable[[], bool], timeout: float = TIMEOUT) -> float:
    """Wait without blocking the event loop; return the time it took."""
    start = time.monotonic()
    while not condition():
        if time.monotonic() - start > timeout:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)
    return time.monotonic() - start


class AsyncClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.events: list[dict[str, Any]] = []
        self._ids = 0

    @classmethod
    async def connect(cls, path: Path) -> "AsyncClient":
        reader, writer = await asyncio.open_unix_connection(str(path))
        return cls(reader, writer)

    async def send_raw(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        self._ids += 1
        line = json.dumps({"v": 1, "id": self._ids, "type": kind, **fields}).encode() + b"\n"
        await self.send_raw(line)
        while True:
            message = await self.receive()
            if "event" in message:
                self.events.append(message)
            elif message.get("id") == self._ids:
                return message

    async def receive(self, timeout: float = TIMEOUT) -> dict[str, Any]:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        if not line:
            raise ConnectionError("closed")
        message: dict[str, Any] = json.loads(line)
        return message

    async def next_event(self, name: str, timeout: float = TIMEOUT) -> dict[str, Any]:
        for message in self.events:
            if message.get("event") == name:
                self.events.remove(message)
                return message
        deadline = time.monotonic() + timeout
        while True:
            message = await self.receive(max(0.01, deadline - time.monotonic()))
            if message.get("event") == name:
                return message
            self.events.append(message)

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(OSError):
            await self.writer.wait_closed()


@dataclass
class System:
    harness: Harness
    guard: RunningGuard
    config: Config
    core: ServerCore

    @property
    def ptt_active(self) -> bool:
        return self.harness.ptt_active

    async def client(self) -> AsyncClient:
        return await AsyncClient.connect(self.config.server.socket_path)

    def restart_guard(self) -> None:
        """Stop the guard thread and start a fresh guard core on the same port."""
        self.guard.stop()
        self.harness.core = self.harness.new_core()
        self.guard = RunningGuard(self.harness).__enter__()


@contextlib.asynccontextmanager
async def running_system(config: Config) -> AsyncIterator[System]:
    harness = Harness(config, real_time=True)
    guard = RunningGuard(harness).__enter__()
    core = ServerCore(config)
    stop = asyncio.Event()
    task = asyncio.create_task(run_server(config, stop, core))
    system = System(harness, guard, config, core)
    try:
        await async_wait_for(lambda: config.server.socket_path.exists() and core.guard.connected)
        yield system
    finally:
        stop.set()
        await asyncio.wait_for(task, TIMEOUT)
        system.guard.stop()
