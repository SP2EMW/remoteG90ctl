# SPDX-License-Identifier: GPL-3.0-or-later
"""Accounts and login (decisions.md D48, AGENTS.md rule 16).

No session, no permission; the first account is made on a fresh installation
and is the only one that manages accounts; one account at a time has pages
open; an ended session closes its pages and so ends their transmit lease.
"""

import asyncio
import errno
import json
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import DummyCookieJar, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from remoteg90ctl import cli
from remoteg90ctl.server.accounts import (
    ACCOUNTS_FILE,
    FREE_FAILURES,
    SESSION_LIFETIME_S,
    SESSIONS_FILE,
    AccountsError,
    AccountStore,
)
from remoteg90ctl.server.web import COOKIE, WebTransport
from server_helpers import System, async_wait_for, running_system
from test_web import FIRST, LISTEN, OURS, ask, web_config

SECOND = {"callsign": "SP2XYZ", "locator": "JO92", "password": "another long password"}
THIRD = {"callsign": "SP3AAA", "locator": "JO82", "password": "a third long password"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


# The store


def test_first_account_is_the_administrator(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    assert store.needs_first_account
    first = store.create("sp1abc", "jo82LJ", FIRST["password"])
    second = store.create(SECOND["callsign"], SECOND["locator"], SECOND["password"])
    assert (first.callsign, first.locator, first.admin) == ("SP1ABC", "JO82lj", True)
    assert second.admin is False
    assert not store.needs_first_account
    with pytest.raises(AccountsError) as info:
        store.create("SP1ABC", "JO82", "yet another password")
    assert info.value.code == "account_exists"


@pytest.mark.parametrize(
    ("callsign", "locator", "password"),
    [
        ("", "JO82", "long enough password"),
        ("SP 1ABC", "JO82", "long enough password"),
        ("ABCDEF", "JO82", "long enough password"),  # no digit
        ("SP1ABC", "JO8", "long enough password"),
        ("SP1ABC", "ZZ82", "long enough password"),
        ("SP1ABC", "JO82", "short"),
        ("SP1ABC", "JO82", None),
    ],
)
def test_invalid_accounts_are_refused(
    tmp_path: Path, callsign: str, locator: str, password: object
) -> None:
    with pytest.raises(AccountsError) as info:
        AccountStore(tmp_path).create(callsign, locator, password)
    assert info.value.code == "invalid_value"


def test_files_hold_no_password_or_token_and_are_private(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    token = store.new_session("SP1ABC")
    for name in (ACCOUNTS_FILE, SESSIONS_FILE):
        path = tmp_path / name
        text = path.read_text()
        assert FIRST["password"] not in text
        assert token not in text
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = AccountStore(tmp_path)  # a restart keeps accounts and sessions
    account = again.session_account(token)
    assert account is not None
    assert account.callsign == "SP1ABC"


def test_login_and_throttling(tmp_path: Path) -> None:
    monotonic = Clock()
    store = AccountStore(tmp_path, monotonic=monotonic)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    assert store.check_login("sp1abc", FIRST["password"]).callsign == "SP1ABC"
    for _ in range(FREE_FAILURES):
        with pytest.raises(AccountsError) as info:
            store.check_login("SP1ABC", "wrong password!")
        assert info.value.code == "login_failed"
    with pytest.raises(AccountsError):
        store.check_login("SP1ABC", "wrong password!")
    with pytest.raises(AccountsError) as info:
        store.check_login("SP1ABC", FIRST["password"])  # even the right one must wait
    assert info.value.code == "login_throttled"
    monotonic.now += 2.0
    assert store.check_login("SP1ABC", FIRST["password"]).callsign == "SP1ABC"
    with pytest.raises(AccountsError) as info:
        store.check_login("NOONE1", FIRST["password"])
    assert info.value.code == "login_failed"


def test_sessions_expire_and_are_revoked(tmp_path: Path) -> None:
    clock = Clock()
    store = AccountStore(tmp_path, clock=clock)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    store.create(SECOND["callsign"], SECOND["locator"], SECOND["password"])
    token = store.new_session("SP1ABC")
    clock.now += SESSION_LIFETIME_S - 1
    assert store.session_account(token) is not None
    clock.now += 2
    assert store.session_account(token) is None  # 30 days (owner's decision)
    token = store.new_session("SP1ABC")
    store.set_password("SP1ABC", "a brand new password")
    assert store.session_account(token) is None  # a new password ends every session
    other = store.new_session("SP2XYZ")
    store.delete("SP2XYZ")
    assert store.session_account(other) is None
    with pytest.raises(AccountsError):
        store.delete("SP1ABC")  # the first account stays


def test_an_expired_session_is_not_a_live_one(tmp_path: Path) -> None:
    """Code review 2026-11-10, point 6: rule 16, a lost session ends the open page."""
    clock = Clock()
    store = AccountStore(tmp_path, clock=clock)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    token = store.new_session("SP1ABC")
    assert store.session_ids("SP1ABC") == {store.session_id(token)}
    clock.now += SESSION_LIFETIME_S + 1
    assert store.session_ids("SP1ABC") == set()  # the page that holds it is closed


@pytest.mark.parametrize("content", ["{", '{"version": 1, "accounts": [{}]}', '{"version": 9}'])
def test_a_damaged_file_locks_the_browser_out(tmp_path: Path, content: str) -> None:
    """Never a first-account form over an unreadable file: anyone could take the station."""
    (tmp_path / ACCOUNTS_FILE).write_text(content)
    store = AccountStore(tmp_path)
    assert store.broken is not None
    assert not store.needs_first_account
    for attempt in (
        lambda: store.create("SP1ABC", "JO82", "long enough password"),
        lambda: store.check_login("SP1ABC", "long enough password"),
    ):
        with pytest.raises(AccountsError) as info:
            attempt()
        assert info.value.code == "accounts_unavailable"


# The web server


@pytest_asyncio.fixture
async def site(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    async with running_system(web_config(tmp_path)) as system:
        transport = WebTransport(
            system.core, (LISTEN,), accounts=AccountStore(system.config.server.state_dir)
        )
        client: TestClient[Any, Any] = TestClient(
            TestServer(transport.make_app()), cookie_jar=DummyCookieJar()
        )
        await client.start_server()
        try:
            yield system, client
        finally:
            await client.close()


async def post(
    client: TestClient[Any, Any], path: str, body: dict[str, Any], token: str | None = None
) -> tuple[int, dict[str, Any], str | None]:
    headers = {**OURS, **({"Cookie": f"{COOKIE}={token}"} if token else {})}
    response = await client.post(path, data=json.dumps(body), headers=headers)
    cookie = response.cookies.get(COOKIE)
    return response.status, await response.json(), cookie.value if cookie else None


async def page(client: TestClient[Any, Any], token: str | None) -> Any:
    headers = {**OURS, **({"Cookie": f"{COOKIE}={token}"} if token else {})}
    return await client.ws_connect("/ws", headers=headers)


async def test_without_a_session_there_is_only_the_login(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    assert (await client.get("/", headers=OURS)).status == 200
    session = await (await client.get("/api/session", headers=OURS)).json()
    assert session["first_account_needed"] is True
    assert session["account"] is None
    with pytest.raises(WSServerHandshakeError) as info:
        await page(client, None)
    assert info.value.status == 401
    with pytest.raises(WSServerHandshakeError):
        await page(client, "made-up-token")


async def test_the_first_account_once_then_login(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    status, body, token = await post(client, "/api/first-account", FIRST)
    assert status == 200
    assert body["account"]["admin"] is True
    assert token
    status, body, _ = await post(client, "/api/first-account", SECOND)
    assert (status, body["error"]) == (403, "forbidden")  # only on a fresh installation
    status, body, _ = await post(client, "/api/login", {**FIRST, "password": "wrong password"})
    assert (status, body["error"]) == (401, "login_failed")
    status, _, token = await post(client, "/api/login", FIRST)
    assert status == 200
    assert token
    ws = await page(client, token)
    assert (await ask(ws, "get_frequency"))["ok"]
    await ws.close()


async def test_a_post_without_origin_is_refused(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    response = await client.post(
        "/api/first-account", data=json.dumps(FIRST), headers={"Host": LISTEN}
    )
    assert response.status == 403


async def test_one_account_at_a_time(site: tuple[System, TestClient[Any, Any]]) -> None:
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    status, _, _ = await post(client, "/api/accounts", SECOND, admin)
    assert status == 200
    ws = await page(client, admin)
    status, body, _ = await post(client, "/api/login", SECOND)
    assert (status, body["error"]) == (409, "account_busy")
    assert "SP1ABC" in body["message"]
    second = await page(client, admin)  # the same account on another device
    await second.close()
    await ws.close()
    await asyncio.sleep(0.05)
    status, _, token = await post(client, "/api/login", SECOND)
    assert status == 200
    assert token


async def test_logout_ends_the_transmission(site: tuple[System, TestClient[Any, Any]]) -> None:
    """Rule 16: losing the session during TX ends the lease like a dropped connection."""
    system, client = site
    _, _, token = await post(client, "/api/first-account", FIRST)
    ws = await page(client, token)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "tx_request", antenna_confirmed=True))["ok"]
    assert system.ptt_active
    await post(client, "/api/logout", {}, token)
    await async_wait_for(lambda: not system.ptt_active)
    with pytest.raises(WSServerHandshakeError):
        await page(client, token)


async def test_only_the_first_account_manages_accounts(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)
    status, body, _ = await post(client, "/api/accounts", {**SECOND, "callsign": "SP3QQQ"}, other)
    assert (status, body["error"]) == (403, "forbidden")
    status, _, _ = await post(client, "/api/accounts", {**SECOND, "callsign": "SP3QQQ"}, None)
    assert status == 401
    ws = await page(client, other)
    response = await client.delete(
        "/api/accounts/SP2XYZ", headers={**OURS, "Cookie": f"{COOKIE}={admin}"}
    )
    assert response.status == 200
    msg = await ws.receive(timeout=5)
    assert msg.type.name in ("CLOSE", "CLOSED", "CLOSING")  # its page is closed


async def test_password_change(site: tuple[System, TestClient[Any, Any]]) -> None:
    _, client = site
    _, _, token = await post(client, "/api/first-account", FIRST)
    status, _, _ = await post(
        client,
        "/api/password",
        {"old_password": "wrong password", "new_password": "a brand new password"},
        token,
    )
    assert status == 401
    status, _, new_token = await post(
        client,
        "/api/password",
        {"old_password": FIRST["password"], "new_password": "a brand new password"},
        token,
    )
    assert status == 200
    assert new_token
    assert new_token != token
    with pytest.raises(WSServerHandshakeError):
        await page(client, token)  # the old session ended
    ws = await page(client, new_token)
    await ws.close()


async def test_cli_manages_accounts(
    site: tuple[System, TestClient[Any, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    system, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    socket = str(system.config.server.socket_path)

    def run(*args: str) -> int:
        return cli.main(["--socket", socket, *args])

    assert await asyncio.to_thread(run, "user", "list") == 0
    out = capsys.readouterr().out
    assert "SP1ABC" in out
    assert "administrator" in out
    assert "SP2XYZ" in out
    assert (
        await asyncio.to_thread(run, "user", "passwd", "sp1abc", "--password", "reset password 1")
        == 0
    )
    status, _, _ = await post(client, "/api/login", {**FIRST, "password": "reset password 1"})
    assert status == 200
    assert await asyncio.to_thread(run, "user", "delete", "SP2XYZ") == 0
    status, _, _ = await post(client, "/api/login", SECOND)
    assert status == 401


# Settings on the account (D48 part C)


async def test_page_settings_are_kept_on_the_account(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    _, _, token = await post(client, "/api/first-account", FIRST)
    headers = {**OURS, "Cookie": f"{COOKIE}={token}"}
    page = {"remoteg90ctl.tuneStep": "500", "remoteg90ctl.txGain": "-3"}
    response = await client.put("/api/settings", data=json.dumps({"page": page}), headers=headers)
    assert response.status == 200
    answer = await (await client.get("/api/settings", headers=headers)).json()
    assert answer["page"] == page
    bad = await client.put("/api/settings", data=json.dumps({"page": {"x": 1}}), headers=headers)
    assert bad.status == 400
    assert (await client.get("/api/settings", headers=OURS)).status == 401


async def test_radio_settings_are_saved_and_restored_at_login(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Owner's decision 2026-11-08: the radio is put back as the station left it."""
    system, client = site
    system.core._browser_connected = lambda: True  # type: ignore[method-assign]
    _, _, token = await post(client, "/api/first-account", FIRST)
    ws = await page(client, token)
    assert (await ask(ws, "set_frequency", hz=14_074_000))["ok"]
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_agc", agc="fast"))["ok"]
    store = system.core.accounts
    assert store is not None

    def saved() -> dict[str, Any]:
        account = store.get("SP1ABC")
        return account.settings.get("radio", {}) if account is not None else {}

    # Saved when the account's last page closes (and every RADIO_SAVE_S meanwhile).
    await ws.close()
    await async_wait_for(lambda: saved().get("hz") == 14_074_000 and saved().get("agc") == "fast")
    radio = system.harness.port.radio
    radio.frequency_hz, radio.agc = 7_100_000, 0x03  # changed at the radio meanwhile
    ws = await page(client, token)
    await async_wait_for(lambda: radio.frequency_hz == 14_074_000 and radio.agc == 0x01)
    assert radio.data_mode is True  # U-D
    await ws.close()


async def test_a_saved_power_never_exceeds_the_limit(tmp_path: Path) -> None:
    import dataclasses  # noqa: PLC0415

    config = web_config(tmp_path)
    config = dataclasses.replace(
        config, safety=dataclasses.replace(config.safety, max_rf_power_w=5.0)
    )
    async with running_system(config) as system:
        steps = system.core._restore_requests({"power_w": 20, "hz": "x", "vfo": "C"})
        assert steps == [("set_rf_power", {"watts": 5})]


# A full disk (code review 2026-11-10, point 5): the machine by the radio has 8 GB


def _disk_full(*_args: object) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


async def test_a_full_disk_does_not_end_the_server(
    site: tuple[System, TestClient[Any, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from remoteg90ctl.server import accounts as accounts_module  # noqa: PLC0415
    from remoteg90ctl.server import core as core_module  # noqa: PLC0415

    monkeypatch.setattr(core_module, "RADIO_SAVE_S", 0.05)
    system, client = site
    system.core._browser_connected = lambda: True  # type: ignore[method-assign]
    _, _, token = await post(client, "/api/first-account", FIRST)
    ws = await page(client, token)
    assert (await ask(ws, "set_frequency", hz=14_074_000))["ok"]
    await asyncio.sleep(0.2)  # the settings were restored and saved once
    monkeypatch.setattr(accounts_module, "_write_json", _disk_full)
    assert (await ask(ws, "set_agc", agc="fast"))["ok"]  # something new to save
    # Another saver beside the server's own: it must survive the failing writes.
    saver = asyncio.create_task(system.core._remember_radio())
    await asyncio.sleep(0.4)  # many attempts
    assert not saver.done(), saver.exception() if saver.done() else None
    saver.cancel()
    assert (await ask(ws, "status"))["ok"]
    await ws.close()


async def test_a_full_disk_does_not_keep_the_lease_or_the_account(
    site: tuple[System, TestClient[Any, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from remoteg90ctl.server import accounts as accounts_module  # noqa: PLC0415

    system, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    assert (await post(client, "/api/accounts", SECOND, admin))[0] == 200
    ws = await page(client, admin)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "set_agc", agc="fast"))["ok"]  # a setting still to be saved
    assert (await ask(ws, "tx_request", antenna_confirmed=True))["ok"]
    monkeypatch.setattr(accounts_module, "_write_json", _disk_full)
    await ws.close()
    # The lease ends at once, not after the guard's 2 s of silence ...
    assert await async_wait_for(lambda: not system.ptt_active, timeout=1.0) < 1.0
    # ... and the account is free again for another one.
    monkeypatch.undo()
    status, body, _ = await post(client, "/api/login", SECOND)
    assert status == 200, body


# The store is used from the event loop and from worker threads (code review 2026-11-10, 8, 10b)


def test_two_threads_never_corrupt_the_accounts_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    from remoteg90ctl.server import accounts as accounts_module  # noqa: PLC0415

    monkeypatch.setattr(accounts_module, "hash_password", lambda password: "hash")  # fast
    store = AccountStore(tmp_path)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    store.create(SECOND["callsign"], SECOND["locator"], SECOND["password"])
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def settings() -> None:  # the server's own saving, in the event loop
        start.wait()
        try:
            for i in range(150):
                store.save_settings("SP1ABC", {"radio": {"hz": i}})
        except BaseException as exc:
            errors.append(exc)

    def passwords() -> None:  # a password change, in a worker thread
        start.wait()
        try:
            for i in range(150):
                store.set_password("SP2XYZ", f"a new long password {i}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=settings), threading.Thread(target=passwords)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    reread = AccountStore(tmp_path)
    assert reread.broken is None
    assert reread.get("SP1ABC") is not None


def test_failed_logins_for_unknown_callsigns_do_not_grow_the_store_for_ever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remoteg90ctl.server import accounts as accounts_module  # noqa: PLC0415

    clock = Clock()
    store = AccountStore(tmp_path, clock=clock, monotonic=clock)
    store.create(FIRST["callsign"], FIRST["locator"], FIRST["password"])
    monkeypatch.setattr(accounts_module, "password_matches", lambda *_args: False)
    for i in range(3000):
        clock.now += 100.0  # slowly, so that no login is throttled away
        with pytest.raises(AccountsError):
            store.check_login(f"X{i}ABC", "guess")
    assert len(store._throttles) < 1500


# The administrator (the first account; owner's decision 2026-11-13)


async def test_the_administrator_sees_who_works_and_may_take_over(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Owner's decision 2026-11-13: the administrator's login throws nobody out; its
    page shows who uses the station, and only its explicit take-over closes that
    account's pages and logs it out (its transmission ends). Its own sessions stay."""
    system, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)
    ws = await page(client, other)
    assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
    assert (await ask(ws, "tx_request", antenna_confirmed=True))["ok"]
    assert system.ptt_active
    status, _, again = await post(client, "/api/login", FIRST)
    assert status == 200  # the administrator may log in, but takes nothing yet
    assert system.ptt_active
    headers = {**OURS, "Cookie": f"{COOKIE}={again}"}
    session = await (await client.get("/api/session", headers=headers)).json()
    assert session["busy"] == "SP2XYZ"
    assert session["busy_transmitting"] is True
    assert session["busy_since"] > 0
    with pytest.raises(WSServerHandshakeError) as info:
        await page(client, again)  # the station is in use
    assert info.value.status == 409
    status, body, _ = await post(client, "/api/take-over", {}, other)
    assert (status, body["error"]) == (403, "forbidden")  # not for other accounts
    status, _, _ = await post(client, "/api/take-over", {}, again)
    assert status == 200
    await async_wait_for(lambda: not system.ptt_active)
    msg = await ws.receive(timeout=5)
    while msg.type.name == "TEXT":  # events sent before the page was closed
        msg = await ws.receive(timeout=5)
    assert msg.type.name in ("CLOSE", "CLOSED", "CLOSING")
    with pytest.raises(WSServerHandshakeError) as info:
        await page(client, other)  # logged out, not only closed
    assert info.value.status == 401
    for token in (again, admin):  # the new session and the earlier one
        mine = await page(client, token)
        assert (await ask(mine, "get_frequency"))["ok"]
        await mine.close()


async def test_the_take_over_logs_out_only_the_account_using_the_station(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Audit 2026-11-15, A2: an account only remembered on another device keeps
    its login; only the one whose callsign the administrator saw is logged out."""
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    await post(client, "/api/accounts", THIRD, admin)
    _, _, idle = await post(client, "/api/login", THIRD)  # no page open
    _, _, working = await post(client, "/api/login", SECOND)
    ws = await page(client, working)
    status, _, _ = await post(client, "/api/take-over", {}, admin)
    assert status == 200
    msg = await ws.receive(timeout=5)
    while msg.type.name == "TEXT":
        msg = await ws.receive(timeout=5)
    headers = {**OURS, "Cookie": f"{COOKIE}={idle}"}
    session = await (await client.get("/api/session", headers=headers)).json()
    assert session["account"]["callsign"] == THIRD["callsign"]
    with pytest.raises(WSServerHandshakeError) as info:
        await page(client, working)
    assert info.value.status == 401


async def test_other_accounts_cannot_log_the_administrator_out(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    ws = await page(client, admin)
    status, body, _ = await post(client, "/api/login", SECOND)
    assert (status, body["error"]) == (409, "account_busy")
    await ws.close()
    await asyncio.sleep(0.05)
    _, _, other = await post(client, "/api/login", SECOND)
    theirs = await page(client, other)
    # Opening the page with a saved session does not take over (only the password).
    with pytest.raises(WSServerHandshakeError) as info:
        await page(client, admin)
    assert info.value.status == 409
    await theirs.close()
    await asyncio.sleep(0.05)
    mine = await page(client, admin)  # the administrator's session survived
    assert (await ask(mine, "get_frequency"))["ok"]
    await mine.close()


async def test_only_the_administrator_may_restart_the_server(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    system, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)
    theirs = await page(client, other)
    assert (await ask(theirs, "server_restart"))["error"] == "forbidden"
    await theirs.close()
    await asyncio.sleep(0.05)
    mine = await page(client, admin)
    assert (await ask(mine, "set_mode", mode="U-D"))["ok"]
    lease = (await ask(mine, "tx_request", antenna_confirmed=True))["lease"]
    # Allowed for the administrator, refused only because of the transmission.
    assert (await ask(mine, "server_restart"))["error"] == "tx_busy"
    assert not system.core.restart_requested.is_set()
    assert (await ask(mine, "tx_release", lease=lease))["ok"]
    await mine.close()


async def test_the_server_log_and_iq_recording_are_the_administrators(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Security review 2026-11-14, Z9: the log shows logins; a recording fills the disk."""
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)
    theirs = await page(client, other)
    for kind, fields in (("log_subscribe", {}), ("iq_record", {"seconds": 1})):
        assert (await ask(theirs, kind, **fields))["error"] == "forbidden", kind
    await theirs.close()
    await asyncio.sleep(0.05)
    mine = await page(client, admin)
    assert (await ask(mine, "log_subscribe"))["ok"]
    await mine.close()


async def test_a_failed_login_logs_no_password(
    site: tuple[System, TestClient[Any, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    """Z9: a password typed into the callsign field must not reach the log."""
    _, client = site
    await post(client, "/api/first-account", FIRST)
    await post(client, "/api/login", {"callsign": "my secret pass", "password": "x" * 12})
    await post(client, "/api/login", {"callsign": "sp9wro", "password": "x" * 12})
    text = " ".join(str(record.__dict__) for record in caplog.records)
    assert "secret" not in text.lower()
    assert "(not a callsign)" in text
    assert "SP9WRO" in text


async def test_who_uses_the_station_is_told_to_accounts_only(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Z11: without a session /api/session says only that the station is in use."""
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)  # before the station is in use
    ws = await page(client, admin)
    anonymous = await (await client.get("/api/session", headers=OURS)).json()
    assert anonymous["busy"] == "another account"
    logged_in = await (
        await client.get("/api/session", headers={**OURS, "Cookie": f"{COOKIE}={other}"})
    ).json()
    assert logged_in["busy"] == FIRST["callsign"]
    await ws.close()


async def test_an_account_not_using_the_station_cannot_import(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Security review 2026-11-14, Z3: an import holds the server for seconds."""
    _, client = site
    _, _, admin = await post(client, "/api/first-account", FIRST)
    await post(client, "/api/accounts", SECOND, admin)
    _, _, other = await post(client, "/api/login", SECOND)
    theirs = await page(client, other)
    headers = {**OURS, "Cookie": f"{COOKIE}={admin}"}
    refused = await client.post("/api/logbook/import", data="<EOR>", headers=headers)
    assert (refused.status, (await refused.json())["error"]) == (409, "account_busy")
    await theirs.close()


def test_only_one_first_account_even_at_once(tmp_path: Path) -> None:
    """Security review 2026-11-14, Z4: two first-account requests at the same time
    (each hashes its password outside the lock) made two accounts."""
    import threading  # noqa: PLC0415

    store = AccountStore(tmp_path)
    results: list[str] = []
    start = threading.Barrier(2)

    def make(callsign: str) -> None:
        start.wait()
        try:
            store.create(callsign, "JO82", "long enough password", only_first=True)
            results.append("made")
        except AccountsError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=make, args=(c,)) for c in ("SP1OWN", "XX1BAD")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["forbidden", "made"]
    assert len(store.accounts()) == 1
    assert store.accounts()[0].admin
