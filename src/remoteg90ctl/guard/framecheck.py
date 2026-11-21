# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation of CI-V frames sent by the server, followed by the allowlist.

AGENTS.md rule 3: one message is exactly one valid frame. The message is checked
as a whole first (hex syntax, single preamble, single trailing FD, no reserved
bytes inside, addresses, length); only then is the command compared with the
allowlist. The guard reads addresses, command and sub-command and the length of
the data — never the data itself. Anything else is rejected in full.

For the menu command ``1A 05`` the 2-byte item number counts as part of the
sub-command (decisions.md D37): the allowlist names items, and reading and
writing each item are separate entries. Values are still not interpreted.
The same mechanism names the two allowed tuner frames ``1C 01 00`` and
``1C 01 01`` (D43); ``1C 01 02`` (start tuning) is not on the list.
"""

import re
from dataclasses import dataclass

from remoteg90ctl.civ import (
    AF_SUBCOMMAND,
    AGC_SUBCOMMAND,
    ALC_SUBCOMMAND,
    ATU_OFF,
    ATU_ON,
    ATU_SUBCOMMAND,
    DATA_MODE_SUBCOMMAND,
    FREQUENCY_BYTES,
    MAX_FRAME_LENGTH,
    NB_LEVEL_SUBCOMMAND,
    NB_SUBCOMMAND,
    POWER_METER_SUBCOMMAND,
    PREAMP_SUBCOMMAND,
    RF_GAIN_SUBCOMMAND,
    RF_POWER_SUBCOMMAND,
    S_METER_SUBCOMMAND,
    SWR_METER_SUBCOMMAND,
    CivParseError,
    Command,
    Frame,
    parse_frame,
)

_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")
MAX_HEX_LENGTH = 2 * MAX_FRAME_LENGTH


@dataclass(frozen=True, slots=True)
class AllowedCommand:
    """One allowlist entry.

    ``subcommand`` is ``None`` for commands without one. ``item`` is the menu
    item number for ``1A 05`` (D37), else ``None``. ``body_lengths`` is the range
    of body sizes (sub-command, item and data) accepted. ``changes_radio_state``
    marks commands refused while a transmission is active (decisions.md D24.7).
    """

    command: int
    subcommand: int | None
    body_lengths: range
    changes_radio_state: bool
    item: bytes | None = None

    def matches(self, frame: Frame) -> bool:
        """Same command, sub-command and item as ``frame`` (length aside)."""
        if self.command != frame.command:
            return False
        if self.subcommand is not None and frame.body[:1] != bytes([self.subcommand]):
            return False
        return self.item is None or frame.body[1 : 1 + len(self.item)] == self.item


# Deny by default: only these leave the guard. Command numbers follow the Icom
# convention; their support on the G90 is UNVERIFIED (AGENTS.md section 8).
ALLOWLIST: tuple[AllowedCommand, ...] = (
    AllowedCommand(Command.READ_FREQUENCY, None, range(0, 1), changes_radio_state=False),
    AllowedCommand(Command.READ_MODE, None, range(0, 1), changes_radio_state=False),
    # Exactly FREQUENCY_BYTES (5) BCD bytes; the width itself is UNVERIFIED.
    AllowedCommand(
        Command.SET_FREQUENCY,
        None,
        range(FREQUENCY_BYTES, FREQUENCY_BYTES + 1),
        changes_radio_state=True,
    ),
    # Mode, optionally followed by a filter byte.
    AllowedCommand(Command.SET_MODE, None, range(1, 3), changes_radio_state=True),
    # RF power output meter, read only (sub-command alone, no data); allowed
    # during TX, when it matters (decisions.md D33, owner's decision 2026-11-03).
    AllowedCommand(
        Command.READ_METER, POWER_METER_SUBCOMMAND, range(1, 2), changes_radio_state=False
    ),
    # S-meter, read only (stage 5, measured 2026-11-07).
    AllowedCommand(Command.READ_METER, S_METER_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    # ALC meter, read only; allowed during TX (stage 5, measured 2026-11-08).
    AllowedCommand(Command.READ_METER, ALC_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    # SWR meter, read only (owner's decision 2026-11-03); allowed during TX.
    AllowedCommand(
        Command.READ_METER, SWR_METER_SUBCOMMAND, range(1, 2), changes_radio_state=False
    ),
    # RF power setting: read (the sub-command alone, owner's decision
    # 2026-11-03) and set (sub-command and exactly 2 data bytes, owner's
    # decision 2026-11-03, D44). The guard does not interpret the value: the
    # upper limit (safety.max_rf_power_w) is the server's. Setting is refused
    # during a transmission, like frequency and mode.
    AllowedCommand(Command.LEVEL, RF_POWER_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.LEVEL, RF_POWER_SUBCOMMAND, range(3, 4), changes_radio_state=True),
    # Speaker volume (AF), read and write (category B, owner's decision
    # 2026-11-04): the server mutes the speaker of a remote station.
    AllowedCommand(Command.LEVEL, AF_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.LEVEL, AF_SUBCOMMAND, range(3, 4), changes_radio_state=True),
    # Mode with the data flag (measured 2026-11-07; U-D/L-D for the DE-19's
    # audio): read, and set with exactly mode, data flag and filter.
    # Category B; refused during TX.
    AllowedCommand(Command.DATA_MODE, DATA_MODE_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.DATA_MODE, DATA_MODE_SUBCOMMAND, range(4, 5), changes_radio_state=True),
    # Preamplifier (16 02) and attenuator (11), category B, measured on the
    # owner's G90 (firmware 1.81, 2026-11-07; owner's decision for stage 5):
    # read, and set with exactly one data byte. Setting is refused during TX.
    AllowedCommand(Command.FUNCTION, PREAMP_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.FUNCTION, PREAMP_SUBCOMMAND, range(2, 3), changes_radio_state=True),
    AllowedCommand(Command.ATTENUATOR, None, range(0, 1), changes_radio_state=False),
    AllowedCommand(Command.ATTENUATOR, None, range(1, 2), changes_radio_state=True),
    # AGC (16 12), category B, measured 2026-11-07 (stage 5): read, and set
    # with one data byte; refused during TX.
    AllowedCommand(Command.FUNCTION, AGC_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.FUNCTION, AGC_SUBCOMMAND, range(2, 3), changes_radio_state=True),
    # RF gain (14 02, two data bytes), category B, receive only; measured
    # 2026-11-07 (stage 5): read and set; refused during TX.
    AllowedCommand(Command.LEVEL, RF_GAIN_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.LEVEL, RF_GAIN_SUBCOMMAND, range(3, 4), changes_radio_state=True),
    # Noise blanker on/off (16 22) and level (14 12, two data bytes), category
    # B, measured 2026-11-07 (stage 5): read and set; refused during TX.
    AllowedCommand(Command.FUNCTION, NB_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.FUNCTION, NB_SUBCOMMAND, range(2, 3), changes_radio_state=True),
    AllowedCommand(Command.LEVEL, NB_LEVEL_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.LEVEL, NB_LEVEL_SUBCOMMAND, range(3, 4), changes_radio_state=True),
    # Antenna tuner (decisions.md D43): read, and switching off or on, as exact
    # frames (the data byte counts as the item, D37). 1C 01 02 starts a tuning,
    # which transmits: it never passes here, only the guard's atu_tune sends it.
    AllowedCommand(Command.TX_CONTROL, ATU_SUBCOMMAND, range(1, 2), changes_radio_state=False),
    AllowedCommand(
        Command.TX_CONTROL,
        ATU_SUBCOMMAND,
        range(2, 3),
        changes_radio_state=True,
        item=bytes([ATU_OFF]),
    ),
    AllowedCommand(
        Command.TX_CONTROL,
        ATU_SUBCOMMAND,
        range(2, 3),
        changes_radio_state=True,
        item=bytes([ATU_ON]),
    ),
    # VFO A/B and split (stage 5, measured 2026-11-08): selecting a VFO or
    # switching split changes where the radio transmits, so both are refused
    # during TX; the server checks split and the transmit VFO before TX.
    AllowedCommand(Command.SELECT_VFO, 0x00, range(1, 2), changes_radio_state=True),
    AllowedCommand(Command.SELECT_VFO, 0x01, range(1, 2), changes_radio_state=True),
    AllowedCommand(Command.SPLIT, None, range(0, 1), changes_radio_state=False),
    AllowedCommand(Command.SPLIT, 0x00, range(1, 2), changes_radio_state=True),
    AllowedCommand(Command.SPLIT, 0x01, range(1, 2), changes_radio_state=True),
    # Frequency of the selected (25 00) / unselected (25 01) VFO, read only.
    AllowedCommand(Command.VFO_FREQUENCY, 0x00, range(1, 2), changes_radio_state=False),
    AllowedCommand(Command.VFO_FREQUENCY, 0x01, range(1, 2), changes_radio_state=False),
    # Mode with data flag of the unselected VFO (26 01), read only.
    AllowedCommand(Command.DATA_MODE, 0x01, range(1, 2), changes_radio_state=False),
)
# No menu item is allowed (D37): the RX filter item 00 10 is answered NG by the
# G90 (firmware 1.81) both when read and when written.


class FrameRejectedError(Exception):
    """The message must not reach the radio. ``code`` is a protocol error code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


FRAME_INVALID = "frame_invalid"
COMMAND_NOT_ALLOWED = "command_not_allowed"


def check_frame(
    message: object, *, radio_address: int, controller_address: int
) -> tuple[Frame, AllowedCommand]:
    """Validate a hex-encoded message; return the frame and its allowlist entry.

    Raises :class:`FrameRejectedError` for anything that is not exactly one valid,
    allowed frame from the controller to the radio.
    """
    if not isinstance(message, str):
        raise FrameRejectedError(FRAME_INVALID, "frame must be a hex string")
    if len(message) > MAX_HEX_LENGTH:
        raise FrameRejectedError(FRAME_INVALID, f"frame longer than {MAX_FRAME_LENGTH} bytes")
    # fullmatch, not bytes.fromhex: fromhex silently accepts whitespace.
    if not _HEX.fullmatch(message):
        raise FrameRejectedError(FRAME_INVALID, "frame is not an even-length hex string")
    try:
        frame = parse_frame(bytes.fromhex(message))
    except CivParseError as exc:
        raise FrameRejectedError(FRAME_INVALID, str(exc)) from None
    if frame.to_address != radio_address:
        raise FrameRejectedError(
            FRAME_INVALID, f"destination 0x{frame.to_address:02X} is not the radio address"
        )
    if frame.from_address != controller_address:
        raise FrameRejectedError(
            FRAME_INVALID, f"source 0x{frame.from_address:02X} is not the controller address"
        )
    candidates = [entry for entry in ALLOWLIST if entry.matches(frame)]
    if not candidates:
        raise FrameRejectedError(COMMAND_NOT_ALLOWED, f"{_describe(frame)} is not on the allowlist")
    for entry in candidates:
        if len(frame.body) in entry.body_lengths:
            return frame, entry
    raise FrameRejectedError(
        COMMAND_NOT_ALLOWED,
        f"{_describe(frame)} with {len(frame.body)} body bytes is not on the allowlist",
    )


def _describe(frame: Frame) -> str:
    # The first body byte is shown because it is the sub-command when one exists.
    sub = f" (first body byte 0x{frame.body[0]:02X})" if frame.body else ""
    return f"command 0x{frame.command:02X}{sub}"
