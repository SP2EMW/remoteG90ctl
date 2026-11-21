# SPDX-License-Identifier: GPL-3.0-or-later
"""The hardware checklist in ``remoteg90ctl setup`` and its record.

The server, the services and the CLI's tx-test are stand-ins: nothing here
starts a service or transmits. What is checked: transmission is on only after
an explicit yes at the end, a "no" stops with transmission off, SKIP is
recorded, the PTT tests need a dummy load (D15), and a port already checked
is not checked again.
"""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from guard_helpers import make_config
from remoteg90ctl.checklist_record import RECORD, Record, checklist_state, read_record, write_record
from remoteg90ctl.config import Backend, load_config
from remoteg90ctl.server.core import ServerCore
from remoteg90ctl.setup import access as access_module
from remoteg90ctl.setup import files
from remoteg90ctl.setup import wizard as wizard_module
from remoteg90ctl.setup.access import access
from remoteg90ctl.setup.checklist import Checklist, Tools
from remoteg90ctl.setup.wizard import SetupStoppedError, System

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "example.toml"
PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"


class FakeStation:
    def __init__(self, identity: str = "confirmed", watts: float = 1.0) -> None:
        self.identity = identity
        self.watts = watts

    def request(self, type_: str, **_fields: object) -> dict[str, object]:
        if type_ == "status":
            return {
                "guard": {"port": "open", "identity": {"state": self.identity, "detail": "x"}},
                "audio": {"clock_synchronized": True},
            }
        if type_ == "get_frequency":
            return {"ok": True, "hz": 14_074_000}
        if type_ == "get_mode":
            return {"ok": True, "mode": "USB"}
        if type_ == "get_power_meter":
            return {"ok": True, "setting_watts": self.watts}
        return {"ok": True}

    def close(self) -> None:
        pass


class Rig:
    """A configuration on disk and the stand-ins, recording what was done."""

    def __init__(self, root: Path, identity: str = "confirmed", *, running: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.running = running  # the program already runs (a later setup)
        self.spawned: list[list[str]] = []
        self.station = FakeStation(identity)
        # What the CLI's tx-test answers, one per run: (exit code, output).
        self.tx_results: list[tuple[int, str]] = []
        self.system = System(
            etc=root / "etc",
            example=EXAMPLE,
            run=self._run,
            settle=lambda: None,
        )
        rules = self.system.udev_rules
        rules.mkdir(parents=True)
        (rules / "90-remoteg90ctl-serial.rules").touch()
        text = files.set_keys(
            EXAMPLE.read_text(), "radio", {"backend": '"hardware"', "port": f'"{PORT}"'}
        )
        text = files.set_keys(text, "server", {"state_dir": f'"{root / "state"}"'})
        self.system.config.parent.mkdir(parents=True)
        self.system.config.write_text(text)
        self.tools = Tools(
            output=lambda argv: "masked\n" if "sleep.target" in argv else "inactive\n",
            spawn=self._spawn,
            connect=self._connect,
            pause=lambda _s: None,
        )
        self.record = root / "state" / RECORD

    def _run(self, argv: list[str]) -> int:
        self.commands.append(argv)
        if argv[:2] in (["systemctl", "start"], ["systemctl", "restart"]):
            self.running = True
        elif argv[:2] == ["systemctl", "stop"]:
            self.running = False
        return 0

    def _connect(self) -> FakeStation:
        if not self.running:
            raise OSError("not running")
        return self.station

    def _spawn(self, argv: list[str]) -> object:
        self.spawned.append(argv)
        result = self.tx_results.pop(0) if self.tx_results else (0, "done")
        return lambda: result

    def run(self, *replies: str) -> tuple[list[str], list[str]]:
        answers: Iterator[str] = iter(replies)
        asked: list[str] = []
        out: list[str] = []

        def ask(question: str) -> str:
            asked.append(question)
            return next(answers)

        Checklist(ask, out.append, self.system, self.tools, PORT).run(
            load_config(self.system.config)
        )
        return asked, out

    def config(self):  # type: ignore[no-untyped-def]
        return load_config(self.system.config)


# y: CAT socket, VOX, SWR, test setup, plugging DE-19, plugging others,
# guard start, CAT display.
BEFORE_TX = ["y"] * 8


def test_all_done_and_transmission_switched_on(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    # 1 = dummy load; PTT ok, interrupted ok, 5 W, no tuner, on.
    rig.run(*BEFORE_TX, "1", "y", "y", "5", "n", "y")
    config = rig.config()
    assert config.safety.allow_transmit is True
    assert config.safety.max_rf_power_w == 5
    assert config.safety.allow_atu_tune is False
    record = read_record(rig.record)
    assert record is not None
    assert (record.result, record.port) == ("passed", PORT)
    # The PTT tests are the CLI's tx-test with the dummy load confirmed (D15).
    assert [argv[-4:] for argv in rig.spawned] == [
        ["tx-test", "--dummy-load-connected", "--seconds", "2"],
        ["tx-test", "--dummy-load-connected", "--seconds", "8"],
    ]
    # The services were stopped for the plugging tests, the server during the second test.
    assert [
        "systemctl",
        "stop",
        "remoteg90ctl-server.service",
        "remoteg90ctl-guard.service",
    ] in rig.commands
    assert ["systemctl", "stop", "remoteg90ctl-server.service"] in rig.commands


def test_on_the_antenna_at_low_power(tmp_path: Path) -> None:
    """D56: the PTT tests may use the antenna; the server checks SSB, 1 W, the band."""
    rig = Rig(tmp_path)
    rig.run(*BEFORE_TX, "2", "y", "y", "5", "n", "y")
    assert [argv[-4:] for argv in rig.spawned] == [
        ["tx-test", "--antenna-low-power", "--seconds", "2"],
        ["tx-test", "--antenna-low-power", "--seconds", "8"],
    ]
    record = read_record(rig.record)
    assert record is not None
    assert record.result == "passed"


def test_without_a_dummy_load_the_ptt_tests_are_skipped(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.run(*BEFORE_TX, "0", "0", "n", "y")
    assert rig.spawned == []  # no transmission at all
    record = read_record(rig.record)
    assert record is not None
    assert record.result == "skipped"
    assert record.skipped == ["ptt test", "interrupted transmission"]  # 0: neither
    assert rig.config().safety.allow_transmit is True  # the operator's choice, with the warning


def test_a_no_stops_with_transmission_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(SetupStoppedError, match="vox"):
        rig.run("y", "n")
    assert rig.config().safety.allow_transmit is False
    assert not rig.record.exists()


def test_skip_is_recorded_with_its_risk(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _asked, out = rig.run("y", "SKIP", *BEFORE_TX[2:], "0", "0", "n", "n")
    record = read_record(rig.record)
    assert record is not None
    assert record.skipped[0] == "vox"
    assert any("past every safeguard" in line for line in out)
    assert rig.config().safety.allow_transmit is False


def test_a_lowercase_skip_is_not_enough(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asked, _out = rig.run("y", "skip", "y", *BEFORE_TX[2:], "0", "0", "n", "n")
    assert sum("VOX switched off" in q for q in asked) == 2  # asked again
    record = read_record(rig.record)
    assert record is not None
    assert "vox" not in record.skipped


def test_an_unconfirmed_radio_stops_and_stops_the_services(tmp_path: Path) -> None:
    rig = Rig(tmp_path, identity="mismatch")
    with pytest.raises(SetupStoppedError, match="G90"):
        rig.run(*BEFORE_TX[:7], "")  # Enter at the identity problem
    assert rig.commands[-1] == [
        "systemctl",
        "stop",
        "remoteg90ctl-server.service",
        "remoteg90ctl-guard.service",
    ]
    assert rig.config().safety.allow_transmit is False


def test_a_port_already_checked_is_not_checked_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    write_record(rig.record, Record("passed", PORT, "2026-11-11 20:00"))
    asked, _out = rig.run("n")
    assert len(asked) == 1  # only whether to go through it again
    assert rig.spawned == []
    # On request it runs again (another cable, or to learn it).
    rig.run("y", *BEFORE_TX, "0", "0", "n", "n")
    record = read_record(rig.record)
    assert record is not None
    assert record.result == "skipped"  # this time without the PTT tests


# ---- The record, as the server reads it ----


def test_checklist_state(tmp_path: Path) -> None:
    path = tmp_path / RECORD
    assert checklist_state(path, PORT)["result"] == "missing"
    write_record(path, Record("skipped", PORT, "t", ["vox"]))
    assert checklist_state(path, PORT) == {"result": "skipped", "skipped": ["vox"], "time": "t"}
    assert checklist_state(path, "/dev/serial/by-id/other")["result"] == "missing"  # another radio
    path.write_text("not json")
    assert checklist_state(path, PORT)["result"] == "missing"


def test_server_reports_the_checklist_only_for_hardware(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert ServerCore(config).hardware_checklist() is None  # the simulator
    hardware = replace(config, radio=replace(config.radio, backend=Backend.HARDWARE, port=PORT))
    core = ServerCore(hardware)
    assert core.hardware_checklist() == {"result": "missing", "skipped": [], "time": None}
    write_record(config.server.state_dir / RECORD, Record("passed", PORT, "t"))
    assert core.hardware_checklist() == {"result": "passed", "skipped": [], "time": "t"}


# ---- Access through Tailscale ----

TAILNET_NAME = "futro.example-tailnet.ts.net"


def tailscale_status(state: str = "Running") -> str:
    return json.dumps({"BackendState": state, "Self": {"DNSName": TAILNET_NAME + "."}})


def test_tailscale_access_sets_allowed_hosts_and_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    monkeypatch.setattr(access_module.shutil, "which", lambda _name: "/usr/bin/tailscale")
    rig.tools.output = lambda argv: tailscale_status() if "status" in argv else ""
    out: list[str] = []
    replies = iter(["y"])
    config = access(lambda _q: next(replies), out.append, rig.system, rig.tools, rig.config())
    assert config.server.allowed_hosts == (TAILNET_NAME,)
    assert ["tailscale", "serve", "--bg", "8078"] in rig.commands
    assert ["tailscale", "up"] not in rig.commands  # already logged in
    assert f"https://{TAILNET_NAME}/" in "\n".join(out)


def test_tailscale_funnel_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D57: Funnel on for any port is said loudly (the server refuses it anyway)."""
    rig = Rig(tmp_path)
    monkeypatch.setattr(access_module.shutil, "which", lambda _name: "/usr/bin/tailscale")
    serve = json.dumps({"AllowFunnel": {f"{TAILNET_NAME}:443": True}})
    rig.tools.output = lambda argv: (
        serve
        if argv[1:3] == ["serve", "status"]
        else tailscale_status()
        if "status" in argv
        else ""
    )
    out: list[str] = []
    replies = iter(["y"])
    access(lambda _q: next(replies), out.append, rig.system, rig.tools, rig.config())
    assert any("Funnel is ON" in line for line in out)


def test_tailscale_logs_in_first_and_keeps_other_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    rig.system.config.write_text(
        files.set_keys(
            rig.system.config.read_text(), "server", {"allowed_hosts": '["old.example.ts.net"]'}
        )
    )
    monkeypatch.setattr(access_module.shutil, "which", lambda _name: "/usr/bin/tailscale")
    states = iter([tailscale_status("NeedsLogin"), tailscale_status()])
    rig.tools.output = lambda argv: (
        "{}" if argv[1:3] == ["serve", "status"] else next(states) if "status" in argv else ""
    )
    replies = iter(["y"])
    config = access(lambda _q: next(replies), lambda _s: None, rig.system, rig.tools, rig.config())
    assert ["tailscale", "up"] in rig.commands
    assert config.server.allowed_hosts == ("old.example.ts.net", TAILNET_NAME)


def test_no_tailscale_keeps_the_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    monkeypatch.setattr(access_module.shutil, "which", lambda _name: "/usr/bin/tailscale")
    replies = iter(["n"])
    before = rig.system.config.read_text()
    access(lambda _q: next(replies), lambda _s: None, rig.system, rig.tools, rig.config())
    assert rig.system.config.read_text() == before
    assert not any(argv[0] == "tailscale" for argv in rig.commands)


def test_a_refused_test_is_never_counted_as_done(tmp_path: Path) -> None:
    """Owner's run 2026-11-11: the server refused (10 W set), yet "yes" passed the check."""
    rig = Rig(tmp_path)
    refused = (2, "remoteg90ctl: power_too_high: the radio is set to 10 W")
    rig.tx_results = [refused, refused]
    _asked, out = rig.run(*BEFORE_TX, "1", "", "SKIP", "0", "n", "n")
    assert any("power_too_high" in line for line in out)  # the reason is shown
    record = read_record(rig.record)
    assert record is not None
    assert record.result == "skipped"
    assert record.skipped == ["ptt test", "interrupted transmission"]
    assert len(rig.spawned) == 2  # tried, tried again, then left out; never the 8 s test


def test_the_antenna_test_waits_for_low_power(tmp_path: Path) -> None:
    """Owner 2026-11-11: power and mode are read already in part B, and again later."""
    rig = Rig(tmp_path)
    rig.station.watts = 10.0
    # B: four answers, then Enter after turning the power down; C, D; the antenna;
    # PTT ok, cut off ok, 0 W, no tuner, off.
    answers = iter([*BEFORE_TX[:4], "", *BEFORE_TX[4:], "2", "y", "y", "0", "n", "n"])
    out: list[str] = []

    def ask(question: str) -> str:
        if "Set it and press Enter" in question:
            rig.station.watts = 1.0  # the operator turned the power down
        return next(answers)

    Checklist(ask, out.append, rig.system, rig.tools, PORT).run(rig.config())
    assert any("set to 10 W" in line for line in out)
    assert sum("Power and mode: 1 W, USB" in line for line in out) == 3  # B, D and E
    assert any("set your usual power again" in line for line in out)
    assert [argv[-1] for argv in rig.spawned] == ["2", "8"]


def test_first_installation_asks_for_power_and_reads_it_after_the_start(tmp_path: Path) -> None:
    rig = Rig(tmp_path, running=False)
    # B: four answers and the power/mode question; C, D; neither; 0 W, no tuner, off.
    asked, out = rig.run(*BEFORE_TX[:4], "y", *BEFORE_TX[4:], "0", "0", "n", "n")
    assert any("1 W and to SSB" in q for q in asked)
    assert sum("Power and mode: 1 W, USB" in line for line in out) == 1  # read in D


def test_the_end_shows_the_tailnet_address_and_the_local_one(tmp_path: Path) -> None:
    """A computer without a screen is reached through the tailnet, so that comes first."""
    rig = Rig(tmp_path)
    text = files.set_keys(
        rig.system.config.read_text(),
        "server",
        {"allowed_hosts": f'["{TAILNET_NAME}"]', "listen": '["127.0.0.1:8090"]'},
    )
    rig.system.config.write_text(text)
    out: list[str] = []
    wizard_module._start(lambda _q: "y", out.append, rig.system, rig.config())
    text = "\n".join(out)
    assert f"https://{TAILNET_NAME}/" in text
    assert "http://127.0.0.1:8090" in text
    assert text.index(TAILNET_NAME) < text.index("127.0.0.1")
