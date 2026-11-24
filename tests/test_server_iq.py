# SPDX-License-Identifier: GPL-3.0-or-later
"""The server's I/Q input, recordings, permissions and the CLI ``iq`` commands.

The I/Q source is synthetic or a stub; no test opens a sound card (AGENTS.md
rule 9).
"""

import asyncio
from pathlib import Path

import numpy as np
import pytest

from guard_helpers import make_config
from remoteg90ctl import cli
from remoteg90ctl.config import IqConfig, IqSourceKind
from remoteg90ctl.iq.analyze import analyze
from remoteg90ctl.iq.source import FrameCallback, SourceError, SyntheticIq, Tone
from remoteg90ctl.iq.wav import read_wav, write_wav
from remoteg90ctl.server.core import ClientContext, ServerCore, Session
from remoteg90ctl.server.iq_service import MAX_PENDING_BLOCKS, IqService
from remoteg90ctl.server.protocol import (
    REQUEST_FIELDS,
    REQUEST_PERMISSIONS,
    Permission,
    Request,
)
from server_helpers import async_wait_for, running_system

RATE = 48_000


def iq_config(tmp_path: Path, **overrides: object) -> IqConfig:
    recordings = tmp_path / "recordings"
    recordings.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "source": IqSourceKind.SYNTHETIC,
        "device": None,
        "file": None,
        "sample_rate": RATE,
        "sample_bits": 24,
        "swap_channels": False,
        "invert_q": False,
        "dc_removal": True,
        "amplitude_balance": 1.0,
        "phase_correction_deg": 0.0,
        "center_offset_hz": {},
        "passband_hz": {},
        "fft_size": 2048,
        "line_rate": 20.0,
        "recordings_dir": recordings,
    }
    values.update(overrides)
    return IqConfig(**values)  # type: ignore[arg-type]


class FailingSource:
    """Factory product that cannot open, like an unplugged card."""

    sample_rate = RATE
    overruns = 0
    alive = False

    def start(self, callback: FrameCallback) -> None:
        raise SourceError("no ALSA card named 'G90IQ'")

    def stop(self) -> None:
        pass


# Server with an I/Q input


async def test_status_reports_a_running_iq_input(tmp_path: Path) -> None:
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    async with running_system(config) as system:
        client = await system.client()
        assert system.core.iq is not None
        assert system.core.waterfall is not None
        # Nobody watches the waterfall: no spectrum is computed (spares a small
        # computer by the radio), the level is.
        await async_wait_for(lambda: system.core.iq.status()["level_dbfs"] is not None)  # type: ignore[union-attr]
        idle = (await client.request("status"))["iq"]
        subscriber = system.core.waterfall.subscribe(256)
        await async_wait_for(lambda: system.core.iq.last_line is not None)  # type: ignore[union-attr]
        iq = (await client.request("status"))["iq"]
        system.core.waterfall.unsubscribe(subscriber)
        await client.close()
    assert (idle["idle"], idle["lines"]) == (True, 0)
    assert iq["running"] is True
    assert iq["error"] is None
    assert iq["idle"] is False
    assert iq["lines"] > 0
    assert iq["calibrated"] is False  # no center_offset_hz configured
    assert iq["level_dbfs"] is not None


async def test_recording_contains_the_synthetic_signal(tmp_path: Path) -> None:
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    async with running_system(config) as system:
        client = await system.client()
        response = await client.request("iq_record", seconds=1)
        await client.close()
    assert response["ok"], response
    recording = read_wav(Path(response["path"]))
    assert (recording.sample_rate, recording.bits) == (RATE, 24)
    assert recording.frames.shape == (RATE, 2)
    # The synthetic source sends a -40 dBFS tone 5 kHz above the centre.
    assert analyze(recording.frames, RATE).peaks[0].offset_hz == pytest.approx(5_000, abs=5)


@pytest.mark.parametrize("seconds", [0, -1, 31, "1"])
async def test_recording_length_is_validated(tmp_path: Path, seconds: object) -> None:
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    async with running_system(config) as system:
        client = await system.client()
        response = await client.request("iq_record", seconds=seconds)
        await client.close()
    assert response["error"] == "invalid_value"


async def test_recording_without_iq_section(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path)) as system:
        client = await system.client()
        assert (await client.request("status"))["iq"] is None
        response = await client.request("iq_record", seconds=1)
        await client.close()
    assert response["error"] == "iq_unavailable"


async def test_iq_source_none_runs_no_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passband = {"USB": (300.0, 3000.0)}
    iq = iq_config(tmp_path, source=IqSourceKind.NONE, sample_rate=96_000, passband_hz=passband)
    config = make_config(tmp_path, iq=iq)
    socket = str(config.server.socket_path)
    async with running_system(config) as system:
        assert system.core.iq is None
        assert system.core.waterfall is None
        client = await system.client()
        status = (await client.request("status"))["iq"]
        record = await client.request("iq_record", seconds=1)
        await client.close()
        assert await asyncio.to_thread(cli.main, ["--socket", socket, "iq", "status"]) == 0
    assert status == {
        "source": "none",
        "running": False,
        "error": None,
        "sample_rate": 96_000,
        "passband_hz": {"USB": [300.0, 3000.0]},
    }
    assert record["error"] == "iq_unavailable"
    assert 'source = "none"' in capsys.readouterr().out


# IqService on its own


async def test_unavailable_source_is_reported_and_retried(tmp_path: Path) -> None:
    attempts: list[int] = []
    config = iq_config(tmp_path)

    def factory(cfg: IqConfig) -> object:
        attempts.append(1)
        if len(attempts) < 3:
            return FailingSource()
        from remoteg90ctl.iq.source import make_source  # noqa: PLC0415

        return make_source(cfg)

    service = IqService(config, source_factory=factory, retry_s=0.05)  # type: ignore[arg-type]
    task = asyncio.create_task(service.run())
    try:
        await async_wait_for(lambda: service.status()["error"] is not None and len(attempts) >= 1)
        assert service.status()["running"] is False
        assert "G90IQ" in str(service.status()["error"])
        await async_wait_for(lambda: service.status()["running"] is True)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(attempts) == 3


async def test_blocks_are_dropped_when_the_loop_falls_behind(tmp_path: Path) -> None:
    service = IqService(iq_config(tmp_path))
    service._loop = asyncio.get_running_loop()
    block = np.zeros((960, 2), dtype=np.float32)
    for _ in range(MAX_PENDING_BLOCKS + 25):  # the loop gets no chance to process
        service._from_source_thread(block)
    assert service.status()["dropped_blocks"] == 25
    await asyncio.sleep(0)  # processing the queued blocks frees the backlog
    assert service._pending == 0


class DyingSource:
    """Delivers nothing and reports itself dead, like a card that was unplugged."""

    sample_rate = RATE
    overruns = 0
    alive = False

    def start(self, callback: FrameCallback) -> None:
        pass

    def stop(self) -> None:
        pass


async def test_dead_source_is_reported_and_reopened(tmp_path: Path) -> None:
    opened: list[object] = []

    def factory(_cfg: IqConfig) -> object:
        opened.append(DyingSource())
        return opened[-1]

    service = IqService(iq_config(tmp_path), source_factory=factory, retry_s=0.05)  # type: ignore[arg-type]
    task = asyncio.create_task(service.run())
    try:
        await async_wait_for(lambda: len(opened) >= 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # Never reported as running, and reopened on every retry.
    assert service.status()["running"] is False


class SilentSource:
    """Open and alive but delivering nothing, like the card after a USB reset."""

    sample_rate = RATE
    overruns = 0
    alive = True

    def start(self, callback: FrameCallback) -> None:
        pass

    def stop(self) -> None:
        pass


async def test_stalled_source_is_reported_and_reopened(tmp_path: Path) -> None:
    opened: list[object] = []

    def factory(_cfg: IqConfig) -> object:
        opened.append(SilentSource())
        return opened[-1]

    service = IqService(
        iq_config(tmp_path),
        source_factory=factory,  # type: ignore[arg-type]
        retry_s=0.05,
        stall_s=0.1,
    )
    task = asyncio.create_task(service.run())
    try:
        await async_wait_for(lambda: "no I/Q data" in str(service.status()["error"]))
        assert service.status()["running"] is False
        await async_wait_for(lambda: len(opened) >= 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# Permissions (AGENTS.md rule 16)


def test_every_request_type_needs_a_permission() -> None:
    assert set(REQUEST_PERMISSIONS) == set(REQUEST_FIELDS)
    transmit = {k for k, v in REQUEST_PERMISSIONS.items() if v is Permission.TRANSMIT}
    assert transmit == {
        "tx_request",
        "tx_renew",
        "tx_release",
        "ft8_transmit",
        "rtty_start",
        "rtty_text",
        "rtty_end",
        "tx_unlock",
        "port_open",
        # The sound card's output drives the radio (owner's decision 2026-11-14).
        "set_tx_output",
    }


async def test_receive_only_client_cannot_control_or_transmit(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path)) as system:
        context = ClientContext("test", "s1", permissions=frozenset({Permission.RECEIVE}))
        session = Session(context, lambda _message: None)
        core = system.core

        async def ask(kind: str, **fields: object) -> dict[str, object]:
            return await core.handle(session, Request(1, kind, dict(fields)))

        assert (await ask("status"))["ok"]
        assert (await ask("set_frequency", hz=7_074_000))["error"] == "forbidden"
        assert (await ask("tx_request"))["error"] == "forbidden"
        assert (await ask("tx_unlock"))["error"] == "forbidden"
    assert not system.ptt_active


# CLI


def test_cli_analyze_reads_a_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "rec.wav"
    write_wav(path, SyntheticIq(RATE, (Tone(-2_500.0, -30),)).generate(RATE), RATE, 24)
    assert cli.main(["iq", "analyze", str(path), "--signal-hz", "-2500"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    first_peak = lines[lines.index("strongest signals (offset from the I/Q centre):") + 1]
    assert float(first_peak.split()[0]) == pytest.approx(-2_500.0, abs=3.0)  # one bin
    assert "image rejection at -2500 Hz" in out


def test_cli_analyze_needs_no_server(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.wav"
    code = cli.main(["--socket", str(tmp_path / "no.sock"), "iq", "analyze", str(missing)])
    assert code == cli.EXIT_USAGE
    assert "nope.wav" in capsys.readouterr().err


async def test_cli_iq_status_and_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = make_config(tmp_path, iq=iq_config(tmp_path))
    socket = str(config.server.socket_path)
    async with running_system(config) as system:
        await async_wait_for(
            lambda: system.core.iq is not None and system.core.iq.status()["level_dbfs"] is not None
        )
        assert await asyncio.to_thread(cli.main, ["--socket", socket, "iq", "status"]) == 0
        args = ["--socket", socket, "iq", "record", "--seconds", "0.5"]
        assert await asyncio.to_thread(cli.main, args) == 0
    out = capsys.readouterr().out
    assert "synthetic, running" in out
    assert "frequency scale uncalibrated" in out
    assert "saved " in out


def test_waterfall_lines_are_dimmed_only_around_a_transmission(tmp_path: Path) -> None:
    """Only TX the server knows of dims lines; the I/Q level is not used (D33)."""
    from remoteg90ctl.server.core import BLANK_AFTER_TX_LINES  # noqa: PLC0415

    core = ServerCore(make_config(tmp_path, iq=iq_config(tmp_path)))
    assert core.waterfall is not None
    seen: list[bool] = []
    core.waterfall.on_line = lambda line, blanked=False: seen.append(blanked)  # type: ignore[method-assign]
    line = np.full(256, -100.0, dtype=np.float32)
    core._on_iq_line(line)
    core._tx = object()  # type: ignore[assignment]  # a transmission through this server
    core._on_iq_line(line)
    core._tx = None
    for _ in range(BLANK_AFTER_TX_LINES + 2):
        core._on_iq_line(line)
    assert seen[0] is False
    assert seen[1] is True
    assert seen[2 : 2 + BLANK_AFTER_TX_LINES] == [True] * BLANK_AFTER_TX_LINES
    assert seen[-1] is False
