# SPDX-License-Identifier: GPL-3.0-or-later
"""RTTY transmission (decisions.md D61): the signal, its keying, the server's rules.

The signal is decoded back by a small demodulator written here (two matched
filters and the framing), independent of the page's decoder. The failure
paths: the lease ending in the middle (the sound stops, the queue goes), a
stuck card, a client that is not a browser, bad text and tones.
"""

import asyncio
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from remoteg90ctl.audio.source import AUDIO_SAMPLE_RATE
from remoteg90ctl.server.rtty_tx import (
    BAUD,
    MAX_STALL_S,
    QUEUE_MAX,
    SHIFT_HZ,
    RttyError,
    RttyTransmitter,
    key,
)
from server_helpers import async_wait_for
from test_ft8_tx import FakeTime, web  # noqa: F401  (web is a fixture)
from test_web import OURS, ask

LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x1bMXV\x1f"
FIGS = "\x003\n- \x0787\r$4',!:(5\")2#6019?&\x1b./;\x1f"


def demodulate(pcm: npt.NDArray[np.int16], mark_hz: float) -> str:
    """Text from the audio: matched filters of one bit, then start/5 bits/stop."""
    x = pcm.astype(np.float64)
    t = np.arange(len(x)) / AUDIO_SAMPLE_RATE
    per_bit = AUDIO_SAMPLE_RATE / BAUD
    n = round(per_bit)
    kernel = np.ones(n)

    def strength(hz: float) -> npt.NDArray[np.float64]:
        mixed = x * np.exp(-2j * np.pi * hz * t)
        return np.abs(np.convolve(mixed, kernel)[: len(x)])

    bits = (strength(mark_hz) > strength(mark_hz + SHIFT_HZ)).astype(int)
    text = ""
    figures = False
    i = 1
    while i < len(bits):
        if bits[i - 1] == 1 and bits[i] == 0:
            # The filter's output lags by its length: the bit ends here.
            middle = i - 1 + n / 2
            if middle + 6 * per_bit >= len(bits) or bits[int(middle)] != 0:
                i += 1
                continue
            code = 0
            for b in range(5):
                code |= int(bits[int(middle + (b + 1) * per_bit)]) << b
            if bits[int(middle + 6 * per_bit)] == 1:
                if code == 0x1F:
                    figures = False
                elif code == 0x1B:
                    figures = True
                else:
                    c = (FIGS if figures else LTRS)[code]
                    if code == 0x04:
                        figures = False
                    if c not in "\0\r\x07":
                        text += c
            i = int(middle + 6 * per_bit)
        i += 1
    return text


def run(signal: RttyTransmitter, seconds: float) -> npt.NDArray[np.int16]:
    frames = [signal.frame() for _ in range(round(seconds * 50))]
    return np.concatenate(frames)


def test_the_signal_decodes_back() -> None:
    text = "CQ CQ DE SP2EMW SP2EMW PSE K"
    signal = RttyTransmitter(2125, False, -10)
    signal.add(text)
    signal.end()
    pcm = run(signal, 10)
    assert signal.done
    assert signal.sent == len(text)
    assert demodulate(pcm, 2125) == text


def test_figures_spaces_and_new_lines() -> None:
    text = "UR 599 NR 012 QTH JO82\nNAME: DAN (OP) 73!"
    signal = RttyTransmitter(1500, False, -10)
    signal.add(text.lower())  # lower case goes in capitals
    signal.end()
    assert demodulate(run(signal, 15), 1500) == text.upper()


def test_reverse_swaps_the_tones() -> None:
    signal = RttyTransmitter(2125, True, -10)
    signal.add("REV")
    signal.end()
    pcm = run(signal, 5)
    # Reversed: mark is the higher tone, so it decodes with the tones swapped.
    assert demodulate(-pcm, 2125) != "REV"
    spectrum = np.abs(np.fft.rfft(pcm[: AUDIO_SAMPLE_RATE // 4]))  # the lead: mark only
    peak_hz = np.argmax(spectrum) * AUDIO_SAMPLE_RATE / (AUDIO_SAMPLE_RATE // 4)
    assert peak_hz == pytest.approx(2125 + SHIFT_HZ, abs=5)


def test_idle_letters_keep_going_until_the_end_is_asked() -> None:
    signal = RttyTransmitter(2125, False, -10)
    signal.add("AB")
    pcm = run(signal, 5)
    assert not signal.done  # still keyed: idle LTRS print nothing
    assert demodulate(pcm, 2125) == "AB"
    assert np.max(np.abs(pcm[-AUDIO_SAMPLE_RATE // 2 :])) > 1000  # still a signal
    signal.end()
    run(signal, 1)
    assert signal.done
    assert not np.any(signal.frame())  # silence after the end


def test_level_and_soft_start() -> None:
    signal = RttyTransmitter(2125, False, -20)
    pcm = run(signal, 1)
    assert int(np.max(np.abs(pcm))) <= 32767 * 10 ** (-20 / 20) + 1
    assert abs(int(pcm[0])) < 200  # ramps up: no click


@pytest.mark.parametrize(
    ("mark", "reverse", "level", "words"),
    [
        (250, False, -10, "mark_hz"),
        (2600, False, -10, "mark_hz"),
        ("2125", False, -10, "mark_hz"),
        (2125, "no", -10, "reverse"),
        (2125, False, 3, "level_db"),
        (2125, False, -41, "level_db"),
    ],
)
def test_bad_settings_are_refused(mark: Any, reverse: Any, level: Any, words: str) -> None:
    with pytest.raises(RttyError, match=words):
        RttyTransmitter(mark, reverse, level)


def test_bad_text_and_overflow_are_refused() -> None:
    signal = RttyTransmitter(2125, False, -10)
    for bad in ("ZAŻÓŁĆ", "A*B", "A@B", "%", 5):
        with pytest.raises(RttyError, match="text"):
            signal.add(bad)
    assert signal.waiting == 0  # nothing of a refused text was taken
    signal.add("A" * QUEUE_MAX)
    with pytest.raises(RttyError, match="at most"):
        signal.add("B")
    signal.end()
    with pytest.raises(RttyError, match="ending"):
        signal.add("C")


# ---- Keying at the card's pace


async def test_key_ends_as_asked_and_reports_each_character() -> None:
    signal = RttyTransmitter(2125, False, -10)
    signal.add("TEST")
    signal.end()
    clock = FakeTime(0)
    fed: list[npt.NDArray[np.int16]] = []
    reports: list[tuple[int, int]] = []

    def feed(pcm: npt.NDArray[np.int16]) -> bool:
        fed.append(pcm)
        return True

    done = await key(
        signal,
        feed,
        alive=lambda: True,
        on_sent=lambda s, w: reports.append((s, w)),
        monotonic=clock,
        sleep=clock.sleep,
    )
    assert done
    assert [s for s, _ in reports] == [1, 2, 3, 4]
    assert demodulate(np.concatenate(fed), 2125) == "TEST"


async def test_a_gone_lease_ends_the_keying_at_once() -> None:
    signal = RttyTransmitter(2125, False, -10)
    signal.add("A LONG TEXT THAT NEVER GETS OUT")
    clock = FakeTime(0)
    fed: list[int] = []

    def feed(pcm: npt.NDArray[np.int16]) -> bool:
        fed.append(len(pcm))
        return len(fed) < 10  # the lease ends after 10 frames

    done = await key(signal, feed, alive=lambda: len(fed) < 10, monotonic=clock, sleep=clock.sleep)
    assert not done
    assert len(fed) == 10
    assert signal.sent < 3


async def test_a_card_that_stays_stuck_ends_the_keying_after_a_second() -> None:
    signal = RttyTransmitter(2125, False, -10)
    clock = FakeTime(0)
    done = await key(
        signal, lambda _pcm: False, alive=lambda: True, monotonic=clock, sleep=clock.sleep
    )
    assert not done
    assert clock.now == pytest.approx(MAX_STALL_S, abs=0.05)


# ---- The server: only a browser, only within its lease


async def start(ws: Any, **fields: Any) -> tuple[str, dict[str, Any]]:
    assert (await ask(ws, "set_mode", mode="L-D"))["ok"]
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    answer = await ask(ws, "rtty_start", lease=lease, mark_hz=2125, **fields)
    return lease, answer


async def events(ws: Any, name: str) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=10)
        if message.get("event") == name:
            return message  # type: ignore[no-any-return]


async def test_the_page_keys_text_within_its_lease(web: Any) -> None:  # noqa: F811
    system, client, sink, _ = web
    ws = await client.ws_connect("/ws", headers=OURS)
    lease, answer = await start(ws, text="CQ ")
    assert answer["ok"], answer
    assert (await ask(ws, "rtty_start", lease=lease, mark_hz=2125))["error"] == "tx_busy"
    assert (await ask(ws, "rtty_text", lease=lease, text="DE SP2EMW"))["ok"]
    assert (await ask(ws, "rtty_text", lease=lease, text="ä"))["error"] == "invalid_value"
    assert (await ask(ws, "rtty_end", lease=lease))["ok"]
    done = await events(ws, "rtty_done")
    assert done["lease"] == lease
    assert done["sent"] == len("CQ DE SP2EMW")
    pcm = np.concatenate(sink.frames)
    assert demodulate(pcm, 2125) == "CQ DE SP2EMW"
    peak = int(np.max(np.abs(pcm)))
    assert peak <= 32767 * 10 ** (-6 / 20) + 1  # the server's limiter (audio.tx_peak_dbfs)
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    assert not system.ptt_active
    await ws.close()


async def test_releasing_the_lease_stops_the_sound_and_drops_the_queue(web: Any) -> None:  # noqa: F811
    system, client, sink, _ = web
    system.core.ft8_sleep = lambda s: asyncio.sleep(0.05)  # the card's real pace
    ws = await client.ws_connect("/ws", headers=OURS)
    lease, answer = await start(ws, text="A TEXT LONG ENOUGH TO BE CUT OFF IN THE MIDDLE")
    assert answer["ok"], answer
    await async_wait_for(lambda: len(sink.frames) > 0)
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    played = len(sink.frames)
    await asyncio.sleep(0.3)
    assert len(sink.frames) == played < 100
    assert not sink.is_open
    assert system.core._rtty is None
    assert (await ask(ws, "rtty_text", lease=lease, text="MORE"))["error"] == "unknown_lease"
    await ws.close()


async def test_text_only_with_a_running_transmission_of_this_lease(web: Any) -> None:  # noqa: F811
    _, client, sink, _ = web
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "set_mode", mode="L-D"))["ok"]
    lease = (await ask(ws, "tx_request", antenna_confirmed=True))["lease"]
    assert (await ask(ws, "rtty_text", lease=lease, text="A"))["error"] == "invalid_value"
    assert (await ask(ws, "rtty_end", lease=lease))["error"] == "invalid_value"
    assert (await ask(ws, "rtty_start", lease="nope", mark_hz=2125))["error"] == "unknown_lease"
    refused = await ask(ws, "rtty_start", lease=lease, mark_hz=2700)
    assert refused["error"] == "invalid_value"
    assert sink.frames == []
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    await ws.close()


async def test_the_cli_cannot_key_rtty(web: Any) -> None:  # noqa: F811
    system, _, sink, _ = web
    cli = await system.client()
    lease = (await cli.request("tx_request", dummy_load_confirmed=True))["lease"]
    refused = await cli.request("rtty_start", lease=lease, mark_hz=2125)
    assert refused["error"] == "forbidden"
    assert (await cli.request("tx_release", lease=lease))["ok"]
    assert sink.frames == []
    await cli.close()


async def test_no_ft8_while_rtty_is_keyed(web: Any) -> None:  # noqa: F811
    _, client, _, _ = web
    ws = await client.ws_connect("/ws", headers=OURS)
    lease, answer = await start(ws)
    assert answer["ok"], answer
    ft8 = {"mode": "FT4", "message": "CQ SP1ABC JO82", "tone_hz": 1000, "start_ms": 0}
    assert (await ask(ws, "ft8_transmit", lease=lease, **ft8))["error"] == "tx_busy"
    assert (await ask(ws, "tx_release", lease=lease))["ok"]
    await ws.close()


async def test_a_page_that_goes_silent_ends_the_keying(web: Any) -> None:  # noqa: F811
    system, client, sink, _ = web
    system.core.ft8_sleep = lambda s: asyncio.sleep(0.02)
    ws = await client.ws_connect("/ws", headers=OURS)
    _lease, answer = await start(ws, text="NO RENEWALS COME")
    assert answer["ok"], answer
    # No tx_renew: the lease ends after lease_timeout_s, by the guard or the server.
    ended = await events(ws, "tx_ended")
    assert ended["reason"] in ("lease_expired", "client_silent")
    await async_wait_for(lambda: not system.ptt_active)
    played = len(sink.frames)
    await asyncio.sleep(0.2)
    assert len(sink.frames) == played
    assert system.core._rtty is None
    await ws.close()
