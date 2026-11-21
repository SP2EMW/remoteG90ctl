# SPDX-License-Identifier: GPL-3.0-or-later
"""Texts the setup writes: udev rules, the WirePlumber rule, the configuration.

The configuration is edited line by line, so its comments stay (the standard
library reads TOML but does not write it); the result is checked with
``load_config`` before it replaces the old file.
"""

import re

# ALSA names the configuration uses (config/example.toml).
RADIO_CARD = "G90AUDIO"
IQ_CARD = "G90IQ"


def card_rule(name: str, vendor: str, product: str, what: str) -> str:
    """A udev rule giving a USB sound card a stable ALSA name (AGENTS.md rule 8)."""
    return (
        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
        f"# {what}: ALSA name {name} (written by `remoteg90ctl setup`).\n"
        f'SUBSYSTEM=="sound", KERNEL=="card*", ATTRS{{idVendor}}=="{vendor}", '
        f'ATTRS{{idProduct}}=="{product}", ATTR{{id}}="{name}"\n'
    )


def wireplumber_lua(vendor: str, product: str) -> str:
    """WirePlumber 0.4: PipeWire leaves the I/Q card to the server."""
    return (
        "-- SPDX-License-Identifier: GPL-3.0-or-later\n"
        "-- The I/Q card is for remoteg90ctl only (written by `remoteg90ctl setup`).\n"
        "table.insert(alsa_monitor.rules, {\n"
        "  matches = {\n"
        "    {\n"
        f'      {{ "device.vendor.id", "equals", "0x{vendor}" }},\n'
        f'      {{ "device.product.id", "equals", "0x{product}" }},\n'
        "    },\n"
        "  },\n"
        '  apply_properties = { ["device.disabled"] = true },\n'
        "})\n"
    )


def wireplumber_conf(vendor: str, product: str) -> str:
    """WirePlumber 0.5: the same as wireplumber_lua."""
    return (
        "# SPDX-License-Identifier: GPL-3.0-or-later\n"
        "# The I/Q card is for remoteg90ctl only (written by `remoteg90ctl setup`).\n"
        "monitor.alsa.rules = [\n"
        "  {\n"
        "    matches = [\n"
        f'      {{ device.vendor.id = "0x{vendor}", device.product.id = "0x{product}" }}\n'
        "    ]\n"
        "    actions = { update-props = { device.disabled = true } }\n"
        "  }\n"
        "]\n"
    )


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_keys(text: str, section: str, values: dict[str, str]) -> str:
    """Set ``key = value`` (values as TOML literals) in a top-level section.

    An active line for the key is replaced; else a commented-out one
    (``# key = ...``); else the key goes at the end of the section. A missing
    section is added at the end.
    """
    lines = text.splitlines()
    header = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
    start = next(
        (i for i, line in enumerate(lines) if (m := header.match(line)) and m.group(1) == section),
        None,
    )
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines += [f"{key} = {value}" for key, value in values.items()]
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if header.match(lines[i])), len(lines))
    # The section's own lines end at its last non-blank, non-comment line.
    last = start
    for i in range(start + 1, end):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            last = i
    for key, value in values.items():
        active = re.compile(rf"^\s*{re.escape(key)}\s*=")
        commented = re.compile(rf"^\s*#\s*{re.escape(key)}\s*=")
        line = f"{key} = {value}"
        found = next((i for i in range(start + 1, end) if active.match(lines[i])), None)
        if found is None:
            found = next((i for i in range(start + 1, end) if commented.match(lines[i])), None)
        if found is not None:
            lines[found] = line
            last = max(last, found)
        else:
            lines.insert(last + 1, line)
            last += 1
            end += 1
    return "\n".join(lines) + "\n"
