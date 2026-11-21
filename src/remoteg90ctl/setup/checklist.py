# SPDX-License-Identifier: GPL-3.0-or-later
"""Step 4 of the setup: checking the radio before it transmits.

The items are those of docs/install.md section 5 for an ordinary user (owner's
decision 2026-11-11, decisions.md D56): some checked here, the others by the
operator watching the radio. They run once per radio port (the first
installation), and again on request. Any item can be skipped by typing SKIP
(POMIŃ): the risk is spelled out, and the skip is recorded in the server's
state directory, so the page keeps a warning up (docs/server-protocol.md,
``status.hardware_checklist``).

Transmitting is switched on only at the end, after an explicit yes. The PTT
tests go through the ordinary path (``remoteg90ctl tx-test``, a lease with
the guard's limits), on a dummy load (decisions.md D15) or on the antenna at
low power (D56: the server allows that only in SSB, at most 1 W set, in a
band every ITU region has); without either they are recorded as not done.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from remoteg90ctl.bandplan import BANDS, in_band
from remoteg90ctl.checklist_record import RECORD, Record, read_record, write_record
from remoteg90ctl.config import Config
from remoteg90ctl.setup.i18n import is_skip, t
from remoteg90ctl.setup.ui import check, fail, note, number, ok, step, warn, yes
from remoteg90ctl.setup.wizard import Ask, Say, SetupStoppedError, System, set_config

__all__ = ["Checklist", "Station", "Tools", "record_path", "yes"]

GUARD = "remoteg90ctl-guard.service"
SERVER = "remoteg90ctl-server.service"
TX_TEST_S = 2
INTERRUPTED_TEST_S = 8
SERVER_WAIT_S = 30.0
MAX_POWER_W = 20  # the G90's highest power
# Every test transmission at 1 W (owner's decision 2026-11-11); on the antenna
# the server allows no more either (D56).
TEST_POWER_W = 1
SSB_MODES = ("USB", "LSB", "U-D", "L-D")
SSB_SPAN_HZ = 3000


def _common_band(hz: int, mode: str) -> bool:
    """The whole SSB signal inside a band all three ITU regions have (as the server checks)."""
    low, high = (hz - SSB_SPAN_HZ, hz) if mode in ("LSB", "L-D") else (hz, hz + SSB_SPAN_HZ)
    return all(in_band(region, low, high) for region in BANDS)


class Station(Protocol):
    """The server's socket, as the CLI uses it (remoteg90ctl.cli.ServerConnection)."""

    def request(self, type_: str, **fields: object) -> dict[str, object]: ...

    def close(self) -> None: ...


@dataclass
class Tools:
    """What the checklist runs besides files: services, programs, the server."""

    output: Callable[[list[str]], str]  # a command's standard output
    # Start a program quietly; the result waits for (exit code, what it printed).
    spawn: Callable[[list[str]], Callable[[], tuple[int, str]]]
    connect: Callable[[], Station]  # raises OSError while the server is not up
    cli: list[str] = field(default_factory=lambda: ["remoteg90ctl"])  # how to run the CLI
    pause: Callable[[float], None] = time.sleep


def record_path(config: Config) -> Path:
    return config.server.state_dir / RECORD


@dataclass
class Checklist:
    ask: Ask
    say: Say
    system: System
    tools: Tools
    port: str
    skipped: list[str] = field(default_factory=list)

    def run(self, config: Config) -> Config:
        """The whole check, then the transmit settings; returns the final config."""
        step(self.say, 4, t("Checking the radio before it transmits"))
        path = record_path(config)
        previous = read_record(path)
        if previous is not None and previous.port == self.port:
            ok(self.say, t("The radio was checked on {time}.", time=previous.time))
            # Once per port; again on request (another cable or interface, or to learn it).
            if not yes(self.ask, t("Check it again?"), default=False):
                return config
        note(
            self.say,
            t(
                "Together we will check that the radio is connected safely. It takes a few"
                " minutes\nand is done once. Answer y (yes) or n (no: I stop, so you can put"
                " it right).\nTo leave something out, type SKIP: the page will then keep"
                " showing a warning."
            ),
        )
        self._computer()
        self._radio_settings()
        self._plugging()
        config = self._program_start(config)
        config = self._test_transmissions(config)
        config = self._browser(config)
        result = "skipped" if self.skipped else "passed"
        write_record(path, Record(result, self.port, time.strftime("%Y-%m-%d %H:%M"), self.skipped))
        self.say("")
        if self.skipped:
            warn(self.say, t("Left out: {items}", items=", ".join(t(k) for k in self.skipped)))
            note(
                self.say,
                t(
                    "The page will remind you of it until the whole check is done"
                    "\n(sudo remoteg90ctl setup)."
                ),
            )
        else:
            ok(self.say, t("The radio is checked."))
        note(
            self.say,
            t(
                "One thing for later: the next time the computer starts, watch the radio:"
                "\nit must not start transmitting."
            ),
        )
        return config

    # -- A: the computer

    def _computer(self) -> None:
        self.say("\n" + t("A. The computer"))
        masked = self.tools.output(["systemctl", "is-enabled", "sleep.target"]).strip()
        self._automatic(
            "sleep",
            masked == "masked",
            t("The computer will not go to sleep"),
            t("The computer can go to sleep. Run the installer (install.sh) again."),
        )
        rule = self.system.udev_rules / "90-remoteg90ctl-serial.rules"
        brltty = self.tools.output(["systemctl", "is-active", "brltty.service"]).strip()
        self._automatic(
            "serial port",
            rule.exists() and brltty != "active",
            t("No other program will take the radio's port"),
            t(
                "Another program may take the radio's port and make it transmit."
                " Run the installer (install.sh) again."
            ),
        )

    # -- B: the radio's settings

    def _radio_settings(self) -> None:
        self.say("\n" + t("B. The radio"))
        self._item(
            "cat socket",
            t(
                "Is the DE-19's cable in the IO/User socket on the radio's FRONT panel?"
                "\n(Not in COMM at the back: that one is for updating the radio's software.)"
            ),
            t("the COMM socket at power-on can put the radio into software update mode"),
        )
        self._item(
            "vox",
            t("Is VOX switched off? (On the radio: FUNC + VOL, then VOX: OFF.)"),
            t(
                "with VOX on, any sound from the computer makes the radio transmit,"
                " past every safeguard of this program"
            ),
        )
        self._item(
            "swr threshold",
            t("Is SWR THR in the radio's menu 3:1 or lower? (2.5 is recommended.)"),
            t("it is the only thing that protects the radio from a bad antenna"),
        )
        self._item(
            "test setup",
            t("Is the radio on a dummy load or a tuned antenna?"),
            t("the test transmissions would go into whatever is connected"),
        )
        # Power and mode can be read only through the running program; its start
        # blinks the radio, so on a first installation the operator sets them
        # first, and step D reads them right after the start.
        if self._running():
            self._until_ready(need_band=False)
        else:
            self._item(
                "power and mode",
                t("Is the radio set to 1 W and to SSB (USB or LSB)? (The POW and MODE buttons.)"),
                t(
                    "the program's start makes the radio blink to transmit for an instant;"
                    " in SSB that sends practically nothing, in CW, AM or FM a carrier"
                ),
            )

    # -- C: plugging in, with the program stopped

    def _plugging(self) -> None:
        self.say("\n" + t("C. Plugging in (the program is stopped now)"))
        self.system.run(["systemctl", "stop", SERVER, GUARD])
        self._item(
            "plugging the de-19",
            t(
                "Pull the DE-19's USB cable out of the computer and put it back, watching"
                " the radio.\nDid the radio stay in receive?"
            ),
            t(
                "the interface itself may make the radio transmit when it is plugged in;"
                " no program can stop that"
            ),
        )
        self._item(
            "plugging other usb",
            t(
                "Now the same with the other USB devices (the waterfall card, a modem)."
                "\nDid the radio stay in receive?"
            ),
            t("the same, for the other devices"),
        )

    # -- D: the program starts, transmitting still off

    def _program_start(self, config: Config) -> Config:
        self.say("\n" + t("D. The program starts (transmitting still off)"))
        if config.safety.allow_transmit:
            config = set_config(self.say, self.system, "safety", {"allow_transmit": "false"})
        note(
            self.say,
            t(
                "Starting the program. Watch the radio: it may blink to transmit for a split"
                " second;\nthat is normal."
            ),
        )
        station = self._start()
        try:
            self._item(
                "guard start",
                t("Did the radio blink at most for an instant and go back to receive?"),
                t(
                    "a longer transmission at start means the interface's lines key the radio:"
                    " stop and look at the interface"
                ),
                stop_services=True,
            )
            guard = _guard(station)
            self._automatic(
                "port",
                guard.get("port") == "open",
                t("The program is connected to the radio"),
                t("The program cannot use the radio's port ({state}).", state=guard.get("port")),
            )
            identity = guard.get("identity")
            state = identity.get("state") if isinstance(identity, dict) else None
            detail = identity.get("detail") if isinstance(identity, dict) else ""
            self._automatic(
                "identity",
                state == "confirmed",
                t("It is a Xiegu G90"),
                t("The radio did not say it is a G90 ({detail}).", detail=detail),
            )
            station.close()
            self._until_ready(need_band=False)
            station = self._wait_for_server()
            hz = station.request("get_frequency").get("hz")
            mode = station.request("get_mode").get("mode")
            if not isinstance(hz, int):
                raise SetupStoppedError(t("the radio does not answer"))
            self._item(
                "cat",
                t(
                    "Does the radio show {mhz} MHz, {mode}? (Compare with its display.)",
                    mhz=f"{hz / 1e6:.6f}".rstrip("0").rstrip("."),
                    mode=mode,
                ),
                t("the program reads something else than the radio shows"),
                stop_services=True,
            )
            audio = station.request("status").get("audio")
            clock = audio.get("clock_synchronized") if isinstance(audio, dict) else None
            if clock is not True:
                warn(
                    self.say,
                    t(
                        "The computer's clock is not set from the internet yet. FT8/FT4 will"
                        " not transmit\nuntil it is; everything else works."
                    ),
                )
        finally:
            station.close()
        return config

    # -- E: two short test transmissions

    def _test_transmissions(self, config: Config) -> Config:
        self.say("\n" + t("E. Test transmissions"))
        note(
            self.say,
            t(
                "The radio will now transmit twice, briefly, to check that the program switches"
                " it\non and off. What is the radio connected to now?"
            ),
        )
        self.say("    1 — " + t("a dummy load"))
        self.say(
            "    2 — "
            + t(
                "the antenna (set the radio to 1 W, USB, e.g. 14.074 MHz;"
                " otherwise the program refuses)"
            )
        )
        self.say("    0 — " + t("neither: leave the test transmissions out"))
        load = number(self.ask, t("Number:"), 2)
        if load == 0:
            self._skip("ptt test", t("not tested"))
            self._skip("interrupted transmission", t("not tested"))
            return config
        if not self._until_ready(need_band=load == 2):
            self._skip("ptt test", t("not tested"))
            self._skip("interrupted transmission", t("not tested"))
            return config
        confirmation = "--dummy-load-connected" if load == 1 else "--antenna-low-power"
        config = set_config(self.say, self.system, "safety", {"allow_transmit": "true"})
        try:
            self._restart()
            test = [*self.tools.cli, "tx-test", confirmation, "--seconds"]
            # A test that did not transmit is never asked about as if it had:
            # the operator puts it right and it runs again, or it is left out.
            while True:
                note(self.say, t("Transmitting for {seconds} seconds…", seconds=TX_TEST_S))
                code, output = self.tools.spawn([*test, str(TX_TEST_S)])()
                if code == 0:
                    break
                fail(self.say, t("The radio did not transmit. The program says:"))
                note(self.say, "  " + output.strip().replace("\n", "\n  "))
                if is_skip(
                    self.ask("  " + t("Put it right and press Enter to try again, or type SKIP: "))
                ):
                    self._skip("ptt test", t("not tested"))
                    self._skip("interrupted transmission", t("not tested"))
                    return config
            self._item(
                "ptt test",
                t("Did the radio transmit for about 2 seconds and go back to receive by itself?"),
                t("the program may not be able to switch the radio to transmit or back"),
                stop_services=True,
            )
            note(
                self.say,
                t("Now a transmission that is cut off: I switch the program off after 2 seconds…"),
            )
            wait = self.tools.spawn([*test, str(INTERRUPTED_TEST_S)])
            self.tools.pause(2)
            self.system.run(["systemctl", "stop", SERVER])
            wait()  # it ends with an error: the server went away, as planned
            self._item(
                "interrupted transmission",
                t("Did the radio transmit, then go back to receive within 2 s of the stop?"),
                t("a transmission might not end when the browser or the program goes away"),
                stop_services=True,
            )
            ok(self.say, t("The test transmissions are done: you can set your usual power again."))
        finally:
            config = set_config(self.say, self.system, "safety", {"allow_transmit": "false"})
        return config

    def _until_ready(self, *, need_band: bool) -> bool:
        """Read power and mode (and the band) until they suit a test transmission.

        1 W and SSB for every test (owner's decision 2026-11-11); on the antenna
        also a band every ITU region has, as the server checks (D56). False: the
        operator typed SKIP, recorded as "power and mode".
        """
        while True:
            station = self._wait_for_server()
            try:
                power = station.request("get_power_meter")
                hz = station.request("get_frequency").get("hz")
                mode = station.request("get_mode").get("mode")
            finally:
                station.close()
            setting = power.get("setting_watts")
            watts = round(setting) if isinstance(setting, int | float) else None
            problems = []
            if watts is None or watts > TEST_POWER_W:
                problems.append(
                    t(
                        "the radio is set to {watts} W: set 1 W (the POW button)",
                        watts=watts if watts is not None else "?",
                    )
                )
            if mode not in SSB_MODES:
                problems.append(t("the radio is in {mode}: switch it to USB", mode=mode))
            if need_band and (not isinstance(hz, int) or not _common_band(hz, str(mode))):
                problems.append(
                    t(
                        "{mhz} MHz is not in a band every country has: tune e.g. 14.074 MHz",
                        mhz=f"{hz / 1e6:.4f}" if isinstance(hz, int) else "?",
                    )
                )
            if not problems:
                ok(self.say, t("Power and mode: {watts} W, {mode}", watts=watts, mode=mode))
                return True
            for problem in problems:
                warn(self.say, problem)
            if is_skip(self.ask("  " + t("Set it and press Enter, or type SKIP: "))):
                if "power and mode" not in self.skipped:
                    self._skip("power and mode", t("the radio may transmit at more than 1 W"))
                return False

    def _running(self) -> bool:
        """Whether the program already runs (a later setup): then the radio can be read."""
        try:
            station = self.tools.connect()
        except OSError:
            return False
        try:
            return station.request("status").get("guard") is not None
        finally:
            station.close()

    # -- F: what the browser may do

    def _browser(self, config: Config) -> Config:
        self.say("\n" + t("F. Transmitting from the browser"))
        power = number(
            self.ask,
            t(
                "The highest power the page may set, in watts, 1 to {highest}"
                " (0: the page does not change the power):",
                highest=MAX_POWER_W,
            ),
            MAX_POWER_W,
        )
        tune = yes(
            self.ask,
            t(
                "May the page start the antenna tuner?"
                " (Tuning transmits a carrier, usually for 1-1.5 s,"
                " longer with a poorly matched antenna.)"
            ),
        )
        on = yes(self.ask, t("Switch transmitting from the browser on?"))
        values = {
            "allow_transmit": "true" if on else "false",
            "allow_atu_tune": "true" if tune else "false",
        }
        if power > 0:
            values["max_rf_power_w"] = str(power)
        config = set_config(self.say, self.system, "safety", values)
        if on:
            ok(self.say, t("Transmitting from the browser: on"))
        else:
            warn(
                self.say, t("Transmitting from the browser: off (the setup can switch it on later)")
            )
        return config

    # ---- Helpers ----

    def _item(self, key: str, question: str, risk: str, *, stop_services: bool = False) -> None:
        answer = check(self.ask, question)
        if answer == "yes":
            return
        if answer == "no":
            if stop_services:
                self.system.run(["systemctl", "stop", SERVER, GUARD])
            raise SetupStoppedError(t("you answered no: {item}", item=t(key)))
        self._skip(key, risk)

    def _automatic(self, key: str, good: bool, done: str, problem: str) -> None:
        if good:
            ok(self.say, done)
            return
        fail(self.say, problem)
        answer = self.ask("  " + t("Type SKIP to go on anyway, or press Enter to stop: "))
        if not is_skip(answer):
            self.system.run(["systemctl", "stop", SERVER, GUARD])
            raise SetupStoppedError(problem)
        self._skip(key, problem)

    def _skip(self, key: str, risk: str) -> None:
        warn(self.say, t("Left out: {item}. Risk: {risk}.", item=t(key), risk=risk))
        self.skipped.append(key)

    def _start(self) -> Station:
        self.system.run(["systemctl", "start", GUARD, SERVER])
        return self._wait_for_server()

    def _restart(self) -> None:
        note(self.say, t("Restarting the program (an instant's blink, as before)…"))
        self.system.run(["systemctl", "restart", GUARD, SERVER])
        self._wait_for_server().close()

    def _wait_for_server(self) -> Station:
        deadline = time.monotonic() + SERVER_WAIT_S
        while True:
            try:
                station = self.tools.connect()
                if station.request("status").get("guard") is not None:
                    return station
                station.close()
            except OSError:
                pass
            if time.monotonic() > deadline:
                self.system.run(["systemctl", "stop", SERVER, GUARD])
                raise SetupStoppedError(
                    t(
                        "the program did not start"
                        " (journalctl -u remoteg90ctl-guard -u remoteg90ctl-server -n 50)"
                    )
                )
            self.tools.pause(1)


def _guard(station: Station) -> dict[str, object]:
    guard = station.request("status").get("guard")
    return guard if isinstance(guard, dict) else {}
