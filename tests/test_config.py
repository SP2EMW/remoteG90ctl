# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration validation: invalid configuration must refuse to start."""

import copy
from pathlib import Path
from typing import Any

import pytest

from remoteg90ctl.civ import Mode
from remoteg90ctl.config import (
    Backend,
    ConfigError,
    IqSourceKind,
    PttLine,
    check_listen_address,
    load_config,
    parse_config,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example.toml"

VALID: dict[str, Any] = {
    "radio": {
        "backend": "simulator",
        "radio_address": 0x88,
        "controller_address": 0xE0,
        "ptt_line": "rts",
    },
    "safety": {
        "lease_renew_interval_s": 0.5,
        "lease_timeout_s": 2.0,
        "hard_limit_s": 180,
        "duty_window_s": 600,
        "duty_max_tx_s": 300,
    },
    "guard": {
        "socket_path": "/run/remoteg90ctl/guard.sock",
        "tx_log_path": "/var/log/remoteg90ctl/tx.jsonl",
        "state_dir": "/var/lib/remoteg90ctl",
    },
    "server": {"socket_path": "/run/remoteg90ctl/server.sock"},
}

HARDWARE_RADIO: dict[str, Any] = {
    "backend": "hardware",
    "port": "/dev/serial/by-id/usb-FTDI_DE-19-if00-port0",
    "baudrate": 19200,
    "radio_address": 0x88,
    "controller_address": 0xE0,
    "ptt_line": "rts",
}


def config_with(**overrides: dict[str, Any]) -> dict[str, Any]:
    """VALID with selected keys of selected sections replaced (None removes a key)."""
    data = copy.deepcopy(VALID)
    for section, values in overrides.items():
        target = data.setdefault(section, {})
        for key, value in values.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
    return data


def problems_of(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    return info.value.problems


def test_valid_simulator_config() -> None:
    config = parse_config(VALID)
    assert config.radio.backend is Backend.SIMULATOR
    assert config.radio.ptt_line is PttLine.RTS
    assert config.safety.hard_limit_s == 180.0
    assert config.server.listen == ()
    assert config.logging.level == "INFO"


def test_allow_transmit_defaults_to_false() -> None:
    assert parse_config(VALID).safety.allow_transmit is False


def test_example_config_is_valid() -> None:
    config = load_config(EXAMPLE)
    assert config.radio.backend is Backend.SIMULATOR
    assert config.safety.allow_transmit is False


def test_valid_hardware_config() -> None:
    config = parse_config(config_with(radio=HARDWARE_RADIO))
    assert config.radio.backend is Backend.HARDWARE
    assert config.radio.port == HARDWARE_RADIO["port"]


@pytest.mark.parametrize(
    ("key", "value", "limit"),
    [
        ("hard_limit_s", 300, "180"),
        ("hard_limit_s", 180.5, "180"),
        ("lease_timeout_s", 2.5, "2"),
        ("lease_renew_interval_s", 0.6, "0.5"),
    ],
)
def test_safety_limits_cannot_be_extended(key: str, value: float, limit: str) -> None:
    problems = problems_of(config_with(safety={key: value}))
    assert any(f"safety.{key}" in p and f"limit of {limit} s" in p for p in problems)


def test_safety_limits_can_be_shortened() -> None:
    config = parse_config(
        config_with(
            safety={"lease_renew_interval_s": 0.2, "lease_timeout_s": 1.0, "hard_limit_s": 60}
        )
    )
    assert config.safety.lease_timeout_s == 1.0
    assert config.safety.hard_limit_s == 60.0


@pytest.mark.parametrize("key", ["lease_renew_interval_s", "lease_timeout_s", "hard_limit_s"])
def test_safety_timings_have_no_silent_default(key: str) -> None:
    problems = problems_of(config_with(safety={key: None}))
    assert any(f"safety.{key}: is required" in p for p in problems)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "2"])
def test_safety_timing_must_be_positive_number(value: object) -> None:
    problems = problems_of(config_with(safety={"lease_timeout_s": value}))
    assert any("safety.lease_timeout_s" in p for p in problems)


def test_renew_interval_must_be_shorter_than_timeout() -> None:
    problems = problems_of(
        config_with(safety={"lease_renew_interval_s": 0.5, "lease_timeout_s": 0.5})
    )
    assert any("lease_renew_interval_s: must be shorter" in p for p in problems)


def test_allow_transmit_must_be_boolean() -> None:
    problems = problems_of(config_with(safety={"allow_transmit": "yes"}))
    assert any("safety.allow_transmit" in p for p in problems)


@pytest.mark.parametrize("port", ["/dev/ttyUSB0", "/dev/serial/by-path/pci-0:1", "ttyUSB0"])
def test_hardware_port_must_be_by_id(port: str) -> None:
    problems = problems_of(config_with(radio={**HARDWARE_RADIO, "port": port}))
    assert any("radio.port: must start with /dev/serial/by-id/" in p for p in problems)


@pytest.mark.parametrize("key", ["port", "baudrate"])
def test_hardware_requires_port_and_baudrate(key: str) -> None:
    problems = problems_of(config_with(radio={**HARDWARE_RADIO, key: None}))
    assert any(f"radio.{key}: is required" in p for p in problems)


def test_simulator_needs_no_port() -> None:
    assert parse_config(VALID).radio.port is None


@pytest.mark.parametrize("radio", [HARDWARE_RADIO, VALID["radio"]])
def test_radio_address_has_no_default(radio: dict[str, Any]) -> None:
    problems = problems_of(config_with(radio={**radio, "radio_address": None}))
    assert any("radio.radio_address: is required" in p and "0x88" in p for p in problems)


@pytest.mark.parametrize("address", [0x00, 0xFD, 0xFE, 0x100, -1])
def test_radio_address_range(address: int) -> None:
    problems = problems_of(config_with(radio={"radio_address": address}))
    assert any("radio.radio_address" in p for p in problems)


def test_addresses_must_differ() -> None:
    problems = problems_of(config_with(radio={"radio_address": 0xE0}))
    assert any("controller_address: must differ" in p for p in problems)


def test_unknown_ptt_line() -> None:
    problems = problems_of(config_with(radio={"ptt_line": "vox"}))
    assert any("radio.ptt_line" in p for p in problems)


def test_ptt_line_civ_is_accepted_by_config() -> None:
    # Accepted here; the guard blocks transmission for it on hardware (stage 1).
    config = parse_config(config_with(radio={**HARDWARE_RADIO, "ptt_line": "civ"}))
    assert config.radio.ptt_line is PttLine.CIV


def test_unsupported_baudrate() -> None:
    problems = problems_of(config_with(radio={**HARDWARE_RADIO, "baudrate": 12345}))
    assert any("radio.baudrate" in p for p in problems)


def test_unknown_backend() -> None:
    problems = problems_of(config_with(radio={"backend": "auto"}))
    assert any("radio.backend" in p for p in problems)


def test_unknown_key_is_rejected() -> None:
    problems = problems_of(config_with(safety={"hard_limit": 100}))
    assert "safety.hard_limit: unknown key" in problems


def test_unknown_section_is_rejected() -> None:
    data = config_with()
    data["auth"] = {"enabled": True}
    assert "[auth]: unknown section" in problems_of(data)


@pytest.mark.parametrize("section", ["radio", "safety", "guard", "server"])
def test_required_sections(section: str) -> None:
    data = config_with()
    del data[section]
    assert f"[{section}]: section is required" in problems_of(data)


def test_paths_must_be_absolute() -> None:
    problems = problems_of(config_with(guard={"state_dir": "var/lib/remoteg90ctl"}))
    assert any("guard.state_dir: must be an absolute path" in p for p in problems)


def test_paths_must_not_contain_control_characters() -> None:
    """Audit 2026-11-15, A1: a newline would make one path pass for several."""
    path = "/var/lib/remoteg90ctl /var/log/x /var/lib/remoteg90ctl-server\n/x"
    problems = problems_of(config_with(guard={"state_dir": path}))
    assert any("guard.state_dir: must not contain control characters" in p for p in problems)


def test_sockets_must_differ() -> None:
    problems = problems_of(config_with(server={"socket_path": VALID["guard"]["socket_path"]}))
    assert any("must differ from guard.socket_path" in p for p in problems)


def test_all_problems_are_reported_together() -> None:
    problems = problems_of(config_with(safety={"hard_limit_s": 300, "lease_timeout_s": 5}))
    assert len(problems) >= 2


def test_invalid_log_level() -> None:
    problems = problems_of(config_with(logging={"level": "LOUD"}))
    assert any("logging.level" in p for p in problems)


# Network barrier (AGENTS.md rule 15)


@pytest.mark.parametrize(
    "entry",
    [
        "0.0.0.0:8080",
        "[::]:8080",
        "192.168.1.10:8080",
        "100.64.0.1:8080",
        "localhost:8080",
        "example.org:443",
        "127.0.0.2:8080",
        "[::ffff:127.0.0.1]:8080",
    ],
)
def test_network_barrier_refuses_non_loopback(entry: str) -> None:
    problems = problems_of(config_with(server={"listen": [entry]}))
    assert any("server.listen" in p and entry in p for p in problems)


@pytest.mark.parametrize("entry", ["127.0.0.1:8080", "[::1]:8443", "unix:/run/remoteg90ctl/web"])
def test_network_barrier_allows_loopback(entry: str) -> None:
    assert check_listen_address(entry) is None
    assert parse_config(config_with(server={"listen": [entry]})).server.listen == (entry,)


@pytest.mark.parametrize("entry", ["127.0.0.1", "127.0.0.1:0", "127.0.0.1:99999", "unix:rel"])
def test_malformed_listen_entries(entry: str) -> None:
    assert check_listen_address(entry) is not None


def test_there_is_no_auth_switch_to_bypass_the_barrier() -> None:
    data = config_with(server={"listen": ["0.0.0.0:8080"], "auth": True})
    problems = problems_of(data)
    assert "server.auth: unknown key" in problems
    assert any("0.0.0.0:8080" in p for p in problems)


# Loading files


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read file"):
        load_config(tmp_path / "missing.toml")


def test_load_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[radio\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_error_message_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "limit.toml"
    path.write_text(EXAMPLE.read_text().replace("hard_limit_s = 180", "hard_limit_s = 300"))
    with pytest.raises(ConfigError) as info:
        load_config(path)
    message = str(info.value)
    assert str(path) in message
    assert "safety.hard_limit_s: 300 s exceeds the safety limit of 180 s" in message


# radio.identity (decisions.md D32)


def test_identity_is_optional() -> None:
    assert parse_config(VALID).radio.identity is None


def test_identity_is_parsed_as_bytes() -> None:
    config = parse_config(config_with(radio={"identity": "0090"}))
    assert config.radio.identity == b"\x00\x90"


@pytest.mark.parametrize("value", ["", "009", "00 90", "0x90", "zz", "00" * 9, "00fd", "fe"])
def test_invalid_identity_is_refused(value: str) -> None:
    problems = problems_of(config_with(radio={"identity": value}))
    assert any(p.startswith("radio.identity") for p in problems), problems


# Duty cycle (decisions.md D31)


@pytest.mark.parametrize("key", ["duty_window_s", "duty_max_tx_s"])
def test_duty_cycle_settings_are_required(key: str) -> None:
    problems = problems_of(config_with(safety={key: None}))
    assert any(p.startswith(f"safety.{key}") for p in problems), problems


def test_duty_cycle_may_be_stricter() -> None:
    config = parse_config(config_with(safety={"duty_window_s": 900, "duty_max_tx_s": 120}))
    assert (config.safety.duty_window_s, config.safety.duty_max_tx_s) == (900.0, 120.0)


@pytest.mark.parametrize(
    ("key", "value"), [("duty_window_s", 599), ("duty_max_tx_s", 301), ("duty_max_tx_s", 0)]
)
def test_duty_cycle_cannot_be_looser(key: str, value: float) -> None:
    problems = problems_of(config_with(safety={key: value}))
    assert any(p.startswith(f"safety.{key}") for p in problems), problems


# [iq] (stage 2, decisions.md D35)

IQ: dict[str, Any] = {
    "source": "alsa",
    "device": "hw:CARD=G90IQ,DEV=0",
    "sample_rate": 48000,
    "sample_bits": 24,
    "fft_size": 4096,
    "line_rate": 20,
    "recordings_dir": "/var/lib/remoteg90ctl/recordings",
}


def with_iq(**values: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data["iq"] = {k: v for k, v in {**IQ, **values}.items() if v is not None}
    return data


def test_iq_section_is_optional() -> None:
    assert parse_config(VALID).iq is None


def test_iq_source_none_keeps_the_passbands() -> None:
    # No I/Q card: no waterfall, but the page still marks the passband on the scale.
    iq = parse_config(with_iq(source="none", device=None)).iq
    assert iq is not None
    assert iq.source is IqSourceKind.NONE


def test_valid_iq_section() -> None:
    iq = parse_config(with_iq()).iq
    assert iq is not None
    assert (iq.device, iq.sample_rate, iq.fft_size) == ("hw:CARD=G90IQ,DEV=0", 48000, 4096)
    assert iq.center_offset_hz == {}  # not measured: uncalibrated scale
    assert iq.dc_removal is True


@pytest.mark.parametrize("device", ["hw:2,0", "hw:CARD=2,DEV=0", "default", "plughw:1"])
def test_iq_card_must_be_named(device: str) -> None:
    problems = problems_of(with_iq(device=device))
    assert any(p.startswith("iq.device") for p in problems), problems


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sample_rate", 44100),
        ("sample_bits", 32),
        ("fft_size", 3000),
        ("fft_size", 128),
        ("line_rate", 0),
        ("line_rate", 60),
        ("amplitude_balance", 3.0),
        ("center_offset_hz", "12k"),
        ("center_offset_hz", {"SSB": 0}),
        ("center_offset_hz", {"USB": 60_000}),
        ("center_offset_hz", {"USB": "x"}),
        ("source", "pulse"),
        ("tx_detect_drop_db", 6),  # removed with the I/Q TX detector (D33)
    ],
)
def test_invalid_iq_values_are_refused(key: str, value: Any) -> None:
    problems = problems_of(with_iq(**{key: value}))
    assert any(p.startswith(f"iq.{key}") for p in problems), problems


def test_alsa_source_requires_a_device() -> None:
    problems = problems_of(with_iq(device=None))
    assert any(p.startswith("iq.device") for p in problems), problems


def test_file_source_requires_a_file() -> None:
    problems = problems_of(with_iq(source="file", device=None))
    assert any(p.startswith("iq.file") for p in problems), problems


def test_center_offsets_per_mode() -> None:
    table = {"USB": -21.5, "LSB": -21.5, "CW": -821.5, "CW-R": 778.5, "AM": 78.5, "FM": 78.5}
    iq = parse_config(with_iq(center_offset_hz=table)).iq
    assert iq is not None
    assert iq.center_offset_hz == table


def test_offset_mode_names_match_the_server() -> None:
    from remoteg90ctl.config import IQ_OFFSET_MODES  # noqa: PLC0415
    from remoteg90ctl.server.core import mode_name  # noqa: PLC0415

    assert {mode_name(m) for m in Mode} == IQ_OFFSET_MODES


def test_passbands_per_mode() -> None:
    table = {"USB": [300, 3000], "LSB": [-3000, -300], "CW": [-250, 250]}
    iq = parse_config(with_iq(passband_hz=table)).iq
    assert iq is not None
    assert iq.passband_hz == {
        "USB": (300.0, 3000.0),
        "LSB": (-3000.0, -300.0),
        "CW": (-250.0, 250.0),
    }


@pytest.mark.parametrize(
    "table",
    [
        {"USB": [3000, 300]},  # low above high
        {"USB": [300]},
        {"USB": 2700},
        {"SSB": [300, 3000]},
        {"USB": [300, "3000"]},
        {"USB": [300, 60_000]},
    ],
)
def test_invalid_passbands_are_refused(table: dict[str, Any]) -> None:
    problems = problems_of(with_iq(passband_hz=table))
    assert any(p.startswith("iq.passband_hz") for p in problems), problems


# server.allowed_hosts (decisions.md D38)


def test_tailnet_identity_is_required_by_default() -> None:
    """D57: off only by an explicit choice."""
    assert parse_config(config_with(server={})).server.require_tailnet_identity is True
    data = config_with(server={"require_tailnet_identity": False})
    assert parse_config(data).server.require_tailnet_identity is False
    with pytest.raises(ConfigError):
        parse_config(config_with(server={"require_tailnet_identity": "no"}))


def test_allowed_hosts_are_dns_names() -> None:
    data = config_with(server={"allowed_hosts": ["ThinkPad.example.ts.net"]})
    assert parse_config(data).server.allowed_hosts == ("thinkpad.example.ts.net",)


@pytest.mark.parametrize("host", ["localhost", "host.example:8078", "*.ts.net", "a..b", ""])
def test_invalid_allowed_hosts_are_refused(host: str) -> None:
    problems = problems_of(config_with(server={"allowed_hosts": [host]}))
    assert any(p.startswith("server.allowed_hosts") for p in problems), problems


def test_the_transmit_jitter_margin_is_bounded() -> None:
    """Note #189 (2026-11-18): default 200 ms (the adaptive margin's limit), at most 500 ms."""
    data = config_with()
    data["audio"] = {"source": "synthetic"}
    assert parse_config(data).audio is not None
    assert parse_config(data).audio.tx_jitter_ms == 200.0  # type: ignore[union-attr]
    data["audio"]["tx_jitter_ms"] = 600
    assert any("audio.tx_jitter_ms" in p for p in problems_of(data))
