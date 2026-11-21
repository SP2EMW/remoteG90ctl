# SPDX-License-Identifier: GPL-3.0-or-later
"""Waterfall lines for browsers: binary frames, bin pooling, per-client queues.

Wire format of one line, version 1, little-endian (docs/server-protocol.md):

====== ======= ===============================================================
offset type    field
====== ======= ===============================================================
0      u8      frame type, ``0x01`` = waterfall line
1      u8      flags: bit 0 = centre frequency calibrated (offset known for mode),
               bit 1 = blanked: the radio transmits or switches TX/RX (D33)
2      u16     number of bins ``n``
4      u32     line sequence number
8      f64     centre frequency in Hz (CAT frequency + I/Q centre offset), or NaN
16     f32     span in Hz (the sample rate)
20     f32     dB value of byte 0
24     f32     dB value of byte 255
28     u8[n]   bins from the lowest to the highest frequency
====== ======= ===============================================================

A slow client never makes the server buffer without bound: each subscriber
has a short queue and the oldest line is dropped (and counted) when it is full.

A subscriber may ask for fewer lines per second (``speed``, for slow mobile
links): every ``n`` consecutive lines are merged into one by their per-bin
maximum, so a short signal is not lost between the lines that are sent, and the
merged line is blanked when any of its lines was.
"""

import asyncio
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from remoteg90ctl.iq.dsp import Spectrum, quantize

FRAME_WATERFALL = 0x01
FLAG_CALIBRATED = 0x01
FLAG_BLANKED = 0x02
HEADER = struct.Struct("<BBHIdfff")
QUEUE_LINES = 8
MIN_BINS, MAX_BINS = 256, 16_384
# dB range carried by the 8-bit values: 0.47 dB per step, from below the noise
# floor of the G90's I/Q output to near full scale (AGENTS.md section 8).
MIN_DB, MAX_DB = -130.0, -10.0
# Waterfall speeds: how many consecutive lines are merged into one sent line.
# "fast" sends every line (iq.line_rate per second); with line_rate 20 the
# speeds are 20, 10, 5, 2 and 1 lines per second.
SPEEDS: dict[str, int] = {"fast": 1, "medium": 2, "slow": 4, "slower": 10, "slowest": 20}
DEFAULT_SPEED = "fast"


def pool_bins(line: Spectrum, bins: int) -> Spectrum:
    """Reduce a line to ``bins`` values, keeping the maximum of each group.

    The maximum, not the mean, so a narrow carrier stays visible when zoomed out.
    """
    if bins >= line.size:
        return line
    if line.size % bins:
        raise ValueError("bins must divide the line length")
    group = line.size // bins
    if group & (group - 1) == 0:
        # Halving by pairs: contiguous element-wise maxima, much cheaper than a
        # reduction over short rows (the common case: both are powers of two).
        pooled: Spectrum = line
        while pooled.size > bins:
            pooled = np.maximum(pooled[0::2], pooled[1::2])
        return pooled
    reduced: Spectrum = line.reshape(bins, group).max(axis=1)
    return reduced


@dataclass(frozen=True, slots=True)
class LineInfo:
    """What a browser needs to place a line on the frequency axis."""

    center_hz: float | None
    calibrated: bool
    span_hz: float
    blanked: bool = False
    min_db: float = MIN_DB
    max_db: float = MAX_DB


def encode_line(seq: int, line: Spectrum, info: LineInfo) -> bytes:
    flags = FLAG_CALIBRATED if info.calibrated and info.center_hz is not None else 0
    if info.blanked:
        flags |= FLAG_BLANKED
    center = float("nan") if info.center_hz is None else info.center_hz
    header = HEADER.pack(
        FRAME_WATERFALL,
        flags,
        line.size,
        seq & 0xFFFFFFFF,
        center,
        info.span_hz,
        info.min_db,
        info.max_db,
    )
    return header + quantize(line, info.min_db, info.max_db).tobytes()


def decode_header(frame: bytes) -> dict[str, object]:
    """Inverse of the header part of :func:`encode_line` (tests, tools)."""
    kind, flags, bins, seq, center, span, min_db, max_db = HEADER.unpack_from(frame)
    return {
        "type": kind,
        "calibrated": bool(flags & FLAG_CALIBRATED),
        "blanked": bool(flags & FLAG_BLANKED),
        "bins": bins,
        "seq": seq,
        "center_hz": None if np.isnan(center) else center,
        "span_hz": span,
        "min_db": min_db,
        "max_db": max_db,
        "data": frame[HEADER.size :],
    }


@dataclass(eq=False)
class Subscriber:
    bins: int
    every: int = 1  # lines merged into one sent line (SPEEDS)
    queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(QUEUE_LINES))
    dropped: int = 0
    _merged: Spectrum | None = None
    _merged_count: int = 0
    _merged_blanked: bool = False

    def offer(self, frame: bytes) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest: the newest line matters most
            self.dropped += 1
        self.queue.put_nowait(frame)

    def merge(self, seq: int, line: Spectrum, info: LineInfo) -> bytes | None:
        """Add a pooled line; return the merged frame once ``every`` lines are in."""
        merged: Spectrum = line if self._merged is None else np.maximum(self._merged, line)
        self._merged = merged
        self._merged_blanked = self._merged_blanked or info.blanked
        self._merged_count += 1
        if self._merged_count < self.every:
            return None
        frame = encode_line(seq, self._merged, replace(info, blanked=self._merged_blanked))
        self._merged, self._merged_count, self._merged_blanked = None, 0, False
        return frame


class WaterfallHub:
    """Fans each new spectrum line out to the subscribed browsers."""

    def __init__(self, center: Callable[[], tuple[float | None, bool]], span_hz: float) -> None:
        self._center = center  # (centre frequency or None, calibrated)
        self._span_hz = span_hz
        self._subscribers: set[Subscriber] = set()
        self._seq = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def wanted(self) -> bool:
        """Whether any browser watches: the I/Q service computes lines only then."""
        return bool(self._subscribers)

    def subscribe(self, bins: int, every: int = 1) -> Subscriber:
        subscriber = Subscriber(bins, every)
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def on_line(self, line: Spectrum, blanked: bool = False) -> None:
        """Send a line to the subscribers; ``blanked`` marks a line taken in TX."""
        self._seq += 1
        if not self._subscribers:
            return
        center, calibrated = self._center()
        info = LineInfo(center, calibrated, self._span_hz, blanked)
        pooled: dict[int, Spectrum] = {}
        frames: dict[int, bytes] = {}  # full-speed frames, shared by bin count
        for subscriber in self._subscribers:
            if subscriber.bins not in pooled:
                pooled[subscriber.bins] = pool_bins(line, subscriber.bins)
            if subscriber.every == 1:
                if subscriber.bins not in frames:
                    frames[subscriber.bins] = encode_line(self._seq, pooled[subscriber.bins], info)
                subscriber.offer(frames[subscriber.bins])
            elif (frame := subscriber.merge(self._seq, pooled[subscriber.bins], info)) is not None:
                subscriber.offer(frame)
