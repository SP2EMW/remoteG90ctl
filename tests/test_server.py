# SPDX-License-Identifier: GPL-3.0-or-later
"""Server: CAT, lease relay, client and guard disconnects (simulator, real guard)."""

import asyncio
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from guard_helpers import Harness, RunningGuard, hardware_config, make_config
from remoteg90ctl import __version__
from remoteg90ctl.civ.commands import Mode
from remoteg90ctl.config import ServerConfig
from remoteg90ctl.serial_port import LineState
from remoteg90ctl.server.core import ClientContext, ServerCore
from remoteg90ctl.server.main import EXIT_RESTART, check_startup, run_server
from remoteg90ctl.server.power import watts_to_setting
from server_helpers import AsyncClient, System, async_wait_for, running_system

# Short lease timing keeps the tests fast; the logic is the same as with 0.5/2.0 s.
RENEW_S = 0.2
TIMEOUT_S = 0.6


@pytest_asyncio.fixture
async def system(tmp_path: Path) -> AsyncIterator[System]:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    async with running_system(config) as running:
        yield running


@pytest_asyncio.fixture
async def client(system: System) -> AsyncIterator[AsyncClient]:
    connection = await system.client()
    yield connection
    await connection.close()


# CAT


async def test_frequency(client: AsyncClient, system: System) -> None:
    assert (await client.request("get_frequency"))["hz"] == 7_074_000
    assert (await client.request("set_frequency", hz=14_074_000))["ok"]
    assert system.harness.port.radio.frequency_hz == 14_074_000
    assert (await client.request("get_frequency"))["hz"] == 14_074_000


async def test_tune_burst_is_coalesced(client: AsyncClient, system: System) -> None:
    others = [await system.client() for _ in range(5)]
    targets = [21_000_000 + 100 * i for i in range(len(others))]
    answers = await asyncio.gather(
        *(c.request("set_frequency", hz=hz) for c, hz in zip(others, targets, strict=True))
    )
    assert all(answer["ok"] for answer in answers)
    # The first goes out at once, the rest wait for it and only the newest follows.
    assert system.core.tune_commands <= 2
    assert system.harness.port.radio.frequency_hz == targets[-1]
    assert (await client.request("get_frequency"))["hz"] == targets[-1]
    for c in others:
        await c.close()


async def test_coalesced_tune_reports_the_failure_to_every_caller(system: System) -> None:
    system.harness.port.set_unresponsive(True)
    others = [await system.client() for _ in range(3)]
    answers = await asyncio.gather(
        *(c.request("set_frequency", hz=21_000_000 + 100 * i) for i, c in enumerate(others))
    )
    assert not any(answer["ok"] for answer in answers)
    for c in others:
        await c.close()


async def test_power_meter_reads_zero_in_receive_and_power_in_transmit(
    client: AsyncClient, system: System
) -> None:
    idle = await client.request("get_power_meter")
    assert idle["ok"]
    assert idle["value"] == 0
    assert idle["raw"].endswith("15110000fd")
    lease = (await client.request("tx_request"))["lease"]
    system.harness.port.radio.swr_meter = 40
    keyed = await client.request("get_power_meter")  # allowed during TX: read only
    assert keyed["value"] == system.harness.port.radio.power_meter
    assert keyed["swr_level"] == 40
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_antenna_tuner(client: AsyncClient, system: System) -> None:
    assert (await client.request("get_atu"))["on"] is False
    assert (await client.request("set_atu", on=True))["ok"]
    assert system.harness.port.radio.atu_on
    assert (await client.request("set_atu", on="yes"))["error"] == "invalid_value"
    assert (await client.request("atu_tune"))["ok"]
    assert system.harness.port.radio.tunings == 1
    again = await client.request("atu_tune")  # the first one may still be tuning (D43)
    assert again["error"] == "tx_busy"
    assert system.harness.port.radio.tunings == 1


async def test_a_tuning_ends_when_the_power_meter_reads_zero(tmp_path: Path) -> None:
    """The guard reads the power meter replies the server asks for (D43): the
    simulated radio shows the tuning for two readings, then zero."""
    async with running_system(make_config(tmp_path)) as system:
        system.core.power_interval_s = 0.2
        client = await system.client()
        assert (await client.request("atu_tune"))["ok"]
        assert system.harness.core.status()["tuning"] is True
        await async_wait_for(lambda: bool(system.harness.tx_events("atu_tune_end")))
        assert system.harness.core.status()["tuning"] is False
        (end,) = system.harness.tx_events("atu_tune_end")
        assert end["reason"] == "meter_zero"
        assert end["emitted"] is True
        await client.close()


async def test_the_clients_hear_whether_the_radio_tuned(tmp_path: Path) -> None:
    """Owner's decision 2026-11-14: a tuning without power on the meter (the G90
    with its screen blanked) reaches the page, which warns in red."""
    async with running_system(make_config(tmp_path)) as system:
        system.core.power_interval_s = 0.2
        system.harness.port.radio.tune_meter_reads = 0  # acknowledges, does not tune
        client = await system.client()
        assert (await client.request("atu_tune"))["ok"]
        end = await client.next_event("atu_tune_end")
        assert (end["reason"], end["emitted"]) == ("meter_zero", False)
        await client.close()


async def test_no_tuning_and_no_tuner_switch_during_tx(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("atu_tune"))["error"] == "tx_busy"
    assert (await client.request("set_atu", on=True))["error"] == "tx_active"
    assert system.harness.port.radio.tunings == 0
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_mode(client: AsyncClient) -> None:
    assert (await client.request("get_mode"))["mode"] == "USB"
    assert (await client.request("set_mode", mode="cw-r"))["ok"]
    assert (await client.request("get_mode"))["mode"] == "CW-R"


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("set_frequency", {"hz": 0}),
        ("set_frequency", {"hz": "7074000"}),
        ("set_frequency", {"hz": True}),
        ("set_frequency", {"hz": 10**11}),
        ("set_mode", {"mode": "SSB"}),
        ("set_mode", {"mode": 1}),
    ],
)
async def test_invalid_cat_values(
    client: AsyncClient, kind: str, fields: dict[str, object]
) -> None:
    assert (await client.request(kind, **fields))["error"] == "invalid_value"


async def test_radio_timeout(client: AsyncClient, system: System) -> None:
    system.harness.port.set_unresponsive(True)
    response = await client.request("get_frequency")
    assert response["error"] == "radio_timeout"


async def test_status(client: AsyncClient) -> None:
    status = await client.request("status")
    assert status["backend"] == "simulator"
    assert status["version"] == __version__
    assert status["guard_connected"] is True
    assert status["guard"]["version"] == __version__
    assert status["guard"]["port"] == "open"
    assert status["tx_active"] is False


async def test_bad_requests(client: AsyncClient) -> None:
    await client.send_raw(b"nope\n")
    assert (await client.receive())["error"] == "bad_request"
    await client.send_raw(b'{"v": 1, "id": 1, "type": "ptt_on"}\n')
    assert (await client.receive())["error"] == "unknown_type"
    await client.send_raw(b'{"v": 9, "id": 2, "type": "status"}\n')
    assert (await client.receive())["error"] == "unsupported_version"
    assert (await client.request("status"))["ok"]


async def test_socket_permissions(system: System) -> None:
    assert stat.S_IMODE(system.config.server.socket_path.stat().st_mode) == 0o660


# Lease relay


async def test_tx_cycle_and_relay_only_on_client_renewal(
    client: AsyncClient, system: System
) -> None:
    granted = await client.request("tx_request")
    assert granted["ok"]
    assert granted["lease_timeout_s"] == TIMEOUT_S
    lease = granted["lease"]
    assert system.ptt_active
    for _ in range(5):
        await asyncio.sleep(RENEW_S)
        assert (await client.request("tx_renew", lease=lease))["ok"]
    assert system.ptt_active
    assert system.core.guard_renewals == 5  # exactly one guard renewal per client renewal
    assert (await client.request("tx_release", lease=lease))["ok"]
    assert not system.ptt_active
    (end,) = system.harness.tx_events("tx_end")
    assert end["reason"] == "released"
    assert end["source"].startswith("cli uid=")
    assert end["frequency_hz"] == 7_074_000


async def test_status_hides_guard_lease(client: AsyncClient) -> None:
    granted = await client.request("tx_request")
    status = await client.request("status")
    assert status["tx_active"]
    assert status["tx_yours"]
    assert "lease" not in status["guard"]["tx"]
    assert granted["lease"] != ""


async def test_silent_client_is_not_renewed(client: AsyncClient, system: System) -> None:
    assert (await client.request("tx_request"))["ok"]
    # Connection stays open, the client just stops renewing.
    elapsed = await async_wait_for(lambda: not system.ptt_active, timeout=TIMEOUT_S + 1.0)
    assert elapsed <= TIMEOUT_S + 0.3
    assert system.core.guard_renewals == 0
    ended = await client.next_event("tx_ended")
    assert ended["reason"] in ("client_silent", "lease_expired")


async def test_client_disconnect_releases_immediately(system: System) -> None:
    client = await system.client()
    assert (await client.request("tx_request"))["ok"]
    await client.close()
    elapsed = await async_wait_for(lambda: not system.ptt_active)
    assert elapsed < TIMEOUT_S  # immediate, not via the lease timeout
    await async_wait_for(lambda: bool(system.harness.tx_events("tx_end")))
    assert system.harness.tx_events("tx_end")[0]["reason"] == "released"


async def test_second_client_cannot_transmit_or_renew(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    other = await system.client()
    try:
        assert (await other.request("tx_request"))["error"] == "tx_busy"
        assert (await other.request("tx_renew", lease=lease))["error"] == "unknown_lease"
        assert (await other.request("tx_release", lease=lease))["error"] == "unknown_lease"
        assert system.ptt_active
        assert system.core.guard_renewals == 0
    finally:
        await other.close()


async def test_renew_with_wrong_lease(client: AsyncClient, system: System) -> None:
    await client.request("tx_request")
    assert (await client.request("tx_renew", lease="bogus"))["error"] == "unknown_lease"
    assert system.core.guard_renewals == 0


async def test_state_change_refused_during_tx(client: AsyncClient) -> None:
    await client.request("tx_request")
    assert (await client.request("set_frequency", hz=14_000_000))["error"] == "tx_active"
    assert (await client.request("get_frequency"))["ok"]


async def test_guard_errors_pass_through(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    assert (await client.request("tx_unlock"))["error"] == "tx_busy"
    await client.request("tx_release", lease=lease)
    assert (await client.request("tx_unlock"))["was_locked"] is False
    assert (await client.request("port_open"))["port"] == "open"


# Guard connection


async def test_guard_connection_loss_during_tx(client: AsyncClient, system: System) -> None:
    assert (await client.request("tx_request"))["ok"]
    await system.core.guard.close()  # the server's side of the guard connection drops
    elapsed = await async_wait_for(lambda: not system.ptt_active)
    assert elapsed < TIMEOUT_S  # the guard releases on disconnect, immediately
    ended = await client.next_event("tx_ended")
    assert ended["reason"] == "guard_disconnected"
    # The server reconnects by itself and nothing is transmitted on reconnect.
    await async_wait_for(lambda: system.core.guard.connected)
    assert not system.ptt_active
    assert (await client.request("status"))["tx_active"] is False


async def test_guard_down_and_back(client: AsyncClient, system: System) -> None:
    system.guard.stop()
    await async_wait_for(lambda: not system.core.guard.connected)
    assert (await client.request("get_frequency"))["error"] == "guard_unavailable"
    assert (await client.request("tx_request"))["error"] == "guard_unavailable"
    assert (await client.request("status"))["guard_connected"] is False
    system.restart_guard()
    await async_wait_for(lambda: system.core.guard.connected)
    assert (await client.request("get_frequency"))["ok"]
    assert not system.ptt_active


async def test_server_shutdown_releases_tx(tmp_path: Path) -> None:
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    async with running_system(config) as system:
        client = await system.client()
        lease = (await client.request("tx_request"))["lease"]
    assert not system.ptt_active
    assert system.harness.tx_events("tx_end")[0]["reason"] == "released"
    # The client was told why before the connection closed.
    ended = await client.next_event("tx_ended")
    assert ended == {"v": 1, "event": "tx_ended", "lease": lease, "reason": "server_shutdown"}


# Hardware gating


async def test_hardware_requires_dummy_load_confirmation(tmp_path: Path) -> None:
    config = hardware_config(
        tmp_path, allow_transmit=True, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S
    )
    async with running_system(config) as system:
        client = await system.client()
        assert (await client.request("tx_request"))["error"] == "dummy_load_required"
        response = await client.request("tx_request", dummy_load_confirmed="yes")
        assert response["error"] == "invalid_value"
        assert not system.ptt_active
        assert (await client.request("tx_request", dummy_load_confirmed=True))["ok"]
        assert system.ptt_active
        await client.close()


async def test_cli_test_on_the_antenna_only_low_power_ssb_common_band(tmp_path: Path) -> None:
    """D56 (owner's decision 2026-11-11): the CLI's PTT test may use the antenna."""
    config = hardware_config(
        tmp_path, allow_transmit=True, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S
    )
    async with running_system(config) as system:
        radio = system.harness.port.radio
        client = await system.client()
        # 20 W set (the simulator's default): refused, nothing keyed.
        response = await client.request("tx_request", antenna_confirmed=True)
        assert response["error"] == "power_too_high"
        assert not system.ptt_active
        radio.rf_power = watts_to_setting(2)  # 2 W: still too much (owner: 1 W)
        response = await client.request("tx_request", antenna_confirmed=True)
        assert response["error"] == "power_too_high"
        radio.rf_power = watts_to_setting(1)  # 1 W
        # 1849 kHz + 3 kHz of USB runs past Region 1's 160 m band (1810-1850
        # kHz): the whole signal must lie in a band every region has.
        radio.frequency_hz = 1_849_000
        assert (await client.request("tx_request", antenna_confirmed=True))[
            "error"
        ] == "out_of_band"
        radio.frequency_hz = 7_074_000
        radio.mode = Mode.CW
        assert (await client.request("tx_request", antenna_confirmed=True))[
            "error"
        ] == "not_data_mode"
        assert not system.ptt_active
        radio.mode = Mode.USB
        response = await client.request("tx_request", antenna_confirmed=True)
        assert response["ok"], response
        assert system.ptt_active
        await client.request("tx_release", lease=response["lease"])
        await client.close()


async def test_hardware_without_allow_transmit(tmp_path: Path) -> None:
    config = hardware_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    async with running_system(config) as system:
        client = await system.client()
        response = await client.request("tx_request", dummy_load_confirmed=True)
        assert response["error"] == "tx_blocked"
        assert "allow_transmit" in response["message"]
        await client.close()


# Startup and context


def test_loopback_web_listener_is_accepted(tmp_path: Path) -> None:
    """Stage 2b serves the browser on TCP loopback (rule 15 still holds in config)."""
    config = make_config(tmp_path)
    with_listen = type(config)(
        config.radio,
        config.safety,
        config.guard,
        ServerConfig(config.server.socket_path, ("127.0.0.1:8078",)),
        config.logging,
    )
    check_startup(with_listen)  # no StartupError


def test_client_context_source() -> None:
    assert ClientContext("cli", "ab12", uid=1000, pid=42).source == (
        "cli uid=1000 pid=42 session=ab12"
    )


async def test_line_fault_is_broadcast(client: AsyncClient, system: System) -> None:
    system.harness.port.set_lines(LineState(dtr=False, rts=True))  # foreign activity
    event = await client.next_event("line_fault")
    assert "without a transmission" in event["message"]
    assert not system.ptt_active
    assert (await client.request("tx_request"))["error"] == "tx_blocked"
    assert (await client.request("port_open"))["ok"]
    assert (await client.request("tx_request"))["ok"]


# Passband (decisions.md D37): from [iq.passband_hz] only


def test_passband_comes_from_the_configuration(tmp_path: Path) -> None:
    from test_server_iq import iq_config  # noqa: PLC0415

    config = make_config(tmp_path, iq=iq_config(tmp_path, passband_hz={"USB": (300.0, 3000.0)}))
    core = ServerCore(config)
    core.radio_mode = "USB"
    assert core.passband() == [300.0, 3000.0]
    core.radio_mode = "CW"
    assert core.passband() is None


async def test_speaker_is_muted_when_configured(tmp_path: Path) -> None:
    from dataclasses import replace  # noqa: PLC0415

    config = make_config(tmp_path)
    config = replace(config, radio=replace(config.radio, mute_speaker=True))
    async with running_system(config) as system:
        await async_wait_for(lambda: system.harness.port.radio.af == 0)
        client = await system.client()
        assert (await client.request("set_af", level=60))["level"] == 60
        assert (await client.request("get_af"))["level"] == 60
        assert (await client.request("set_af", level=300))["error"] == "invalid_value"
        await client.close()


def _af_sets(radio: Any) -> int:
    """How many times the speaker volume was set (CI-V 14 01 with a value)."""
    return sum(1 for f in radio.received if len(f) > 7 and f[4] == 0x14 and f[5] == 0x01)


async def test_the_speaker_is_muted_again_when_somebody_turns_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner's request 2026-11-10: every 30 s the volume is read and muted if it is not 0."""
    from dataclasses import replace  # noqa: PLC0415

    from remoteg90ctl.server import core as core_module  # noqa: PLC0415

    monkeypatch.setattr(core_module, "SPEAKER_CHECK_S", 0.05)
    config = make_config(tmp_path)
    config = replace(config, radio=replace(config.radio, mute_speaker=True))
    async with running_system(config) as system:
        radio = system.harness.port.radio
        await async_wait_for(lambda: radio.af == 0)
        sets = _af_sets(radio)
        await asyncio.sleep(0.3)  # several checks: a muted radio is not written to
        assert _af_sets(radio) == sets
        radio.af = 80  # the knob, at the radio
        await async_wait_for(lambda: radio.af == 0, timeout=3.0)


async def test_the_speaker_is_left_alone_during_a_transmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from remoteg90ctl.server import core as core_module  # noqa: PLC0415

    monkeypatch.setattr(core_module, "SPEAKER_CHECK_S", 0.05)
    config = make_config(tmp_path)
    config = replace(config, radio=replace(config.radio, mute_speaker=True))
    async with running_system(config) as system:
        radio = system.harness.port.radio
        await async_wait_for(lambda: radio.af == 0)
        client = await system.client()
        lease = (await client.request("tx_request", dummy_load_confirmed=True))["lease"]
        radio.af = 80
        sets = _af_sets(radio)
        await asyncio.sleep(0.4)
        assert radio.af == 80  # the guard would refuse the change; none is even tried
        assert _af_sets(radio) == sets
        assert (await client.request("tx_release", lease=lease))["ok"]
        await async_wait_for(lambda: radio.af == 0, timeout=3.0)  # and then it is muted again
        await client.close()


async def test_the_speaker_is_not_touched_unless_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remoteg90ctl.server import core as core_module  # noqa: PLC0415

    monkeypatch.setattr(core_module, "SPEAKER_CHECK_S", 0.05)
    async with running_system(make_config(tmp_path)) as system:
        radio = system.harness.port.radio
        await asyncio.sleep(0.4)
        assert radio.af == 128
        assert _af_sets(radio) == 0


async def test_ssb_becomes_a_data_mode_when_configured(tmp_path: Path) -> None:
    """USB/LSB set as U-D/L-D: only then does the G90 send audio to the DE-19."""
    from dataclasses import replace  # noqa: PLC0415

    config = make_config(tmp_path)
    config = replace(config, radio=replace(config.radio, ssb_data_mode=True))
    async with running_system(config) as system:
        radio = system.harness.port.radio
        client = await system.client()
        assert (await client.request("set_mode", mode="USB"))["ok"]
        assert radio.data_mode is True
        assert (await client.request("get_mode"))["mode"] == "U-D"
        assert (await client.request("set_mode", mode="CW"))["ok"]
        assert (await client.request("get_mode"))["mode"] == "CW"
        await client.close()


async def test_data_modes_can_be_chosen_and_left(client: AsyncClient, system: System) -> None:
    radio = system.harness.port.radio
    assert (await client.request("set_mode", mode="l-d"))["ok"]
    assert radio.data_mode is True
    assert (await client.request("get_mode"))["mode"] == "L-D"
    assert (await client.request("set_mode", mode="LSB"))["ok"]  # ssb_data_mode off
    assert radio.data_mode is False
    assert (await client.request("get_mode"))["mode"] == "LSB"
    assert (await client.request("set_mode", mode="X-D"))["error"] == "invalid_value"


async def test_the_tuner_is_switched_only_when_it_differs(
    client: AsyncClient, system: System
) -> None:
    """Seen 2026-11-08: 1C 01 00/01 toggles the G90's tuner. Asking for the state it
    already has must send nothing, or "off" would switch it on."""
    radio = system.harness.port.radio
    for wanted in (False, True, True, False, False):
        assert (await client.request("set_atu", on=wanted))["ok"]
        assert radio.atu_on is wanted
    writes = [w for w in system.harness.port.written if w[4:6] == b"\x1c\x01" and len(w) == 8]
    assert len(writes) == 2  # on once, off once


async def test_the_speaker_is_left_alone_right_after_a_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The guard refuses changes for 3 s after an atu_tune (the carrier cannot be
    stopped): the check waits instead of failing (review of 2026-11-10)."""
    import logging  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    from remoteg90ctl.server import core as core_module  # noqa: PLC0415

    monkeypatch.setattr(core_module, "SPEAKER_CHECK_S", 0.05)
    config = make_config(tmp_path)
    config = replace(config, radio=replace(config.radio, mute_speaker=True))
    async with running_system(config) as system:
        radio = system.harness.port.radio
        await async_wait_for(lambda: radio.af == 0)
        client = await system.client()
        assert (await client.request("set_atu", on=True))["ok"]
        with caplog.at_level(logging.WARNING):
            assert (await client.request("atu_tune"))["ok"]
            radio.af = 80
            await asyncio.sleep(0.5)  # well inside the guard's 3 s
        assert radio.af == 80
        assert "speaker_check_failed" not in caplog.text
        await client.close()


# Restart from the page (owner's decision 2026-11-13): the server only.


async def test_restart_is_refused_while_transmitting(client: AsyncClient, system: System) -> None:
    lease = (await client.request("tx_request"))["lease"]
    refused = await client.request("server_restart")
    assert refused["error"] == "tx_busy"
    assert not system.core.restart_requested.is_set()
    assert system.ptt_active
    assert (await client.request("tx_release", lease=lease))["ok"]


async def test_restart_stops_the_server_and_asks_for_a_new_start(tmp_path: Path) -> None:
    """run_server() returns True; main() turns it into exit status 75 for systemd."""
    config = make_config(tmp_path, lease_renew_interval_s=RENEW_S, lease_timeout_s=TIMEOUT_S)
    harness = Harness(config, real_time=True)
    guard = RunningGuard(harness).__enter__()
    core = ServerCore(config)
    task = asyncio.create_task(run_server(config, asyncio.Event(), core))
    try:
        await async_wait_for(lambda: config.server.socket_path.exists() and core.guard.connected)
        client = await AsyncClient.connect(config.server.socket_path)
        answer = await client.request("server_restart")
        assert answer["ok"]
        assert answer["restart_in_s"] > 0
        assert await asyncio.wait_for(task, 5) is True
        assert not harness.ptt_active
    finally:
        guard.stop()
    assert EXIT_RESTART == 75
