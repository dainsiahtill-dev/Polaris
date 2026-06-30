"""Architecture fence for retired Role Kernel policy compatibility surfaces."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_TOOL_POLICY_MODULE = "polaris.cells.roles.kernel.internal.policy.tool_policy"
POLICY_PACKAGE_MODULE = "polaris.cells.roles.kernel.internal.policy"
RETIRED_PACKAGE_EXPORT = "ToolPolicyDecision"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_policy_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_TOOL_POLICY_MODULE:
                    violations.append(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_names = {alias.name for alias in node.names}
        if node.module == RETIRED_TOOL_POLICY_MODULE:
            imported = ", ".join(sorted(imported_names))
            violations.append(f"{node.module} import {imported}")
            continue
        if node.module == POLICY_PACKAGE_MODULE and RETIRED_PACKAGE_EXPORT in imported_names:
            violations.append(f"{node.module} import {RETIRED_PACKAGE_EXPORT}")
    return violations


def test_production_code_does_not_import_retired_tool_policy_facade() -> None:
    """Production code must use the canonical policy layer, not old single-call facades."""
    retired_path = POLARIS_ROOT / "cells/roles/kernel/internal/policy/tool_policy.py"
    assert not retired_path.exists(), "Retired ToolPolicy single-call facade was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_policy_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must use 'polaris.cells.roles.kernel.internal.policy.layer' "
        "or the canonical package-level ToolPolicy export; retired tool_policy imports remain:\n"
        + "\n".join(violations)
    )
