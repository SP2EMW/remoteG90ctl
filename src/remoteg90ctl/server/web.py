# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP and WebSocket transport for browsers (stage 2b).

Loopback only (AGENTS.md rule 15; ``server.listen`` is
validated at start-up). Every request must carry our own ``Host``; a WebSocket
must also carry our own ``Origin`` (decisions.md D34): any page open in the
browser could otherwise drive the radio through ``ws://127.0.0.1``, and a
foreign ``Host`` is the mark of DNS rebinding.

A loopback HTTPS proxy (``tailscale serve``) may present the page under a name
from ``server.allowed_hosts`` (D38): such a name is accepted as ``Host`` only
with an ``https://`` ``Origin``, and only with Tailscale's identity header
(D57): ``tailscale serve`` adds ``Tailscale-User-Login`` for tailnet users and
removes any copy a client sends, while Tailscale Funnel serves the same name to
the public internet without it.

Browsers log in to an account (decisions.md D48, :mod:`.accounts`): the
``/api`` routes create the first account, log in and out, change the password
and, for the first account, add and delete accounts. ``/ws`` needs a valid
session cookie and gets :data:`ACCOUNT_PERMISSIONS`; without one a browser
gets nothing but the page itself and the login (AGENTS.md rule 16). One
account at a time may have pages open; an ended session closes its pages,
which ends their transmit lease.

Text messages on ``/ws`` are the requests and responses of the server protocol
(docs/server-protocol.md); waterfall lines are binary frames
(:mod:`remoteg90ctl.server.waterfall`).
"""

import asyncio
import functools
import json
import logging
import mimetypes
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from aiohttp import WSMsgType, web
from aiohttp.typedefs import Handler

from remoteg90ctl.audio.opus import OpusUnavailableError
from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.accounts import (
    CALLSIGN,
    SESSION_LIFETIME_S,
    Account,
    AccountsError,
    AccountStore,
)
from remoteg90ctl.server.audio_service import AudioBusyError, Listener
from remoteg90ctl.server.core import ClientContext, Message, ServerCore, Session
from remoteg90ctl.server.logbook import MAX_IMPORT_BYTES, Logbook, LogbookError, Station
from remoteg90ctl.server.logbuffer import LOG_BUFFER
from remoteg90ctl.server.protocol import (
    ACCOUNT_PERMISSIONS,
    ADMIN_PERMISSIONS,
    AUDIO_BUSY,
    AUDIO_UNAVAILABLE,
    INVALID_VALUE,
    IQ_OFF,
    IQ_UNAVAILABLE,
    MAX_LINE_BYTES,
    Request,
    RequestError,
    error,
    event,
    ok,
    parse_request,
)
from remoteg90ctl.server.sysmon import INTERVAL_S, SystemMonitor, find_process
from remoteg90ctl.server.waterfall import DEFAULT_SPEED, MAX_BINS, MIN_BINS, SPEEDS, Subscriber

logger = logging.getLogger(__name__)

STATIC_DIR = Path(str(files("remoteg90ctl.server") / "static"))
# The app manifest (installing the page as an app): not every system's
# mime.types knows the extension, and browsers want this type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
SEND_TIMEOUT_S = 2.0
HEARTBEAT_S = 10.0
COOKIE = "remoteg90ctl_session"
# Added by `tailscale serve` for tailnet users, removed from what clients send.
IDENTITY_HEADER = "Tailscale-User-Login"
# Set by `tailscale serve` on every request it proxies (overwriting a client's
# copy), while it passes the client's Host on unchanged: so this header, not
# Host, tells a proxied request (security review 2026-11-14, Z1).
PROXY_HEADER = "X-Forwarded-Host"
# Set by `tailscale serve` on requests that came through Funnel; a client's copy
# is removed.
FUNNEL_HEADER = "Tailscale-Funnel-Request"
NO_IDENTITY_TEXT = (
    "refused: this page is for your tailnet only, and the request has no "
    "Tailscale identity.\nIt came through Tailscale Funnel (the public internet), "
    "a tagged device or another proxy.\nSwitch Funnel off; `tailscale funnel status` shows it.\n"
)
# How often open pages are checked against their session (expired, revoked).
SESSION_CHECK_S = 30.0
MAX_API_BODY = 4096
# The page's own settings on the account (D48 part C): string keys and values.
MAX_SETTINGS_BODY = 32 * 1024
MAX_SETTINGS = 100
MAX_SETTING_VALUE = 8 * 1024
# API error codes and their HTTP status.
_API_STATUS = {
    "invalid_value": 400,
    "login_failed": 401,
    "not_logged_in": 401,
    "forbidden": 403,
    "unknown_account": 404,
    "account_exists": 409,
    "account_busy": 409,
    "no_log": 404,
    "login_throttled": 429,
    "accounts_unavailable": 503,
}


def parse_listen(entry: str) -> tuple[str, int]:
    """``127.0.0.1:8078`` or ``[::1]:8078`` (already validated) to host and port."""
    host, _, port = entry.rpartition(":")
    return host.strip("[]"), int(port)


def allowed_hosts(listen: tuple[str, ...]) -> frozenset[str]:
    """``Host`` values that address this server: its loopback addresses and ``localhost``."""
    hosts: set[str] = set()
    for entry in listen:
        _, port = parse_listen(entry)
        hosts.update({f"127.0.0.1:{port}", f"[::1]:{port}", f"localhost:{port}"})
    return frozenset(hosts)


@dataclass
class Traffic:
    """Bytes over browser WebSockets, by kind (waterfall lines, audio, messages)."""

    waterfall: int = 0
    audio: int = 0
    messages: int = 0
    received: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "waterfall": self.waterfall,
            "audio": self.audio,
            "messages": self.messages,
            "received": self.received,
        }


class WebTransport:
    def __init__(  # noqa: PLR0913  (keyword-only options after the two transport inputs)
        self,
        core: ServerCore,
        listen: tuple[str, ...],
        proxy_hosts: tuple[str, ...] = (),
        *,
        accounts: AccountStore,
        logbook: Logbook | None = None,
        require_tailnet_identity: bool = True,
    ) -> None:
        self._core = core
        self._accounts = accounts
        core.accounts = accounts
        core.logbook = logbook
        core.close_pages = self._close_pages
        # Open pages: their callsign and session (token hash).
        self._pages: dict[web.WebSocketResponse, tuple[str, str]] = {}
        # When the active account opened its first page now open (UTC seconds).
        self._active_since: float | None = None
        self._session_check: asyncio.Task[None] | None = None
        self._listen = listen
        self._hosts = allowed_hosts(listen)
        self._proxy_hosts = frozenset(proxy_hosts)
        self._require_identity = require_tailnet_identity
        self._runner: web.AppRunner | None = None
        self._sockets: set[web.WebSocketResponse] = set()
        self.traffic = Traffic()  # all browsers since the server started

    def make_app(self) -> web.Application:
        app = web.Application(middlewares=[self._same_origin_only])
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._websocket)
        app.router.add_get("/api/session", self._api_session)
        app.router.add_post("/api/first-account", self._api_first_account)
        app.router.add_post("/api/login", self._api_login)
        app.router.add_post("/api/logout", self._api_logout)
        app.router.add_post("/api/take-over", self._api_take_over)
        app.router.add_post("/api/password", self._api_password)
        app.router.add_get("/api/accounts", self._api_accounts)
        app.router.add_post("/api/accounts", self._api_add_account)
        app.router.add_delete("/api/accounts/{callsign}", self._api_delete_account)
        app.router.add_get("/api/settings", self._api_settings)
        app.router.add_put("/api/settings", self._api_save_settings)
        app.router.add_get("/api/logbook.adi", self._api_export_log)
        app.router.add_post("/api/logbook/import", self._api_import_log)
        app.router.add_static("/static/", STATIC_DIR, follow_symlinks=False)
        return app

    async def start(self) -> None:
        self._runner = web.AppRunner(self.make_app(), access_log=None)
        await self._runner.setup()
        for entry in self._listen:
            host, port = parse_listen(entry)
            await web.TCPSite(self._runner, host, port).start()
            logger.info("web_listening", extra=kv(address=entry))
        self._session_check = asyncio.create_task(self._check_sessions())
        if self._accounts.broken is not None:
            logger.error("accounts_unusable", extra=kv(detail=self._accounts.broken))

    async def close(self) -> None:
        if self._session_check is not None:
            self._session_check.cancel()
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # D34: our own Host, and our own Origin for WebSockets

    @web.middleware
    async def _same_origin_only(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        host = request.headers.get("Host", "").lower()
        origin = request.headers.get("Origin")
        # Loopback names come over plain HTTP; proxy names only over HTTPS (D38).
        scheme = "https" if host in self._proxy_hosts else "http"
        problem = None
        if host not in self._hosts and host not in self._proxy_hosts:
            problem = "foreign Host"
        elif PROXY_HEADER in request.headers and host not in self._proxy_hosts:
            # A proxied request under a loopback name: not a browser's (Z1).
            problem = "proxied request without the tailnet name"
        elif origin is not None and origin.lower() != f"{scheme}://{host}":
            problem = "foreign Origin"
        elif request.path == "/ws" and origin is None:
            problem = "WebSocket without Origin"
        elif request.method not in ("GET", "HEAD") and origin is None:
            problem = f"{request.method} without Origin"  # no cross-site form posts
        elif FUNNEL_HEADER in request.headers or (
            (host in self._proxy_hosts or PROXY_HEADER in request.headers)
            and self._require_identity
            and not request.headers.get(IDENTITY_HEADER)
        ):
            # D57: Funnel (public internet), a tagged device or another proxy,
            # also with a loopback Host put in by a client that is not a browser.
            problem = "no Tailscale identity"
            logger.warning(
                "web_request_refused",
                extra=kv(reason=problem, host=host, path=request.path),
            )
            raise web.HTTPForbidden(text=NO_IDENTITY_TEXT)
        if problem is not None:
            logger.warning(
                "web_request_refused",
                extra=kv(reason=problem, host=host, origin=origin, path=request.path),
            )
            raise web.HTTPForbidden(text=f"refused: {problem}\n")
        response = await handler(request)
        if request.path == "/" or request.path.startswith("/static/"):
            # Ask the server every time (a cheap 304 when unchanged): without
            # it the browser kept old scripts for hours after an update, e.g.
            # the audio worklet (owner's report 2026-11-18).
            response.headers["Cache-Control"] = "no-cache"
        return response

    # Routes

    async def _index(self, _request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def _websocket(self, request: web.Request) -> web.StreamResponse:
        token = request.cookies.get(COOKIE)
        account = self._accounts.session_account(token)
        if account is None or token is None:
            raise web.HTTPUnauthorized(text="log in first\n")
        busy = self._active_callsign()
        if busy is not None and busy != account.callsign:
            raise web.HTTPConflict(text=f"{busy} is logged in\n")
        ws = web.WebSocketResponse(heartbeat=HEARTBEAT_S, max_msg_size=MAX_LINE_BYTES)
        await ws.prepare(request)
        self._sockets.add(ws)
        if not self._pages:
            self._active_since = time.time()
        self._pages[ws] = (account.callsign, self._accounts.session_id(token))
        context = ClientContext(
            "web",
            secrets.token_hex(4),
            permissions=ADMIN_PERMISSIONS if account.admin else ACCOUNT_PERMISSIONS,
            account=account.callsign,
        )
        outgoing: asyncio.Queue[Message | None] = asyncio.Queue()
        session = self._core.open_session(context, outgoing.put_nowait)
        client = _WebClient(self._core, ws, session, outgoing, self.traffic)
        try:
            await client.serve()
        finally:
            # Each step on its own: whatever fails, the page's subscriptions and
            # its account must be released (code review 2026-11-10, point 5).
            try:
                await self._core.close_session(session)
            except Exception:
                logger.exception("close_session_failed")
            finally:
                client.stop()
                self._sockets.discard(ws)
                self._pages.pop(ws, None)
                await ws.close()
        return ws

    # Accounts and sessions (D48)

    def _active_callsign(self) -> str | None:
        """The account with a page open now: only it may open more (one at a time)."""
        return next((callsign for callsign, _ in self._pages.values()), None)

    def _close_pages(self, callsign: str | None = None, session: str | None = None) -> None:
        """Close the open pages of an account, or of one session; their leases end."""
        for ws, (page_callsign, page_session) in list(self._pages.items()):
            if callsign in (None, page_callsign) and session in (None, page_session):
                # No longer the active account from now on: another one may open
                # its page before this one has finished closing.
                self._pages.pop(ws, None)
                asyncio.get_running_loop().create_task(ws.close())

    async def _check_sessions(self) -> None:
        while True:
            await asyncio.sleep(SESSION_CHECK_S)
            for ws, (callsign, session) in list(self._pages.items()):
                if session not in self._accounts.session_ids(callsign):
                    logger.info("session_ended", extra=kv(account=callsign))
                    await ws.close()

    def _account_of(self, request: web.Request) -> Account | None:
        return self._accounts.session_account(request.cookies.get(COOKIE))

    def _set_cookie(self, request: web.Request, response: web.Response, token: str) -> None:
        response.set_cookie(
            COOKIE,
            token,
            max_age=int(SESSION_LIFETIME_S),
            path="/",
            httponly=True,
            samesite="Strict",
            # The proxy names come over HTTPS (D38); loopback over plain HTTP.
            secure=request.headers.get("Host", "").lower() in self._proxy_hosts,
        )

    @staticmethod
    def _api_error(code: str, message: str) -> web.Response:
        return web.json_response(
            {"ok": False, "error": code, "message": message}, status=_API_STATUS.get(code, 400)
        )

    @staticmethod
    async def _body(request: web.Request, limit: int = MAX_API_BODY) -> dict[str, object]:
        if request.content_length is not None and request.content_length > limit:
            raise AccountsError("invalid_value", "request too large")
        try:
            data = json.loads(await request.text())
        except ValueError:
            raise AccountsError("invalid_value", "expected a JSON object") from None
        if not isinstance(data, dict):
            raise AccountsError("invalid_value", "expected a JSON object")
        return data

    async def _api_session(self, request: web.Request) -> web.Response:
        account = self._account_of(request)
        active = self._active_callsign()
        return web.json_response(
            {
                "ok": True,
                "first_account_needed": self._accounts.needs_first_account,
                "accounts_unusable": self._accounts.broken is not None,
                "account": account.public() if account is not None else None,
                # Who uses the station is told to logged-in accounts only (Z11).
                "busy": (active if account is not None else "another account")
                if active is not None and (account is None or active != account.callsign)
                else None,
                # Who uses the station, for the administrator's take-over window.
                "busy_since": self._active_since
                if active is not None and account is not None and account.admin
                else None,
                "busy_transmitting": self._core.tx_account() == active
                if active is not None and account is not None and account.admin
                else None,
            }
        )

    async def _api_first_account(self, request: web.Request) -> web.Response:
        try:
            body = await self._body(request)
            if not self._accounts.needs_first_account:
                raise AccountsError("forbidden", "the administrator account already exists")
            account = await asyncio.to_thread(
                functools.partial(self._accounts.create, only_first=True),
                body.get("callsign"),
                body.get("locator"),
                body.get("password"),
            )
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        logger.warning("account_created", extra=kv(account=account.callsign, first=True))
        return self._logged_in(request, account)

    def _logged_in(self, request: web.Request, account: Account) -> web.Response:
        response = web.json_response({"ok": True, "account": account.public()})
        self._set_cookie(request, response, self._accounts.new_session(account.callsign))
        return response

    async def _api_login(self, request: web.Request) -> web.Response:
        try:
            body = await self._body(request)
            account = await asyncio.to_thread(
                self._accounts.check_login, body.get("callsign"), body.get("password")
            )
            busy = self._active_callsign()
            # The administrator logs in even then: its page shows who works and
            # offers to take the station over (/api/take-over).
            if busy is not None and busy != account.callsign and not account.admin:
                raise AccountsError("account_busy", f"{busy} is logged in")
        except AccountsError as exc:
            if exc.code == "login_failed":
                # What was typed as the callsign is logged only if it looks like
                # one: a password typed into the wrong field must not (Z9).
                typed = str(body.get("callsign", "")).strip().upper()
                shown = typed if CALLSIGN.fullmatch(typed) else "(not a callsign)"
                logger.warning("login_failed", extra=kv(callsign=shown))
            return self._api_error(exc.code, exc.message)
        logger.info("logged_in", extra=kv(account=account.callsign))
        return self._logged_in(request, account)

    async def _api_take_over(self, request: web.Request) -> web.Response:
        """The administrator closes the pages of the account using the station and
        logs it out (owner's decision 2026-11-13): only on this explicit request,
        after the page has shown who it is; never on login or reconnecting."""
        try:
            admin = self._admin(request)
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        # Only the account using the station, the one the page has shown; other
        # accounts' remembered logins stay (audit 2026-11-15, A2).
        busy = self._active_callsign()
        if busy is not None and busy != admin.callsign:
            self._accounts.revoke_account(busy)
            self._close_pages(callsign=busy)
            logger.warning("logged_out_by_administrator", extra=kv(account=busy, by=admin.callsign))
        return web.json_response({"ok": True})

    async def _api_logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get(COOKIE)
        account = self._accounts.session_account(token)
        if token is not None:
            self._accounts.revoke(token)
            self._close_pages(session=self._accounts.session_id(token))
        if account is not None:
            logger.info("logged_out", extra=kv(account=account.callsign))
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response

    async def _api_password(self, request: web.Request) -> web.Response:
        account = self._account_of(request)
        if account is None:
            return self._api_error("not_logged_in", "log in first")
        try:
            body = await self._body(request)
            await asyncio.to_thread(
                self._accounts.check_login, account.callsign, body.get("old_password")
            )
            await asyncio.to_thread(
                self._accounts.set_password, account.callsign, body.get("new_password")
            )
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        logger.warning("password_changed", extra=kv(account=account.callsign))
        self._close_pages(callsign=account.callsign)  # they reconnect with the new cookie
        return self._logged_in(request, account)

    async def _api_settings(self, request: web.Request) -> web.Response:
        account = self._account_of(request)
        if account is None:
            return self._api_error("not_logged_in", "log in first")
        page = account.settings.get("page", {})
        return web.json_response({"ok": True, "page": page if isinstance(page, dict) else {}})

    async def _api_save_settings(self, request: web.Request) -> web.Response:
        account = self._account_of(request)
        if account is None:
            return self._api_error("not_logged_in", "log in first")
        try:
            page = (await self._body(request, MAX_SETTINGS_BODY)).get("page")
            if (
                not isinstance(page, dict)
                or len(page) > MAX_SETTINGS
                or not all(
                    isinstance(k, str) and isinstance(v, str) and len(v) <= MAX_SETTING_VALUE
                    for k, v in page.items()
                )
            ):
                raise AccountsError("invalid_value", "page: an object of strings")
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        self._accounts.save_settings(account.callsign, {**account.settings, "page": page})
        return web.json_response({"ok": True})

    # Station log, ADIF (stage 9, D50): the account's own log only.

    async def _api_export_log(self, request: web.Request) -> web.StreamResponse:
        account = self._account_of(request)
        logbook = self._core.logbook
        if account is None:
            return self._api_error("not_logged_in", "log in first")
        if logbook is None:
            return self._api_error("no_log", "no station log on this server")
        text = logbook.export_adif(Station(account.callsign, account.locator))
        name = f"{account.callsign.replace('/', '-')}.adi"
        return web.Response(
            text=text,
            content_type="text/plain",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def _api_import_log(self, request: web.Request) -> web.Response:
        account = self._account_of(request)
        logbook = self._core.logbook
        if account is None:
            return self._api_error("not_logged_in", "log in first")
        busy = self._active_callsign()
        if busy is not None and busy != account.callsign:
            # The import holds the server for seconds: not while another works.
            return self._api_error("account_busy", f"{busy} is using the station")
        if logbook is None:
            return self._api_error("no_log", "no station log on this server")
        if request.content_length is not None and request.content_length > MAX_IMPORT_BYTES:
            return self._api_error("invalid_value", "ADIF file larger than 5 MB")
        # Streamed: request.read() would stop at aiohttp's 1 MiB default.
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.content.iter_chunked(65536):
            size += len(chunk)
            if size > MAX_IMPORT_BYTES:
                return self._api_error("invalid_value", "ADIF file larger than 5 MB")
            chunks.append(chunk)
        try:
            text = b"".join(chunks).decode(errors="replace")
            # In the event loop: SQLite connections stay in their thread; one
            # transaction keeps even a large file short.
            result = logbook.import_adif(account.callsign, text)
        except LogbookError as exc:
            return self._api_error(exc.code, exc.message)
        logger.info("log_imported", extra=kv(account=account.callsign, **result))
        return web.json_response({"ok": True, **result})

    def _admin(self, request: web.Request) -> Account:
        account = self._account_of(request)
        if account is None:
            raise AccountsError("not_logged_in", "log in first")
        if not account.admin:
            raise AccountsError("forbidden", "only the administrator manages accounts")
        return account

    async def _api_accounts(self, request: web.Request) -> web.Response:
        try:
            self._admin(request)
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        accounts = [a.public() for a in self._accounts.accounts()]
        return web.json_response({"ok": True, "accounts": accounts})

    async def _api_add_account(self, request: web.Request) -> web.Response:
        try:
            admin = self._admin(request)
            body = await self._body(request)
            account = await asyncio.to_thread(
                self._accounts.create,
                body.get("callsign"),
                body.get("locator"),
                body.get("password"),
            )
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        logger.warning("account_created", extra=kv(account=account.callsign, by=admin.callsign))
        return web.json_response({"ok": True, "account": account.public()})

    async def _api_delete_account(self, request: web.Request) -> web.Response:
        callsign = request.match_info["callsign"].upper()
        try:
            admin = self._admin(request)
            self._accounts.delete(callsign)
        except AccountsError as exc:
            return self._api_error(exc.code, exc.message)
        logger.warning("account_deleted", extra=kv(account=callsign, by=admin.callsign))
        self._close_pages(callsign=callsign)
        return web.json_response({"ok": True})


# Requests answered at once, ahead of the page's queue (see serve()).
LEASE_KEEPING = frozenset({"tx_renew", "tx_release"})


def _lease_keeping(text: str) -> bool:
    """Whether a text request renews or releases a lease; anything unparsable is not."""
    try:
        return parse_request(text.encode()).type in LEASE_KEEPING
    except RequestError:
        return False


class _WebClient:
    """One browser connection: requests in, responses, events and lines out."""

    def __init__(
        self,
        core: ServerCore,
        ws: web.WebSocketResponse,
        session: Session,
        outgoing: asyncio.Queue[Message | None],
        server_traffic: Traffic,
    ) -> None:
        self._core = core
        self._traffic = Traffic()  # this browser since it connected
        self._server_traffic = server_traffic
        self._connected_at = time.monotonic()
        self._tailscaled: int | None = None  # its pid, for the resource window
        self._ws = ws
        self._session = session
        self._outgoing = outgoing
        self._subscriber: Subscriber | None = None
        self._listener: Listener | None = None
        self._monitor: asyncio.Task[None] | None = None
        self._log_listener: Callable[[str], None] | None = None
        self._tasks: list[asyncio.Task[None]] = []
        # Requests answered one after another, in the order they came.
        self._in_order: asyncio.Queue[str] = asyncio.Queue()
        self._lease_tasks: set[asyncio.Task[None]] = set()

    async def serve(self) -> None:
        self._tasks.append(asyncio.create_task(self._send_messages()))
        self._tasks.append(asyncio.create_task(self._answer_in_order()))
        async for message in self._ws:
            if message.type is WSMsgType.BINARY:
                # Audio for the radio (stage 4): played only during this session's lease.
                self._count("received", len(message.data))
                self._core.tx_audio_frame(self._session, message.data)
                continue
            if message.type is not WSMsgType.TEXT:
                break  # text requests and binary audio only; anything else ends the session
            self._count("received", len(message.data))
            if _lease_keeping(message.data):
                # Renewing or releasing the lease never waits behind this page's
                # other requests: one waiting for the radio (up to a CAT timeout
                # each, in the shared CAT queue) would hold a renewal back past
                # the lease timeout and end a transmission of a page that is
                # alive. The renewal still comes from the page (rule 5).
                task = asyncio.create_task(self._answer_now(message.data))
                self._lease_tasks.add(task)
                task.add_done_callback(self._lease_tasks.discard)
            else:
                self._in_order.put_nowait(message.data)

    async def _answer_in_order(self) -> None:
        while True:
            text = await self._in_order.get()
            self._outgoing.put_nowait(await self._answer(text))

    async def _answer_now(self, text: str) -> None:
        self._outgoing.put_nowait(await self._answer(text))

    async def _answer(self, text: str) -> Message:
        try:
            request = parse_request(text.encode())
        except RequestError as exc:
            return error(exc.request_id, exc.code, exc.message)
        refusal = self._core.permission_refusal(self._session, request)
        if refusal is not None:
            return refusal
        match request.type:
            case "waterfall_subscribe":
                return self._subscribe(request)
            case "waterfall_unsubscribe":
                self._unsubscribe()
                return ok(request.id)
            case "audio_subscribe":
                return self._listen(request)
            case "audio_unsubscribe":
                self._stop_listening()
                return ok(request.id)
            case "system_subscribe":
                self._stop_monitor()
                self._monitor = asyncio.create_task(self._send_stats())
                return ok(request.id, interval_s=INTERVAL_S)
            case "system_unsubscribe":
                self._stop_monitor()
                return ok(request.id)
            case "log_subscribe":
                return self._log_subscribe(request)
            case "log_unsubscribe":
                self._log_unsubscribe()
                return ok(request.id)
            case _:
                return await self._core.handle(self._session, request)

    def _subscribe(self, request: Request) -> Message:
        hub, iq = self._core.waterfall, self._core.iq
        if hub is None or iq is None:
            return error(request.id, IQ_UNAVAILABLE, IQ_OFF)
        bins = request.fields.get("bins")
        fft_size = iq.config.fft_size
        if (
            isinstance(bins, bool)
            or not isinstance(bins, int)
            or not MIN_BINS <= bins <= min(MAX_BINS, fft_size)
            or bins & (bins - 1)
        ):
            return error(
                request.id,
                INVALID_VALUE,
                f"'bins' must be a power of two from {MIN_BINS} to {fft_size}",
            )
        speed = request.fields.get("speed", DEFAULT_SPEED)
        if not isinstance(speed, str) or speed not in SPEEDS:
            return error(request.id, INVALID_VALUE, f"'speed' must be one of {', '.join(SPEEDS)}")
        every = SPEEDS[speed]
        self._unsubscribe()
        self._subscriber = hub.subscribe(bins, every)
        self._tasks.append(asyncio.create_task(self._send_lines(self._subscriber)))
        return ok(request.id, bins=bins, speed=speed, line_rate=iq.config.line_rate / every)

    def _unsubscribe(self) -> None:
        if self._subscriber is not None and self._core.waterfall is not None:
            self._core.waterfall.unsubscribe(self._subscriber)
        self._subscriber = None

    # Receive audio (stage 3): one listener for the whole server.

    def _listen(self, request: Request) -> Message:
        audio = self._core.audio
        if audio is None:
            return error(request.id, AUDIO_UNAVAILABLE, "no [audio] section in the configuration")
        codec = request.fields.get("codec")
        bitrate = request.fields.get("bitrate")
        complexity = request.fields.get("complexity")
        if complexity is not None and (
            isinstance(complexity, bool) or not isinstance(complexity, int)
        ):
            return error(request.id, INVALID_VALUE, "'complexity' must be an integer 0 to 10")
        # A wrong request is refused before it ends the listening it would
        # replace (audit 2026-11-15, A3).
        try:
            audio.check_listen(
                codec if isinstance(codec, str) else "",
                bitrate if isinstance(bitrate, int) and not isinstance(bitrate, bool) else None,
                complexity,
            )
        except ValueError as exc:
            return error(request.id, INVALID_VALUE, str(exc))
        self._stop_listening()
        try:
            listener = audio.subscribe(
                codec if isinstance(codec, str) else "",
                bitrate if isinstance(bitrate, int) and not isinstance(bitrate, bool) else None,
                complexity,
            )
        except AudioBusyError as exc:
            return error(request.id, AUDIO_BUSY, str(exc))
        except ValueError as exc:
            return error(request.id, INVALID_VALUE, str(exc))
        except OpusUnavailableError as exc:
            return error(request.id, AUDIO_UNAVAILABLE, str(exc))
        self._listener = listener
        self._tasks.append(asyncio.create_task(self._send_audio(listener)))
        return ok(request.id, codec=codec, bitrate=bitrate, sample_rate=AUDIO_SAMPLE_RATE)

    def _stop_listening(self) -> None:
        if self._listener is not None and self._core.audio is not None:
            self._core.audio.unsubscribe(self._listener)
        self._listener = None

    async def _send_audio(self, listener: Listener) -> None:
        while self._listener is listener:
            frame = await listener.queue.get()
            self._count("audio", len(frame))
            try:
                await asyncio.wait_for(self._ws.send_bytes(frame), SEND_TIMEOUT_S)
            except (TimeoutError, ConnectionError, RuntimeError):
                await self._ws.close()
                return

    # Resource window: /proc is read only while it is open (server/sysmon.py).

    def _processes(self) -> list[tuple[str, int | None]]:
        processes = [("server", os.getpid()), ("guard", self._core.guard.peer_pid)]
        # Remote access goes through Tailscale: shown while its daemon runs.
        self._tailscaled = find_process("tailscaled", hint=self._tailscaled)
        if self._tailscaled is not None:
            processes.append(("tailscaled", self._tailscaled))
        return processes

    def _count(self, kind: str, size: int) -> None:
        for traffic in (self._traffic, self._server_traffic):
            setattr(traffic, kind, getattr(traffic, kind) + size)

    def _network(self) -> dict[str, object]:
        return {
            "browser": self._traffic.as_dict(),
            "server": self._server_traffic.as_dict(),
            "connected_s": round(time.monotonic() - self._connected_at, 1),
        }

    async def _send_stats(self) -> None:
        core = self._core
        monitor = SystemMonitor(
            self._processes,
            disk_path=core.state_dir,
            clock_synchronized=lambda: core.audio.clock.synchronized if core.audio else None,
        )
        while True:
            stats = event("system_stats", **monitor.sample(), network=self._network())
            self._outgoing.put_nowait(stats)
            await asyncio.sleep(INTERVAL_S)

    # System log window: the server's recent INFO+ lines, then each new one.

    def _log_subscribe(self, request: Request) -> Message:
        self._log_unsubscribe()
        loop = asyncio.get_running_loop()
        outgoing = self._outgoing

        def listener(line: str) -> None:
            # Log records may come from other threads (audio, I/Q).
            loop.call_soon_threadsafe(outgoing.put_nowait, event("log_line", line=line))

        self._log_listener = listener
        return ok(request.id, lines=LOG_BUFFER.subscribe(listener))

    def _log_unsubscribe(self) -> None:
        if self._log_listener is not None:
            LOG_BUFFER.unsubscribe(self._log_listener)
            self._log_listener = None

    def _stop_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None

    async def _send_messages(self) -> None:
        while (message := await self._outgoing.get()) is not None:
            text = json.dumps(message)
            self._count("messages", len(text))
            try:
                await asyncio.wait_for(self._ws.send_str(text), SEND_TIMEOUT_S)
            except (TimeoutError, ConnectionError, RuntimeError):
                await self._ws.close()
                return

    async def _send_lines(self, subscriber: Subscriber) -> None:
        while self._subscriber is subscriber:
            frame = await subscriber.queue.get()
            self._count("waterfall", len(frame))
            try:
                await asyncio.wait_for(self._ws.send_bytes(frame), SEND_TIMEOUT_S)
            except (TimeoutError, ConnectionError, RuntimeError):
                await self._ws.close()
                return

    def stop(self) -> None:
        self._unsubscribe()
        self._stop_listening()
        self._stop_monitor()
        self._log_unsubscribe()
        self._outgoing.put_nowait(None)
        for task in [*self._tasks, *self._lease_tasks]:
            task.cancel()
