"""Architecture fence for the removed TurnEngine compatibility helper API."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
REMOVED_TURN_ENGINE_COMPAT_MODULE = "polaris.cells.roles.kernel.internal.turn_engine.compat"
REMOVED_TURN_ENGINE_COMPAT_CLASS = "TurnEngineCompatMixin"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _removed_compat_references(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == REMOVED_TURN_ENGINE_COMPAT_MODULE:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom):
            imported_names = {alias.name for alias in node.names}
            if node.module == REMOVED_TURN_ENGINE_COMPAT_MODULE:
                imported = ", ".join(sorted(imported_names))
                violations.append(f"{node.module} import {imported}")
            if REMOVED_TURN_ENGINE_COMPAT_CLASS in imported_names:
                violations.append(f"{node.module or '<relative>'} import {REMOVED_TURN_ENGINE_COMPAT_CLASS}")
            continue
        if isinstance(node, ast.Name) and node.id == REMOVED_TURN_ENGINE_COMPAT_CLASS:
            violations.append(REMOVED_TURN_ENGINE_COMPAT_CLASS)
    return violations


def test_turn_engine_compat_helper_api_is_not_reintroduced() -> None:
    """TurnEngine must remain a TransactionKernel facade, not a second helper API."""
    removed_path = POLARIS_ROOT / "cells/roles/kernel/internal/turn_engine/compat.py"
    assert not removed_path.exists(), "Removed TurnEngine compatibility helper module was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        for reference in _removed_compat_references(path):
            violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {reference}")

    assert violations == [], (
        "TurnEngineCompatMixin is removed. Add execution behavior to TransactionKernel/"
        "RoleExecutionKernel instead of reviving the old TurnEngine helper API:\n" + "\n".join(violations)
    )
