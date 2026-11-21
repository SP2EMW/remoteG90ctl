# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural model of a G90 answering basic CI-V commands.

The model follows the Icom CI-V convention. It is a test double, not a
description of the real radio: every behaviour here is UNVERIFIED against a G90
(AGENTS.md section 8).
"""

import logging

from remoteg90ctl.civ import (
    AF_SUBCOMMAND,
    AGC_MODES,
    AGC_SUBCOMMAND,
    ALC_SUBCOMMAND,
    ATTENUATOR_OFF,
    ATTENUATOR_ON,
    ATU_ON,
    ATU_SUBCOMMAND,
    ATU_TUNE,
    BROADCAST_ADDRESS,
    DATA_MODE_SUBCOMMAND,
    NB_LEVEL_SUBCOMMAND,
    NB_SUBCOMMAND,
    POWER_METER_SUBCOMMAND,
    PREAMP_ON,
    PREAMP_SUBCOMMAND,
    READ_ID_SUBCOMMAND,
    REPLY_NG,
    REPLY_OK,
    RF_GAIN_SUBCOMMAND,
    RF_POWER_SUBCOMMAND,
    S_METER_SUBCOMMAND,
    SWR_METER_SUBCOMMAND,
    XIEGU_ID_SUBCOMMAND,
    CivError,
    Command,
    Frame,
    Mode,
    decode_frequency,
    encode_frequency,
    g90_level_to_bytes,
    mode_from_code,
    parse_frame,
)
from remoteg90ctl.logging_setup import kv

logger = logging.getLogger(__name__)

# Default filter byte returned with the mode (Icom "FIL1"; the owner's G90 reports 01).
_DEFAULT_FILTER = 0x01
# Body of the reply to "1D 19" (sub-command echoed, then 0x0090 per the Xiegu
# CI-V Reference). UNVERIFIED.
DEFAULT_IDENTITY = b"\x19\x00\x90"


class SimulatedRadio:
    """Holds frequency and mode and answers CI-V request frames."""

    def __init__(
        self,
        address: int,
        frequency_hz: int = 7_074_000,
        mode: Mode = Mode.USB,
        identity: bytes | None = DEFAULT_IDENTITY,
    ) -> None:
        """``identity=None`` answers ``1D 19`` with NG, like firmware before 1.8."""
        self.address = address
        self.identity = identity
        self.frequency_hz = frequency_hz
        self.mode = mode
        self.filter_code = _DEFAULT_FILTER
        self.received: list[bytes] = []
        # Set by the simulated port: a PTT line is asserted.
        self.keyed = False
        # Power meter level (0-255) while keyed and emitting; 171 was measured at
        # 1 W. Tests set 0 for PTT without modulation.
        self.power_meter = 171
        # RF power setting level (0-255).
        self.rf_power = 255
        # Added to a written power level: tests use it for a radio that decodes
        # a written level differently (D44; the real G90 is UNVERIFIED).
        self.rf_power_write_error = 0
        # SWR meter level (0-255) while keyed.
        self.swr_meter = 0
        self.s_meter = 106  # S7 on the owner's G90 (measured)
        # ALC meter level (0-255) while keyed; 0 in receive (measured).
        self.alc = 0
        self.atu_on = False
        # Preamplifier and attenuator exclude each other (measured 2026-11-07).
        self.preamp_on = False
        self.attenuator_on = False
        self.agc = 0x03  # auto
        self.nb_on = False
        self.nb_level = 127  # 0-255 as reported: floor(step * 25.5), step 5
        self.rf_gain = 255  # 0-255 as reported: floor((p - 1) * 255 / 99), 100 %
        self.data_mode = False  # U-D / L-D; 0x06 leaves it as it is (measured)
        self.af = 128  # speaker volume
        self.tunings = 0  # 1C 01 02 received (a real radio would transmit)
        # A tuning shows on the power meter for this many reads, then 0, like the
        # G90 (about 9 s with a poor match, measured 2026-11-13). The level read
        # meanwhile is the end of the scale, as measured.
        self.tune_meter_reads = 2
        self.tune_meter_level = 255
        self._tune_reads_left = 0
        # Two VFOs (measured 2026-11-08): the fields above are the selected one;
        # the other is kept here as (frequency, mode, data flag, filter).
        self.vfo = "A"
        self.other_vfo: tuple[int, Mode, bool, int] = (frequency_hz, mode, False, _DEFAULT_FILTER)
        self.split = False

    def handle(self, message: bytes) -> bytes:
        """Process one message ending with FD; return the reply bytes (may be empty).

        Like a real radio on a shared bus, malformed frames and frames addressed
        to someone else are ignored without a reply.
        """
        self.received.append(message)
        try:
            frame = parse_frame(message)
        except CivError as exc:
            logger.debug("sim_ignored_frame", extra=kv(reason=str(exc), data=message.hex()))
            return b""
        if frame.to_address != self.address:
            return b""
        return self._reply(frame).to_bytes()

    def broadcast_frequency(self) -> bytes:
        """Unsolicited frequency report, as sent with CI-V transceive enabled."""
        return Frame(
            BROADCAST_ADDRESS,
            self.address,
            Command.TRANSCEIVE_FREQUENCY,
            encode_frequency(self.frequency_hz),
        ).to_bytes()

    def _reply(self, frame: Frame) -> Frame:
        to = frame.from_address
        match frame.command:
            case Command.READ_FREQUENCY if not frame.body:
                return Frame(
                    to, self.address, Command.READ_FREQUENCY, encode_frequency(self.frequency_hz)
                )
            case Command.READ_MODE if not frame.body:
                return Frame(
                    to, self.address, Command.READ_MODE, bytes([self.mode, self.filter_code])
                )
            case Command.READ_ID if frame.body == bytes([READ_ID_SUBCOMMAND]):
                # Like the owner's G90: the answer is the radio's CI-V address.
                return Frame(to, self.address, Command.READ_ID, bytes([self.address]))
            case Command.XIEGU if frame.body == bytes([XIEGU_ID_SUBCOMMAND]):
                if self.identity is None:
                    return Frame(to, self.address, REPLY_NG)
                return Frame(to, self.address, Command.XIEGU, self.identity)
            case Command.READ_METER | Command.LEVEL:
                return self._levels(to, frame)
            case Command.DATA_MODE | Command.SELECT_VFO | Command.SPLIT | Command.VFO_FREQUENCY:
                return self._vfo(to, frame)
            case Command.TX_CONTROL:
                return self._tuner(to, frame.body)
            case Command.FUNCTION | Command.ATTENUATOR:
                return self._front_end(to, frame)
            case Command.SET_FREQUENCY:
                return self._set_frequency(to, frame.body)
            case Command.SET_MODE:
                return self._set_mode(to, frame.body)
            case _:
                return Frame(to, self.address, REPLY_NG)

    def _levels(self, to: int, frame: Frame) -> Frame:
        """Meters (15 11 power, 15 12 SWR, measured while keyed) and the power setting."""
        if frame.command == Command.LEVEL and len(frame.body) == 3:
            hundreds, tens, units = frame.body[1], frame.body[2] >> 4, frame.body[2] & 0x0F
            written = min(255, 100 * hundreds + 10 * tens + units)
            if frame.body[0] == AF_SUBCOMMAND:
                self.af = written
                return Frame(to, self.address, REPLY_OK)
            if frame.body[0] == RF_GAIN_SUBCOMMAND:
                # Like the G90 (measured): cut down to a whole percent.
                percent = 1 + written * 99 // 255
                self.rf_gain = (percent - 1) * 255 // 99
                return Frame(to, self.address, REPLY_OK)
            if frame.body[0] == NB_LEVEL_SUBCOMMAND:
                # Like the G90 (measured): cut down to a menu step 0-10,
                # reported as floor(step * 25.5).
                step = written * 10 // 255
                self.nb_level = step * 255 // 10
                return Frame(to, self.address, REPLY_OK)
            return self._set_rf_power(to, frame.body)
        power = self.power_meter if self.keyed else 0
        reading_power = (frame.command, frame.body[:1]) == (
            Command.READ_METER,
            bytes([POWER_METER_SUBCOMMAND]),
        )
        if reading_power and self._tune_reads_left > 0:
            self._tune_reads_left -= 1
            power = max(power, self.tune_meter_level)
        values = {
            (Command.READ_METER, POWER_METER_SUBCOMMAND): power,
            (Command.READ_METER, SWR_METER_SUBCOMMAND): self.swr_meter if self.keyed else 0,
            (Command.READ_METER, S_METER_SUBCOMMAND): self.s_meter,
            (Command.READ_METER, ALC_SUBCOMMAND): self.alc if self.keyed else 0,
            (Command.LEVEL, RF_POWER_SUBCOMMAND): self.rf_power,
            (Command.LEVEL, AF_SUBCOMMAND): self.af,
            (Command.LEVEL, NB_LEVEL_SUBCOMMAND): self.nb_level,
            (Command.LEVEL, RF_GAIN_SUBCOMMAND): self.rf_gain,
        }
        key = (frame.command, frame.body[0]) if len(frame.body) == 1 else None
        if key not in values:
            return Frame(to, self.address, REPLY_NG)
        data = frame.body + g90_level_to_bytes(values[key])
        return Frame(to, self.address, frame.command, data)

    def _set_rf_power(self, to: int, body: bytes) -> Frame:
        """14 0A + 2 bytes of plain BCD 0000-0255 (as the server sends it)."""
        hundreds, tens, units = body[1], body[2] >> 4, body[2] & 0x0F
        level = 100 * hundreds + 10 * tens + units
        if body[0] != RF_POWER_SUBCOMMAND or hundreds > 2 or tens > 9 or units > 9 or level > 255:
            return Frame(to, self.address, REPLY_NG)
        # Like the G90 (measured 2026-11-04): the written level becomes whole
        # watts rounding down, and the level reported back is that of the watts,
        # also rounded down (54 written: 5 W, reported 53; 53 written: 4 W, 40).
        written = max(0, min(255, level + self.rf_power_write_error))
        watts = int(1 + 19 * written / 255 + 1e-9)
        self.rf_power = int((watts - 1) * 255 / 19 + 1e-9)
        return Frame(to, self.address, REPLY_OK)

    def _vfo(self, to: int, frame: Frame) -> Frame:
        """07 00/01 select, 0F read/set split, 25 00/01 frequency (measured 2026-11-08)."""
        body = frame.body
        if frame.command == Command.DATA_MODE:
            return self._data_mode(to, body)
        if frame.command == Command.SELECT_VFO and len(body) == 1 and body[0] in (0, 1):
            wanted = "AB"[body[0]]
            if wanted != self.vfo:
                selected = (self.frequency_hz, self.mode, self.data_mode, self.filter_code)
                self.frequency_hz, self.mode, self.data_mode, self.filter_code = self.other_vfo
                self.other_vfo = selected
                self.vfo = wanted
            return Frame(to, self.address, REPLY_OK)
        if frame.command == Command.SPLIT and not body:
            return Frame(to, self.address, Command.SPLIT, bytes([int(self.split)]))
        if frame.command == Command.SPLIT and len(body) == 1 and body[0] in (0, 1):
            self.split = body[0] == 1
            return Frame(to, self.address, REPLY_OK)
        if frame.command == Command.VFO_FREQUENCY and len(body) == 1 and body[0] in (0, 1):
            hz = self.other_vfo[0] if body[0] else self.frequency_hz
            # Like the G90: the reply's sub-command byte is the selected VFO.
            data = bytes([0 if self.vfo == "A" else 1]) + encode_frequency(hz)
            return Frame(to, self.address, Command.VFO_FREQUENCY, data)
        return Frame(to, self.address, REPLY_NG)

    def _data_mode(self, to: int, body: bytes) -> Frame:
        """26 00: read, or set mode + data flag + filter (no filter byte: NG, measured).

        26 01: read the other VFO (its data flag is right on firmware 1.81, measured).
        """
        if body == bytes([DATA_MODE_SUBCOMMAND]):
            data = bytes([DATA_MODE_SUBCOMMAND, self.mode, int(self.data_mode), self.filter_code])
            return Frame(to, self.address, Command.DATA_MODE, data)
        if body == b"\x01":
            _, mode, data_flag, filter_code = self.other_vfo
            data = bytes([0x01, mode, int(data_flag), filter_code])
            return Frame(to, self.address, Command.DATA_MODE, data)
        if len(body) == 4 and body[0] == DATA_MODE_SUBCOMMAND and body[2] in (0, 1):
            try:
                self.mode = mode_from_code(body[1])
            except CivError:
                return Frame(to, self.address, REPLY_NG)
            self.data_mode = body[2] == 1
            self.filter_code = body[3]
            return Frame(to, self.address, REPLY_OK)
        return Frame(to, self.address, REPLY_NG)

    def _tuner(self, to: int, body: bytes) -> Frame:
        """1C 01: read, off (00), on (01), tune (02). Anything else (1C 00 is PTT): NG."""
        if body == bytes([ATU_SUBCOMMAND]):
            data = bytes([ATU_SUBCOMMAND, ATU_ON if self.atu_on else 0])
            return Frame(to, self.address, Command.TX_CONTROL, data)
        if len(body) == 2 and body[0] == ATU_SUBCOMMAND and body[1] in (0, ATU_ON, ATU_TUNE):
            if body[1] == ATU_TUNE:
                self.tunings += 1
                self._tune_reads_left = self.tune_meter_reads
                self.atu_on = True  # the G90 leaves the tuner on after tuning (measured)
            else:
                # Like the G90 (seen 2026-11-08): 00 or 01 toggles the tuner.
                self.atu_on = not self.atu_on
            return Frame(to, self.address, REPLY_OK)
        return Frame(to, self.address, REPLY_NG)

    def _front_end(self, to: int, frame: Frame) -> Frame:
        if frame.command == Command.FUNCTION and frame.body[:1] == bytes([AGC_SUBCOMMAND]):
            return self._agc(to, frame.body)
        if frame.command == Command.FUNCTION and frame.body[:1] == bytes([NB_SUBCOMMAND]):
            if len(frame.body) == 1:
                data = bytes([NB_SUBCOMMAND, int(self.nb_on)])
                return Frame(to, self.address, Command.FUNCTION, data)
            if len(frame.body) == 2 and frame.body[1] in (0, 1):
                self.nb_on = frame.body[1] == 1
                return Frame(to, self.address, REPLY_OK)
            return Frame(to, self.address, REPLY_NG)
        if frame.command == Command.FUNCTION:
            return self._preamp(to, frame.body)
        return self._attenuator(to, frame.body)

    def _agc(self, to: int, body: bytes) -> Frame:
        """16 12: read, or set 00-03 (OK)."""
        if len(body) == 1:
            return Frame(to, self.address, Command.FUNCTION, bytes([AGC_SUBCOMMAND, self.agc]))
        if len(body) == 2 and body[1] in AGC_MODES:
            self.agc = body[1]
            return Frame(to, self.address, REPLY_OK)
        return Frame(to, self.address, REPLY_NG)

    def _preamp(self, to: int, body: bytes) -> Frame:
        """16 02: read, or set (OK; any non-zero value is on, 02 included, measured)."""
        if body == bytes([PREAMP_SUBCOMMAND]):
            data = bytes([PREAMP_SUBCOMMAND, PREAMP_ON if self.preamp_on else 0])
            return Frame(to, self.address, Command.FUNCTION, data)
        if len(body) == 2 and body[0] == PREAMP_SUBCOMMAND:
            self.preamp_on = body[1] != 0
            if self.preamp_on:
                self.attenuator_on = False
            return Frame(to, self.address, REPLY_OK)
        return Frame(to, self.address, REPLY_NG)

    def _attenuator(self, to: int, body: bytes) -> Frame:
        """11: read, or toggle; like the G90, any data toggles it (11 00 too) and
        the answer is the new state, not OK."""
        if len(body) > 1:
            return Frame(to, self.address, REPLY_NG)
        if body:
            self.attenuator_on = not self.attenuator_on
            if self.attenuator_on:
                self.preamp_on = False
        state = ATTENUATOR_ON if self.attenuator_on else ATTENUATOR_OFF
        return Frame(to, self.address, Command.ATTENUATOR, bytes([state]))

    def _set_frequency(self, to: int, body: bytes) -> Frame:
        try:
            if len(body) != len(encode_frequency(0)):
                raise CivError("wrong frequency field length")
            self.frequency_hz = decode_frequency(body)
        except CivError:
            return Frame(to, self.address, REPLY_NG)
        return Frame(to, self.address, REPLY_OK)

    def _set_mode(self, to: int, body: bytes) -> Frame:
        # Like the owner's G90 (firmware 1.81, 2026-11-02): mode without the
        # filter byte is refused with NG.
        try:
            if len(body) != 2:
                raise CivError("mode must be followed by a filter byte")
            self.mode = mode_from_code(body[0])
        except CivError:
            return Frame(to, self.address, REPLY_NG)
        self.filter_code = body[1]
        return Frame(to, self.address, REPLY_OK)
