# SPDX-License-Identifier: GPL-3.0-or-later
"""Duty cycle: total transmission time in a sliding window (decisions.md D31).

The tests use a 600 s window with a 10 s limit, so a few short transmissions
exhaust it.
"""

import json
from pathlib import Path

import pytest

from guard_helpers import Harness, make_config
from remoteg90ctl.guard.state import HISTORY_FILE, UNCLEAN_SHUTDOWN_DURING_TX, TxHistory

WINDOW_S = 600.0
MAX_TX_S = 10.0


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    harness = Harness(make_config(tmp_path, duty_window_s=WINDOW_S, duty_max_tx_s=MAX_TX_S))
    harness.core.start()
    return harness


def transmit(h: Harness, seconds: int) -> str:
    """Transmit for ``seconds``, renewing every second; return the lease."""
    lease = h.start_tx()
    for _ in range(seconds):
        h.clock.advance(1)
        h.send("tx_renew", lease=lease)
        h.core.poll()
    return lease


def restart(h: Harness, *, clean: bool = True) -> None:
    if clean:
        h.core.shutdown(clean=True)
    else:
        h.port.close()  # as after SIGKILL: the kernel drops the lines (HUPCL)
    h.core = h.new_core()
    h.core.start()


def test_limit_ends_the_transmission(h: Harness) -> None:
    lease = transmit(h, 6)
    assert h.send("tx_release", lease=lease)["ok"]
    h.clock.advance(5)
    transmit(h, 6)  # the limit is reached after 4 s of this one
    assert not h.core.tx_active
    assert not h.ptt_active
    end = h.tx_events("tx_end")[-1]
    assert end["reason"] == "duty_limit"
    assert end["duration_s"] == pytest.approx(4.0)
    # A duty-cycle end is not a lockout.
    assert h.core.lockout is None


def test_full_window_refuses_new_transmissions_until_it_has_room(h: Harness) -> None:
    transmit(h, 11)  # ended by the limit at 10 s; now is 11 s after the start
    response = h.tx_request()
    assert response["error"] == "tx_blocked"
    assert "duty cycle" in response["message"]
    duty = h.send("status")["duty"]
    assert duty["used_s"] == pytest.approx(MAX_TX_S)
    # The first second of that transmission leaves the window 600 s after it began.
    assert duty["available_in_s"] == pytest.approx(WINDOW_S - 11, abs=0.01)
    h.clock.advance(WINDOW_S - 11 - 1)
    assert h.tx_request()["error"] == "tx_blocked"
    h.clock.advance(2)
    # Room again, without tx_unlock.
    assert h.tx_request()["ok"]


def test_renewal_reports_the_nearer_of_hard_limit_and_duty(h: Harness) -> None:
    lease = transmit(h, 3)
    assert h.send("tx_renew", lease=lease)["remaining_s"] == pytest.approx(MAX_TX_S - 3)


def test_old_transmissions_leave_the_window(h: Harness) -> None:
    lease = transmit(h, 8)
    h.send("tx_release", lease=lease)
    h.clock.advance(WINDOW_S)
    assert h.send("status")["duty"]["used_s"] == 0.0
    transmit(h, 9)
    assert h.core.tx_active


def test_history_survives_a_clean_restart(h: Harness) -> None:
    transmit(h, 11)
    restart(h)
    assert "duty cycle" in h.tx_request()["message"]


def test_crash_during_tx_counts_until_the_restart(h: Harness) -> None:
    transmit(h, 4)
    h.clock.advance(1)
    restart(h, clean=False)
    assert h.core.lockout is not None
    assert h.core.lockout.reason == UNCLEAN_SHUTDOWN_DURING_TX
    assert h.send("tx_unlock")["ok"]
    # 5 s until the crash are counted, although the last renewal was at 4 s.
    assert h.send("status")["duty"]["used_s"] == pytest.approx(5.0)


@pytest.mark.parametrize("content", [b"not json", b'{"version": 1, "intervals": [[5, 1]]}'])
def test_unreadable_history_means_a_full_window(tmp_path: Path, content: bytes) -> None:
    config = make_config(tmp_path, duty_window_s=WINDOW_S, duty_max_tx_s=MAX_TX_S)
    (config.guard.state_dir / HISTORY_FILE).write_bytes(content)
    harness = Harness(config)
    harness.core.start()
    response = harness.tx_request()
    assert response["error"] == "tx_blocked"
    assert "history unreadable" in response["message"]
    harness.clock.advance(WINDOW_S + 1)
    assert harness.tx_request()["ok"]


def test_history_from_the_future_means_a_full_window(h: Harness) -> None:
    """The wall clock went back since the history was written."""
    path = h.config.guard.state_dir / HISTORY_FILE
    now = h.core._txlog.now().timestamp()
    path.write_text(json.dumps({"version": 1, "intervals": [[now + 3600, now + 3601]]}))
    restart(h)
    assert "clock went back" in h.tx_request()["message"]


def test_unsaved_history_blocks_the_window(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: TxHistory, intervals: list[tuple[float, float]]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(TxHistory, "save", fail)
    lease = transmit(h, 1)
    h.send("tx_release", lease=lease)
    assert "history not saved" in h.tx_request()["message"]


def test_history_file_is_written_after_each_transmission(h: Harness) -> None:
    lease = transmit(h, 2)
    h.send("tx_release", lease=lease)
    stored = TxHistory(h.config.guard.state_dir).load()
    assert len(stored) == 1
    start, end = stored[0]
    assert end - start == pytest.approx(2.0)


# A history that could not be saved (code review 2026-11-10, point 10d; owner's
# decision 2026-11-10): no transmission until a save succeeds, also after a restart.


def _failing_save(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """TxHistory.save fails while the returned flag is set."""
    failing = [True]
    real = TxHistory.save

    def save(self: TxHistory, intervals: list[tuple[float, float]]) -> None:
        if failing[0]:
            raise OSError("No space left on device")
        real(self, intervals)

    monkeypatch.setattr(TxHistory, "save", save)
    return failing


def test_a_restart_after_an_unsaved_history_does_not_forget_the_window(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.send("tx_release", lease=transmit(h, 2))  # saved: the file holds these 2 s
    failing = _failing_save(monkeypatch)
    lease = transmit(h, 9)  # 11 s in all, over the limit; this one is not saved
    h.send("tx_release", lease=lease)
    failing[0] = False
    monkeypatch.undo()
    restart(h)
    # The old file held only 2 s: read as it was, the window would look free.
    response = h.tx_request()
    assert response["error"] == "tx_blocked", response
    assert "history unreadable" in response["message"]


def test_no_transmission_until_the_history_is_saved(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = _failing_save(monkeypatch)
    lease = transmit(h, 1)
    h.send("tx_release", lease=lease)
    h.clock.advance(WINDOW_S + 1)  # the window's own block is over ...
    response = h.tx_request()
    assert response["error"] == "tx_blocked"  # ... but the disk still takes nothing
    assert "history not saved" in response["message"]
    assert "history not saved" in "; ".join(h.send("status")["blockers"])
    failing[0] = False  # the disk recovers: the next request saves it, then goes
    h.clock.advance(WINDOW_S + 1)
    assert h.tx_request()["ok"]
