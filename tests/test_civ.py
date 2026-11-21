# SPDX-License-Identifier: GPL-3.0-or-later
"""CI-V codec: frames, BCD frequencies, modes and stream splitting."""

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remoteg90ctl.civ import (
    MAX_FRAME_LENGTH,
    BcdError,
    CivError,
    CivParseError,
    Command,
    Frame,
    FrameSplitter,
    Garbage,
    Mode,
    build_frame,
    decode_frequency,
    encode_frequency,
    frequency_from_frame,
    is_echo,
    mode_and_filter_from_frame,
    mode_from_frame,
    parse_frame,
    read_frequency_request,
    read_mode_request,
    set_frequency_request,
    set_mode_request,
)

RADIO = 0x88
CTRL = 0xE0


# BCD


@pytest.mark.parametrize(
    ("hz", "encoded"),
    [
        (14_074_000, "0040071400"),
        (7_074_000, "0040070700"),
        (0, "0000000000"),
        (9_999_999_999, "9999999999"),
        (1_234_567_890, "9078563412"),
    ],
)
def test_encode_frequency(hz: int, encoded: str) -> None:
    assert encode_frequency(hz).hex() == encoded
    assert decode_frequency(bytes.fromhex(encoded)) == hz


@pytest.mark.parametrize("hz", [-1, 10_000_000_000])
def test_encode_frequency_out_of_range(hz: int) -> None:
    with pytest.raises(BcdError):
        encode_frequency(hz)


@pytest.mark.parametrize("data", ["", "0a", "a0", "00ff000000"])
def test_decode_invalid_bcd(data: str) -> None:
    with pytest.raises(BcdError):
        decode_frequency(bytes.fromhex(data))


@given(st.integers(min_value=0, max_value=9_999_999_999))
def test_bcd_round_trip(hz: int) -> None:
    assert decode_frequency(encode_frequency(hz)) == hz


@given(st.binary(max_size=8))
def test_decode_arbitrary_bytes_only_raises_bcd_error(data: bytes) -> None:
    with contextlib.suppress(BcdError):
        decode_frequency(data)


# Frames


def test_build_read_frequency() -> None:
    assert read_frequency_request(RADIO, CTRL).hex(" ") == "fe fe 88 e0 03 fd"


def test_build_set_frequency() -> None:
    assert set_frequency_request(RADIO, CTRL, 14_074_000).hex(" ") == (
        "fe fe 88 e0 05 00 40 07 14 00 fd"
    )


def test_build_mode_requests() -> None:
    assert read_mode_request(RADIO, CTRL).hex(" ") == "fe fe 88 e0 04 fd"
    assert set_mode_request(RADIO, CTRL, Mode.LSB).hex(" ") == "fe fe 88 e0 06 00 fd"
    assert set_mode_request(RADIO, CTRL, Mode.LSB, 0x01).hex(" ") == "fe fe 88 e0 06 00 01 fd"


def test_build_frame_with_subcommand() -> None:
    assert build_frame(RADIO, CTRL, 0x1A, 0x05, b"\x01").hex(" ") == "fe fe 88 e0 1a 05 01 fd"


@pytest.mark.parametrize(
    ("to", "frm", "cmd", "sub", "data"),
    [
        (0xFE, CTRL, 0x03, None, b""),
        (RADIO, 0xFD, 0x03, None, b""),
        (RADIO, CTRL, 0xFE, None, b""),
        (RADIO, CTRL, 0x05, 0xFD, b""),
        (RADIO, CTRL, 0x05, None, b"\x00\xfd"),
        (0x100, CTRL, 0x03, None, b""),
        (RADIO, CTRL, 0x05, None, bytes(MAX_FRAME_LENGTH)),
    ],
)
def test_build_frame_rejects_invalid(
    to: int, frm: int, cmd: int, sub: int | None, data: bytes
) -> None:
    with pytest.raises(CivParseError):
        build_frame(to, frm, cmd, sub, data)


def test_parse_frame() -> None:
    frame = parse_frame(bytes.fromhex("fefee08803 0040071400 fd"))
    assert frame == Frame(CTRL, RADIO, Command.READ_FREQUENCY, bytes.fromhex("0040071400"))
    assert frequency_from_frame(frame) == 14_074_000


@pytest.mark.parametrize("field", ["00400714", "004007140000"])
def test_frequency_from_frame_requires_five_bytes(field: str) -> None:
    """A field that lost or gained a byte must not decode to some other frequency."""
    with pytest.raises(CivParseError):
        frequency_from_frame(Frame(CTRL, RADIO, Command.READ_FREQUENCY, bytes.fromhex(field)))


def test_parse_ok_and_ng() -> None:
    assert parse_frame(bytes.fromhex("fefee088fbfd")).is_ok
    assert parse_frame(bytes.fromhex("fefee088fafd")).is_ng
    assert not parse_frame(bytes.fromhex("fefee088fafd")).is_ok


@pytest.mark.parametrize(
    "data",
    [
        "",  # empty
        "fefe88e0fd",  # too short (no command)
        "fe88e003fd",  # single preamble byte
        "fefefe88e003fd",  # triple preamble
        "fefe88e003",  # no end of message
        "fefe88e003fd00",  # trailing byte
        "00fefe88e003fd",  # leading byte
        "fefe88e003fdfefe88e004fd",  # two frames glued
        "fefe88e005fe00fd",  # FE inside
        "fefe88e005fd00fd",  # FD inside
        "fefe88e005" + "00" * MAX_FRAME_LENGTH + "fd",  # too long
    ],
)
def test_parse_rejects_malformed(data: str) -> None:
    with pytest.raises(CivParseError):
        parse_frame(bytes.fromhex(data))


@given(st.binary(max_size=MAX_FRAME_LENGTH + 8))
def test_parse_arbitrary_bytes_only_raises_parse_error(data: bytes) -> None:
    try:
        frame = parse_frame(data)
    except CivParseError:
        return
    assert frame.to_bytes() == data


frames = st.builds(
    Frame,
    to_address=st.integers(0, 0xFC),
    from_address=st.integers(0, 0xFC),
    command=st.integers(0, 0xFC),
    body=st.binary(max_size=MAX_FRAME_LENGTH - 6).map(
        lambda b: bytes(x for x in b if x not in (0xFE, 0xFD))
    ),
)


@given(frames)
def test_frame_round_trip(frame: Frame) -> None:
    assert parse_frame(frame.to_bytes()) == frame


def test_is_echo() -> None:
    sent = read_frequency_request(RADIO, CTRL)
    assert is_echo(sent, sent)
    assert not is_echo(read_mode_request(RADIO, CTRL), sent)


# Modes


def test_mode_from_frame_ignores_filter_byte() -> None:
    assert mode_from_frame(Frame(CTRL, RADIO, Command.READ_MODE, b"\x01\x02")) is Mode.USB
    assert mode_from_frame(Frame(0x00, RADIO, Command.TRANSCEIVE_MODE, b"\x03")) is Mode.CW


def test_mode_and_filter_from_frame() -> None:
    frame = Frame(CTRL, RADIO, Command.READ_MODE, b"\x01\x02")
    assert mode_and_filter_from_frame(frame) == (Mode.USB, 0x02)
    frame = Frame(0x00, RADIO, Command.TRANSCEIVE_MODE, b"\x03")
    assert mode_and_filter_from_frame(frame) == (Mode.CW, None)


@pytest.mark.parametrize("body", [b"", b"\x01\x01\x01", b"\x55"])
def test_mode_from_frame_rejects_invalid(body: bytes) -> None:
    with pytest.raises(CivParseError):
        mode_from_frame(Frame(CTRL, RADIO, Command.READ_MODE, body))


def test_value_extractors_check_command() -> None:
    with pytest.raises(CivParseError):
        frequency_from_frame(Frame(CTRL, RADIO, Command.READ_MODE, b"\x01"))
    with pytest.raises(CivParseError):
        mode_from_frame(Frame(CTRL, RADIO, Command.READ_FREQUENCY, b"\x01"))


# Stream splitting


def test_splitter_handles_fragmented_frames() -> None:
    splitter = FrameSplitter()
    data = bytes.fromhex("fefee08803 0040071400 fd fefee088fbfd")
    items: list[Frame | Garbage] = []
    for byte in data:
        items.extend(splitter.feed(bytes([byte])))
    assert items == [
        Frame(CTRL, RADIO, 0x03, bytes.fromhex("0040071400")),
        Frame(CTRL, RADIO, 0xFB),
    ]
    assert splitter.pending == b""


def test_splitter_reports_garbage_before_frame() -> None:
    items = FrameSplitter().feed(bytes.fromhex("0102 fefee088fbfd"))
    assert items[0] == Garbage(b"\x01\x02", "bytes before preamble")
    assert items[1] == Frame(CTRL, RADIO, 0xFB)


def test_splitter_reports_unterminated_noise() -> None:
    splitter = FrameSplitter()
    items = splitter.feed(bytes(MAX_FRAME_LENGTH))
    assert len(items) == 1
    assert isinstance(items[0], Garbage)
    assert splitter.pending == b""


def test_splitter_keeps_frame_start_after_noise() -> None:
    splitter = FrameSplitter()
    items = splitter.feed(bytes(MAX_FRAME_LENGTH - 2) + bytes.fromhex("fefee088"))
    assert [type(i) for i in items] == [Garbage]
    assert splitter.feed(bytes.fromhex("fbfd")) == [Frame(CTRL, RADIO, 0xFB)]


def test_splitter_rejects_malformed_candidate() -> None:
    items = FrameSplitter().feed(bytes.fromhex("fefefe88e003fd"))
    assert len(items) == 1
    assert isinstance(items[0], Garbage)


@given(st.lists(st.binary(max_size=20), max_size=20))
def test_splitter_accounts_for_every_byte(chunks: list[bytes]) -> None:
    splitter = FrameSplitter()
    out = bytearray()
    for chunk in chunks:
        for item in splitter.feed(chunk):
            out.extend(item.to_bytes() if isinstance(item, Frame) else item.data)
    assert bytes(out) + splitter.pending == b"".join(chunks)


@given(st.binary(max_size=200))
def test_splitter_never_raises(data: bytes) -> None:
    try:
        FrameSplitter().feed(data)
    except CivError:  # pragma: no cover - would be a bug
        pytest.fail("splitter raised on stream input")
