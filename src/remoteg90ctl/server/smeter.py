# SPDX-License-Identifier: GPL-3.0-or-later
"""S-meter: the G90's 0-255 level (CI-V 15 02) as S units.

Measured on the owner's G90 (firmware 1.81, 2026-11-07), with a tinySA and
the RF gain to move the needle: the level is not linear in dB, so it is
converted by interpolating between the measured points. Below the scale the
radio sends 242, also without an antenna (probably -14 as an unsigned byte):
every level from BELOW_SCALE_FROM up counts as S0.
"""

import itertools

# (level, dB relative to S9), measured; one S unit is 6 dB.
MEASURED: tuple[tuple[int, float], ...] = (
    (0, -54.0),  # S0: UNVERIFIED, the radio sends 242 below the scale
    (22, -48.0),  # S1
    (44, -36.0),  # S3
    (73, -24.0),  # S5 (antenna noise; read as "about S5")
    (106, -12.0),  # S7
    (136, 0.0),  # S9
    (160, 20.0),  # S9+20
    (174, 40.0),  # S9+40
)
BELOW_SCALE_FROM = 200
S0_DB = MEASURED[0][1]
MAX_DB = 60.0  # beyond the last point the last slope is used: UNVERIFIED


def level_to_db(level: int) -> float:
    """dB relative to S9 of a 15 02 level."""
    if level >= BELOW_SCALE_FROM or level <= 0:
        return S0_DB
    for (low, low_db), (high, high_db) in itertools.pairwise(MEASURED):
        if level <= high:
            return low_db + (level - low) * (high_db - low_db) / (high - low)
    (low, low_db), (high, high_db) = MEASURED[-2], MEASURED[-1]
    slope = (high_db - low_db) / (high - low)
    return min(MAX_DB, high_db + (level - high) * slope)


def db_to_text(db: float) -> str:
    """As the radio shows it: S0-S9, then S9+10, S9+20 … (rounded to 10 dB)."""
    if db <= 0:
        return f"S{max(0, round(9 + db / 6))}"
    over = round(db / 10) * 10
    return "S9" if over == 0 else f"S9+{over}"
