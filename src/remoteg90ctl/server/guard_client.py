# SPDX-License-Identifier: GPL-3.0-or-later
"""Connection from the server to the guard (docs/guard-protocol.md).

The server is the guard's only client. The connection is re-established
automatically; while it is down every request fails with
:class:`GuardUnavailableError` and nothing is transmitted (the guard itself
ends any transmission the moment the connection drops).
"""

import asyncio
import contextlib
import itertools
import json
import logging
import socket
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

GUARD_PROTOCOL_VERSION = 1
REQUEST_TIMEOUT_S = 2.0
RECONNECT_DELAY_S = 1.0
MAX_LINE_BYTES = 64 * 1024

EventHandler = Callable[[dict[str, Any]], None]
ConnectionHandler = Callable[[bool], None]


class GuardUnavailableError(Exception):
    """The guard is not connected or did not answer in time."""


class GuardClient:
    def __init__(
        self,
        path: Path,
        *,
        on_event: EventHandler,
        on_connection: ConnectionHandler,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        reconnect_delay_s: float = RECONNECT_DELAY_S,
    ) -> None:
        self._path = path
        self._on_event = on_event
        self._on_connection = on_connection
        self._request_timeout_s = request_timeout_s
        self._reconnect_delay_s = reconnect_delay_s
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._write_lock = asyncio.Lock()
        self._connected = asyncio.Event()
        # Pid of the guard process, from the socket credentials (SO_PEERCRED);
        # shown in the resource window (server/sysmon.py), None while not connected.
        self.peer_pid: int | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def wait_connected(self, timeout: float) -> bool:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected.wait(), timeout)
        return self.connected

    async def run(self) -> None:
        """Keep a connection to the guard until cancelled."""
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(
                    str(self._path), limit=MAX_LINE_BYTES
                )
            except OSError as exc:
                logger.debug("guard_connect_failed", extra=kv(error=exc))
                await asyncio.sleep(self._reconnect_delay_s)
                continue
            self._writer = writer
            self.peer_pid = _peer_pid(writer)
            self._connected.set()
            logger.info("guard_connected", extra=kv(socket=self._path))
            self._on_connection(True)
            try:
                await self._read_loop(reader)
            finally:
                self._drop_connection("connection closed")
            await asyncio.sleep(self._reconnect_delay_s)

    async def request(self, kind: str, **fields: object) -> dict[str, Any]:
        """Send one request and return the guard's response (``ok`` true or false)."""
        writer = self._writer
        if writer is None:
            raise GuardUnavailableError("guard is not connected")
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        line = json.dumps(
            {"v": GUARD_PROTOCOL_VERSION, "id": request_id, "type": kind, **fields},
            separators=(",", ":"),
        )
        started = time.monotonic()
        logger.debug("guard_request", extra=kv(id=request_id, type=kind, **fields))
        try:
            async with self._write_lock:
                writer.write(line.encode() + b"\n")
                await writer.drain()
            response = await asyncio.wait_for(future, self._request_timeout_s)
            logger.debug(
                "guard_response",
                extra=kv(
                    id=request_id,
                    ok=response.get("ok"),
                    error=response.get("error"),
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                ),
            )
            return response
        except TimeoutError:
            # An unresponsive guard is treated as gone; dropping the connection
            # also makes the guard end any transmission.
            self._drop_connection("request timed out")
            raise GuardUnavailableError("guard did not answer in time") from None
        except (ConnectionError, OSError) as exc:
            self._drop_connection(f"write failed: {exc}")
            raise GuardUnavailableError(f"guard connection failed: {exc}") from None
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        writer = self._writer
        self._drop_connection("closing")
        if writer is not None:
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await reader.readline()
            except (ValueError, ConnectionError, OSError) as exc:
                logger.error("guard_read_failed", extra=kv(error=exc))
                return
            if not line:
                return
            try:
                message = json.loads(line)
            except ValueError:
                logger.error("guard_sent_invalid_json", extra=kv(line=line[:200]))
                return
            if not isinstance(message, dict):
                logger.error("guard_sent_non_object")
                return
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "event" in message:
            fields = {k: v for k, v in message.items() if k not in ("v", "event")}
            logger.debug("guard_event", extra=kv(name=message["event"], **fields))
            self._on_event(message)
            return
        request_id = message.get("id")
        future = self._pending.get(request_id) if isinstance(request_id, int) else None
        if future is None:
            # e.g. the guard's "busy" refusal, which has no request id.
            logger.warning("guard_unsolicited_response", extra=kv(message=message))
            if message.get("error") == "busy":
                self._drop_connection("guard has another client")
            return
        if not future.done():
            future.set_result(message)

    def _drop_connection(self, reason: str) -> None:
        writer, self._writer = self._writer, None
        self.peer_pid = None
        self._connected.clear()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(GuardUnavailableError(f"guard connection lost: {reason}"))
        self._pending.clear()
        if writer is None:
            return
        writer.close()
        logger.warning("guard_disconnected", extra=kv(reason=reason))
        self._on_connection(False)


def _peer_pid(writer: asyncio.StreamWriter) -> int | None:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return None
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    pid: int = struct.unpack("3i", creds)[0]
    return pid if pid > 0 else None
