# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial port backends for the guard: pyserial on hardware, or the simulator.

Opening sequence on hardware (AGENTS.md rule 2):

1. DTR and RTS are requested low *before* ``open()``; pyserial applies the
   request right after the device is opened.
2. ``TIOCEXCL`` is set, so further opens of the device fail with ``EBUSY`` —
   except for root, which the kernel exempts. pyserial's ``exclusive`` flag is
   used as well, but it is only an advisory ``flock``.
3. ``HUPCL`` is set, so the kernel drops the lines when the last descriptor
   closes, including when the process dies.

The kernel raises DTR/RTS when the device is opened — by us, for a moment
before pyserial can lower them, and by any other process that manages to open
it. Software cannot prevent the first; it is a hardware measurement item
(AGENTS.md section 8, item 8) documented in docs/install.md. The guard watches
the lines on every loop iteration to catch the second.
"""

import contextlib
import fcntl
import os
import stat
import struct
import termios
from collections.abc import Callable

import serial

from remoteg90ctl.config import Backend, RadioConfig
from remoteg90ctl.serial_port import INACTIVE, LineState, PortLostError, SerialPort
from remoteg90ctl.sim import DEFAULT_IDENTITY, SimulatedRadio, SimulatedSerialPort

PortFactory = Callable[[], SerialPort]

WRITE_TIMEOUT_S = 0.2


def check_device(path: str) -> str | None:
    """Return why ``path`` is not a usable serial device, or ``None`` if it is."""
    try:
        mode = os.stat(path).st_mode  # follows the by-id symlink
    except FileNotFoundError:
        return f"device {path} does not exist"
    except OSError as exc:
        return f"cannot inspect device {path}: {exc.strerror or exc}"
    if not stat.S_ISCHR(mode):
        return f"{path} is not a character device"
    return None


class PySerialPort:
    """:class:`SerialPort` backed by pyserial, with read-back of the modem lines."""

    def __init__(self, path: str, baudrate: int) -> None:
        self._path = path
        self._serial = serial.Serial()
        self._serial.port = path
        self._serial.baudrate = baudrate
        self._serial.bytesize = serial.EIGHTBITS
        self._serial.parity = serial.PARITY_NONE
        self._serial.stopbits = serial.STOPBITS_ONE
        self._serial.timeout = 0
        self._serial.write_timeout = WRITE_TIMEOUT_S
        self._serial.exclusive = True
        self._serial.dtr = False
        self._serial.rts = False

    @property
    def is_open(self) -> bool:
        return bool(self._serial.is_open)

    def open(self) -> None:
        problem = check_device(self._path)
        if problem is not None:
            raise PortLostError(problem)
        try:
            self._serial.open()
            fd = self._serial.fileno()
            fcntl.ioctl(fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(fd)
            attrs[2] |= termios.HUPCL
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except (serial.SerialException, OSError, termios.error) as exc:
            self.close()
            raise PortLostError(f"cannot open {self._path}: {exc}") from exc

    def close(self) -> None:
        # Closing the descriptor is what drops the lines (HUPCL); nothing else to do.
        with contextlib.suppress(serial.SerialException, OSError):
            self._serial.close()

    def write(self, data: bytes) -> None:
        try:
            written = self._serial.write(data)
        except (serial.SerialException, OSError) as exc:
            raise PortLostError(f"write failed: {exc}") from exc
        if written != len(data):
            raise PortLostError(f"short write ({written} of {len(data)} bytes)")

    def fileno(self) -> int | None:
        try:
            return int(self._serial.fileno()) if self._serial.is_open else None
        except (serial.SerialException, OSError, ValueError):
            return None

    def read_available(self) -> bytes:
        try:
            waiting = self._serial.in_waiting
            return bytes(self._serial.read(waiting)) if waiting else b""
        except (serial.SerialException, OSError) as exc:
            raise PortLostError(f"read failed: {exc}") from exc

    def set_lines(self, state: LineState) -> None:
        try:
            self._serial.dtr = state.dtr
            self._serial.rts = state.rts
        except (serial.SerialException, OSError) as exc:
            raise PortLostError(f"cannot set modem lines: {exc}") from exc

    def read_lines(self) -> LineState:
        """Read DTR/RTS back from the driver with ``TIOCMGET``."""
        if not self._serial.is_open:
            raise PortLostError("port is not open")
        try:
            raw = fcntl.ioctl(self._serial.fileno(), termios.TIOCMGET, struct.pack("I", 0))
        except OSError as exc:
            raise PortLostError(f"cannot read modem lines: {exc}") from exc
        (bits,) = struct.unpack("I", raw)
        return LineState(dtr=bool(bits & termios.TIOCM_DTR), rts=bool(bits & termios.TIOCM_RTS))


def make_port_factory(radio: RadioConfig) -> PortFactory:
    """Return a factory producing the port for the configured backend."""
    if radio.backend is Backend.SIMULATOR:
        # One simulated radio for the whole run, so state survives a reopen.
        # The simulated radio reports the configured identity, so the check
        # passes in simulator mode once radio.identity is set.
        identity = radio.identity if radio.identity is not None else DEFAULT_IDENTITY
        simulated = SimulatedSerialPort(SimulatedRadio(radio.radio_address, identity=identity))
        simulated.set_lines(INACTIVE)
        return lambda: simulated
    if radio.port is None or radio.baudrate is None:  # guaranteed by config validation
        raise ValueError("hardware backend requires radio.port and radio.baudrate")
    path, baudrate = radio.port, radio.baudrate
    return lambda: PySerialPort(path, baudrate)
