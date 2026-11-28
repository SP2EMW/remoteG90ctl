# SPDX-License-Identifier: GPL-3.0-or-later
"""The DX cluster (decisions.md D63, AGENTS.md section 10).

The process sends the cluster only the callsign, drops telnet commands and
anything that is not a whole spot; the server passes it only the address,
port and callsign, keeps the administrator's choice, and hands the spots only
to pages that subscribed. A fake cluster (TCP on loopback) and a fake cluster
process stand in for the real ones.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from guard_helpers import make_config
from import_graph import closure
from remoteg90ctl.cluster.main import ClusterProcess, _listening_socket
from remoteg90ctl.cluster.spots import (
    IAC,
    MAX_LINE_CHARS,
    SB,
    SE,
    WILL,
    TelnetText,
    check_call,
    check_host,
    check_port,
    parse_solar,
    parse_solar_row,
    parse_spot,
)
from remoteg90ctl.server.cluster import ClusterHub, valid_solar, valid_spot
from remoteg90ctl.server.core import ClientContext, ServerCore, Session
from remoteg90ctl.server.protocol import (
    ACCOUNT_PERMISSIONS,
    ADMIN_PERMISSIONS,
    REQUEST_PERMISSIONS,
    Permission,
    Request,
)
from server_helpers import async_wait_for

SPOT = "DX de SP7ABC:     14074.0  DL1XYZ       FT8 -12 dB                  1234Z JO93"

# ---- Telnet and spot lines


def test_telnet_commands_are_dropped_and_lines_come_out() -> None:
    text = TelnetText()
    data = (
        bytes([IAC, WILL, 1])
        + b"Hello\r\n"
        + bytes([IAC, SB, 24, 1, IAC, SE])
        + b"a"
        + bytes([IAC, IAC])
        + b"b\n"
    )
    assert text.feed(data) == ["Hello", "a b"]  # 255 is not printable: a space


def test_a_prompt_without_a_line_end_is_seen() -> None:
    text = TelnetText()
    assert text.feed(b"Welcome\r\nlogin: ") == ["Welcome"]
    assert text.partial == "login: "


def test_a_too_long_line_is_dropped_whole() -> None:
    text = TelnetText()
    assert text.feed(b"x" * (MAX_LINE_CHARS + 10) + b"\n" + SPOT.encode() + b"\n") == [SPOT]


def test_control_characters_become_spaces() -> None:
    assert TelnetText().feed(b"\x1b[31mred\x07\n") == ["[31mred"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (SPOT, ("SP7ABC", 14074.0, "DL1XYZ", "FT8 -12 dB", "1234", "JO93")),
        (
            "DX de DK9IP-#:   7023.0  SP2EMW  CW 12 dB 25 WPM CQ  0815Z",
            ("DK9IP-#", 7023.0, "SP2EMW", "CW 12 dB 25 WPM CQ", "0815", None),
        ),
        ("DX de W1AW:  3573.5  JA1ABC  2359Z", ("W1AW", 3573.5, "JA1ABC", "", "2359", None)),
        (
            "dx de sp7abc: 14200.0 9a1a <b>bold</b> 1200Z",
            ("SP7ABC", 14200.0, "9A1A", "<b>bold</b>", "1200", None),
        ),
    ],
)
def test_spot_lines_are_read(line: str, expected: tuple[Any, ...]) -> None:
    spot = parse_spot(line)
    assert spot is not None
    assert (spot.spotter, spot.freq_khz, spot.dx, spot.comment, spot.utc, spot.locator) == expected


@pytest.mark.parametrize(
    "line",
    [
        "",
        "To ALL de SP7ABC: hello",
        "DX de SP7ABC: 14074.0 DL1XYZ FT8 2534Z",  # no 25:34
        "DX de SP7ABC: 14074.0 DL1XYZ FT8",  # no time
        "DX de SP7ABC: 99.0 DL1XYZ FT8 1234Z",  # below 100 kHz
        "DX de SP7ABC: 14074.0 DLXYZ FT8 1234Z",  # no digit in the call
        "WWV de VE7CC <18>:   SFI=150, A=5, K=1",
    ],
)
def test_other_lines_are_no_spots(line: str) -> None:
    assert parse_spot(line) is None


def test_solar_reports_are_read() -> None:
    wwv = parse_solar("WWV de VE7CC <18>:   SFI=150, A=5, K=1, No Storms -> No Storms <VE7CC>")
    assert wwv is not None
    assert (wwv.kind, wwv.source, wwv.hour, wwv.sfi, wwv.a, wwv.k) == (
        "WWV",
        "VE7CC",
        18,
        150,
        5,
        1,
    )
    wcy = parse_solar("WCY de DK0WCY-1 <07> : K=2 expK=3 A=7 R=91 SFI=147 SA=qui GMF=qui Au=no")
    assert wcy is not None
    assert (wcy.kind, wcy.hour, wcy.sfi, wcy.a, wcy.k) == ("WCY", 7, 147, 7, 2)  # not expK
    assert parse_solar("WWV de W0MU <18Z> : SFI=132, A=12, K=3") is not None
    for line in (
        "WWV de VE7CC <25>: SFI=150, A=5, K=1",  # no 25 h
        "WWV de VE7CC <18>: no figures",
        "To ALL de SP7ABC: SFI=150",
        "WWV de VE7CC <18>: K=12",  # K is 0-9
    ):
        assert parse_solar(line) is None, line
    assert parse_spot("WWV de VE7CC <18>:   SFI=150, A=5, K=1") is None


def test_a_long_comment_is_cut() -> None:
    spot = parse_spot(f"DX de SP7ABC: 14074.0 DL1XYZ {'X' * 200} 1234Z")
    assert spot is not None
    assert len(spot.comment) == 64


def test_only_a_host_a_port_and_a_callsign_pass() -> None:
    assert check_host("dxc.example.org") == "dxc.example.org"
    assert check_host("192.0.2.1") == "192.0.2.1"
    assert check_host("2001:db8::1") == "2001:db8::1"
    assert check_host("localhost") == "localhost"
    for bad in ("", "a b", "host:7300", "-bad.org", "1.2.3", "x" * 300, 7300, None):
        assert check_host(bad) is None, bad
    assert check_port(7300) == 7300
    for bad_port in (0, 65536, "7300", True, 7300.0):
        assert check_port(bad_port) is None, bad_port
    assert check_call("sp2emw") == "SP2EMW"
    assert check_call("SP2EMW-2") == "SP2EMW-2"
    assert check_call("SP2EMW/P") == "SP2EMW/P"
    for bad_call in ("", "SP2EMW\r\nSH/DX", "SP2 EMW", "HELLO", 5):
        assert check_call(bad_call) is None, bad_call


def test_the_cluster_process_imports_nothing_of_the_radio_or_the_station() -> None:
    modules, _external = closure("remoteg90ctl.cluster.main")
    for forbidden in (
        "remoteg90ctl.guard",
        "remoteg90ctl.server",
        "remoteg90ctl.civ",
        "remoteg90ctl.config",
    ):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in modules), forbidden


# ---- The process against a fake cluster


class FakeCluster:
    """A telnet cluster on loopback that records what it is sent."""

    def __init__(self, greeting: bytes) -> None:
        self.greeting = greeting
        self.received = bytearray()
        self.writers: list[asyncio.StreamWriter] = []
        self.connections = 0
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port: int = self.server.sockets[0].getsockname()[1]
        return port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.writers.append(writer)
        writer.write(self.greeting)
        with contextlib.suppress(ConnectionError):
            while data := await reader.read(100):
                self.received.extend(data)

    def send(self, data: bytes) -> None:
        self.writers[-1].write(data)

    def drop(self) -> None:
        for writer in self.writers:
            writer.close()

    async def stop(self) -> None:
        self.drop()
        if self.server is not None:
            self.server.close()


class ProcessClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    def send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message).encode() + b"\n")

    async def next(self, event: str, **match: object) -> dict[str, Any]:
        while True:
            line = await asyncio.wait_for(self.reader.readline(), 5)
            assert line, "the process closed the connection"
            message: dict[str, Any] = json.loads(line)
            if message.get("event") == event and all(message.get(k) == v for k, v in match.items()):
                return message


@contextlib.asynccontextmanager
async def running_process(tmp_path: Path, **kwargs: Any) -> AsyncIterator[ProcessClient]:
    path = tmp_path / "c.sock"
    process = ClusterProcess(retry_delays_s=(0.1,), **kwargs)
    task = asyncio.create_task(process.serve(_listening_socket(path)))
    reader, writer = await asyncio.open_unix_connection(str(path))
    client = ProcessClient(reader, writer)
    try:
        yield client
    finally:
        writer.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        process.configure(None)


async def test_the_process_logs_in_with_the_callsign_only_and_passes_whole_spots(
    tmp_path: Path,
) -> None:
    cluster = FakeCluster(bytes([IAC, WILL, 1]) + b"Welcome to the test cluster\r\nlogin: ")
    port = await cluster.start()
    try:
        async with running_process(tmp_path) as client:
            assert (await client.next("status"))["state"] == "off"
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "sp2emw"}
            )
            await client.next("status", state="connected")
            await async_wait_for(lambda: bytes(cluster.received) == b"SP2EMW\r\n")
            cluster.send(
                b"Hello SP2EMW\r\nnot a spot\r\n" + SPOT.encode() + b"\r\nDX de X: junk\r\n"
            )
            spot = await client.next("spot")
            assert {
                k: spot[k] for k in ("spotter", "freq_khz", "dx", "comment", "utc", "locator")
            } == {
                "spotter": "SP7ABC",
                "freq_khz": 14074.0,
                "dx": "DL1XYZ",
                "comment": "FT8 -12 dB",
                "utc": "1234",
                "locator": "JO93",
            }
            # Nothing but the callsign ever went to the cluster.
            await asyncio.sleep(0.2)
            assert bytes(cluster.received) == b"SP2EMW\r\n"
    finally:
        await cluster.stop()


async def test_a_spot_right_after_the_prompt_is_not_lost(tmp_path: Path) -> None:
    # The prompt has no line end; the first spot must not be glued to it.
    cluster = FakeCluster(b"login: ")
    port = await cluster.start()
    try:
        async with running_process(tmp_path) as client:
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "SP2EMW"}
            )
            await client.next("status", state="connected")
            cluster.send(SPOT.encode() + b"\r\n")
            assert (await client.next("spot"))["dx"] == "DL1XYZ"
    finally:
        await cluster.stop()


async def test_without_a_prompt_the_callsign_goes_after_a_while(tmp_path: Path) -> None:
    cluster = FakeCluster(b"")
    port = await cluster.start()
    try:
        async with running_process(tmp_path, login_wait_s=0.2) as client:
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "SP2EMW"}
            )
            await client.next("status", state="connected")
            assert bytes(cluster.received) == b"SP2EMW\r\n"
    finally:
        await cluster.stop()


async def test_a_lost_cluster_is_connected_to_again_and_none_disconnects(tmp_path: Path) -> None:
    cluster = FakeCluster(b"login: ")
    port = await cluster.start()
    try:
        async with running_process(tmp_path) as client:
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "SP2EMW"}
            )
            await client.next("status", state="connected")
            cluster.drop()
            await client.next("status", state="retrying")
            await client.next("status", state="connected")
            assert cluster.connections == 2
            client.send({"v": 1, "type": "configure", "host": None})
            await client.next("status", state="off")
    finally:
        await cluster.stop()


def test_rows_of_the_solar_tables_are_read() -> None:
    row = parse_solar_row("28-Nov-2026   18    87   8   1 No Storms -> No Storms        <VE7CC>")
    assert row is not None
    report, ms = row
    assert (report.kind, report.source, report.hour, report.sfi, report.a, report.k) == (
        "WWV",
        "VE7CC",
        18,
        87,
        8,
        1,
    )
    assert ms == 1_795_888_800_000  # 2026-11-28 18:00 UTC
    wcy = parse_solar_row(
        "28-Nov-2026   21    87   8   1     1  59 qui   qui   no       <DK0WCY-2>"
    )
    assert wcy is not None
    assert (wcy[0].kind, wcy[0].hour) == ("WCY", 21)
    for line in (
        "Date        Hour   SFI   A   K Forecast                  Logger",
        "31-Feb-2026   18    87   8   1 x <VE7CC>",
        "28-Xyz-2026   18    87   8   1 x <VE7CC>",
        "28-Nov-2026   25    87   8   1 x <VE7CC>",
        "Sorry, command not found",
    ):
        assert parse_solar_row(line) is None, line


def test_wcy_reports_bring_the_expected_k_solar_activity_and_aurora() -> None:
    wcy = parse_solar("WCY de DK0WCY-1 <07> : K=2 expK=5 A=7 R=91 SFI=147 SA=eru GMF=act Au=yes")
    assert wcy is not None
    assert (wcy.k, wcy.exp_k, wcy.sa, wcy.au) == (2, 5, "eru", True)
    row = parse_solar_row(
        "28-Nov-2026   21    87   8   1     4  59 act   qui   no       <DK0WCY-2>"
    )
    assert row is not None
    assert (row[0].exp_k, row[0].sa, row[0].au) == (4, "act", False)
    wwv = parse_solar("WWV de VE7CC <18>:   SFI=150, A=5, K=1, No Storms")
    assert wwv is not None
    assert (wwv.exp_k, wwv.sa, wwv.au) == (None, None, None)
    # An expected K out of range and an SA that is not a word are dropped.
    bad = parse_solar("WCY de DK0WCY-1 <07> : K=2 expK=12 SA=<b>")
    assert bad is not None
    assert (bad.exp_k, bad.sa) == (None, None)


async def test_the_solar_query_goes_at_most_hourly_also_across_restarts(tmp_path: Path) -> None:
    cluster = FakeCluster(b"login: ")
    port = await cluster.start()
    now = [1_800_000_000.0]
    state = tmp_path / "state"
    state.mkdir()
    try:
        async with running_process(tmp_path, state_dir=state, clock=lambda: now[0]) as client:
            client.send({"v": 1, "type": "query_solar"})  # not connected yet: nothing
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "SP2EMW"}
            )
            await client.next("status", state="connected")
            client.send({"v": 1, "type": "query_solar"})
            await async_wait_for(
                lambda: bytes(cluster.received) == b"SP2EMW\r\nsh/wwv\r\nsh/wcy\r\n"
            )
            now[0] += 3599
            client.send({"v": 1, "type": "query_solar"})  # within the hour: nothing
            cluster.send(b"28-Nov-2026   18    87   8   1 No Storms   <VE7CC>\r\n")
            solar = await client.next("solar")
            assert (solar["sfi"], solar["report_ms"]) == (87, 1_795_888_800_000)
            assert bytes(cluster.received) == b"SP2EMW\r\nsh/wwv\r\nsh/wcy\r\n"
        # A restart: the time of the last query is read back.
        cluster.received.clear()
        async with running_process(
            tmp_path, state_dir=state, clock=lambda: now[0], login_wait_s=0.1
        ) as client:
            client.send(
                {"v": 1, "type": "configure", "host": "127.0.0.1", "port": port, "call": "SP2EMW"}
            )
            await client.next("status", state="connected")
            client.send({"v": 1, "type": "query_solar"})
            await asyncio.sleep(0.2)
            assert bytes(cluster.received) == b"SP2EMW\r\n"
            now[0] += 2  # over an hour since the last query
            client.send({"v": 1, "type": "query_solar"})
            await async_wait_for(
                lambda: bytes(cluster.received) == b"SP2EMW\r\nsh/wwv\r\nsh/wcy\r\n"
            )
    finally:
        await cluster.stop()


async def test_the_process_refuses_anything_but_a_host_a_port_and_a_callsign(
    tmp_path: Path,
) -> None:
    async with running_process(tmp_path) as client:
        client.send(
            {
                "v": 1,
                "type": "configure",
                "host": "127.0.0.1",
                "port": 7300,
                "call": "SP2EMW\r\nSH/DX",
            }
        )
        await client.next("error")
        client.send({"v": 1, "type": "send", "text": "SH/DX"})
        await client.next("error")
        client.send({"v": 2, "type": "configure", "host": None})
        await client.next("error")


async def test_the_process_stops_with_the_server_still_connected(tmp_path: Path) -> None:
    # SIGTERM must not wait for the server to hang up (systemd would kill it).
    path = tmp_path / "c.sock"
    process = ClusterProcess()
    task = asyncio.create_task(process.serve(_listening_socket(path)))
    reader, writer = await asyncio.open_unix_connection(str(path))
    await reader.readline()  # the first status
    process.close()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert await reader.read() == b""  # the server sees the end
    writer.close()


# ---- The server's side


class FakeProcess:
    """Stands in for remoteg90ctl-cluster on its socket."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.received: list[dict[str, Any]] = []
        self.writer: asyncio.StreamWriter | None = None
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_unix_server(self._handle, str(self.path))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        writer.write(b'{"v":1,"event":"status","state":"off","message":"no cluster set"}\n')
        while line := await reader.readline():
            self.received.append(json.loads(line))

    def send(self, message: dict[str, Any]) -> None:
        assert self.writer is not None
        self.writer.write(json.dumps({"v": 1, **message}).encode() + b"\n")

    async def stop(self) -> None:
        if self.writer is not None:
            self.writer.close()
        if self.server is not None:
            self.server.close()


def spot_message(**changes: object) -> dict[str, Any]:
    spot: dict[str, Any] = {
        "event": "spot",
        "spotter": "SP7ABC",
        "freq_khz": 14074.0,
        "dx": "DL1XYZ",
        "comment": "FT8 -12 dB",
        "utc": "1234",
        "locator": "JO93",
        "received_ms": 1_800_000_000_000,
    }
    spot.update(changes)
    return spot


def test_spots_from_the_process_are_checked_again() -> None:
    assert valid_spot(spot_message()) is not None
    for bad in (
        {"dx": "<script>"},
        {"comment": "x" * 65},
        {"comment": "bell\x07"},
        {"freq_khz": float("nan")},
        {"utc": "9999"},
        {"locator": "JO93 <b>"},
        {"received_ms": "now"},
    ):
        assert valid_spot(spot_message(**bad)) is None, bad


async def test_the_hub_tells_the_process_the_saved_cluster_and_relays_spots(tmp_path: Path) -> None:
    fake = FakeProcess(tmp_path / "c.sock")
    await fake.start()
    hub = ClusterHub(
        tmp_path / "c.sock",
        tmp_path / "state",
        reconnect_delay_s=0.05,
        clock=lambda: 1_800_000_000.0,
    )
    assert hub.status["state"] == "unavailable"
    hub.configure(("dxc.example.org", 7300, "SP2EMW"))
    task = asyncio.create_task(hub.run())
    try:
        await async_wait_for(lambda: len(fake.received) == 1)
        assert fake.received[0] == {
            "v": 1,
            "type": "configure",
            "host": "dxc.example.org",
            "port": 7300,
            "call": "SP2EMW",
        }
        got: list[dict[str, Any]] = []
        assert hub.subscribe("page", got.append) == []
        fake.send(spot_message())
        fake.send(spot_message(dx="<img>"))  # rejected
        fake.send(
            {"event": "status", "state": "connected", "message": "dxc.example.org:7300 as SP2EMW"}
        )
        await async_wait_for(lambda: len(got) == 2)
        assert got[0]["event"] == "cluster_spot"
        assert got[0]["spot"]["dx"] == "DL1XYZ"
        assert got[1] == {
            "v": 1,
            "event": "cluster_status",
            "state": "connected",
            "message": "dxc.example.org:7300 as SP2EMW",
        }
        hub.unsubscribe("page")
        fake.send(spot_message(dx="OK1ABC"))
        await asyncio.sleep(0.1)
        assert len(got) == 2
        # A later subscriber gets the spots kept.
        assert [s["dx"] for s in hub.subscribe("other", lambda _m: None)] == ["DL1XYZ", "OK1ABC"]
        hub.configure(None)
        await async_wait_for(lambda: len(fake.received) == 2)
        assert fake.received[1] == {"v": 1, "type": "configure", "host": None}
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await fake.stop()
    # The choice is kept across restarts.
    hub.configure(("192.0.2.1", 23, "SP2EMW-1"))
    assert ClusterHub(tmp_path / "c.sock", tmp_path / "state").target == (
        "192.0.2.1",
        23,
        "SP2EMW-1",
    )


async def test_solar_reports_reach_the_pages(tmp_path: Path) -> None:
    fake = FakeProcess(tmp_path / "c.sock")
    await fake.start()
    hub = ClusterHub(tmp_path / "c.sock", tmp_path / "state", reconnect_delay_s=0.05)
    task = asyncio.create_task(hub.run())
    try:
        got: list[dict[str, Any]] = []
        hub.subscribe("page", got.append)
        await async_wait_for(lambda: fake.writer is not None)
        report = {
            "event": "solar",
            "kind": "WWV",
            "source": "VE7CC",
            "hour": 18,
            "sfi": 150,
            "a": 5,
            "k": 1,
            "received_ms": int(time.time() * 1000),
        }
        fake.send({**report, "k": 12})  # rejected
        fake.send(report)
        await async_wait_for(lambda: any(m["event"] == "cluster_solar" for m in got))
        solar = next(m for m in got if m["event"] == "cluster_solar")["solar"]
        assert (solar["sfi"], solar["a"], solar["k"]) == (150, 5, 1)
        assert hub.solar == solar
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await fake.stop()


def test_the_last_solar_report_is_kept_six_hours_across_restarts(tmp_path: Path) -> None:
    now = [1_800_000_000.0]
    hub = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0])
    report = {
        "event": "solar",
        "kind": "WWV",
        "source": "VE7CC",
        "hour": 18,
        "sfi": 150,
        "a": 5,
        "k": 1,
    }
    hub._on_message({**report, "received_ms": int(now[0] * 1000)})
    # A restart: the report comes back from the state directory.
    again = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0])
    assert again.solar is not None
    assert again.solar["sfi"] == 150
    now[0] += 6 * 3600 - 1
    assert again.solar is not None
    now[0] += 2  # more than six hours, and no newer one
    assert again.solar is None
    # A newer report replaces it.
    again._on_message({**report, "sfi": 160, "received_ms": int(now[0] * 1000)})
    assert again.solar is not None
    assert again.solar["sfi"] == 160


def test_spots_are_kept_across_restarts_for_an_hour(tmp_path: Path) -> None:
    now = [1_800_000_000.0]
    hub = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0])
    hub._on_message(spot_message(received_ms=int(now[0] * 1000) - 50 * 60 * 1000))
    hub._on_message(spot_message(dx="OK1ABC", received_ms=int(now[0] * 1000)))
    hub.save_spots()
    again = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0])
    assert [s["dx"] for s in again.subscribe("p", lambda _m: None)] == ["DL1XYZ", "OK1ABC"]
    now[0] += 11 * 60  # the first is now over an hour old
    later = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0])
    assert [s["dx"] for s in later.subscribe("p", lambda _m: None)] == ["OK1ABC"]
    now[0] += 3600  # all older than an hour: the list is empty
    assert (
        ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now[0]).subscribe(
            "p", lambda _m: None
        )
        == []
    )
    # A damaged file means no spots, not an error.
    (tmp_path / "state" / "cluster-spots.json").write_text("[1, {}, ")
    assert ClusterHub(tmp_path / "c.sock", tmp_path / "state").subscribe("p", lambda _m: None) == []


def test_the_newest_solar_report_is_kept(tmp_path: Path) -> None:
    hub = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: 1_800_000_000.0)
    base = {
        "event": "solar",
        "kind": "WWV",
        "source": "VE7CC",
        "a": 5,
        "k": 1,
        "received_ms": 1_800_000_000_000,
    }
    hub._on_message({**base, "hour": 18, "sfi": 90, "report_ms": 1_799_990_000_000})
    hub._on_message(
        {**base, "hour": 15, "sfi": 80, "report_ms": 1_799_980_000_000}
    )  # older row: not kept
    assert hub.solar is not None
    assert hub.solar["sfi"] == 90


def test_the_solar_history_gives_the_newest_and_the_ones_before(tmp_path: Path) -> None:
    now = 1_800_000_000.0
    hub = ClusterHub(tmp_path / "c.sock", tmp_path / "state", clock=lambda: now)
    base = {"event": "solar", "source": "VE7CC", "hour": 18, "a": 5, "received_ms": int(now * 1000)}
    hub._on_message({**base, "kind": "WWV", "sfi": 90, "k": 2, "report_ms": 1_799_980_000_000})
    hub._on_message(
        {
            **base,
            "kind": "WCY",
            "source": "DK0WCY-2",
            "sfi": 87,
            "k": 3,
            "exp_k": 5,
            "sa": "eru",
            "au": False,
            "report_ms": 1_799_990_000_000,
        }
    )
    hub._on_message(
        {**base, "kind": "WWV", "sfi": 90, "k": 2, "report_ms": 1_799_980_000_000}
    )  # again: once
    assert [r["sfi"] for r in hub.solar_history] == [90, 87]
    assert hub.solar is not None
    assert (hub.solar["exp_k"], hub.solar["sa"]) == (5, "eru")
    # Kept across a restart.
    assert [
        r["k"]
        for r in ClusterHub(
            tmp_path / "c.sock", tmp_path / "state", clock=lambda: now
        ).solar_history
    ] == [2, 3]
    for bad in ({"sa": "<b>"}, {"exp_k": 10}, {"au": "yes"}):
        assert valid_solar({**base, "kind": "WCY", "k": 1, **bad}) is None, bad


def test_solar_reports_from_the_process_are_checked_again() -> None:
    good = {
        "kind": "WCY",
        "source": "DK0WCY-1",
        "hour": 7,
        "sfi": 147,
        "a": 7,
        "k": 2,
        "received_ms": 1,
    }
    assert valid_solar(good) is not None
    for bad in (
        {"kind": "XXX"},
        {"hour": 24},
        {"sfi": "150"},
        {"k": 10},
        {"source": "<b>"},
        {"sfi": None, "a": None, "k": None},
    ):
        assert valid_solar({**good, **bad}) is None, bad


def test_a_damaged_setting_means_no_cluster(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "cluster.json").write_text('{"host": "a b", "port": 1, "call": "SP2EMW"}')
    assert ClusterHub(tmp_path / "c.sock", state).target is None
    (state / "cluster.json").write_text("not json")
    assert ClusterHub(tmp_path / "c.sock", state).target is None


def test_reading_spots_is_receiving_and_setting_the_cluster_is_the_administrators() -> None:
    assert REQUEST_PERMISSIONS["cluster_subscribe"] is Permission.RECEIVE
    assert REQUEST_PERMISSIONS["cluster_unsubscribe"] is Permission.RECEIVE
    assert REQUEST_PERMISSIONS["cluster_config"] is Permission.ADMIN
    assert REQUEST_PERMISSIONS["cluster_set"] is Permission.ADMIN


async def test_only_the_administrator_sets_the_cluster(tmp_path: Path) -> None:
    core = ServerCore(make_config(tmp_path))
    user = Session(
        ClientContext("web", "s1", permissions=ACCOUNT_PERMISSIONS, account="SP7ABC"),
        lambda _m: None,
    )
    admin = Session(
        ClientContext("web", "s2", permissions=ADMIN_PERMISSIONS, account="SP2EMW"), lambda _m: None
    )
    fields = {"host": "dxc.example.org", "port": 7300, "call": "SP2EMW"}
    refused = await core.handle(user, Request(1, "cluster_set", fields))
    assert refused["error"] == "forbidden"
    assert (await core.handle(user, Request(2, "cluster_config", {})))["error"] == "forbidden"
    answer = await core.handle(user, Request(3, "cluster_subscribe", {}))
    assert answer["ok"] is True
    assert answer["spots"] == []
    assert answer["status"]["state"] == "unavailable"
    bad = await core.handle(admin, Request(4, "cluster_set", {**fields, "call": "SP2EMW\nSH/DX"}))
    assert bad["error"] == "invalid_value"
    assert (await core.handle(admin, Request(5, "cluster_set", fields)))["ok"] is True
    config = await core.handle(admin, Request(6, "cluster_config", {}))
    assert (config["host"], config["port"], config["call"]) == ("dxc.example.org", 7300, "SP2EMW")
    assert (await core.handle(admin, Request(7, "cluster_set", {"host": None})))["ok"] is True
    assert core.cluster.target is None
