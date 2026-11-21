# SPDX-License-Identifier: GPL-3.0-or-later
"""FT8/FT4 transmission (stage 7, decisions.md D53): the signal is made here.

The page decides what to send (its sequencer follows WSJT-X); the server checks
the message and the tone, makes the waveform with ft8lib's encoder
(GPL-3.0-or-later) and plays it from 0.5 s after the cycle start by this
server's clock. Every rule of the voice path stays: the page holds an ordinary
lease and renews it (AGENTS.md rules 4-7), the audio reaches the radio only
through ``TxAudio`` during that lease and through its limiter, and playback
stops the moment the lease ends, whatever ends it.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import ft8lib.pack  # type: ignore[import-untyped]  # ft8lib ships no type information
import numpy as np
import numpy.typing as npt
from ft8lib import (
    ft4_tones_from_bits,
    ft8_tones_from_bits,
    gen_ft4wave,
    gen_ft8wave,
    pack77,
    unpack77,
)

from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

CYCLE_S = {"FT8": 15.0, "FT4": 7.5}
# Width of the signal above its lowest tone: 8 tones of 6.25 Hz, 4 of 20.83 Hz.
BANDWIDTH_HZ = {"FT8": 50.0, "FT4": 83.3}
# The signal must stay inside the radio's transmit audio band (AGENTS.md
# stage 7); the edges are those of the page's default transmit filter.
TONE_MIN_HZ = 200.0
TONE_MAX_HZ = 2800.0
TX_DELAY_S = 0.5  # transmissions start 0.5 s into the cycle, as in WSJT-X
# A request may come this late after the signal's usual start and still be
# sent: what the others' decoders still take (a WSJT-X port decoded FT8 1.5 s
# and FT4 0.9 s late, not later; FT4 review 2026-11-15).
MAX_LATE_S = {"FT8": 1.5, "FT4": 0.8}
MAX_AHEAD_S = 16.0
LEVEL_MIN_DB = -40.0
FRAME_SAMPLES = AUDIO_SAMPLE_RATE // 50  # 20 ms, as the voice path
FRAMES_AHEAD = 4  # kept queued for the card, well under TxAudio's queue
DRAIN_S = 0.15  # the card's own buffer after the last frame
MESSAGE = re.compile(r"[A-Z0-9 /+\-.?<>]{1,37}")


class Ft8Error(ValueError):
    """A refused transmission: the code is a server protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def chkcall(w: str) -> tuple[str, bool]:
    """Whether ``w`` is a standard or compound callsign: (base call, ok).

    ft8lib 1.3.2's ``ft8lib.pack.chkcall`` (GPL-3.0-or-later, a port of
    WSJT-X's lib/chkcall.f90, GPL-3.0) with one correction: in WSJT-X the two
    tests for the call area digit (2nd and 3rd character) are independent, the
    3rd wins; ft8lib tests the 2nd first and stops, so S51A, A71AB or E73M were
    "not a callsign", went as free text and were refused (FT8 review
    2026-11-15). The round trip in make_signal still checks every message.
    """
    w = w.strip().upper()
    bc = w[:6]
    n1 = len(w)
    if n1 > 11 or any(ch in w for ch in ".+-?"):
        return bc, False
    if n1 > 6 and "/" not in w:
        return bc, False
    i0 = w.find("/") + 1  # 1-based like Fortran (0 if absent)
    if i0 and max(i0 - 1, n1 - i0) > 6:
        return bc, False
    if 2 <= i0 <= n1 - 1:
        bc = w[i0:] if i0 - 1 <= n1 - i0 else w[: i0 - 1]
    if len(bc) > 6:
        return bc, False
    if not (bc[:1].isalpha() or bc[1:2].isalpha()):
        return bc, False
    if bc.startswith("Q") and bc[:5] != "QU1RK":
        return bc, False
    i1 = 0
    if len(bc) >= 2 and bc[1].isdigit():
        i1 = 2
    if len(bc) >= 3 and bc[2].isdigit():
        i1 = 3
    if i1 == 0 or i1 == len(bc):
        return bc, False
    sfx = bc[i1:]
    if not sfx.isalpha() or not (1 <= len(sfx) <= 3):
        return bc, False
    return bc, True


# pack77 looks chkcall up in its module at each call.
ft8lib.pack.chkcall = chkcall


@dataclass(frozen=True)
class Ft8Signal:
    mode: str
    message: str
    tone_hz: float
    start_utc: float  # cycle start, s
    pcm: npt.NDArray[np.int16]

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / AUDIO_SAMPLE_RATE


def make_signal(
    mode: object, message: object, tone_hz: object, level_db: object, start_ms: object
) -> Ft8Signal:
    """Checks every field; raises Ft8Error("invalid_value", ...)."""
    if not isinstance(mode, str) or mode not in CYCLE_S:
        raise Ft8Error("invalid_value", "'mode' must be FT8 or FT4")
    text = " ".join(message.upper().split()) if isinstance(message, str) else ""
    if not MESSAGE.fullmatch(text):
        raise Ft8Error(
            "invalid_value", "'message': up to 37 characters of A-Z 0-9 space / + - . ? < >"
        )
    highest = TONE_MAX_HZ - BANDWIDTH_HZ[mode]
    tone = _number(tone_hz)
    if tone is None or not TONE_MIN_HZ <= tone <= highest:
        raise Ft8Error(
            "invalid_value", f"'tone_hz' must be {TONE_MIN_HZ:.0f}-{highest:.0f} Hz for {mode}"
        )
    level = _number(level_db)
    if level is None or not LEVEL_MIN_DB <= level <= 0:
        raise Ft8Error("invalid_value", f"'level_db' must be {LEVEL_MIN_DB:.0f} to 0")
    start = _number(start_ms)
    if start is None or start % (CYCLE_S[mode] * 1000) != 0:
        raise Ft8Error("invalid_value", f"'start_ms' must be a {mode} cycle start (UTC ms)")
    bits = pack77(text)
    decoded, ok = unpack77(bits, nrx=0)
    if not ok or " ".join(str(decoded).split()) != text:
        # ft8lib shortens what does not fit (free text: 13 characters).
        raise Ft8Error("invalid_value", f"'{text}' cannot be sent as one {mode} message")
    if mode == "FT8":
        wave = gen_ft8wave(ft8_tones_from_bits(bits), f0=tone, fsample=AUDIO_SAMPLE_RATE)
    else:
        wave = gen_ft4wave(ft4_tones_from_bits(bits), f0=tone, fsample=AUDIO_SAMPLE_RATE)
    amplitude = 32767 * 10 ** (level / 20)
    pcm = np.round(np.asarray(wave, dtype=np.float64) * amplitude).astype(np.int16)
    return Ft8Signal(mode, text, tone, start / 1000, pcm)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def check_timing(signal: Ft8Signal, now_utc: float) -> None:
    """Raises Ft8Error("too_late" / "invalid_value") for a cycle not to be sent now."""
    start = signal.start_utc + TX_DELAY_S
    if now_utc > start + MAX_LATE_S[signal.mode]:
        raise Ft8Error("too_late", f"the {signal.mode} cycle has already started")
    if start - now_utc > MAX_AHEAD_S:
        raise Ft8Error("invalid_value", "'start_ms' is too far ahead")


Pcm = npt.NDArray[np.int16]
# Offers a frame to the radio's audio path; False when it was not taken: the
# lease is gone, or the card's queue is full for the moment.
Feed = Callable[[Pcm], bool]
# A full queue is waited out this long, frame by frame, before the signal is
# given up (code review 2026-11-10, point 1): a stall of the event loop makes
# the pacing catch up in a burst, which must not end a good transmission.
MAX_STALL_S = 1.0
STALL_STEP_S = 0.02


async def play(  # noqa: PLR0913  (the clocks and the sleep are injected for the tests)
    signal: Ft8Signal,
    feed: Feed,
    *,
    alive: Callable[[], bool] = lambda: True,
    wall_clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Waits for the start, then feeds 20 ms frames at their pace.

    True when all went out. False when the lease is gone (``alive()`` false)
    or the card took nothing for MAX_STALL_S. The start is by the wall clock
    (cycles are UTC); the pace by the monotonic one, so a step of the wall
    clock in the middle does not disturb it.
    """
    pace = monotonic if monotonic is not None else wall_clock
    start = signal.start_utc + TX_DELAY_S
    wait = start - wall_clock()
    if wait > 0:
        await sleep(wait)
    pcm = signal.pcm
    began = pace()
    sent = 0
    for index in range(0, len(pcm), FRAME_SAMPLES):
        frame = pcm[index : index + FRAME_SAMPLES]
        stalled = 0.0
        while not feed(frame):
            if not alive() or stalled >= MAX_STALL_S:
                logger.warning(
                    "ft8_stopped",
                    extra=kv(mode=signal.mode, sent_s=sent / AUDIO_SAMPLE_RATE, alive=alive()),
                )
                return False
            await sleep(STALL_STEP_S)
            stalled += STALL_STEP_S
        sent += len(frame)
        # Keep a few frames queued for the card, which sets the real pace.
        ahead = sent / AUDIO_SAMPLE_RATE - (pace() - began)
        if ahead > FRAMES_AHEAD * FRAME_SAMPLES / AUDIO_SAMPLE_RATE:
            await sleep(ahead - FRAMES_AHEAD * FRAME_SAMPLES / AUDIO_SAMPLE_RATE / 2)
    # Let the card play what is queued before the page gives the lease back.
    await sleep(max(0.0, sent / AUDIO_SAMPLE_RATE - (pace() - began)) + DRAIN_S)
    return True
