# SPDX-License-Identifier: GPL-3.0-or-later
"""Frame smuggling: only exactly one valid, allowed frame may reach the port."""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from guard_helpers import CTRL, IDENTITY_QUERY, RADIO, Harness, make_config
from remoteg90ctl.civ import (
    Mode,
    parse_frame,
    read_frequency_request,
    read_mode_request,
    read_power_meter_request,
    read_rf_power_request,
    read_swr_meter_request,
    set_frequency_request,
    set_mode_request,
)
from remoteg90ctl.guard.framecheck import (
    ALLOWLIST,
    COMMAND_NOT_ALLOWED,
    FRAME_INVALID,
    AllowedCommand,
    FrameRejectedError,
    check_frame,
)

READ_FREQ = read_frequency_request(RADIO, CTRL).hex()
ALLOWED_FRAMES = [
    READ_FREQ,
    read_mode_request(RADIO, CTRL).hex(),
    set_frequency_request(RADIO, CTRL, 14_074_000).hex(),
    set_mode_request(RADIO, CTRL, Mode.USB).hex(),
    read_power_meter_request(RADIO, CTRL).hex(),
    read_rf_power_request(RADIO, CTRL).hex(),
    read_swr_meter_request(RADIO, CTRL).hex(),
]


def check(message: object) -> None:
    check_frame(message, radio_address=RADIO, controller_address=CTRL)


@pytest.mark.parametrize("frame", ALLOWED_FRAMES)
def test_allowed_frames(frame: str) -> None:
    check(frame)
    check(frame.upper())


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (READ_FREQ + READ_FREQ, FRAME_INVALID),  # two allowed frames glued
        (READ_FREQ + "fefe88e01c0001fd", FRAME_INVALID),  # allowed + PTT
        ("00" + READ_FREQ, FRAME_INVALID),  # byte before
        (READ_FREQ + "00", FRAME_INVALID),  # byte after
        ("fe" + READ_FREQ, FRAME_INVALID),  # extra preamble
        ("fefe88e005fe40071400fd", FRAME_INVALID),  # FE inside
        ("fefe88e005fd40071400fd", FRAME_INVALID),  # FD inside
        ("fefe88e003", FRAME_INVALID),  # truncated
        ("fefe70e003fd", FRAME_INVALID),  # wrong destination
        ("fefe00e003fd", FRAME_INVALID),  # broadcast destination
        ("fefe88e103fd", FRAME_INVALID),  # wrong source
        ("fefe88e003fdz", FRAME_INVALID),  # not hex
        ("fefe88e003f", FRAME_INVALID),  # odd length
        ("fe fe 88 e0 03 fd", FRAME_INVALID),  # whitespace (bytes.fromhex would accept)
        ("", FRAME_INVALID),
        (None, FRAME_INVALID),
        (b"\xfe\xfe\x88\xe0\x03\xfd", FRAME_INVALID),  # not a string
        ("fefe88e0" + "05" + "00" * 70 + "fd", FRAME_INVALID),  # too long
        ("fefe88e01c0001fd", COMMAND_NOT_ALLOWED),  # Icom PTT on
        ("fefe88e01a0501fd", COMMAND_NOT_ALLOWED),
        ("fefe88e00300fd", COMMAND_NOT_ALLOWED),  # read with data
        ("fefe88e0050040fd", COMMAND_NOT_ALLOWED),  # frequency too short
        ("fefe88e00500400714fd", COMMAND_NOT_ALLOWED),  # 4-byte frequency (no source for it)
        ("fefe88e005004007140000fd", COMMAND_NOT_ALLOWED),  # 6-byte frequency
        ("fefe88e006fd", COMMAND_NOT_ALLOWED),  # mode without data
        ("fefe88e006010101fd", COMMAND_NOT_ALLOWED),  # mode with too much data
        # Menu (D37): no item is allowed; the G90 answers NG to the RX filter item.
        ("fefe88e01a050010fd", COMMAND_NOT_ALLOWED),  # read the RX filter
        ("fefe88e01a0500100324fd", COMMAND_NOT_ALLOWED),  # write the RX filter
        ("fefe88e01a05001003fd", COMMAND_NOT_ALLOWED),  # one value byte
        ("fefe88e01a050010032400fd", COMMAND_NOT_ALLOWED),  # three value bytes
        ("fefe88e01a0500110324fd", COMMAND_NOT_ALLOWED),  # another item with a value
        ("fefe88e01a050011fd", COMMAND_NOT_ALLOWED),  # another item
        ("fefe88e01a051000fd", COMMAND_NOT_ALLOWED),  # item bytes swapped
        ("fefe88e01a0500fd", COMMAND_NOT_ALLOWED),  # truncated item number
        ("fefe88e01a05fd", COMMAND_NOT_ALLOWED),  # no item
        ("fefe88e01a030010fd", COMMAND_NOT_ALLOWED),  # another sub-command
        ("fefe88e0110c00fd", COMMAND_NOT_ALLOWED),  # attenuator with two data bytes
        ("fefe88e016fd", COMMAND_NOT_ALLOWED),  # functions without a sub-command
        ("fefe88e01644fd", COMMAND_NOT_ALLOWED),  # compressor, not decided yet
        ("fefe88e0162201fffd", COMMAND_NOT_ALLOWED),  # noise blanker with two data bytes
        ("fefe88e0141201fd", COMMAND_NOT_ALLOWED),  # noise blanker level with one data byte
        ("fefe88e016120300fd", COMMAND_NOT_ALLOWED),  # AGC with two data bytes
        ("fefe88e016020100fd", COMMAND_NOT_ALLOWED),  # preamp with two data bytes
    ],
)
def test_rejected_messages(message: object, code: str) -> None:
    with pytest.raises(FrameRejectedError) as info:
        check(message)
    assert info.value.code == code


def test_allowlist_is_exactly_what_was_decided() -> None:
    """Every entry is a decision (AGENTS.md rule 3, D37); adding one must change this test."""
    entries = sorted(
        (
            (e.command, e.subcommand, e.item, e.body_lengths, e.changes_radio_state)
            for e in ALLOWLIST
        ),
        key=lambda e: (e[0], e[1] or 0, e[2] or b""),
    )
    assert entries == [
        (0x03, None, None, range(0, 1), False),
        (0x04, None, None, range(0, 1), False),
        (0x05, None, None, range(5, 6), True),
        (0x06, None, None, range(1, 3), True),
        (0x07, 0x00, None, range(1, 2), True),  # select VFO A (stage 5)
        (0x07, 0x01, None, range(1, 2), True),  # select VFO B (stage 5)
        (0x0F, None, None, range(0, 1), False),  # split, read (stage 5)
        (0x0F, 0x00, None, range(1, 2), True),  # split off (stage 5)
        (0x0F, 0x01, None, range(1, 2), True),  # split on (stage 5)
        (0x11, None, None, range(0, 1), False),  # attenuator, read (stage 5)
        (0x11, None, None, range(1, 2), True),  # attenuator, set (stage 5)
        (0x14, 0x01, None, range(1, 2), False),  # speaker volume, read (2026-11-04)
        (0x14, 0x01, None, range(3, 4), True),  # speaker volume, write (2026-11-04)
        (0x14, 0x02, None, range(1, 2), False),  # RF gain, read (stage 5)
        (0x14, 0x02, None, range(3, 4), True),  # RF gain, set (stage 5)
        (0x14, 0x0A, None, range(1, 2), False),  # RF power setting, read (2026-11-03)
        (0x14, 0x0A, None, range(3, 4), True),  # RF power setting, set (D44)
        (0x14, 0x12, None, range(1, 2), False),  # noise blanker level, read (stage 5)
        (0x14, 0x12, None, range(3, 4), True),  # noise blanker level, set (stage 5)
        (0x15, 0x02, None, range(1, 2), False),  # S-meter, read only (stage 5)
        (0x15, 0x11, None, range(1, 2), False),  # power meter, read only (2026-11-03)
        (0x15, 0x12, None, range(1, 2), False),  # SWR meter, read only (2026-11-03)
        (0x15, 0x13, None, range(1, 2), False),  # ALC meter, read only (stage 5)
        (0x16, 0x02, None, range(1, 2), False),  # preamplifier, read (stage 5)
        (0x16, 0x02, None, range(2, 3), True),  # preamplifier, set (stage 5)
        (0x16, 0x12, None, range(1, 2), False),  # AGC, read (stage 5)
        (0x16, 0x12, None, range(2, 3), True),  # AGC, set (stage 5)
        (0x16, 0x22, None, range(1, 2), False),  # noise blanker, read (stage 5)
        (0x16, 0x22, None, range(2, 3), True),  # noise blanker, set (stage 5)
        (0x1C, 0x01, None, range(1, 2), False),  # ATU state, read (D43)
        (0x1C, 0x01, b"\x00", range(2, 3), True),  # ATU off (D43)
        (0x1C, 0x01, b"\x01", range(2, 3), True),  # ATU on (D43)
        (0x25, 0x00, None, range(1, 2), False),  # selected VFO frequency, read (stage 5)
        (0x25, 0x01, None, range(1, 2), False),  # unselected VFO frequency, read (stage 5)
        (0x26, 0x00, None, range(1, 2), False),  # mode with data flag, read
        (0x26, 0x00, None, range(4, 5), True),  # mode with data flag, set
        (0x26, 0x01, None, range(1, 2), False),  # other VFO's mode, read (stage 5)
    ]


@pytest.mark.parametrize(
    "body",
    # with data, voltage (always 255 on the G90), no sub-command
    ["11" + "0120", "12" + "0001", "02" + "0001", "13" + "0001", "15", ""],
)
def test_only_the_decided_meter_reads_are_allowed(body: str) -> None:
    with pytest.raises(FrameRejectedError) as info:
        check(f"fefe{RADIO:02x}{CTRL:02x}15{body}fd")
    assert info.value.code == COMMAND_NOT_ALLOWED


@pytest.mark.parametrize("body", ["0102", "0103", "010100", "0000", "0001", "00", "02"])
def test_tuning_and_ptt_never_pass_the_allowlist(body: str) -> None:
    """1C 01 02 transmits (only the guard's atu_tune sends it); 1C 00 is PTT (D26, D43)."""
    with pytest.raises(FrameRejectedError) as info:
        check(f"fefe{RADIO:02x}{CTRL:02x}1c{body}fd")
    assert info.value.code == COMMAND_NOT_ALLOWED


@pytest.mark.parametrize("body", ["01", "0100", "0101"])
def test_tuner_read_and_switch_pass(body: str) -> None:
    check(f"fefe{RADIO:02x}{CTRL:02x}1c{body}fd")


@pytest.mark.parametrize("body", ["0a", "0a0128", "0a0255"])
def test_rf_power_may_be_read_and_set(body: str) -> None:
    """14 0A alone reads; with exactly 2 data bytes it sets (D44). Values are not read."""
    check(f"fefe{RADIO:02x}{CTRL:02x}14{body}fd")


@pytest.mark.parametrize(
    "body", ["0a00", "0a000000", "0100", "01000000", "0200", "02000000", "030128", "0b0128"]
)
def test_other_rf_power_frames_and_levels_are_refused(body: str) -> None:
    """One data byte or three, and every 14 level not decided (squelch, mic gain…)."""
    with pytest.raises(FrameRejectedError) as info:
        check(f"fefe{RADIO:02x}{CTRL:02x}14{body}fd")
    assert info.value.code == COMMAND_NOT_ALLOWED


def test_menu_entries_match_their_item_only() -> None:
    """The D37 mechanism: an entry with an item matches that item and nothing else."""
    entry = AllowedCommand(0x1A, 0x05, range(3, 4), changes_radio_state=False, item=b"\x00\x10")
    assert entry.matches(parse_frame(bytes.fromhex("fefe88e01a050010fd")))
    assert not entry.matches(parse_frame(bytes.fromhex("fefe88e01a050011fd")))
    assert not entry.matches(parse_frame(bytes.fromhex("fefe88e01a051000fd")))
    assert not entry.matches(parse_frame(bytes.fromhex("fefe88e01a0500fd")))
    assert not entry.matches(parse_frame(bytes.fromhex("fefe88e01a030010fd")))


def _passes(message: str) -> bool:
    try:
        check(message)
    except FrameRejectedError:
        return False
    return True


def _is_one_allowed_frame(data: bytes) -> bool:
    frame = parse_frame(data)  # raises if not exactly one frame
    if frame.to_bytes() != data or frame.to_address != RADIO or frame.from_address != CTRL:
        return False
    return any(e.matches(frame) and len(frame.body) in e.body_lengths for e in ALLOWLIST)


allowed_prefixes = st.sampled_from([bytes.fromhex(f) for f in ALLOWED_FRAMES])
messages = st.one_of(
    st.binary(max_size=80),
    st.builds(lambda a, b: a + b, allowed_prefixes, st.binary(max_size=20)),
    st.builds(lambda a, b: a + b, st.binary(max_size=20), allowed_prefixes),
    st.builds(lambda a, b: a + b, allowed_prefixes, allowed_prefixes),
    st.builds(
        lambda body: bytes([0xFE, 0xFE, RADIO, CTRL]) + body + b"\xfd", st.binary(max_size=12)
    ),
)


@given(messages)
def test_only_single_allowed_frames_pass(data: bytes) -> None:
    if _passes(data.hex()):
        assert _is_one_allowed_frame(data)


@given(st.text(max_size=60))
def test_arbitrary_text_only_raises_frame_rejected(text: str) -> None:
    if _passes(text):
        assert _is_one_allowed_frame(bytes.fromhex(text))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
@given(messages)
def test_nothing_else_reaches_the_port(tmp_path: Path, data: bytes) -> None:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    harness.send("civ", frame=data.hex())
    # The guard's own identity query is written on open; nothing else may pass.
    assert harness.port.written[0] == IDENTITY_QUERY
    for written in harness.port.written[1:]:
        assert _is_one_allowed_frame(written)
    assert not harness.ptt_active


@pytest.mark.parametrize("body", ["00", "000101", "00010101", "01", "01010101", "02", "0001010100"])
def test_data_mode_frames(body: str) -> None:
    """26 00 read, or set with exactly mode, data flag and filter; 26 01 read only."""
    message = f"fefe{RADIO:02x}{CTRL:02x}26{body}fd"
    if body in ("00", "00010101", "01"):
        check(message)
    else:
        with pytest.raises(FrameRejectedError):
            check(message)
