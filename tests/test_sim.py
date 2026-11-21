# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulator: CI-V replies, line history and fault injection."""

import pytest

from remoteg90ctl.civ import (
    Command,
    Frame,
    FrameSplitter,
    Garbage,
    Mode,
    frequency_from_frame,
    mode_from_frame,
    parse_frame,
    read_frequency_request,
    read_mode_request,
    set_frequency_request,
    set_mode_request,
)
from remoteg90ctl.serial_port import INACTIVE, LineState, PortLostError, SerialPort
from remoteg90ctl.sim import SimulatedRadio, SimulatedSerialPort

RADIO = 0x88
CTRL = 0xE0


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def radio() -> SimulatedRadio:
    return SimulatedRadio(RADIO, frequency_hz=7_074_000, mode=Mode.USB)


@pytest.fixture
def port(radio: SimulatedRadio, clock: FakeClock) -> SimulatedSerialPort:
    port = SimulatedSerialPort(radio, clock=clock, echo=False)
    port.set_lines(INACTIVE)
    port.open()
    return port


def exchange(port: SimulatedSerialPort, request: bytes) -> Frame:
    port.write(request)
    items = FrameSplitter().feed(port.read_available())
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, Frame)
    return item


def test_implements_serial_port_protocol(port: SimulatedSerialPort) -> None:
    typed: SerialPort = port
    assert typed.is_open


# CI-V behaviour


def test_read_frequency(port: SimulatedSerialPort) -> None:
    reply = exchange(port, read_frequency_request(RADIO, CTRL))
    assert (reply.to_address, reply.from_address) == (CTRL, RADIO)
    assert frequency_from_frame(reply) == 7_074_000


def test_set_then_read_frequency(port: SimulatedSerialPort, radio: SimulatedRadio) -> None:
    assert exchange(port, set_frequency_request(RADIO, CTRL, 14_074_000)).is_ok
    assert radio.frequency_hz == 14_074_000
    assert frequency_from_frame(exchange(port, read_frequency_request(RADIO, CTRL))) == 14_074_000


def test_set_then_read_mode(port: SimulatedSerialPort, radio: SimulatedRadio) -> None:
    assert exchange(port, set_mode_request(RADIO, CTRL, Mode.CW, 0x02)).is_ok
    assert radio.mode is Mode.CW
    reply = exchange(port, read_mode_request(RADIO, CTRL))
    assert mode_from_frame(reply) is Mode.CW
    assert reply.body == bytes([Mode.CW, 0x02])


def test_set_mode_without_filter_is_refused(
    port: SimulatedSerialPort, radio: SimulatedRadio
) -> None:
    """Like the owner's G90 (firmware 1.81): the filter byte is required."""
    assert exchange(port, set_mode_request(RADIO, CTRL, Mode.CW)).is_ng
    assert radio.mode is Mode.USB


@pytest.mark.parametrize(
    "request_hex",
    [
        "fefe88e0050040fd",  # frequency field too short
        "fefe88e005aa40071400fd",  # invalid BCD
        "fefe88e00655fd",  # unknown mode
        "fefe88e01c00fd",  # unsupported command
    ],
)
def test_invalid_requests_get_ng(port: SimulatedSerialPort, request_hex: str) -> None:
    assert exchange(port, bytes.fromhex(request_hex)).is_ng


def test_frames_for_other_addresses_are_ignored(port: SimulatedSerialPort) -> None:
    port.write(read_frequency_request(0x70, CTRL))
    assert port.read_available() == b""


def test_malformed_frames_are_ignored(port: SimulatedSerialPort) -> None:
    port.write(bytes.fromhex("0102fefefe88e003fd"))
    assert port.read_available() == b""


def test_echo(radio: SimulatedRadio) -> None:
    port = SimulatedSerialPort(radio, echo=True)
    port.set_lines(INACTIVE)
    port.open()
    request = read_frequency_request(RADIO, CTRL)
    port.write(request)
    items = FrameSplitter().feed(port.read_available())
    assert items[0] == parse_frame(request)
    assert isinstance(items[1], Frame)
    assert items[1].command == Command.READ_FREQUENCY


def test_fragmented_write(port: SimulatedSerialPort) -> None:
    request = read_frequency_request(RADIO, CTRL)
    port.write(request[:3])
    assert port.read_available() == b""
    port.write(request[3:])
    assert port.read_available() != b""


def test_broadcast_frequency(radio: SimulatedRadio) -> None:
    frame = parse_frame(radio.broadcast_frequency())
    assert frame.to_address == 0x00
    assert frequency_from_frame(frame) == 7_074_000


# Line history


def test_lines_come_up_asserted_if_not_lowered_before_open(
    radio: SimulatedRadio, clock: FakeClock
) -> None:
    # Models pyserial's default; a caller that forgets set_lines() is caught.
    port = SimulatedSerialPort(radio, clock=clock)
    port.open()
    assert port.read_lines() == LineState(dtr=True, rts=True)
    assert port.active_intervals("rts") == [(100.0, None)]


def test_lines_lowered_before_open_never_activate(port: SimulatedSerialPort) -> None:
    assert port.read_lines() == INACTIVE
    assert port.active_intervals("dtr") == []
    assert port.active_intervals("rts") == []


def test_line_history_records_intervals(port: SimulatedSerialPort, clock: FakeClock) -> None:
    clock.advance(1)
    port.set_lines(LineState(dtr=False, rts=True))
    clock.advance(2.5)
    port.set_lines(INACTIVE)
    assert port.active_intervals("rts") == [(101.0, 103.5)]
    assert port.active_intervals("dtr") == []


def test_close_drops_lines(port: SimulatedSerialPort, clock: FakeClock) -> None:
    port.set_lines(LineState(dtr=True, rts=False))
    clock.advance(1)
    port.close()
    assert port.lines == INACTIVE
    assert port.active_intervals("dtr") == [(100.0, 101.0)]


def test_unknown_line_name(port: SimulatedSerialPort) -> None:
    with pytest.raises(ValueError, match="unknown line"):
        port.active_intervals("cts")


# Fault injection


def test_disconnect_raises_and_drops_lines(port: SimulatedSerialPort, clock: FakeClock) -> None:
    port.set_lines(LineState(dtr=False, rts=True))
    clock.advance(1)
    port.disconnect()
    assert port.lines == INACTIVE
    assert port.active_intervals("rts") == [(100.0, 101.0)]
    with pytest.raises(PortLostError):
        port.write(read_frequency_request(RADIO, CTRL))
    with pytest.raises(PortLostError):
        port.read_available()
    with pytest.raises(PortLostError):
        port.read_lines()
    with pytest.raises(PortLostError):
        port.set_lines(INACTIVE)


def test_reconnect_requires_reopen(port: SimulatedSerialPort) -> None:
    port.disconnect()
    with pytest.raises(PortLostError):
        port.open()
    port.reconnect()
    assert not port.is_open
    port.set_lines(INACTIVE)
    port.open()
    assert port.read_lines() == INACTIVE


def test_unresponsive_radio(port: SimulatedSerialPort, radio: SimulatedRadio) -> None:
    port.set_unresponsive(True)
    port.write(read_frequency_request(RADIO, CTRL))
    assert port.read_available() == b""
    assert len(radio.received) == 1


def test_injected_garbage(port: SimulatedSerialPort) -> None:
    port.inject_rx(b"\x00\x13\x37")
    items = FrameSplitter().feed(port.read_available() + bytes.fromhex("fefee088fbfd"))
    assert isinstance(items[0], Garbage)
    assert isinstance(items[1], Frame)


def test_io_on_closed_port(radio: SimulatedRadio) -> None:
    port = SimulatedSerialPort(radio)
    with pytest.raises(PortLostError):
        port.write(b"\xfd")
