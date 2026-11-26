# SPDX-License-Identifier: GPL-3.0-or-later
"""DX cluster process (decisions.md D63, AGENTS.md section 10).

Keeps one telnet connection to the DX cluster the administrator set and
passes the spots to the server over a Unix socket. The only process with
network access beyond loopback; it has no path to the guard, the radio, the
station log or transmitting.
"""
