# SPDX-License-Identifier: GPL-3.0-or-later
"""Radio simulator used by tests and by ``radio.backend = "simulator"``."""

from remoteg90ctl.sim.port import LineEvent, SimulatedSerialPort
from remoteg90ctl.sim.radio import DEFAULT_IDENTITY, SimulatedRadio

__all__ = ["DEFAULT_IDENTITY", "LineEvent", "SimulatedRadio", "SimulatedSerialPort"]
