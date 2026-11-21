# SPDX-License-Identifier: GPL-3.0-or-later
"""The guard depends on the standard library and pyserial only (AGENTS.md rule 11)."""

import sys

from import_graph import PACKAGE, SRC, closure

ALLOWED_THIRD_PARTY = {"serial"}


def test_guard_imports_only_stdlib_and_pyserial() -> None:
    guard_modules = [
        f"{PACKAGE}.guard.{p.stem}"
        for p in (SRC / PACKAGE / "guard").glob("*.py")
        if p.stem != "__init__"
    ]
    for module in guard_modules:
        reachable, external = closure(module)
        third_party = external - set(sys.stdlib_module_names) - ALLOWED_THIRD_PARTY
        assert third_party == set(), f"{module} pulls in {sorted(third_party)}"
        assert not any(m.startswith(f"{PACKAGE}.server") for m in reachable), module
        # The I/Q code (numpy, sounddevice) belongs to the server only.
        assert not any(m.startswith(f"{PACKAGE}.iq") for m in reachable), module


def test_closure_sees_transitive_imports() -> None:
    # Self-check of the helper: main reaches pyserial only through guard.port.
    reachable, external = closure(f"{PACKAGE}.guard.main")
    assert f"{PACKAGE}.guard.port" in reachable
    assert f"{PACKAGE}.sim.radio" in reachable
    assert "serial" in external
