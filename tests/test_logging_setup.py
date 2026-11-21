# SPDX-License-Identifier: GPL-3.0-or-later
"""Key=value log formatting."""

import io
import logging

from remoteg90ctl.logging_setup import kv, setup_logging


def test_key_value_output() -> None:
    stream = io.StringIO()
    setup_logging("INFO", stream)
    logging.getLogger("remoteg90ctl.test").warning(
        "civ_rejected", extra=kv(reason='not "allowed"', command="1c", count=2)
    )
    line = stream.getvalue().strip()
    assert "level=WARNING" in line
    assert "logger=remoteg90ctl.test" in line
    assert "event=civ_rejected" in line
    assert 'reason="not \\"allowed\\""' in line
    assert "command=1c" in line
    assert "count=2" in line
    assert "\n" not in line


def test_level_filtering() -> None:
    stream = io.StringIO()
    setup_logging("WARNING", stream)
    logging.getLogger("remoteg90ctl.test").info("hidden")
    assert stream.getvalue() == ""


def test_exception_is_on_one_line() -> None:
    stream = io.StringIO()
    setup_logging("INFO", stream)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("remoteg90ctl.test").exception("failed")
    assert stream.getvalue().count("\n") == 1
    assert "exc=" in stream.getvalue()
