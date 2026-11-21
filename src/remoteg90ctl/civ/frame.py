# SPDX-License-Identifier: GPL-3.0-or-later
"""Building and parsing of CI-V frames.

A frame on the wire is::

    FE FE <to> <from> <command> [<body> ...] FD

``body`` holds an optional sub-command followed by data. Whether a command has a
sub-command is a property of the command, not of the frame, so this module keeps
the body opaque; :func:`build_frame` accepts the sub-command separately only as
a convenience.

Parsing is strict on purpose: exactly one two-byte preamble, exactly one
end-of-message byte and it is the last byte, no reserved byte (``FE``/``FD``)
anywhere else. Input that does not satisfy this is rejected, never repaired.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from remoteg90ctl.civ.errors import CivParseError

PREAMBLE = 0xFE
END_OF_MESSAGE = 0xFD
RESERVED_BYTES = frozenset({PREAMBLE, END_OF_MESSAGE})

# Reply commands used by Icom-dialect radios to acknowledge a set command.
REPLY_OK = 0xFB
REPLY_NG = 0xFA

# FE FE to from cmd FD
MIN_FRAME_LENGTH = 6
# Generous upper bound for the short commands this project uses; anything
# longer is treated as malformed rather than buffered indefinitely.
MAX_FRAME_LENGTH = 64

# Destination address of unsolicited ("transceive") broadcasts from a radio.
BROADCAST_ADDRESS = 0x00


@dataclass(frozen=True, slots=True)
class Frame:
    """A syntactically valid CI-V frame. ``body`` is sub-command plus data."""

    to_address: int
    from_address: int
    command: int
    body: bytes = b""

    def __post_init__(self) -> None:
        for name, value in (
            ("to_address", self.to_address),
            ("from_address", self.from_address),
            ("command", self.command),
        ):
            _check_byte(name, value)
        reserved = RESERVED_BYTES.intersection(self.body)
        if reserved:
            raise CivParseError(
                f"frame body contains reserved byte(s) {_hex_list(sorted(reserved))}"
            )
        if len(self.body) + MIN_FRAME_LENGTH > MAX_FRAME_LENGTH:
            raise CivParseError(f"frame longer than {MAX_FRAME_LENGTH} bytes")

    def to_bytes(self) -> bytes:
        """Serialise the frame including preamble and end-of-message byte."""
        return bytes(
            [
                PREAMBLE,
                PREAMBLE,
                self.to_address,
                self.from_address,
                self.command,
                *self.body,
                END_OF_MESSAGE,
            ]
        )

    @property
    def is_ok(self) -> bool:
        """True for a positive acknowledgement (``FB``) with no data."""
        return self.command == REPLY_OK and not self.body

    @property
    def is_ng(self) -> bool:
        """True for a negative acknowledgement (``FA``) with no data."""
        return self.command == REPLY_NG and not self.body


def build_frame(
    to_address: int,
    from_address: int,
    command: int,
    subcommand: int | None = None,
    data: bytes = b"",
) -> bytes:
    """Build the wire bytes of a frame; raises :class:`CivParseError` on bad input."""
    body = data if subcommand is None else bytes([_check_byte("subcommand", subcommand)]) + data
    return Frame(to_address, from_address, command, body).to_bytes()


def parse_frame(data: bytes) -> Frame:
    """Parse exactly one frame; any deviation raises :class:`CivParseError`."""
    if len(data) < MIN_FRAME_LENGTH:
        raise CivParseError(f"frame too short ({len(data)} bytes)")
    if len(data) > MAX_FRAME_LENGTH:
        raise CivParseError(f"frame too long ({len(data)} bytes)")
    if data[0] != PREAMBLE or data[1] != PREAMBLE:
        raise CivParseError("frame does not start with preamble FE FE")
    if data[-1] != END_OF_MESSAGE:
        raise CivParseError("frame does not end with FD")
    inner = data[2:-1]
    reserved = RESERVED_BYTES.intersection(inner)
    if reserved:
        raise CivParseError(f"reserved byte(s) {_hex_list(sorted(reserved))} inside frame")
    return Frame(inner[0], inner[1], inner[2], bytes(inner[3:]))


def is_echo(received: bytes, sent: bytes) -> bool:
    """True if ``received`` is the echo of ``sent`` on a single-wire CI-V bus."""
    return received == sent


@dataclass(frozen=True, slots=True)
class Garbage:
    """Bytes from a stream that do not form a valid frame."""

    data: bytes
    reason: str


class FrameSplitter:
    """Incrementally split a byte stream from the radio into frames.

    Every byte fed in ends up either in a complete :class:`Frame` or in a
    :class:`Garbage` item; nothing is silently dropped or repaired.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame | Garbage]:
        """Add bytes and return every item that became complete."""
        self._buffer.extend(data)
        return list(self._drain())

    @property
    def pending(self) -> bytes:
        """Bytes buffered while waiting for the end of a frame."""
        return bytes(self._buffer)

    def _drain(self) -> Iterator[Frame | Garbage]:
        while True:
            end = self._buffer.find(END_OF_MESSAGE)
            if end < 0:
                yield from self._trim_unterminated()
                return
            segment = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            yield from _split_segment(segment)

    def _trim_unterminated(self) -> Iterator[Garbage]:
        if len(self._buffer) < MAX_FRAME_LENGTH:
            return
        start = self._buffer.rfind(bytes([PREAMBLE, PREAMBLE]))
        keep_from = start if start > 0 and len(self._buffer) - start < MAX_FRAME_LENGTH else None
        cut = len(self._buffer) if keep_from is None else keep_from
        yield Garbage(bytes(self._buffer[:cut]), "no end of message within maximum frame length")
        del self._buffer[:cut]


def _split_segment(segment: bytes) -> Iterator[Frame | Garbage]:
    """Handle a segment that ends with FD and contains no other FD."""
    start = segment.find(bytes([PREAMBLE, PREAMBLE]))
    if start < 0:
        yield Garbage(segment, "no preamble before end of message")
        return
    if start > 0:
        yield Garbage(segment[:start], "bytes before preamble")
    candidate = segment[start:]
    try:
        yield parse_frame(candidate)
    except CivParseError as exc:
        yield Garbage(candidate, str(exc))


def _check_byte(name: str, value: int) -> int:
    if not 0 <= value <= 0xFF:
        raise CivParseError(f"{name} {value} is not a byte")
    if value in RESERVED_BYTES:
        raise CivParseError(f"{name} 0x{value:02X} is a reserved CI-V byte")
    return value


def _hex_list(values: list[int]) -> str:
    return " ".join(f"{v:02X}" for v in values)
