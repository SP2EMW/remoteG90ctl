# SPDX-License-Identifier: GPL-3.0-or-later
"""The sound card's output level (server/mixer.py): set, read, kept, restored.

amixer is replaced by a fake that answers as the DE-19's C-Media card did on
the S740 (2026-11-14): "Speaker", 0-37 in 1 dB steps, 37 = 0 dB.
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from remoteg90ctl.config import AudioConfig, AudioSourceKind
from remoteg90ctl.server.audio_service import AudioService
from remoteg90ctl.server.mixer import (
    OUTPUT_CONTROL,
    OUTPUT_FILE,
    OutputLevel,
    card_of,
    playback_db,
    run_amixer,
)
from remoteg90ctl.server.protocol import REQUEST_PERMISSIONS, Permission
from server_helpers import running_system
from test_web import OURS, ask, start_logged_in, web_config

SGET = """Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 37
  Mono:
  Front Left: Playback {step} [{pct}%] [{db:.2f}dB] [on]
  Front Right: Playback {step} [{pct}%] [{db:.2f}dB] [on]
"""


class FakeCard:
    def __init__(self, db: float = -20.0) -> None:
        self.db = db
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str) -> tuple[int, str]:
        self.calls.append(args)
        if args[2] == "sset":
            value = args[-1]
            # As amixer's option parser: a negative value needs "--" before it.
            if value.startswith("-") and args[-2] != "--":
                return 1, f"amixer: invalid option -- '{value[1]}'"
            self.db = max(-37.0, min(0.0, round(float(value.removesuffix("dB")))))
        step = int(self.db + 37)
        return 0, SGET.format(step=step, pct=round(step / 37 * 100), db=self.db)


def test_the_card_and_the_level_are_read_from_amixer() -> None:
    assert card_of("hw:CARD=G90AUDIO,DEV=0") == "G90AUDIO"
    assert card_of(None) is None
    assert playback_db(SGET.format(step=17, pct=46, db=-20.0)) == -20.0
    assert playback_db("Invalid card number") is None


async def test_a_level_is_set_kept_and_restored(tmp_path: Path) -> None:
    card = FakeCard()
    output = OutputLevel("G90AUDIO", tmp_path, card)
    assert await output.read() == -20.0
    assert await output.set(-14.4) == -14.0
    assert json.loads((tmp_path / OUTPUT_FILE).read_text()) == {"db": -14.0}
    assert await output.set(+6) == 0.0  # the card's range
    # After a restart (the card was left at -20 dB by the system): the kept level.
    fresh = FakeCard()
    await OutputLevel("G90AUDIO", tmp_path, fresh).restore()
    assert fresh.db == 0.0
    assert fresh.calls[-1] == ("-c", "G90AUDIO", "sset", "Speaker", "--", "0dB")


async def test_a_level_is_sent_in_whole_decibels(tmp_path: Path) -> None:
    """Audit 2026-11-15, A3: "{:g}" made "-1e-05dB" of a value near zero."""
    card = FakeCard()
    output = OutputLevel("G90AUDIO", tmp_path, card)
    assert await output.set(-0.00001) == 0.0
    assert card.calls[-1][-1] == "0dB"
    await output.set(-14.6)
    assert card.calls[-1][-1] == "-15dB"


async def test_nothing_kept_means_the_card_is_left_alone(tmp_path: Path) -> None:
    card = FakeCard()
    await OutputLevel("G90AUDIO", tmp_path, card).restore()
    assert card.calls == []
    (tmp_path / OUTPUT_FILE).write_text("{damaged")
    await OutputLevel("G90AUDIO", tmp_path, card).restore()
    assert card.calls == []


async def test_without_a_card_there_is_no_level(tmp_path: Path) -> None:
    card = FakeCard()
    output = OutputLevel(None, tmp_path, card)
    assert not output.available
    assert await output.set(-10) is None
    assert card.calls == []


async def test_the_audio_service_restores_the_level_when_the_card_opens(tmp_path: Path) -> None:
    (tmp_path / OUTPUT_FILE).write_text(json.dumps({"db": -12}))
    card = FakeCard()
    config = AudioConfig(AudioSourceKind.ALSA, "hw:CARD=G90AUDIO,DEV=0", None)
    service = AudioService(config, state_dir=tmp_path, mixer=card)
    await service._set_mixer()  # what opening the card runs
    assert card.db == -12.0


async def test_the_page_sets_the_level_also_while_transmitting(tmp_path: Path) -> None:
    """Owner's decisions 2026-11-14: any account (transmit permission), live during
    TX; every page hears the new level."""
    config = replace(web_config(tmp_path), audio=AudioConfig(AudioSourceKind.SYNTHETIC, None))
    async with running_system(config) as system:
        client = await start_logged_in(system)
        try:
            ws = await client.ws_connect("/ws", headers=OURS)
            # Without a card for the radio: nothing to set.
            assert (await ask(ws, "get_tx_output"))["error"] == "audio_unavailable"
            card = FakeCard()
            assert system.core.audio is not None
            system.core.audio.output = OutputLevel("G90AUDIO", tmp_path, card)
            got = await ask(ws, "get_tx_output")
            assert (got["db"], got["min_db"], got["max_db"]) == (-20.0, -37.0, 0.0)
            assert (await ask(ws, "set_mode", mode="U-D"))["ok"]
            lease = (await ask(ws, "tx_request"))["lease"]
            other = await client.ws_connect("/ws", headers=OURS)
            assert (await ask(ws, "set_tx_output", db=-12))["db"] == -12.0
            while (message := await other.receive_json(timeout=5)).get("event") != "tx_output":
                pass
            assert message["db"] == -12.0
            for bad in ("-10", None, True):
                assert (await ask(ws, "set_tx_output", db=bad))["error"] == "invalid_value"
            assert (await ask(ws, "tx_release", lease=lease))["ok"]
            await other.close()
            await ws.close()
        finally:
            await client.close()


def test_the_level_needs_transmit_to_set_and_receive_to_read() -> None:
    assert REQUEST_PERMISSIONS["set_tx_output"] is Permission.TRANSMIT
    assert REQUEST_PERMISSIONS["get_tx_output"] is Permission.RECEIVE


@pytest.mark.skipif(shutil.which("amixer") is None, reason="no amixer here")
async def test_the_real_amixer_takes_a_negative_level() -> None:
    """The bug found on the S740 (2026-11-14): without "--" amixer refused "-20dB"
    as options. Run the arguments OutputLevel gives on the real amixer, with a
    control that does not exist: it must get past option parsing."""
    card = FakeCard()
    await OutputLevel("0", None, card).set(-20)
    args = list(card.calls[-1])
    args[args.index(OUTPUT_CONTROL)] = "NoSuchControl"
    _status, out = await run_amixer(*args)
    assert "invalid option" not in out, out
