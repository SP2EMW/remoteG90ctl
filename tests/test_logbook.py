# SPDX-License-Identifier: GPL-3.0-or-later
"""The station log (stage 9, decisions.md D30, D50): one log per account, ADIF,
and no path to the radio."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient

from remoteg90ctl.server.logbook import Logbook, LogbookError, Station, band_of, parse_adif
from server_helpers import System, running_system
from test_web import FIRST, OURS, ask, start_logged_in, web_config

NOON = 1_762_689_600.0  # 2025-11-09 12:00:00 UTC
QSO = {"call": "dl1abc", "freq_hz": 14_205_000, "mode": "USB", "rst_sent": "59", "rst_rcvd": "57"}


@pytest.fixture
def log(tmp_path: Path) -> Logbook:
    return Logbook(tmp_path, clock=lambda: NOON)


def test_a_qso_gets_band_and_the_save_time(log: Logbook) -> None:
    qso = log.add("SP1ABC", QSO)
    assert (qso["call"], qso["band"], qso["qso_date"], qso["time_on"]) == (
        "DL1ABC",
        "20m",
        "20251109",
        "120000",
    )
    assert qso["dupe"] is False


def test_long_special_event_calls_are_logged(log: Logbook) -> None:
    for call in ("PH40EUDXF", "PA75LIBERATION", "DL/PH40EUDXF/P"):
        assert log.add("SP1ABC", {**QSO, "call": call})["call"] == call


@pytest.mark.parametrize(
    "change",
    [
        {"call": ""},
        {"call": "DL1 ABC"},
        {"call": "X" * 21},
        {"freq_hz": 50},
        {"freq_hz": "14205000"},
        {"mode": "PSK31"},
        {"qso_date": "20251399"},
        {"time_on": "2561"},
        {"gridsquare": "ZZ99"},
        {"rst_sent": "fine"},
        {"comment": "x" * 301},
    ],
)
def test_invalid_qsos_are_refused(log: Logbook, change: dict[str, Any]) -> None:
    with pytest.raises(LogbookError):
        log.add("SP1ABC", {**QSO, **change})


def test_each_account_has_its_own_log(log: Logbook) -> None:
    mine = log.add("SP1ABC", QSO)
    log.add("SP2XYZ", {**QSO, "call": "F5AAA"})
    qsos, total = log.qsos("SP1ABC")
    assert [q["call"] for q in qsos] == ["DL1ABC"]
    assert total == 1
    with pytest.raises(LogbookError):
        log.delete("SP2XYZ", mine["id"])
    with pytest.raises(LogbookError):
        log.update("SP2XYZ", mine["id"], QSO)


def test_dupes_and_worked_before(log: Logbook) -> None:
    log.add("SP1ABC", QSO)
    second = log.add("SP1ABC", QSO)
    other_band = log.add("SP1ABC", {**QSO, "freq_hz": 7_100_000})
    assert second["dupe"] is True
    assert other_band["dupe"] is False
    assert len(log.worked_before("SP1ABC", "dl1abc")) == 3


def test_adif_round_trip(log: Logbook, tmp_path: Path) -> None:
    log.add("SP1ABC", {**QSO, "name": "Hans", "gridsquare": "jo62QM", "comment": "ąę test"})
    log.add("SP1ABC", {**QSO, "call": "JA1XYZ", "mode": "FT4", "freq_hz": 14_080_000})
    text = log.export_adif(Station("SP1ABC", "JO82lj"))
    assert "<ADIF_VER:5>3.1.5" in text
    assert "<MODE:3>SSB <SUBMODE:3>USB" in text
    assert "<MODE:4>MFSK <SUBMODE:3>FT4" in text  # LoTW logs a bare FT4 as DATA
    assert "<STATION_CALLSIGN:6>SP1ABC" in text
    assert "<MY_GRIDSQUARE:6>JO82lj" in text
    assert "<COMMENT:9>ąę test" in text  # length in bytes (UTF-8)
    other = Logbook(tmp_path / "other", clock=lambda: NOON)
    assert other.import_adif("SP1ABC", text) == {"added": 2, "skipped": 0, "failed": 0}
    assert other.import_adif("SP1ABC", text) == {"added": 0, "skipped": 2, "failed": 0}
    qsos, _ = other.qsos("SP1ABC")
    assert {(q["call"], q["mode"], q["gridsquare"]) for q in qsos} == {
        ("DL1ABC", "USB", "JO62qm"),
        ("JA1XYZ", "FT4", ""),
    }


def test_adif_from_another_program() -> None:
    text = (
        "Exported by somebody\n<ADIF_VER:5>3.1.4 <EOH>\n"
        "<call:5>K1ABC<qso_date:8>20240101<time_on:4>0930<band:3>40m<mode:2>CW<eor>\n"
        "<CALL:4>G0XX <QSO_DATE:8>20240102 <TIME_ON:6>101500 <FREQ:9>21.074000 "
        "<MODE:3>FT8 <EOR>\n"
    )
    records = parse_adif(text)
    assert records[0]["CALL"] == "K1ABC"
    assert records[1]["FREQ"] == "21.074000"


def test_band_edges() -> None:
    assert band_of(1_810_000) == "160m"
    assert band_of(29_700_000) == "10m"
    assert band_of(13_000_000) == ""


@pytest_asyncio.fixture
async def site(tmp_path: Path) -> AsyncIterator[tuple[System, TestClient[Any, Any]]]:
    async with running_system(web_config(tmp_path)) as system:
        client = await start_logged_in(system)
        try:
            yield system, client
        finally:
            await client.close()


async def test_the_page_logs_without_touching_the_radio(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """AGENTS.md stage 9: the log has no path to the radio."""
    system, client = site
    ws = await client.ws_connect("/ws", headers=OURS)
    await ask(ws, "get_frequency")
    written = len(system.harness.port.written)
    added = await ask(ws, "log_add", **QSO)
    assert added["ok"], added
    qso_id = added["qso"]["id"]
    assert (await ask(ws, "log_update", qso_id=qso_id, **{**QSO, "name": "Hans"}))["qso"][
        "name"
    ] == "Hans"
    listed = await ask(ws, "log_list", search="DL1")
    assert listed["total"] == 1
    assert listed["qsos"][0]["call"] == "DL1ABC"
    assert (await ask(ws, "log_worked", call="dl1abc"))["worked"][0]["id"] == qso_id
    assert (await ask(ws, "log_delete", qso_id=qso_id))["ok"]
    assert (await ask(ws, "log_delete", qso_id=qso_id))["error"] == "unknown_qso"
    # The follow loop may read the radio meanwhile; the log itself sends no frame.
    frames = system.harness.port.written[written:]
    # Frames that change the radio (a 26 00 read alone is the follow loop's).
    changing = [
        f for f in frames if f[4] in (0x05, 0x06, 0x07, 0x0F, 0x1C) or (f[4] == 0x26 and len(f) > 7)
    ]
    assert changing == []
    assert not system.ptt_active
    await ws.close()


async def test_adif_export_and_import_over_http(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    _, client = site
    ws = await client.ws_connect("/ws", headers=OURS)
    assert (await ask(ws, "log_add", **QSO))["ok"]
    response = await client.get("/api/logbook.adi", headers=OURS)
    assert response.status == 200
    assert 'filename="SP1ABC.adi"' in response.headers["Content-Disposition"]
    text = await response.text()
    assert "<CALL:6>DL1ABC" in text
    assert f"<STATION_CALLSIGN:6>{FIRST['callsign']}" in text
    imported = await client.post(
        "/api/logbook/import", data=text.replace("DL1ABC", "OK1XYZ"), headers=OURS
    )
    assert await imported.json() == {"ok": True, "added": 1, "skipped": 0, "failed": 0}
    assert (await ask(ws, "log_list"))["total"] == 2
    await ws.close()


async def test_the_cli_has_no_log(site: tuple[System, TestClient[Any, Any]]) -> None:
    system, _ = site
    cli = await system.client()
    assert (await cli.request("log_list"))["error"] == "no_log"
    await cli.close()


def test_the_log_files_are_private(log: Logbook, tmp_path: Path) -> None:
    import stat  # noqa: PLC0415

    log.add("SP1ABC", QSO)
    for name in ("logbook.sqlite", "logbook.sqlite-wal"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


async def test_a_large_adif_file_imports_and_a_larger_one_is_refused(
    site: tuple[System, TestClient[Any, Any]],
) -> None:
    """Code review 2026-11-10, point 7: the limit is 5 MB, not aiohttp's default 1 MiB."""
    _, client = site
    note = "x" * 100
    records = "".join(
        f"<CALL:7>T{i:06d}<QSO_DATE:8>20240101<TIME_ON:6>120000<BAND:3>20m<MODE:3>SSB"
        f"<COMMENT:100>{note}<EOR>\n"
        for i in range(12_000)
    )
    assert 1_500_000 < len(records) < 5_000_000
    imported = await client.post("/api/logbook/import", data=records, headers=OURS)
    assert imported.status == 200, await imported.text()
    assert (await imported.json())["added"] == 12_000
    too_big = await client.post("/api/logbook/import", data="x" * 5_300_000, headers=OURS)
    body = await too_big.json()
    assert (too_big.status, body["error"]) == (400, "invalid_value")


# Security review 2026-11-14, Z3: a hostile ADIF file neither breaks the import
# nor fills the disk.


def test_hostile_adif_values_are_bad_records_not_errors(log: Logbook) -> None:
    huge_length = "<CALL:" + "9" * 5000 + ">X<EOR>\n"
    assert parse_adif(huge_length) == []  # not a tag, so no record and no error
    infinite = (
        "<CALL:6>DL1ABC<QSO_DATE:8>20240101<TIME_ON:4>1200<FREQ:3>inf<BAND:3>20m<MODE:3>SSB<EOR>\n"
    )
    result = log.import_adif("SP1ABC", huge_length + infinite)
    assert result == {"added": 1, "skipped": 0, "failed": 0}  # the band gives the frequency


def test_an_import_is_limited_by_records_and_by_the_log_size(
    log: Logbook, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remoteg90ctl.server import logbook as module  # noqa: PLC0415

    record = "<CALL:6>DL{:04d}<QSO_DATE:8>20240101<TIME_ON:4>1200<BAND:3>20m<MODE:3>SSB<EOR>\n"
    monkeypatch.setattr(module, "MAX_IMPORT_RECORDS", 3)
    with pytest.raises(LogbookError, match="more than 3 records"):
        log.import_adif("SP1ABC", "".join(record.format(i) for i in range(4)))
    monkeypatch.setattr(module, "MAX_IMPORT_RECORDS", 100)
    monkeypatch.setattr(module, "MAX_QSOS_PER_ACCOUNT", 5)
    assert log.import_adif("SP1ABC", "".join(record.format(i) for i in range(4)))["added"] == 4
    with pytest.raises(LogbookError, match="more than 5 QSOs"):
        log.import_adif("SP1ABC", "".join(record.format(i) for i in range(10, 12)))
    # Another account's log is its own.
    assert log.import_adif("SP2XYZ", "".join(record.format(i) for i in range(2)))["added"] == 2
