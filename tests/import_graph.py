# SPDX-License-Identifier: GPL-3.0-or-later
"""Static import graph of the package, for structural dependency tests."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
PACKAGE = "remoteg90ctl"


def module_path(module: str) -> Path | None:
    base = SRC.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def closure(root: str) -> tuple[set[str], set[str]]:
    """Package modules reachable from ``root`` and the external top-level imports."""
    seen: set[str] = set()
    external: set[str] = set()
    pending = [root]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        path = module_path(module)
        if path is None:
            continue
        seen.add(module)
        # Importing a submodule also imports every parent package.
        parts = module.split(".")
        pending.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        for name in imports(path):
            top = name.split(".")[0]
            if top == PACKAGE:
                pending.append(name)
            else:
                external.add(top)
    return seen, external
