# SPDX-License-Identifier: GPL-3.0-or-later
"""pyserial backend, exercised on a pseudo-terminal (never on a real radio).

A pty supports I/O but not modem lines, so these tests also show that a device
whose lines cannot be read back is never accepted for transmission.
"""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from guard_helpers import FakeClock, hardware_config
from remoteg90ctl.guard.core import GuardCore, PortState
from remoteg90ctl.guard.port import PySerialPort, check_device
from remoteg90ctl.guard.protocol import Request
from remoteg90ctl.serial_port import PortLostError


@pytest.fixture
def pty() -> Iterator[tuple[int, str]]:
    master, slave = os.openpty()
    path = os.ttyname(slave)
    yield master, path
    os.close(master)
    os.close(slave)


def test_check_device(tmp_path: Path, pty: tuple[int, str]) -> None:
    assert check_device(pty[1]) is None
    assert check_device(str(tmp_path / "missing")) is not None
    regular = tmp_path / "file"
    regular.write_text("")
    assert "not a character device" in (check_device(str(regular)) or "")


def test_open_missing_device(tmp_path: Path) -> None:
    with pytest.raises(PortLostError, match="does not exist"):
        PySerialPort(str(tmp_path / "missing"), 19200).open()


def test_io_on_pty(pty: tuple[int, str]) -> None:
    master, path = pty
    port = PySerialPort(path, 19200)
    port.open()
    try:
        assert port.is_open
        port.write(b"\xfe\xfe\x88\xe0\x03\xfd")
        assert os.read(master, 16) == b"\xfe\xfe\x88\xe0\x03\xfd"
        os.write(master, b"\xfe\xfe\xe0\x88\xfb\xfd")
        time.sleep(0.05)
        assert port.read_available() == b"\xfe\xfe\xe0\x88\xfb\xfd"
    finally:
        port.close()
    assert not port.is_open


def test_exclusive_open(pty: tuple[int, str]) -> None:
    first = PySerialPort(pty[1], 19200)
    first.open()
    try:
        with pytest.raises(PortLostError):
            PySerialPort(pty[1], 19200).open()
    finally:
        first.close()


def test_unreadable_lines_block_transmission(tmp_path: Path, pty: tuple[int, str]) -> None:
    config = hardware_config(tmp_path, allow_transmit=True)
    core = GuardCore(config, lambda: PySerialPort(pty[1], 19200), clock=FakeClock())
    core.start()
    assert core.port_state is PortState.UNAVAILABLE
    response = core.handle(Request(1, "tx_request", {"source": "t", "frequency_hz": 1}))
    assert response["error"] == "tx_blocked"


@pytest.mark.skipif(os.geteuid() == 0, reason="root is exempt from TIOCEXCL")
def test_tiocexcl_blocks_other_opens(pty: tuple[int, str]) -> None:
    port = PySerialPort(pty[1], 19200)
    port.open()
    try:
        # A plain open (no flock), as another program would do it.
        with pytest.raises(OSError, match="Device or resource busy"):
            os.close(os.open(pty[1], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK))
    finally:
        port.close()
