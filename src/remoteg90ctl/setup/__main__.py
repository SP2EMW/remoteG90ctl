# SPDX-License-Identifier: GPL-3.0-or-later
"""``sudo remoteg90ctl setup`` starts this: the hardware setup in the terminal."""

import os
import subprocess
import sys
from collections.abc import Callable

from remoteg90ctl.cli import DEFAULT_SOCKET, ServerConnection
from remoteg90ctl.config import Config
from remoteg90ctl.setup.access import access
from remoteg90ctl.setup.checklist import Checklist, Station, Tools
from remoteg90ctl.setup.i18n import LANGUAGES, set_language, t
from remoteg90ctl.setup.wizard import Ask, System, run


def say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def output(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=False).stdout  # noqa: S603


def spawn(argv: list[str]) -> Callable[[], tuple[int, str]]:
    """Start quietly; the result waits and gives the exit code and the output."""
    process = subprocess.Popen(  # noqa: S603  (the CLI's tx-test, fixed arguments)
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    def wait() -> tuple[int, str]:
        output, _ = process.communicate()
        return process.returncode, output

    return wait


def connect() -> Station:
    try:
        return ServerConnection(DEFAULT_SOCKET, timeout=5.0)  # type: ignore[return-value]
    except Exception as exc:  # the CLI's own error while the server is not up
        raise OSError(str(exc)) from exc


def checklist(ask: Ask, system: System) -> Callable[[Config, str], Config]:
    tools = Tools(output, spawn, connect, cli=[sys.executable, "-m", "remoteg90ctl.cli"])

    def after(config: Config, port: str) -> Config:
        config = Checklist(ask, say, system, tools, port).run(config)
        return access(ask, say, system, tools, config)

    return after


def choose_language() -> None:
    """The first question: English or Polish; Enter takes the system's language."""
    system_language = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    default = "2" if system_language.startswith("pl") else "1"
    say("Language / Język:  1. English   2. Polski")
    while True:
        answer = input(f"[{default}] ").strip() or default
        if answer in LANGUAGES:
            set_language(LANGUAGES[answer])
            say("")
            return


def main() -> int:
    if os.geteuid() != 0:
        sys.stderr.write("run as root: sudo remoteg90ctl setup\n")
        return 2
    owner = os.environ.get("SUDO_USER", "")
    try:
        choose_language()
        system = System(owner="" if owner == "root" else owner)
        return run(input, say, system, checklist(input, system))
    except (EOFError, KeyboardInterrupt):
        say(t("\nStopped."))
        return 130


if __name__ == "__main__":
    sys.exit(main())
