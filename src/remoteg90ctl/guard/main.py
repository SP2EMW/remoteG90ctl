# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard process: Unix socket server around :class:`GuardCore`.

Single thread, one ``selectors`` loop. Each iteration handles socket I/O, runs
the safety checks (:meth:`GuardCore.poll`) and only then, after a complete
iteration, sends ``WATCHDOG=1`` (AGENTS.md rule 12). Nothing in the loop
blocks: sockets are non-blocking and a server that stops reading is
disconnected, which ends any transmission.

At most one client (the server) is connected; further connections are
refused and logged (decisions.md D24.5).
"""

import argparse
import contextlib
import logging
import os
import selectors
import signal
import socket
import stat
import struct
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import FrameType

from remoteg90ctl.config import ConfigError, load_config
from remoteg90ctl.guard.core import GuardCore
from remoteg90ctl.guard.notify import Notifier
from remoteg90ctl.guard.port import make_port_factory
from remoteg90ctl.guard.protocol import (
    MAX_LINE_BYTES,
    ErrorCode,
    RequestError,
    encode,
    error,
    parse_request,
)
from remoteg90ctl.logging_setup import kv, setup_logging

logger = logging.getLogger(__name__)

LOOP_INTERVAL_S = 0.05
# Wake-ups on the port with nothing read, in a row, before falling back to the timeout.
EMPTY_WAKEUPS_LIMIT = 50
MAX_OUTPUT_BYTES = 256 * 1024
SOCKET_MODE = 0o660
_PEERCRED = struct.Struct("3i")


class GuardServer:
    def __init__(self, core: GuardCore, socket_path: Path, notifier: Notifier) -> None:
        self.core = core
        self.socket_path = socket_path
        self.notifier = notifier
        self._selector = selectors.DefaultSelector()
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._inbuf = bytearray()
        self._outbuf = bytearray()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._stop = False
        # The open port's descriptor in the selector, so a reply from the radio
        # wakes the loop at once instead of after up to LOOP_INTERVAL_S.
        self._port_wakeup: tuple[int, int] | None = None
        self._port_wakeup_failed: int | None = None  # open count given up on
        self._empty_wakeups = 0
        self._last_watchdog = float("-inf")
        # True once the socket listens and the port has been set up (READY=1 sent).
        self.ready = False

    def request_stop(self) -> None:
        """Ask the loop to exit. Safe to call from a signal handler or another thread."""
        self._stop = True
        with contextlib.suppress(OSError):
            self._wake_w.send(b"\0")

    def serve_forever(self) -> None:
        """Run until :meth:`request_stop`. Any exception lowers the lines first."""
        try:
            self._listen()
            self.core.start()
            self._flush_events()
            self.notifier.ready()
            self.ready = True
            self.notifier.status(f"port {self.core.port_state}")
            logger.info("guard_ready", extra=kv(socket=self.socket_path))
            while not self._stop:
                self.iterate()
        except BaseException:
            self.core.emergency_lower()
            logger.exception("guard_failed")
            self.core.shutdown(clean=False)
            raise
        else:
            self.notifier.stopping()
            self.core.shutdown(clean=True)
            logger.info("guard_stopped")
        finally:
            self._close_sockets()

    def iterate(self) -> None:
        """One iteration of the safety loop."""
        self._watch_port()
        port_ready = False
        for key, _mask in self._selector.select(LOOP_INTERVAL_S):
            if self._port_wakeup is not None and key.fileobj == self._port_wakeup[1]:
                port_ready = True
            if key.fileobj is self._listener:
                self._accept()
            elif key.fileobj is self._wake_r:
                with contextlib.suppress(OSError):
                    self._wake_r.recv(64)
            elif key.fileobj is self._client:
                self._read_client()
        self.core.poll()
        self._check_wakeup(port_ready)
        self._flush_events()
        self._send_pending()
        self._safety_loop_completed()

    def _watch_port(self) -> None:
        """Keep the selector on the current port descriptor (or none).

        Failures only cost the early wake-up: the loop still reads the port on
        its timeout. A closed descriptor is unregistered by number; epoll has
        already dropped it.
        """
        wanted = self.core.port_wakeup()
        if wanted is not None and wanted[0] == self._port_wakeup_failed:
            wanted = None  # given up on this open: read on the timeout
        if wanted == self._port_wakeup:
            return
        if self._port_wakeup is not None:
            with contextlib.suppress(KeyError, ValueError, OSError):
                self._selector.unregister(self._port_wakeup[1])
            self._port_wakeup = None
        if wanted is not None:
            try:
                self._selector.register(wanted[1], selectors.EVENT_READ)
            except (KeyError, ValueError, OSError) as exc:
                logger.warning("port_wakeup_unavailable", extra=kv(error=exc))
                self._port_wakeup_failed = wanted[0]
                return
            self._port_wakeup = wanted
            self._empty_wakeups = 0

    def _check_wakeup(self, port_ready: bool) -> None:
        """A descriptor that keeps waking the loop with nothing to read (a device
        in a bad state) would spin it: then fall back to the timeout for this open."""
        if not port_ready or self.core.last_read_bytes > 0:
            self._empty_wakeups = 0
            return
        self._empty_wakeups += 1
        if self._empty_wakeups >= EMPTY_WAKEUPS_LIMIT and self._port_wakeup is not None:
            logger.warning("port_wakeup_disabled", extra=kv(empty_wakeups=self._empty_wakeups))
            self._port_wakeup_failed = self._port_wakeup[0]
            self._watch_port()

    # Watchdog

    def _safety_loop_completed(self) -> None:
        interval = self.notifier.watchdog_interval_s
        if interval is None:
            return
        now = time.monotonic()
        if now - self._last_watchdog >= interval:
            self.notifier.watchdog()
            self._last_watchdog = now

    # Sockets

    def _listen(self) -> None:
        path = self.socket_path
        _remove_stale_socket(path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        listener.bind(str(path))
        # No connection is possible before listen(), so tightening the mode here
        # leaves no window (and avoids changing the process-wide umask).
        os.chmod(path, SOCKET_MODE)
        listener.listen(2)
        listener.setblocking(False)
        self._listener = listener
        self._selector.register(listener, selectors.EVENT_READ)
        self._selector.register(self._wake_r, selectors.EVENT_READ)

    def _accept(self) -> None:
        if self._listener is None:
            return
        try:
            conn, _ = self._listener.accept()
        except BlockingIOError:
            return
        conn.setblocking(False)
        pid, uid, gid = _peer_credentials(conn)
        if self._client is not None:
            logger.warning("second_client_rejected", extra=kv(pid=pid, uid=uid, gid=gid))
            with contextlib.suppress(OSError):
                conn.send(encode(error(None, ErrorCode.BUSY, "another client is connected")))
            conn.close()
            return
        logger.info("client_connected", extra=kv(pid=pid, uid=uid, gid=gid))
        self._client = conn
        self._inbuf.clear()
        self._outbuf.clear()
        self._selector.register(conn, selectors.EVENT_READ)

    def _read_client(self) -> None:
        conn = self._client
        if conn is None:
            return
        try:
            data = conn.recv(65536)
        except BlockingIOError:
            return
        except OSError as exc:
            self._disconnect(f"receive failed: {exc}")
            return
        if not data:
            self._disconnect("closed by peer")
            return
        self._inbuf.extend(data)
        while self._client is not None and (end := self._inbuf.find(b"\n")) >= 0:
            line = bytes(self._inbuf[:end])
            del self._inbuf[: end + 1]
            if len(line) > MAX_LINE_BYTES:
                self._disconnect("line too long")
                return
            self._queue(self._handle_line(line))
        if self._client is not None and len(self._inbuf) > MAX_LINE_BYTES:
            self._disconnect("line too long")

    def _handle_line(self, line: bytes) -> dict[str, object]:
        try:
            request = parse_request(line)
        except RequestError as exc:
            logger.warning("bad_request", extra=kv(code=exc.code, reason=exc.message))
            return error(exc.request_id, exc.code, exc.message)
        logger.debug("request", extra=kv(id=request.id, type=request.type, **request.fields))
        response = self.core.handle(request)
        logger.debug(
            "response",
            extra=kv(id=request.id, ok=response.get("ok"), error=response.get("error")),
        )
        return response

    def _queue(self, message: dict[str, object]) -> None:
        if self._client is None:
            return
        self._outbuf.extend(encode(message))
        if len(self._outbuf) > MAX_OUTPUT_BYTES:
            self._disconnect("client is not reading")

    def _send_pending(self) -> None:
        conn = self._client
        if conn is None or not self._outbuf:
            return
        try:
            sent = conn.send(self._outbuf)
        except BlockingIOError:
            return
        except OSError as exc:
            self._disconnect(f"send failed: {exc}")
            return
        del self._outbuf[:sent]

    def _flush_events(self) -> None:
        for message in self.core.take_events():
            self._queue(message)

    def _disconnect(self, reason: str) -> None:
        conn, self._client = self._client, None
        if conn is None:
            return
        logger.warning("client_disconnected", extra=kv(reason=reason))
        self._selector.unregister(conn)
        conn.close()
        self._inbuf.clear()
        self._outbuf.clear()
        # Immediate release (decisions.md D24.6).
        self.core.client_disconnected()

    def _close_sockets(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
            with contextlib.suppress(FileNotFoundError):
                self.socket_path.unlink()
        self._selector.close()
        self._wake_r.close()
        self._wake_w.close()


def _remove_stale_socket(path: Path) -> None:
    """Remove a socket left by a dead guard; refuse if one is still answering."""
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
        raise RuntimeError(f"another guard is listening on {path}")
    finally:
        probe.close()


def _peer_credentials(conn: socket.socket) -> tuple[int, int, int]:
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED.size)
    except OSError:
        return (-1, -1, -1)
    pid, uid, gid = _PEERCRED.unpack(raw)
    return pid, uid, gid


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="remoteg90ctl-guard", description="remoteG90ctl PTT guard (sole owner of the port)"
    )
    parser.add_argument("--config", type=Path, required=True, help="path to the TOML config")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"remoteg90ctl-guard: {exc}\n")
        return 2
    setup_logging(config.logging.level)

    core = GuardCore(config, make_port_factory(config.radio))
    server = GuardServer(core, config.guard.socket_path, Notifier())
    install_signal_handlers(server)
    try:
        server.serve_forever()
    except Exception:  # already logged with traceback in serve_forever
        return 1
    return 0


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def install_signal_handlers(server: GuardServer) -> None:
    """Lower the lines and stop the loop on SIGTERM, SIGINT and SIGHUP."""

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        # Lines first, then everything else (AGENTS.md rule 2).
        server.core.emergency_lower()
        logger.warning("signal_received", extra=kv(signal=signal.Signals(signum).name))
        server.request_stop()

    for sig in STOP_SIGNALS:
        signal.signal(sig, on_signal)


if __name__ == "__main__":
    sys.exit(main())
