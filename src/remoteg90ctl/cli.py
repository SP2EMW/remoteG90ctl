# SPDX-License-Identifier: GPL-3.0-or-later
"""``remoteg90ctl`` command-line client.

The CLI talks **only to the server**, never to the guard (AGENTS.md rule 5).
It does not read the configuration file and knows only the server socket.
"""

import argparse
import getpass
import json
import os
import socket
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from remoteg90ctl import __version__

DEFAULT_SOCKET = Path("/run/remoteg90ctl/server.sock")
PROTOCOL_VERSION = 1
TIMEOUT_S = 5.0
# Error code from docs/server-protocol.md.
UNKNOWN_LEASE = "unknown_lease"
# How long to wait for the reason after a renewal of an already ended lease.
ENDED_EVENT_WAIT_S = 0.5
POWER_WATCH_INTERVAL_S = 0.5

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class CliError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ConnectionLostError(CliError):
    """The connection to the server broke after it was established."""


class ServerConnection:
    """Line-based JSON client; events arriving between responses are buffered."""

    def __init__(self, path: Path, timeout: float = TIMEOUT_S, *, verbose: bool = False) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._timeout = timeout
        self._verbose = verbose
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(str(path))
        except OSError as exc:
            self._sock.close()
            raise CliError(
                f"cannot connect to the server at {path}: {exc.strerror or exc}", EXIT_USAGE
            ) from None
        self._buffer = b""
        self._ids = 0
        self.events: list[dict[str, Any]] = []

    def close(self) -> None:
        self._sock.close()

    def request(
        self, kind: str, *, wait_s: float | None = None, **fields: object
    ) -> dict[str, Any]:
        """Send a request and return its response; ``wait_s`` overrides the timeout."""
        timeout = self._timeout if wait_s is None else wait_s
        self._ids += 1
        line = json.dumps({"v": PROTOCOL_VERSION, "id": self._ids, "type": kind, **fields})
        try:
            if self._verbose:
                _err(f"> {line}")
            self._sock.sendall(line.encode() + b"\n")
            while True:
                message = self._receive(timeout)
                if message is None:
                    raise CliError("the server did not answer in time")
                if "event" in message:
                    self.events.append(message)
                elif message.get("id") == self._ids:
                    return message
        except OSError as exc:
            raise ConnectionLostError(f"connection to the server failed: {exc}") from None

    def wait_events(self, seconds: float) -> None:
        """Collect events for up to ``seconds``."""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = self._receive(remaining)
            except OSError as exc:
                raise ConnectionLostError(f"connection to the server failed: {exc}") from None
            if message is None:
                return
            if "event" in message:
                self.events.append(message)

    def _receive(self, timeout: float) -> dict[str, Any] | None:
        while b"\n" not in self._buffer:
            self._sock.settimeout(max(timeout, 0.001))
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                return None
            if not chunk:
                raise ConnectionLostError("the server closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        if self._verbose:
            _err(f"< {line.decode(errors='replace')}")
        message: dict[str, Any] = json.loads(line)
        return message


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _checked(response: dict[str, Any]) -> dict[str, Any]:
    if not response.get("ok"):
        raise CliError(f"{response.get('error')}: {response.get('message')}")
    return response


# Commands


def cmd_status(conn: ServerConnection, _args: argparse.Namespace) -> int:
    status = _checked(conn.request("status"))
    _out(f"backend:        {status['backend']}")
    guard = status.get("guard")
    versions = {"cli": __version__, "server": status.get("version")}
    if guard is not None:
        versions["guard"] = guard.get("version")
    _out("versions:       " + ", ".join(f"{name} {v}" for name, v in versions.items()))
    if len(set(versions.values())) > 1:
        _out("                WARNING: versions differ - restart both services after an update")
    if guard is None:
        _out("guard:          NOT CONNECTED (transmission impossible)")
        return EXIT_OK
    _out(f"serial port:    {guard['port']} ({guard['port_detail']})")
    identity = guard.get("identity") or {}
    _out(f"radio identity: {identity.get('state')} ({identity.get('detail')})")
    _out(f"PTT line:       {guard['ptt_line']}")
    tx = guard.get("tx")
    if tx is None:
        _out("transmitting:   no")
    else:
        mine = " (this connection)" if status.get("tx_yours") else ""
        _out(
            f"transmitting:   YES{mine}, {tx['elapsed_s']:.1f} s, "
            f"{tx['remaining_s']:.1f} s to hard limit, source {tx['source']}"
        )
    lockout = guard.get("lockout")
    _out(
        "lockout:        "
        + (f"{lockout['reason']} since {lockout['since']} (tx-unlock)" if lockout else "none")
    )
    blockers = list(guard.get("blockers") or [])
    if lockout:
        blockers.insert(0, f"lockout {lockout['reason']} (tx-unlock)")
    _out("TX blocked by:  " + ("; ".join(blockers) if blockers else "nothing"))
    limits = guard["limits"]
    _out(
        f"limits:         renew {limits['lease_renew_interval_s']} s, "
        f"timeout {limits['lease_timeout_s']} s, hard limit {limits['hard_limit_s']} s"
    )
    duty = guard.get("duty")
    if duty is not None:
        _out(
            f"duty cycle:     {duty['used_s']:.0f} s of {limits['duty_max_tx_s']:g} s "
            f"in the last {limits['duty_window_s']:g} s"
            + (
                f", next transmission in {duty['available_in_s']:.0f} s"
                if duty["available_in_s"] > 0
                else ""
            )
        )
    return EXIT_OK


def cmd_freq(conn: ServerConnection, args: argparse.Namespace) -> int:
    if args.hz is None:
        _out(str(_checked(conn.request("get_frequency"))["hz"]))
    else:
        _checked(conn.request("set_frequency", hz=args.hz))
    return EXIT_OK


def cmd_mode(conn: ServerConnection, args: argparse.Namespace) -> int:
    if args.mode is None:
        _out(str(_checked(conn.request("get_mode"))["mode"]))
    else:
        _checked(conn.request("set_mode", mode=args.mode))
    return EXIT_OK


def cmd_power(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Print the RF power meter: value and raw reply (measurement, AGENTS.md item 5).

    With ``--set W`` set the RF power instead (within safety.max_rf_power_w, D44).
    """
    if args.set is not None:
        reply = _checked(conn.request("set_rf_power", watts=args.set))
        _out(f"setting {reply['setting']}\t{reply['setting_watts']}W")
        return EXIT_OK
    while True:
        reply = _checked(conn.request("get_power_meter"))
        watts = reply["watts"]
        shown = "-" if watts is None else f"{'>' if reply['above'] else ''}{watts}W"
        _out(
            f"meter {reply['value']}\t{shown}\t{reply['raw']}"
            f"\tsetting {reply['setting']}\t{reply['setting_watts']}W\t{reply['setting_raw']}"
            f"\tswr {reply['swr_level']}\t{reply['swr_raw']}"
        )
        if not args.watch:
            return EXIT_OK
        time.sleep(POWER_WATCH_INTERVAL_S)


def cmd_front_end(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Preamplifier / attenuator: show, or set off, pre or att."""
    if args.state is None:
        _out(str(_checked(conn.request("get_front_end"))["front_end"]))
    else:
        _checked(conn.request("set_front_end", state=args.state))
    return EXIT_OK


def cmd_agc(conn: ServerConnection, args: argparse.Namespace) -> int:
    """AGC: show, or set off, fast, slow or auto."""
    if args.agc is None:
        _out(str(_checked(conn.request("get_agc"))["agc"]))
    else:
        _checked(conn.request("set_agc", agc=args.agc))
    return EXIT_OK


def cmd_nb(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Noise blanker: show, switch on/off, or set the level 0-10."""
    if args.action is None:
        reply = _checked(conn.request("get_nb"))
        _out(f"{'on' if reply['nb_on'] else 'off'}, level {reply['nb_level']}")
    elif args.action == "level":
        if args.value is None:
            raise CliError("nb level needs a value 0-10")
        _checked(conn.request("set_nb", level=args.value))
    else:
        _checked(conn.request("set_nb", on=args.action == "on"))
    return EXIT_OK


def cmd_rf_gain(conn: ServerConnection, args: argparse.Namespace) -> int:
    """RF gain: show, or set 1-100 %."""
    if args.percent is None:
        _out(f"{_checked(conn.request('get_rf_gain'))['rf_gain']} %")
    else:
        _checked(conn.request("set_rf_gain", percent=args.percent))
    return EXIT_OK


def cmd_alc(conn: ServerConnection, _args: argparse.Namespace) -> int:
    """ALC meter: as on the radio's display, and the raw level."""
    alc = _checked(conn.request("get_alc"))["alc"]
    _out(f"ALC {alc['display']} (level {alc['level']} of 255)")
    return EXIT_OK


def cmd_s_meter(conn: ServerConnection, _args: argparse.Namespace) -> int:
    """S-meter: the radio's reading and the raw level."""
    meter = _checked(conn.request("get_s_meter"))["s_meter"]
    _out(f"{meter['text']} ({meter['db']:+.0f} dB from S9, level {meter['level']})")
    return EXIT_OK


def cmd_atu(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Antenna tuner: state, on, off, or tune (tune transmits a short carrier, D43)."""
    if args.action == "state":
        _out("on" if _checked(conn.request("get_atu"))["on"] else "off")
    elif args.action == "tune":
        _checked(conn.request("atu_tune"))
    else:
        _checked(conn.request("set_atu", on=args.action == "on"))
    return EXIT_OK


def _show_vfo(answer: dict[str, Any]) -> None:
    other = "B" if answer["vfo"] == "A" else "A"
    split = "on (transmits on VFO " + other + ")" if answer["split"] else "off"
    _out(f"VFO {answer['vfo']}, VFO {other}: {answer['other_hz']} Hz, split {split}")


def cmd_vfo(conn: ServerConnection, args: argparse.Namespace) -> int:
    """VFO A/B: show both and split, or select one."""
    if args.vfo is None:
        _show_vfo(_checked(conn.request("get_vfo")))
    else:
        _show_vfo(_checked(conn.request("set_vfo", vfo=args.vfo.upper())))
    return EXIT_OK


def cmd_split(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Split: show it, or switch it on/off (in split the radio transmits on the other VFO)."""
    if args.action is None:
        _show_vfo(_checked(conn.request("get_vfo")))
    else:
        _show_vfo(_checked(conn.request("set_split", on=args.action == "on")))
    return EXIT_OK


def cmd_af(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Speaker volume 0-255: read it, or set it."""
    if args.level is None:
        _out(str(_checked(conn.request("get_af"))["level"]))
    else:
        _out(str(_checked(conn.request("set_af", level=args.level))["level"]))
    return EXIT_OK


def cmd_user(conn: ServerConnection, args: argparse.Namespace) -> int:
    """Browser accounts (D48): list, reset a password, delete. This machine only."""
    if args.action == "list":
        answer = _checked(conn.request("account_list"))
        if answer.get("broken"):
            _out(f"accounts file unusable: {answer['broken']}")
        for account in answer["accounts"]:
            role = "  (administrator: manages accounts)" if account["admin"] else ""
            _out(f"{account['callsign']:<10} {account['locator']}{role}")
        return EXIT_OK
    if args.callsign is None:
        raise CliError(f"user {args.action} needs a callsign", EXIT_USAGE)
    if args.action == "delete":
        _checked(conn.request("account_delete", callsign=args.callsign))
        _out(f"{args.callsign.upper()} deleted; its pages were closed")
        return EXIT_OK
    password = args.password
    if password is None:
        password = getpass.getpass(f"new password for {args.callsign.upper()}: ")
        if getpass.getpass("again: ") != password:
            raise CliError("the passwords differ", EXIT_USAGE)
    _checked(conn.request("account_password", callsign=args.callsign, password=password))
    _out(f"password of {args.callsign.upper()} set; its sessions ended")
    return EXIT_OK


def cmd_tx_unlock(conn: ServerConnection, _args: argparse.Namespace) -> int:
    response = _checked(conn.request("tx_unlock"))
    _out("transmit unlocked" if response.get("was_locked") else "transmit was not locked")
    return EXIT_OK


def cmd_port_open(conn: ServerConnection, _args: argparse.Namespace) -> int:
    _checked(conn.request("port_open"))
    _out("serial port open, lines confirmed inactive")
    return EXIT_OK


def _check_tx_test_allowed(conn: ServerConnection, args: argparse.Namespace) -> None:
    status = _checked(conn.request("status"))
    guard = status.get("guard")
    if guard is None:
        raise CliError("the guard is not connected")
    hard_limit = float(guard["limits"]["hard_limit_s"])
    if not 0 < args.seconds <= hard_limit:
        raise CliError(f"--seconds must be between 0 and {hard_limit:g}", EXIT_USAGE)
    # Report a known refusal before warning that the radio will transmit. The
    # guard decides anyway; this only keeps the message honest.
    lockout = guard.get("lockout")
    if lockout:
        raise CliError(f"transmit locked: {lockout['reason']} since {lockout['since']} (tx-unlock)")
    blockers = guard.get("blockers") or []
    if blockers:
        raise CliError("transmit blocked: " + "; ".join(str(b) for b in blockers))
    if status["backend"] == "hardware":
        if not (args.dummy_load_connected or args.antenna_low_power):
            raise CliError(
                "refusing to transmit on hardware without --dummy-load-connected"
                " or --antenna-low-power",
                EXIT_USAGE,
            )
        if args.antenna_low_power and not args.dummy_load_connected:
            _err(
                "WARNING: the radio will TRANSMIT ON THE ANTENNA (SSB, at most 1 W set,"
                " a band of every ITU region; the server checks)."
            )
        else:
            _err("WARNING: the radio will TRANSMIT. Dummy load connected, low power set?")


def cmd_tx_test(conn: ServerConnection, args: argparse.Namespace) -> int:
    _check_tx_test_allowed(conn, args)
    granted = _checked(
        conn.request(
            "tx_request",
            dummy_load_confirmed=args.dummy_load_connected,
            antenna_confirmed=args.antenna_low_power,
        )
    )
    lease = granted["lease"]
    period = float(granted["lease_renew_interval_s"]) / 2
    _out(f"transmitting for {args.seconds:g} s (Ctrl+C stops)")
    start = time.monotonic()
    active = True
    try:
        while (elapsed := time.monotonic() - start) < args.seconds:
            conn.wait_events(min(period, args.seconds - elapsed))
            if (reason := _ended_reason(conn, lease)) is not None:
                active = False
                raise CliError(f"transmission ended by the system: {reason}")
            if time.monotonic() - start >= args.seconds:
                break
            renewed = conn.request("tx_renew", lease=lease)
            if not renewed.get("ok"):
                active = False
                if renewed.get("error") == UNKNOWN_LEASE:
                    # The server may have ended the transmission just before this
                    # renewal; its tx_ended event with the reason follows shortly.
                    conn.wait_events(ENDED_EVENT_WAIT_S)
                    if (reason := _ended_reason(conn, lease)) is not None:
                        raise CliError(f"transmission ended by the system: {reason}")
                raise CliError(f"renewal refused: {renewed.get('error')}: {renewed.get('message')}")
    except ConnectionLostError as exc:
        # Nothing to release over a dead connection: the server releases TX when a
        # client disconnects, and the guard when the server does.
        active = False
        reason = _ended_reason(conn, lease)
        suffix = f" (reason: {reason})" if reason is not None else ""
        raise CliError(f"{exc}; the transmission was ended by the system{suffix}") from None
    except KeyboardInterrupt:
        _err("interrupted")
        if active:
            _release(conn, lease)
            active = False
        return EXIT_INTERRUPTED
    finally:
        if active:
            _release(conn, lease)
    _out(f"done after {time.monotonic() - start:.1f} s")
    return EXIT_OK


def _ended_reason(conn: ServerConnection, lease: str) -> str | None:
    """The reason from a received ``tx_ended`` event for ``lease``, if any."""
    for e in conn.events:
        if e.get("event") == "tx_ended" and e.get("lease") == lease:
            return str(e.get("reason"))
    return None


def _release(conn: ServerConnection, lease: str) -> None:
    # Even if this fails, closing the connection releases TX on the server.
    try:
        conn.request("tx_release", lease=lease)
    except CliError as exc:
        _err(f"release failed ({exc}); closing the connection releases TX")


def cmd_iq_status(conn: ServerConnection, _args: argparse.Namespace) -> int:
    iq = _checked(conn.request("status")).get("iq")
    if iq is None:
        _out("I/Q input:      not configured (no [iq] section)")
        return EXIT_OK
    if iq["source"] == "none":
        _out('I/Q input:      off ([iq] source = "none"): no waterfall, scale only')
        return EXIT_OK
    state = "running" if iq["running"] else f"NOT RUNNING ({iq['error']})"
    _out(f"I/Q input:      {iq['source']}, {state}")
    _out(f"format:         {iq['sample_rate']} Hz, {iq['sample_bits']} bit")
    idle = " (paused: no browser watches the waterfall)" if iq.get("idle") else ""
    _out(
        f"spectrum:       {iq['fft_size']} bins, {iq['line_rate']:g} lines/s, "
        f"{iq['lines']} lines{idle}"
    )
    level = iq["level_dbfs"]
    _out(f"level:          {'-' if level is None else f'{level:.1f} dBFS'}")
    _out(f"overruns:       {iq['overruns']} (card), {iq['dropped_blocks']} dropped blocks")
    offsets = iq["center_offset_hz"] or {}
    _out(
        "centre offset:  "
        + (
            ", ".join(f"{mode} {hz:+g} Hz" for mode, hz in offsets.items())
            if offsets
            else "not measured (frequency scale uncalibrated)"
        )
    )
    return EXIT_OK


def cmd_iq_record(conn: ServerConnection, args: argparse.Namespace) -> int:
    seconds = float(args.seconds)
    _out(f"recording {seconds:g} s of I/Q ...")
    result = _checked(conn.request("iq_record", wait_s=seconds + 10, seconds=seconds))
    _out(f"saved {result['path']} ({result['sample_rate']} Hz, {result['sample_bits']} bit)")
    return EXIT_OK


def cmd_iq_analyze(args: argparse.Namespace) -> int:
    """Offline: measure a recording (AGENTS.md section 8, items 1 to 2c)."""
    # numpy is loaded only here, so the other commands stay quick.
    from remoteg90ctl.iq.analyze import analyze  # noqa: PLC0415
    from remoteg90ctl.iq.wav import read_wav  # noqa: PLC0415

    try:
        rec = read_wav(args.file)
        result = analyze(
            rec.frames,
            rec.sample_rate,
            swap_channels=args.swap_channels,
            invert_q=args.invert_q,
            signal_hz=args.signal_hz,
        )
    except (OSError, ValueError) as exc:
        raise CliError(str(exc), EXIT_USAGE) from None
    _out(f"file:           {args.file}")
    _out(f"format:         {rec.sample_rate} Hz, {rec.bits} bit, {result.seconds:.1f} s")
    _out(f"RMS L / R:      {result.rms_dbfs[0]:.1f} / {result.rms_dbfs[1]:.1f} dBFS")
    _out(f"balance L-R:    {result.balance_db:+.2f} dB")
    _out(f"DC L / R:       {result.dc_dbfs[0]:.1f} / {result.dc_dbfs[1]:.1f} dBFS")
    _out(f"noise floor:    {result.noise_floor_dbfs:.1f} dBFS per {result.bin_hz:.2f} Hz bin")
    _out("strongest signals (offset from the I/Q centre):")
    for peak in result.peaks:
        _out(f"  {peak.offset_hz:+10.1f} Hz  {peak.level_dbfs:7.1f} dBFS")
    if result.image_rejection_db is not None:
        _out(f"image rejection at {args.signal_hz:+g} Hz: {result.image_rejection_db:.1f} dB")
    return EXIT_OK


def _add_radio_settings(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Commands for the radio's receive settings and the tuner."""
    front = sub.add_parser(
        "frontend", help="preamplifier / attenuator: show, or set off, pre, att (12 dB)"
    )
    front.add_argument("state", nargs="?", choices=["off", "pre", "att"])
    front.set_defaults(func=cmd_front_end)
    agc = sub.add_parser("agc", help="AGC: show, or set off, fast, slow, auto")
    agc.add_argument("agc", nargs="?", choices=["off", "fast", "slow", "auto"])
    agc.set_defaults(func=cmd_agc)
    nb = sub.add_parser("nb", help="noise blanker: show, on, off, or level 0-10")
    nb.add_argument("action", nargs="?", choices=["on", "off", "level"])
    nb.add_argument("value", nargs="?", type=int, help="level 0-10 (with 'level')")
    nb.set_defaults(func=cmd_nb)
    atu = sub.add_parser(
        "atu", help="antenna tuner: state, on, off, tune (tune TRANSMITS a short carrier)"
    )
    atu.add_argument("action", choices=["state", "on", "off", "tune"])
    atu.set_defaults(func=cmd_atu)
    vfo = sub.add_parser("vfo", help="show VFO A/B and split, or select a VFO")
    vfo.add_argument("vfo", nargs="?", choices=["A", "B", "a", "b"])
    vfo.set_defaults(func=cmd_vfo)
    split = sub.add_parser("split", help="show split, or switch it on/off (TX on the other VFO)")
    split.add_argument("action", nargs="?", choices=["on", "off"])
    split.set_defaults(func=cmd_split)
    af = sub.add_parser("af", help="read or set the speaker volume (0-255)")
    af.add_argument("level", nargs="?", type=int)
    af.set_defaults(func=cmd_af)
    rf_gain = sub.add_parser("rfgain", help="read or set the RF gain (1-100 %%)")
    rf_gain.add_argument("percent", nargs="?", type=int)
    rf_gain.set_defaults(func=cmd_rf_gain)
    sub.add_parser("smeter", help="read the S-meter").set_defaults(func=cmd_s_meter)
    sub.add_parser("alc", help="read the ALC meter (meaningful while transmitting)").set_defaults(
        func=cmd_alc
    )


def cmd_setup(_args: argparse.Namespace) -> int:
    """The interactive hardware setup, a program of its own (python -m remoteg90ctl.setup).

    Started, not imported: it reads the configuration, and the CLI must have no
    path to the guard (AGENTS.md rule 5).
    """
    os.execv(sys.executable, [sys.executable, "-m", "remoteg90ctl.setup"])  # noqa: S606  (our own module, fixed arguments)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remoteg90ctl", description="remoteG90ctl client")
    parser.add_argument(
        "--socket", type=Path, default=DEFAULT_SOCKET, help=f"server socket ({DEFAULT_SOCKET})"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print every message sent and received"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show server, guard and transmit state").set_defaults(
        func=cmd_status
    )
    freq = sub.add_parser("freq", help="read or set the frequency in Hz")
    freq.add_argument("hz", nargs="?", type=int)
    freq.set_defaults(func=cmd_freq)
    mode = sub.add_parser("mode", help="read or set the mode (LSB, USB, AM, CW, FM, CW-R)")
    mode.add_argument("mode", nargs="?")
    mode.set_defaults(func=cmd_mode)
    power = sub.add_parser("power", help="read the RF power meter (value and raw reply)")
    power.add_argument("--watch", action="store_true", help="repeat every 0.5 s until Ctrl+C")
    power.add_argument(
        "--set",
        type=float,
        metavar="W",
        help="set the RF power in watts (up to safety.max_rf_power_w); refused during TX",
    )
    power.set_defaults(func=cmd_power)
    _add_radio_settings(sub)
    tx = sub.add_parser("tx-test", help="test transmission with lease renewal")
    tx.add_argument("--seconds", type=float, required=True)
    tx.add_argument(
        "--dummy-load-connected",
        action="store_true",
        help="on hardware: confirms a dummy load is connected",
    )
    tx.add_argument(
        "--antenna-low-power",
        action="store_true",
        help="on hardware, instead: on the antenna; the server allows it only in SSB,"
        " at most 1 W set, in a band of every ITU region (decisions.md D56)",
    )
    tx.set_defaults(func=cmd_tx_test)
    sub.add_parser("tx-unlock", help="clear the transmit lockout").set_defaults(func=cmd_tx_unlock)
    user = sub.add_parser("user", help="browser accounts: list, passwd CALLSIGN, delete CALLSIGN")
    user.add_argument("action", choices=["list", "passwd", "delete"])
    user.add_argument("callsign", nargs="?")
    user.add_argument("--password", help="the new password (asked for when left out)")
    user.set_defaults(func=cmd_user)
    sub.add_parser("port-open", help="reopen the serial port after it was lost").set_defaults(
        func=cmd_port_open
    )
    iq = sub.add_parser("iq", help="I/Q input: status, recordings and their analysis")
    iq_sub = iq.add_subparsers(dest="iq_command", required=True)
    iq_sub.add_parser("status", help="state of the I/Q input").set_defaults(func=cmd_iq_status)
    record = iq_sub.add_parser("record", help="record raw I/Q on the server to a WAV file")
    record.add_argument("--seconds", type=float, required=True)
    record.set_defaults(func=cmd_iq_record)
    an = iq_sub.add_parser("analyze", help="measure a WAV recording (offline, no server)")
    an.add_argument("file", type=Path)
    an.add_argument("--signal-hz", type=float, help="offset of a known signal, for image rejection")
    an.add_argument("--swap-channels", action="store_true", help="I on the right channel")
    an.add_argument("--invert-q", action="store_true", help="negate Q")
    an.set_defaults(offline=cmd_iq_analyze)
    setup = sub.add_parser(
        "setup",
        help="hardware setup after install.sh: radio port, sound cards, configuration (root)",
    )
    setup.set_defaults(offline=cmd_setup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    offline = getattr(args, "offline", None)
    if offline is not None:
        try:
            code: int = offline(args)
        except CliError as exc:
            _err(f"remoteg90ctl: {exc}")
            return exc.exit_code
        return code
    try:
        conn = ServerConnection(args.socket, verbose=args.verbose)
    except CliError as exc:
        _err(f"remoteg90ctl: {exc}")
        return exc.exit_code
    try:
        result: int = args.func(conn, args)
    except CliError as exc:
        _err(f"remoteg90ctl: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        conn.close()
    return result


if __name__ == "__main__":
    sys.exit(main())
