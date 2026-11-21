# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface of the serial port as used by the guard.

Both the real pyserial-backed port (stage 1, ``guard/``) and the simulator
implement this protocol. The module depends on the standard library only, so
the guard may import it (AGENTS.md rule 11).
"""

from dataclasses import dataclass
from typing import Protocol


class PortLostError(OSError):
    """The port stopped working: device unplugged, I/O error, or closed underneath us."""


@dataclass(frozen=True, slots=True)
class LineState:
    """Output modem control lines. ``True`` means asserted (active)."""

    dtr: bool
    rts: bool

    @property
    def any_active(self) -> bool:
        return self.dtr or self.rts


INACTIVE = LineState(dtr=False, rts=False)


class SerialPort(Protocol):
    """Serial port with explicit control of DTR and RTS.

    Contract for implementations:

    * :meth:`set_lines` may be called before :meth:`open`; the requested state
      must then be applied as part of opening, before any other I/O.
    * :meth:`read_lines` reports the state read back from the device (not the
      last requested state), so the guard can confirm it.
    * Every I/O failure is raised as :class:`PortLostError`.
    """

    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> None: ...

    def read_available(self) -> bytes:
        """Return the bytes received so far without blocking (possibly empty)."""
        ...

    def fileno(self) -> int | None:
        """Descriptor that becomes readable when bytes arrive, or None (no descriptor).

        The guard waits on it only to wake up sooner; reading is still done by
        :meth:`read_available` in the safety loop.
        """
        ...

    def set_lines(self, state: LineState) -> None: ...

    def read_lines(self) -> LineState: ...
