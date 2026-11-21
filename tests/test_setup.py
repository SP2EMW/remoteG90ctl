# SPDX-License-Identifier: GPL-3.0-or-later
"""``remoteg90ctl setup``, part 1: finding the hardware and writing the files.

A made-up /sys, /proc and /dev in a temporary directory, shaped like the
owner's ThinkPad on 2026-11-11 (the DE-19's CH340 and card on hub 1-2.3, the
I/Q card on 1-2.2). Nothing touches the real system or a device.
"""

import ast
import string
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from remoteg90ctl.config import load_config
from remoteg90ctl.setup import files, i18n
from remoteg90ctl.setup.detect import capture_channels, classify, serial_ports, sound_cards
from remoteg90ctl.setup.wizard import System, run

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example.toml"
PCI = "devices/pci0000:00/0000:00:14.0/usb1"
STEREO = "Playback:\n  Channels: 2\n\nCapture:\n  Status: Stop\n    Channels: 2\n"
MONO = "Playback:\n  Channels: 2\n\nCapture:\n    Channels: 1\n"


def usb(sys: Path, path: str, vendor: str, product: str, name: str) -> Path:
    device = sys / PCI / path
    device.mkdir(parents=True, exist_ok=True)
    (device / "idVendor").write_text(vendor + "\n")
    (device / "idProduct").write_text(product + "\n")
    (device / "product").write_text(name + "\n")
    return device


def tty(root: Path, path: str, name: str, by_id: str) -> None:
    sys = root / "sys"
    interface = usb(sys, path, "1a86", "7523", "USB Serial") / f"{path.rsplit('/', 1)[-1]}:1.0"
    (interface / name).mkdir(parents=True)
    link = sys / "class/tty" / name
    link.mkdir(parents=True)
    (link / "device").symlink_to(interface / name)
    (root / "dev").mkdir(exist_ok=True)
    (root / "dev" / name).touch()
    serial = root / "dev/serial/by-id"
    serial.mkdir(parents=True, exist_ok=True)
    (serial / by_id).symlink_to(f"../../{name}")


def card(
    root: Path,
    index: int,
    alsa_id: str,
    path: str | None,
    *,
    ids: tuple[str, str, str],
    stream: str,
) -> None:
    sys = root / "sys"
    entry = sys / "class/sound" / f"card{index}"
    entry.mkdir(parents=True)
    (entry / "id").write_text(alsa_id + "\n")
    device = usb(sys, path, *ids) if path is not None else sys / "devices/pci0000:00/0000:00:1f.3"
    device.mkdir(parents=True, exist_ok=True)
    (entry / "device").symlink_to(device)
    proc = root / "proc/asound" / f"card{index}"
    proc.mkdir(parents=True)
    (proc / "stream0").write_text(stream)


@pytest.fixture
def thinkpad(tmp_path: Path) -> Path:
    tty(tmp_path, "1-2/1-2.3/1-2.3.4", "ttyUSB0", "usb-1a86_USB_Serial-if00-port0")
    card(
        tmp_path,
        0,
        "Device",
        "1-2/1-2.3/1-2.3.1",
        ids=("0d8c", "0012", "USB Audio Device"),
        stream=MONO,
    )
    card(
        tmp_path,
        1,
        "Device_1",
        "1-2/1-2.2",
        ids=("0d8c", "8810", "USB Advanced Audio Device"),
        stream=STEREO,
    )
    card(tmp_path, 2, "PCH", None, ids=("", "", ""), stream=STEREO)
    return tmp_path


def system(root: Path, commands: list[list[str]] | None = None) -> System:
    log = commands if commands is not None else []
    return System(
        sys=root / "sys",
        proc=root / "proc",
        dev=root / "dev",
        etc=root / "etc",
        example=EXAMPLE,
        run=lambda argv: log.append(argv) or 0,
        settle=lambda: None,
    )


def answers(*replies: str) -> Iterator[str]:
    yield from replies


# ---- Finding ----


def test_the_de19_card_is_the_one_on_its_serial_port_hub(thinkpad: Path) -> None:
    ports = serial_ports(thinkpad / "dev", thinkpad / "sys")
    cards = sound_cards(thinkpad / "sys", thinkpad / "proc")
    found = classify(ports, cards)
    assert [p.by_id.name for p in found.ports] == ["usb-1a86_USB_Serial-if00-port0"]
    assert [c.index for c in found.radio_cards] == [0]
    assert [c.index for c in found.iq_cards] == [1]  # USB, stereo; not the built-in card


def test_capture_channels() -> None:
    assert capture_channels(STEREO) == 2
    assert capture_channels(MONO) == 1
    assert capture_channels("Playback:\n  Channels: 2\n") == 0
    assert capture_channels("") == 0


# ---- Files ----


def test_set_keys_keeps_comments_and_uses_commented_lines() -> None:
    text = EXAMPLE.read_text()
    out = files.set_keys(text, "radio", {"backend": '"hardware"', "port": '"/dev/serial/by-id/x"'})
    assert out.count("\n# ") >= text.count("\n# ") - 1  # only the commented port line is used
    radio = tomllib.loads(out)["radio"]
    assert (radio["backend"], radio["port"]) == ("hardware", "/dev/serial/by-id/x")
    # A key that is nowhere goes to the end of its section; a missing section is added.
    out = files.set_keys(out, "safety", {"max_rf_power_w": "5"})
    out = files.set_keys(out, "newsection", {"a": "1"})
    parsed = tomllib.loads(out)
    assert parsed["safety"]["max_rf_power_w"] == 5
    assert parsed["newsection"] == {"a": 1}
    assert parsed["iq"]["center_offset_hz"]["USB"] == -21.5  # subsections untouched


def test_card_rule_matches_the_model() -> None:
    rule = files.card_rule("G90IQ", "0d8c", "8810", "The I/Q card")
    assert 'ATTRS{idVendor}=="0d8c", ATTRS{idProduct}=="8810", ATTR{id}="G90IQ"' in rule


# ---- The wizard ----


def test_wizard_writes_rules_and_a_hardware_configuration(thinkpad: Path) -> None:
    commands: list[list[str]] = []
    replies = answers("y")  # yes, that card is on the I/Q output
    out: list[str] = []
    assert run(lambda _q: next(replies), out.append, system(thinkpad, commands)) == 0
    rules = thinkpad / "etc/udev/rules.d"
    assert (
        'ATTRS{idProduct}=="0012", ATTR{id}="G90AUDIO"'
        in (rules / "92-remoteg90ctl-audio.rules").read_text()
    )
    assert (
        'ATTRS{idProduct}=="8810", ATTR{id}="G90IQ"'
        in (rules / "91-remoteg90ctl-iq.rules").read_text()
    )
    assert ["udevadm", "control", "--reload"] in commands
    config = load_config(thinkpad / "etc/remoteg90ctl/config.toml")
    assert config.radio.backend == "hardware"
    assert str(config.radio.port).endswith("usb-1a86_USB_Serial-if00-port0")
    assert config.safety.allow_transmit is False  # until the hardware checklist
    assert config.iq is not None
    assert config.iq.device == "hw:CARD=G90IQ,DEV=0"
    assert config.audio is not None
    assert config.audio.device == "hw:CARD=G90AUDIO,DEV=0"
    text = "\n".join(out)
    assert "Transmitting is off for now" in text
    assert "plug it in again" in text  # the made-up cards were not renamed


def test_without_an_iq_card_the_page_shows_the_scale(thinkpad: Path) -> None:
    replies = answers("n")  # not the I/Q card
    assert run(lambda _q: next(replies), lambda _s: None, system(thinkpad)) == 0
    config = load_config(thinkpad / "etc/remoteg90ctl/config.toml")
    assert config.iq is not None
    assert str(config.iq.source) == "none"
    assert not (thinkpad / "etc/udev/rules.d/91-remoteg90ctl-iq.rules").exists()


def test_rerun_on_the_same_port_keeps_transmission(thinkpad: Path) -> None:
    sys_ = system(thinkpad)
    replies = answers("y", "y")
    run(lambda _q: next(replies), lambda _s: None, sys_)
    path = sys_.config
    path.write_text(files.set_keys(path.read_text(), "safety", {"allow_transmit": "true"}))
    run(lambda _q: next(replies), lambda _s: None, sys_)
    out: list[str] = []
    replies = answers("y")
    run(lambda _q: next(replies), out.append, sys_)
    assert load_config(path).safety.allow_transmit is True  # same radio, same computer
    assert any("Transmitting stays on" in line for line in out)
    assert not list(path.parent.glob("config.toml.before-setup-*"))  # nothing changed


def test_a_new_port_switches_transmission_off(thinkpad: Path) -> None:
    sys_ = system(thinkpad)
    path = sys_.config
    path.parent.mkdir(parents=True)
    old = files.set_keys(
        EXAMPLE.read_text(), "radio", {"backend": '"hardware"', "port": '"/dev/serial/by-id/other"'}
    )
    path.write_text(files.set_keys(old, "safety", {"allow_transmit": "true"}))
    replies = answers("y")
    run(lambda _q: next(replies), lambda _s: None, sys_)
    assert load_config(path).safety.allow_transmit is False
    assert list(path.parent.glob("config.toml.before-setup-*"))  # the old one is kept


def test_no_de19_stops_without_writing(tmp_path: Path) -> None:
    replies = answers("n")
    out: list[str] = []
    assert run(lambda _q: next(replies), out.append, system(tmp_path)) == 1
    assert not (tmp_path / "etc/remoteg90ctl/config.toml").exists()
    assert any("must not start transmitting" in line for line in out)


def test_two_cards_of_one_model_cannot_be_named(tmp_path: Path) -> None:
    tty(tmp_path, "1-2/1-2.3/1-2.3.4", "ttyUSB0", "usb-1a86_USB_Serial-if00-port0")
    card(
        tmp_path,
        0,
        "Device",
        "1-2/1-2.3/1-2.3.1",
        ids=("0d8c", "0012", "USB Audio Device"),
        stream=STEREO,
    )
    card(
        tmp_path,
        1,
        "Device_1",
        "1-2/1-2.2",
        ids=("0d8c", "0012", "USB Audio Device"),
        stream=STEREO,
    )
    replies = answers("y")
    out: list[str] = []
    assert run(lambda _q: next(replies), out.append, system(tmp_path)) == 1
    assert any("same model" in line for line in out)
    assert not (tmp_path / "etc/remoteg90ctl/config.toml").exists()


def test_a_tx_log_outside_the_guard_state_moves_in(thinkpad: Path) -> None:
    """Before 2026-11-09 the TX log was in /var/log; it belongs with the guard's state."""
    commands: list[list[str]] = []
    sys_ = system(thinkpad, commands)
    old_log = thinkpad / "var/log/remoteg90ctl/tx.jsonl"
    old_log.parent.mkdir(parents=True)
    old_log.write_text('{"event": "tx_start"}\n')
    state = thinkpad / "var/lib/remoteg90ctl"
    text = files.set_keys(
        EXAMPLE.read_text(),
        "guard",
        {"tx_log_path": f'"{old_log}"', "state_dir": f'"{state}"'},
    )
    sys_.config.parent.mkdir(parents=True)
    sys_.config.write_text(text)
    replies = answers("y", "y")  # the I/Q card, move the log
    assert run(lambda _q: next(replies), lambda _s: None, sys_) == 0
    moved = state / "tx.jsonl"
    assert moved.read_text() == '{"event": "tx_start"}\n'
    assert not old_log.exists()
    assert load_config(sys_.config).guard.tx_log_path == moved
    assert commands[-1][:2] == ["systemctl", "stop"]  # the guard was stopped first


# ---- Polish ----


def test_every_setup_text_has_a_polish_translation() -> None:
    """Each t("...") in the setup has a translation with the same placeholders."""

    setup = Path(__file__).resolve().parent.parent / "src/remoteg90ctl/setup"
    texts: set[str] = set()
    for path in setup.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                texts.add(node.args[0].value)
    # The checklist's item labels are passed as variables.
    texts |= {
        "sleep",
        "serial port",
        "cat socket",
        "vox",
        "swr threshold",
        "test setup",
        "plugging the de-19",
        "plugging other usb",
        "guard start",
        "port",
        "identity",
        "cat",
        "ptt test",
        "interrupted transmission",
    }
    missing = sorted(texts - i18n.POLISH.keys())
    assert not missing, missing

    def fields(template: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(template) if name}

    for text in texts:
        assert fields(text) == fields(i18n.POLISH[text]), text


def test_the_wizard_speaks_polish(thinkpad: Path) -> None:
    i18n.set_language("pl")
    try:
        replies = answers("t")  # "tak": that is the I/Q card
        out: list[str] = []
        asked: list[str] = []

        def ask(question: str) -> str:
            asked.append(question)
            return next(replies)

        assert run(ask, out.append, system(thinkpad)) == 0
    finally:
        i18n.set_language("en")
    assert any(q.endswith("[T/n] ") and "wyjścia I/Q" in q for q in asked)
    assert any("Krok 1 z 6" in line for line in out)
    assert any("Nadawanie jest na razie wyłączone" in line for line in out)
