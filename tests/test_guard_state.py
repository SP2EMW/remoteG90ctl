# SPDX-License-Identifier: GPL-3.0-or-later
"""Restarts never restore transmission and never clear the lockout (AGENTS.md rule 13)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from guard_helpers import Harness, make_config
from remoteg90ctl.guard.core import GuardCore
from remoteg90ctl.guard.state import (
    HARD_LIMIT,
    LOCKOUT_FILE,
    RUNNING_FILE,
    STATE_UNREADABLE,
    UNCLEAN_SHUTDOWN_DURING_TX,
    Lockout,
    LockoutStore,
    RunMarker,
)
from remoteg90ctl.guard.txlog import TxLog


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    harness = Harness(make_config(tmp_path))
    harness.core.start()
    return harness


def crash_and_restart(h: Harness) -> None:
    """Abandon the core without any cleanup, as a SIGKILL would; start a new one."""
    h.port.close()  # the kernel closes the descriptor; HUPCL drops the lines
    h.core = h.new_core()
    h.core.start()


def test_restart_after_hard_limit_stays_locked(h: Harness) -> None:
    h.start_tx()
    h.clock.advance(180)
    h.core.poll()
    assert h.core.lockout is not None
    crash_and_restart(h)
    assert h.core.lockout is not None
    assert h.core.lockout.reason == HARD_LIMIT
    assert h.tx_request()["error"] == "tx_locked"
    assert h.send("tx_unlock")["ok"]
    assert h.tx_request()["ok"]


def test_clean_restart_after_hard_limit_stays_locked(h: Harness) -> None:
    h.start_tx()
    h.clock.advance(180)
    h.core.poll()
    h.core.shutdown(clean=True)
    h.core = h.new_core()
    h.core.start()
    assert h.tx_request()["error"] == "tx_locked"


def test_unlock_survives_restart(h: Harness) -> None:
    h.start_tx()
    h.clock.advance(180)
    h.core.poll()
    h.send("tx_unlock")
    crash_and_restart(h)
    assert h.core.lockout is None


def test_crash_during_tx(h: Harness) -> None:
    h.start_tx()
    crash_and_restart(h)
    assert not h.core.tx_active
    assert not h.ptt_active
    # D24.8: a crash during transmission locks TX until tx_unlock.
    assert h.core.lockout is not None
    assert h.core.lockout.reason == UNCLEAN_SHUTDOWN_DURING_TX
    assert h.tx_request()["error"] == "tx_locked"
    starts = h.tx_events("guard_start")
    assert starts[-1]["unclean_previous_shutdown"] is True
    assert starts[-1]["tx_active_at_shutdown"] is True


def test_no_continuation_of_previous_lease(h: Harness) -> None:
    lease = h.start_tx()
    crash_and_restart(h)
    assert h.send("tx_renew", lease=lease)["error"] == "unknown_lease"
    assert h.send("tx_release", lease=lease)["error"] == "unknown_lease"
    assert not h.ptt_active


def test_crash_without_tx_is_logged_but_not_locked(h: Harness) -> None:
    crash_and_restart(h)
    assert h.core.lockout is None
    assert h.tx_events("guard_start")[-1]["unclean_previous_shutdown"] is True
    assert h.tx_request()["ok"]


def test_clean_restart_is_not_unclean(h: Harness) -> None:
    h.core.shutdown(clean=True)
    h.core = h.new_core()
    h.core.start()
    assert h.tx_events("guard_start")[-1]["unclean_previous_shutdown"] is False


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json",
        b"[]",
        b'{"version": 2, "locked": false}',
        b'{"version": 1}',
        b'{"version": 1, "locked": "no"}',
        b'{"version": 1, "locked": true, "reason": 1, "since": "x"}',
    ],
)
def test_corrupt_lockout_file_starts_locked(tmp_path: Path, content: bytes) -> None:
    config = make_config(tmp_path)
    (config.guard.state_dir / LOCKOUT_FILE).write_bytes(content)
    harness = Harness(config)
    harness.core.start()
    assert harness.core.lockout is not None
    assert harness.core.lockout.reason == STATE_UNREADABLE
    assert harness.tx_request()["error"] == "tx_locked"
    assert harness.send("tx_unlock")["ok"]
    assert harness.tx_request()["ok"]


def test_missing_state_dir_starts_locked(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.guard.state_dir.rmdir()
    harness = Harness(config)
    harness.core.start()
    assert harness.core.lockout is not None
    assert harness.tx_request()["error"] == "tx_locked"


@pytest.mark.parametrize("content", [b"", b"{", b'{"tx_active": "yes"}'])
def test_corrupt_run_marker_assumes_tx_was_active(tmp_path: Path, content: bytes) -> None:
    config = make_config(tmp_path)
    (config.guard.state_dir / RUNNING_FILE).write_bytes(content)
    harness = Harness(config)
    harness.core.start()
    assert harness.core.lockout is not None
    assert harness.core.lockout.reason == UNCLEAN_SHUTDOWN_DURING_TX


def test_lockout_store_round_trip(tmp_path: Path) -> None:
    store = LockoutStore(tmp_path)
    assert store.load() is None
    store.save(Lockout(HARD_LIMIT, "2026-10-31T00:00:00+00:00"))
    assert store.load() == Lockout(HARD_LIMIT, "2026-10-31T00:00:00+00:00")
    store.save(None)
    assert store.load() is None
    assert json.loads((tmp_path / LOCKOUT_FILE).read_text())["locked"] is False
    assert not list(tmp_path.glob(".*.tmp"))


def test_run_marker(tmp_path: Path) -> None:
    marker = RunMarker(tmp_path)
    assert not marker.previous_run().unclean
    marker.write(tx_active=True)
    previous = marker.previous_run()
    assert previous.unclean
    assert previous.tx_active
    marker.remove()
    assert not marker.previous_run().unclean


def test_unsaved_hard_limit_lockout_survives_clean_restart(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.start_tx()

    def fail(self: LockoutStore, lockout: Lockout | None) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(LockoutStore, "save", fail)
    h.clock.advance(180)
    h.core.poll()
    assert not h.ptt_active
    assert h.core.lockout is not None
    h.core.shutdown(clean=True)
    # The marker was kept and carries the lockout.
    assert (h.config.guard.state_dir / RUNNING_FILE).exists()
    monkeypatch.undo()
    h.core = h.new_core()
    h.core.start()
    assert h.core.lockout is not None
    assert h.core.lockout.reason == HARD_LIMIT
    assert h.tx_request()["error"] == "tx_locked"
    # Now saved properly: unlocking works and is durable.
    assert h.send("tx_unlock")["ok"]
    h.core.shutdown(clean=True)
    h.core = h.new_core()
    h.core.start()
    assert h.core.lockout is None


def test_marker_pending_lockout_field(tmp_path: Path) -> None:
    marker = RunMarker(tmp_path)
    marker.write(tx_active=False, pending_lockout=HARD_LIMIT)
    previous = marker.previous_run()
    assert previous.unclean
    assert previous.pending_lockout == HARD_LIMIT


def test_crash_during_tx_closes_the_tx_log_record(h: Harness) -> None:
    lease = h.start_tx()
    crash_and_restart(h)
    starts = h.tx_events("tx_start")
    ends = h.tx_events("tx_end")
    assert len(starts) == len(ends) == 1
    end = ends[0]
    assert end["lease"] == lease
    assert end["reason"] == "unclean_shutdown"
    assert end["duration_s"] is None
    assert end["source"] == "test"
    assert end["started"] == starts[0]["ts"]
    # The closing record comes before the new start record.
    events = [r["event"] for r in h.tx_log()]
    assert events.index("tx_end") < len(events) - 1 - events[::-1].index("guard_start")


def test_marker_records_lease(tmp_path: Path) -> None:
    marker = RunMarker(tmp_path)
    marker.write(tx_active=True, lease={"lease": "abc", "source": "s"})
    assert marker.previous_run().lease == {"lease": "abc", "source": "s"}
    (tmp_path / RUNNING_FILE).write_text('{"tx_active": true, "lease": "not a dict"}')
    previous = marker.previous_run()
    assert previous.tx_active
    assert previous.lease is None


class TickingWallClock:
    """Wall clock that moves 1 ms on every reading, like a real one between calls."""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 31, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(milliseconds=1)
        return self._now


@pytest.mark.parametrize("crash", [False, True])
def test_tx_end_started_equals_tx_start_time(tmp_path: Path, crash: bool) -> None:
    harness = Harness(make_config(tmp_path))
    txlog = TxLog(harness.config.guard.tx_log_path, TickingWallClock())
    harness.core = GuardCore(harness.config, lambda: harness.port, clock=harness.clock, txlog=txlog)
    harness.core.start()
    lease = harness.start_tx()
    if crash:
        harness.port.close()
        harness.core = GuardCore(
            harness.config, lambda: harness.port, clock=harness.clock, txlog=txlog
        )
        harness.core.start()
    else:
        harness.send("tx_release", lease=lease)
    (start,) = harness.tx_events("tx_start")
    (end,) = harness.tx_events("tx_end")
    assert end["started"] == start["ts"]
