# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard logic: serial port ownership, CI-V forwarding and the transmit lease.

:class:`GuardCore` is free of sockets and threads. The serving loop
(:mod:`remoteg90ctl.guard.main`) feeds it requests and calls :meth:`poll` on
every iteration; tests drive it directly with a controllable clock.

Safety rules implemented here (AGENTS.md section 2, decisions.md D24):

* PTT is asserted only after the transmission has been durably recorded (run
  marker and TX log) and only on an open port whose lines were confirmed
  inactive and whose radio identity was confirmed (D32); the asserted state is
  read back and confirmed.
* The lease ends on release, missing renewal, hard limit, client disconnect,
  port loss, line mismatch, shutdown or error — always lowering the lines first.
* Hitting the hard limit persists a lockout *before* PTT is released.
* The duty cycle (D31) limits the total transmission time in a sliding window;
  the history survives restarts, and doubt means a full window.
* No safety path waits for the server.
"""

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from remoteg90ctl import __version__
from remoteg90ctl.civ import (
    BROADCAST_ADDRESS,
    POWER_METER_SUBCOMMAND,
    Command,
    Frame,
    FrameSplitter,
    Garbage,
    atu_tune_request,
    read_xiegu_id_request,
)
from remoteg90ctl.config import (
    MAX_HARD_LIMIT_S,
    MAX_LEASE_RENEW_INTERVAL_S,
    MAX_LEASE_TIMEOUT_S,
    Backend,
    Config,
    PttLine,
)
from remoteg90ctl.guard.framecheck import FrameRejectedError, check_frame
from remoteg90ctl.guard.protocol import ErrorCode, Request, error, event, ok
from remoteg90ctl.guard.state import (
    ATU_TUNE_OVERRUN,
    HARD_LIMIT,
    UNCLEAN_SHUTDOWN_DURING_TX,
    HistoryUnreadableError,
    Lockout,
    LockoutStore,
    RunMarker,
    TxHistory,
)
from remoteg90ctl.guard.txlog import TxLog
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.serial_port import INACTIVE, LineState, PortLostError, SerialPort

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
PortFactory = Callable[[], SerialPort]
Message = dict[str, object]

MAX_SOURCE_LENGTH = 200
# How long the guard waits for the answer to its identity query (D32). The wait
# happens while opening the port, never during a transmission.
IDENTITY_TIMEOUT_S = 1.0
_IDENTITY_POLL_S = 0.01
# Stored history ending later than "now" by more than this means the wall clock
# went back since it was written (D31); the window is then treated as full.
_CLOCK_TOLERANCE_S = 5.0

# Antenna tuning (decisions.md D43): the only transmission the guard cannot end.
# The G90 stops tuning by itself: 1-1.5 s with a matched antenna, about 9 s
# with a poor one (measured 2026-11-13, section 8 item 13); the longest time
# is not known. So a tuning lasts until the radio's power meter reads zero:
# the guard reads every power meter reply that passes through it (the server
# asks every second; the guard sends nothing itself) and ends the tuning at
# ATU_TUNE_ZERO_READINGS zero readings in a row, not before ATU_TUNE_MIN_S.
# Until then no other transmission starts and nothing changes the radio.
# Without readings (no server, no port) it lasts until ATU_TUNE_MAX_S, and a
# tuning still emitting then is a fault: transmit is locked until tx_unlock.
ATU_TUNE_MIN_S = 3.0
ATU_TUNE_MAX_S = 30.0
ATU_TUNE_ZERO_READINGS = 2
# The next tuning at least this long after the end of the previous one.
ATU_TUNE_INTERVAL_S = 10.0

Interval = tuple[float, float]


class PortState(StrEnum):
    OPEN = "open"
    UNAVAILABLE = "unavailable"
    LOST = "lost"
    # Lines became active while nobody was transmitting (e.g. another process
    # opened the device). The port stays open so the guard can keep the lines
    # low: closing would not drop them while another descriptor is open.
    LINE_FAULT = "line_fault"


class IdentityState(StrEnum):
    """Result of the radio identity check (decisions.md D32)."""

    NOT_CHECKED = "not_checked"
    CONFIRMED = "confirmed"
    MISMATCH = "mismatch"
    REJECTED = "rejected"
    NO_REPLY = "no_reply"
    NOT_CONFIGURED = "not_configured"


# tx_end reason written after a restart for a transmission the previous run left open.
UNCLEAN_SHUTDOWN = "unclean_shutdown"


class EndReason(StrEnum):
    RELEASED = "released"
    LEASE_EXPIRED = "lease_expired"
    HARD_LIMIT = "hard_limit"
    CLIENT_DISCONNECTED = "client_disconnected"
    PORT_LOST = "port_lost"
    LINE_MISMATCH = "line_mismatch"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    DUTY_LIMIT = "duty_limit"


@dataclass(slots=True)
class Tuning:
    """An antenna tuning in progress (D43)."""

    source: str
    frequency_hz: int | None
    started_at: float
    started_ts: str
    zeros: int = 0  # zero power meter readings in a row
    emitted: bool = False  # a reading above zero was seen


@dataclass(slots=True)
class Lease:
    id: str
    source: str
    frequency_hz: int | None
    started_at: float
    started_ts: str
    last_renew: float


class GuardCore:
    def __init__(
        self,
        config: Config,
        port_factory: PortFactory,
        *,
        clock: Clock = time.monotonic,
        txlog: TxLog | None = None,
        identity_timeout_s: float = IDENTITY_TIMEOUT_S,
    ) -> None:
        safety = config.safety
        # Defence in depth: config validation already enforces these (AGENTS.md rule 4).
        if not (
            0 < safety.lease_renew_interval_s <= MAX_LEASE_RENEW_INTERVAL_S
            and 0 < safety.lease_timeout_s <= MAX_LEASE_TIMEOUT_S
            and 0 < safety.hard_limit_s <= MAX_HARD_LIMIT_S
        ):
            raise ValueError("lease timing outside the safety limits")
        self._config = config
        self._radio = config.radio
        self._safety = safety
        self._port_factory = port_factory
        self._clock = clock
        self._txlog = txlog if txlog is not None else TxLog(config.guard.tx_log_path)
        self._lockouts = LockoutStore(config.guard.state_dir)
        self._marker = RunMarker(config.guard.state_dir)
        self._history_store = TxHistory(config.guard.state_dir)
        # Finished transmissions within the duty window, in this run's clock.
        self._history: list[Interval] = []
        self._history_unsaved = False  # the last save failed: no transmission until one works
        self._wall_offset = 0.0  # wall-clock seconds minus self._clock(), fixed at start
        self._duty_blocked_until: float | None = None
        self._duty_block_reason = ""
        self._port: SerialPort | None = None
        self._port_generation = 0  # counts opens: a reopened port may reuse the descriptor
        self.last_read_bytes = 0  # bytes the last port read returned (for the wake-up)
        self._port_state = PortState.UNAVAILABLE
        self._port_detail = "not opened yet"
        self._splitter = FrameSplitter()
        self._expected_echo: bytes | None = None
        self._lease: Lease | None = None
        self._lockout: Lockout | None = None
        self._stopping = False
        # A lockout that could not be written to lockout.json (D25.5).
        self._lockout_unsaved = False
        self._events: list[Message] = []
        self._identity_timeout_s = identity_timeout_s
        self._identity = IdentityState.NOT_CHECKED
        self._identity_detail = "port not open"
        self._identity_reply: bytes | None = None
        self._identity_pending = False
        self._tune: Tuning | None = None  # the antenna tuning in progress
        self._last_tune_end: float | None = None  # when the last tuning ended
        self._ptt_target = LineState(
            dtr=self._radio.ptt_line is PttLine.DTR, rts=self._radio.ptt_line is PttLine.RTS
        )

    # Lifecycle

    def start(self) -> None:
        """Check the previous run, load the lockout and open the port."""
        previous = self._marker.previous_run()
        lockout = self._lockouts.load()
        if previous.unclean and previous.tx_active and previous.lease is not None:
            # Close the record of the transmission the previous run could not end (rule 7).
            self._log_tx(
                "tx_end",
                **{
                    k: previous.lease.get(k) for k in ("lease", "source", "frequency_hz", "started")
                },
                duration_s=None,
                reason=UNCLEAN_SHUTDOWN,
                detail="guard ended without cleanup; end time unknown",
            )
        if previous.unclean:
            logger.warning(
                "unclean_previous_shutdown",
                extra=kv(tx_active=previous.tx_active, detail=previous.detail),
            )
            if lockout is None and (previous.tx_active or previous.pending_lockout):
                reason = previous.pending_lockout or UNCLEAN_SHUTDOWN_DURING_TX
                lockout = Lockout(reason, self._txlog.timestamp())
                self._lockout_unsaved = not self._persist_lockout(lockout)
        if lockout is not None:
            logger.warning(
                "tx_locked_at_start", extra=kv(reason=lockout.reason, since=lockout.since)
            )
        self._lockout = lockout
        self._load_history(previous.lease if previous.unclean and previous.tx_active else None)
        self._log_tx(
            "guard_start",
            unclean_previous_shutdown=previous.unclean,
            tx_active_at_shutdown=previous.tx_active if previous.unclean else None,
            detail=previous.detail,
            lockout=lockout.reason if lockout else None,
        )
        # If this fails, tx_request fails on the same write: no transmission possible.
        self._write_marker(tx_active=False)
        self._open_port()

    def shutdown(self, *, clean: bool) -> None:
        """End any transmission and close the port.

        After an error (``clean=False``) the run marker is left untouched, so an
        error during transmission locks TX on the next start (D24.8).
        """
        self._stopping = True
        if self._lease is not None:
            self._end_lease(EndReason.SHUTDOWN if clean else EndReason.ERROR, update_marker=clean)
        self._close_port()
        if not clean:
            return
        self._log_tx("guard_stop")
        if self._lockout_unsaved and self._lockout is not None:
            self._lockout_unsaved = not self._persist_lockout(self._lockout)
        if self._lockout_unsaved:
            # Keep the marker: it carries the lockout to the next start.
            logger.error("run_marker_kept_for_unsaved_lockout")
            return
        try:
            self._marker.remove()
        except OSError as exc:
            logger.error("run_marker_remove_failed", extra=kv(error=exc))

    def emergency_lower(self) -> None:
        """Lower the lines immediately. Safe to call from a signal handler; never raises."""
        self._stopping = True
        port = self._port
        if port is None:
            return
        try:
            port.set_lines(INACTIVE)
        except Exception:
            logger.exception("emergency_lower_failed")

    # Inputs from the serving loop

    def handle(self, request: Request) -> Message:
        """Process one request and return the response."""
        self._check_lease()
        match request.type:
            case "civ":
                return self._handle_civ(request)
            case "tx_request":
                return self._handle_tx_request(request)
            case "atu_tune":
                return self._handle_atu_tune(request)
            case "tx_renew":
                return self._handle_tx_renew(request)
            case "tx_release":
                return self._handle_tx_release(request)
            case "tx_unlock":
                return self._handle_tx_unlock(request)
            case "port_open":
                return self._handle_port_open(request)
            case "status":
                return ok(request.id, **self.status())
            case _:
                return error(request.id, ErrorCode.UNKNOWN_TYPE, f"unknown type {request.type}")

    def port_wakeup(self) -> tuple[int, int] | None:
        """(open count, descriptor) of the open port to wake the loop on, or None.

        Only to wake up sooner when the radio answers; the safety checks do not
        depend on it (the loop still runs at least every LOOP_INTERVAL_S).
        """
        if self._port is None:
            return None
        fd = self._port.fileno()
        return None if fd is None else (self._port_generation, fd)

    def poll(self) -> None:
        """One pass of the safety checks; called on every loop iteration."""
        self._read_port()
        self._check_lease()
        self._check_tune()

    def client_disconnected(self) -> None:
        self._expected_echo = None
        if self._lease is not None:
            self._end_lease(EndReason.CLIENT_DISCONNECTED)

    def take_events(self) -> list[Message]:
        events, self._events = self._events, []
        return events

    # State

    @property
    def tx_active(self) -> bool:
        return self._lease is not None

    @property
    def port_state(self) -> PortState:
        return self._port_state

    @property
    def lockout(self) -> Lockout | None:
        return self._lockout

    def blockers(self, *, tuning: bool = False) -> list[str]:
        """Reasons, other than a lockout, why a transmission cannot start now.

        ``tuning``: for an antenna tuning (D43), which needs no PTT line and has
        its own switch, ``safety.allow_atu_tune``, instead of ``allow_transmit``.
        """
        found = []
        if self._port_state is not PortState.OPEN:
            found.append(f"serial port {self._port_state}: {self._port_detail}")
        elif self._identity is not IdentityState.CONFIRMED:
            found.append(f"radio identity {self._identity}: {self._identity_detail}")
        hardware = self._radio.backend is Backend.HARDWARE
        if tuning:
            if hardware and not self._safety.allow_atu_tune:
                found.append("safety.allow_atu_tune is false")
        else:
            if self._radio.ptt_line is PttLine.CIV:
                found.append("PTT via CI-V is not implemented (radio.ptt_line = 'civ')")
            if hardware and not self._safety.allow_transmit:
                found.append("safety.allow_transmit is false")
        duty = self._duty_blocker(self._clock())
        if duty is not None:
            found.append(duty)
        if self._history_unsaved:
            found.append("transmission history not saved (tried again at each request)")
        return found

    def status(self) -> dict[str, object]:
        lease = self._lease
        now = self._clock()
        lease_info: dict[str, object] | None = None
        if lease is not None:
            elapsed = now - lease.started_at
            lease_info = {
                "lease": lease.id,
                "source": lease.source,
                "frequency_hz": lease.frequency_hz,
                "elapsed_s": round(elapsed, 3),
                "remaining_s": round(max(0.0, self._safety.hard_limit_s - elapsed), 3),
            }
        return {
            "version": __version__,
            "backend": str(self._radio.backend),
            "ptt_line": str(self._radio.ptt_line),
            "port": str(self._port_state),
            "port_detail": self._port_detail,
            "identity": {
                "state": str(self._identity),
                "detail": self._identity_detail,
                "reply": self._identity_reply.hex() if self._identity_reply is not None else None,
            },
            "tx": lease_info,
            # An antenna tuning the guard is waiting to see end (D43).
            "tuning": self._tune is not None,
            "lockout": (
                {"reason": self._lockout.reason, "since": self._lockout.since}
                if self._lockout
                else None
            ),
            "blockers": self.blockers(),
            "duty": {
                "used_s": round(self._duty_used(now), 3),
                "available_in_s": round(self._duty_available_in(now), 3),
            },
            "limits": {
                "lease_renew_interval_s": self._safety.lease_renew_interval_s,
                "lease_timeout_s": self._safety.lease_timeout_s,
                "hard_limit_s": self._safety.hard_limit_s,
                "duty_window_s": self._safety.duty_window_s,
                "duty_max_tx_s": self._safety.duty_max_tx_s,
            },
        }

    # Request handlers

    def _handle_civ(self, request: Request) -> Message:
        try:
            frame, entry = check_frame(
                request.fields.get("frame"),
                radio_address=self._radio.radio_address,
                controller_address=self._radio.controller_address,
            )
        except FrameRejectedError as exc:
            logger.warning(
                "civ_rejected",
                extra=kv(code=exc.code, reason=exc.reason, frame=request.fields.get("frame")),
            )
            return error(request.id, ErrorCode(exc.code), exc.reason)
        # While transmitting, and while an antenna tuning may still be sending its
        # carrier (it cannot be stopped, D43), nothing may change the radio.
        if entry.changes_radio_state and (self._lease is not None or self._tuning(self._clock())):
            logger.warning(
                "civ_rejected", extra=kv(code=ErrorCode.TX_ACTIVE, command=f"{frame.command:02x}")
            )
            return error(
                request.id,
                ErrorCode.TX_ACTIVE,
                "changing radio state is refused while transmitting",
            )
        port = self._port
        if port is None:
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, self._port_detail)
        data = frame.to_bytes()
        try:
            port.write(data)
        except PortLostError as exc:
            self._port_lost(str(exc))
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, str(exc))
        logger.debug("civ_tx", extra=kv(frame=data.hex()))
        self._expected_echo = data
        return ok(request.id)

    def _handle_tx_request(self, request: Request) -> Message:
        source = request.fields.get("source")
        frequency = request.fields.get("frequency_hz")
        if not isinstance(source, str) or not 0 < len(source) <= MAX_SOURCE_LENGTH:
            return error(request.id, ErrorCode.BAD_REQUEST, "'source' must be a short string")
        if frequency is not None and (
            isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 0
        ):
            return error(request.id, ErrorCode.BAD_REQUEST, "'frequency_hz' must be an integer")
        refused = self._refuse_unless_free(request)
        if refused is not None:
            return refused

        lease_id = secrets.token_hex(8)
        started_ts = self._txlog.timestamp()
        # Durable record first (D24.8, D24.9); PTT only afterwards.
        try:
            self._marker.write(
                tx_active=True,
                pending_lockout=self._pending_lockout(),
                lease={
                    "lease": lease_id,
                    "source": source,
                    "frequency_hz": frequency,
                    "started": started_ts,
                },
            )
        except OSError as exc:
            logger.error("run_marker_write_failed", extra=kv(error=exc))
            return self._refuse_tx(request, ErrorCode.STATE_WRITE_FAILED, f"state not saved: {exc}")
        try:
            self._txlog.write_at(
                started_ts, "tx_start", lease=lease_id, source=source, frequency_hz=frequency
            )
        except OSError as exc:
            logger.error("tx_log_write_failed", extra=kv(error=exc))
            self._write_marker(tx_active=False)
            return self._refuse_tx(request, ErrorCode.TX_LOG_FAILED, f"TX log not written: {exc}")

        now = self._clock()
        self._lease = Lease(lease_id, source, frequency, now, started_ts, now)
        port = self._port
        if port is None or self._stopping:
            self._end_lease(EndReason.ERROR, detail="port closed before PTT")
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, "serial port is not open")
        try:
            actual = self._apply_lines(port, self._ptt_target, "ptt_on")
        except PortLostError as exc:
            self._port_lost(str(exc))
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, str(exc))
        if self._stopping:
            # A stop signal arrived while PTT was being keyed (D25.3); its handler
            # may already have lowered the lines, which is not a line fault.
            self._end_lease(EndReason.SHUTDOWN)
            return self._refuse_tx(request, ErrorCode.TX_BLOCKED, "guard is shutting down")
        if actual != self._ptt_target:
            detail = f"PTT lines not confirmed after assert (read {actual})"
            self._port_lost(detail, lease_reason=EndReason.LINE_MISMATCH)
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, detail)
        logger.info("tx_started", extra=kv(lease=lease_id, source=source, frequency_hz=frequency))
        return ok(
            request.id,
            lease=lease_id,
            lease_renew_interval_s=self._safety.lease_renew_interval_s,
            lease_timeout_s=self._safety.lease_timeout_s,
            hard_limit_s=self._safety.hard_limit_s,
            remaining_s=round(self._remaining(self._lease, self._clock()), 3),
        )

    def _refuse_unless_free(self, request: Request, *, tuning: bool = False) -> Message | None:
        """The refusal for a transmission that may not start now, else None."""
        if self._lease is not None:
            return self._refuse_tx(request, ErrorCode.TX_BUSY, "a transmission is already active")
        if self._tuning(self._clock()):
            return self._refuse_tx(request, ErrorCode.TX_BUSY, "the antenna tuner is tuning")
        if self._lockout is not None:
            return self._refuse_tx(
                request,
                ErrorCode.TX_LOCKED,
                f"transmit locked ({self._lockout.reason} since {self._lockout.since}); "
                "tx_unlock required",
            )
        if self._history_unsaved:
            self._save_history(self._clock())  # the disk may take it again now
        blockers = self.blockers(tuning=tuning)
        if blockers:
            return self._refuse_tx(request, ErrorCode.TX_BLOCKED, "; ".join(blockers))
        if self._stopping:
            return self._refuse_tx(request, ErrorCode.TX_BLOCKED, "guard is shutting down")
        return None

    def _tuning(self, _now: float) -> bool:
        """A tuning may still be transmitting (it cannot be stopped, D43)."""
        return self._tune is not None

    def _handle_atu_tune(self, request: Request) -> Message:
        """Start an antenna tuning: a short carrier the guard cannot stop (D43)."""
        source = request.fields.get("source")
        frequency = request.fields.get("frequency_hz")
        if not isinstance(source, str) or not 0 < len(source) <= MAX_SOURCE_LENGTH:
            return error(request.id, ErrorCode.BAD_REQUEST, "'source' must be a short string")
        if frequency is not None and (
            isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 0
        ):
            return error(request.id, ErrorCode.BAD_REQUEST, "'frequency_hz' must be an integer")
        refused = self._refuse_unless_free(request, tuning=True)
        if refused is not None:
            return refused
        now = self._clock()
        last = self._last_tune_end
        if last is not None and now - last < ATU_TUNE_INTERVAL_S:
            wait = ATU_TUNE_INTERVAL_S - (now - last)
            return self._refuse_tx(
                request, ErrorCode.TX_BLOCKED, f"next tuning allowed in {wait:.0f} s"
            )
        # A tuning cannot be stopped: there must be room for the longest one.
        if self._duty_used(now) + ATU_TUNE_MAX_S > self._safety.duty_max_tx_s:
            return self._refuse_tx(
                request,
                ErrorCode.TX_BLOCKED,
                f"duty cycle: no room for a tuning (up to {ATU_TUNE_MAX_S:g} s)",
            )
        port = self._port
        if port is None:
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, self._port_detail)
        # Durable records first, as for a lease: TX log, then the duty history,
        # which holds the longest tuning until its end is seen (a restart meanwhile
        # keeps that).
        started_ts = self._txlog.timestamp()
        try:
            self._txlog.write(
                "atu_tune",
                source=source,
                frequency_hz=frequency,
                max_s=ATU_TUNE_MAX_S,
            )
        except OSError as exc:
            logger.error("tx_log_write_failed", extra=kv(error=exc))
            return self._refuse_tx(request, ErrorCode.TX_LOG_FAILED, f"TX log not written: {exc}")
        self._record_transmission((now, now + ATU_TUNE_MAX_S))
        data = atu_tune_request(self._radio.radio_address, self._radio.controller_address)
        try:
            port.write(data)
        except PortLostError as exc:
            self._port_lost(str(exc))
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, str(exc))
        self._tune = Tuning(source, frequency, now, started_ts)
        self._expected_echo = data
        logger.warning("atu_tune", extra=kv(source=source, frequency_hz=frequency))
        return ok(request.id, max_s=ATU_TUNE_MAX_S, min_interval_s=ATU_TUNE_INTERVAL_S)

    def _tune_meter_reading(self, frame: Frame) -> None:
        """A power meter reply from the radio while tuning: zero twice ends it."""
        tune = self._tune
        if tune is None or frame.command != Command.READ_METER:
            return
        if frame.body[:1] != bytes([POWER_METER_SUBCOMMAND]) or len(frame.body) < 2:
            return
        if any(frame.body[1:]):
            tune.zeros = 0
            tune.emitted = True
            return
        tune.zeros += 1
        now = self._clock()
        if tune.zeros >= ATU_TUNE_ZERO_READINGS and now - tune.started_at >= ATU_TUNE_MIN_S:
            self._end_tune(now, "meter_zero")

    def _check_tune(self) -> None:
        """Safety loop: a tuning that outlasts ATU_TUNE_MAX_S is a fault (D43)."""
        tune = self._tune
        if tune is None:
            return
        now = self._clock()
        if now - tune.started_at < ATU_TUNE_MAX_S:
            return
        lockout = Lockout(ATU_TUNE_OVERRUN, self._txlog.timestamp())
        self._lockout_unsaved = not self._persist_lockout(lockout)
        self._lockout = lockout
        self._end_tune(now, "overrun")
        self._log_tx("lockout", reason=ATU_TUNE_OVERRUN)
        message = (
            f"the antenna tuning still emitted, or could not be seen to end, after "
            f"{ATU_TUNE_MAX_S:g} s; transmit locked, tx_unlock required"
        )
        logger.error("atu_tune_overrun", extra=kv(detail=message))
        self._events.append(event("atu_tune_overrun", message=message))

    def _end_tune(self, now: float, reason: str) -> None:
        tune = self._tune
        if tune is None:
            return
        self._tune = None
        self._last_tune_end = now
        end = max(now, tune.started_at + ATU_TUNE_MIN_S)
        if reason != "overrun":
            # The duty history held the longest tuning: now the real one.
            planned = (tune.started_at, tune.started_at + ATU_TUNE_MAX_S)
            if planned in self._history:
                self._history.remove(planned)
            self._record_transmission((tune.started_at, end))
        self._log_tx(
            "atu_tune_end",
            source=tune.source,
            frequency_hz=tune.frequency_hz,
            started=tune.started_ts,
            duration_s=round(now - tune.started_at, 3),
            reason=reason,
            emitted=tune.emitted,
        )
        logger.warning(
            "atu_tune_end",
            extra=kv(
                reason=reason, duration_s=round(now - tune.started_at, 1), emitted=tune.emitted
            ),
        )
        # Information for the page (owner's decision 2026-11-14): no power seen
        # means the radio did not tune; with its screen blanked the G90 keeps the
        # tuning until the screen comes on (section 8 item 13). Nothing here
        # depends on it.
        self._events.append(
            event(
                "atu_tune_end",
                reason=reason,
                duration_s=round(now - tune.started_at, 1),
                emitted=tune.emitted,
            )
        )

    def _handle_tx_renew(self, request: Request) -> Message:
        lease = self._matching_lease(request)
        if lease is None:
            return error(request.id, ErrorCode.UNKNOWN_LEASE, "no such active lease")
        now = self._clock()
        lease.last_renew = now
        return ok(request.id, remaining_s=round(self._remaining(lease, now), 3))

    def _handle_tx_release(self, request: Request) -> Message:
        if self._matching_lease(request) is None:
            return error(request.id, ErrorCode.UNKNOWN_LEASE, "no such active lease")
        self._end_lease(EndReason.RELEASED)
        return ok(request.id)

    def _handle_tx_unlock(self, request: Request) -> Message:
        if self._lease is not None:
            return error(request.id, ErrorCode.TX_BUSY, "cannot unlock during a transmission")
        previous = self._lockout
        if previous is None:
            return ok(request.id, was_locked=False)
        try:
            self._lockouts.save(None)
        except OSError as exc:
            logger.error("lockout_clear_failed", extra=kv(error=exc))
            return error(request.id, ErrorCode.STATE_WRITE_FAILED, f"unlock not saved: {exc}")
        self._lockout = None
        self._lockout_unsaved = False
        self._write_marker(tx_active=False)
        self._log_tx("tx_unlock", previous_reason=previous.reason, locked_since=previous.since)
        logger.warning("tx_unlocked", extra=kv(previous_reason=previous.reason))
        return ok(request.id, was_locked=True)

    def _handle_port_open(self, request: Request) -> Message:
        if self._port_state is PortState.OPEN:
            return ok(request.id, port=str(self._port_state))
        if self._port_state is PortState.LINE_FAULT and self._port is not None:
            # The port is still open: confirm the lines are low again.
            if not self._lower_lines_on(self._port):
                self._port_lost("lines could not be confirmed inactive")
                return error(request.id, ErrorCode.PORT_UNAVAILABLE, self._port_detail)
            self._set_port_state(PortState.OPEN, "lines confirmed inactive")
            self._log_tx("port_open", result=str(self._port_state), detail="after line fault")
            return ok(request.id, port=str(self._port_state))
        opened = self._open_port()
        self._log_tx("port_open", result=str(self._port_state), detail=self._port_detail)
        if not opened:
            return error(request.id, ErrorCode.PORT_UNAVAILABLE, self._port_detail)
        return ok(request.id, port=str(self._port_state))

    # Lease

    def _matching_lease(self, request: Request) -> Lease | None:
        lease = self._lease
        if lease is None or request.fields.get("lease") != lease.id:
            return None
        return lease

    def _check_lease(self) -> None:
        lease = self._lease
        if lease is None:
            self._watch_idle_lines()
            return
        now = self._clock()
        if now - lease.started_at >= self._safety.hard_limit_s:
            self._enforce_hard_limit()
            return
        if self._duty_used(now) >= self._safety.duty_max_tx_s:
            self._end_lease(
                EndReason.DUTY_LIMIT,
                detail=(
                    f"{self._safety.duty_max_tx_s:g} s of transmission in "
                    f"{self._safety.duty_window_s:g} s reached"
                ),
            )
            return
        if now - lease.last_renew >= self._safety.lease_timeout_s:
            self._end_lease(EndReason.LEASE_EXPIRED)
            return
        if self._stopping:
            # emergency_lower() already dropped the lines; record the end as a shutdown.
            self._end_lease(EndReason.SHUTDOWN)
            return
        port = self._port
        if port is None:
            self._end_lease(EndReason.PORT_LOST, detail="port closed during transmission")
            return
        try:
            actual = port.read_lines()
        except PortLostError as exc:
            self._port_lost(str(exc))
            return
        if actual != self._ptt_target:
            self._port_lost(
                f"PTT lines changed during transmission (read {actual})",
                lease_reason=EndReason.LINE_MISMATCH,
            )

    def _watch_idle_lines(self) -> None:
        """Without a transmission both lines must be inactive (D25.1)."""
        port = self._port
        if port is None:
            return
        try:
            lines = port.read_lines()
        except PortLostError as exc:
            self._port_lost(str(exc))
            return
        if not lines.any_active:
            return
        if not self._lower_lines_on(port):
            self._port_lost(f"lines active without a transmission and not lowered (read {lines})")
            return
        if self._port_state is PortState.LINE_FAULT:
            return  # already reported; keep lowering on every iteration
        detail = (
            f"lines became active without a transmission (read {lines}); another process "
            "may have opened the device; port_open required"
        )
        self._set_port_state(PortState.LINE_FAULT, detail)
        self._log_tx("line_fault", lines=str(lines))
        self._events.append(event("line_fault", message=detail))

    def _enforce_hard_limit(self) -> None:
        lockout = Lockout(HARD_LIMIT, self._txlog.timestamp())
        # Persist before releasing PTT (AGENTS.md rule 13); release regardless of the outcome.
        # If saving fails, the run marker carries the lockout instead (D25.5).
        self._lockout_unsaved = not self._persist_lockout(lockout)
        self._lockout = lockout
        self._end_lease(EndReason.HARD_LIMIT)
        self._log_tx("lockout", reason=HARD_LIMIT)

    def _end_lease(
        self, reason: EndReason, *, detail: str | None = None, update_marker: bool = True
    ) -> None:
        lease = self._lease
        if lease is None:
            return
        self._lease = None
        confirmed = self._lower_lines()
        ended_at = self._clock()
        duration = ended_at - lease.started_at
        self._record_transmission((lease.started_at, ended_at))
        self._log_tx(
            "tx_end",
            lease=lease.id,
            source=lease.source,
            frequency_hz=lease.frequency_hz,
            started=lease.started_ts,
            duration_s=round(duration, 3),
            reason=str(reason),
            detail=detail,
        )
        if update_marker:
            self._write_marker(tx_active=False)
        self._events.append(event("tx_ended", lease=lease.id, reason=str(reason)))
        log = logger.info if reason is EndReason.RELEASED else logger.warning
        log("tx_ended", extra=kv(lease=lease.id, reason=reason, duration_s=round(duration, 3)))
        if not confirmed:
            self._port_lost("lines not confirmed inactive after release")

    def _refuse_tx(self, request: Request, code: ErrorCode, message: str) -> Message:
        logger.warning("tx_refused", extra=kv(code=code, reason=message))
        return error(request.id, code, message)

    # Port

    def _open_port(self) -> bool:
        """Open the port safely: lines low before and after open, confirmed by read-back."""
        self._close_port()
        port = self._port_factory()
        try:
            port.set_lines(INACTIVE)
            logger.debug("port_open_step", extra=kv(step="lines_requested_low_before_open"))
            port.open()
            logger.debug("port_open_step", extra=kv(step="opened"))
            lines = self._apply_lines(port, INACTIVE, "after_open")
        except PortLostError as exc:
            logger.debug("port_open_step", extra=kv(step="failed", error=exc))
            self._safe_close(port)
            self._set_port_state(PortState.UNAVAILABLE, str(exc))
            return False
        if lines != INACTIVE:
            self._safe_close(port)
            self._set_port_state(
                PortState.UNAVAILABLE, f"lines not confirmed inactive after open (read {lines})"
            )
            return False
        self._port = port
        self._port_generation += 1
        self._splitter = FrameSplitter()
        self._expected_echo = None
        self._set_port_state(PortState.OPEN, "lines confirmed inactive")
        self._check_identity(port)
        return self._port is not None

    def _check_identity(self, port: SerialPort) -> None:
        """Ask the radio who it is (``1D 19``, D32); TX stays blocked unless it matches.

        Runs synchronously while the port is being opened, so no server frame
        can be in flight and any NG is the answer to this query.
        """
        query = read_xiegu_id_request(self._radio.radio_address, self._radio.controller_address)
        self._identity_reply = None
        self._identity_pending = True
        try:
            port.write(query)
        except PortLostError as exc:
            self._identity_pending = False
            self._port_lost(str(exc))
            return
        logger.debug("civ_tx", extra=kv(frame=query.hex(), kind="identity_query"))
        self._expected_echo = query
        deadline = time.monotonic() + self._identity_timeout_s
        while self._identity_pending and self._port is not None:
            self._read_port()
            if not self._identity_pending or time.monotonic() >= deadline:
                break
            time.sleep(_IDENTITY_POLL_S)
        if self._identity_pending:
            self._identity_pending = False
            self._set_identity(
                IdentityState.NO_REPLY,
                f"no answer to the identity query within {self._identity_timeout_s:g} s",
            )

    def _identity_answer(self, frame: Frame) -> bool:
        """Consume the answer to the identity query; False if ``frame`` is something else."""
        if frame.is_ng:
            self._identity_pending = False
            self._set_identity(
                IdentityState.REJECTED,
                "the radio refused the identity query (NG); firmware older than 1.8?",
            )
            return True
        if frame.command != Command.XIEGU:
            return False
        self._identity_pending = False
        # The whole body is compared as raw bytes: whether the radio echoes the
        # sub-command is not assumed (the G90 echoes it for 1D 19, not for 19 00).
        data = frame.body
        self._identity_reply = data
        expected = self._radio.identity
        if expected is None:
            self._set_identity(
                IdentityState.NOT_CONFIGURED,
                f"radio.identity is not set; the radio answered {data.hex()!r} — "
                "set radio.identity to this value after checking it is the G90",
            )
        elif data == expected:
            self._set_identity(IdentityState.CONFIRMED, f"radio answered {data.hex()}")
        else:
            self._set_identity(
                IdentityState.MISMATCH,
                f"radio answered {data.hex()}, expected {expected.hex()} (radio.identity)",
            )
        return True

    def _set_identity(self, state: IdentityState, detail: str) -> None:
        self._identity = state
        self._identity_detail = detail
        level = logging.INFO if state is IdentityState.CONFIRMED else logging.WARNING
        reply = self._identity_reply.hex() if self._identity_reply is not None else None
        logger.log(level, "radio_identity", extra=kv(state=state, reply=reply, detail=detail))

    def _close_port(self) -> None:
        port, self._port = self._port, None
        self._identity = IdentityState.NOT_CHECKED
        self._identity_detail = "port not open"
        self._identity_pending = False
        if port is not None:
            self._lower_lines_on(port)
            self._safe_close(port)

    def _port_lost(self, detail: str, *, lease_reason: EndReason = EndReason.PORT_LOST) -> None:
        self._close_port()
        self._set_port_state(PortState.LOST, detail)
        if self._lease is not None:
            self._end_lease(lease_reason, detail=detail)
        self._log_tx("port_lost", detail=detail)
        self._events.append(event("port_lost", message=detail))

    def _read_port(self) -> None:
        port = self._port
        if port is None:
            return
        try:
            data = port.read_available()
        except PortLostError as exc:
            self._port_lost(str(exc))
            return
        self.last_read_bytes = len(data)
        if data:
            for item in self._splitter.feed(data):
                self._handle_rx(item)

    def _handle_rx(self, item: Frame | Garbage) -> None:
        if isinstance(item, Garbage):
            logger.warning("civ_rx_garbage", extra=kv(data=item.data.hex(), reason=item.reason))
            return
        raw = item.to_bytes()
        if self._expected_echo is not None and raw == self._expected_echo:
            self._expected_echo = None
            logger.debug("civ_rx", extra=kv(frame=raw.hex(), kind="echo", forwarded=False))
            return
        if item.from_address != self._radio.radio_address or item.to_address not in (
            self._radio.controller_address,
            BROADCAST_ADDRESS,
        ):
            logger.debug("civ_rx", extra=kv(frame=raw.hex(), kind="other_device", forwarded=False))
            return
        if (
            self._identity_pending
            and item.to_address == self._radio.controller_address
            and self._identity_answer(item)
        ):
            logger.debug("civ_rx", extra=kv(frame=raw.hex(), kind="identity", forwarded=False))
            return
        if item.to_address == self._radio.controller_address:
            self._tune_meter_reading(item)  # read only; the reply still goes to the server
        kind = "broadcast" if item.to_address == BROADCAST_ADDRESS else "reply"
        logger.debug("civ_rx", extra=kv(frame=raw.hex(), kind=kind, forwarded=True))
        self._events.append(event("civ_rx", frame=raw.hex()))

    def _lower_lines(self) -> bool:
        """Lower PTT; True if the inactive state was confirmed (or the port is closed)."""
        port = self._port
        if port is None:
            return True
        return self._lower_lines_on(port)

    def _lower_lines_on(self, port: SerialPort) -> bool:
        try:
            return self._apply_lines(port, INACTIVE, "lower") == INACTIVE
        except PortLostError as exc:
            logger.debug("lines", extra=kv(reason="lower", error=exc))
            return False

    @staticmethod
    def _apply_lines(port: SerialPort, state: LineState, reason: str) -> LineState:
        """Set the lines, read them back and trace both (DEBUG)."""
        port.set_lines(state)
        actual = port.read_lines()
        logger.debug(
            "lines",
            extra=kv(
                reason=reason,
                set_dtr=state.dtr,
                set_rts=state.rts,
                read_dtr=actual.dtr,
                read_rts=actual.rts,
            ),
        )
        return actual

    @staticmethod
    def _safe_close(port: SerialPort) -> None:
        try:
            port.close()
        except OSError as exc:
            logger.warning("port_close_failed", extra=kv(error=exc))

    def _set_port_state(self, state: PortState, detail: str) -> None:
        self._port_state = state
        self._port_detail = detail
        level = logging.INFO if state is PortState.OPEN else logging.ERROR
        logger.log(level, "port_state", extra=kv(state=state, detail=detail))

    # Duty cycle (decisions.md D31)

    def _remaining(self, lease: Lease | None, now: float) -> float:
        """Seconds until the hard limit or the duty cycle ends ``lease``."""
        if lease is None:
            return 0.0
        hard = self._safety.hard_limit_s - (now - lease.started_at)
        duty = self._safety.duty_max_tx_s - self._duty_used(now)
        return max(0.0, min(hard, duty))

    def _duty_used(self, now: float) -> float:
        """Seconds transmitted within the window ending at ``now``."""
        since = now - self._safety.duty_window_s
        used = sum(max(0.0, min(end, now) - max(start, since)) for start, end in self._history)
        if self._lease is not None:
            used += now - max(self._lease.started_at, since)
        return used

    def _duty_available_in(self, now: float) -> float:
        """Seconds until a new transmission is allowed by the duty cycle."""
        waits = [0.0]
        if self._duty_blocked_until is not None and now < self._duty_blocked_until:
            waits.append(self._duty_blocked_until - now)
        if self._lease is None:
            # Enough old transmission time has to leave the window; it leaves
            # oldest first, one second per second.
            window = self._safety.duty_window_s
            to_shed = self._duty_used(now) - self._safety.duty_max_tx_s + 0.001
            for begin, end in sorted(self._history):
                if to_shed <= 0:
                    break
                start = max(begin, now - window)
                length = max(0.0, min(end, now) - start)
                if length >= to_shed:
                    waits.append(start + to_shed + window - now)
                    to_shed = 0.0
                else:
                    to_shed -= length
        return max(waits)

    def _duty_blocker(self, now: float) -> str | None:
        if self._duty_blocked_until is not None and now < self._duty_blocked_until:
            return (
                f"duty cycle: {self._duty_block_reason}; "
                f"available in {self._duty_blocked_until - now:.0f} s"
            )
        if self._lease is None and self._duty_used(now) >= self._safety.duty_max_tx_s:
            return (
                f"duty cycle: {self._duty_used(now):.0f} s of transmission in the last "
                f"{self._safety.duty_window_s:g} s (limit {self._safety.duty_max_tx_s:g} s); "
                f"available in {self._duty_available_in(now):.0f} s"
            )
        return None

    def _block_duty(self, now: float, reason: str) -> None:
        """Treat the window as full from ``now`` on: no transmission for one window."""
        until = now + self._safety.duty_window_s
        if self._duty_blocked_until is None or until > self._duty_blocked_until:
            self._duty_blocked_until = until
            self._duty_block_reason = reason
        logger.error("duty_window_full", extra=kv(reason=reason))

    def _load_history(self, unfinished: dict[str, object] | None) -> None:
        """Restore the duty window; ``unfinished`` is the lease a crash left open."""
        now = self._clock()
        wall = self._txlog.now().timestamp()
        self._wall_offset = wall - now
        try:
            stored = self._history_store.load()
        except HistoryUnreadableError as exc:
            self._block_duty(now, f"transmission history unreadable ({exc})")
            stored = []
        if any(end > wall + _CLOCK_TOLERANCE_S for _, end in stored):
            self._block_duty(now, "transmission history lies in the future (clock went back?)")
        if unfinished is not None:
            started = _parse_timestamp(unfinished.get("started"))
            if started is None or started > wall:
                self._block_duty(now, "start of the interrupted transmission is unknown")
            else:
                # Assume the worst: it transmitted until this restart.
                stored.append((started, wall))
        self._history = [(a - self._wall_offset, b - self._wall_offset) for a, b in stored]
        self._prune_history(now)
        self._save_history(now)

    def _record_transmission(self, interval: Interval) -> None:
        self._history.append(interval)
        self._prune_history(interval[1])
        self._save_history(interval[1])

    def _prune_history(self, now: float) -> None:
        since = now - self._safety.duty_window_s
        self._history = [(a, b) for a, b in self._history if b > since]

    def _save_history(self, now: float) -> None:
        offset = self._wall_offset
        try:
            self._history_store.save([(a + offset, b + offset) for a, b in self._history])
        except OSError as exc:
            # Without a saved history a restart could forget this window: block it,
            # here for a window, and after a restart through an emptied (unreadable)
            # file; and no transmission until a save succeeds (code review
            # 2026-11-10, point 10d; owner's decision 2026-11-10).
            self._history_unsaved = True
            self._block_duty(now, f"transmission history not saved ({exc})")
            try:
                self._history_store.invalidate()
            except OSError as invalidate_exc:
                logger.error("history_invalidate_failed", extra=kv(error=invalidate_exc))
        else:
            self._history_unsaved = False

    # Persistence helpers

    def _persist_lockout(self, lockout: Lockout) -> bool:
        try:
            self._lockouts.save(lockout)
        except OSError as exc:
            # The lockout still applies for this run.
            logger.error("lockout_persist_failed", extra=kv(reason=lockout.reason, error=exc))
            return False
        return True

    def _pending_lockout(self) -> str | None:
        if self._lockout_unsaved and self._lockout is not None:
            return self._lockout.reason
        return None

    def _write_marker(self, *, tx_active: bool) -> None:
        try:
            self._marker.write(tx_active=tx_active, pending_lockout=self._pending_lockout())
        except OSError as exc:
            logger.error("run_marker_write_failed", extra=kv(error=exc))

    def _log_tx(self, name: str, **fields: object) -> None:
        try:
            self._txlog.write(name, **fields)
        except OSError as exc:
            logger.error("tx_log_write_failed", extra=kv(record=name, error=exc))


def _parse_timestamp(value: object) -> float | None:
    """UNIX seconds from an ISO 8601 TX log timestamp, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
