# SPDX-License-Identifier: GPL-3.0-or-later
"""RF power from the radio's power meter (CI-V ``15 11``).

The meter level (0-255) is converted to watts by piecewise linear
interpolation over calibration points measured on the radio
(``radio.power_meter_watts``). The scale is not extended beyond the last
point: the G90's meter reaches the end of its scale at about 3 W, so a level
above the last point only says "more than" its watts.
"""

import itertools
import math
from collections.abc import Sequence


def meter_to_watts(points: Sequence[tuple[int, float]], value: int) -> tuple[float | None, bool]:
    """Watts for a meter level, and whether the power is above the calibrated range.

    Without calibration points the watts are ``None``.
    """
    if len(points) < 2:
        return None, False
    if value <= points[0][0]:
        return points[0][1], False
    for (m0, w0), (m1, w1) in itertools.pairwise(points):
        if value <= m1:
            return w0 + (w1 - w0) * (value - m0) / (m1 - m0), False
    return points[-1][1], True


# RF power setting (CI-V 14 0A): level 0-255 over the G90's 1-20 W range, set
# in whole watts. The radio truncates both ways (measured 2026-11-03/04): it
# reports 5 W as level 53, not 54, and it turns a written level into whole
# watts rounding down (53 written gives 4 W, reported back as 40). So the
# middle of a reported level's interval gives the watts, and the smallest
# level reaching the watts is written.
SETTING_MIN_W = 1.0
SETTING_MAX_W = 20.0


def watts_to_setting(watts: int) -> int:
    """The level to write for whole watts: the smallest one the radio reads as them."""
    level = math.ceil((watts - SETTING_MIN_W) * 255 / (SETTING_MAX_W - SETTING_MIN_W) - 1e-9)
    return max(0, min(255, level))


def setting_whole_watts(level: int) -> int:
    """Whole watts of a reported setting level (the radio sets whole watts)."""
    return round(setting_to_watts(level))


def setting_to_watts(level: int) -> float:
    """Watts of an RF power setting level."""
    watts = SETTING_MIN_W + (SETTING_MAX_W - SETTING_MIN_W) * (level + 0.5) / 255
    return min(SETTING_MAX_W, watts)
