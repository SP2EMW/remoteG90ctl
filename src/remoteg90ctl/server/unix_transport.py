# SPDX-License-Identifier: GPL-3.0-or-later
"""Unix socket transport for local clients (the CLI).

The socket has mode ``0660``: access is limited to the service user and its
group. Peer credentials (``SO_PEERCRED``) identify the client in the
:class:`ClientContext`. Requests on one connection are handled in order.
"""

import asyncio
import contextlib
import logging
import os
import secrets
import socket
import stat
import struct
from pathlib import Path

from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.core import ClientContext, Message, ServerCore, Session
from remoteg90ctl.server.protocol import MAX_LINE_BYTES, RequestError, encode, error, parse_request

logger = logging.getLogger(__name__)

SOCKET_MODE = 0o660
SEND_TIMEOUT_S = 2.0
FLUSH_TIMEOUT_S = 0.5
_PEERCRED = struct.Struct("3i")


class UnixSocketTransport:
    def __init__(self, core: ServerCore, path: Path) -> None:
        self._core = core
        self._path = path
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        _remove_stale_socket(self._path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        sock.bind(str(self._path))
        # No connection is possible before listen() (done by start_unix_server),
        # so tightening the mode here leaves no window.
        os.chmod(self._path, SOCKET_MODE)
        self._server = await asyncio.start_unix_server(
            self._serve_client, sock=sock, limit=MAX_LINE_BYTES + 1
        )
        logger.info("server_listening", extra=kv(socket=self._path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            for task in list(self._clients):
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*self._clients, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    async def _serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        uid, pid = _peer(writer)
        context = ClientContext("cli", secrets.token_hex(4), uid=uid, pid=pid)
        outgoing: asyncio.Queue[Message | None] = asyncio.Queue()
        session = self._core.open_session(context, outgoing.put_nowait)
        sender = asyncio.create_task(self._send_loop(writer, outgoing))
        try:
            await self._receive_loop(reader, outgoing, session)
        finally:
            # Release first (immediate TX release on disconnect), then tidy up.
            await self._core.close_session(session)
            # Let queued messages (e.g. tx_ended on shutdown) reach the client.
            outgoing.put_nowait(None)
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(sender, FLUSH_TIMEOUT_S)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            if task is not None:
                self._clients.discard(task)

    async def _receive_loop(
        self,
        reader: asyncio.StreamReader,
        outgoing: asyncio.Queue[Message | None],
        session: Session,
    ) -> None:
        while not session.closed:
            try:
                line = await reader.readline()
            except ValueError:
                logger.warning("client_line_too_long", extra=kv(source=session.context.source))
                return
            except (ConnectionError, OSError):
                return
            if not line.endswith(b"\n"):
                return  # EOF (possibly after a partial line)
            try:
                request = parse_request(line[:-1])
            except RequestError as exc:
                outgoing.put_nowait(error(exc.request_id, exc.code, exc.message))
                continue
            outgoing.put_nowait(await self._core.handle(session, request))

    @staticmethod
    async def _send_loop(
        writer: asyncio.StreamWriter, outgoing: asyncio.Queue[Message | None]
    ) -> None:
        while (message := await outgoing.get()) is not None:
            writer.write(encode(message))
            try:
                await asyncio.wait_for(writer.drain(), SEND_TIMEOUT_S)
            except (TimeoutError, ConnectionError, OSError):
                # A client that does not read is disconnected (and loses any TX).
                writer.transport.abort()
                return


def _peer(writer: asyncio.StreamWriter) -> tuple[int | None, int | None]:
    sock = writer.get_extra_info("socket")
    if sock is None:
        return None, None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    except OSError:
        return None, None
    pid, uid, _gid = _PEERCRED.unpack(raw)
    return uid, pid


def _remove_stale_socket(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"{path} exists and is not a socket")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except OSError:
        path.unlink()
    else:
        raise RuntimeError(f"another server is listening on {path}")
    finally:
        probe.close()
