# SPDX-License-Identifier: GPL-3.0-or-later
"""The DX cluster process, ``remoteg90ctl-cluster`` (decisions.md D63).

It reads no configuration file and nothing of the station: the server, its
only client, tells it the cluster's address, port and callsign over a Unix
socket (docs/cluster-protocol.md). Under systemd the socket comes from
``remoteg90ctl-cluster.socket`` (only the server's user may connect) and the
process runs as a user of its own (``DynamicUser``); without systemd it
binds ``--socket`` itself.

To the cluster it sends the callsign once, at login, and nothing else
(AGENTS.md section 10); what the cluster sends is parsed as untrusted text
(spots.py) and only whole spots go to the server.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import stat
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from remoteg90ctl.cluster.spots import (
    LOGIN_PROMPT,
    TelnetText,
    check_call,
    check_host,
    check_port,
    parse_spot,
)
from remoteg90ctl.logging_setup import kv, setup_logging

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_SOCKET = Path("/run/remoteg90ctl-cluster.sock")
SOCKET_MODE = 0o600
MAX_REQUEST_BYTES = 4096
CONNECT_TIMEOUT_S = 20.0
# The callsign goes after the cluster's prompt, or this long after connecting
# if no prompt is recognised.
LOGIN_WAIT_S = 5.0
# A cluster that sends nothing for this long is connected to again.
IDLE_TIMEOUT_S = 900.0
RETRY_DELAYS_S = (5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
MAX_CLIENT_BUFFER = 1024 * 1024  # a server that stops reading is dropped
READ_CHUNK = 4096

Target = tuple[str, int, str]  # host, port, callsign


class ClusterProcess:
    def __init__(
        self,
        *,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        login_wait_s: float = LOGIN_WAIT_S,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        retry_delays_s: Sequence[float] = RETRY_DELAYS_S,
    ) -> None:
        self._connect_timeout_s = connect_timeout_s
        self._login_wait_s = login_wait_s
        self._idle_timeout_s = idle_timeout_s
        self._retry_delays_s = tuple(retry_delays_s)
        self._client: asyncio.StreamWriter | None = None
        self._target: Target | None = None
        self._link: asyncio.Task[None] | None = None
        self._status: dict[str, Any] = {"state": "off", "message": "no cluster set"}

    # ---- The server's side

    async def serve(self, sock: socket.socket) -> None:
        server = await asyncio.start_unix_server(
            self._handle_client, sock=sock, limit=MAX_REQUEST_BYTES
        )
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # One client: the server. A new connection replaces the old one.
        if self._client is not None:
            self._client.close()
        self._client = writer
        logger.info("cluster_client_connected")
        self._send(self._status_message())
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ValueError, ConnectionError, OSError):
                    break  # too long or broken: the server reconnects
                if not line:
                    break
                self._request(line)
        finally:
            if self._client is writer:
                self._client = None
            writer.close()
            logger.info("cluster_client_disconnected")

    def _request(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._send({"v": PROTOCOL_VERSION, "event": "error", "message": "not JSON"})
            return
        if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
            self._send(
                {
                    "v": PROTOCOL_VERSION,
                    "event": "error",
                    "message": f"protocol version {PROTOCOL_VERSION} required",
                }
            )
            return
        if message.get("type") != "configure":
            self._send({"v": PROTOCOL_VERSION, "event": "error", "message": "unknown type"})
            return
        if message.get("host") is None:
            self.configure(None)
            return
        host = check_host(message.get("host"))
        port = check_port(message.get("port"))
        call = check_call(message.get("call"))
        if host is None or port is None or call is None:
            self._send(
                {"v": PROTOCOL_VERSION, "event": "error", "message": "bad host, port or callsign"}
            )
            return
        self.configure((host, port, call))

    def configure(self, target: Target | None) -> None:
        if target == self._target and (target is None or self._link is not None):
            self._send(self._status_message())
            return
        self._target = target
        if self._link is not None:
            self._link.cancel()
            self._link = None
        if target is None:
            self._set_status("off", "no cluster set")
            logger.info("cluster_off")
        else:
            self._link = asyncio.get_running_loop().create_task(self._keep_link(target))

    def _set_status(self, state: str, message: str) -> None:
        self._status = {"state": state, "message": message}
        self._send(self._status_message())

    def _status_message(self) -> dict[str, Any]:
        return {"v": PROTOCOL_VERSION, "event": "status", **self._status}

    def _send(self, message: dict[str, Any]) -> None:
        writer = self._client
        if writer is None or writer.is_closing():
            return
        transport = writer.transport
        if transport.get_write_buffer_size() > MAX_CLIENT_BUFFER:
            logger.warning("cluster_client_not_reading")
            writer.close()
            self._client = None
            return
        writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")

    # ---- The cluster's side

    async def _keep_link(self, target: Target) -> None:
        host, port, _call = target
        failures = 0
        while True:
            self._set_status("connecting", f"connecting to {host}:{port}")
            try:
                await self._session(target)
                reason = "the cluster closed the connection"
            except TimeoutError:
                reason = "no answer"
            except OSError as exc:
                reason = exc.strerror or str(exc)
            delay = self._retry_delays_s[min(failures, len(self._retry_delays_s) - 1)]
            failures += 1
            logger.warning(
                "cluster_link_lost", extra=kv(host=host, port=port, reason=reason, retry_s=delay)
            )
            self._set_status("retrying", f"{host}:{port}: {reason}; again in {delay:g} s")
            await asyncio.sleep(delay)

    async def _session(self, target: Target) -> None:
        host, port, call = target
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), self._connect_timeout_s
        )
        try:
            text = TelnetText()
            connected_at = time.monotonic()
            logged_in = False
            logger.info("cluster_connected", extra=kv(host=host, port=port))
            while True:
                wait = self._idle_timeout_s
                if not logged_in:
                    wait = max(0.0, connected_at + self._login_wait_s - time.monotonic())
                try:
                    data = await asyncio.wait_for(reader.read(READ_CHUNK), wait)
                except TimeoutError:
                    if logged_in:
                        raise
                    data = None
                if data == b"":
                    return
                lines = text.feed(data) if data else []
                if not logged_in and (
                    data is None
                    or any(LOGIN_PROMPT.search(x) for x in lines)
                    or LOGIN_PROMPT.search(text.partial)
                ):
                    # The only thing ever sent to the cluster (D63).
                    writer.write(call.encode("ascii") + b"\r\n")
                    await writer.drain()
                    logged_in = True
                    self._set_status("connected", f"{host}:{port} as {call}")
                for line in lines:
                    spot = parse_spot(line)
                    if spot is None:
                        continue
                    self._send(
                        {
                            "v": PROTOCOL_VERSION,
                            "event": "spot",
                            "spotter": spot.spotter,
                            "freq_khz": spot.freq_khz,
                            "dx": spot.dx,
                            "comment": spot.comment,
                            "utc": spot.utc,
                            "locator": spot.locator,
                            "received_ms": int(time.time() * 1000),
                        }
                    )
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


def _listening_socket(path: Path) -> socket.socket:
    """The socket from systemd (LISTEN_FDS), or one bound at ``path``."""
    if os.environ.get("LISTEN_PID") == str(os.getpid()) and os.environ.get("LISTEN_FDS") == "1":
        return socket.socket(fileno=3)
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    os.chmod(path, SOCKET_MODE)
    sock.listen(4)
    return sock


async def _run(sock: socket.socket) -> None:
    process = ClusterProcess()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    serving = asyncio.create_task(process.serve(sock))
    await stop.wait()
    serving.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serving


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="remoteg90ctl-cluster", description="remoteG90ctl DX cluster process (D63)"
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_SOCKET,
        help="Unix socket when not started by systemd",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    sock = _listening_socket(args.socket)
    logger.info("cluster_process_started", extra=kv(socket=args.socket))
    asyncio.run(_run(sock))
    return 0
