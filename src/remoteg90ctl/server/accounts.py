# SPDX-License-Identifier: GPL-3.0-or-later
"""Accounts and login sessions for the browser (decisions.md D48).

An account is a station: its callsign is the login, with the remote station's
QTH locator and a password. The first account is created from the page of a
fresh installation and is the only one that may add or delete accounts. Every
account has every browser permission (owner's decision 2026-11-08); no account,
no session, no permission (AGENTS.md rule 16).

Passwords are stored as scrypt hashes; session tokens are given to the browser
in a cookie and kept here only as SHA-256 hashes, so neither file lets anyone
log in. Both files are written atomically (temporary file, fsync, rename) and
readable by the service user only. An unreadable or damaged accounts file
locks the browser out: nobody can log in and no first account can be made
(that would let anyone take over the station), until it is repaired.

Standard library only.
"""

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
ACCOUNTS_FILE = "accounts.json"
SESSIONS_FILE = "sessions.json"
SESSION_LIFETIME_S = 30 * 24 * 3600  # owner's decision 2026-11-08
PASSWORD_MIN = 10
PASSWORD_MAX = 256
# scrypt cost: n=2**15, r=8, p=1 (about 32 MiB and 50-100 ms per check).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
# Failed logins: after FREE_FAILURES in a row the next try must wait, doubling
# up to MAX_DELAY_S. Counted per callsign and for all callsigns together.
FREE_FAILURES = 5
MAX_DELAY_S = 60.0
# 2.0 ** this is already far beyond MAX_DELAY_S; larger exponents overflow floats.
MAX_EXPONENT = 30
# Entries kept for guessed callsigns before the finished ones are dropped.
MAX_THROTTLES = 1000

CALLSIGN = re.compile(r"[A-Z0-9]{1,4}[0-9][A-Z0-9]{0,4}(/[A-Z0-9]{1,4})?")
LOCATOR = re.compile(r"[A-R]{2}[0-9]{2}([A-X]{2})?")


class AccountsError(Exception):
    """A request the accounts cannot honour; ``code`` is a short error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Account:
    callsign: str
    locator: str
    password_hash: str
    admin: bool = False
    created: float = 0.0
    # Settings remembered for this station (part C: radio and page).
    settings: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {"callsign": self.callsign, "locator": self.locator, "admin": self.admin}


def normalize_callsign(value: object) -> str:
    text = value.strip().upper() if isinstance(value, str) else ""
    if not CALLSIGN.fullmatch(text):
        raise AccountsError("invalid_value", "callsign: letters and digits, e.g. SP1ABC, SP1ABC/P")
    return text


def normalize_locator(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not LOCATOR.fullmatch(text[:4].upper() + text[4:].upper()):
        raise AccountsError("invalid_value", "locator: 4 or 6 characters, e.g. JO82 or JO82lj")
    return text[:4].upper() + text[4:].lower()


def check_password(value: object) -> str:
    if not isinstance(value, str) or not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise AccountsError("invalid_value", f"password: at least {PASSWORD_MIN} characters")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        computed = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed.hex(), digest)


# Checked for unknown callsigns, so a wrong login takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_json(path: Path, data: object) -> None:
    """Atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    tmp.replace(path)
    with contextlib.suppress(OSError):
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


@dataclass
class _Throttle:
    failures: int = 0
    not_before: float = 0.0


class AccountStore:
    """The accounts file and the login sessions, in ``state_dir``."""

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # The store is used from the event loop and from worker threads (scrypt runs
        # in a thread): every change of the state and every write holds this lock.
        # The password hashing itself never does, so the loop is not held up.
        self._lock = threading.RLock()
        self._accounts_path = state_dir / ACCOUNTS_FILE
        self._sessions_path = state_dir / SESSIONS_FILE
        self._clock = clock
        self._monotonic = monotonic
        self._accounts: dict[str, Account] = {}
        self._sessions: dict[str, tuple[str, float]] = {}  # token hash -> (callsign, expiry)
        self._throttles: dict[str, _Throttle] = {}
        self.broken: str | None = None  # why the accounts cannot be used
        self._load()

    # Files

    def _load(self) -> None:
        try:
            raw = json.loads(self._accounts_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"version": FORMAT_VERSION, "accounts": []}
        except (OSError, ValueError) as exc:
            self.broken = f"{self._accounts_path}: {exc}"
            return
        try:
            if raw.get("version") != FORMAT_VERSION:
                raise ValueError(f"unknown version {raw.get('version')!r}")
            for item in raw["accounts"]:
                account = Account(
                    callsign=str(item["callsign"]),
                    locator=str(item["locator"]),
                    password_hash=str(item["password_hash"]),
                    admin=bool(item.get("admin", False)),
                    created=float(item.get("created", 0.0)),
                    settings=dict(item.get("settings", {})),
                )
                self._accounts[account.callsign] = account
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._accounts = {}
            self.broken = f"{self._accounts_path}: {exc}"
            return
        with contextlib.suppress(OSError, ValueError, AttributeError, KeyError, TypeError):
            sessions = json.loads(self._sessions_path.read_text(encoding="utf-8"))
            now = self._clock()
            for digest, (callsign, expiry) in sessions["sessions"].items():
                if callsign in self._accounts and float(expiry) > now:
                    self._sessions[str(digest)] = (str(callsign), float(expiry))

    def _save_accounts(self) -> None:
        accounts = [
            {
                "callsign": a.callsign,
                "locator": a.locator,
                "password_hash": a.password_hash,
                "admin": a.admin,
                "created": a.created,
                "settings": a.settings,
            }
            for a in self._accounts.values()
        ]
        _write_json(self._accounts_path, {"version": FORMAT_VERSION, "accounts": accounts})

    def _save_sessions(self) -> None:
        sessions = {digest: [c, e] for digest, (c, e) in self._sessions.items()}
        _write_json(self._sessions_path, {"version": FORMAT_VERSION, "sessions": sessions})

    def _usable(self) -> None:
        if self.broken is not None:
            raise AccountsError("accounts_unavailable", f"accounts file unusable: {self.broken}")

    # Accounts

    @property
    def needs_first_account(self) -> bool:
        return self.broken is None and not self._accounts

    def get(self, callsign: str) -> Account | None:
        return self._accounts.get(callsign)

    def accounts(self) -> list[Account]:
        return sorted(self._accounts.values(), key=lambda a: a.created)

    def create(
        self, callsign: object, locator: object, password: object, *, only_first: bool = False
    ) -> Account:
        """A new account; the first one is the administrator. The caller checks who may.

        ``only_first``: the page of a fresh installation; checked under the lock,
        so two such requests at once make one account, never two (security
        review 2026-11-14, Z4).
        """
        self._usable()
        name = normalize_callsign(callsign)
        place = normalize_locator(locator)
        password_hash = hash_password(check_password(password))  # slow: outside the lock
        with self._lock:
            if only_first and self._accounts:
                raise AccountsError("forbidden", "the administrator account already exists")
            account = Account(
                callsign=name,
                locator=place,
                password_hash=password_hash,
                admin=not self._accounts,
                created=self._clock(),
            )
            if account.callsign in self._accounts:
                raise AccountsError("account_exists", f"{account.callsign} already has an account")
            self._accounts[account.callsign] = account
            self._save_accounts()
            return account

    def delete(self, callsign: str) -> None:
        self._usable()
        with self._lock:
            account = self._accounts.get(callsign)
            if account is None:
                raise AccountsError("unknown_account", f"no account {callsign}")
            if account.admin:
                raise AccountsError("invalid_value", "the administrator account cannot be deleted")
            del self._accounts[callsign]
            self._save_accounts()
            self.revoke_account(callsign)

    def set_password(self, callsign: str, password: object) -> None:
        """New password; every session of the account ends."""
        self._usable()
        if self._accounts.get(callsign) is None:
            raise AccountsError("unknown_account", f"no account {callsign}")
        password_hash = hash_password(check_password(password))  # slow: outside the lock
        with self._lock:
            account = self._accounts.get(callsign)
            if account is None:
                raise AccountsError("unknown_account", f"no account {callsign}")
            account.password_hash = password_hash
            self._save_accounts()
            self.revoke_account(callsign)

    def save_settings(self, callsign: str, settings: dict[str, Any]) -> None:
        with self._lock:
            account = self._accounts.get(callsign)
            if account is None or self.broken is not None:
                return
            account.settings = settings
            self._save_accounts()

    # Login

    def login_delay(self, callsign: str) -> float:
        """Seconds before the next try is allowed (0: now)."""
        now = self._monotonic()
        waits = [t.not_before - now for t in self._throttles_for(callsign)]
        return max(0.0, *waits)

    def _throttles_for(self, callsign: str) -> list[_Throttle]:
        with self._lock:
            if len(self._throttles) > MAX_THROTTLES:
                # Every guessed callsign makes an entry: drop those whose delay
                # is over (their failures are forgotten; "*" is never dropped).
                now = self._monotonic()
                self._throttles = {
                    key: t for key, t in self._throttles.items() if key == "*" or t.not_before > now
                }
            return [self._throttles.setdefault(key, _Throttle()) for key in (callsign, "*")]

    def check_login(self, callsign: object, password: object) -> Account:
        """The account for a correct callsign and password; raises otherwise."""
        self._usable()
        name = callsign.strip().upper() if isinstance(callsign, str) else ""
        with self._lock:
            wait = self.login_delay(name)
            if wait > 0:
                raise AccountsError("login_throttled", f"too many failed logins, wait {wait:.0f} s")
            account = self._accounts.get(name)
            stored = account.password_hash if account is not None else _DUMMY_HASH
        given = password if isinstance(password, str) else ""
        matched = password_matches(given[:PASSWORD_MAX], stored)  # slow: outside the lock
        with self._lock:
            if not matched or account is None:
                now = self._monotonic()
                for throttle in self._throttles_for(name):
                    throttle.failures += 1
                    extra = min(throttle.failures - FREE_FAILURES, MAX_EXPONENT)
                    if extra >= 0:
                        throttle.not_before = now + min(MAX_DELAY_S, 2.0**extra)
                raise AccountsError("login_failed", "wrong callsign or password")
            for key in (name, "*"):
                self._throttles.pop(key, None)
        return account

    # Sessions

    def new_session(self, callsign: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[_token_hash(token)] = (callsign, self._clock() + SESSION_LIFETIME_S)
            self._save_sessions()
        return token

    def session_account(self, token: str | None) -> Account | None:
        """The account of a valid session token, or None."""
        if not token or self.broken is not None:
            return None
        with self._lock:
            entry = self._sessions.get(_token_hash(token))
            if entry is None:
                return None
            callsign, expiry = entry
            if expiry <= self._clock():
                self._sessions.pop(_token_hash(token), None)
                return None
            return self._accounts.get(callsign)

    def revoke(self, token: str | None) -> None:
        with self._lock:
            if token and self._sessions.pop(_token_hash(token), None) is not None:
                self._save_sessions()

    def revoke_account(self, callsign: str) -> None:
        with self._lock:
            before = len(self._sessions)
            self._sessions = {d: v for d, v in self._sessions.items() if v[0] != callsign}
            if len(self._sessions) != before:
                self._save_sessions()

    def session_ids(self, callsign: str) -> set[str]:
        """Hashes of the account's live sessions (to close its open pages).

        An expired session is not live: the page that holds it ends with it
        (AGENTS.md rule 16), not only at its next request.
        """
        now = self._clock()
        with self._lock:
            return {d for d, (c, e) in self._sessions.items() if c == callsign and e > now}

    @staticmethod
    def session_id(token: str) -> str:
        return _token_hash(token)
