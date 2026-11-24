# SPDX-License-Identifier: GPL-3.0-or-later
"""RTTY transmission (decisions.md D61; AGENTS.md section 10): the signal is made here.

45.45 baud, 170 Hz shift, 1.5 stop bits, Baudot (ITA2 with the US figures),
continuous-phase FSK. The page holds an ordinary lease and renews it (AGENTS.md
rules 4-7) and sends the text as the operator types it; the server keys it
out. While the text runs out and the lease lives, LTRS characters keep the
others' receivers in step (idle). The audio reaches the radio only through
``TxAudio`` during the lease and through its limiter, and stops the moment the
lease ends, whatever ends it; the queue goes with it.

Our own code (AGENTS.md section 6: transmit paths are never copied).
"""

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable

import numpy as np
import numpy.typing as npt

from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

BAUD = 45.45
SHIFT_HZ = 170.0
# The whole signal (mark, space and their sidebands, about 100 Hz each side)
# inside the radio's transmit audio band, 200-2800 Hz (AGENTS.md section 10).
MARK_MIN_HZ = 300.0
MARK_MAX_HZ = 2800.0 - SHIFT_HZ - 100.0
LEVEL_MIN_DB = -40.0
QUEUE_MAX = 1000  # characters waiting to be sent
LEAD_S = 0.3  # mark before the first character, for the others' receivers
TAIL_S = 0.2  # mark after the last one
RAMP_S = 0.005  # level up at the start and down at the end: no click
FRAME_SAMPLES = AUDIO_SAMPLE_RATE // 50  # 20 ms, as the voice path
# Pacing, as the FT8/FT4 playback (ft8_tx.play): a few frames queued for the
# card, which sets the real pace; a full queue is waited out at most
# MAX_STALL_S before the signal is given up.
FRAMES_AHEAD = 4
DRAIN_S = 0.15
MAX_STALL_S = 1.0
STALL_STEP_S = 0.02

# Baudot code, letters and figures; index = the 5 bits, the first bit sent
# lowest. The same tables as the page's decoder (web/src/rtty.ts).
_LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x1bMXV\x1f"
_FIGS = "\x003\n- \x0787\r$4',!:(5\")2#6019?&\x1b./;\x1f"
CODE_LF = 0x02
CODE_SPACE = 0x04
CODE_CR = 0x08
CODE_FIGS = 0x1B
CODE_LTRS = 0x1F
_LETTERS = {c: i for i, c in enumerate(_LTRS) if "A" <= c <= "Z"}
_FIGURES = {c: i for i, c in enumerate(_FIGS) if c not in "\0\n \x07\r\x1b\x1f"}
# What the page may send: these, a space and a new line (lower case is raised).
ALLOWED = frozenset(_LETTERS) | frozenset(_FIGURES) | {" ", "\n"}


class RttyError(ValueError):
    """A refused request: the code is a server protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def clean_text(text: object) -> str:
    """The text in capitals; RttyError("invalid_value") for anything else."""
    if not isinstance(text, str):
        raise RttyError("invalid_value", "'text' must be a string")
    upper = text.upper().replace("\r\n", "\n").replace("\r", "\n")
    bad = sorted({c for c in upper if c not in ALLOWED})
    if bad:
        shown = " ".join(repr(c) for c in bad[:5])
        raise RttyError(
            "invalid_value",
            f"'text': not in the Baudot code: {shown}"
            " (A-Z 0-9 space and - ? : ( ) . , ' / \" $ ! & # ;)",
        )
    return upper


class Encoder:
    """Characters to Baudot codes, with the shifts they need."""

    def __init__(self) -> None:
        self.figures = False

    def codes(self, char: str) -> list[int]:
        if char == "\n":
            return [CODE_CR, CODE_LF]
        if char == " ":
            # Receivers go back to letters on a space (unshift on space), so
            # a figure after it needs FIGS again.
            self.figures = False
            return [CODE_SPACE]
        if char in _LETTERS:
            shift = [CODE_LTRS] if self.figures else []
            self.figures = False
            return [*shift, _LETTERS[char]]
        shift = [] if self.figures else [CODE_FIGS]
        self.figures = True
        return [*shift, _FIGURES[char]]


# One bit to key: (mark?, its length in bits); None marks a character sent.
_Bit = tuple[bool, float] | None


def _frame(code: int) -> list[_Bit]:
    """Start bit, 5 data bits (lowest first), 1.5 stop bits."""
    return [(False, 1.0), *((bool(code >> b & 1), 1.0) for b in range(5)), (True, 1.5)]


class RttyTransmitter:
    """The signal of one transmission, made 20 ms at a time as it is played."""

    def __init__(self, mark_hz: object, reverse: object, level_db: object) -> None:
        mark = _number(mark_hz)
        if mark is None or not MARK_MIN_HZ <= mark <= MARK_MAX_HZ:
            raise RttyError(
                "invalid_value", f"'mark_hz' must be {MARK_MIN_HZ:.0f}-{MARK_MAX_HZ:.0f} Hz"
            )
        if not isinstance(reverse, bool):
            raise RttyError("invalid_value", "'reverse' must be true or false")
        level = _number(level_db)
        if level is None or not LEVEL_MIN_DB <= level <= 0:
            raise RttyError("invalid_value", f"'level_db' must be {LEVEL_MIN_DB:.0f} to 0")
        self.mark_hz = mark
        self.reverse = reverse
        self.level_db = level
        self._amplitude = 32767 * 10 ** (level / 20)
        self._mark_tone = mark + SHIFT_HZ if reverse else mark
        self._space_tone = mark if reverse else mark + SHIFT_HZ
        self._encoder = Encoder()
        self._text: deque[str] = deque()
        self._bits: deque[_Bit] = deque([(True, LEAD_S * BAUD)])
        for _ in range(2):  # letters, whatever the others' receivers were in
            self._bits.extend(_frame(CODE_LTRS))
        self._ending = False
        self._tail_queued = False
        self._bit_left = 0.0  # samples of the current bit still to make
        self._bit_mark = True
        self._phase = 0.0
        self._made = 0  # samples made
        self._ramp_down_at: int | None = None  # sample at which the level falls
        self.sent = 0  # characters of the text keyed out completely
        self.queued = 0  # characters ever taken in
        self.done = False

    @property
    def waiting(self) -> int:
        return len(self._text)

    def add(self, text: object) -> int:
        """Queues text; the number waiting. RttyError for bad text or overflow."""
        if self._ending:
            raise RttyError("invalid_value", "the transmission is ending")
        clean = clean_text(text)
        if len(self._text) + len(clean) > QUEUE_MAX:
            raise RttyError("invalid_value", f"at most {QUEUE_MAX} characters may wait")
        self._text.extend(clean)
        self.queued += len(clean)
        return len(self._text)

    def end(self) -> None:
        """Send what waits, then the tail; ``done`` after that."""
        self._ending = True

    def frame(self, samples: int = FRAME_SAMPLES) -> npt.NDArray[np.int16]:
        """The next ``samples`` of the signal (silence once done)."""
        out = np.zeros(samples, dtype=np.float64)
        per_bit = AUDIO_SAMPLE_RATE / BAUD
        ramp = RAMP_S * AUDIO_SAMPLE_RATE
        i = 0
        while i < samples and not self.done:
            while self._bit_left <= 0:
                self._bit_mark, length = self._next_bit()
                self._bit_left += length * per_bit
            # A run of samples of one bit; the fraction of a bit carries over,
            # so the bit timing stays exact over any length.
            n = min(samples - i, math.ceil(self._bit_left))
            if self._ramp_down_at is not None:
                n = min(n, self._ramp_down_at - self._made)
                if n <= 0:
                    self.done = True
                    break
            tone = self._mark_tone if self._bit_mark else self._space_tone
            step = 2 * math.pi * tone / AUDIO_SAMPLE_RATE
            phases = self._phase + step * np.arange(1, n + 1)
            index = self._made + np.arange(n)
            gain = np.minimum(1.0, (index + 1) / ramp)
            if self._ramp_down_at is not None:
                gain = np.minimum(gain, np.maximum(0.0, (self._ramp_down_at - index) / ramp))
            out[i : i + n] = gain * np.sin(phases)
            self._phase = float(phases[-1] % (2 * math.pi))
            self._made += n
            self._bit_left -= n
            i += n
        return np.round(out * self._amplitude).astype(np.int16)

    def _next_bit(self) -> tuple[bool, float]:
        """The next bit to key: the text, then idle LTRS or the tail."""
        while True:
            if self._bits:
                bit = self._bits.popleft()
                if bit is None:
                    self.sent += 1
                    continue
                return bit
            if self._text:
                char = self._text.popleft()
                for code in self._encoder.codes(char):
                    self._bits.extend(_frame(code))
                self._bits.append(None)
            elif self._ending and not self._tail_queued:
                self._tail_queued = True
                self._bits.append((True, TAIL_S * BAUD))
                # The level falls over the last RAMP_S of the tail.
                self._ramp_down_at = self._made + round(TAIL_S * AUDIO_SAMPLE_RATE)
            elif self._ending:
                # The tail is keyed; the ramp (frame) ends the signal.
                return (True, 1.0)
            else:
                # Nothing to send and no end asked: idle on LTRS.
                self._bits.extend(_frame(CODE_LTRS))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


Pcm = npt.NDArray[np.int16]
# Offers a frame to the radio's audio path; False when it was not taken: the
# lease is gone, or the card's queue is full for the moment.
Feed = Callable[[Pcm], bool]


async def key(  # noqa: PLR0913  (the clocks and the sleep are injected for the tests)
    signal: RttyTransmitter,
    feed: Feed,
    *,
    alive: Callable[[], bool],
    on_sent: Callable[[int, int], None] = lambda _sent, _waiting: None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Feeds the signal 20 ms at a time at its pace until it is done.

    True when it ended as asked (``end`` and the tail). False when the lease
    is gone (``alive()`` false) or the card took nothing for MAX_STALL_S:
    then nothing more is made, and what waited is dropped with the signal.
    ``on_sent(sent, waiting)`` is called whenever another character is out.
    """
    frame_s = FRAME_SAMPLES / AUDIO_SAMPLE_RATE
    began = monotonic()
    made = 0
    reported = 0
    while not signal.done:
        frame = signal.frame()
        stalled = 0.0
        while not feed(frame):
            if not alive() or stalled >= MAX_STALL_S:
                logger.warning(
                    "rtty_stopped",
                    extra=kv(sent=signal.sent, waiting=signal.waiting, alive=alive()),
                )
                return False
            await sleep(STALL_STEP_S)
            stalled += STALL_STEP_S
        made += len(frame)
        if signal.sent != reported:
            reported = signal.sent
            on_sent(reported, signal.waiting)
        ahead = made / AUDIO_SAMPLE_RATE - (monotonic() - began)
        if ahead > FRAMES_AHEAD * frame_s:
            await sleep(ahead - FRAMES_AHEAD * frame_s / 2)
    # Let the card play what is queued before the page gives the lease back.
    await sleep(max(0.0, made / AUDIO_SAMPLE_RATE - (monotonic() - began)) + DRAIN_S)
    return True
