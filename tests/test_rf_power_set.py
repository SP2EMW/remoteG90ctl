# SPDX-License-Identifier: GPL-3.0-or-later
"""Setting the RF power (CI-V 14 0A, decisions.md D44) within safety.max_rf_power_w.

Runs on the simulator. The limit is the server's: the guard passes the frame
without reading the value (AGENTS.md rule 3) and refuses it during TX.
"""

from pathlib import Path
from typing import Any

import pytest

from guard_helpers import make_config
from remoteg90ctl.civ import set_rf_power_request
from remoteg90ctl.config import parse_config
from remoteg90ctl.server.power import setting_to_watts, watts_to_setting
from remoteg90ctl.sim import SimulatedRadio
from server_helpers import running_system


def test_set_request_is_plain_bcd() -> None:
    assert set_rf_power_request(0x70, 0xE0, 53).hex() == "fefe70e0140a0053fd"
    assert set_rf_power_request(0x70, 0xE0, 255).hex() == "fefe70e0140a0255fd"
    with pytest.raises(ValueError, match="outside 0-255"):
        set_rf_power_request(0x70, 0xE0, 256)


# Measured on the G90 (2026-11-04): written level -> level reported back.
MEASURED = [(53, 40), (120, 107), (134, 120), (147, 134), (241, 228), (255, 255), (0, 0)]


@pytest.mark.parametrize(("written", "reported"), MEASURED)
def test_the_simulator_rounds_written_levels_down_like_the_radio(
    written: int, reported: int
) -> None:
    radio = SimulatedRadio(0x70)
    radio.handle(set_rf_power_request(0x70, 0xE0, written))
    assert radio.rf_power == reported


@pytest.mark.parametrize("watts", range(1, 21))
def test_every_whole_watt_is_reached_and_read_back(watts: int) -> None:
    """Write the smallest level of the watts; the reported level reads as the same watts."""
    radio = SimulatedRadio(0x70)
    radio.handle(set_rf_power_request(0x70, 0xE0, watts_to_setting(watts)))
    assert round(setting_to_watts(radio.rf_power)) == watts


def test_five_watts_writes_54_and_reads_53() -> None:
    assert watts_to_setting(5) == 54
    assert watts_to_setting(1) == 0
    assert watts_to_setting(20) == 255


def config_with_limit(limit: object) -> dict[str, Any]:
    from test_config import VALID  # noqa: PLC0415

    data = {k: dict(v) if isinstance(v, dict) else v for k, v in VALID.items()}
    data["safety"]["max_rf_power_w"] = limit
    return data


def test_limit_is_optional_and_validated() -> None:
    from test_config import VALID, problems_of  # noqa: PLC0415

    assert parse_config(VALID).safety.max_rf_power_w is None
    assert parse_config(config_with_limit(5)).safety.max_rf_power_w == 5.0
    for bad in (0.5, 21, "5", True, float("nan")):
        problems = problems_of(config_with_limit(bad))
        assert any(p.startswith("safety.max_rf_power_w") for p in problems), (bad, problems)


async def test_without_a_limit_the_power_cannot_be_set(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path)) as system:
        client = await system.client()
        response = await client.request("set_rf_power", watts=5)
        assert response["error"] == "rf_power_disabled"
        assert system.harness.port.radio.rf_power == 255
        assert (await client.request("status"))["max_rf_power_w"] is None
        await client.close()


@pytest.mark.parametrize("watts", [0.5, 10.5, 2.5, -1, "5", True, None])
async def test_values_outside_the_limit_are_refused(tmp_path: Path, watts: object) -> None:
    async with running_system(make_config(tmp_path, max_rf_power_w=10)) as system:
        client = await system.client()
        response = await client.request("set_rf_power", watts=watts)
        assert response["error"] == "invalid_value"
        assert system.harness.port.radio.rf_power == 255
        await client.close()


async def test_power_is_set_read_back_and_announced(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path, max_rf_power_w=10)) as system:
        client = await system.client()
        response = await client.request("set_rf_power", watts=5)
        assert response["ok"]
        assert response["setting"] == 53
        assert response["setting_watts"] == 5.0
        assert system.harness.port.radio.rf_power == 53
        seen = await client.next_event("radio_power")
        assert seen["setting_watts"] == 5.0
        assert (await client.request("status"))["max_rf_power_w"] == 10
        await client.close()


async def test_a_mismatch_fails_and_above_the_limit_drops_to_the_minimum(
    tmp_path: Path,
) -> None:
    """If the radio decodes a written level differently (UNVERIFIED), never stay above."""
    async with running_system(make_config(tmp_path, max_rf_power_w=10)) as system:
        radio = system.harness.port.radio
        radio.rf_power_write_error = 40  # 10 W asked, about 13 W stored
        client = await system.client()
        response = await client.request("set_rf_power", watts=10)
        assert response["error"] == "rf_power_mismatch"
        assert radio.rf_power <= watts_to_setting(10)
        await client.close()


async def test_a_mismatch_below_the_limit_fails_but_keeps_the_setting(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path, max_rf_power_w=10)) as system:
        radio = system.harness.port.radio
        radio.rf_power_write_error = -3
        client = await system.client()
        response = await client.request("set_rf_power", watts=5)
        assert response["error"] == "rf_power_mismatch"
        assert radio.rf_power == 40  # 4 W, below the limit: kept
        await client.close()


async def test_power_cannot_be_changed_during_a_transmission(tmp_path: Path) -> None:
    async with running_system(make_config(tmp_path, max_rf_power_w=10)) as system:
        client = await system.client()
        lease = (await client.request("tx_request"))["lease"]
        response = await client.request("set_rf_power", watts=5)
        assert response["error"] == "tx_active"
        assert system.harness.port.radio.rf_power == 255
        assert (await client.request("tx_release", lease=lease))["ok"]
        await client.close()


def test_browsers_may_set_the_power() -> None:
    from remoteg90ctl.server.protocol import (  # noqa: PLC0415
        ACCOUNT_PERMISSIONS,
        REQUEST_PERMISSIONS,
        Permission,
    )

    assert REQUEST_PERMISSIONS["set_rf_power"] is Permission.CONTROL
    assert Permission.CONTROL in ACCOUNT_PERMISSIONS
