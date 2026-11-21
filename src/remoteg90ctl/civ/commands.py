# SPDX-License-Identifier: GPL-3.0-or-later
"""CI-V command numbers and operating modes used by this project.

Command numbers and mode codes are taken from the Icom CI-V convention. Which of
them the G90 implements, and with which firmware, is UNVERIFIED (AGENTS.md
section 8). Do not add a command here without a source; do not treat its
presence here as proof that the radio supports it.
"""

from enum import IntEnum

from remoteg90ctl.civ.bcd import FREQUENCY_BYTES, decode_frequency, encode_frequency
from remoteg90ctl.civ.errors import CivParseError
from remoteg90ctl.civ.frame import Frame, build_frame


class Command(IntEnum):
    """CI-V command bytes known to this project (Icom numbering, UNVERIFIED for G90)."""

    TRANSCEIVE_FREQUENCY = 0x00  # unsolicited frequency broadcast from the radio
    TRANSCEIVE_MODE = 0x01  # unsolicited mode broadcast from the radio
    READ_FREQUENCY = 0x03
    READ_MODE = 0x04
    SET_FREQUENCY = 0x05
    SET_MODE = 0x06
    # VFO select: 07 00 VFO A, 07 01 VFO B (each VFO keeps its own mode).
    # Measured on the owner's G90 (firmware 1.81, 2026-11-08); answered OK.
    SELECT_VFO = 0x07
    # Split: without data = read (0F 00 off, 0F 01 on); 0F 00 / 0F 01 set it.
    # Measured on the owner's G90 (firmware 1.81, 2026-11-08). In split the
    # radio transmits on the unselected VFO, in that VFO's mode (measured).
    SPLIT = 0x0F
    # Attenuator, no sub-command: state 00 off, 0C on (12 dB, binary, not the
    # Icom BCD); without data = read. Measured on the owner's G90 (firmware
    # 1.81, 2026-11-07): any data byte TOGGLES it (11 00 switches it on when
    # it is off), and the answer is "11 <new state>", not OK; switching it on
    # switches the preamplifier off (one front-panel button cycles off ->
    # preamp -> attenuator).
    ATTENUATOR = 0x11
    # Mode with the data flag (sub-command 0x00, selected VFO): 26 00 <mode>
    # <data 00/01> <filter>; without data = read. Measured on the owner's G90
    # (firmware 1.81, 2026-11-07): read and set work (USB <-> U-D, LSB <-> L-D);
    # setting without the filter byte is answered NG. 0x04 cannot see the flag.
    DATA_MODE = 0x26
    # Frequency of a VFO: 25 00 the selected one, 25 01 the unselected one.
    # Measured on the owner's G90 (firmware 1.81, 2026-11-08): the frequency is
    # right, but the sub-command byte of the REPLY tells which VFO is selected
    # (00 A, 01 B), whatever was asked.
    VFO_FREQUENCY = 0x25
    # Transmit control (Icom 0x1C). Sub-command 0x00 is PTT and never leaves
    # the guard. Sub-command 0x01 is the antenna tuner: 00 off, 01 on, 02 start
    # tuning, without data = read. Measured on the owner's G90 (firmware 1.81,
    # 2026-11-03): on/off and read work; 02 tunes with a carrier (usually
    # 1-1.5 s, longer with a poorly matched antenna) and cannot be stopped
    # (01 00 during tuning is acknowledged and ignored). Tuning is
    # transmitting (decisions.md D26, D43).
    TX_CONTROL = 0x1C
    # Levels. Sub-command 0x0A: RF power setting; without data = read. The
    # reply is a 0-255 level in the G90's BCD (g90_level_from_bytes).
    # Sub-command 0x02: RF gain, 1-100 % on the radio over 0-255. Measured on
    # the owner's G90 (firmware 1.81, 2026-11-07): 0 -> 1 %, 255 -> 100 %, a
    # written level is cut down to a whole percent (126 -> 49 %, 127 -> 50 %)
    # and read back as floor((p - 1) * 255 / 99) (50 % -> 126). Listed in
    # the G90 extended manual (firmware 1.80), not in the Xiegu CI-V reference.
    # Sub-command 0x12: noise blanker LEVEL (not its width, which CAT cannot
    # set), menu steps 0-10 on 0-255. Measured on the owner's G90 (firmware
    # 1.81, 2026-11-07): a written level is cut down to a whole step (25 -> 0,
    # 26 -> 1, 128 -> 5) and read back as floor(step * 25.5) (25, 127, 178,
    # 255); the read follows the menu.
    LEVEL = 0x14
    # Meters (read only). Sub-command 0x11: RF power output, a 0-255 level in
    # the G90's BCD. Measured on the owner's G90 (firmware 1.81, 2026-11-03):
    # 0 in receive and with PTT but no modulation, 171 at 1 W, 241 at 2 W, and
    # 255 (end of scale) from about 3 W up to 20 W.
    READ_METER = 0x15
    # Functions. Sub-command 0x02: preamplifier, 00 off, 01 on; without data =
    # read. Measured on the owner's G90 (firmware 1.81, 2026-11-07): set is
    # answered OK, the read follows the front panel, switching it on switches
    # the attenuator off, and 02 is accepted but sets 01 (a single stage).
    # Sub-command 0x12: AGC, 00 off, 01 fast, 02 slow, 03 auto (AGC-F, AGC-S,
    # AGC-A on the display); without data = read. Measured on the owner's G90
    # (firmware 1.81, 2026-11-07): set answered OK, read back as set; 04 is
    # answered OK too, so the server sends only 00-03.
    # Sub-command 0x22: noise blanker, 00 off, 01 on (measured 2026-11-07:
    # set answered OK, read back as set). Its level is 14 12.
    FUNCTION = 0x16
    # Sub-command 0x00. The owner's G90 (firmware 1.81) answers "19 70": its
    # CI-V address, like an Icom radio (measured 2026-11-02).
    READ_ID = 0x19
    # Sub-command 0x19: Xiegu radio identity, from firmware 1.8. Per the Xiegu
    # CI-V Reference the G90 reports 0x0090; the reply is UNVERIFIED.
    XIEGU = 0x1D
    # Sub-command 0x05: menu items, addressed by a 2-byte item number
    # (decisions.md D37). Without data = read (Icom convention, UNVERIFIED).
    EXTENDED = 0x1A


READ_ID_SUBCOMMAND = 0x00
XIEGU_ID_SUBCOMMAND = 0x19
MENU_SUBCOMMAND = 0x05
DATA_MODE_SUBCOMMAND = 0x00
# 25 and 26: the selected VFO (00) or the unselected one (01). 26 01 reports
# the data flag correctly on the owner's G90 (firmware 1.81, 2026-11-08).
SELECTED_VFO = 0x00
UNSELECTED_VFO = 0x01
VFO_CODES = {"A": 0x00, "B": 0x01}
SPLIT_OFF = 0x00
SPLIT_ON = 0x01
ATU_SUBCOMMAND = 0x01
ATU_OFF = 0x00
ATU_ON = 0x01
ATU_TUNE = 0x02
POWER_METER_SUBCOMMAND = 0x11
SWR_METER_SUBCOMMAND = 0x12
# S-meter (15 02): a 0-255 level in the G90's BCD; scale in server/smeter.py.
S_METER_SUBCOMMAND = 0x02
# ALC meter (15 13): a 0-255 level in the G90's BCD, 0 in receive (measured);
# the number on the radio's display is converted in server/alc.py.
ALC_SUBCOMMAND = 0x13
RF_POWER_SUBCOMMAND = 0x0A
AF_SUBCOMMAND = 0x01  # speaker volume (AF gain), level 0-255
PREAMP_SUBCOMMAND = 0x02
PREAMP_OFF = 0x00
PREAMP_ON = 0x01
AGC_SUBCOMMAND = 0x12
# AGC codes of the G90 (measured): not the Icom order (01 fast, 02 mid, 03 slow).
AGC_MODES: dict[int, str] = {0x00: "off", 0x01: "fast", 0x02: "slow", 0x03: "auto"}
NB_SUBCOMMAND = 0x22
NB_OFF = 0x00
NB_ON = 0x01
NB_LEVEL_SUBCOMMAND = 0x12
NB_LEVEL_STEPS = 10  # the radio's menu: level 0-10
RF_GAIN_SUBCOMMAND = 0x02
RF_GAIN_MIN, RF_GAIN_MAX = 1, 100  # percent, as on the radio
ATTENUATOR_OFF = 0x00
ATTENUATOR_ON = 0x0C  # 12 dB, the G90's only step (measured)


class Mode(IntEnum):
    """Operating mode codes (Icom numbering; G90 subset UNVERIFIED)."""

    LSB = 0x00
    USB = 0x01
    AM = 0x02
    CW = 0x03
    FM = 0x05
    CW_R = 0x07


def mode_from_code(code: int) -> Mode:
    """Map a mode byte to :class:`Mode`; unknown codes raise :class:`CivParseError`."""
    try:
        return Mode(code)
    except ValueError:
        raise CivParseError(f"unknown mode code 0x{code:02X}") from None


def read_frequency_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its operating frequency."""
    return build_frame(to_address, from_address, Command.READ_FREQUENCY)


def read_mode_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its operating mode."""
    return build_frame(to_address, from_address, Command.READ_MODE)


def read_id_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its identity (``19 00``)."""
    return build_frame(to_address, from_address, Command.READ_ID, READ_ID_SUBCOMMAND)


def read_xiegu_id_request(to_address: int, from_address: int) -> bytes:
    """Frame asking a Xiegu radio for its model identity (``1D 19``)."""
    return build_frame(to_address, from_address, Command.XIEGU, XIEGU_ID_SUBCOMMAND)


def read_power_meter_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its RF power output meter (``15 11``)."""
    return build_frame(to_address, from_address, Command.READ_METER, POWER_METER_SUBCOMMAND)


def read_data_mode_request(
    to_address: int, from_address: int, *, unselected: bool = False
) -> bytes:
    """Frame asking for the mode with its data flag (``26 00``; ``26 01`` other VFO)."""
    sub = UNSELECTED_VFO if unselected else DATA_MODE_SUBCOMMAND
    return build_frame(to_address, from_address, Command.DATA_MODE, sub)


def set_data_mode_request(
    to_address: int, from_address: int, mode: Mode, data: bool, filter_code: int
) -> bytes:
    """Frame setting the mode, data flag and filter (``26 00 <mode> <data> <filter>``)."""
    payload = bytes([mode, 1 if data else 0, filter_code])
    return build_frame(to_address, from_address, Command.DATA_MODE, DATA_MODE_SUBCOMMAND, payload)


def data_mode_from_frame(frame: Frame) -> tuple[Mode, bool, int]:
    """Mode, data flag and filter from a ``26 00`` or ``26 01`` reply."""
    if frame.command != Command.DATA_MODE or frame.body[:1] not in (b"\x00", b"\x01"):
        raise CivParseError(f"command 0x{frame.command:02X} is not a data mode reply")
    if len(frame.body) != 4 or frame.body[2] not in (0, 1):
        raise CivParseError(f"data mode reply {frame.body.hex()} is not 00 <mode> <data> <filter>")
    return mode_from_code(frame.body[1]), frame.body[2] == 1, frame.body[3]


def read_vfo_frequency_request(to_address: int, from_address: int, *, unselected: bool) -> bytes:
    """Frame asking for the frequency of the selected (``25 00``) or other VFO (``25 01``)."""
    sub = UNSELECTED_VFO if unselected else SELECTED_VFO
    return build_frame(to_address, from_address, Command.VFO_FREQUENCY, sub)


def vfo_frequency_from_frame(frame: Frame) -> tuple[str, int]:
    """Selected VFO ("A"/"B", from the reply's sub-command byte) and the frequency."""
    if frame.command != Command.VFO_FREQUENCY or len(frame.body) != 1 + FREQUENCY_BYTES:
        raise CivParseError(f"{frame.body.hex()} is not a 25 reply with a frequency")
    selected = {v: k for k, v in VFO_CODES.items()}.get(frame.body[0])
    if selected is None:
        raise CivParseError(f"25 reply names VFO {frame.body[0]:02X}")
    return selected, decode_frequency(frame.body[1:])


def select_vfo_request(to_address: int, from_address: int, vfo: str) -> bytes:
    """Frame selecting VFO "A" (``07 00``) or "B" (``07 01``)."""
    return build_frame(to_address, from_address, Command.SELECT_VFO, VFO_CODES[vfo])


def read_split_request(to_address: int, from_address: int) -> bytes:
    """Frame asking whether split is on (``0F``)."""
    return build_frame(to_address, from_address, Command.SPLIT)


def set_split_request(to_address: int, from_address: int, on: bool) -> bytes:
    """Frame switching split on (``0F 01``) or off (``0F 00``)."""
    return build_frame(to_address, from_address, Command.SPLIT, SPLIT_ON if on else SPLIT_OFF)


def split_from_frame(frame: Frame) -> bool:
    """Split state from a ``0F`` reply."""
    if frame.command != Command.SPLIT or frame.body not in (b"\x00", b"\x01"):
        raise CivParseError(f"{frame.body.hex()} is not a split reply")
    return frame.body == b"\x01"


def read_atu_request(to_address: int, from_address: int) -> bytes:
    """Frame asking whether the antenna tuner is on (``1C 01``)."""
    return build_frame(to_address, from_address, Command.TX_CONTROL, ATU_SUBCOMMAND)


def set_atu_request(to_address: int, from_address: int, on: bool) -> bytes:
    """Frame switching the antenna tuner on or off (``1C 01 01`` / ``1C 01 00``)."""
    data = bytes([ATU_ON if on else ATU_OFF])
    return build_frame(to_address, from_address, Command.TX_CONTROL, ATU_SUBCOMMAND, data)


def atu_tune_request(to_address: int, from_address: int) -> bytes:
    """Frame starting a tuning (``1C 01 02``): it TRANSMITS. Sent by the guard only."""
    data = bytes([ATU_TUNE])
    return build_frame(to_address, from_address, Command.TX_CONTROL, ATU_SUBCOMMAND, data)


def atu_from_frame(frame: Frame) -> bool:
    """Whether the tuner is on, from a ``1C 01`` reply."""
    if frame.command != Command.TX_CONTROL or frame.body[:1] != bytes([ATU_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an ATU reply")
    if len(frame.body) != 2 or frame.body[1] not in (ATU_OFF, ATU_ON):
        raise CivParseError(f"ATU reply {frame.body.hex()} is not 00 or 01")
    return frame.body[1] == ATU_ON


def read_preamp_request(to_address: int, from_address: int) -> bytes:
    """Frame asking whether the preamplifier is on (``16 02``)."""
    return build_frame(to_address, from_address, Command.FUNCTION, PREAMP_SUBCOMMAND)


def set_preamp_request(to_address: int, from_address: int, on: bool) -> bytes:
    """Frame switching the preamplifier on or off (``16 02 01`` / ``16 02 00``)."""
    data = bytes([PREAMP_ON if on else PREAMP_OFF])
    return build_frame(to_address, from_address, Command.FUNCTION, PREAMP_SUBCOMMAND, data)


def preamp_from_frame(frame: Frame) -> bool:
    """Whether the preamplifier is on, from a ``16 02`` reply."""
    if frame.command != Command.FUNCTION or frame.body[:1] != bytes([PREAMP_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not a preamp reply")
    if len(frame.body) != 2 or frame.body[1] not in (PREAMP_OFF, PREAMP_ON):
        raise CivParseError(f"preamp reply {frame.body.hex()} is not 00 or 01")
    return frame.body[1] == PREAMP_ON


def read_agc_request(to_address: int, from_address: int) -> bytes:
    """Frame asking for the AGC setting (``16 12``)."""
    return build_frame(to_address, from_address, Command.FUNCTION, AGC_SUBCOMMAND)


def set_agc_request(to_address: int, from_address: int, agc: str) -> bytes:
    """Frame setting the AGC: ``off``, ``fast``, ``slow`` or ``auto`` (``16 12 <code>``)."""
    codes = {name: code for code, name in AGC_MODES.items()}
    if agc not in codes:
        raise ValueError(f"unknown AGC setting {agc!r}")
    return build_frame(
        to_address, from_address, Command.FUNCTION, AGC_SUBCOMMAND, bytes([codes[agc]])
    )


def agc_from_frame(frame: Frame) -> str:
    """AGC setting name from a ``16 12`` reply."""
    if frame.command != Command.FUNCTION or frame.body[:1] != bytes([AGC_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an AGC reply")
    if len(frame.body) != 2 or frame.body[1] not in AGC_MODES:
        raise CivParseError(f"AGC reply {frame.body.hex()} is not 00-03")
    return AGC_MODES[frame.body[1]]


def read_attenuator_request(to_address: int, from_address: int) -> bytes:
    """Frame asking whether the attenuator is on (``11``)."""
    return build_frame(to_address, from_address, Command.ATTENUATOR)


def set_attenuator_request(to_address: int, from_address: int, on: bool) -> bytes:
    """Frame ``11 0C`` / ``11 00``; the G90 toggles the attenuator on either (measured)."""
    data = bytes([ATTENUATOR_ON if on else ATTENUATOR_OFF])
    return build_frame(to_address, from_address, Command.ATTENUATOR, None, data)


def attenuator_from_frame(frame: Frame) -> bool:
    """Whether the attenuator is on, from an ``11`` reply (to a read or a setting)."""
    if frame.command != Command.ATTENUATOR:
        raise CivParseError(f"command 0x{frame.command:02X} is not an attenuator reply")
    if len(frame.body) != 1 or frame.body[0] not in (ATTENUATOR_OFF, ATTENUATOR_ON):
        raise CivParseError(f"attenuator reply {frame.body.hex()} is not 00 or 0C")
    return frame.body[0] == ATTENUATOR_ON


def read_rf_power_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its RF power setting (``14 0A``, no data)."""
    return build_frame(to_address, from_address, Command.LEVEL, RF_POWER_SUBCOMMAND)


def read_af_request(to_address: int, from_address: int) -> bytes:
    """Frame asking for the speaker volume (``14 01``)."""
    return build_frame(to_address, from_address, Command.LEVEL, AF_SUBCOMMAND)


def _plain_bcd_level(level: int) -> bytes:
    """A 0-255 level as 2 bytes of plain BCD 0000-0255 (how levels are written)."""
    if not 0 <= level <= 255:
        raise ValueError(f"level {level} outside 0-255")
    return bytes([level // 100, ((level // 10 % 10) << 4) | (level % 10)])


def set_af_request(to_address: int, from_address: int, level: int) -> bytes:
    """Frame setting the speaker volume 0-255 (``14 01`` + 2 bytes, plain BCD as for 14 0A)."""
    data = _plain_bcd_level(level)
    return build_frame(to_address, from_address, Command.LEVEL, AF_SUBCOMMAND, data)


def read_nb_request(to_address: int, from_address: int) -> bytes:
    """Frame asking whether the noise blanker is on (``16 22``)."""
    return build_frame(to_address, from_address, Command.FUNCTION, NB_SUBCOMMAND)


def set_nb_request(to_address: int, from_address: int, on: bool) -> bytes:
    """Frame switching the noise blanker on or off (``16 22 01`` / ``16 22 00``)."""
    data = bytes([NB_ON if on else NB_OFF])
    return build_frame(to_address, from_address, Command.FUNCTION, NB_SUBCOMMAND, data)


def nb_from_frame(frame: Frame) -> bool:
    """Whether the noise blanker is on, from a ``16 22`` reply."""
    if frame.command != Command.FUNCTION or frame.body[:1] != bytes([NB_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not a noise blanker reply")
    if len(frame.body) != 2 or frame.body[1] not in (NB_OFF, NB_ON):
        raise CivParseError(f"noise blanker reply {frame.body.hex()} is not 00 or 01")
    return frame.body[1] == NB_ON


def step_to_level(step: int, steps: int) -> int:
    """Smallest 0-255 level the G90 cuts down to ``step`` of ``steps`` (measured).

    The G90 maps a setting with ``steps`` + 1 positions on 0-255, cuts a
    written level down to a whole step and reports a step as
    floor(step * 255 / steps): writing ceil(step * 255 / steps) gives it.
    """
    if not 0 <= step <= steps:
        raise ValueError(f"step {step} outside 0-{steps}")
    return -(-step * 255 // steps)


def level_to_step(level: int, steps: int) -> int:
    """The step of a reported 0-255 level (see step_to_level)."""
    return round(level * steps / 255)


def nb_step_to_level(step: int) -> int:
    """Smallest 0-255 level the radio cuts down to menu step ``step`` (0-10)."""
    if not 0 <= step <= NB_LEVEL_STEPS:
        raise ValueError(f"noise blanker level {step} outside 0-{NB_LEVEL_STEPS}")
    return step_to_level(step, NB_LEVEL_STEPS)


def nb_level_to_step(level: int) -> int:
    """Menu step (0-10) of a reported 0-255 level (reported as floor(step * 25.5))."""
    return level_to_step(level, NB_LEVEL_STEPS)


def read_rf_gain_request(to_address: int, from_address: int) -> bytes:
    """Frame asking for the RF gain (``14 02``)."""
    return build_frame(to_address, from_address, Command.LEVEL, RF_GAIN_SUBCOMMAND)


def set_rf_gain_request(to_address: int, from_address: int, percent: int) -> bytes:
    """Frame setting the RF gain to 1-100 % (``14 02`` + plain BCD)."""
    if not RF_GAIN_MIN <= percent <= RF_GAIN_MAX:
        raise ValueError(f"RF gain {percent} % outside {RF_GAIN_MIN}-{RF_GAIN_MAX}")
    data = _plain_bcd_level(step_to_level(percent - RF_GAIN_MIN, RF_GAIN_MAX - RF_GAIN_MIN))
    return build_frame(to_address, from_address, Command.LEVEL, RF_GAIN_SUBCOMMAND, data)


def rf_gain_from_frame(frame: Frame) -> int:
    """RF gain in percent (1-100), from a ``14 02`` reply."""
    if frame.command != Command.LEVEL or frame.body[:1] != bytes([RF_GAIN_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an RF gain reply")
    level = g90_level_from_bytes(frame.body[1:])
    return RF_GAIN_MIN + level_to_step(level, RF_GAIN_MAX - RF_GAIN_MIN)


def read_nb_level_request(to_address: int, from_address: int) -> bytes:
    """Frame asking for the noise blanker level (``14 12``)."""
    return build_frame(to_address, from_address, Command.LEVEL, NB_LEVEL_SUBCOMMAND)


def set_nb_level_request(to_address: int, from_address: int, step: int) -> bytes:
    """Frame setting the noise blanker level to menu step 0-10 (``14 12`` + plain BCD)."""
    data = _plain_bcd_level(nb_step_to_level(step))
    return build_frame(to_address, from_address, Command.LEVEL, NB_LEVEL_SUBCOMMAND, data)


def nb_level_from_frame(frame: Frame) -> int:
    """Noise blanker level as a menu step 0-10, from a ``14 12`` reply."""
    if frame.command != Command.LEVEL or frame.body[:1] != bytes([NB_LEVEL_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not a noise blanker level reply")
    return nb_level_to_step(g90_level_from_bytes(frame.body[1:]))


def af_from_frame(frame: Frame) -> int:
    """Speaker volume 0-255 of a ``14 01`` reply (the G90's level encoding)."""
    if frame.command != Command.LEVEL or frame.body[:1] != bytes([AF_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an AF reply")
    return g90_level_from_bytes(frame.body[1:])


def set_rf_power_request(to_address: int, from_address: int, level: int) -> bytes:
    """Frame setting the RF power level 0-255 (``14 0A`` + 2 bytes).

    UNVERIFIED (AGENTS.md section 8, item 5): the level is sent in plain BCD
    0000-0255, as the Xiegu CI-V reference specifies. The G90 is known to get
    the last digit wrong when it *reports* a level (g90_level_from_bytes);
    whether it reads a written level the same way is not measured, so the
    server reads the setting back after every write.
    """
    data = _plain_bcd_level(level)
    return build_frame(to_address, from_address, Command.LEVEL, RF_POWER_SUBCOMMAND, data)


def g90_level_from_bytes(data: bytes) -> int:
    """A 0-255 level as the G90 encodes it in 2 bytes.

    Icom sends 0000-0255 in BCD. The G90's firmware gets the last digit
    wrong: the hundreds and tens digits are BCD, but the low nibble carries
    the low 4 bits of the whole number instead of the units digit (171 comes
    as 01 7B, 255 as 02 5F). The units digit is recovered from that nibble.
    Idea from Hamlib's G90 workaround (rigs/icom/xiegu.c, LGPL-2.1-or-later),
    see docs/third-party.md; this code is our own.
    """
    if len(data) != 2:
        raise CivParseError(f"level has {len(data)} bytes, expected 2")
    hundreds, tens, nibble = data[0], data[1] >> 4, data[1] & 0x0F
    if hundreds > 2 or tens > 9:
        raise CivParseError(f"level {data.hex()} is not a G90 level")
    prefix = 100 * hundreds + 10 * tens
    units = (nibble - prefix) % 16
    value = prefix + units
    if units > 9 or value > 255:
        raise CivParseError(f"level {data.hex()} is not a G90 level")
    return value


def g90_level_to_bytes(value: int) -> bytes:
    """The G90's encoding of a 0-255 level (the inverse of g90_level_from_bytes)."""
    if not 0 <= value <= 255:
        raise ValueError(f"level {value} outside 0-255")
    return bytes([value // 100, ((value // 10 % 10) << 4) | (value & 0x0F)])


def power_meter_from_frame(frame: Frame) -> int:
    """Level 0-255 of a power meter reply (``15 11``)."""
    if frame.command != Command.READ_METER or frame.body[:1] != bytes([POWER_METER_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not a power meter reply")
    return g90_level_from_bytes(frame.body[1:])


def read_swr_meter_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its SWR meter (``15 12``). Scale UNVERIFIED."""
    return build_frame(to_address, from_address, Command.READ_METER, SWR_METER_SUBCOMMAND)


def read_s_meter_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its S-meter (``15 02``)."""
    return build_frame(to_address, from_address, Command.READ_METER, S_METER_SUBCOMMAND)


def s_meter_from_frame(frame: Frame) -> int:
    """Level 0-255 of an S-meter reply (``15 02``); 242 means below the scale (measured)."""
    if frame.command != Command.READ_METER or frame.body[:1] != bytes([S_METER_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an S-meter reply")
    return g90_level_from_bytes(frame.body[1:])


def read_alc_request(to_address: int, from_address: int) -> bytes:
    """Frame asking the radio for its ALC meter (``15 13``)."""
    return build_frame(to_address, from_address, Command.READ_METER, ALC_SUBCOMMAND)


def alc_from_frame(frame: Frame) -> int:
    """Level 0-255 of an ALC meter reply (``15 13``)."""
    if frame.command != Command.READ_METER or frame.body[:1] != bytes([ALC_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an ALC meter reply")
    return g90_level_from_bytes(frame.body[1:])


def swr_meter_from_frame(frame: Frame) -> int:
    """Level 0-255 of an SWR meter reply (``15 12``)."""
    if frame.command != Command.READ_METER or frame.body[:1] != bytes([SWR_METER_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an SWR meter reply")
    return g90_level_from_bytes(frame.body[1:])


def rf_power_from_frame(frame: Frame) -> int:
    """Level 0-255 of an RF power setting reply (``14 0A``)."""
    if frame.command != Command.LEVEL or frame.body[:1] != bytes([RF_POWER_SUBCOMMAND]):
        raise CivParseError(f"command 0x{frame.command:02X} is not an RF power reply")
    return g90_level_from_bytes(frame.body[1:])


def set_frequency_request(to_address: int, from_address: int, hz: int) -> bytes:
    """Frame setting the operating frequency."""
    return build_frame(to_address, from_address, Command.SET_FREQUENCY, data=encode_frequency(hz))


def set_mode_request(
    to_address: int, from_address: int, mode: Mode, filter_code: int | None = None
) -> bytes:
    """Frame setting the operating mode, followed by ``filter_code`` if given.

    The owner's G90 (firmware 1.81, checked 2026-11-02) answers NG to a mode
    without the filter byte, so callers pass the filter the radio reported.
    """
    data = bytes([mode]) if filter_code is None else bytes([mode, filter_code])
    return build_frame(to_address, from_address, Command.SET_MODE, data=data)


def frequency_from_frame(frame: Frame) -> int:
    """Extract the frequency from a read-frequency reply or frequency broadcast.

    The field must have exactly ``FREQUENCY_BYTES`` bytes: on the owner's G90 a
    byte was seen to go missing on the bus, and a shortened field would still
    decode, to a wrong frequency.
    """
    if frame.command not in (Command.READ_FREQUENCY, Command.TRANSCEIVE_FREQUENCY):
        raise CivParseError(f"command 0x{frame.command:02X} does not carry a frequency")
    if len(frame.body) != FREQUENCY_BYTES:
        raise CivParseError(
            f"frequency field has {len(frame.body)} bytes, expected {FREQUENCY_BYTES}"
        )
    return decode_frequency(frame.body)


def mode_from_frame(frame: Frame) -> Mode:
    """Extract the mode from a read-mode reply or mode broadcast.

    The optional trailing filter byte is ignored.
    """
    return mode_and_filter_from_frame(frame)[0]


def mode_and_filter_from_frame(frame: Frame) -> tuple[Mode, int | None]:
    """Extract the mode and the filter byte (``None`` if absent) from a mode reply."""
    if frame.command not in (Command.READ_MODE, Command.TRANSCEIVE_MODE):
        raise CivParseError(f"command 0x{frame.command:02X} does not carry a mode")
    if not 1 <= len(frame.body) <= 2:
        raise CivParseError(f"mode reply has {len(frame.body)} data bytes, expected 1 or 2")
    filter_code = frame.body[1] if len(frame.body) == 2 else None
    return mode_from_code(frame.body[0]), filter_code
