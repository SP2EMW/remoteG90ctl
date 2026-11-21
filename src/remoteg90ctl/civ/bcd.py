# SPDX-License-Identifier: GPL-3.0-or-later
"""Packed BCD encoding of frequencies, as used by the Icom CI-V dialect.

A frequency is sent least significant byte first; each byte holds two decimal
digits, the higher one in the upper nibble. 14.074 MHz in five bytes is
``00 40 07 14 00``.

The five-byte (10 digit) frequency field is the Icom convention. That the G90
uses the same width is UNVERIFIED (AGENTS.md section 8); callers pass the width
explicitly through ``FREQUENCY_BYTES`` so it can be changed in one place.
"""

from remoteg90ctl.civ.errors import BcdError

# UNVERIFIED: Icom convention, assumed for the G90 (AGENTS.md section 8).
FREQUENCY_BYTES = 5


def encode_frequency(hz: int, length: int = FREQUENCY_BYTES) -> bytes:
    """Encode ``hz`` as ``length`` bytes of little-endian packed BCD."""
    if length < 1:
        raise BcdError(f"BCD field length must be positive, got {length}")
    if hz < 0 or hz >= 10 ** (2 * length):
        raise BcdError(f"frequency {hz} Hz does not fit in {length} BCD bytes")
    out = bytearray()
    remaining = hz
    for _ in range(length):
        low = remaining % 10
        remaining //= 10
        high = remaining % 10
        remaining //= 10
        out.append((high << 4) | low)
    return bytes(out)


def decode_frequency(data: bytes) -> int:
    """Decode little-endian packed BCD into an integer number of hertz."""
    if not data:
        raise BcdError("empty BCD field")
    hz = 0
    for byte in reversed(data):
        high, low = byte >> 4, byte & 0x0F
        if high > 9 or low > 9:
            raise BcdError(f"byte 0x{byte:02X} is not a valid BCD digit pair")
        hz = hz * 100 + high * 10 + low
    return hz
