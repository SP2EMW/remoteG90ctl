# SPDX-License-Identifier: GPL-3.0-or-later
"""How the setup looks and asks: numbered steps, short results, plain questions.

For people who install a radio station, not a Linux system: one idea per
line, a mark in front of every result (✓ done, ⚠ worth knowing, ✗ a
problem), questions as whole sentences. Enter answers "yes" only where that
is harmless (``default=True``); questions about the radio's safety need an
explicit answer.
"""

from collections.abc import Callable

from remoteg90ctl.setup.i18n import is_no, is_skip, is_yes, t

Ask = Callable[[str], str]
Say = Callable[[str], None]

STEPS = 6
RULE = "━"


def step(say: Say, number: int, title: str) -> None:
    head = t("Step {number} of {steps}", number=number, steps=STEPS)
    line = f"{RULE * 2} {head} · {title} "
    say("\n" + line + RULE * max(3, 64 - len(line)))


def ok(say: Say, text: str) -> None:
    say(f"  ✓ {text}")


def warn(say: Say, text: str) -> None:
    say(f"  ⚠ {text}")


def fail(say: Say, text: str) -> None:
    say(f"  ✗ {text}")


def note(say: Say, text: str) -> None:
    """Explanation under a step title or a question, indented like the results."""
    for line in text.splitlines():
        say(f"  {line}" if line else "")


def yes(ask: Ask, question: str, *, default: bool | None = None) -> bool:
    """A yes/no question; Enter takes ``default`` when there is one."""
    choices = t("[y/n]") if default is None else t("[Y/n]") if default else t("[y/N]")
    while True:
        answer = ask(f"  {_indent(question)} {choices} ")
        if not answer.strip() and default is not None:
            return default
        if is_yes(answer):
            return True
        if is_no(answer):
            return False


def number(ask: Ask, question: str, highest: int, *, lowest: int = 0) -> int:
    while True:
        answer = ask(f"  {_indent(question)} ").strip()
        if answer.isdigit() and lowest <= int(answer) <= highest:
            return int(answer)


def choose[T](
    say: Say, ask: Ask, options: list[T], text: Callable[[T], str], none: str
) -> T | None:
    """A numbered list; 0 is ``none``."""
    for i, option in enumerate(options, 1):
        say(f"    {i} — {text(option)}")
    say(f"    0 — {none}")
    picked = number(ask, t("Number:"), len(options))
    return options[picked - 1] if picked else None


def check(ask: Ask, question: str) -> str:
    """A checklist question: "yes", "no" or "skip"."""
    while True:
        answer = ask(f"  {_indent(question)} {t('[y/n, SKIP]')} ")
        if is_yes(answer):
            return "yes"
        if is_no(answer):
            return "no"
        if is_skip(answer):
            return "skip"


def _indent(text: str) -> str:
    """Later lines of a question line up with its first."""
    return text.replace("\n", "\n  ")
