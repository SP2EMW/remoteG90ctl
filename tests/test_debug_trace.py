# SPDX-License-Identifier: GPL-3.0-or-later
"""DEBUG tracing of CI-V traffic, modem lines and requests."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from guard_helpers import CTRL, RADIO, Harness, make_config
from remoteg90ctl import cli
from remoteg90ctl.civ import read_frequency_request, read_xiegu_id_request
from remoteg90ctl.logging_setup import KeyValueFormatter
from server_helpers import running_system


def traces(
    caplog: pytest.LogCaptureFixture, event: str, *, setup: bool = True
) -> list[dict[str, Any]]:
    """DEBUG records of ``event``; ``setup=False`` skips the fixture's start-up traffic."""
    records = caplog.get_records("setup") + caplog.records if setup else caplog.records
    return [
        dict(getattr(r, "kv_fields", {}))
        for r in records
        if r.levelno == logging.DEBUG and r.getMessage() == event
    ]


@pytest.fixture
def h(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Harness:
    caplog.set_level(logging.DEBUG, logger="remoteg90ctl")
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    return harness


def test_port_open_sequence_is_traced(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    steps = [t["step"] for t in traces(caplog, "port_open_step")]
    assert steps == ["lines_requested_low_before_open", "opened"]
    (after_open,) = [t for t in traces(caplog, "lines") if t["reason"] == "after_open"]
    assert after_open == {
        "reason": "after_open",
        "set_dtr": False,
        "set_rts": False,
        "read_dtr": False,
        "read_rts": False,
    }


def test_civ_traffic_is_traced(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    request = read_frequency_request(RADIO, CTRL).hex()
    h.send("civ", frame=request)
    h.core.poll()
    assert traces(caplog, "civ_tx", setup=False) == [{"frame": request}]
    rx = traces(caplog, "civ_rx", setup=False)
    assert rx == [
        {"frame": request, "kind": "echo", "forwarded": False},
        {"frame": "fefee088030040070700fd", "kind": "reply", "forwarded": True},
    ]


def test_other_devices_and_broadcasts_are_labelled(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.port.inject_rx(bytes.fromhex("fefee070fbfd") + h.port.radio.broadcast_frequency())
    h.core.poll()
    kinds = [(t["kind"], t["forwarded"]) for t in traces(caplog, "civ_rx", setup=False)]
    assert kinds == [("other_device", False), ("broadcast", True)]


def test_identity_check_is_traced(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    query = read_xiegu_id_request(RADIO, CTRL).hex()
    assert traces(caplog, "civ_tx") == [{"frame": query, "kind": "identity_query"}]
    kinds = [(t["kind"], t["forwarded"]) for t in traces(caplog, "civ_rx")]
    assert kinds == [("echo", False), ("identity", False)]


def test_ptt_lines_are_traced(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    lease = h.start_tx()
    h.send("tx_release", lease=lease)
    by_reason = {t["reason"]: t for t in traces(caplog, "lines")}
    assert by_reason["ptt_on"]["set_rts"] is True
    assert by_reason["ptt_on"]["read_rts"] is True
    assert by_reason["lower"]["read_rts"] is False


def test_idle_polls_do_not_flood_the_log(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    before = len(caplog.records)
    for _ in range(100):
        h.core.poll()
    assert len(caplog.records) == before


def test_trace_line_format(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    h.send("civ", frame=read_frequency_request(RADIO, CTRL).hex())
    (record,) = [r for r in caplog.records if r.getMessage() == "civ_tx"]
    line = KeyValueFormatter().format(record)
    assert "level=DEBUG" in line
    assert "event=civ_tx frame=fefe88e003fd" in line


async def test_server_traces_guard_round_trips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="remoteg90ctl")
    async with running_system(make_config(tmp_path)) as system:
        client = await system.client()
        assert (await client.request("get_frequency"))["hz"] == 7_074_000
        await client.close()
    requests = traces(caplog, "guard_request")
    assert any(t["type"] == "civ" and t["frame"] == "fefe88e003fd" for t in requests)
    responses = traces(caplog, "guard_response")
    assert all(isinstance(t["elapsed_ms"], float) for t in responses)
    (cat,) = traces(caplog, "cat_reply")
    assert cat["reply"] == "fefee088030040070700fd"
    assert any(t["name"] == "civ_rx" for t in traces(caplog, "guard_event"))


async def test_cli_verbose(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    async with running_system(make_config(tmp_path)) as system:
        argv = ["--socket", str(system.config.server.socket_path), "--verbose", "freq"]
        assert await asyncio.to_thread(cli.main, argv) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "7074000"
    assert '> {"v": 1, "id": 1, "type": "get_frequency"}' in captured.err
    assert '< {"v":1,"id":1,"ok":true,"hz":7074000}' in captured.err
