# SPDX-License-Identifier: GPL-3.0-or-later
"""``remoteg90ctl setup``: the hardware setup after the installer (install.sh).

It finds the radio's serial port and the sound cards, names the cards for
udev, keeps PipeWire away from the I/Q card and writes the configuration with
transmission switched off. The hardware checklist and switching transmission
on come next (docs/install.md section 5).
"""
