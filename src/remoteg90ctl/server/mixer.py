# SPDX-License-Identifier: GPL-3.0-or-later
"""The output level of the DE-19's sound card (``amixer``), kept for the station.

The card's playback volume comes after the server's limiter, so it sets how
hard the radio is driven (its ALC), for voice and FT8/FT4 alike. The program
did not set it, so it stayed wherever the system left it (on the S740: -20 dB,
FT8 drove the ALC to 18 instead of about 60; owner, 2026-11-14). The page sets
it now, also while transmitting (owner's decision 2026-11-14: tuned watching
the ALC), and the server keeps it in its state directory and sets it again
whenever the card opens.

Measured on the owner's DE-19 (C-Media, 2026-11-14): the control is "Speaker",
0-37 in 1 dB steps, 37 = 0 dB.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

OUTPUT_CONTROL = "Speaker"
OUTPUT_MIN_DB = -37.0
OUTPUT_MAX_DB = 0.0
OUTPUT_FILE = "audio-output.json"

# Runs amixer with these arguments: (exit status, output).
Runner = Callable[..., Awaitable[tuple[int, str]]]

_DB = re.compile(r"Playback\s+-?\d+\s+\[\d+%\]\s+\[(-?\d+(?:\.\d+)?)dB\]")


async def run_amixer(*args: str) -> tuple[int, str]:
    """``amixer -q``-free run: the output is parsed for the level."""
    try:
        process = await asyncio.create_subprocess_exec(
            "amixer",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 1, str(exc)
    out, _ = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace")


def card_of(device: str | None) -> str | None:
    """``G90AUDIO`` from ``hw:CARD=G90AUDIO,DEV=0``; None without a card name."""
    if device is None or "CARD=" not in device:
        return None
    return device.split("CARD=", 1)[1].split(",", 1)[0]


def playback_db(text: str) -> float | None:
    """The playback level in dB from ``amixer sget``'s output (first channel)."""
    match = _DB.search(text)
    return float(match.group(1)) if match else None


class OutputLevel:
    """Reads, sets and keeps the card's output level."""

    def __init__(
        self, card: str | None, state_dir: Path | None, runner: Runner = run_amixer
    ) -> None:
        self._card = card
        self._path = state_dir / OUTPUT_FILE if state_dir is not None else None
        self._run = runner

    @property
    def available(self) -> bool:
        return self._card is not None

    def saved(self) -> float | None:
        if self._path is None:
            return None
        try:
            value = json.loads(self._path.read_text())["db"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return min(OUTPUT_MAX_DB, max(OUTPUT_MIN_DB, float(value)))

    def _save(self, db: float) -> None:
        if self._path is None:
            return
        temporary = self._path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"db": db}))
            os.replace(temporary, self._path)
        except OSError as exc:
            logger.warning("audio_output_not_saved", extra=kv(error=exc))
            with contextlib.suppress(OSError):
                temporary.unlink()

    async def read(self) -> float | None:
        if self._card is None:
            return None
        status, out = await self._run("-c", self._card, "sget", OUTPUT_CONTROL)
        return playback_db(out) if status == 0 else None

    async def set(self, db: float, *, keep: bool = True) -> float | None:
        """Set ``db`` (clamped to the card's range); the level the card then has."""
        if self._card is None:
            return None
        # Whole decibels: amixer refuses "1e-05dB", which "{:g}" makes of a value
        # near zero (audit 2026-11-15, A3); the control's step is 1 dB anyway.
        db = round(min(OUTPUT_MAX_DB, max(OUTPUT_MIN_DB, db)))
        # "--": amixer's option parser takes a negative value ("-20dB") for
        # options and refuses the command (S740, 2026-11-14).
        status, out = await self._run("-c", self._card, "sset", OUTPUT_CONTROL, "--", f"{db:g}dB")
        if status != 0:
            logger.warning("audio_output_failed", extra=kv(error=out.strip()))
            return None
        level = playback_db(out)
        if keep and level is not None:
            self._save(level)
        return level

    async def restore(self) -> None:
        """When the card opens: the kept level, if there is one."""
        saved = self.saved()
        if saved is None or not self.available:
            return
        level = await self.set(saved, keep=False)
        if level is None:
            logger.warning("audio_output_not_restored", extra=kv(kept_db=saved))
        else:
            logger.info("audio_output_restored", extra=kv(db=level))
