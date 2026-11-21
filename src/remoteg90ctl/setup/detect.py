# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding the radio's interface and the sound cards in sysfs.

Pure reading of files under the given roots (``/sys``, ``/proc``, ``/dev``),
so the tests run on a made-up tree. Nothing here opens a device: opening the
radio's serial port would raise RTS and key the radio (AGENTS.md rule 1).

The DE-19 holds a USB hub with its serial adapter (CH340) and its sound card
(AGENTS.md section 3): the sound card on the same hub as the serial adapter
is the radio's. The I/Q card is another USB card that records in stereo.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# The DE-19's serial adapter (AGENTS.md section 8, item 8b).
CH340 = ("1a86", "7523")


@dataclass(frozen=True)
class UsbDevice:
    path: Path  # the USB device's directory in sysfs, e.g. .../usb1/1-2/1-2.3/1-2.3.4
    vendor: str  # four hex digits
    product: str
    name: str  # manufacturer and product as the device reports them

    @property
    def hub(self) -> Path:
        """The hub it is plugged into (its parent in sysfs)."""
        return self.path.parent


@dataclass(frozen=True)
class SerialPort:
    by_id: Path  # /dev/serial/by-id/... (AGENTS.md rule 8)
    usb: UsbDevice | None

    @property
    def is_ch340(self) -> bool:
        return self.usb is not None and (self.usb.vendor, self.usb.product) == CH340


@dataclass(frozen=True)
class SoundCard:
    index: int
    id: str  # the ALSA name, e.g. "G90IQ" or "Device"
    usb: UsbDevice | None  # None: not a USB card
    capture_channels: int  # most channels it records, 0: none


def usb_device(path: Path) -> UsbDevice | None:
    """The USB device a sysfs path belongs to, walking up to its idVendor."""
    current = path.resolve()
    while current != current.parent:
        if (current / "idVendor").is_file() and (current / "idProduct").is_file():
            name = " ".join(
                text
                for f in ("manufacturer", "product")
                if (text := _read(current / f)) is not None
            )
            return UsbDevice(
                current,
                _read(current / "idVendor") or "",
                _read(current / "idProduct") or "",
                name,
            )
        current = current.parent
    return None


def serial_ports(dev: Path = Path("/dev"), sys: Path = Path("/sys")) -> list[SerialPort]:
    by_id = dev / "serial" / "by-id"
    if not by_id.is_dir():
        return []
    ports = []
    for link in sorted(by_id.iterdir()):
        tty = link.resolve().name  # ../../ttyUSB0 -> ttyUSB0
        ports.append(SerialPort(link, usb_device(sys / "class" / "tty" / tty / "device")))
    return ports


def sound_cards(sys: Path = Path("/sys"), proc: Path = Path("/proc")) -> list[SoundCard]:
    cards = []
    for entry in sorted((sys / "class" / "sound").glob("card*")):
        match = re.fullmatch(r"card(\d+)", entry.name)
        if match is None:
            continue
        index = int(match.group(1))
        stream = proc / "asound" / f"card{index}" / "stream0"
        cards.append(
            SoundCard(
                index,
                _read(entry / "id") or "",
                usb_device(entry / "device"),
                capture_channels(_read(stream) or ""),
            )
        )
    return cards


def capture_channels(stream0: str) -> int:
    """Most channels of any capture format in /proc/asound/cardN/stream0."""
    capture = stream0.split("Capture:", 1)
    if len(capture) < 2:
        return 0
    section = capture[1].split("Playback:", 1)[0]
    return max((int(n) for n in re.findall(r"Channels:\s*(\d+)", section)), default=0)


@dataclass(frozen=True)
class Found:
    ports: list[SerialPort]  # CH340 ports, normally one
    radio_cards: list[SoundCard]  # USB cards on the hub of the chosen port
    iq_cards: list[SoundCard]  # other USB cards recording in stereo


def classify(
    ports: list[SerialPort], cards: list[SoundCard], port: SerialPort | None = None
) -> Found:
    """Sort what was found; ``port`` is the one chosen when there are several."""
    ch340 = [p for p in ports if p.is_ch340]
    chosen = port if port is not None else (ch340[0] if len(ch340) == 1 else None)
    usb_cards = [c for c in cards if c.usb is not None]
    radio = [
        c
        for c in usb_cards
        if chosen is not None
        and chosen.usb is not None
        and c.usb is not None
        and c.usb.hub == chosen.usb.hub
    ]
    iq = [c for c in usb_cards if c not in radio and c.capture_channels >= 2]
    return Found(ch340, radio, iq)


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None
