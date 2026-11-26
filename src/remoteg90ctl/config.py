# SPDX-License-Identifier: GPL-3.0-or-later
"""Loading and validation of the TOML configuration.

Invalid configuration is a refusal to start: :func:`load_config` raises
:class:`ConfigError` listing every problem found. Safety parameters never get
silent defaults; the only default with safety relevance is
``safety.allow_transmit = false``, which is the safe direction.

Standard library only, so the guard may use it (AGENTS.md rule 11).
"""

import ipaddress
import itertools
import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

# Upper bounds from AGENTS.md rule 4. Configuration may shorten them, never extend.
MAX_LEASE_RENEW_INTERVAL_S = 0.5
MAX_LEASE_TIMEOUT_S = 2.0
MAX_HARD_LIMIT_S = 180.0
# Duty cycle (decisions.md D31): at most this much transmission in the window.
# Stricter means a longer window or less transmission time, so the window has a
# minimum and the transmission time a maximum.
MAX_DUTY_TX_S = 300.0
MIN_DUTY_WINDOW_S = 600.0
MAX_DUTY_WINDOW_S = 86_400.0
# The G90's RF power range (safety.max_rf_power_w, D44).
RF_POWER_MIN_W = 1.0
RF_POWER_MAX_W = 20.0

SERIAL_BY_ID_PREFIX = "/dev/serial/by-id/"
ALLOWED_BAUDRATES = frozenset({4800, 9600, 19200, 38400, 57600, 115200})
# radio.identity: body of the radio's answer to "1D 19", as hex.
MAX_IDENTITY_BYTES = 8
_IDENTITY_HEX = re.compile(rf"(?:[0-9a-fA-F]{{2}}){{1,{MAX_IDENTITY_BYTES}}}")
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# AGENTS.md rule 15: until authentication exists, only these addresses are allowed.
LOOPBACK_ADDRESSES = frozenset({ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")})
UNIX_LISTEN_PREFIX = "unix:"


class ConfigError(Exception):
    """The configuration is invalid; ``problems`` lists every issue found."""

    def __init__(self, problems: list[str], source: str | None = None) -> None:
        self.problems = problems
        where = f" in {source}" if source else ""
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"invalid configuration{where}:\n{lines}")


class Backend(StrEnum):
    SIMULATOR = "simulator"
    HARDWARE = "hardware"


class PttLine(StrEnum):
    DTR = "dtr"
    RTS = "rts"
    # PTT through a CI-V command: interface reserved, not implemented and not
    # verified on the G90 (AGENTS.md section 8, item 4). The guard starts with
    # transmit blocked when this is selected on hardware.
    CIV = "civ"


@dataclass(frozen=True, slots=True)
class RadioConfig:
    backend: Backend
    port: str | None
    baudrate: int | None
    radio_address: int
    controller_address: int
    ptt_line: PttLine
    # Expected body of the answer to "1D 19" (decisions.md D32); None = not configured,
    # which blocks transmitting until it is set.
    identity: bytes | None = None
    # Power meter calibration: (meter value, watts) points, rising; empty = no
    # watts shown, only the meter value. Measured on the radio (section 8, item 5).
    power_meter_watts: tuple[tuple[int, float], ...] = ()
    # Mute the radio's speaker (AF 0, 14 01) when the server connects to the
    # guard: a remote station must not play into the room (owner, 2026-11-04).
    mute_speaker: bool = False
    # USB and LSB are set as U-D and L-D (26 00 with the data flag): only in the
    # data modes does the G90 send receive audio to the DE-19 (owner, 2026-11-07).
    ssb_data_mode: bool = False


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    allow_transmit: bool
    lease_renew_interval_s: float
    lease_timeout_s: float
    hard_limit_s: float
    duty_window_s: float
    duty_max_tx_s: float
    # Antenna tuning on hardware (decisions.md D43), independent of allow_transmit.
    allow_atu_tune: bool = False
    # Highest RF power a client may set (CI-V 14 0A, D44); None = setting the
    # power is refused. The guard does not check the value: this is the limit.
    max_rf_power_w: float | None = None


@dataclass(frozen=True, slots=True)
class GuardConfig:
    socket_path: Path
    tx_log_path: Path
    state_dir: Path


@dataclass(frozen=True, slots=True)
class ServerConfig:
    socket_path: Path
    listen: tuple[str, ...]
    # Host names a loopback proxy such as `tailscale serve` presents the page
    # under (decisions.md D38); HTTPS only. The server itself never listens
    # outside loopback (AGENTS.md rule 15).
    allowed_hosts: tuple[str, ...] = ()
    # Accounts and login sessions (decisions.md D48); the systemd unit's
    # StateDirectory. Readable by the service user only.
    state_dir: Path = Path("/var/lib/remoteg90ctl-server")
    # Under an allowed_hosts name a request must carry Tailscale's identity
    # header, which `tailscale serve` adds for tailnet users and strips from
    # what clients send (decisions.md D57). Tailscale Funnel uses the same name
    # but brings no identity: the page stays off the public internet.
    require_tailnet_identity: bool = True


@dataclass(frozen=True, slots=True)
class ClusterConfig:
    """Where the server finds the DX cluster process (decisions.md D63)."""

    socket_path: Path = Path("/run/remoteg90ctl-cluster.sock")


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str


class AudioSourceKind(StrEnum):
    SYNTHETIC = "synthetic"
    ALSA = "alsa"


# The DE-19 card's capture gain is set to this when it is opened (stage 3,
# measured 2026-11-07: +23 dB with its AGC on clipped the U-D audio).
MAX_CAPTURE_GAIN_DB = 23.0
# Upper bound of [audio] tx_jitter_ms: the voice is delayed by it, and the
# tail played after a release grows with it (it plays while PTT is held).
MAX_TX_JITTER_MS = 500.0


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """Receive audio from the radio's audio card in the DE-19 (stage 3)."""

    source: AudioSourceKind
    device: str | None  # e.g. "hw:CARD=G90AUDIO,DEV=0"; None for the synthetic source
    # Capture gain of the card in dB, set (and its AGC switched off) when the
    # card is opened; None leaves the mixer as it is.
    capture_gain_db: float | None = None
    # Ceiling of the server-side limiter for audio sent to the radio (stage 4),
    # in dBFS; tuned on the radio so that ALC is not overdriven.
    tx_peak_dbfs: float = -6.0
    # The largest margin of browser voice collected before it is played to the
    # radio (note #189, 2026-11-18; adaptive from 80 ms since 2026-11-21): late
    # frames over LTE left gaps on the air. 0 = off.
    tx_jitter_ms: float = 200.0


class IqSourceKind(StrEnum):
    SYNTHETIC = "synthetic"
    ALSA = "alsa"
    FILE = "file"
    # No I/Q card: no waterfall, the page shows the frequency scale only.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class IqConfig:
    """The I/Q input of the waterfall (stage 2, decisions.md D35)."""

    source: IqSourceKind
    device: str | None  # ALSA device by card name, e.g. "hw:CARD=G90IQ,DEV=0"
    file: Path | None  # WAV file for source = "file"
    sample_rate: int
    sample_bits: int
    swap_channels: bool
    invert_q: bool
    dc_removal: bool
    amplitude_balance: float  # gain applied to Q
    phase_correction_deg: float
    # I/Q centre minus the CAT frequency per mode (AGENTS.md item 2a): on the G90
    # it depends on the mode (CW by the CW pitch). A mode missing here has an
    # uncalibrated frequency scale.
    center_offset_hz: dict[str, float]
    # Receive passband per mode relative to the dial frequency, (low, high) in Hz,
    # for the waterfall only. The G90 does not report its DSP filter over CAT
    # (decisions.md D37), so the operator keeps this in step with the radio.
    passband_hz: dict[str, tuple[float, float]]
    fft_size: int
    line_rate: float  # waterfall lines per second
    recordings_dir: Path


@dataclass(frozen=True, slots=True)
class Config:
    radio: RadioConfig
    safety: SafetyConfig
    guard: GuardConfig
    server: ServerConfig
    logging: LoggingConfig
    # None: no I/Q input; the server runs CAT and the lease relay only.
    # With source = "none" there is no I/Q input either, but the passband table
    # still marks the receive band on the page's frequency scale.
    iq: IqConfig | None = None
    # None: no receive audio for the browser.
    audio: AudioConfig | None = None
    # The DX cluster process (D63); the cluster itself is set in the page.
    cluster: ClusterConfig = ClusterConfig()


_MISSING = object()


class _Section:
    """Typed accessor for one TOML table that records problems instead of raising."""

    def __init__(self, name: str, data: object, problems: list[str]) -> None:
        self.name = name
        self.problems = problems
        if not isinstance(data, Mapping):
            problems.append(f"[{name}]: must be a table")
            data = {}
        self._data: dict[str, Any] = dict(data)
        self._seen: set[str] = set()

    def _key(self, key: str) -> str:
        return f"{self.name}.{key}"

    def raw(self, key: str) -> object:
        self._seen.add(key)
        return self._data.get(key, _MISSING)

    def has(self, key: str) -> bool:
        return key in self._data

    def error(self, key: str, message: str) -> None:
        self.problems.append(f"{self._key(key)}: {message}")

    def string(self, key: str, *, required: bool = True, default: str | None = None) -> str | None:
        value = self.raw(key)
        if value is _MISSING:
            if required:
                self.error(key, "is required")
            return default
        if not isinstance(value, str) or not value:
            self.error(key, "must be a non-empty string")
            return default
        return value

    def integer(self, key: str, *, required: bool = True) -> int | None:
        value = self.raw(key)
        if value is _MISSING:
            if required:
                self.error(key, "is required")
            return None
        # bool is a subclass of int; reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            self.error(key, "must be an integer")
            return None
        return value

    def boolean(self, key: str, *, default: bool) -> bool:
        value = self.raw(key)
        if value is _MISSING:
            return default
        if not isinstance(value, bool):
            self.error(key, "must be true or false")
            return default
        return value

    def duration(self, key: str, maximum: float) -> float | None:
        value = self.raw(key)
        if value is _MISSING:
            self.error(key, f"is required (seconds, at most {maximum:g})")
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            self.error(key, "must be a number of seconds")
            return None
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            self.error(key, "must be a positive, finite number of seconds")
            return None
        if seconds > maximum:
            self.error(
                key,
                f"{seconds:g} s exceeds the safety limit of {maximum:g} s "
                "(AGENTS.md rule 4: values may be shorter, never longer)",
            )
            return None
        return seconds

    def path(self, key: str) -> Path | None:
        value = self.string(key)
        if value is None:
            return None
        if not PurePosixPath(value).is_absolute():
            self.error(key, f"must be an absolute path, got {value!r}")
            return None
        # A control character (a newline above all) would let a path pass for
        # several in the scripts that read them line by line (deploy/restore.sh;
        # audit 2026-11-15, A1).
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            self.error(key, f"must not contain control characters, got {value!r}")
            return None
        return Path(value)

    def string_list(self, key: str) -> tuple[str, ...]:
        value = self.raw(key)
        if value is _MISSING:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.error(key, "must be a list of strings")
            return ()
        return tuple(value)

    def check_unknown(self) -> None:
        for key in sorted(set(self._data) - self._seen):
            self.error(key, "unknown key")


def parse_config(data: Mapping[str, Any], source: str | None = None) -> Config:
    """Validate an already parsed TOML document."""
    problems: list[str] = []
    sections = {"radio", "safety", "guard", "server", "logging", "iq", "audio", "cluster"}
    for name in sorted(set(data) - sections):
        problems.append(f"[{name}]: unknown section")
    for name in ("radio", "safety", "guard", "server"):
        if name not in data:
            problems.append(f"[{name}]: section is required")

    radio = _parse_radio(_Section("radio", data.get("radio", {}), problems))
    safety = _parse_safety(_Section("safety", data.get("safety", {}), problems))
    guard = _parse_guard(_Section("guard", data.get("guard", {}), problems))
    server = _parse_server(_Section("server", data.get("server", {}), problems))
    log = _parse_logging(_Section("logging", data.get("logging", {}), problems))
    iq = _parse_iq(_Section("iq", data["iq"], problems)) if "iq" in data else None
    audio = _parse_audio(_Section("audio", data["audio"], problems)) if "audio" in data else None
    cluster = _parse_cluster(_Section("cluster", data.get("cluster", {}), problems))

    if guard is not None and server is not None and guard.socket_path == server.socket_path:
        problems.append("server.socket_path: must differ from guard.socket_path")
    # Each parser returns None only after recording a problem; the fallback
    # message guards against a parser that forgets to.
    if problems or radio is None or safety is None or guard is None or server is None:
        raise ConfigError(problems or ["configuration rejected"], source)
    return Config(radio, safety, guard, server, log, iq, audio, cluster)


def load_config(path: Path) -> Config:
    """Read and validate a TOML configuration file."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ConfigError([f"cannot read file: {exc.strerror or exc}"], str(path)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"not valid TOML: {exc}"], str(path)) from exc
    return parse_config(data, str(path))


def _parse_radio(s: _Section) -> RadioConfig | None:
    backend_name = s.string("backend")
    backend: Backend | None = None
    if backend_name is not None:
        try:
            backend = Backend(backend_name)
        except ValueError:
            s.error("backend", f"must be 'simulator' or 'hardware', got {backend_name!r}")

    hardware = backend is Backend.HARDWARE
    port = s.string("port", required=hardware)
    if hardware and port is not None and not port.startswith(SERIAL_BY_ID_PREFIX):
        s.error(
            "port",
            f"must start with {SERIAL_BY_ID_PREFIX} (stable device name, AGENTS.md rule 8), "
            f"got {port!r}",
        )
        port = None

    baudrate = s.integer("baudrate", required=hardware)
    if baudrate is not None and baudrate not in ALLOWED_BAUDRATES:
        allowed = ", ".join(str(b) for b in sorted(ALLOWED_BAUDRATES))
        s.error("baudrate", f"must be one of {allowed}, got {baudrate}")
        baudrate = None

    if not s.has("radio_address"):
        # No default on purpose: published values conflict (AGENTS.md section 8, item 7a).
        s.error(
            "radio_address",
            "is required; there is no default because reported values conflict "
            "(0x88 vs 0x70) — read it from the radio's menu",
        )
        s.raw("radio_address")
        radio_address = None
    else:
        radio_address = _address(s, "radio_address")
    controller_address = _address(s, "controller_address")
    if (
        radio_address is not None
        and controller_address is not None
        and radio_address == controller_address
    ):
        s.error("controller_address", "must differ from radio.radio_address")
        controller_address = None

    ptt_name = s.string("ptt_line")
    ptt_line: PttLine | None = None
    if ptt_name is not None:
        try:
            ptt_line = PttLine(ptt_name)
        except ValueError:
            s.error("ptt_line", f"must be 'dtr', 'rts' or 'civ', got {ptt_name!r}")

    identity = _identity(s)
    power_points = _power_points(s)
    mute_speaker = s.boolean("mute_speaker", default=False)
    ssb_data_mode = s.boolean("ssb_data_mode", default=False)

    s.check_unknown()
    if backend is None or radio_address is None or controller_address is None or ptt_line is None:
        return None
    if hardware and (port is None or baudrate is None):
        return None
    return RadioConfig(
        backend,
        port,
        baudrate,
        radio_address,
        controller_address,
        ptt_line,
        identity,
        power_points,
        mute_speaker,
        ssb_data_mode,
    )


MAX_POWER_METER = 0xFFFF
MAX_WATTS = 100.0


def _power_points(s: _Section) -> tuple[tuple[int, float], ...]:
    """``power_meter_watts = [[meter, watts], ...]``, meter values rising."""
    value = s.raw("power_meter_watts")
    if value is _MISSING:
        return ()
    usage = "must be a list of [meter value, watts] pairs, e.g. [[0, 0], [370, 10]]"
    if not isinstance(value, list) or len(value) < 2:
        s.error("power_meter_watts", f"{usage}, at least two")
        return ()
    points: list[tuple[int, float]] = []
    for pair in value:
        if not (
            isinstance(pair, list)
            and len(pair) == 2
            and isinstance(pair[0], int)
            and not isinstance(pair[0], bool)
            and 0 <= pair[0] <= MAX_POWER_METER
            and isinstance(pair[1], int | float)
            and not isinstance(pair[1], bool)
            and math.isfinite(pair[1])
            and 0 <= pair[1] <= MAX_WATTS
        ):
            s.error("power_meter_watts", f"{usage}; got {pair!r}")
            return ()
        points.append((pair[0], float(pair[1])))
    if any(b[0] <= a[0] or b[1] < a[1] for a, b in itertools.pairwise(points)):
        s.error("power_meter_watts", "meter values must rise and watts must not fall")
        return ()
    return tuple(points)


def _identity(s: _Section) -> bytes | None:
    # Optional on purpose (D32): without it the guard starts with transmit
    # blocked and logs what the radio answered, so the value can be measured.
    value = s.string("identity", required=False)
    if value is None:
        return None
    if not _IDENTITY_HEX.fullmatch(value):
        s.error(
            "identity",
            f'must be 1 to {MAX_IDENTITY_BYTES} bytes as hex (e.g. "0090"), got {value!r}',
        )
        return None
    data = bytes.fromhex(value)
    if 0xFD in data or 0xFE in data:
        s.error("identity", "cannot contain the reserved CI-V bytes FD or FE")
        return None
    return data


def _address(s: _Section, key: str) -> int | None:
    value = s.integer(key)
    if value is None:
        return None
    # 0x00 is the broadcast address; 0xE0-0xFF include the controller range
    # convention and the reserved bytes FE/FD. Only reject what CI-V forbids.
    if not 0x01 <= value <= 0xFC:
        s.error(key, f"must be a CI-V address between 0x01 and 0xFC, got 0x{value:02X}")
        return None
    return value


IQ_SAMPLE_RATES = frozenset({48_000, 96_000})
IQ_SAMPLE_BITS = frozenset({16, 24})
MIN_FFT_SIZE, MAX_FFT_SIZE = 256, 16_384
MAX_LINE_RATE = 30.0
# Cards are named, never numbered (AGENTS.md rule 8): numbering changes between boots.
_ALSA_DEVICE = re.compile(r"(?:plug)?hw:CARD=[A-Za-z][A-Za-z0-9_]{0,31},DEV=[0-9]{1,2}")


def _parse_audio(s: _Section) -> AudioConfig | None:
    kind_name = s.string("source")
    kind: AudioSourceKind | None = None
    if kind_name is not None:
        try:
            kind = AudioSourceKind(kind_name)
        except ValueError:
            s.error("source", f"must be 'synthetic' or 'alsa', got {kind_name!r}")
    device = s.string("device", required=kind is AudioSourceKind.ALSA)
    if device is not None and not _ALSA_DEVICE.fullmatch(device):
        s.error(
            "device",
            f"must name the card, e.g. 'hw:CARD=G90AUDIO,DEV=0' (AGENTS.md rule 8), got {device!r}",
        )
        device = None
    gain = (
        _number(s, "capture_gain_db", None, 0.0, MAX_CAPTURE_GAIN_DB)
        if s.has("capture_gain_db")
        else None
    )
    tx_peak = _number(s, "tx_peak_dbfs", -6.0, -40.0, 0.0)
    jitter = _number(s, "tx_jitter_ms", 200.0, 0.0, MAX_TX_JITTER_MS)
    s.check_unknown()
    if (
        kind is None
        or (kind is AudioSourceKind.ALSA and device is None)
        or tx_peak is None
        or jitter is None
    ):
        return None
    return AudioConfig(kind, device, gain, tx_peak, jitter)


def _parse_iq(s: _Section) -> IqConfig | None:
    kind_name = s.string("source")
    kind: IqSourceKind | None = None
    if kind_name is not None:
        try:
            kind = IqSourceKind(kind_name)
        except ValueError:
            s.error("source", f"must be 'synthetic', 'alsa', 'file' or 'none', got {kind_name!r}")
    device = s.string("device", required=kind is IqSourceKind.ALSA)
    if device is not None and not _ALSA_DEVICE.fullmatch(device):
        s.error(
            "device",
            "must name the card, e.g. 'hw:CARD=G90IQ,DEV=0' (card numbers change between "
            f"boots, AGENTS.md rule 8), got {device!r}",
        )
        device = None
    file = s.path("file") if kind is IqSourceKind.FILE or s.has("file") else None
    rate = s.integer("sample_rate")
    if rate is not None and rate not in IQ_SAMPLE_RATES:
        s.error("sample_rate", f"must be 48000 or 96000, got {rate}")
        rate = None
    bits = s.integer("sample_bits")
    if bits is not None and bits not in IQ_SAMPLE_BITS:
        s.error("sample_bits", f"must be 16 or 24, got {bits}")
        bits = None
    swap = s.boolean("swap_channels", default=False)
    invert_q = s.boolean("invert_q", default=False)
    dc_removal = s.boolean("dc_removal", default=True)
    balance = _number(s, "amplitude_balance", 1.0, 0.5, 2.0)
    phase = _number(s, "phase_correction_deg", 0.0, -20.0, 20.0)
    offset, offset_invalid = _center_offsets(s)
    passband, passband_invalid = _passbands(s)
    fft_size = s.integer("fft_size")
    if fft_size is not None and not (
        MIN_FFT_SIZE <= fft_size <= MAX_FFT_SIZE and fft_size & (fft_size - 1) == 0
    ):
        s.error("fft_size", f"must be a power of two from {MIN_FFT_SIZE} to {MAX_FFT_SIZE}")
        fft_size = None
    line_rate = _number(s, "line_rate", None, 1.0, MAX_LINE_RATE)
    recordings = s.path("recordings_dir")
    s.check_unknown()
    if (
        kind is None
        or (kind is IqSourceKind.ALSA and device is None)
        or (kind is IqSourceKind.FILE and file is None)
        or rate is None
        or bits is None
        or balance is None
        or phase is None
        or offset_invalid
        or passband_invalid
        or fft_size is None
        or line_rate is None
        or recordings is None
    ):
        return None
    return IqConfig(
        kind,
        device,
        file,
        rate,
        bits,
        swap,
        invert_q,
        dc_removal,
        balance,
        phase,
        offset,
        passband,
        fft_size,
        line_rate,
        recordings,
    )


# Mode names as the server reports them (remoteg90ctl.civ.Mode, "_" shown as "-").
IQ_OFFSET_MODES = frozenset({"LSB", "USB", "AM", "CW", "FM", "CW-R"})
MAX_CENTER_OFFSET_HZ = 50_000.0


def _center_offsets(s: _Section) -> tuple[dict[str, float], bool]:
    """``[iq.center_offset_hz]``: mode name -> Hz. Returns the table and whether it was invalid."""
    value = s.raw("center_offset_hz")
    if value is _MISSING:
        return {}, False
    if not isinstance(value, Mapping):
        s.error("center_offset_hz", "must be a table of mode = offset in Hz, e.g. USB = -21.5")
        return {}, True
    table: dict[str, float] = {}
    invalid = False
    for mode, hz in value.items():
        if mode not in IQ_OFFSET_MODES:
            names = ", ".join(sorted(IQ_OFFSET_MODES))
            s.error("center_offset_hz", f"unknown mode {mode!r} (one of {names})")
            invalid = True
        elif (
            isinstance(hz, bool)
            or not isinstance(hz, int | float)
            or not math.isfinite(hz)
            or abs(hz) > MAX_CENTER_OFFSET_HZ
        ):
            s.error(
                "center_offset_hz",
                f"{mode}: must be a number within ±{MAX_CENTER_OFFSET_HZ:g} Hz, got {hz!r}",
            )
            invalid = True
        else:
            table[mode] = float(hz)
    return table, invalid


def _passbands(s: _Section) -> tuple[dict[str, tuple[float, float]], bool]:
    """``[iq.passband_hz]``: mode name -> [low, high] in Hz around the dial frequency."""
    value = s.raw("passband_hz")
    if value is _MISSING:
        return {}, False
    if not isinstance(value, Mapping):
        s.error(
            "passband_hz", "must be a table of mode = [low, high] in Hz, e.g. USB = [300, 3000]"
        )
        return {}, True
    table: dict[str, tuple[float, float]] = {}
    invalid = False
    for mode, edges in value.items():
        ok = (
            mode in IQ_OFFSET_MODES
            and isinstance(edges, list)
            and len(edges) == 2
            and all(
                not isinstance(e, bool) and isinstance(e, int | float) and math.isfinite(e)
                for e in edges
            )
            and -MAX_CENTER_OFFSET_HZ <= edges[0] < edges[1] <= MAX_CENTER_OFFSET_HZ
        )
        if not ok:
            s.error(
                "passband_hz", f"{mode}: must be [low, high] in Hz with low < high, got {edges!r}"
            )
            invalid = True
        else:
            table[mode] = (float(edges[0]), float(edges[1]))
    return table, invalid


def _number(
    s: _Section, key: str, default: float | None, minimum: float, maximum: float
) -> float | None:
    """A finite number in ``[minimum, maximum]``; ``default=None`` makes it required."""
    value = s.raw(key)
    if value is _MISSING:
        if default is None:
            s.error(key, f"is required ({minimum:g} to {maximum:g})")
        return default
    if isinstance(value, bool) or not isinstance(value, int | float):
        s.error(key, "must be a number")
        return None
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        s.error(key, f"must be between {minimum:g} and {maximum:g}, got {value!r}")
        return None
    return number


def _parse_safety(s: _Section) -> SafetyConfig | None:
    allow_transmit = s.boolean("allow_transmit", default=False)
    allow_atu_tune = s.boolean("allow_atu_tune", default=False)
    renew = s.duration("lease_renew_interval_s", MAX_LEASE_RENEW_INTERVAL_S)
    timeout = s.duration("lease_timeout_s", MAX_LEASE_TIMEOUT_S)
    hard_limit = s.duration("hard_limit_s", MAX_HARD_LIMIT_S)
    if renew is not None and timeout is not None and renew >= timeout:
        s.error("lease_renew_interval_s", "must be shorter than safety.lease_timeout_s")
        renew = None
    if timeout is not None and hard_limit is not None and timeout >= hard_limit:
        s.error("lease_timeout_s", "must be shorter than safety.hard_limit_s")
        timeout = None
    duty_window, duty_max_tx = _duty_cycle(s)
    max_rf_power_w = (
        _number(s, "max_rf_power_w", None, RF_POWER_MIN_W, RF_POWER_MAX_W)
        if s.raw("max_rf_power_w") is not _MISSING
        else None
    )
    s.check_unknown()
    if (
        renew is None
        or timeout is None
        or hard_limit is None
        or duty_window is None
        or duty_max_tx is None
    ):
        return None
    return SafetyConfig(
        allow_transmit,
        renew,
        timeout,
        hard_limit,
        duty_window,
        duty_max_tx,
        allow_atu_tune,
        max_rf_power_w,
    )


def _duty_cycle(s: _Section) -> tuple[float | None, float | None]:
    """``duty_window_s`` (at least 600) and ``duty_max_tx_s`` (at most 300), D31."""
    duty_max_tx = s.duration("duty_max_tx_s", MAX_DUTY_TX_S)
    window = s.duration("duty_window_s", MAX_DUTY_WINDOW_S)
    if window is not None and window < MIN_DUTY_WINDOW_S:
        s.error(
            "duty_window_s",
            f"{window:g} s is shorter than the minimum of {MIN_DUTY_WINDOW_S:g} s "
            "(decisions.md D31: the duty cycle may be stricter, never looser)",
        )
        window = None
    return window, duty_max_tx


def _parse_guard(s: _Section) -> GuardConfig | None:
    socket_path = s.path("socket_path")
    tx_log_path = s.path("tx_log_path")
    state_dir = s.path("state_dir")
    s.check_unknown()
    if socket_path is None or tx_log_path is None or state_dir is None:
        return None
    return GuardConfig(socket_path, tx_log_path, state_dir)


def _parse_cluster(s: _Section) -> ClusterConfig:
    socket_path = s.path("socket_path") if s.has("socket_path") else None
    s.check_unknown()
    return ClusterConfig() if socket_path is None else ClusterConfig(socket_path)


def _parse_server(s: _Section) -> ServerConfig | None:
    socket_path = s.path("socket_path")
    listen = s.string_list("listen")
    for entry in listen:
        problem = check_listen_address(entry)
        if problem is not None:
            s.error("listen", problem)
    state_dir = s.path("state_dir") if s.raw("state_dir") is not _MISSING else None
    allowed_hosts = s.string_list("allowed_hosts")
    require_identity = s.boolean("require_tailnet_identity", default=True)
    for host in allowed_hosts:
        if not _HOST_NAME.fullmatch(host):
            s.error("allowed_hosts", f"{host!r}: expected a DNS name, e.g. 'host.tailnet.ts.net'")
    s.check_unknown()
    if socket_path is None or any(check_listen_address(e) for e in listen):
        return None
    if any(not _HOST_NAME.fullmatch(h) for h in allowed_hosts):
        return None
    hosts = tuple(h.lower() for h in allowed_hosts)
    if state_dir is None:
        return ServerConfig(socket_path, listen, hosts, require_tailnet_identity=require_identity)
    return ServerConfig(
        socket_path, listen, hosts, state_dir, require_tailnet_identity=require_identity
    )


# A DNS name with at least one dot (no IP addresses, no ports, no wildcards).
_HOST_NAME = re.compile(
    r"(?=.{1,253}$)(?!.*\.\.)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)


def check_listen_address(entry: str) -> str | None:
    """Return a problem description if ``entry`` violates the network barrier.

    Accepted forms: ``unix:/absolute/path``, ``127.0.0.1:PORT``, ``[::1]:PORT``.
    Host names (including ``localhost``) are rejected because their resolution
    is outside our control. There is deliberately no switch to bypass this
    until authentication is implemented (AGENTS.md rule 15).
    """
    if entry.startswith(UNIX_LISTEN_PREFIX):
        path = entry.removeprefix(UNIX_LISTEN_PREFIX)
        if not PurePosixPath(path).is_absolute():
            return f"{entry!r}: unix socket path must be absolute"
        return None
    host, sep, port = entry.rpartition(":")
    if not sep or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        return f"{entry!r}: expected 'unix:/path', '127.0.0.1:PORT' or '[::1]:PORT'"
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"{entry!r}: host must be a literal loopback address, not a name"
    if address not in LOOPBACK_ADDRESSES:
        return (
            f"{entry!r}: listening outside loopback is refused until authentication "
            "is implemented (AGENTS.md rule 15)"
        )
    return None


def _parse_logging(s: _Section) -> LoggingConfig:
    level = s.string("level", required=False, default="INFO") or "INFO"
    if level.upper() not in LOG_LEVELS:
        s.error("level", f"must be one of {', '.join(sorted(LOG_LEVELS))}, got {level!r}")
        level = "INFO"
    s.check_unknown()
    return LoggingConfig(level.upper())
