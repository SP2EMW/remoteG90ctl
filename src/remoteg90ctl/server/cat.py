# SPDX-License-Identifier: GPL-3.0-or-later
"""High-level CAT operations over CI-V, sent through the guard.

One transaction at a time: a request frame goes to the guard, then the reply is
awaited among the ``civ_rx`` events. Unrelated frames (e.g. transceive
broadcasts) are skipped while waiting.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from remoteg90ctl.civ import (
    CivError,
    Command,
    Frame,
    Mode,
    af_from_frame,
    agc_from_frame,
    alc_from_frame,
    attenuator_from_frame,
    atu_from_frame,
    data_mode_from_frame,
    frequency_from_frame,
    mode_and_filter_from_frame,
    nb_from_frame,
    nb_level_from_frame,
    parse_frame,
    power_meter_from_frame,
    preamp_from_frame,
    read_af_request,
    read_agc_request,
    read_alc_request,
    read_attenuator_request,
    read_atu_request,
    read_data_mode_request,
    read_frequency_request,
    read_mode_request,
    read_nb_level_request,
    read_nb_request,
    read_power_meter_request,
    read_preamp_request,
    read_rf_gain_request,
    read_rf_power_request,
    read_s_meter_request,
    read_split_request,
    read_swr_meter_request,
    read_vfo_frequency_request,
    rf_gain_from_frame,
    rf_power_from_frame,
    s_meter_from_frame,
    select_vfo_request,
    set_af_request,
    set_agc_request,
    set_attenuator_request,
    set_atu_request,
    set_data_mode_request,
    set_frequency_request,
    set_mode_request,
    set_nb_level_request,
    set_nb_request,
    set_preamp_request,
    set_rf_gain_request,
    set_rf_power_request,
    set_split_request,
    split_from_frame,
    swr_meter_from_frame,
    vfo_frequency_from_frame,
)
from remoteg90ctl.logging_setup import kv
from remoteg90ctl.server.guard_client import GuardClient

logger = logging.getLogger(__name__)

REPLY_TIMEOUT_S = 1.0
# After a timeout, frames arriving this long before the next request are
# discarded, so a late reply is not taken as the answer to the next question.
STALE_DRAIN_S = 0.3


class CatError(Exception):
    """A CAT operation failed; ``code`` is a server protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Cat:
    def __init__(
        self,
        guard: GuardClient,
        *,
        radio_address: int,
        controller_address: int,
        reply_timeout_s: float = REPLY_TIMEOUT_S,
    ) -> None:
        self._guard = guard
        self._radio = radio_address
        self._controller = controller_address
        self._reply_timeout_s = reply_timeout_s
        # One CAT exchange at a time. A tuning request goes before the reads that
        # are waiting (follow loop, power meter), so dragging the fine slider or
        # the waterfall is not queued behind them (owner's decision 2026-11-08).
        self._bus = asyncio.Condition()
        self._busy = False
        self._priority_waiting = 0
        self._replies: asyncio.Queue[Frame] | None = None
        self._drain_before_next = False

    def on_civ_rx(self, frame_hex: object) -> None:
        """Feed a ``civ_rx`` event from the guard."""
        if self._replies is None or not isinstance(frame_hex, str):
            return
        try:
            frame = parse_frame(bytes.fromhex(frame_hex))
        except (ValueError, CivError):
            logger.warning("cat_bad_frame_from_guard", extra=kv(frame=frame_hex))
            return
        self._replies.put_nowait(frame)

    async def get_frequency(self) -> int:
        reply = await self._transact(
            read_frequency_request(self._radio, self._controller), Command.READ_FREQUENCY
        )
        return self._decode(frequency_from_frame, reply)

    async def set_frequency(self, hz: int) -> None:
        try:
            request = set_frequency_request(self._radio, self._controller, hz)
        except CivError as exc:
            raise CatError("invalid_value", str(exc)) from None
        self._check_ack(await self._transact(request, None, priority=True))

    async def get_power_meter(self) -> tuple[int, str]:
        """RF power meter: the value and the raw reply hex."""
        reply = await self._transact(
            read_power_meter_request(self._radio, self._controller), Command.READ_METER
        )
        return self._decode(power_meter_from_frame, reply), reply.to_bytes().hex()

    async def get_swr_meter(self) -> tuple[int, str]:
        """SWR meter: the level 0-255 and the raw reply hex."""
        reply = await self._transact(
            read_swr_meter_request(self._radio, self._controller), Command.READ_METER
        )
        return self._decode(swr_meter_from_frame, reply), reply.to_bytes().hex()

    async def get_alc(self) -> int:
        """ALC meter level 0-255 (server/alc.py converts it)."""
        request = read_alc_request(self._radio, self._controller)
        return self._decode(alc_from_frame, await self._transact(request, Command.READ_METER))

    async def get_s_meter(self) -> int:
        """S-meter level 0-255 (server/smeter.py converts it)."""
        request = read_s_meter_request(self._radio, self._controller)
        return self._decode(s_meter_from_frame, await self._transact(request, Command.READ_METER))

    async def get_atu(self) -> bool:
        """Whether the antenna tuner is on."""
        reply = await self._transact(
            read_atu_request(self._radio, self._controller), Command.TX_CONTROL
        )
        return self._decode(atu_from_frame, reply)

    async def set_atu(self, on: bool) -> None:
        """Switch the antenna tuner on or off (no transmission)."""
        self._check_ack(
            await self._transact(set_atu_request(self._radio, self._controller, on), None)
        )

    async def get_front_end(self) -> str:
        """Preamplifier and attenuator as one state: "off", "pre" or "att"."""
        reply = await self._transact(
            read_attenuator_request(self._radio, self._controller), Command.ATTENUATOR
        )
        if self._decode(attenuator_from_frame, reply):
            return "att"
        reply = await self._transact(
            read_preamp_request(self._radio, self._controller), Command.FUNCTION
        )
        return "pre" if self._decode(preamp_from_frame, reply) else "off"

    async def set_preamp(self, on: bool) -> None:
        """Switch the preamplifier; on, the radio switches the attenuator off (measured)."""
        self._check_ack(
            await self._transact(set_preamp_request(self._radio, self._controller, on), None)
        )

    async def toggle_attenuator(self) -> bool:
        """Switch the attenuator over; returns its new state.

        The G90 toggles it on any data byte (11 00 switches it on when it is
        off) and answers with the new state, not with OK (measured 2026-11-07).
        """
        reply = await self._transact(
            set_attenuator_request(self._radio, self._controller, True), Command.ATTENUATOR
        )
        return self._decode(attenuator_from_frame, reply)

    async def get_agc(self) -> str:
        """AGC setting: "off", "fast", "slow" or "auto"."""
        reply = await self._transact(
            read_agc_request(self._radio, self._controller), Command.FUNCTION
        )
        return self._decode(agc_from_frame, reply)

    async def set_agc(self, agc: str) -> None:
        """Set the AGC (no transmission)."""
        self._check_ack(
            await self._transact(set_agc_request(self._radio, self._controller, agc), None)
        )

    async def get_nb(self) -> tuple[bool, int]:
        """Noise blanker: on, and its level as the menu step 0-10."""
        reply = await self._transact(
            read_nb_request(self._radio, self._controller), Command.FUNCTION
        )
        on = self._decode(nb_from_frame, reply)
        request = read_nb_level_request(self._radio, self._controller)
        level = self._decode(nb_level_from_frame, await self._transact(request, Command.LEVEL))
        return on, level

    async def set_nb(self, on: bool) -> None:
        """Switch the noise blanker on or off (no transmission)."""
        request = set_nb_request(self._radio, self._controller, on)
        self._check_ack(await self._transact(request, None))

    async def set_nb_level(self, step: int) -> None:
        """Set the noise blanker level, menu step 0-10 (no transmission)."""
        request = set_nb_level_request(self._radio, self._controller, step)
        self._check_ack(await self._transact(request, None))

    async def get_rf_gain(self) -> int:
        """RF gain, 1-100 %."""
        request = read_rf_gain_request(self._radio, self._controller)
        return self._decode(rf_gain_from_frame, await self._transact(request, Command.LEVEL))

    async def set_rf_gain(self, percent: int) -> None:
        """Set the RF gain, 1-100 % (no transmission)."""
        request = set_rf_gain_request(self._radio, self._controller, percent)
        self._check_ack(await self._transact(request, None))

    async def get_af(self) -> int:
        """Speaker volume 0-255."""
        reply = await self._transact(read_af_request(self._radio, self._controller), Command.LEVEL)
        return self._decode(af_from_frame, reply)

    async def set_af(self, level: int) -> None:
        """Set the speaker volume 0-255 (no transmission)."""
        self._check_ack(
            await self._transact(set_af_request(self._radio, self._controller, level), None)
        )

    async def get_rf_power(self) -> tuple[int, str]:
        """RF power setting: the level 0-255 and the raw reply hex."""
        reply = await self._transact(
            read_rf_power_request(self._radio, self._controller), Command.LEVEL
        )
        return self._decode(rf_power_from_frame, reply), reply.to_bytes().hex()

    async def set_rf_power(self, level: int) -> None:
        """Set the RF power level 0-255 (14 0A); the caller checks the limit."""
        self._check_ack(
            await self._transact(set_rf_power_request(self._radio, self._controller, level), None)
        )

    async def get_mode(self) -> Mode:
        return (await self._get_mode_and_filter())[0]

    async def set_mode(self, mode: Mode) -> None:
        # The G90 refuses a mode without the filter byte; keep the filter in use.
        _, filter_code = await self._get_mode_and_filter()
        request = set_mode_request(self._radio, self._controller, mode, filter_code)
        self._check_ack(await self._transact(request, None))

    async def get_data_mode(self, *, unselected: bool = False) -> tuple[Mode, bool]:
        """Mode and data flag (``26 00``, other VFO ``26 01``): U-D is USB with the flag."""
        request = read_data_mode_request(self._radio, self._controller, unselected=unselected)
        reply = await self._transact(request, Command.DATA_MODE)
        mode, data, _ = self._decode(data_mode_from_frame, reply)
        return mode, data

    async def set_data_mode(self, mode: Mode, data: bool) -> None:
        """Set mode and data flag, keeping the filter in use (the G90 needs it)."""
        reply = await self._transact(
            read_data_mode_request(self._radio, self._controller), Command.DATA_MODE
        )
        _, _, filter_code = self._decode(data_mode_from_frame, reply)
        request = set_data_mode_request(self._radio, self._controller, mode, data, filter_code)
        self._check_ack(await self._transact(request, None))

    async def get_vfo_frequency(self, *, unselected: bool) -> tuple[str, int]:
        """The selected VFO ("A"/"B") and the frequency of the selected or other VFO."""
        request = read_vfo_frequency_request(self._radio, self._controller, unselected=unselected)
        reply = await self._transact(request, Command.VFO_FREQUENCY)
        return self._decode(vfo_frequency_from_frame, reply)

    async def select_vfo(self, vfo: str) -> None:
        self._check_ack(
            await self._transact(select_vfo_request(self._radio, self._controller, vfo), None)
        )

    async def get_split(self) -> bool:
        reply = await self._transact(
            read_split_request(self._radio, self._controller), Command.SPLIT
        )
        return self._decode(split_from_frame, reply)

    async def set_split(self, on: bool) -> None:
        self._check_ack(
            await self._transact(set_split_request(self._radio, self._controller, on), None)
        )

    async def _get_mode_and_filter(self) -> tuple[Mode, int | None]:
        reply = await self._transact(
            read_mode_request(self._radio, self._controller), Command.READ_MODE
        )
        return self._decode(mode_and_filter_from_frame, reply)

    async def _acquire(self, *, priority: bool) -> None:
        if priority:
            self._priority_waiting += 1
        try:
            async with self._bus:
                await self._bus.wait_for(
                    lambda: not self._busy and (priority or self._priority_waiting == 0)
                )
                self._busy = True
        finally:
            if priority:
                self._priority_waiting -= 1

    async def _release(self) -> None:
        async with self._bus:
            self._busy = False
            self._bus.notify_all()

    async def _transact(
        self, request: bytes, reply_command: int | None, *, priority: bool = False
    ) -> Frame:
        """Send ``request``; wait for a reply with ``reply_command`` or, if None, OK/NG.

        ``priority``: go before the exchanges already waiting (tuning).
        """
        await self._acquire(priority=priority)
        try:
            return await self._exchange(request, reply_command)
        finally:
            await asyncio.shield(self._release())

    async def _exchange(self, request: bytes, reply_command: int | None) -> Frame:
        if self._drain_before_next:
            await self._discard_late_replies()
        replies: asyncio.Queue[Frame] = asyncio.Queue()
        self._replies = replies
        started = time.monotonic()
        try:
            response = await self._guard.request("civ", frame=request.hex())
            if not response.get("ok"):
                raise CatError(str(response.get("error")), str(response.get("message")))
            reply = await asyncio.wait_for(
                self._wait_reply(replies, reply_command), self._reply_timeout_s
            )
            logger.debug(
                "cat_reply",
                extra=kv(
                    request=request.hex(),
                    reply=reply.to_bytes().hex(),
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                ),
            )
            return reply
        except TimeoutError:
            logger.debug("cat_timeout", extra=kv(request=request.hex()))
            self._drain_before_next = True
            raise CatError("radio_timeout", "the radio did not answer") from None
        finally:
            self._replies = None

    async def _discard_late_replies(self) -> None:
        discarded: asyncio.Queue[Frame] = asyncio.Queue()
        self._replies = discarded
        await asyncio.sleep(STALE_DRAIN_S)
        self._replies = None
        self._drain_before_next = False
        if not discarded.empty():
            logger.warning("cat_late_replies_discarded", extra=kv(count=discarded.qsize()))

    async def _wait_reply(self, replies: asyncio.Queue[Frame], reply_command: int | None) -> Frame:
        while True:
            frame = await replies.get()
            if frame.to_address != self._controller or frame.from_address != self._radio:
                continue
            if reply_command is None and (frame.is_ok or frame.is_ng):
                return frame
            if reply_command is not None and (frame.command == reply_command or frame.is_ng):
                return frame

    @staticmethod
    def _check_ack(reply: Frame) -> None:
        if reply.is_ng:
            raise CatError("radio_rejected", "the radio refused the command (NG)")

    @staticmethod
    def _decode[T](decoder: Callable[[Frame], T], reply: Frame) -> T:
        if reply.is_ng:
            raise CatError("radio_rejected", "the radio refused the command (NG)")
        try:
            return decoder(reply)
        except CivError as exc:
            raise CatError("radio_bad_reply", f"unexpected reply from the radio: {exc}") from None
