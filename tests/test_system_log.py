# SPDX-License-Identifier: GPL-3.0-or-later
"""The page's "System log" window: the server's recent INFO+ lines, then new ones."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient

from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server import logbuffer
from remoteg90ctl.server.logbuffer import LogBuffer
from server_helpers import System, running_system
from test_web import OURS, ask, start_logged_in, web_config

logger = logging.getLogger("remoteg90ctl.server.test")
logger.setLevel(logging.INFO)  # pytest leaves the root at WARNING


def test_buffer_keeps_info_and_above_and_the_latest() -> None:
    buffer = LogBuffer(capacity=3)
    record = logger.makeRecord
    seen: list[str] = []
    buffer.handle(record(logger.name, logging.DEBUG, "", 0, "civ_tx", (), None))
    for name in ("a", "b", "c", "d"):
        buffer.handle(record(logger.name, logging.INFO, "", 0, name, (), None))
    lines = buffer.subscribe(seen.append)
    assert [line.split("event=")[1] for line in lines] == ["b", "c", "d"]
    buffer.handle(record(logger.name, logging.WARNING, "", 0, "e", (), None, extra=kv(x=1)))
    assert seen[0].endswith("event=e x=1")
    assert "level=WARNING" in seen[0]
    buffer.unsubscribe(seen.append)
    buffer.handle(record(logger.name, logging.INFO, "", 0, "f", (), None))
    assert len(seen) == 1


def test_a_failing_listener_does_not_break_logging() -> None:
    buffer = LogBuffer()

    def broken(_line: str) -> None:
        raise RuntimeError("closed")

    buffer.subscribe(broken)
    buffer.handle(logger.makeRecord(logger.name, logging.INFO, "", 0, "x", (), None))


@pytest_asyncio.fixture
async def web(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    logbuffer.install()
    async with running_system(web_config(tmp_path)) as system:
        client = await start_logged_in(system)
        try:
            yield system, client
        finally:
            await client.close()


async def test_browser_gets_the_log_while_the_window_is_open(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = web
    logger.info("before_open")
    ws = await client.ws_connect("/ws", headers=OURS)
    answer = await ask(ws, "log_subscribe")
    assert any("event=before_open" in line for line in answer["lines"])
    logger.warning("while_open", extra=kv(n=1))
    while True:
        message = await ws.receive(timeout=5)
        data = message.json() if message.type is WSMsgType.TEXT else {}
        if data.get("event") == "log_line" and "event=while_open" in data["line"]:
            break
    assert (await ask(ws, "log_unsubscribe"))["ok"]
    await ws.close()
