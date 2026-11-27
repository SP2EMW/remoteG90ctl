# SPDX-License-Identifier: GPL-3.0-or-later
"""Telnet bytes to text lines, DX spot lines to spots, and the checks on what
the server may ask for (decisions.md D63).

Everything the cluster sends is untrusted (AGENTS.md section 10): telnet
commands are dropped, lines are length-limited, a spot is taken only from a
line that parses whole, and its text keeps printable characters only.
"""

import ipaddress
import re
from dataclasses import dataclass

MAX_LINE_CHARS = 512  # a longer line is dropped whole
MAX_COMMENT_CHARS = 64

# Telnet (RFC 854): IAC and the commands that follow it.
IAC = 255
SB = 250
SE = 240
WILL, WONT, DO, DONT = 251, 252, 253, 254


class TelnetText:
    """Turns the bytes from the cluster into lines of text.

    Telnet negotiation is read and dropped (nothing is answered: the process
    sends the cluster only the callsign). A line longer than MAX_LINE_CHARS
    is dropped whole; so are lines with nothing printable.
    """

    def __init__(self) -> None:
        self._state = "data"  # data, iac, option, sub, sub_iac
        self._line = bytearray()
        self._overflow = False

    @property
    def partial(self) -> str:
        """The text received after the last line end (a prompt has none)."""
        return _printable(self._line.decode("latin-1"))

    def clear_partial(self) -> None:
        """Forget the text after the last line end (the prompt just answered):
        a cluster may send its next line right after it, with no line end."""
        self._line.clear()
        self._overflow = False

    def feed(self, data: bytes) -> list[str]:
        lines: list[str] = []
        for byte in data:
            if self._state != "data":
                self._telnet(byte)
            elif byte == IAC:
                self._state = "iac"
            elif byte == 0x0A:
                if not self._overflow:
                    text = _printable(self._line.decode("latin-1")).strip()
                    if text:
                        lines.append(text)
                self._line.clear()
                self._overflow = False
            else:
                self._add(byte)
        return lines

    def _telnet(self, byte: int) -> None:
        """One byte of a telnet command, which is dropped."""
        state = self._state
        if state == "iac":
            if byte == IAC:
                self._add(byte)  # an escaped 255
                self._state = "data"
            elif byte in (WILL, WONT, DO, DONT):
                self._state = "option"
            else:
                self._state = "sub" if byte == SB else "data"
        elif state == "option":
            self._state = "data"
        elif state == "sub":
            if byte == IAC:
                self._state = "sub_iac"
        else:  # sub_iac
            self._state = "data" if byte == SE else "sub"

    def _add(self, byte: int) -> None:
        if self._overflow:
            return
        if len(self._line) >= MAX_LINE_CHARS:
            self._overflow = True
            self._line.clear()
            return
        self._line.append(byte)


def _printable(text: str) -> str:
    """Printable ASCII only; tabs and other characters become spaces."""
    return "".join(c if " " <= c <= "~" else " " for c in text.replace("\r", ""))


# The prompt for the callsign: "login:", "Please enter your call:", "callsign:".
LOGIN_PROMPT = re.compile(r"(login|call(sign)?)\s*:?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Spot:
    spotter: str
    freq_khz: float
    dx: str
    comment: str
    utc: str  # "HHMM", the time the cluster gives
    locator: str | None


# "DX de SP2EMW:     14074.0  DL1XYZ       FT8 -12 dB                  1234Z JO93"
_SPOT = re.compile(
    r"DX de (?P<spotter>[A-Z0-9/#-]{2,20}):?\s+"
    r"(?P<freq>\d{1,8}(?:\.\d{1,3})?)\s+"
    r"(?P<dx>[A-Z0-9/]{2,20})\s+"
    r"(?P<comment>.*?)\s*"
    r"(?P<utc>[0-2]\d[0-5]\d)Z"
    r"(?:\s+(?P<locator>[A-R]{2}\d{2}(?:[A-X]{2})?))?\s*",
    re.IGNORECASE,
)
_CALL = re.compile(
    r"[A-Z0-9/]*[0-9][A-Z0-9/]*[A-Z][A-Z0-9/]*|[A-Z0-9/]*[A-Z][A-Z0-9/]*[0-9][A-Z0-9/]*"
)
MIN_KHZ = 100.0
MAX_KHZ = 300_000_000.0


def parse_spot(line: str) -> Spot | None:
    """A spot from one cluster line, or None if the line is not a whole spot."""
    match = _SPOT.fullmatch(line.strip())
    if match is None:
        return None
    dx = match["dx"].upper()
    spotter = match["spotter"].upper()
    if _CALL.fullmatch(dx) is None or int(match["utc"][:2]) > 23:
        return None
    freq = float(match["freq"])
    if not MIN_KHZ <= freq <= MAX_KHZ:
        return None
    comment = " ".join(match["comment"].split())[:MAX_COMMENT_CHARS]
    locator = match["locator"].upper() if match["locator"] else None
    return Spot(spotter, freq, dx, comment, match["utc"], locator)


@dataclass(frozen=True, slots=True)
class SolarReport:
    """Solar and geomagnetic figures a cluster broadcasts by itself (WWV, WCY)."""

    kind: str  # "WWV" or "WCY"
    source: str
    hour: int  # UTC hour the cluster gives
    sfi: int | None  # solar flux index
    a: int | None
    k: int | None


# "WWV de VE7CC <18>:   SFI=150, A=5, K=1, No Storms -> No Storms <VE7CC>"
# "WCY de DK0WCY-1 <18> : K=2 expK=2 A=7 R=91 SFI=147 SA=qui GMF=qui Au=no"
_SOLAR = re.compile(
    r"(?P<kind>WWV|WCY) de (?P<source>[A-Z0-9/#-]{2,20})\s*<(?P<hour>[0-2]\d)Z?>\s*:?(?P<rest>.*)",
    re.IGNORECASE,
)
_FIGURE = r"(?<![A-Za-z]){}\s*=\s*(\d{{1,3}})"


def _figure(text: str, name: str, low: int, high: int) -> int | None:
    match = re.search(_FIGURE.format(name), text)
    if match is None:
        return None
    value = int(match[1])
    return value if low <= value <= high else None


def parse_solar(line: str) -> SolarReport | None:
    """A WWV or WCY report from one cluster line, or None."""
    match = _SOLAR.fullmatch(line.strip())
    if match is None or int(match["hour"]) > 23:
        return None
    rest = match["rest"]
    sfi = _figure(rest, "SFI", 30, 999)
    a = _figure(rest, "A", 0, 400)
    k = _figure(rest, "K", 0, 9)
    if sfi is None and a is None and k is None:
        return None
    return SolarReport(
        match["kind"].upper(), match["source"].upper(), int(match["hour"]), sfi, a, k
    )


# ---- What the server may ask for: the address, the port and the callsign.

_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
# A station's call, optionally with a portable part and a cluster suffix (-1).
_LOGIN_CALL = re.compile(
    r"(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,4}[A-Z](?:/[A-Z0-9]{1,4})?(?:-[0-9]{1,2})?"
)


def check_host(value: object) -> str | None:
    """The cluster's host name or IP address, or None if it is not one."""
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    labels = value.rstrip(".").split(".")
    if all(_LABEL.fullmatch(label) for label in labels) and not labels[-1].isdigit():
        return value.rstrip(".").lower()
    return None


def check_port(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        return None
    return value


def check_call(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    call = value.strip().upper()
    return call if _LOGIN_CALL.fullmatch(call) else None
