# SPDX-License-Identifier: GPL-3.0-or-later
"""Step 5 of the setup: reaching the page from a phone or another computer.

The server keeps listening on loopback only (AGENTS.md rule 15); Tailscale's
``serve`` puts HTTPS in front of it inside the user's tailnet (decisions.md
D38), and the server accepts the machine's tailnet name as Host
(``server.allowed_hosts``, D34). The home network without Tailscale comes
later (D56).
"""

import json
import shutil

from remoteg90ctl.config import Config
from remoteg90ctl.setup import files
from remoteg90ctl.setup.checklist import Tools
from remoteg90ctl.setup.i18n import t
from remoteg90ctl.setup.ui import fail, note, ok, step, warn, yes
from remoteg90ctl.setup.wizard import Ask, Say, System, page_port, set_config


def access(ask: Ask, say: Say, system: System, tools: Tools, config: Config) -> Config:
    """Offer Tailscale; returns the configuration, with allowed_hosts when it is set up."""
    port = page_port(config)
    step(say, 5, t("Access from your phone and other computers"))
    note(say, t("On this computer the page is at http://127.0.0.1:{port}", port=port))
    if shutil.which("tailscale") is None:
        warn(say, t("Tailscale is not installed: the page works on this computer only."))
        return config
    note(
        say,
        t(
            "From a phone or another computer you reach it through Tailscale: a free,"
            " private\nnetwork between your own devices, encrypted."
        ),
    )
    if not yes(ask, t("Set up access through Tailscale?"), default=True):
        return config
    status = _status(tools)
    if status.get("BackendState") != "Running":
        note(say, t("Log this computer in to Tailscale: open the link below and sign in."))
        system.run(["tailscale", "up"])
        status = _status(tools)
    name = _name(status)
    if name is None:
        fail(say, t("This computer is not logged in to Tailscale."))
        note(say, t("Later: sudo tailscale up, then start the setup again."))
        return config
    hosts = list(dict.fromkeys([*config.server.allowed_hosts, name]))
    config = set_config(
        say,
        system,
        "server",
        {"allowed_hosts": "[" + ", ".join(files.toml_string(h) for h in hosts) + "]"},
    )
    note(say, t("Switching on HTTPS (Tailscale may ask you to allow it first)…"))
    if system.run(["tailscale", "serve", "--bg", str(port)]) != 0:
        fail(say, t("HTTPS is not on. Later: sudo tailscale serve --bg {port}", port=port))
        return config
    ok(say, t("From your devices: https://{name}/", name=name))
    note(
        say,
        t("On a phone: install the Tailscale app and sign in with the same account."),
    )
    if _funnel_on(tools):
        # The server refuses Funnel requests anyway (D57); still say it loudly.
        warn(
            say,
            t(
                "Tailscale Funnel is ON: it offers this computer's HTTPS to the whole"
                " internet.\nThe page refuses those requests, but switch Funnel off"
                " (tailscale funnel status shows it)."
            ),
        )
    return config


def _funnel_on(tools: Tools) -> bool:
    """Whether any HTTPS port of this computer is open to the internet (Funnel)."""
    try:
        data = json.loads(tools.output(["tailscale", "serve", "status", "--json"]) or "{}")
    except ValueError:
        return False
    allowed = data.get("AllowFunnel") if isinstance(data, dict) else None
    return isinstance(allowed, dict) and any(allowed.values())


def _status(tools: Tools) -> dict[str, object]:
    try:
        data = json.loads(tools.output(["tailscale", "status", "--json"]) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _name(status: dict[str, object]) -> str | None:
    me = status.get("Self")
    name = me.get("DNSName") if isinstance(me, dict) else None
    return name.rstrip(".") or None if isinstance(name, str) else None
