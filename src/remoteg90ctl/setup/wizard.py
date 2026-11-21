# SPDX-License-Identifier: GPL-3.0-or-later
"""The hardware setup in the terminal (``sudo remoteg90ctl setup``).

Six steps, for someone who installs a radio station, not a Linux system:
1 the radio (the DE-19's serial port and sound card), 2 the waterfall (the
I/Q card), 3 saving the settings (udev names, PipeWire, the configuration),
4 checking the radio before transmitting (setup.checklist), 5 access from
other devices (setup.access), 6 starting the program. Steps 4 and 5 come in
through ``after``. Transmission stays off until step 4 switches it on.
Every effect on the system goes through ``System``, so the tests run
against a temporary tree.
"""

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from remoteg90ctl.config import Config, ConfigError, load_config
from remoteg90ctl.setup import files
from remoteg90ctl.setup.detect import (
    SerialPort,
    SoundCard,
    classify,
    serial_ports,
    sound_cards,
)
from remoteg90ctl.setup.i18n import t
from remoteg90ctl.setup.ui import Ask, Say, choose, fail, note, ok, step, warn, yes

__all__ = ["Ask", "Say", "SetupStoppedError", "System", "run", "set_config", "write_config"]


@dataclass
class System:
    """Where the setup reads and writes, and how it runs commands."""

    sys: Path = Path("/sys")
    proc: Path = Path("/proc")
    dev: Path = Path("/dev")
    etc: Path = Path("/etc")
    example: Path = Path("/opt/remoteg90ctl/config/example.toml")
    owner: str = ""  # the desktop user whose PipeWire gets the I/Q rule
    run: Callable[[list[str]], int] = field(
        default=lambda argv: subprocess.run(argv, check=False).returncode  # noqa: S603
    )
    settle: Callable[[], None] = field(default=lambda: time.sleep(2))

    @property
    def config(self) -> Path:
        return self.etc / "remoteg90ctl" / "config.toml"

    @property
    def udev_rules(self) -> Path:
        return self.etc / "udev" / "rules.d"


class SetupStoppedError(Exception):
    pass


# Steps 4 and 5 (setup.checklist, setup.access): the configuration and the
# radio's port in, the final configuration out.
After = Callable[[Config, str], Config]
SERVICES = ["remoteg90ctl-guard.service", "remoteg90ctl-server.service"]


def run(ask: Ask, say: Say, system: System, after: After | None = None) -> int:
    """Steps 1-3, then ``after`` (4 and 5) and step 6."""
    say(t("remoteG90ctl — setup"))
    note(
        say,
        t(
            "I will find the radio and the sound cards, save the settings, check the radio"
            " with you\nbefore its first transmission, set up access from your phone and"
            " start the program.\nThe radio transmits nothing until I ask you first."
            " Ctrl+C stops at any moment."
        ),
    )
    try:
        port, radio_card = _radio(ask, say, system)
        iq_card = _waterfall(ask, say, system, port)
        transmitting = _save(ask, say, system, port, radio_card, iq_card)
        if after is None:
            say("")
            if transmitting:
                ok(say, t("Done. Transmitting stays on: the same radio as before."))
            else:
                ok(say, t("Done. Transmitting is off until the radio is checked (step 4)."))
            return 0
        config = after(load_config(system.config), _port_path(port))
        _start(ask, say, system, config)
    except SetupStoppedError as exc:
        say("")
        fail(say, t("Stopped: {reason}", reason=exc))
        note(say, t("Put it right and start again: sudo remoteg90ctl setup"))
        return 1
    return 0


# ---- Step 1: the radio ----


def _radio(ask: Ask, say: Say, system: System) -> tuple[SerialPort, SoundCard | None]:
    step(say, 1, t("The radio"))
    note(say, t("Looking for the DE-19 interface (the radio's USB connection)…"))
    while True:
        ports = serial_ports(system.dev, system.sys)
        cards = sound_cards(system.sys, system.proc)
        found = classify(ports, cards)
        if found.ports:
            break
        fail(say, t("I do not see the DE-19."))
        note(
            say,
            t(
                "Plug its USB cable into this computer and watch the radio while you do:"
                "\nit must not start transmitting. If it does, unplug the cable and stop here."
            ),
        )
        if not yes(ask, t("Plugged in? Look again?")):
            raise SetupStoppedError(t("the DE-19 was not found"))
    if len(found.ports) == 1:
        port = found.ports[0]
    else:
        note(
            say,
            t(
                "There are several adapters of the DE-19's kind. Which one is the DE-19?"
                "\n(Easier: unplug the others and start the setup again.)"
            ),
        )
        picked = choose(say, ask, found.ports, lambda p: p.by_id.name, t("none of them"))
        if picked is None:
            raise SetupStoppedError(t("no DE-19 chosen"))
        port = picked
    ok(say, t("DE-19 found"))
    found = classify(ports, cards, port)
    if len(found.radio_cards) == 1:
        ok(say, t("Its sound card found (the radio's audio, both ways)"))
        return port, found.radio_cards[0]
    warn(say, t("I did not find the DE-19's sound card next to it. Which card is it?"))
    usb = [c for c in cards if c.usb is not None]
    return port, choose(say, ask, usb, _card_name, t("none of them"))


def _card_name(card: SoundCard) -> str:
    return card.usb.name if card.usb is not None else card.id


def _port_path(port: SerialPort) -> str:
    """The port as the configuration names it, whatever /dev was read."""
    return f"/dev/serial/by-id/{port.by_id.name}"


# ---- Step 2: the waterfall ----


def _waterfall(ask: Ask, say: Say, system: System, port: SerialPort) -> SoundCard | None:
    step(say, 2, t("The waterfall"))
    note(
        say,
        t(
            "The waterfall shows the band around the radio's frequency. It needs a second"
            "\nUSB sound card, connected to the radio's I/Q output."
        ),
    )
    ports = serial_ports(system.dev, system.sys)
    cards = sound_cards(system.sys, system.proc)
    chosen = next((p for p in ports if p.by_id.name == port.by_id.name), port)
    candidates = classify(ports, cards, chosen).iq_cards
    if len(candidates) == 1:
        card = candidates[0]
        if yes(
            ask,
            t("I found: {name}. Is it the one on the I/Q output?", name=_card_name(card)),
            default=True,
        ):
            ok(say, t("Waterfall card: {name}", name=_card_name(card)))
            return card
    elif candidates:
        note(say, t("Which card is on the radio's I/Q output?"))
        picked = choose(say, ask, candidates, _card_name, t("I have no such card"))
        if picked is not None:
            ok(say, t("Waterfall card: {name}", name=_card_name(picked)))
            return picked
    warn(say, t("No waterfall: the page will show the frequency scale only."))
    note(say, t("To add it later: plug the card in and start the setup again."))
    return None


# ---- Step 3: saving the settings ----


def _save(  # noqa: PLR0913, PLR0917  (the answers of steps 1 and 2)
    ask: Ask,
    say: Say,
    system: System,
    port: SerialPort,
    radio_card: SoundCard | None,
    iq_card: SoundCard | None,
) -> bool:
    """Name the cards, keep PipeWire off the I/Q card, write the configuration.

    True when transmitting is on in it (the same radio as before).
    """
    step(say, 3, t("Saving the settings"))
    _name_cards(say, system, radio_card, iq_card)
    if iq_card is not None and iq_card.usb is not None:
        _pipewire(say, system, iq_card)
    transmitting = _configure(say, system, port, radio_card, iq_card)
    _move_tx_log(ask, say, system)
    return transmitting


def _name_cards(
    say: Say, system: System, radio_card: SoundCard | None, iq_card: SoundCard | None
) -> None:
    system.udev_rules.mkdir(parents=True, exist_ok=True)
    rules = [
        ("92-remoteg90ctl-audio.rules", files.RADIO_CARD, radio_card, "The DE-19's sound card"),
        ("91-remoteg90ctl-iq.rules", files.IQ_CARD, iq_card, "The I/Q card"),
    ]
    if (
        radio_card is not None
        and iq_card is not None
        and radio_card.usb is not None
        and iq_card.usb is not None
        and (radio_card.usb.vendor, radio_card.usb.product)
        == (iq_card.usb.vendor, iq_card.usb.product)
    ):
        raise SetupStoppedError(
            t(
                "both sound cards are the same model, so the program cannot tell them apart"
                " (docs/install.md, section 6)"
            )
        )
    for filename, name, card, what in rules:
        path = system.udev_rules / filename
        if card is None or card.usb is None:
            if path.exists():
                path.unlink()
            continue
        path.write_text(files.card_rule(name, card.usb.vendor, card.usb.product, what))
    system.run(["udevadm", "control", "--reload"])
    system.run(["udevadm", "trigger", "--action=add", "--subsystem-match=sound"])
    system.settle()
    named = {c.id for c in sound_cards(system.sys, system.proc)}
    ok(say, t("The sound cards got fixed names"))
    for name, card in ((files.RADIO_CARD, radio_card), (files.IQ_CARD, iq_card)):
        if card is not None and name not in named:
            warn(
                say,
                t(
                    "Unplug the card “{card}” and plug it in again, so that it takes its new name.",
                    card=_card_name(card),
                ),
            )


def _pipewire(say: Say, system: System, iq_card: SoundCard) -> None:
    """On a desktop, PipeWire would take the I/Q card the server opens directly."""
    usb = iq_card.usb
    if usb is None or not system.owner:
        return
    home = Path(os.path.expanduser(f"~{system.owner}"))
    wireplumber = shutil.which("wireplumber")
    if wireplumber is None:
        return
    version = subprocess.run(  # noqa: S603  (the found wireplumber, fixed arguments)
        [wireplumber, "--version"], capture_output=True, text=True, check=False
    ).stdout
    if "0.4." in version:
        target = home / ".config/wireplumber/main.lua.d/51-remoteg90ctl-iq.lua"
        text = files.wireplumber_lua(usb.vendor, usb.product)
    else:
        target = home / ".config/wireplumber/wireplumber.conf.d/51-remoteg90ctl-iq.conf"
        text = files.wireplumber_conf(usb.vendor, usb.product)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    shutil.chown(target, system.owner, system.owner)
    ok(say, t("The computer's own sound leaves the waterfall card alone"))
    # In the desktop user's session; nothing happens when nobody is logged in.
    system.run(
        [
            "systemd-run",
            "--quiet",
            "--wait",
            f"--machine={system.owner}@",
            "--user",
            "systemctl",
            "--user",
            "restart",
            "wireplumber",
        ]
    )


def _configure(
    say: Say,
    system: System,
    port: SerialPort,
    radio_card: SoundCard | None,
    iq_card: SoundCard | None,
) -> bool:
    """Write the configuration; True when transmitting is on in it."""
    config = system.config
    old = config.read_text() if config.exists() else None
    text = old if old is not None else system.example.read_text()
    port_path = _port_path(port)
    same_port = old is not None and f'port = "{port_path}"' in old
    radio = {"backend": '"hardware"', "port": files.toml_string(port_path)}
    text = files.set_keys(text, "radio", radio)
    if not same_port:
        # Another radio or another computer: transmitting off until the checklist.
        text = files.set_keys(
            text, "safety", {"allow_transmit": "false", "allow_atu_tune": "false"}
        )
    if radio_card is not None:
        text = files.set_keys(
            text,
            "audio",
            {"source": '"alsa"', "device": files.toml_string(f"hw:CARD={files.RADIO_CARD},DEV=0")},
        )
    iq = (
        {"source": '"alsa"', "device": files.toml_string(f"hw:CARD={files.IQ_CARD},DEV=0")}
        if iq_card is not None
        else {"source": '"none"'}
    )
    text = files.set_keys(text, "iq", iq)
    parsed = write_config(say, system, text, backup=True)
    if parsed.safety.allow_transmit:
        ok(say, t("Transmitting stays on: the same radio as before"))
    else:
        warn(say, t("Transmitting is off for now: it goes on after the radio check (step 4)"))
    return parsed.safety.allow_transmit


def write_config(say: Say, system: System, text: str, *, backup: bool = False) -> Config:
    """Check ``text`` with load_config, then put it in place of the configuration."""
    config = system.config
    old = config.read_text() if config.exists() else None
    candidate = config.with_name("config.toml.new")
    backup_note = ""
    config.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(text)
    try:
        parsed = load_config(candidate)
    except ConfigError as exc:
        candidate.unlink()
        raise SetupStoppedError(t("the new settings are not valid: {error}", error=exc)) from exc
    if backup and old is not None and old != text:
        kept = config.with_name(f"config.toml.before-setup-{time.strftime('%Y%m%d-%H%M%S')}")
        kept.write_text(old)
        backup_note = t("(the previous settings are kept in {path})", path=kept)
    if old is not None:
        shutil.copymode(config, candidate)
    else:
        candidate.chmod(0o640)
    _same_owner(config, candidate)
    candidate.replace(config)
    if backup:
        ok(say, t("Settings saved ({path})", path=config))
        if backup_note:
            note(say, backup_note)
    return parsed


def set_config(say: Say, system: System, section: str, values: dict[str, str]) -> Config:
    """Set keys in one section of the configuration (TOML literals)."""
    return write_config(say, system, files.set_keys(system.config.read_text(), section, values))


def _move_tx_log(ask: Ask, say: Say, system: System) -> None:
    """A TX log outside the guard's state directory (before 2026-11-09: /var/log) moves in.

    It is a safety record (AGENTS.md rule 7): it belongs with the guard's
    state, never in RAM; the guard is stopped while it moves.
    """
    config = load_config(system.config)
    log, state = config.guard.tx_log_path, config.guard.state_dir
    if log.parent == state:
        return
    target = state / "tx.jsonl"
    if target.exists():
        warn(
            say,
            t(
                "The transmission log is in an old place ({log}), and {target} exists too:"
                " join them by hand (docs/install.md 8.3).",
                log=log,
                target=target,
            ),
        )
        return
    note(
        say,
        t(
            "The transmission log (a record of every transmission) is still in its old place:"
            "\n{log}. Its place now is {target}, next to the program's other records.",
            log=log,
            target=target,
        ),
    )
    if not yes(ask, t("Move it? The program stops for a moment."), default=True):
        return
    system.run(["systemctl", "stop", *reversed(SERVICES)])
    state.mkdir(parents=True, exist_ok=True)
    if log.exists():
        shutil.move(log, target)
        _same_owner(state, target)
    set_config(say, system, "guard", {"tx_log_path": files.toml_string(str(target))})
    ok(say, t("The transmission log moved"))


def _same_owner(reference: Path, path: Path) -> None:
    """The new file gets the old one's owner and group (root:remoteg90ctl)."""
    try:
        stat = reference.stat() if reference.exists() else reference.parent.stat()
        os.chown(path, stat.st_uid, stat.st_gid)
    except PermissionError:
        pass  # the tests run as a normal user


# ---- Step 6: starting ----


def page_port(config: Config) -> int:
    """The port of the page on this computer (server.listen)."""
    for entry in config.server.listen:
        host, _, port = entry.rpartition(":")
        if host and port.isdigit():
            return int(port)
    return 8078


def _start(ask: Ask, say: Say, system: System, config: Config) -> None:
    step(say, 6, t("Starting the program"))
    if yes(
        ask,
        t(
            "Start the program now and every time the computer starts?"
            " (The radio may blink to transmit for an instant.)"
        ),
        default=True,
    ):
        system.run(["systemctl", "enable", "--quiet", *SERVICES])
        system.run(["systemctl", "restart", *SERVICES])
        ok(say, t("The program is running"))
    else:
        system.run(["systemctl", "stop", *reversed(SERVICES)])
        warn(say, t("Not started. Later: sudo ~/remoteG90ctl/deploy/services.sh --start"))
    if config.safety.allow_transmit:
        ok(say, t("Transmitting from the browser: on"))
    else:
        warn(say, t("Transmitting from the browser: off"))
    say("")
    say(t("Ready. Open the page in a browser:"))
    # The tailnet names step 5 put in allowed_hosts are the addresses that
    # work from other devices; the loopback one only on this computer.
    for name in (h for h in config.server.allowed_hosts if h.endswith(".ts.net")):
        say("  " + t("from your devices: {page}", page=f"https://{name}/"))
    page = f"http://127.0.0.1:{page_port(config)}"
    say("  " + t("on this computer: {page}", page=page))
    note(say, t("The first time, create the station's account there (callsign and locator)."))
