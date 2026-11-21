# SPDX-License-Identifier: GPL-3.0-or-later
"""Browser transport (stage 2b): same-origin checks (D34), permissions, waterfall frames.

Runs on the simulator and a synthetic I/Q source; no radio, no sound card.
"""

import asyncio
import dataclasses
import re
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from guard_helpers import make_config
from remoteg90ctl.config import Config, ServerConfig
from remoteg90ctl.iq.dsp import quantize
from remoteg90ctl.server.accounts import AccountStore
from remoteg90ctl.server.logbook import Logbook
from remoteg90ctl.server.main import StartupError, check_startup
from remoteg90ctl.server.protocol import ACCOUNT_PERMISSIONS, REQUEST_PERMISSIONS, Permission
from remoteg90ctl.server.waterfall import (
    MAX_DB,
    MIN_DB,
    QUEUE_LINES,
    SPEEDS,
    LineInfo,
    Subscriber,
    WaterfallHub,
    decode_header,
    encode_line,
    pool_bins,
)
from remoteg90ctl.server.web import STATIC_DIR, WebTransport, allowed_hosts
from server_helpers import System, async_wait_for, running_system
from test_server_iq import iq_config

LISTEN = "127.0.0.1:8078"
OURS = {"Host": LISTEN, "Origin": f"http://{LISTEN}"}
FIRST = {"callsign": "SP1ABC", "locator": "JO82lj", "password": "correct horse battery"}


async def start_logged_in(
    system: System, proxy_hosts: tuple[str, ...] = (), *, require_identity: bool = True
) -> TestClient[Any, Any]:
    """A test web server with the first account created; the client keeps its cookie."""
    transport = WebTransport(
        system.core,
        (LISTEN,),
        proxy_hosts,
        accounts=AccountStore(system.config.server.state_dir),
        logbook=Logbook(system.config.server.state_dir),
        require_tailnet_identity=require_identity,
    )
    client: TestClient[Any, Any] = TestClient(TestServer(transport.make_app()))
    await client.start_server()
    response = await client.post("/api/first-account", json=FIRST, headers=OURS)
    assert response.status == 200, await response.text()
    return client


def web_config(tmp_path: Path, **iq: object) -> Config:
    """A config with I/Q but without listen: tests never bind the real port 8078."""
    return make_config(tmp_path, iq=iq_config(tmp_path, **iq))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest_asyncio.fixture
async def web(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    config = web_config(tmp_path)
    async with running_system(config) as system:
        # The test server listens on a random port; the Host checks use LISTEN.
        client = await start_logged_in(system)
        try:
            yield system, client
        finally:
            await client.close()


async def ask(ws: Any, kind: str, **fields: object) -> dict[str, Any]:
    """Send a request and return its response, skipping events and binary frames."""
    ask.ids = getattr(ask, "ids", 0) + 1  # type: ignore[attr-defined]
    request_id = ask.ids  # type: ignore[attr-defined]
    await ws.send_json({"v": 1, "id": request_id, "type": kind, **fields})
    while True:
        message = await ws.receive(timeout=5)
        if message.type is WSMsgType.TEXT:
            data: dict[str, Any] = message.json()
            if data.get("id") == request_id:
                return data


# Same origin only (decisions.md D34)


def test_allowed_hosts() -> None:
    assert allowed_hosts((LISTEN,)) == {
        "127.0.0.1:8078",
        "[::1]:8078",
        "localhost:8078",
    }


async def test_page_is_served_to_our_host(web: tuple[System, TestClient[Any, Any]]) -> None:
    _, client = web
    response = await client.get("/", headers={"Host": LISTEN})
    assert response.status == 200
    assert "remoteG90ctl" in await response.text()
    script = await client.get("/static/app.js", headers={"Host": "localhost:8078"})
    assert script.status == 200
    # Checked every time, so an update reaches the browser (2026-11-18).
    for answer in (response, script):
        assert answer.headers.get("Cache-Control") == "no-cache"
    worklet = await client.get("/static/audio-worklet.js", headers={"Host": LISTEN})
    assert worklet.headers.get("Cache-Control") == "no-cache"


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example:8078"},  # DNS rebinding
        {"Host": "127.0.0.1:9999"},
        {"Host": LISTEN, "Origin": "http://evil.example"},
    ],
)
async def test_foreign_host_or_origin_is_refused(
    web: tuple[System, TestClient[Any, Any]],
    headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, client = web
    response = await client.get("/", headers=headers)
    assert response.status == 403
    assert "web_request_refused" in caplog.text


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": LISTEN},  # no Origin
        {"Host": LISTEN, "Origin": "http://evil.example"},
        {"Host": LISTEN, "Origin": "null"},
        {"Host": "evil.example", "Origin": "http://evil.example"},
    ],
)
async def test_foreign_websocket_is_refused(
    web: tuple[System, TestClient[Any, Any]], headers: dict[str, str]
) -> None:
    _, client = web
    with pytest.raises(WSServerHandshakeError) as info:
        await client.ws_connect("/ws", headers=headers)
    assert info.value.status == 403


# Permissions: receive, control and antenna tuning (D43), never PTT


def test_accounts_may_transmit_but_not_manage_accounts() -> None:
    """D48: every account has receive, control, tune and transmit; account management
    over the server protocol is the CLI's only."""
    assert Permission.TRANSMIT in ACCOUNT_PERMISSIONS
    for kind in ("tx_request", "tx_renew", "tx_release"):
        assert REQUEST_PERMISSIONS[kind] in ACCOUNT_PERMISSIONS
    for kind in ("account_list", "account_password", "account_delete"):
        assert REQUEST_PERMISSIONS[kind] not in ACCOUNT_PERMISSIONS


async def test_browser_transmits_only_in_a_data_mode_and_with_a_lease(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """Stage 4: USB would send the radio's microphone, so only U-D/L-D (owner's decision)."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "tx_request"))["error"] == "not_data_mode"
    assert not system.ptt_active
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    granted = await ask(ws, "tx_request")
    assert granted["ok"], granted
    assert system.ptt_active
    assert (await ask(ws, "tx_release", lease=granted["lease"]))["ok"]
    assert not system.ptt_active
    await ws.close()


async def test_transmitting_is_announced_for_the_whole_lease(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """The page's red bar follows the lease, not only the power meter (zero between words)."""
    system, client = web
    system.harness.port.radio.power_meter = 0  # PTT without modulation
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]

    async def announced() -> dict[str, Any]:
        while True:
            message = await ws.receive(timeout=5)
            if (
                message.type is WSMsgType.TEXT
                and message.json().get("event") == "radio_transmitting"
            ):
                data: dict[str, Any] = message.json()
                return data

    await ws.send_json({"v": 1, "id": 990, "type": "tx_request"})
    started = await announced()
    assert (started["active"], started["leased"]) == (True, True)
    lease = system.core._tx.token if system.core._tx is not None else ""
    await ws.send_json({"v": 1, "id": 991, "type": "tx_release", "lease": lease})
    ended = await announced()
    assert (ended["active"], ended["leased"]) == (False, False)
    await ws.close()


async def test_browser_may_start_an_antenna_tuning(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """Owner's decision D43: tuning from the browser before authentication."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_atu", on=True))["ok"]
    assert (await ask(ws, "get_atu"))["on"] is True
    assert (await ask(ws, "atu_tune"))["ok"]
    assert system.harness.port.radio.tunings == 1
    (record,) = system.harness.tx_events("atu_tune")
    assert record["source"].startswith("web ")
    assert (await ask(ws, "tx_request"))["error"] == "not_data_mode"
    await ws.close()
    assert not system.ptt_active


async def test_browser_can_tune(web: tuple[System, TestClient[Any, Any]]) -> None:
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_frequency", hz=14_074_000))["ok"]
    assert system.harness.port.radio.frequency_hz == 14_074_000
    await ws.close()


# Waterfall lines


async def test_subscribed_browser_receives_lines(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "get_frequency"))["ok"]
    response = await ask(ws, "waterfall_subscribe", bins=1024)
    assert response["ok"]
    assert response["bins"] == 1024
    while (message := await ws.receive(timeout=5)).type is not WSMsgType.BINARY:
        pass
    line = decode_header(message.data)
    assert line["type"] == 0x01
    assert line["bins"] == 1024
    data = line["data"]
    assert isinstance(data, bytes)
    assert len(data) == 1024
    assert line["span_hz"] == 48_000
    assert line["center_hz"] == 7_074_000  # simulator VFO; no offset configured
    assert line["calibrated"] is False
    await ws.close()


@pytest.mark.parametrize(("speed", "every"), list(SPEEDS.items()))
async def test_speed_lowers_the_line_rate(
    web: tuple[System, TestClient[Any, Any]], speed: str, every: int
) -> None:
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    response = await ask(ws, "waterfall_subscribe", bins=1024, speed=speed)
    assert response["ok"]
    assert response["speed"] == speed
    iq = system.config.iq
    assert iq is not None
    assert response["line_rate"] == iq.line_rate / every
    await ws.close()


async def test_speed_defaults_to_fast(web: tuple[System, TestClient[Any, Any]]) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    response = await ask(ws, "waterfall_subscribe", bins=1024)
    assert response["speed"] == "fast"
    assert response["line_rate"] == 20.0
    await ws.close()


@pytest.mark.parametrize("speed", ["turbo", "", 2, None, True])
async def test_invalid_speed_is_refused(
    web: tuple[System, TestClient[Any, Any]], speed: object
) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    response = await ask(ws, "waterfall_subscribe", bins=1024, speed=speed)
    assert response["error"] == "invalid_value"
    await ws.close()


@pytest.mark.parametrize("bins", [0, 100, 128, 8192, "1024", True])
async def test_invalid_bins_are_refused(
    web: tuple[System, TestClient[Any, Any]], bins: object
) -> None:
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "waterfall_subscribe", bins=bins))["error"] == "invalid_value"
    await ws.close()


async def test_scale_follows_the_vfo(web: tuple[System, TestClient[Any, Any]]) -> None:
    """Turning the radio's dial changes the waterfall centre (stage 2b)."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    await ask(ws, "waterfall_subscribe", bins=1024)
    system.harness.port.radio.frequency_hz = 21_100_000  # the dial, not CAT
    await async_wait_for(lambda: system.core.radio_hz == 21_100_000, timeout=3)
    while True:
        message = await ws.receive(timeout=5)
        if message.type is WSMsgType.TEXT and message.json().get("event") == "radio_state":
            assert message.json()["hz"] == 21_100_000
            break
    await ws.close()


async def test_frequency_follows_the_dial_with_the_waterfall_off(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """A browser without the waterfall (switched off to save the link) still sees the VFO."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "status"))["ok"]
    assert system.core.waterfall is not None
    assert system.core.waterfall.subscriber_count == 0
    system.harness.port.radio.frequency_hz = 18_100_000  # the dial, not CAT
    while True:
        message = await ws.receive(timeout=5)
        assert message.type is WSMsgType.TEXT  # no waterfall lines
        if message.json().get("event") == "radio_state":
            assert message.json()["hz"] == 18_100_000
            break
    await ws.close()


async def test_offset_for_the_mode_calibrates_the_centre(tmp_path: Path) -> None:
    config = web_config(tmp_path, center_offset_hz={"USB": -21.5})
    async with running_system(config) as system:
        system.core.radio_hz, system.core.radio_mode = 21_100_000, "USB"
        assert system.core._waterfall_center() == (21_100_000 - 21.5, True)
        system.core.radio_mode = "CW"
        assert system.core._waterfall_center() == (21_100_000, False)


def test_pooling_keeps_the_maximum() -> None:
    line = np.array([-90, -80, -90, -90, -30, -90, -90, -90], dtype=np.float32)
    assert pool_bins(line, 4).tolist() == [-80, -90, -30, -90]
    assert pool_bins(line, 8) is line


def test_pooling_by_pairs_matches_the_reduction() -> None:
    rng = np.random.default_rng(3)
    line = rng.uniform(-130, -10, 8192).astype(np.float32)
    for bins in (4096, 1024, 256):
        expected = line.reshape(bins, -1).max(axis=1)
        np.testing.assert_array_equal(pool_bins(line, bins), expected)
    odd = rng.uniform(-130, -10, 12).astype(np.float32)  # a group of 3: the reduction
    np.testing.assert_array_equal(pool_bins(odd, 4), odd.reshape(4, 3).max(axis=1))


def test_frame_round_trip() -> None:
    line = np.linspace(-130, -10, 256).astype(np.float32)
    frame = encode_line(7, line, LineInfo(21_100_000.0, True, 48_000.0))
    header = decode_header(frame)
    assert header["seq"] == 7
    assert header["calibrated"] is True
    assert header["center_hz"] == 21_100_000.0
    data = header["data"]
    assert isinstance(data, bytes)
    assert data[0] == 0
    assert data[-1] == 255
    unknown = decode_header(encode_line(1, line, LineInfo(None, True, 48_000.0)))
    assert unknown["center_hz"] is None
    assert unknown["calibrated"] is False
    assert unknown["blanked"] is False
    blanked = decode_header(encode_line(2, line, LineInfo(21e6, True, 48_000.0, blanked=True)))
    assert blanked["blanked"] is True
    assert blanked["calibrated"] is True


def test_slow_speed_merges_lines_by_their_maximum() -> None:
    """Every n lines become one, so a short signal between sent lines is not lost."""
    hub = WaterfallHub(lambda: (7_074_000.0, True), 48_000.0)
    fast, slow = hub.subscribe(256), hub.subscribe(256, every=SPEEDS["slow"])
    lines = [np.full(256, -100.0, dtype=np.float32) for _ in range(8)]
    lines[1][10] = -40.0  # a short burst in the second line only
    for line in lines:
        hub.on_line(line)
    assert fast.queue.qsize() == 8
    assert slow.queue.qsize() == 2
    first = decode_header(slow.queue.get_nowait())
    second = decode_header(slow.queue.get_nowait())
    assert first["seq"] == 4
    assert second["seq"] == 8
    expected = np.full(256, -100.0, dtype=np.float32)
    expected[10] = -40.0
    assert first["data"] == quantize(expected, MIN_DB, MAX_DB).tobytes()
    assert second["data"] == quantize(lines[4], MIN_DB, MAX_DB).tobytes()


def test_merged_line_is_blanked_when_any_line_was() -> None:
    hub = WaterfallHub(lambda: (None, False), 48_000.0)
    medium = hub.subscribe(256, every=SPEEDS["medium"])
    line = np.full(256, -100.0, dtype=np.float32)
    hub.on_line(line, blanked=True)
    hub.on_line(line)
    hub.on_line(line)
    hub.on_line(line)
    assert decode_header(medium.queue.get_nowait())["blanked"] is True
    assert decode_header(medium.queue.get_nowait())["blanked"] is False


async def test_slow_client_loses_lines_instead_of_memory() -> None:
    subscriber = Subscriber(256)
    for i in range(QUEUE_LINES + 5):
        subscriber.offer(bytes([i]))
    assert subscriber.queue.qsize() == QUEUE_LINES
    assert subscriber.dropped == 5
    assert (await subscriber.queue.get()) == bytes([5])  # the oldest were dropped


# Start-up


def test_web_listens_on_tcp_loopback_only(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    unix = dataclasses.replace(
        config, server=ServerConfig(config.server.socket_path, ("unix:/run/x.sock",))
    )
    with pytest.raises(StartupError, match="TCP loopback"):
        check_startup(unix)


async def test_server_serves_the_web_on_its_listen_address(tmp_path: Path) -> None:
    import aiohttp  # noqa: PLC0415

    port = free_port()
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    config = dataclasses.replace(
        config,
        server=ServerConfig(
            config.server.socket_path, (f"127.0.0.1:{port}",), state_dir=config.server.state_dir
        ),
    )
    async with running_system(config), aiohttp.ClientSession() as session:
        await asyncio.sleep(0.1)
        async with session.get(f"http://127.0.0.1:{port}/") as response:
            assert response.status == 200


# HTTPS proxy names (decisions.md D38): tailscale serve on loopback

PROXY = "thinkpad.example.ts.net"
# What `tailscale serve` adds for a tailnet user (D57).
IDENTITY = {"Tailscale-User-Login": "operator@example.com"}


@pytest_asyncio.fixture
async def proxied(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    async with running_system(web_config(tmp_path)) as system:
        client = await start_logged_in(system, (PROXY,))
        try:
            yield system, client
        finally:
            await client.close()


async def test_proxy_name_is_served_over_https(
    proxied: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = proxied
    assert (await client.get("/", headers={"Host": PROXY, **IDENTITY})).status == 200
    ws = await client.ws_connect(
        "/ws", headers={"Host": PROXY, "Origin": f"https://{PROXY}", **IDENTITY}
    )
    assert (await ask(ws, "status"))["ok"]
    assert (await ask(ws, "tx_request"))["error"] == "not_data_mode"
    await ws.close()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": PROXY, "Origin": f"http://{PROXY}", **IDENTITY},  # plain HTTP
        {"Host": PROXY, "Origin": "https://evil.example", **IDENTITY},
        {"Host": PROXY, **IDENTITY},  # WebSocket without Origin
        {"Host": PROXY, "Origin": f"https://{PROXY}"},  # no identity: Tailscale Funnel
        {"Host": "other.example.ts.net", "Origin": "https://other.example.ts.net"},
        {"Host": LISTEN, "Origin": f"https://{LISTEN}"},  # loopback name over https
    ],
)
async def test_proxy_name_rules(
    proxied: tuple[System, TestClient[Any, Any]], headers: dict[str, str]
) -> None:
    _, client = proxied
    with pytest.raises(WSServerHandshakeError) as info:
        await client.ws_connect("/ws", headers=headers)
    assert info.value.status == 403


async def test_tailscale_funnel_is_refused(
    proxied: tuple[System, TestClient[Any, Any]],
) -> None:
    """D57: Funnel serves the tailnet name to the public internet without the
    identity header; neither the page nor the login answers it."""
    _, client = proxied
    page = await client.get("/", headers={"Host": PROXY})
    assert page.status == 403
    assert "no Tailscale identity" in await page.text()
    login = await client.post(
        "/api/login",
        json={"callsign": FIRST["callsign"], "password": FIRST["password"]},
        headers={"Host": PROXY, "Origin": f"https://{PROXY}"},
    )
    assert login.status == 403
    empty = {"Host": PROXY, "Tailscale-User-Login": ""}
    assert (await client.get("/", headers=empty)).status == 403
    # The loopback name on the computer itself needs no identity.
    assert (await client.get("/", headers=OURS)).status == 200


async def test_a_loopback_host_through_the_proxy_is_refused(
    proxied: tuple[System, TestClient[Any, Any]],
) -> None:
    """Security review 2026-11-14, Z1: `tailscale serve` passes the client's Host on
    unchanged, so a client that is not a browser could send `Host: 127.0.0.1:…`
    through Funnel and skip D57. The proxy always sets X-Forwarded-Host: such a
    request needs the tailnet name and the identity."""
    _, client = proxied
    through_proxy = {**OURS, "X-Forwarded-Host": OURS["Host"], "X-Forwarded-For": "203.0.113.7"}
    for path in ("/", "/api/session"):
        assert (await client.get(path, headers=through_proxy)).status == 403, path
    created = await client.post(
        "/api/first-account",
        json={"callsign": "XX1BAD", "locator": "JO82", "password": "long enough password"},
        headers=through_proxy,
    )
    assert created.status == 403
    # Even with an identity: a tailnet user's browser always sends the tailnet name.
    assert (await client.get("/", headers={**through_proxy, **IDENTITY})).status == 403


async def test_a_funnel_request_is_refused_whatever_it_carries(
    proxied: tuple[System, TestClient[Any, Any]],
) -> None:
    """Z1: `tailscale serve` marks Funnel requests (and removes a client's copy)."""
    _, client = proxied
    funnel = {"Host": PROXY, "X-Forwarded-Host": PROXY, "Tailscale-Funnel-Request": "?1"}
    assert (await client.get("/", headers={**funnel, **IDENTITY})).status == 403
    assert (await client.get("/", headers={**OURS, "Tailscale-Funnel-Request": "?1"})).status == 403
    # A tailnet user's request as `tailscale serve` passes it on is still let in.
    tailnet = {
        "Host": PROXY,
        "X-Forwarded-Host": PROXY,
        "X-Forwarded-For": "100.64.0.2",
        **IDENTITY,
    }
    assert (await client.get("/", headers=tailnet)).status == 200


async def test_tagged_devices_can_be_let_in(tmp_path: Path) -> None:
    """server.require_tailnet_identity = false: a deliberate choice for tagged devices."""
    async with running_system(web_config(tmp_path)) as system:
        client = await start_logged_in(system, (PROXY,), require_identity=False)
        try:
            assert (await client.get("/", headers={"Host": PROXY})).status == 200
        finally:
            await client.close()


def test_speeds_match_the_page() -> None:
    """web/src/app.ts SPEED_MERGE and index.html repeat these; change them together."""
    assert SPEEDS == {"fast": 1, "medium": 2, "slow": 4, "slower": 10, "slowest": 20}
    page = (Path(__file__).parent.parent / "web/src/app.ts").read_text()
    assert "{ fast: 1, medium: 2, slow: 4, slower: 10, slowest: 20 }" in page


async def test_subscriptions_need_the_receive_permission(
    web: tuple[System, TestClient[Any, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review 2026-11-10, 10a: requests handled by the web transport itself
    went round the permission check of ServerCore.handle."""
    from remoteg90ctl.server import web as web_module  # noqa: PLC0415
    from remoteg90ctl.server.protocol import Permission  # noqa: PLC0415

    monkeypatch.setattr(web_module, "ACCOUNT_PERMISSIONS", frozenset({Permission.CONTROL}))
    monkeypatch.setattr(web_module, "ADMIN_PERMISSIONS", frozenset({Permission.CONTROL}))
    _, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    for kind, fields in (
        ("waterfall_subscribe", {"bins": 256}),
        ("audio_subscribe", {"codec": "raw"}),
        ("system_subscribe", {}),
        ("log_subscribe", {}),
    ):
        assert (await ask(ws, kind, **fields))["error"] == "forbidden", kind
    assert (await ask(ws, "get_frequency"))["error"] == "forbidden"
    await ws.close()


def test_the_page_refers_to_its_own_files_under_static() -> None:
    """Every local src/href in index.html points at a file the server serves."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    local = [
        ref
        for ref in re.findall(r'(?:src|href)="([^"]+)"', page)
        if not ref.startswith(("https://", "http://", "#", "/api/"))
    ]
    assert local
    for ref in local:
        assert ref.startswith("/static/"), ref
        assert (STATIC_DIR / ref.removeprefix("/static/")).is_file(), ref


async def test_renewals_do_not_wait_behind_a_slow_request(
    web: tuple[System, TestClient[Any, Any]],
) -> None:
    """A request waiting for the radio (CAT timeouts) must not hold the page's
    renewals back: on the S740 an FT8 transmission ended with client_silent while
    the page was alive (2026-11-13). The lease still needs the page's renewals."""
    system, client = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    granted = await ask(ws, "tx_request")
    assert granted["ok"], granted
    lease = granted["lease"]

    handle = system.core.handle

    async def slow(session: Any, request: Any) -> Any:
        if request.type == "get_frequency":
            await asyncio.sleep(3.0)  # longer than the 2 s lease timeout
        return await handle(session, request)

    system.core.handle = slow  # type: ignore[method-assign]
    await ws.send_json({"v": 1, "id": 900, "type": "get_frequency"})
    for n in range(7):
        await ws.send_json({"v": 1, "id": 901 + n, "type": "tx_renew", "lease": lease})
        await asyncio.sleep(0.5)
    answers: dict[int, dict[str, Any]] = {}
    while len(answers) < 8:
        message = await ws.receive(timeout=5)
        if message.type is WSMsgType.TEXT and isinstance(message.json().get("id"), int):
            answers[message.json()["id"]] = message.json()
    renewed = sum(1 for n in range(7) if answers[901 + n].get("ok"))
    assert renewed == 7, answers
    assert system.ptt_active  # still transmitting after 3.5 s
    assert answers[900]["ok"]  # the slow request is answered too
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    assert not system.ptt_active
    await ws.close()
