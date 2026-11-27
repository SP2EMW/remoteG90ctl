# SPDX-License-Identifier: GPL-3.0-or-later
"""The server's side of the DX cluster (decisions.md D63).

The server never reaches the network itself (it stays on loopback): it
connects to the ``remoteg90ctl-cluster`` process over a Unix socket
(docs/cluster-protocol.md), tells it the cluster the administrator set
(address, port, callsign; nothing else can be passed) and receives the spots.
They are kept for SPOT_KEEP_S and sent to the pages that subscribed.

Spots carry no path to the radio or to transmitting: a page that tunes to a
spot does it with an ordinary ``set_frequency``/``set_mode`` (control
permission).
"""

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remoteg90ctl.cluster.spots import MAX_COMMENT_CHARS, check_call, check_host, check_port
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.protocol import event

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
CONFIG_FILE = "cluster.json"
SOLAR_FILE = "cluster-solar.json"
# The last solar report is kept this long, also across restarts, unless a newer
# one comes (owner's decision 2026-11-27).
SOLAR_KEEP_S = 6 * 3600.0
RECONNECT_DELAY_S = 2.0
MAX_LINE_BYTES = 4096
SPOT_KEEP_S = 3600.0  # spots older than this are forgotten
SPOTS_KEPT = 2000

Message = dict[str, Any]
CLUSTER_REQUESTS = frozenset(
    {"cluster_subscribe", "cluster_unsubscribe", "cluster_config", "cluster_set"}
)
Target = tuple[str, int, str]

_CALL_TEXT = re.compile(r"[A-Z0-9/#-]{2,20}")
_UTC = re.compile(r"[0-2]\d[0-5]\d")
_LOCATOR = re.compile(r"[A-R]{2}\d{2}(?:[A-X]{2})?")


def valid_spot(message: Message) -> Message | None:
    """The spot fields of a message from the cluster process, checked again
    (it is the one process with network access), or None."""
    spotter, dx, freq = message.get("spotter"), message.get("dx"), message.get("freq_khz")
    comment, utc, locator = message.get("comment"), message.get("utc"), message.get("locator")
    received = message.get("received_ms")
    if not (isinstance(spotter, str) and _CALL_TEXT.fullmatch(spotter)):
        return None
    if not (isinstance(dx, str) and _CALL_TEXT.fullmatch(dx)):
        return None
    if (
        isinstance(freq, bool)
        or not isinstance(freq, int | float)
        or not math.isfinite(freq)
        or freq <= 0
    ):
        return None
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARS:
        return None
    if any(not " " <= c <= "~" for c in comment):
        return None
    if not (isinstance(utc, str) and _UTC.fullmatch(utc)):
        return None
    if locator is not None and not (isinstance(locator, str) and _LOCATOR.fullmatch(locator)):
        return None
    if isinstance(received, bool) or not isinstance(received, int):
        return None
    return {
        "spotter": spotter,
        "dx": dx,
        "freq_khz": float(freq),
        "comment": comment,
        "utc": utc,
        "locator": locator,
        "received_ms": received,
    }


def valid_solar(message: Message) -> Message | None:
    """A solar report (WWV/WCY) from the cluster process, checked again, or None."""
    kind, source, hour = message.get("kind"), message.get("source"), message.get("hour")
    received = message.get("received_ms")
    if kind not in ("WWV", "WCY") or not (isinstance(source, str) and _CALL_TEXT.fullmatch(source)):
        return None
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        return None
    if isinstance(received, bool) or not isinstance(received, int):
        return None
    figures: dict[str, int | None] = {}
    for name, high in (("sfi", 999), ("a", 400), ("k", 9)):
        value = message.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high
        ):
            return None
        figures[name] = value
    if all(v is None for v in figures.values()):
        return None
    return {"kind": kind, "source": source, "hour": hour, **figures, "received_ms": received}


def check_target(host: object, port: object, call: object) -> Target | str:
    """The cluster to connect to, or why it is refused."""
    checked_host = check_host(host)
    if checked_host is None:
        return "'host' must be a host name or an IP address"
    checked_port = check_port(port)
    if checked_port is None:
        return "'port' must be 1-65535"
    checked_call = check_call(call)
    if checked_call is None:
        return "'call' must be a callsign (optionally with -N)"
    return checked_host, checked_port, checked_call


class ClusterHub:
    def __init__(
        self,
        socket_path: Path,
        state_dir: Path,
        *,
        reconnect_delay_s: float = RECONNECT_DELAY_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._socket_path = socket_path
        self._config_path = state_dir / CONFIG_FILE
        self._reconnect_delay_s = reconnect_delay_s
        self._clock = clock
        self._writer: asyncio.StreamWriter | None = None
        self._process_status: Message = {"state": "off", "message": "no cluster set"}
        self._spots: deque[Message] = deque(maxlen=SPOTS_KEPT)
        # The last solar report (WWV/WCY) the cluster sent by itself.
        self._solar_path = state_dir / SOLAR_FILE
        self._solar: Message | None = self._load_solar()
        # Subscribers: a callable sending one message to the page.
        self._subscribers: dict[object, Callable[[Message], None]] = {}
        self.target: Target | None = self._load()

    # ---- The last solar report

    @property
    def solar(self) -> Message | None:
        """The last report, or None when none came in the last SOLAR_KEEP_S."""
        solar = self._solar
        if solar is None or self._clock() * 1000 - solar["received_ms"] > SOLAR_KEEP_S * 1000:
            return None
        return solar

    def _load_solar(self) -> Message | None:
        try:
            data = json.loads(self._solar_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("cluster_solar_unreadable", extra=kv(path=self._solar_path, error=exc))
            return None
        return valid_solar(data) if isinstance(data, dict) else None

    def _keep_solar(self, solar: Message) -> None:
        self._solar = solar
        tmp = self._solar_path.with_name(SOLAR_FILE + ".tmp")
        try:
            self._solar_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(solar), encoding="utf-8")
            tmp.replace(self._solar_path)
        except OSError as exc:  # shown anyway; only a restart forgets it
            logger.warning("cluster_solar_not_saved", extra=kv(error=exc))

    # ---- Configuration (the administrator's)

    def _load(self) -> Target | None:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("cluster_config_unreadable", extra=kv(path=self._config_path, error=exc))
            return None
        if not isinstance(data, dict) or data.get("host") is None:
            return None
        target = check_target(data.get("host"), data.get("port"), data.get("call"))
        if isinstance(target, str):
            logger.warning(
                "cluster_config_invalid", extra=kv(path=self._config_path, problem=target)
            )
            return None
        return target

    def configure(self, target: Target | None) -> None:
        """Keep the cluster to use (None: none) and tell the process."""
        data = (
            {"host": None}
            if target is None
            else {"host": target[0], "port": target[1], "call": target[2]}
        )
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._config_path.with_name(CONFIG_FILE + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._config_path)
        self.target = target
        logger.info(
            "cluster_configured",
            extra=kv(host=data.get("host"), port=data.get("port"), call=data.get("call")),
        )
        self._send_target()

    # ---- Pages

    @property
    def status(self) -> Message:
        if self._writer is None:
            return {
                "state": "unavailable",
                "message": "the DX cluster service (remoteg90ctl-cluster) is not running",
            }
        return dict(self._process_status)

    def subscribe(self, key: object, send: Callable[[Message], None]) -> list[Message]:
        """Recent spots, oldest first; then each new one goes to ``send``."""
        self._subscribers[key] = send
        self._forget_old()
        return list(self._spots)

    def unsubscribe(self, key: object) -> None:
        self._subscribers.pop(key, None)

    def _publish(self, message: Message) -> None:
        for send in list(self._subscribers.values()):
            send(message)

    def _forget_old(self) -> None:
        oldest = (self._clock() - SPOT_KEEP_S) * 1000
        while self._spots and self._spots[0]["received_ms"] < oldest:
            self._spots.popleft()

    # ---- The cluster process

    async def run(self) -> None:
        """Keep a connection to the cluster process until cancelled."""
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(
                    str(self._socket_path), limit=MAX_LINE_BYTES
                )
            except OSError as exc:
                logger.debug("cluster_process_connect_failed", extra=kv(error=exc))
                await asyncio.sleep(self._reconnect_delay_s)
                continue
            self._writer = writer
            logger.info("cluster_process_connected", extra=kv(socket=self._socket_path))
            self._send_target()
            try:
                await self._read(reader)
            finally:
                self._writer = None
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
                self._publish(self._status_event())
            await asyncio.sleep(self._reconnect_delay_s)

    def _send_target(self) -> None:
        writer = self._writer
        if writer is None:
            return
        target = self.target
        message: Message = {"v": PROTOCOL_VERSION, "type": "configure", "host": None}
        if target is not None:
            message.update(host=target[0], port=target[1], call=target[2])
        writer.write(json.dumps(message).encode() + b"\n")

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await reader.readline()
            except (ValueError, ConnectionError, OSError) as exc:
                logger.warning("cluster_process_read_failed", extra=kv(error=exc))
                return
            if not line:
                return
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("v") == PROTOCOL_VERSION:
                self._on_message(message)

    def _on_message(self, message: Message) -> None:
        kind = message.get("event")
        if kind == "spot":
            spot = valid_spot(message)
            if spot is None:
                logger.debug("cluster_spot_rejected")
                return
            self._spots.append(spot)
            self._forget_old()
            self._publish(event("cluster_spot", spot=spot))
        elif kind == "solar":
            solar = valid_solar(message)
            if solar is not None:
                self._keep_solar(solar)
                self._publish(event("cluster_solar", solar=solar))
        elif kind == "status":
            state, text = message.get("state"), message.get("message")
            if isinstance(state, str) and isinstance(text, str):
                self._process_status = {"state": state[:20], "message": text[:200]}
                self._publish(self._status_event())
        elif kind == "error":
            logger.warning(
                "cluster_process_error", extra=kv(message=str(message.get("message"))[:200])
            )

    def _status_event(self) -> Message:
        return event("cluster_status", **self.status)
