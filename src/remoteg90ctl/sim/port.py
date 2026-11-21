# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated serial port connected to a :class:`SimulatedRadio`.

Implements :class:`remoteg90ctl.serial_port.SerialPort`. It records every change
of DTR and RTS with a timestamp from an injectable clock, so tests can check
exactly when PTT could have been keyed, and it can inject faults.

Lines requested before :meth:`open` are applied on open. If nothing was
requested, both lines come up **asserted**, as pyserial does by default — so a
caller that forgets to lower them first is caught by the tests.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from remoteg90ctl.civ import END_OF_MESSAGE
from remoteg90ctl.serial_port import INACTIVE, LineState, PortLostError
from remoteg90ctl.sim.radio import SimulatedRadio

Clock = Callable[[], float]

_ASSERTED = LineState(dtr=True, rts=True)


@dataclass(frozen=True, slots=True)
class LineEvent:
    """Line state in effect from ``time`` on."""

    time: float
    state: LineState


class SimulatedSerialPort:
    """Serial port test double with line history and fault injection."""

    def __init__(
        self,
        radio: SimulatedRadio,
        *,
        clock: Clock = time.monotonic,
        echo: bool = True,
    ) -> None:
        self.radio = radio
        self.echo = echo
        self._clock = clock
        self._open = False
        self._requested: LineState | None = None
        self._lines = INACTIVE
        self._rx = bytearray()
        self._tx_pending = bytearray()
        self._disconnected = False
        self._unresponsive = False
        self.history: list[LineEvent] = [LineEvent(clock(), INACTIVE)]
        self.written: list[bytes] = []

    # SerialPort protocol

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        if self._disconnected:
            raise PortLostError("simulated device is not present")
        if self._open:
            raise PortLostError("simulated port is already open")
        self._open = True
        self._apply(self._requested if self._requested is not None else _ASSERTED)

    def close(self) -> None:
        # HUPCL: the lines drop when the last descriptor is closed.
        if self._open:
            self._open = False
            self._apply(INACTIVE)
        self._rx.clear()
        self._tx_pending.clear()

    def write(self, data: bytes) -> None:
        self._check_io()
        self.written.append(bytes(data))
        if self.echo:
            self._rx.extend(data)
        self._tx_pending.extend(data)
        while (end := self._tx_pending.find(END_OF_MESSAGE)) >= 0:
            message = bytes(self._tx_pending[: end + 1])
            del self._tx_pending[: end + 1]
            reply = self.radio.handle(message)
            if not self._unresponsive:
                self._rx.extend(reply)

    def fileno(self) -> int | None:
        return None  # no descriptor: the guard reads it on its loop timeout

    def read_available(self) -> bytes:
        self._check_io()
        data = bytes(self._rx)
        self._rx.clear()
        return data

    def set_lines(self, state: LineState) -> None:
        if self._disconnected:
            raise PortLostError("simulated device is not present")
        self._requested = state
        if self._open:
            self._apply(state)

    def read_lines(self) -> LineState:
        self._check_io()
        return self._lines

    # Fault injection

    def disconnect(self) -> None:
        """Simulate unplugging the USB adapter; the kernel drops the lines."""
        self._disconnected = True
        if self._lines != INACTIVE:
            self._apply(INACTIVE)

    def reconnect(self) -> None:
        """Make the device present again; the port stays closed until reopened."""
        self._disconnected = False
        self._open = False
        self._requested = None

    def set_unresponsive(self, unresponsive: bool) -> None:
        """When set, the radio receives frames but never replies."""
        self._unresponsive = unresponsive

    def inject_rx(self, data: bytes) -> None:
        """Queue arbitrary bytes (garbage, broadcasts) as if sent by the radio."""
        self._rx.extend(data)

    # Inspection helpers for tests

    @property
    def lines(self) -> LineState:
        """Current line state regardless of the port being open."""
        return self._lines

    def active_intervals(self, line: str) -> list[tuple[float, float | None]]:
        """Intervals during which ``line`` (``"dtr"`` or ``"rts"``) was asserted.

        The end of an interval still in progress is ``None``.
        """
        if line not in ("dtr", "rts"):
            raise ValueError(f"unknown line {line!r}")
        intervals: list[tuple[float, float | None]] = []
        start: float | None = None
        for event in self.history:
            active = getattr(event.state, line)
            if active and start is None:
                start = event.time
            elif not active and start is not None:
                intervals.append((start, event.time))
                start = None
        if start is not None:
            intervals.append((start, None))
        return intervals

    def _apply(self, state: LineState) -> None:
        self.radio.keyed = state.dtr or state.rts
        if state != self._lines:
            self._lines = state
            self.history.append(LineEvent(self._clock(), state))

    def _check_io(self) -> None:
        if self._disconnected:
            raise PortLostError("simulated device was disconnected")
        if not self._open:
            raise PortLostError("simulated port is not open")
