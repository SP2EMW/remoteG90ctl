# SPDX-License-Identifier: GPL-3.0-or-later
"""Amateur bands and band plans by ITU region (decisions.md D54).

* ``BANDS``: the amateur service's regional allocations from 1.8 to 30 MHz in
  the ITU Radio Regulations, Article 5 — without national footnotes (owner's
  decision: regional only; e.g. 1850-2000 kHz in Region 1 is a national
  allocation under footnote 5.96, not a regional one). The server refuses a
  browser's transmission whose signal would leave them.
* ``PLANS``: the IARU band plans, for display only (they are recommendations):
  Region 1 HF band plan effective 16 October 2020; Region 2 band plan,
  September 2020; Region 3 R3-004 Rev. 2, November 2024.
* ``region_of``: the region of a Maidenhead locator, from the ITU lines A, B
  and C (Radio Regulations No. 5.3-5.9) and their exceptions: Iran in Region
  3; the former USSR, Mongolia and Turkey in Region 1. The borders are drawn
  coarsely (a 4-character square is 2 x 1 degrees); see docs/third-party.md.

No CI-V and no I/O here: pure data and functions, shared by the server and
the tests.
"""

import math
import re
from dataclasses import dataclass

LOCATOR = re.compile(r"[A-R]{2}[0-9]{2}([A-X]{2})?")


@dataclass(frozen=True, slots=True)
class Segment:
    low_hz: int
    high_hz: int
    kind: str  # cw, narrow, all, beacon, satellite, fm, guard
    note: str = ""


def _khz(low: float, high: float) -> tuple[int, int]:
    return round(low * 1000), round(high * 1000)


# ITU regional allocations to the amateur service, 1.8-30 MHz (Article 5).
BANDS: dict[int, tuple[tuple[int, int], ...]] = {
    1: tuple(
        _khz(*b)
        for b in [
            (1810, 1850),
            (3500, 3800),
            (5351.5, 5366.5),
            (7000, 7200),
            (10100, 10150),
            (14000, 14350),
            (18068, 18168),
            (21000, 21450),
            (24890, 24990),
            (28000, 29700),
        ]
    ),
    2: tuple(
        _khz(*b)
        for b in [
            (1800, 2000),
            (3500, 4000),
            (5351.5, 5366.5),
            (7000, 7300),
            (10100, 10150),
            (14000, 14350),
            (18068, 18168),
            (21000, 21450),
            (24890, 24990),
            (28000, 29700),
        ]
    ),
    3: tuple(
        _khz(*b)
        for b in [
            (1800, 2000),
            (3500, 3900),
            (5351.5, 5366.5),
            (7000, 7200),
            (10100, 10150),
            (14000, 14350),
            (18068, 18168),
            (21000, 21450),
            (24890, 24990),
            (28000, 29700),
        ]
    ),
}

_P = tuple[float, float, str, str]


def _plan(rows: list[_P]) -> tuple[Segment, ...]:
    return tuple(Segment(*_khz(low, high), kind, note) for low, high, kind, note in rows)


# IARU band plans (display only). Kinds: cw = CW only; narrow = narrow band
# modes and digimodes (<= 500 Hz, or CW and data as written); all = all modes;
# beacon = beacons exclusively; satellite; fm = FM repeaters and simplex;
# guard = no transmission.
PLANS: dict[int, tuple[Segment, ...]] = {
    1: _plan(
        [
            (1810, 1838, "cw", "1836 CW QRP"),
            (1838, 1840, "narrow", ""),
            (1840, 1843, "all", "digimodes"),
            (1843, 2000, "all", ""),
            (3500, 3560, "cw", "3500-3510 intercontinental; CW contest preferred"),
            (3560, 3570, "cw", "3560 CW QRP"),
            (3570, 3600, "narrow", "digimodes"),
            (3600, 3800, "all", "3630 DV, 3690 SSB QRP, 3735 image, 3760 emergency"),
            (5351.5, 5354, "narrow", "CW, narrow band modes"),
            (5354, 5366, "all", "USB for voice"),
            (5366, 5366.5, "narrow", "weak signal, 20 Hz"),
            (7000, 7040, "cw", "7030 CW QRP"),
            (7040, 7050, "narrow", "digimodes"),
            (7050, 7200, "all", "7070 DV, 7090 SSB QRP, 7110 emergency, 7165 image"),
            (10100, 10130, "cw", "10116 CW QRP"),
            (10130, 10150, "narrow", "digimodes"),
            (14000, 14070, "cw", "14055 QRS, 14060 CW QRP"),
            (14070, 14099, "narrow", "digimodes"),
            (14099, 14101, "beacon", "IBP, beacons exclusively"),
            (
                14101,
                14350,
                "all",
                "14195 DX-peditions, 14230 image, 14285 QRP, 14300 emergency",
            ),
            (18068, 18095, "cw", "18086 CW QRP"),
            (18095, 18109, "narrow", "digimodes"),
            (18109, 18111, "beacon", "IBP, beacons exclusively"),
            (18111, 18168, "all", "18130 SSB QRP, 18150 DV, 18160 emergency"),
            (21000, 21070, "cw", "21055 QRS, 21060 QRP"),
            (21070, 21110, "narrow", "digimodes"),
            (21110, 21120, "all", "digimodes, not SSB"),
            (21120, 21149, "narrow", ""),
            (21149, 21151, "beacon", "IBP, beacons exclusively"),
            (21151, 21450, "all", "21180 DV, 21285 SSB QRP, 21340 image, 21360 emergency"),
            (24890, 24915, "cw", "24906 CW QRP"),
            (24915, 24929, "narrow", "digimodes"),
            (24929, 24931, "beacon", "IBP, beacons exclusively"),
            (24931, 24990, "all", "24950 SSB QRP, 24960 DV"),
            (28000, 28070, "cw", "28055 QRS, 28060 QRP"),
            (28070, 28190, "narrow", "digimodes"),
            (28190, 28225, "beacon", "beacons exclusively"),
            (28225, 29300, "all", "28330 DV, 28360 SSB QRP, 28680 image; 29100-29200 FM simplex"),
            (29300, 29510, "satellite", "satellite links"),
            (29510, 29520, "guard", "guard channel"),
            (29520, 29700, "fm", "FM repeaters, 29600 calling"),
        ]
    ),
    2: _plan(
        [
            (1800, 1810, "narrow", "digimodes"),
            (1810, 1839, "cw", "1812 CW QRP; 1830-1839 DX window"),
            (1839, 1840, "narrow", "CW, digimodes"),
            (1840, 2000, "all", "1840-1850 DX window; 1910 SSB QRP"),
            (3500, 3570, "cw", "3500-3510 DX window; 3555 QRS, 3560 QRP"),
            (3570, 3600, "narrow", "CW, digimodes"),
            (3600, 4000, "all", "3690 SSB QRP, 3735/3845 image, 3750/3985 emergency, 3885 AM"),
            (5351.5, 5354, "narrow", "CW, digimodes"),
            (5354, 5366, "all", ""),
            (5366, 5366.5, "narrow", "CW, digimodes, 20 Hz"),
            (7000, 7040, "cw", "7000-7025 DX window; 7030 QRP"),
            (7040, 7050, "narrow", "CW, digimodes"),
            (7050, 7300, "all", "7060/7240/7275 emergency, 7070 DV, 7090 SSB QRP, 7165 image"),
            (10100, 10130, "cw", "10116 CW QRP"),
            (10130, 10150, "narrow", "CW, digimodes"),
            (14000, 14070, "cw", "14000-14025 DX window; 14055 QRS, 14060 QRP"),
            (14070, 14099, "narrow", "CW, digimodes"),
            (14099, 14101, "beacon", "IBP, exclusive"),
            (
                14101,
                14350,
                "all",
                "14190-14200 DX, 14230 image, 14285 QRP, 14300 emergency",
            ),
            (18068, 18095, "cw", "18086 CW QRP"),
            (18095, 18109, "narrow", "CW, digimodes"),
            (18109, 18111, "beacon", "IBP, exclusive"),
            (18111, 18168, "all", "18130 QRP, 18160 emergency"),
            (21000, 21070, "cw", "21060 CW QRP"),
            (21070, 21149, "narrow", "CW, digimodes"),
            (21149, 21151, "beacon", "IBP, exclusive"),
            (21151, 21450, "all", "21285 SSB QRP, 21340 image, 21360 emergency"),
            (24890, 24915, "cw", "24906 CW QRP"),
            (24915, 24929, "narrow", "CW, digimodes"),
            (24929, 24931, "beacon", "IBP, exclusive"),
            (24931, 24990, "all", "24950 SSB QRP"),
            (28000, 28070, "cw", "28055 QRS, 28060 QRP"),
            (28070, 28190, "narrow", "CW, digimodes"),
            (28190, 28225, "beacon", "beacons, IBP exclusive"),
            (28225, 29300, "all", "28330 DV, 28360 SSB QRP, 28680 image, 29000-29200 AM"),
            (29300, 29510, "satellite", "satellite"),
            (29510, 29520, "guard", "guard band, no transmission"),
            (29520, 29700, "fm", "FM/DV repeaters, 29600 calling"),
        ]
    ),
    3: _plan(
        [
            (1800, 1830, "cw", ""),
            (1830, 1840, "narrow", "1836 QRP"),
            (1840, 2000, "all", ""),
            (3500, 3535, "cw", "3500-3510 DX"),
            (3535, 3900, "all", "3560 QRP, 3600 emergency, 3690 DV/SSB QRP, 3735/3845 image"),
            (5351.5, 5354, "narrow", "CW, digimodes"),
            (5354, 5366, "all", "USB for phone"),
            (5366, 5366.5, "narrow", "weak signal, 20 Hz"),
            (7000, 7030, "cw", ""),
            (7030, 7200, "all", "7030 QRP, 7070 DV, 7090 SSB QRP, 7110 emergency, 7165 image"),
            # The plan's 30 m segment starts at 10110 kHz (as written).
            (10110, 10130, "cw", "10116 QRP"),
            (10130, 10150, "narrow", "CW, digimodes"),
            (14000, 14070, "cw", "14055 QRS, 14060 QRP"),
            (14070, 14099.5, "narrow", "CW, narrowband modes"),
            (14099.5, 14100.5, "beacon", "IBP 14100, 500 Hz guard band"),
            (14100.5, 14350, "all", "14130 DV, 14195 DX phone, 14230 image, 14300 emergency"),
            (18068, 18095, "cw", "18086 QRP"),
            (18095, 18109.5, "narrow", "CW, digimodes"),
            (18109.5, 18110.5, "beacon", "IBP 18110, 500 Hz guard band"),
            (18110.5, 18168, "all", "18130 SSB QRP, 18160 DV/emergency"),
            (21000, 21070, "cw", "21055 QRS, 21060 QRP"),
            (21070, 21149.5, "narrow", "CW, digimodes; 21125-21150 satellite uplink"),
            (21149.5, 21150.5, "beacon", "IBP 21150, 500 Hz guard band"),
            (21150.5, 21450, "all", "21180 DV, 21295 DX phone, 21340 image, 21360 emergency"),
            (24890, 24915, "cw", "24906 QRP"),
            (24915, 24929.5, "narrow", "CW, narrowband modes"),
            (24929.5, 24930.5, "beacon", "IBP 24930, 500 Hz guard band"),
            (24930.5, 24990, "all", "24950 SSB QRP, 24960 DV"),
            (28000, 28070, "cw", "28055 QRS"),
            (28070, 28190, "narrow", "CW, narrowband modes"),
            (28190, 28225, "beacon", "IBP 28200, continuous duty beacons"),
            (28225, 29300, "all", "28330 DV, 28360 QRP, 28495 DX, 28680 image"),
            (29300, 29510, "satellite", "satellite"),
            (29510, 29700, "fm", "FM repeaters and simplex, 29600 calling"),
        ]
    ),
}


def locator_to_latlon(locator: str) -> tuple[float, float]:
    """Centre of a 4- or 6-character Maidenhead square, degrees (lat, lon)."""
    text = locator.strip()
    if not LOCATOR.fullmatch(text[:4].upper() + text[4:].upper()):
        raise ValueError(f"not a locator: {locator!r}")
    up = text.upper()
    lon = float((ord(up[0]) - 65) * 20 - 180 + int(up[2]) * 2)
    lat = float((ord(up[1]) - 65) * 10 - 90 + int(up[3]))
    if len(up) == 6:
        lon += (ord(up[4]) - 65) * (2 / 24) + 1 / 24
        lat += (ord(up[5]) - 65) * (1 / 24) + 1 / 48
    else:
        lon += 1.0
        lat += 0.5
    return lat, lon


def _vector(lat: float, lon: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def _great_circle_lon(start: tuple[float, float], end: tuple[float, float], lat: float) -> float:
    """Longitude where the great circle arc from start to end (lat, lon) reaches lat.

    Latitude changes monotonically along each arc used here, so bisection works.
    """
    a, b = _vector(*start), _vector(*end)

    def point(t: float) -> tuple[float, float]:
        x = [a[k] * (1 - t) + b[k] * t for k in range(3)]
        norm = math.sqrt(sum(c * c for c in x))
        return (
            math.degrees(math.asin(x[2] / norm)),
            math.degrees(math.atan2(x[1], x[0])),
        )

    low, high = 0.0, 1.0
    rising = end[0] > start[0]
    for _ in range(50):
        middle = (low + high) / 2
        if (point(middle)[0] < lat) == rising:
            low = middle
        else:
            high = middle
    return point((low + high) / 2)[1]


def line_a(lat: float) -> float:
    """ITU line A: 40 E down to 40 N, great circle arc to 60 E / 23.5 N, then 60 E."""
    if lat >= 40:
        return 40.0
    if lat >= 23.5:
        return _great_circle_lon((40.0, 40.0), (23.5, 60.0), lat)
    return 60.0


def line_b(lat: float) -> float:
    """ITU line B: 10 W down to 72 N, arcs to 50 W / 40 N and 20 W / 10 S, then 20 W."""
    if lat >= 72:
        return -10.0
    if lat >= 40:
        return _great_circle_lon((72.0, -10.0), (40.0, -50.0), lat)
    if lat >= -10:
        return _great_circle_lon((40.0, -50.0), (-10.0, -20.0), lat)
    return -20.0


def line_c(lat: float) -> float:
    """ITU line C, west of 0 (165 E = -195): Bering Strait 65.5 N, arcs to
    165 E / 50 N and 170 W / 10 N, along 10 N to 120 W, then 120 W."""
    if lat >= 65.5:
        return -169.0
    if lat >= 50:
        lon = _great_circle_lon((65.5, -169.0), (50.0, 165.0), lat)
    elif lat >= 10:
        lon = _great_circle_lon((50.0, 165.0), (10.0, -170.0), lat)
    else:
        return -120.0
    return lon if lon < 0 else lon - 360


# Region 1 east of line A (No. 5.3): the former USSR, Mongolia, Turkey and the
# Arctic north of Russia; (lon 0-360, lat), drawn along their southern borders.
_REGION1_EAST = [
    (34.0, 90.0),
    (191.0, 90.0),
    (191.0, 65.5),
    (172.0, 60.0),
    (163.0, 56.0),
    (156.5, 50.8),
    (147.0, 44.5),
    (141.5, 45.8),
    (139.5, 47.0),
    (133.0, 42.8),
    (130.7, 42.3),
    (131.3, 45.0),
    (133.1, 45.1),
    (134.7, 48.3),
    (130.7, 48.9),
    (127.6, 49.8),
    (125.7, 52.9),
    (123.3, 53.5),
    (120.8, 53.3),
    (117.9, 49.6),
    (119.9, 46.7),
    (117.4, 46.6),
    (113.6, 44.8),
    (111.8, 43.7),
    (110.4, 42.8),
    (105.0, 41.6),
    (100.8, 42.6),
    (96.4, 42.7),
    (95.3, 44.2),
    (90.9, 45.3),
    (87.8, 49.2),
    (85.5, 47.1),
    (82.5, 45.5),
    (80.0, 44.9),
    (80.2, 42.1),
    (76.5, 40.4),
    (73.6, 39.4),
    (74.9, 37.4),
    (71.6, 36.7),
    (67.8, 37.2),
    (64.8, 36.3),
    (61.3, 35.6),
    (60.4, 36.6),
    (56.5, 38.1),
    (53.9, 37.4),
    (48.9, 38.4),
    (46.5, 38.9),
    (44.8, 39.7),
    (44.3, 38.4),
    (44.8, 37.2),
    (42.4, 37.1),
    (34.0, 36.0),
]
# Iran (No. 5.3: wholly in Region 3), (lon, lat).
_IRAN = [
    (44.0, 39.4),
    (44.8, 39.7),
    (46.5, 38.9),
    (48.9, 38.4),
    (49.0, 37.5),
    (53.9, 37.4),
    (56.5, 38.1),
    (60.4, 36.6),
    (61.3, 35.6),
    (61.0, 34.4),
    (60.5, 33.6),
    (61.7, 31.4),
    (60.9, 29.9),
    (61.8, 28.7),
    (63.3, 27.2),
    (61.6, 25.2),
    (57.8, 25.7),
    (56.4, 27.1),
    (54.7, 26.5),
    (51.4, 27.9),
    (50.0, 30.0),
    (48.5, 29.9),
    (47.7, 31.0),
    (46.2, 33.0),
    (45.4, 33.9),
    (46.0, 35.0),
    (45.5, 35.9),
    (44.8, 37.2),
]


def _inside(polygon: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (x2 - x1) * (y - y1) / (y2 - y1):
            inside = not inside
    return inside


def region_of(locator: str) -> int:
    """ITU region (1, 2 or 3) of the centre of a Maidenhead locator square."""
    lat, lon = locator_to_latlon(locator)
    west = lon if lon < 0 else lon - 360  # continuous frame for line C
    if line_c(lat) < west < line_b(lat):
        return 2
    east = lon % 360
    if _inside(_REGION1_EAST, east, lat):
        return 1
    if _inside(_IRAN, lon, lat):
        return 3
    if lon < -60:  # the Pacific west of line C
        return 3
    if lon < line_a(lat):
        return 1
    return 3


def in_band(region: int, low_hz: float, high_hz: float) -> bool:
    """Whether the whole span [low, high] lies inside one of the region's bands."""
    return any(lo <= low_hz and high_hz <= hi for lo, hi in BANDS[region])
