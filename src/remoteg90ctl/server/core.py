# SPDX-License-Identifier: GPL-3.0-or-later
"""Server logic: client sessions, CAT and relaying of the transmit lease.

Independent of the transport. Each connected client gets a :class:`Session`
with a :class:`ClientContext` saying who it is; permissions (AGENTS.md rule 16)
will be checked against that context in stage 5.

Lease relay (AGENTS.md rule 5):

* The guard lease is renewed **only** in :meth:`ServerCore._forward_client_renewal`,
  which is called only while handling a ``tx_renew`` message from the client
  that owns the transmission. There is no timer or background task that renews.
* A client that disconnects has its transmission released immediately.
* A client that stays connected but stops renewing has its transmission
  released by the server after ``lease_timeout_s`` — in addition to, not
  instead of, the guard's own timeout.
"""

import asyncio
import contextlib
import functools
import logging
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remoteg90ctl import __version__
from remoteg90ctl.bandplan import BANDS, PLANS, in_band, region_of
from remoteg90ctl.checklist_record import RECORD as CHECKLIST_RECORD
from remoteg90ctl.checklist_record import checklist_state
from remoteg90ctl.civ import AGC_MODES, NB_LEVEL_STEPS, RF_GAIN_MAX, RF_GAIN_MIN, Mode
from remoteg90ctl.config import Backend, Config, IqSourceKind
from remoteg90ctl.iq.dsp import Spectrum
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.accounts import AccountsError, AccountStore
from remoteg90ctl.server.alc import level_to_display
from remoteg90ctl.server.audio_service import FRAME_TX_AUDIO, AudioService
from remoteg90ctl.server.audio_service import HEADER as TX_HEADER
from remoteg90ctl.server.cat import Cat, CatError
from remoteg90ctl.server.ft8_tx import (
    Ft8Error,
    Ft8Signal,
    Pcm,
    check_timing,
    make_signal,
    play,
)
from remoteg90ctl.server.guard_client import GuardClient, GuardUnavailableError
from remoteg90ctl.server.iq_service import IqService, IqUnavailableError, SourceFactory
from remoteg90ctl.server.logbook import Logbook, LogbookError
from remoteg90ctl.server.mixer import OUTPUT_MAX_DB, OUTPUT_MIN_DB
from remoteg90ctl.server.power import (
    SETTING_MIN_W,
    meter_to_watts,
    setting_to_watts,
    setting_whole_watts,
    watts_to_setting,
)
from remoteg90ctl.server.protocol import (
    ACCOUNTS_UNAVAILABLE,
    ALL_PERMISSIONS,
    ATU_MISMATCH,
    AUDIO_UNAVAILABLE,
    CLOCK_NOT_SYNCHRONIZED,
    DUMMY_LOAD_REQUIRED,
    FORBIDDEN,
    FRONT_END_MISMATCH,
    FRONT_END_STATES,
    GUARD_UNAVAILABLE,
    INVALID_VALUE,
    IQ_OFF,
    IQ_UNAVAILABLE,
    NB_MISMATCH,
    NO_LOG,
    NOT_DATA_MODE,
    OUT_OF_BAND,
    POWER_TOO_HIGH,
    REQUEST_PERMISSIONS,
    RF_GAIN_MISMATCH,
    RF_POWER_DISABLED,
    RF_POWER_MISMATCH,
    TX_BUSY,
    UNKNOWN_LEASE,
    UNKNOWN_TYPE,
    VFO_MISMATCH,
    Permission,
    Request,
    error,
    event,
    ok,
)
from remoteg90ctl.server.rtty_tx import RttyError, RttyTransmitter, key
from remoteg90ctl.server.smeter import BELOW_SCALE_FROM, db_to_text, level_to_db
from remoteg90ctl.server.tx_audio import TxAudio
from remoteg90ctl.server.waterfall import WaterfallHub

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Message = dict[str, object]
SILENCE_CHECK_INTERVAL_S = 0.1
# While a browser is connected (with or without the waterfall), the radio's
# frequency, mode and tuner are read this often so the page follows the VFO
# (the G90 sends no transceive frames).
FOLLOW_INTERVAL_S = 0.5
SETTINGS_EVERY = 2  # follow cycles per read of the radio's settings (1 s)
# The radio's power meter is read this often, whether anybody watches or not, so
# a transmission the guard did not key is noticed (microphone PTT, TUNE, VOX).
POWER_INTERVAL_S = 1.0
# A transmission ends after this long without any power on the meter: speech
# in SSB has pauses, and PTT without modulation shows no power at all.
POWER_HOLD_S = 3.0
# After our lease ends, output on the power meter is still ours for this long:
# the meter is read once a second and still showed power right after PTT was
# released (owner's radio, 2026-11-08), which raised a false "without a lease".
LEASE_TAIL_S = 2.0
# radio.mute_speaker: the volume is read this often and muted again if it is not 0
# (owner's request 2026-11-10: somebody may turn the knob at the radio).
SPEAKER_CHECK_S = 30.0
# After an antenna tuning the guard refuses changes of the radio for 3 s (the
# carrier cannot be stopped, D43); the speaker check keeps away a little longer.
TUNING_HOLD_S = 4.0
# Requests that retune the radio; held while a transmission or a tuning starts.
RADIO_TARGET_CHANGES = frozenset({"set_frequency", "set_mode", "set_vfo", "set_split"})
# Width of a browser's transmitted signal from the dial, for the band check
# (D54): the radio's SSB filter; the IARU Region 1 plan also keeps USB dials
# 3 kHz below the upper band edge.
TX_SPAN_HZ = 3000
# The CLI's PTT test on the antenna (D56, owner's decision 2026-11-11): the
# radio's set power at most this.
ANTENNA_TEST_MAX_W = 1.0
# How often the logged-in account's radio settings are saved when they changed,
# and always when its last page closes. Not more often: each save is a write
# with fsync, and the machine by the radio runs from a small 8 GB disk.
RADIO_SAVE_S = 30.0
# The S-meter reads 242 ("below the scale") while the radio transmits, also
# without output (measured 2026-11-08: microphone PTT in U-D or without speech,
# which the power meter cannot see). A drop from at least S1 to 242 that lasts
# MUTED_S_METER_S, while this server does not transmit, is reported as a
# possible transmission (owner's decision 2026-11-08). Informational, like D33:
# the same 242 also comes from a removed antenna or the RF gain turned down.
MUTED_S_METER_FROM = 22  # S1 (measured scale, server/smeter.py)
MUTED_S_METER_S = 1.0
# SWR meter (15 12) level at the end of its scale. Measured on the owner's G90
# (2026-11-03, mag loop): 100 at SWR 1.1, 220 at 1.5, 255 at 2.0, so the end
# of the scale means "SWR about 2 or more" (it may be reached from about 1.7).
SWR_HIGH_LEVEL = 255
# Consecutive readings at the end of the scale before warning (not on the rise
# of a transmission).
SWR_HIGH_READINGS = 2
# server_restart: the answer goes out first; then the server exits with
# EXIT_RESTART and systemd starts it again (Restart=on-failure).
RESTART_DELAY_S = 0.5
# Waterfall lines dimmed after a transmission ends: the I/Q output clicks and
# needs about 0.4 s to settle (AGENTS.md section 8, item 3).
BLANK_AFTER_TX_LINES = 10


@dataclass
class _RememberedRadio:
    """Accounts whose settings were restored at login, and what was saved last."""

    restored: set[str] = field(default_factory=set)
    saved: dict[str, dict[str, object]] = field(default_factory=dict)
    save_failing: bool = False  # the last save failed (one log line per outage)


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Who is asking and what they may do. ``kind`` is the transport (``cli``).

    Members of the ``remoteg90ctl`` group on the Unix socket have every
    permission until stage 5 (decisions.md D24.11); other transports get less.
    """

    kind: str
    session_id: str
    uid: int | None = None
    pid: int | None = None
    permissions: frozenset[Permission] = ALL_PERMISSIONS
    account: str | None = None  # the logged-in station's callsign (browsers, D48)

    @property
    def source(self) -> str:
        """Description recorded in the guard's TX log."""
        parts = [self.kind]
        if self.account is not None:
            parts.append(f"account={self.account}")
        if self.uid is not None:
            parts.append(f"uid={self.uid}")
        if self.pid is not None:
            parts.append(f"pid={self.pid}")
        parts.append(f"session={self.session_id}")
        return " ".join(parts)


@dataclass(eq=False)
class Session:
    context: ClientContext
    send: Callable[[Message], None]
    closed: bool = False


@dataclass(eq=False)
class _Transmission:
    token: str
    guard_lease: str
    session: Session
    last_client_renew: float
    limits: dict[str, Any] = field(default_factory=dict)


def mode_name(mode: Mode) -> str:
    return mode.name.replace("_", "-")


# Data modes (26 00 with the data flag) and the mode they are a variant of.
DATA_MODE_NAMES = {"U-D": Mode.USB, "L-D": Mode.LSB}


def display_mode(mode: Mode, data: bool) -> str:
    """The mode as the radio shows it: U-D and L-D for USB and LSB with the data flag."""
    if data:
        for name, base in DATA_MODE_NAMES.items():
            if base is mode:
                return name
    return mode_name(mode)


def base_mode(name: str | None) -> str | None:
    """USB for U-D and LSB for L-D (for the I/Q offset and passband tables)."""
    if name in DATA_MODE_NAMES:
        return mode_name(DATA_MODE_NAMES[name])
    return name


def parse_mode(name: object) -> Mode | None:
    if not isinstance(name, str):
        return None
    try:
        return Mode[name.upper().replace("-", "_")]
    except KeyError:
        return None


class ServerCore:
    # Browser accounts (D48) and the station log (D50), set by the web
    # transport; None without one.
    accounts: AccountStore | None = None
    logbook: Logbook | None = None
    # FT8/FT4 transmission being played (stage 7, D53), and the clock it is
    # timed by (replaced in tests).
    _ft8_task: "asyncio.Task[None] | None" = None
    ft8_wall_clock: Callable[[], float] = staticmethod(time.time)
    ft8_monotonic: Callable[[], float] = staticmethod(time.monotonic)
    ft8_sleep: Callable[[float], Awaitable[None]] = staticmethod(asyncio.sleep)
    # RTTY transmission being keyed (D61): its lease and its signal.
    _rtty: "tuple[_Transmission, RttyTransmitter] | None" = None
    _rtty_task: "asyncio.Task[None] | None" = None
    # True only while _end_tx clears the lease the guard still holds (tx_audio.stop).
    _ending_own_lease = False

    def __init__(
        self,
        config: Config,
        *,
        clock: Clock = time.monotonic,
        iq_source_factory: SourceFactory | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self.iq: IqService | None = None
        if config.iq is not None and config.iq.source is not IqSourceKind.NONE:
            self.iq = (
                IqService(config.iq)
                if iq_source_factory is None
                else IqService(config.iq, source_factory=iq_source_factory)
            )
        self.audio: AudioService | None = (
            AudioService(config.audio, state_dir=config.server.state_dir)
            if config.audio is not None
            else None
        )
        self.tx_audio: TxAudio | None = TxAudio(config.audio) if config.audio is not None else None
        self.guard = GuardClient(
            config.guard.socket_path,
            on_event=self._on_guard_event,
            on_connection=self._on_guard_connection,
        )
        self.cat = Cat(
            self.guard,
            radio_address=config.radio.radio_address,
            controller_address=config.radio.controller_address,
        )
        self.waterfall = self._attach_waterfall()
        # Last frequency and mode known from CAT (None until read).
        self.radio_hz: int | None = None
        self.radio_mode: str | None = None
        # How the web transport closes the open pages of an account whose
        # sessions ended (rule 16: ends its lease).
        self.close_pages: Callable[[str], None] = lambda _callsign: None
        # Radio settings remembered on the logged-in account (D48 part C): saved
        # only once restored at login, so the login itself does not overwrite them.
        self._remembered = _RememberedRadio()
        # Tuning: one CAT command at a time; a newer target replaces waiting ones.
        self._tune_lock = asyncio.Lock()
        # Newest waiting target and everyone waiting for it.
        self._tune_pending: tuple[int, list[asyncio.Future[None]]] | None = None
        self.tune_commands = 0  # observable by tests: CAT set-frequency frames sent
        self._blank_left = 0  # waterfall lines still dimmed after a transmission
        # Power meter (decisions.md D33): last value, and whether the radio emits.
        self.power_interval_s = POWER_INTERVAL_S
        self.power_hold_s = POWER_HOLD_S
        self.power_value: int | None = None
        self.rf_power_level: int | None = None  # RF power setting, 0-255
        self.swr_level: int | None = None  # SWR meter, 0-255
        self.swr_high = False  # informational warning, no safety path (D33)
        self.atu_on: bool | None = None  # antenna tuner state, read with the VFO
        self.front_end: str | None = None  # "off", "pre" or "att", read with the VFO
        self.agc: str | None = None  # "off", "fast", "slow" or "auto", read with the VFO
        self.nb: tuple[bool, int] | None = None  # noise blanker on, level 0-10
        self.rf_gain: int | None = None  # 1-100 %, read with the VFO
        self.s_meter: int | None = None  # 15 02 level, read with the VFO in receive
        self.alc: int | None = None  # 15 13 level, read with the VFO in transmit
        self.vfo: str | None = None  # "A" or "B", read with the VFO (25 01)
        self.other_hz: int | None = None  # the unselected VFO, read with the VFO
        self.split: bool | None = None  # read with the settings and before TX
        self._swr_high_count = 0
        self.rf_active = False
        self._rf_seen_at: float | None = None
        self._tx_ended_at: float | None = None  # monotonic, for LEASE_TAIL_S
        self._s_meter_heard = False  # the last S-meter reading was S1 or more
        self._s_meter_muted_at: float | None = None  # 242 after such a reading
        self.tx_suspected = False  # the S-meter suggests a transmission (yellow bar)
        self._mute_task: asyncio.Task[None] | None = None
        self.warnings_without_lease = 0  # observable by tests: tx_without_lease logged
        self._sessions: set[Session] = set()
        self._tx_state: _Transmission | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._audio_failed_for: _Transmission | None = None
        self._tuned_at: float | None = None  # monotonic time of the last antenna tuning
        self._tx_starting = False
        self.guard_renewals = 0  # observable by tests: every one stems from a client renewal

    # The active transmission. Every change goes through this property, so the
    # browser's audio path to the radio opens with a lease and closes with it,
    # whatever ends it (AGENTS.md section 3).

    @property
    def _tx(self) -> "_Transmission | None":
        return self._tx_state

    @_tx.setter
    def _tx(self, tx: "_Transmission | None") -> None:
        old, self._tx_state = self._tx_state, tx
        if (old is None) != (tx is None):
            if tx is None:
                # What the meter shows next is the tail of this lease (LEASE_TAIL_S).
                self._tx_ended_at = time.monotonic()
                self._rf_seen_at = None
                self.rf_active = False
            # The page's red bar: on for the whole lease, not only while the
            # power meter (read every second, zero between words) shows output.
            self._broadcast(
                event("radio_transmitting", active=tx is not None, leased=tx is not None)
            )
        if self.tx_audio is None or old is tx:
            return
        if tx is None:
            self.tx_audio.stop(play_tail=self._ending_own_lease)
        elif tx.session.context.kind == "web":
            self.tx_audio.start(tx.session)
            if not self.tx_audio.active:
                # The card did not open: a lease without sound would key the
                # radio for nothing (code review 2026-11-10, point 10e).
                self._audio_failed_for = tx
                task = asyncio.get_running_loop().create_task(
                    self._end_tx(tx, notify_reason="audio_unavailable")
                )
                self._background.add(task)
                task.add_done_callback(self._background.discard)

    def tx_audio_frame(self, session: "Session", frame: bytes) -> bool:
        """A binary frame from a browser: audio for the radio, played only during its lease."""
        if self.tx_audio is None or len(frame) < TX_HEADER.size:
            return False
        kind, codec, _samples, _seq = TX_HEADER.unpack_from(frame)
        if kind != FRAME_TX_AUDIO:
            return False
        return self.tx_audio.feed(session, codec, frame[TX_HEADER.size :])

    # Lifecycle

    def hardware_checklist(self) -> dict[str, object] | None:
        """``status.hardware_checklist``; None with the simulator (nothing to check)."""
        if self._config.radio.backend is not Backend.HARDWARE:
            return None
        return checklist_state(
            self._config.server.state_dir / CHECKLIST_RECORD, self._config.radio.port
        )

    async def run(self) -> None:
        """Run the guard connection and the silence watch until cancelled."""
        checklist = self.hardware_checklist()
        if checklist is not None and checklist["result"] != "passed":
            skipped = checklist["skipped"]
            names = ",".join(str(item) for item in skipped) if isinstance(skipped, list) else ""
            logger.warning(
                "hardware_checklist_not_passed", extra=kv(result=checklist["result"], skipped=names)
            )
        async with asyncio.TaskGroup() as group:
            group.create_task(self.guard.run())
            group.create_task(self._watch_silent_clients())
            group.create_task(self._watch_power())
            if self._config.radio.mute_speaker:
                group.create_task(self._watch_speaker())
            group.create_task(self._remember_radio())
            if self.audio is not None:
                group.create_task(self.audio.run())
            if self.iq is not None:
                group.create_task(self.iq.run())
                group.create_task(self._follow_radio())

    def tx_account(self) -> str | None:
        """The account whose page holds the transmit lease now, if any."""
        tx = self._tx
        return tx.session.context.account if tx is not None else None

    async def _tx_output(self, session: Session, request: Request) -> Message:
        """The sound card's output level, which drives the radio (server/mixer.py).
        Set also while transmitting (owner's decision 2026-11-14: tuned while
        watching the ALC); power and time stay limited by the radio's setting,
        max_rf_power_w and the guard."""
        audio = self.audio
        if audio is None or not audio.output.available:
            return error(request.id, AUDIO_UNAVAILABLE, "no sound card for the radio configured")
        limits = {"min_db": OUTPUT_MIN_DB, "max_db": OUTPUT_MAX_DB}
        if request.type == "get_tx_output":
            return ok(request.id, db=await audio.output.read(), **limits)
        db = request.fields.get("db")
        if isinstance(db, bool) or not isinstance(db, int | float) or not math.isfinite(db):
            return error(request.id, INVALID_VALUE, "'db' must be a number")
        level = await audio.output.set(float(db))
        if level is None:
            return error(request.id, AUDIO_UNAVAILABLE, "the sound card's level could not be set")
        logger.info("tx_output_set", extra=kv(source=session.context.source, db=level))
        self._broadcast(event("tx_output", db=level))
        return ok(request.id, db=level, **limits)

    @functools.cached_property
    def restart_requested(self) -> asyncio.Event:
        """Set by server_restart; run_server() then stops and main() exits for systemd."""
        return asyncio.Event()

    def _server_restart(self, session: Session, request: Request) -> Message:
        """Restart this server (the page's button); never while anything transmits."""
        if self._tx is not None or self._tx_starting or self.rf_active:
            return error(request.id, TX_BUSY, "the radio is transmitting; restart when it ends")
        logger.warning("server_restart_requested", extra=kv(source=session.context.source))
        asyncio.get_running_loop().call_later(RESTART_DELAY_S, self.restart_requested.set)
        return ok(request.id, restart_in_s=RESTART_DELAY_S)

    async def shutdown(self) -> None:
        tx = self._tx
        if tx is not None:
            await self._end_tx(tx, notify_reason="server_shutdown")
        await self.guard.close()

    # Accounts (D48): the CLI may list them, reset a password and delete one.

    def _account_request(self, request: Request) -> Message:
        store = self.accounts
        if store is None:
            return error(request.id, ACCOUNTS_UNAVAILABLE, "no web server: no accounts")
        try:
            if request.type == "account_list":
                return ok(
                    request.id,
                    accounts=[a.public() for a in store.accounts()],
                    broken=store.broken,
                )
            callsign = str(request.fields.get("callsign", "")).strip().upper()
            if request.type == "account_password":
                store.set_password(callsign, request.fields.get("password"))
            else:
                store.delete(callsign)
        except AccountsError as exc:
            return error(request.id, exc.code, exc.message)
        logger.warning("account_changed", extra=kv(type=request.type, callsign=callsign))
        self.close_pages(callsign)  # its sessions ended: so do its open pages
        return ok(request.id)

    # Station log (stage 9, D50): the account's own log. Nothing here talks to
    # the radio (AGENTS.md, stage 9).

    def _log_request(self, session: Session, request: Request) -> Message:
        account = session.context.account
        if self.logbook is None or account is None:
            return error(request.id, NO_LOG, "the log belongs to a logged-in account (web page)")
        fields = request.fields
        try:
            if request.type == "log_list":
                search = fields.get("search", "")
                limit = fields.get("limit", 100)
                qsos, total = self.logbook.qsos(
                    account,
                    search if isinstance(search, str) else "",
                    limit if isinstance(limit, int) and not isinstance(limit, bool) else 100,
                )
                return ok(request.id, qsos=qsos, total=total)
            if request.type == "log_worked":
                call = fields.get("call", "")
                return ok(request.id, worked=self.logbook.worked_before(account, str(call)))
            if request.type == "log_add":
                result: dict[str, Any] | None = self.logbook.add(account, fields)
            else:
                qso_id = fields.get("qso_id")
                if isinstance(qso_id, bool) or not isinstance(qso_id, int):
                    return error(request.id, INVALID_VALUE, "'qso_id' must be an integer")
                if request.type == "log_update":
                    result = self.logbook.update(account, qso_id, fields)
                else:
                    self.logbook.delete(account, qso_id)
                    result = None
        except LogbookError as exc:
            return error(request.id, exc.code, exc.message)
        for other in self._sessions:  # the account's other pages refresh their list
            if other.context.account == account:
                other.send(event("log_changed"))
        logger.info("log_changed", extra=kv(account=account, type=request.type))
        return ok(request.id, qso=result)

    def open_session(self, context: ClientContext, send: Callable[[Message], None]) -> Session:
        account = context.account
        first_page = account is not None and not any(
            s.context.account == account for s in self._sessions
        )
        session = Session(context, send)
        self._sessions.add(session)
        logger.info("client_connected", extra=kv(source=context.source))
        if first_page and account is not None:
            # The account's first open page: put the radio as the station left it.
            self._remembered.restored.discard(account)
            asyncio.get_running_loop().create_task(self._restore_radio(session, account))
        return session

    # Radio settings on the account (D48 part C, owner's decision 2026-11-08)

    def _radio_snapshot(self) -> dict[str, object]:
        """The settings known now; unknown ones are left out."""
        snapshot: dict[str, object] = {
            "vfo": self.vfo,
            "hz": self.radio_hz,
            "mode": self.radio_mode,
            "split": self.split,
            "front_end": self.front_end,
            "agc": self.agc,
            "rf_gain": self.rf_gain,
            "atu": self.atu_on,
        }
        if self.nb is not None:
            snapshot["nb_on"], snapshot["nb_level"] = self.nb
        if self.rf_power_level is not None:
            snapshot["power_w"] = setting_whole_watts(self.rf_power_level)
        return {k: v for k, v in snapshot.items() if v is not None}

    def _restore_requests(self, saved: dict[str, Any]) -> list[tuple[str, dict[str, object]]]:
        """The settings as ordinary requests, in an order that works on the G90:
        the VFO first (its own frequency and mode), split last of the tuning."""
        steps: list[tuple[str, dict[str, object]]] = []
        if saved.get("vfo") in ("A", "B"):
            steps.append(("set_vfo", {"vfo": saved["vfo"]}))
        if isinstance(saved.get("hz"), int):
            steps.append(("set_frequency", {"hz": saved["hz"]}))
        if isinstance(saved.get("mode"), str):
            steps.append(("set_mode", {"mode": saved["mode"]}))
        if isinstance(saved.get("split"), bool):
            steps.append(("set_split", {"on": saved["split"]}))
        if isinstance(saved.get("front_end"), str):
            steps.append(("set_front_end", {"state": saved["front_end"]}))
        if isinstance(saved.get("agc"), str):
            steps.append(("set_agc", {"agc": saved["agc"]}))
        if isinstance(saved.get("nb_on"), bool) and isinstance(saved.get("nb_level"), int):
            steps.append(("set_nb", {"on": saved["nb_on"], "level": saved["nb_level"]}))
        if isinstance(saved.get("rf_gain"), int):
            steps.append(("set_rf_gain", {"percent": saved["rf_gain"]}))
        if isinstance(saved.get("atu"), bool):
            steps.append(("set_atu", {"on": saved["atu"]}))
        limit = self._config.safety.max_rf_power_w
        if isinstance(saved.get("power_w"), int) and limit is not None:
            # Never above the configured limit, even if it was lowered since.
            steps.append(("set_rf_power", {"watts": min(saved["power_w"], int(limit))}))
        return steps

    async def _restore_radio(self, session: Session, account: str) -> None:
        """Set the saved settings through the normal requests (permissions, TX refusal)."""
        stored = self.accounts.get(account) if self.accounts is not None else None
        saved = stored.settings.get("radio") if stored is not None else None
        failed: list[str] = []
        if isinstance(saved, dict) and saved:
            for kind, fields in self._restore_requests(saved):
                if session.closed:
                    return
                answer = await self.handle(session, Request("restore", kind, dict(fields)))
                if not answer.get("ok"):
                    failed.append(f"{kind}: {answer.get('message', answer.get('error'))}")
            logger.info("radio_settings_restored", extra=kv(account=account, failed=len(failed)))
            session.send(event("radio_settings_restored", failed=failed))
        self._remembered.saved[account] = self._radio_snapshot()
        self._remembered.restored.add(account)

    async def _remember_radio(self) -> None:
        """Every RADIO_SAVE_S: the logged-in account's radio settings, when they changed."""
        while True:
            await asyncio.sleep(RADIO_SAVE_S)
            if self.accounts is None:
                continue
            account = next(
                (s.context.account for s in self._sessions if s.context.account is not None),
                None,
            )
            if account is not None:
                self._save_radio(account)

    def _save_radio(self, account: str) -> None:
        """Save the account's radio settings if they changed; a failing disk never raises.

        A full or failing disk (the machine by the radio has 8 GB; code review
        2026-11-10, point 5) would otherwise end the whole server from the
        saving task, or skip the clean-up of a closing page.
        """
        try:
            self._save_radio_unguarded(account)
        except OSError as exc:
            if not self._remembered.save_failing:
                logger.error("radio_settings_not_saved", extra=kv(account=account, error=exc))
            self._remembered.save_failing = True
        else:
            self._remembered.save_failing = False

    def _save_radio_unguarded(self, account: str) -> None:
        if self.accounts is None or account not in self._remembered.restored:
            return
        snapshot = self._radio_snapshot()
        if snapshot and snapshot != self._remembered.saved.get(account):
            stored = self.accounts.get(account)
            if stored is None:
                return
            self.accounts.save_settings(account, {**stored.settings, "radio": snapshot})
            self._remembered.saved[account] = snapshot

    async def close_session(self, session: Session) -> None:
        session.closed = True
        self._sessions.discard(session)
        logger.info("client_disconnected", extra=kv(source=session.context.source))
        # The lease first: nothing else may delay the radio's return to receive.
        tx = self._tx
        if tx is not None and tx.session is session:
            await self._end_tx(tx, notify_reason=None)
        account = session.context.account
        if account is not None and not any(s.context.account == account for s in self._sessions):
            self._save_radio(account)  # its last page closed

    # Requests

    def permission_refusal(self, session: Session, request: Request) -> Message | None:
        """``forbidden`` if the session lacks the permission the request needs (rule 16).

        Also for the requests the web transport answers itself (subscriptions).
        """
        required = REQUEST_PERMISSIONS.get(request.type)
        if required is None or required in session.context.permissions:
            return None
        logger.warning(
            "request_forbidden",
            extra=kv(source=session.context.source, type=request.type, needs=required),
        )
        return error(request.id, FORBIDDEN, f"{request.type} needs the {required} permission")

    async def handle(self, session: Session, request: Request) -> Message:
        refusal = self.permission_refusal(session, request)
        if refusal is not None:
            return refusal
        if self._tx_starting and request.type in RADIO_TARGET_CHANGES:
            # A transmission is being checked and keyed: the frequency, mode and
            # VFO it was checked at must stay (code review 2026-11-10, point 2).
            return error(
                request.id,
                TX_BUSY,
                f"{request.type} is held for a moment: a transmission is starting",
            )
        if request.type == "server_restart":
            return self._server_restart(session, request)
        if request.type in ("get_tx_output", "set_tx_output"):
            return await self._tx_output(session, request)
        try:
            return await self._dispatch(session, request)
        except GuardUnavailableError as exc:
            return error(request.id, GUARD_UNAVAILABLE, str(exc))
        except CatError as exc:
            return error(request.id, exc.code, exc.message)

    async def _dispatch(self, session: Session, request: Request) -> Message:
        fields = request.fields
        match request.type:
            case "status":
                return await self._status(session, request)
            case "get_frequency":
                current = await self.cat.get_frequency()
                self._radio_state(hz=current)
                return ok(request.id, hz=current)
            case "set_frequency":
                hz = fields.get("hz")
                if isinstance(hz, bool) or not isinstance(hz, int) or hz <= 0:
                    return error(request.id, INVALID_VALUE, "'hz' must be a positive integer")
                await self._tune(hz)
                return ok(request.id)
            case (
                "get_power_meter"
                | "set_rf_power"
                | "get_atu"
                | "set_atu"
                | "atu_tune"
                | "get_af"
                | "set_af"
                | "get_front_end"
                | "set_front_end"
                | "get_agc"
                | "set_agc"
                | "get_nb"
                | "set_nb"
                | "get_rf_gain"
                | "set_rf_gain"
                | "get_s_meter"
                | "get_alc"
                | "get_vfo"
                | "set_vfo"
                | "set_split"
            ):
                return await self._meters_and_tuner(session, request)
            case "get_mode":
                name = await self._read_mode()
                self._radio_state(mode=name)
                return ok(request.id, mode=name)
            case "set_mode":
                return await self._set_mode(request, fields.get("mode"))
            case (
                "waterfall_subscribe"
                | "waterfall_unsubscribe"
                | "system_subscribe"
                | "system_unsubscribe"
                | "log_subscribe"
                | "log_unsubscribe"
            ):
                return error(request.id, UNKNOWN_TYPE, f"{request.type} is served on the WebSocket")
            case "account_list" | "account_password" | "account_delete":
                return self._account_request(request)
            case "log_list" | "log_worked" | "log_add" | "log_update" | "log_delete":
                return self._log_request(session, request)
            case "iq_record":
                return await self._iq_record(request)
            case _:
                return await self._dispatch_transmit(session, request)

    async def _dispatch_transmit(self, session: Session, request: Request) -> Message:
        match request.type:
            case "tx_request":
                return await self._tx_request(session, request)
            case "tx_renew":
                return await self._tx_renew(session, request)
            case "tx_release":
                return await self._tx_release(session, request)
            case "ft8_transmit":
                return self._ft8_transmit(session, request)
            case "rtty_start":
                return self._rtty_start(session, request)
            case "rtty_text" | "rtty_end":
                return self._rtty_more(session, request)
            case "get_bandplan":
                return self._bandplan(session, request)
            case "tx_unlock" | "port_open":
                return self._passthrough(request, await self.guard.request(request.type))
            case _:
                return error(request.id, UNKNOWN_TYPE, f"unknown type {request.type}")

    async def _status(self, session: Session, request: Request) -> Message:
        guard_status: dict[str, Any] | None = None
        if self.guard.connected:
            with contextlib.suppress(GuardUnavailableError):
                response = await self.guard.request("status")
                guard_status = {k: v for k, v in response.items() if k not in ("v", "id", "ok")}
                # The guard's lease id is internal to the server.
                if isinstance(guard_status.get("tx"), dict):
                    guard_status["tx"] = {
                        k: v for k, v in guard_status["tx"].items() if k != "lease"
                    }
        tx = self._tx
        return ok(
            request.id,
            version=__version__,
            backend=str(self._config.radio.backend),
            guard_connected=guard_status is not None,
            guard=guard_status,
            tx_active=tx is not None,
            tx_yours=tx is not None and tx.session is session,
            iq=self._iq_status(),
            audio=self.audio.status() if self.audio is not None else None,
            power={**self._power_fields(), "transmitting": self.rf_active},
            max_rf_power_w=self._config.safety.max_rf_power_w,
            hardware_checklist=self.hardware_checklist(),
        )

    def _iq_status(self) -> dict[str, object] | None:
        """``status.iq``: the I/Q input, the switched-off one, or None without [iq]."""
        if self.iq is not None:
            return self.iq.status()
        iq = self._config.iq
        if iq is None:
            return None
        return {
            "source": str(iq.source),
            "running": False,
            "error": None,
            "sample_rate": iq.sample_rate,
            "passband_hz": {mode: list(edges) for mode, edges in iq.passband_hz.items()},
        }

    async def _iq_record(self, request: Request) -> Message:
        seconds = request.fields.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int | float):
            return error(request.id, INVALID_VALUE, "'seconds' must be a number")
        if self.iq is None:
            return error(request.id, IQ_UNAVAILABLE, IQ_OFF)
        try:
            path = await self.iq.record(float(seconds))
        except ValueError as exc:
            return error(request.id, INVALID_VALUE, str(exc))
        except IqUnavailableError as exc:
            return error(request.id, IQ_UNAVAILABLE, str(exc))
        iq = self.iq.config
        return ok(
            request.id, path=str(path), sample_rate=iq.sample_rate, sample_bits=iq.sample_bits
        )

    # Transmit lease relay

    async def _tx_request(self, session: Session, request: Request) -> Message:
        confirmed = request.fields.get("dummy_load_confirmed", False)
        on_antenna = request.fields.get("antenna_confirmed", False)
        if not isinstance(confirmed, bool) or not isinstance(on_antenna, bool):
            return error(request.id, INVALID_VALUE, "confirmations must be booleans")
        web = session.context.kind == "web"
        # The CLI test confirms a dummy load (D15), or the antenna at low power
        # (owner's decision 2026-11-11, D56: SSB, bands common to every ITU
        # region, at most ANTENNA_TEST_MAX_W set); a browser confirms
        # transmitting on the antenna in a dialog instead (stage 4, D46).
        antenna_test = not web and on_antenna and not confirmed
        if self._config.radio.backend is Backend.HARDWARE and not (confirmed or on_antenna):
            return error(
                request.id,
                DUMMY_LOAD_REQUIRED,
                "transmitting on hardware requires confirming a dummy load is connected",
            )
        if self._tx is not None or self._tx_starting:
            return error(request.id, TX_BUSY, "a transmission is already active")
        # From here to the end of the check after the grant nothing may retune
        # the radio (the flag holds set_frequency, set_mode, set_vfo and
        # set_split): the band check and the keying are one step (code review
        # 2026-11-10, point 2; AGENTS.md rule 17).
        self._tx_starting = True
        try:
            hz: int | None = None
            if web:
                hz, refusal = await self._web_target_check(session, request)
                if refusal is not None:
                    return refusal
            elif antenna_test:
                hz, refusal = await self._antenna_test_check(request)
                if refusal is not None:
                    return refusal
            frequency = hz if web or antenna_test else await self._frequency_for_log()
            response = await self.guard.request(
                "tx_request", source=session.context.source, frequency_hz=frequency
            )
            if not response.get("ok"):
                return self._passthrough(request, response)
            guard_lease = str(response["lease"])
            limits = {
                key: response[key]
                for key in ("lease_renew_interval_s", "lease_timeout_s", "hard_limit_s")
            }
            tx = _Transmission(secrets.token_hex(8), guard_lease, session, self._clock(), limits)
            self._tx = tx
            refusal = await self._confirm_grant(
                session, request, tx, web=web, antenna_test=antenna_test
            )
            if refusal is not None:
                return refusal
        finally:
            self._tx_starting = False
        logger.info("tx_granted", extra=kv(source=session.context.source, frequency_hz=frequency))
        return ok(request.id, lease=tx.token, **limits)

    async def _confirm_grant(
        self,
        session: Session,
        request: Request,
        tx: _Transmission,
        *,
        web: bool,
        antenna_test: bool = False,
    ) -> Message | None:
        """After the guard's grant: the refusal that ends the new lease again, or None."""
        if session.closed:
            # The client vanished while we waited for the guard.
            await self._end_tx(tx, notify_reason=None)
            return error(request.id, UNKNOWN_LEASE, "client disconnected")
        if web or antenna_test:
            # The lease exists, so the guard now refuses every change of the
            # radio: what is read now is the truth. Anything that slipped in
            # after the first check ends the lease again at once; a radio that
            # cannot be read is not transmitted on either.
            try:
                if web:
                    _, refusal = await self._web_target_check(session, request)
                else:
                    _, refusal = await self._antenna_test_check(request)
            except Exception:
                await self._end_tx(tx, notify_reason=None)
                raise
            if refusal is not None:
                await self._end_tx(tx, notify_reason=None)
                return refusal
        if self._tx is not tx:
            # Ended meanwhile: not granted. Only a card that did not open is
            # "audio_unavailable"; the guard or the port may end it too.
            if self._audio_failed_for is tx:
                return error(request.id, AUDIO_UNAVAILABLE, "the sound card did not open")
            return error(request.id, UNKNOWN_LEASE, "the transmission ended while starting")
        return None

    async def _antenna_test_check(self, request: Request) -> tuple[int, Message | None]:
        """The CLI's PTT test on the antenna (D56): SSB, a band of every region, low power.

        No account and so no region here: the whole signal (3 kHz in SSB) must
        lie in a band that every ITU region has (rule 17's spirit without a
        locator), and the radio's set power must be at most ANTENNA_TEST_MAX_W.
        A radio that cannot be read is not transmitted on.
        """
        hz, mode, _split = await self._tx_target()
        if mode in ("USB", "U-D"):
            low, high = hz, hz + TX_SPAN_HZ
        elif mode in ("LSB", "L-D"):
            low, high = hz - TX_SPAN_HZ, hz
        else:
            return hz, error(
                request.id,
                NOT_DATA_MODE,
                f"the radio is in {mode}; a test on the antenna needs SSB",
            )
        if not all(in_band(region, low, high) for region in BANDS):
            return hz, error(
                request.id,
                OUT_OF_BAND,
                f"{hz / 1e6:.4f} MHz: a test on the antenna needs a band every ITU region has",
            )
        level, _ = await self.cat.get_rf_power()
        watts = setting_whole_watts(level)  # the radio sets whole watts (D44)
        if watts > ANTENNA_TEST_MAX_W:
            return hz, error(
                request.id,
                POWER_TOO_HIGH,
                f"the radio is set to {watts:.0f} W; a test on the antenna needs at most"
                f" {ANTENNA_TEST_MAX_W:g} W",
            )
        return hz, None

    async def _web_target_check(
        self, session: Session, request: Request
    ) -> tuple[int, Message | None]:
        """Read where the radio would transmit; the refusal if a browser may not there.

        The G90 takes audio from the DE-19 only in U-D/L-D; in USB it would send
        whatever its microphone hears (owner's decision, stage 4). In split it
        transmits on the other VFO, in that VFO's mode (measured 2026-11-08):
        that is the one checked. A failed read refuses TX. The whole signal
        must lie inside the account's ITU region's band (D54).
        """
        hz, mode, split = await self._tx_target()
        if mode not in DATA_MODE_NAMES:
            where = f"the transmit VFO (split) is in {mode}" if split else f"the radio is in {mode}"
            return hz, error(
                request.id,
                NOT_DATA_MODE,
                f"{where}; voice from the browser needs U-D or L-D",
            )
        low, high = (hz, hz + TX_SPAN_HZ) if mode == "U-D" else (hz - TX_SPAN_HZ, hz)
        refusal = self._band_refusal(session, low, high)
        if refusal is not None:
            return hz, error(request.id, OUT_OF_BAND, refusal)
        return hz, None

    async def _tx_renew(self, session: Session, request: Request) -> Message:
        tx = self._owned(session, request)
        if tx is None:
            return error(request.id, UNKNOWN_LEASE, "no active lease of yours with this id")
        tx.last_client_renew = self._clock()
        response = await self._forward_client_renewal(tx, request)
        if not response.get("ok"):
            if self._tx is tx:
                self._tx = None
            return self._passthrough(request, response)
        audio = self.tx_audio
        if audio is not None and audio.active and tx.session.context.kind == "web":
            # The voice path's state, shown next to PTT (2026-11-21).
            return ok(request.id, remaining_s=response.get("remaining_s"), audio=audio.stats())
        return ok(request.id, remaining_s=response.get("remaining_s"))

    async def _forward_client_renewal(self, tx: _Transmission, client_request: Request) -> Message:
        """The only place that renews the guard lease; ``client_request`` is the cause."""
        if client_request.type != "tx_renew" or client_request.fields.get("lease") != tx.token:
            raise RuntimeError("guard renewal without a matching client renewal")
        self.guard_renewals += 1
        return await self.guard.request("tx_renew", lease=tx.guard_lease)

    async def _tx_release(self, session: Session, request: Request) -> Message:
        tx = self._owned(session, request)
        if tx is None:
            return error(request.id, UNKNOWN_LEASE, "no active lease of yours with this id")
        await self._end_tx(tx, notify_reason=None)
        return ok(request.id)

    # FT8/FT4 transmission (stage 7, D53): the page holds the lease; the
    # server makes the signal and plays it, only within that lease.

    def _ft8_transmit(self, session: Session, request: Request) -> Message:
        if session.context.kind != "web":
            # Only a browser's lease opens the audio path to the radio.
            return error(request.id, FORBIDDEN, "FT8/FT4 transmission is for the web page")
        tx = self._owned(session, request)
        if tx is None:
            return error(request.id, UNKNOWN_LEASE, "no active lease of yours with this id")
        if self.tx_audio is None or self.audio is None:
            return error(request.id, AUDIO_UNAVAILABLE, "no [audio] section in the configuration")
        if self.audio.clock.synchronized is not True:
            # AGENTS.md stage 7: no FT8/FT4 transmission without a synchronised clock.
            return error(
                request.id, CLOCK_NOT_SYNCHRONIZED, "the server's clock is not synchronised (NTP)"
            )
        if self._ft8_task is not None and not self._ft8_task.done():
            return error(request.id, TX_BUSY, "an FT8/FT4 transmission is already playing")
        if self._rtty_busy():
            return error(request.id, TX_BUSY, "an RTTY transmission is being keyed")
        fields = request.fields
        try:
            signal = make_signal(
                fields.get("mode"),
                fields.get("message"),
                fields.get("tone_hz"),
                fields.get("level_db", -10),
                fields.get("start_ms"),
            )
            check_timing(signal, self.ft8_wall_clock())
        except Ft8Error as exc:
            return error(request.id, exc.code, str(exc))
        self._ft8_task = asyncio.create_task(self._ft8_play(tx, signal))
        logger.info(
            "ft8_transmit",
            extra=kv(mode=signal.mode, message=signal.message, tone_hz=signal.tone_hz),
        )
        return ok(request.id, message=signal.message, duration_s=round(signal.duration_s, 2))

    async def _ft8_play(self, tx: _Transmission, signal: Ft8Signal) -> None:
        audio = self.tx_audio
        if audio is None:
            return

        def feed(pcm: Pcm) -> bool:
            return self._tx is tx and audio.feed_pcm(tx.session, pcm)

        def alive() -> bool:
            return self._tx is tx and audio.active

        done = await play(
            signal,
            feed,
            alive=alive,
            wall_clock=self.ft8_wall_clock,
            monotonic=self.ft8_monotonic,
            sleep=self.ft8_sleep,
        )
        if self._tx is not tx:
            return  # the lease ended meanwhile; whoever ended it told the page
        if done:
            if not tx.session.closed:
                tx.session.send(
                    event("ft8_sent", lease=tx.token, start_ms=round(signal.start_utc * 1000))
                )
        else:
            # The playback broke while the lease lives: the radio would key
            # without sound until the hard limit (code review 2026-11-10, 1).
            logger.error("ft8_playback_failed", extra=kv(source=tx.session.context.source))
            await self._end_tx(tx, notify_reason="ft8_playback_failed")

    # RTTY transmission (D61): the page holds the lease and sends the text as
    # it is typed; the server keys it out, only within that lease.

    def _rtty_busy(self) -> bool:
        return self._rtty_task is not None and not self._rtty_task.done()

    def _rtty_start(self, session: Session, request: Request) -> Message:
        if session.context.kind != "web":
            # Only a browser's lease opens the audio path to the radio.
            return error(request.id, FORBIDDEN, "RTTY transmission is for the web page")
        tx = self._owned(session, request)
        if tx is None:
            return error(request.id, UNKNOWN_LEASE, "no active lease of yours with this id")
        if self.tx_audio is None:
            return error(request.id, AUDIO_UNAVAILABLE, "no [audio] section in the configuration")
        if self._rtty_busy() or (self._ft8_task is not None and not self._ft8_task.done()):
            return error(request.id, TX_BUSY, "a transmission is already being played")
        fields = request.fields
        try:
            signal = RttyTransmitter(
                fields.get("mark_hz"), fields.get("reverse", False), fields.get("level_db", -10)
            )
            if "text" in fields:
                signal.add(fields["text"])
        except RttyError as exc:
            return error(request.id, exc.code, str(exc))
        self._rtty = (tx, signal)
        self._rtty_task = asyncio.create_task(self._rtty_play(tx, signal))
        logger.info(
            "rtty_transmit",
            extra=kv(mark_hz=signal.mark_hz, reverse=signal.reverse, level_db=signal.level_db),
        )
        return ok(request.id, waiting=signal.waiting)

    def _rtty_more(self, session: Session, request: Request) -> Message:
        tx = self._owned(session, request)
        if tx is None:
            return error(request.id, UNKNOWN_LEASE, "no active lease of yours with this id")
        current = self._rtty
        if current is None or current[0] is not tx or not self._rtty_busy():
            return error(request.id, INVALID_VALUE, "no RTTY transmission on this lease")
        signal = current[1]
        if request.type == "rtty_end":
            signal.end()
            return ok(request.id, waiting=signal.waiting)
        try:
            waiting = signal.add(request.fields.get("text"))
        except RttyError as exc:
            return error(request.id, exc.code, str(exc))
        return ok(request.id, waiting=waiting)

    async def _rtty_play(self, tx: _Transmission, signal: RttyTransmitter) -> None:
        """Keys the signal out while the lease lives; tells the page how it goes."""
        audio = self.tx_audio
        if audio is None:
            return

        def feed(pcm: Pcm) -> bool:
            return self._tx is tx and audio.feed_pcm(tx.session, pcm)

        def alive() -> bool:
            return self._tx is tx and audio.active

        def on_sent(sent: int, waiting: int) -> None:
            if not tx.session.closed:
                tx.session.send(event("rtty_sent", lease=tx.token, sent=sent, waiting=waiting))

        try:
            done = await key(
                signal,
                feed,
                alive=alive,
                on_sent=on_sent,
                monotonic=self.ft8_monotonic,
                sleep=self.ft8_sleep,
            )
        finally:
            if self._rtty is not None and self._rtty[1] is signal:
                self._rtty = None
        if self._tx is not tx:
            return  # the lease ended meanwhile, the queue with it; the page was told
        if done:
            if not tx.session.closed:
                tx.session.send(event("rtty_done", lease=tx.token, sent=signal.sent))
        else:
            # The playback broke while the lease lives: the radio would key
            # without sound until the hard limit.
            logger.error("rtty_playback_failed", extra=kv(source=tx.session.context.source))
            await self._end_tx(tx, notify_reason="rtty_playback_failed")

    # Band limits (D54): a browser transmits only inside the amateur bands of
    # the ITU region of its account's locator (regional allocations only).

    def _region(self, session: Session) -> int | None:
        account = session.context.account
        stored = self.accounts.get(account) if self.accounts and account else None
        if stored is None:
            return None
        try:
            return region_of(stored.locator)
        except ValueError:
            return None

    def _band_refusal(self, session: Session, low_hz: float, high_hz: float) -> str | None:
        region = self._region(session)
        if region is None:
            return "the account has no QTH locator, so its ITU region is unknown"
        if low_hz > 0 and in_band(region, low_hz, high_hz):
            return None
        span = (
            f"{low_hz / 1e6:.4f} MHz"
            if low_hz == high_hz
            else f"{low_hz / 1e6:.4f}-{high_hz / 1e6:.4f} MHz"
        )
        return f"the signal ({span}) is outside the amateur bands of ITU Region {region}"

    def _bandplan(self, session: Session, request: Request) -> Message:
        region = self._region(session)
        if region is None:
            return ok(request.id, region=None, bands=[], segments=[])
        return ok(
            request.id,
            region=region,
            bands=[[low, high] for low, high in BANDS[region]],
            segments=[
                {"low": s.low_hz, "high": s.high_hz, "kind": s.kind, "note": s.note}
                for s in PLANS[region]
            ],
        )

    def _owned(self, session: Session, request: Request) -> _Transmission | None:
        tx = self._tx
        if tx is None or tx.session is not session or request.fields.get("lease") != tx.token:
            return None
        return tx

    async def _end_tx(self, tx: _Transmission, *, notify_reason: str | None) -> None:
        """Release a transmission at the guard; optionally tell the owner why."""
        if self._tx is not tx:
            return
        # The guard still holds PTT until tx_release below: the tail of the last
        # word may be played (only here; every other end throws it away). Only
        # for a plain release: a server-side end with a reason (a silent
        # client, a failed card, shutdown) may race the guard's own expiry,
        # and then the tail would come after PTT (review 2026-11-21).
        self._ending_own_lease = notify_reason is None
        try:
            self._tx = None
        finally:
            self._ending_own_lease = False
        # If the connection is gone, the guard has already ended the transmission.
        with contextlib.suppress(GuardUnavailableError):
            await self.guard.request("tx_release", lease=tx.guard_lease)
        if notify_reason is not None and not tx.session.closed:
            tx.session.send(event("tx_ended", lease=tx.token, reason=notify_reason))
        logger.info("tx_released", extra=kv(source=tx.session.context.source, reason=notify_reason))

    async def _watch_silent_clients(self) -> None:
        timeout = self._config.safety.lease_timeout_s
        while True:
            await asyncio.sleep(SILENCE_CHECK_INTERVAL_S)
            tx = self._tx
            if tx is not None and self._clock() - tx.last_client_renew >= timeout:
                logger.warning("client_silent", extra=kv(source=tx.session.context.source))
                await self._end_tx(tx, notify_reason="client_silent")

    async def _meters_and_tuner(self, session: Session, request: Request) -> Message:
        if request.type == "get_power_meter":
            return await self._get_power_meter(request)
        if request.type == "set_rf_power":
            return await self._set_rf_power(request)
        if request.type in ("get_front_end", "set_front_end"):
            return await self._front_end(request)
        if request.type in ("get_agc", "set_agc"):
            return await self._agc(request)
        if request.type in ("get_nb", "set_nb"):
            return await self._nb(request)
        if request.type in ("get_vfo", "set_vfo", "set_split"):
            return await self._vfo(request)
        if request.type in ("get_rf_gain", "set_rf_gain"):
            return await self._rf_gain(request)
        if request.type == "get_s_meter":
            meter = await self.cat.get_s_meter()
            self._s_meter_state(meter)
            return ok(request.id, s_meter=self._s_meter_fields(meter))
        if request.type == "get_alc":
            alc = await self.cat.get_alc()
            return ok(request.id, alc={"level": alc, "display": level_to_display(alc)})
        if request.type == "get_af":
            return ok(request.id, level=await self.cat.get_af())
        if request.type == "set_af":
            level = request.fields.get("level")
            if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 255:
                return error(request.id, INVALID_VALUE, "'level' must be an integer 0-255")
            await self.cat.set_af(level)
            return ok(request.id, level=await self.cat.get_af())
        return await self._atu(session, request)

    async def _get_power_meter(self, request: Request) -> Message:
        """Power meter, power setting and SWR meter, with their raw replies."""
        value, raw = await self.cat.get_power_meter()
        setting, setting_raw = await self.cat.get_rf_power()
        swr, swr_raw = await self.cat.get_swr_meter()
        watts, above = meter_to_watts(self._config.radio.power_meter_watts, value)
        return ok(
            request.id,
            value=value,
            watts=None if watts is None else round(watts, 1),
            above=above,
            raw=raw,
            setting=setting,
            setting_watts=round(setting_to_watts(setting), 1),
            setting_raw=setting_raw,
            swr_level=swr,
            swr_raw=swr_raw,
        )

    async def _set_rf_power(self, request: Request) -> Message:
        """Set the RF power (14 0A) within safety.max_rf_power_w, then read it back (D44).

        The guard passes the frame without reading the value (rule 3) and refuses
        it during a transmission; the limit is checked here. How the G90 decodes
        a written level is UNVERIFIED, so the setting is read back: on a
        mismatch the request fails, and a setting above the limit is lowered to
        the minimum at once.
        """
        limit = self._config.safety.max_rf_power_w
        if limit is None:
            return error(
                request.id,
                RF_POWER_DISABLED,
                "setting the power needs safety.max_rf_power_w in the configuration",
            )
        watts = request.fields.get("watts")
        if (
            isinstance(watts, bool)
            or not isinstance(watts, int | float)
            or not math.isfinite(watts)
            or not float(watts).is_integer()
            or not SETTING_MIN_W <= watts <= limit
        ):
            return error(
                request.id,
                INVALID_VALUE,
                f"'watts' must be whole watts from {SETTING_MIN_W:g} to {limit:g}",
            )
        level = watts_to_setting(int(watts))
        await self.cat.set_rf_power(level)
        actual, _ = await self.cat.get_rf_power()
        # The radio reports another level than written (54 -> 53): compare watts.
        if setting_whole_watts(actual) != int(watts):
            logger.warning("rf_power_mismatch", extra=kv(requested=level, read=actual))
            if setting_to_watts(actual) > limit:
                await self.cat.set_rf_power(0)
                actual, _ = await self.cat.get_rf_power()
                logger.warning("rf_power_lowered", extra=kv(read=actual))
            self._rf_power_setting(actual)
            return error(
                request.id,
                RF_POWER_MISMATCH,
                f"the radio reports level {actual} after level {level} was set",
            )
        logger.info("rf_power_set", extra=kv(level=level, watts=watts))
        self._rf_power_setting(actual)
        return ok(request.id, setting=actual, setting_watts=round(setting_to_watts(actual), 1))

    def _rf_power_setting(self, level: int) -> None:
        if level != self.rf_power_level:
            self.rf_power_level = level
            self._broadcast(event("radio_power", **self._power_fields()))

    async def _atu(self, session: Session, request: Request) -> Message:
        """Antenna tuner: state, on/off, and tuning through the guard (D43)."""
        if request.type == "get_atu":
            on = await self.cat.get_atu()
            self._atu_state(on)
            return ok(request.id, on=on)
        if request.type == "set_atu":
            wanted = request.fields.get("on")
            if not isinstance(wanted, bool):
                return error(request.id, INVALID_VALUE, "'on' must be a boolean")
            # 1C 01 00/01 TOGGLES the tuner on the G90 (seen 2026-11-08: "off"
            # switched it on, "on" switched it off), like the attenuator: send
            # it only when the state differs, then read back.
            current = await self.cat.get_atu()
            if current != wanted:
                await self.cat.set_atu(wanted)
                current = await self.cat.get_atu()
            self._atu_state(current)
            if current != wanted:
                return error(
                    request.id,
                    ATU_MISMATCH,
                    f"the radio reports the tuner {'on' if current else 'off'}",
                )
            return ok(request.id)
        # A tuning transmits a carrier the guard cannot stop; the guard decides.
        if self._tx is not None or self._tx_starting:
            return error(request.id, TX_BUSY, "a transmission is already active")
        # Nothing may retune the radio between the band check and the tuning
        # (code review 2026-11-10, point 2): the tuning cannot be stopped once
        # started, so there is no check "afterwards" as for a lease.
        self._tx_starting = True
        try:
            # The G90 tunes on the selected (receive) VFO, also in split (measured
            # 2026-11-08), so that frequency goes to the TX log, not _tx_target's.
            try:
                frequency: int | None = await self.cat.get_frequency()
            except (CatError, GuardUnavailableError) as exc:
                logger.warning("tx_frequency_unknown", extra=kv(error=exc))
                frequency = None
            if session.context.kind == "web":
                # The tuning carrier must be inside the band too (D54); no
                # frequency, no tuning.
                where = frequency if frequency is not None else -1
                refusal = self._band_refusal(session, where, where)
                if refusal is not None:
                    return error(request.id, OUT_OF_BAND, refusal)
            response = await self.guard.request(
                "atu_tune", source=session.context.source, frequency_hz=frequency
            )
            if response.get("ok"):
                self._tuned_at = time.monotonic()
        finally:
            self._tx_starting = False
        logger.warning(
            "atu_tune",
            extra=kv(source=session.context.source, frequency_hz=frequency, ok=response.get("ok")),
        )
        return self._passthrough(request, response)

    async def _front_end(self, request: Request) -> Message:
        """Preamplifier / attenuator (16 02, 11): one state, set and read back.

        The guard refuses the setting during a transmission (category B).
        """
        if request.type == "set_front_end":
            wanted = request.fields.get("state")
            if wanted not in FRONT_END_STATES:
                names = ", ".join(FRONT_END_STATES)
                return error(request.id, INVALID_VALUE, f"'state' must be one of {names}")
            # Each switch-on switches the other one off in the radio, and 11
            # toggles the attenuator whatever its data (measured): it is sent
            # only when the attenuator has to change.
            current = await self.cat.get_front_end()
            if wanted == "pre" and current != "pre":
                await self.cat.set_preamp(True)
            elif (wanted == "att") != (current == "att"):
                await self.cat.toggle_attenuator()
            elif wanted == "off" and current == "pre":
                await self.cat.set_preamp(False)
            actual = await self.cat.get_front_end()
            self._front_end_state(actual)
            if actual != wanted:
                logger.warning("front_end_mismatch", extra=kv(requested=wanted, read=actual))
                return error(
                    request.id,
                    FRONT_END_MISMATCH,
                    f"the radio reports {actual} after {wanted} was set",
                )
            return ok(request.id, front_end=actual)
        state = await self.cat.get_front_end()
        self._front_end_state(state)
        return ok(request.id, front_end=state)

    async def _agc(self, request: Request) -> Message:
        """AGC (16 12): set and read back; the guard refuses setting it during TX."""
        if request.type == "set_agc":
            wanted = request.fields.get("agc")
            if wanted not in AGC_MODES.values():
                names = ", ".join(AGC_MODES.values())
                return error(request.id, INVALID_VALUE, f"'agc' must be one of {names}")
            await self.cat.set_agc(str(wanted))
        agc = await self.cat.get_agc()
        self._agc_state(agc)
        return ok(request.id, agc=agc)

    async def _nb(self, request: Request) -> Message:
        """Noise blanker (16 22) and its level (14 12, menu steps 0-10), read back.

        ``set_nb`` takes ``on``, ``level`` or both; the guard refuses it during TX.
        """
        if request.type == "set_nb":
            on, level = request.fields.get("on"), request.fields.get("level")
            if on is None and level is None:
                return error(request.id, INVALID_VALUE, "'on' or 'level' is needed")
            if on is not None and not isinstance(on, bool):
                return error(request.id, INVALID_VALUE, "'on' must be a boolean")
            if level is not None and (
                isinstance(level, bool)
                or not isinstance(level, int)
                or not 0 <= level <= NB_LEVEL_STEPS
            ):
                return error(
                    request.id, INVALID_VALUE, f"'level' must be an integer 0-{NB_LEVEL_STEPS}"
                )
            if level is not None:
                await self.cat.set_nb_level(level)
            if on is not None:
                await self.cat.set_nb(on)
            actual = await self.cat.get_nb()
            self._nb_state(actual)
            if (on is not None and actual[0] != on) or (level is not None and actual[1] != level):
                logger.warning("nb_mismatch", extra=kv(on=on, level=level, read=actual))
                return error(
                    request.id,
                    NB_MISMATCH,
                    f"the radio reports on={actual[0]} level={actual[1]} after the setting",
                )
            return ok(request.id, nb_on=actual[0], nb_level=actual[1])
        actual = await self.cat.get_nb()
        self._nb_state(actual)
        return ok(request.id, nb_on=actual[0], nb_level=actual[1])

    async def _rf_gain(self, request: Request) -> Message:
        """RF gain (14 02), 1-100 %, set and read back; the guard refuses it during TX."""
        if request.type == "set_rf_gain":
            percent = request.fields.get("percent")
            if (
                isinstance(percent, bool)
                or not isinstance(percent, int)
                or not RF_GAIN_MIN <= percent <= RF_GAIN_MAX
            ):
                return error(
                    request.id,
                    INVALID_VALUE,
                    f"'percent' must be an integer {RF_GAIN_MIN}-{RF_GAIN_MAX}",
                )
            await self.cat.set_rf_gain(percent)
            actual = await self.cat.get_rf_gain()
            self._rf_gain_state(actual)
            if actual != percent:
                logger.warning("rf_gain_mismatch", extra=kv(requested=percent, read=actual))
                return error(
                    request.id,
                    RF_GAIN_MISMATCH,
                    f"the radio reports {actual} % after {percent} % was set",
                )
            return ok(request.id, rf_gain=actual)
        actual = await self.cat.get_rf_gain()
        self._rf_gain_state(actual)
        return ok(request.id, rf_gain=actual)

    def _s_meter_fields(self, level: int) -> dict[str, object]:
        db = level_to_db(level)
        return {"level": level, "db": round(db, 1), "text": db_to_text(db)}

    def _s_meter_state(self, level: int) -> None:
        if level != self.s_meter:
            self.s_meter = level
            self._broadcast(event("radio_s_meter", **self._s_meter_fields(level)))

    def _muted_s_meter(self, level: int | None, now: float) -> None:
        """Yellow bar: S1 or more, then 242 for MUTED_S_METER_S. ``None``: not read
        (our lease or output on the power meter, which the red bar covers)."""
        suspected = self.tx_suspected
        if level is None:
            self._s_meter_heard, self._s_meter_muted_at, suspected = False, None, False
        elif level >= BELOW_SCALE_FROM:
            if self._s_meter_heard:
                self._s_meter_heard, self._s_meter_muted_at = False, now
            muted_at = self._s_meter_muted_at
            if muted_at is not None and now - muted_at >= MUTED_S_METER_S:
                suspected = True
        else:
            self._s_meter_heard = level >= MUTED_S_METER_FROM
            self._s_meter_muted_at, suspected = None, False
        if suspected != self.tx_suspected:
            self.tx_suspected = suspected
            self._broadcast(event("radio_tx_suspected", active=suspected))
            if suspected:
                logger.warning("tx_suspected", extra=kv(hz=self.radio_hz, mode=self.radio_mode))

    def _alc_state(self, level: int) -> None:
        if level != self.alc:
            self.alc = level
            self._broadcast(event("radio_alc", level=level, display=level_to_display(level)))

    def _vfo_fields(self) -> dict[str, object]:
        return {"vfo": self.vfo, "other_hz": self.other_hz, "split": self.split}

    def _vfo_state(
        self, *, vfo: str | None = None, other_hz: int | None = None, split: bool | None = None
    ) -> None:
        changed = False
        for name, value in (("vfo", vfo), ("other_hz", other_hz), ("split", split)):
            if value is not None and value != getattr(self, name):
                setattr(self, name, value)
                changed = True
        if changed:
            self._broadcast(event("radio_vfo", **self._vfo_fields()))

    async def _vfo(self, request: Request) -> Message:
        """VFO A/B and split. USB/LSB become U-D/L-D on the VFO that takes over
        (radio.ssb_data_mode; owner's decision 2026-11-08), so the browser's
        voice reaches the DE-19 on either VFO."""
        if request.type == "set_vfo":
            wanted = request.fields.get("vfo")
            if wanted not in ("A", "B"):
                return error(request.id, INVALID_VALUE, "'vfo' must be 'A' or 'B'")
            await self.cat.select_vfo(str(wanted))
            await self._ssb_as_data()
        elif request.type == "set_split":
            on = request.fields.get("on")
            if not isinstance(on, bool):
                return error(request.id, INVALID_VALUE, "'on' must be a boolean")
            if on:
                await self._other_vfo_ssb_as_data()
            await self.cat.set_split(on)
        vfo, other_hz = await self.cat.get_vfo_frequency(unselected=True)
        split = await self.cat.get_split()
        hz = await self.cat.get_frequency()
        self._radio_state(hz=hz, mode=await self._read_mode())
        self._vfo_state(vfo=vfo, other_hz=other_hz, split=split)
        if request.type == "set_vfo" and vfo != request.fields.get("vfo"):
            return error(request.id, VFO_MISMATCH, f"the radio reports VFO {vfo}")
        if request.type == "set_split" and split != request.fields.get("on"):
            return error(
                request.id, VFO_MISMATCH, f"the radio reports split {'on' if split else 'off'}"
            )
        return ok(request.id, **self._vfo_fields())

    async def _ssb_as_data(self) -> None:
        """With radio.ssb_data_mode: the selected VFO in USB/LSB becomes U-D/L-D."""
        if not self._config.radio.ssb_data_mode:
            return
        mode, data = await self.cat.get_data_mode()
        if mode in (Mode.USB, Mode.LSB) and not data:
            await self.cat.set_data_mode(mode, True)

    async def _other_vfo_ssb_as_data(self) -> None:
        """The same for the unselected VFO: selected for a moment, then back."""
        if not self._config.radio.ssb_data_mode:
            return
        mode, data = await self.cat.get_data_mode(unselected=True)
        if mode not in (Mode.USB, Mode.LSB) or data:
            return
        vfo, _ = await self.cat.get_vfo_frequency(unselected=False)
        await self.cat.select_vfo("B" if vfo == "A" else "A")
        try:
            await self.cat.set_data_mode(mode, True)
        finally:
            await self.cat.select_vfo(vfo)

    def _rf_gain_state(self, percent: int) -> None:
        if percent != self.rf_gain:
            self.rf_gain = percent
            self._broadcast(event("radio_rf_gain", rf_gain=percent))

    def _nb_state(self, nb: tuple[bool, int]) -> None:
        if nb != self.nb:
            self.nb = nb
            self._broadcast(event("radio_nb", nb_on=nb[0], nb_level=nb[1]))

    def _agc_state(self, agc: str) -> None:
        if agc != self.agc:
            self.agc = agc
            self._broadcast(event("radio_agc", agc=agc))

    def _front_end_state(self, state: str) -> None:
        if state != self.front_end:
            self.front_end = state
            self._broadcast(event("radio_front_end", front_end=state))

    def _atu_state(self, on: bool) -> None:
        if on != self.atu_on:
            self.atu_on = on
            self._broadcast(event("radio_atu", on=on))

    async def _tx_target(self) -> tuple[int, str, bool]:
        """Where and in which mode the radio would transmit now, and whether split is on.

        In split the G90 transmits on the unselected VFO, in that VFO's mode
        (measured 2026-11-08); split is read fresh, never taken from the cache.
        """
        split = await self.cat.get_split()
        self._vfo_state(split=split)
        if split:
            vfo, hz = await self.cat.get_vfo_frequency(unselected=True)
            self._vfo_state(vfo=vfo, other_hz=hz)
            tx_mode, data = await self.cat.get_data_mode(unselected=True)
            return hz, display_mode(tx_mode, data), True
        hz = await self.cat.get_frequency()
        name = await self._read_mode()
        self._radio_state(hz=hz, mode=name)
        return hz, name, False

    async def _frequency_for_log(self) -> int | None:
        """The transmit frequency for the TX log: the other VFO in split (rule 7)."""
        try:
            hz, _, _ = await self._tx_target()
            return hz
        except (CatError, GuardUnavailableError) as exc:
            logger.warning("tx_frequency_unknown", extra=kv(error=exc))
            return None

    # Guard events

    def _on_guard_event(self, message: dict[str, Any]) -> None:
        match message.get("event"):
            case "civ_rx":
                self.cat.on_civ_rx(message.get("frame"))
            case "tx_ended":
                tx = self._tx
                if tx is not None and message.get("lease") == tx.guard_lease:
                    self._tx = None
                    if not tx.session.closed:
                        tx.session.send(
                            event("tx_ended", lease=tx.token, reason=message.get("reason"))
                        )
            case "port_lost" | "line_fault" | "atu_tune_overrun" as name:
                self._broadcast(event(name, message=message.get("message")))
            case "atu_tune_end":
                # The page warns when the radio did not tune (owner, 2026-11-14).
                self._broadcast(
                    event(
                        "atu_tune_end",
                        reason=message.get("reason"),
                        duration_s=message.get("duration_s"),
                        emitted=message.get("emitted") is True,
                    )
                )
            case other:
                logger.warning("guard_unknown_event", extra=kv(event=other))

    async def _mute_speaker(self) -> None:
        """Set the speaker volume to 0 (radio.mute_speaker); a failure is only logged."""
        try:
            await self.cat.set_af(0)
            logger.info("speaker_muted")
        except (CatError, GuardUnavailableError) as exc:
            logger.warning("speaker_mute_failed", extra=kv(error=exc))

    async def _watch_speaker(self) -> None:
        """Every SPEAKER_CHECK_S: if the speaker is not muted, mute it (radio.mute_speaker).

        Not while a transmission or a tuning is starting or going on: the guard
        refuses every change of the radio then, and nothing is gained by trying.
        """
        failing = False
        while True:
            await asyncio.sleep(SPEAKER_CHECK_S)
            if not self.guard.connected or self._tx is not None or self._tx_starting:
                continue
            if self.rf_active:
                continue
            tuned = self._tuned_at
            if tuned is not None and time.monotonic() - tuned < TUNING_HOLD_S:
                continue
            try:
                level = await self.cat.get_af()
                if level != 0:
                    logger.warning("speaker_unmuted", extra=kv(level=level))
                    await self.cat.set_af(0)
                    logger.info("speaker_muted")
            except (CatError, GuardUnavailableError) as exc:
                if not failing:  # one line per outage, not one every 30 s
                    logger.warning("speaker_check_failed", extra=kv(error=exc))
                failing = True
            else:
                failing = False

    def _on_guard_connection(self, connected: bool) -> None:
        if connected:
            self._broadcast(event("guard_connected"))
            if self._config.radio.mute_speaker:
                self._mute_task = asyncio.get_running_loop().create_task(self._mute_speaker())
            return
        tx, self._tx = self._tx, None
        if tx is not None and not tx.session.closed:
            tx.session.send(event("tx_ended", lease=tx.token, reason="guard_disconnected"))
        self._broadcast(event("guard_disconnected"))

    async def _tune(self, hz: int) -> None:
        """Set the frequency; a burst of requests sends the newest value, not each one.

        Dragging across the waterfall produces many requests. Only one CAT
        command is in flight; requests arriving meanwhile replace the waiting
        target, and the last one always reaches the radio.
        """
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        waiters = self._tune_pending[1] if self._tune_pending is not None else []
        self._tune_pending = (hz, [*waiters, done])
        async with self._tune_lock:
            batch, self._tune_pending = self._tune_pending, None
            if batch is not None:  # else an earlier caller already sent our batch
                target, batch_waiters = batch
                try:
                    self.tune_commands += 1
                    await self.cat.set_frequency(target)
                    self._radio_state(hz=target)
                except Exception as exc:  # every caller of the batch gets the outcome
                    for waiter in batch_waiters:
                        waiter.set_exception(exc)
                else:
                    for waiter in batch_waiters:
                        waiter.set_result(None)
        await done

    # Transmissions seen on the radio's power meter (decisions.md D33): informational

    async def _watch_power(self) -> None:
        while True:
            await asyncio.sleep(self.power_interval_s)
            if not self.guard.connected:
                continue
            try:
                value, _ = await self.cat.get_power_meter()
                setting, _ = await self.cat.get_rf_power()
                swr, _ = await self.cat.get_swr_meter()
            except (CatError, GuardUnavailableError):
                continue
            self._power_reading(value, setting=setting, swr=swr)

    def _power_reading(
        self,
        value: int,
        now: float | None = None,
        setting: int | None = None,
        swr: int | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        changed = (
            value != self.power_value
            or (setting is not None and setting != self.rf_power_level)
            or (swr is not None and swr != self.swr_level)
        )
        ended = self._tx_ended_at
        lease_tail = self._tx is None and ended is not None and 0 <= now - ended < LEASE_TAIL_S
        if value > 0 and not lease_tail:
            self._rf_seen_at = now
        active = self._rf_seen_at is not None and now - self._rf_seen_at < self.power_hold_s
        swr_high = self._swr_is_high(swr, emitting=value > 0)
        if changed or swr_high != self.swr_high:
            self.power_value = value
            if setting is not None:
                self.rf_power_level = setting
            if swr is not None:
                self.swr_level = swr
            if swr_high and not self.swr_high:
                logger.warning("high_swr", extra=kv(hz=self.radio_hz, swr_level=swr))
            self.swr_high = swr_high
            self._broadcast(event("radio_power", **self._power_fields()))
        if active == self.rf_active:
            return
        self.rf_active = active
        leased = self._tx is not None
        self._broadcast(event("radio_transmitting", active=active or leased, leased=leased))
        if active and not leased:
            # Microphone PTT, TUNE or VOX: the guard did not key this (D26, D33).
            self.warnings_without_lease += 1
            logger.warning(
                "tx_without_lease", extra=kv(hz=self.radio_hz, mode=self.radio_mode, meter=value)
            )
        else:
            logger.info("radio_transmitting", extra=kv(active=active, leased=leased))

    def _swr_is_high(self, swr: int | None, *, emitting: bool) -> bool:
        """SWR at the end of the meter's scale for SWR_HIGH_READINGS readings in a row."""
        if swr is None:
            return self.swr_high
        if emitting and swr >= SWR_HIGH_LEVEL:
            self._swr_high_count += 1
        elif emitting:
            self._swr_high_count = 0
        else:
            # No output: SWR is not measured. The warning stays until the next
            # transmission shows a lower SWR.
            return self.swr_high
        return self._swr_high_count >= SWR_HIGH_READINGS

    def _power_fields(self) -> dict[str, object]:
        """Meter level, its watts (and whether above the calibrated range), setting level."""
        watts: float | None = None
        above = False
        if self.power_value is not None:
            watts, above = meter_to_watts(self._config.radio.power_meter_watts, self.power_value)
        return {
            "value": self.power_value,
            "watts": None if watts is None else round(watts, 1),
            "above": above,
            "setting": self.rf_power_level,
            "setting_watts": (
                None
                if self.rf_power_level is None
                else round(setting_to_watts(self.rf_power_level), 1)
            ),
            "swr_level": self.swr_level,
            "swr_high": self.swr_high,
        }

    @property
    def state_dir(self) -> Path:
        """The server's state directory (accounts, settings, the log)."""
        return self._config.server.state_dir

    def _attach_waterfall(self) -> WaterfallHub | None:
        """Lines from the I/Q input to the browsers; computed only while one watches."""
        if self.iq is None:
            return None
        hub = WaterfallHub(self._waterfall_center, self.iq.config.sample_rate)
        self.iq.on_line = self._on_iq_line
        self.iq.wanted = hub.wanted
        return hub

    # Waterfall lines taken while the radio transmits are dimmed (D33)

    def _on_iq_line(self, line: Spectrum) -> None:
        if self._tx is not None or self.rf_active:
            self._blank_left = BLANK_AFTER_TX_LINES  # the TX->RX click comes after
            blanked = True
        else:
            blanked = self._blank_left > 0
            self._blank_left = max(0, self._blank_left - 1)
        if self.waterfall is not None:
            self.waterfall.on_line(line, blanked)

    # Radio state (frequency and mode) for the waterfall scale

    def _radio_state(self, *, hz: int | None = None, mode: str | None = None) -> None:
        if hz is not None and hz != self.radio_hz:
            # Another band can be silent (S0 = 242 at once, seen 2026-11-08 on
            # 160 m): the muted S-meter check starts over on the new frequency.
            self._s_meter_heard, self._s_meter_muted_at = False, None
            if self.tx_suspected:
                self.tx_suspected = False
                self._broadcast(event("radio_tx_suspected", active=False))
        changed = (hz is not None and hz != self.radio_hz) or (
            mode is not None and mode != self.radio_mode
        )
        if hz is not None:
            self.radio_hz = hz
        if mode is not None:
            self.radio_mode = mode
        if changed:
            self._broadcast_radio_state()

    def _broadcast_radio_state(self) -> None:
        self._broadcast(
            event(
                "radio_state",
                hz=self.radio_hz,
                mode=self.radio_mode,
                passband_hz=self.passband(),
            )
        )

    def passband(self) -> list[float] | None:
        """Configured receive passband of the current mode, [low, high] Hz around the dial.

        From [iq.passband_hz]: the G90 neither reports nor accepts its filter
        over CAT (decisions.md D37).
        """
        mode = base_mode(self.radio_mode)
        if mode is None:
            return None
        table = self._config.iq.passband_hz if self._config.iq is not None else {}
        edges = table.get(mode)
        return list(edges) if edges is not None else None

    def _waterfall_center(self) -> tuple[float | None, bool]:
        """Centre frequency of the I/Q spectrum and whether it is calibrated."""
        if self.radio_hz is None or self.iq is None:
            return None, False
        offsets = self.iq.config.center_offset_hz
        mode = base_mode(self.radio_mode)
        if mode is None or mode not in offsets:
            return float(self.radio_hz), False
        return self.radio_hz + offsets[mode], True

    async def _follow_radio(self) -> None:
        """Read frequency and mode, and the settings (tuner, preamp/attenuator, AGC, NB,
        RF gain)
        every SETTINGS_EVERY cycles, while a browser is connected.

        Also with the waterfall switched off in the browser (to save the link):
        the frequency display still follows the dial.
        """
        cycle = -1
        while True:
            await asyncio.sleep(FOLLOW_INTERVAL_S)
            if not self._browser_connected():
                continue
            if not self.guard.connected:
                continue
            with contextlib.suppress(CatError, GuardUnavailableError):
                hz = await self.cat.get_frequency()
                mode = await self._read_mode()
                self._radio_state(hz=hz, mode=mode)
                vfo, other_hz = await self.cat.get_vfo_frequency(unselected=True)
                self._vfo_state(vfo=vfo, other_hz=other_hz)
                # In receive only: during a transmission the S-meter means nothing.
                # The ALC only in transmit (ours, or microphone PTT seen on the power meter).
                if self._tx is None and not self.rf_active:
                    meter = await self.cat.get_s_meter()
                    self._s_meter_state(meter)
                    self._muted_s_meter(meter, time.monotonic())
                    self.alc = None  # sent again in the next transmission
                else:
                    self.s_meter = None  # sent again after the transmission
                    self._muted_s_meter(None, time.monotonic())
                    self._alc_state(await self.cat.get_alc())
                # Settings change rarely: every other cycle, to keep the bus free.
                cycle += 1
                if cycle % SETTINGS_EVERY == 0:
                    self._atu_state(await self.cat.get_atu())
                    self._front_end_state(await self.cat.get_front_end())
                    self._agc_state(await self.cat.get_agc())
                    self._nb_state(await self.cat.get_nb())
                    self._rf_gain_state(await self.cat.get_rf_gain())
                    self._vfo_state(split=await self.cat.get_split())

    async def _read_mode(self) -> str:
        """The mode with its data flag (26 00); 0x04 alone cannot see U-D/L-D."""
        try:
            mode, data = await self.cat.get_data_mode()
        except CatError:
            return mode_name(await self.cat.get_mode())  # radio without 26 00
        return display_mode(mode, data)

    async def _set_mode(self, request: Request, wanted: object) -> Message:
        """Set a mode; USB/LSB become U-D/L-D with radio.ssb_data_mode (audio to the DE-19)."""
        name = wanted.upper() if isinstance(wanted, str) else None
        mode = DATA_MODE_NAMES.get(name) if name in DATA_MODE_NAMES else parse_mode(wanted)
        if mode is None:
            names = ", ".join([*(mode_name(m) for m in Mode), *DATA_MODE_NAMES])
            return error(request.id, INVALID_VALUE, f"'mode' must be one of {names}")
        if name in DATA_MODE_NAMES:
            data: bool | None = True
        elif mode in (Mode.USB, Mode.LSB):
            data = self._config.radio.ssb_data_mode
        else:
            data = None
        if data is None:
            await self.cat.set_mode(mode)
            self._radio_state(mode=mode_name(mode))
        else:
            await self.cat.set_data_mode(mode, data)
            self._radio_state(mode=display_mode(mode, data))
        return ok(request.id)

    def _browser_connected(self) -> bool:
        if self.waterfall is not None and self.waterfall.subscriber_count:
            return True
        return any(session.context.kind == "web" for session in self._sessions)

    def _broadcast(self, message: Message) -> None:
        for session in list(self._sessions):
            session.send(message)

    @staticmethod
    def _passthrough(request: Request, response: dict[str, Any]) -> Message:
        """Relay a guard response, keeping only client-relevant fields."""
        if response.get("ok"):
            fields = {k: v for k, v in response.items() if k not in ("v", "id", "ok", "lease")}
            return ok(request.id, **fields)
        return error(request.id, str(response.get("error")), str(response.get("message")))
