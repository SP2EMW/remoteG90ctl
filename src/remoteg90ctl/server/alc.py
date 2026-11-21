# SPDX-License-Identifier: GPL-3.0-or-later
"""ALC meter: the G90's 0-255 level (CI-V 15 13) as the number on its display.

Measured on the owner's G90 (firmware 1.81, 2026-11-08), U-D, transmitting
from the browser with a steady "ooo" held for several seconds: display 5-6 at
level 7, 30 at 40, 82 at 95, and still 82 at 99 (the loudest possible: the
ALC limits the signal there). The level is converted by interpolating between
the measured points; earlier comparisons with speech were unreliable (the
display and the 0.5 s reading saw different moments).
"""

import itertools

# (level, number on the radio's display), measured.
MEASURED: tuple[tuple[int, float], ...] = (
    (0, 0.0),  # silence and receive: both 0
    (7, 5.5),
    (40, 30.0),
    (95, 82.0),
    (99, 82.0),  # the loudest possible
)


def level_to_display(level: int) -> int:
    """The radio's ALC display for a 15 13 level; above the last point: UNVERIFIED, held."""
    if level <= 0:
        return 0
    for (low, low_shown), (high, high_shown) in itertools.pairwise(MEASURED):
        if level <= high:
            return round(low_shown + (level - low) * (high_shown - low_shown) / (high - low))
    return round(MEASURED[-1][1])
