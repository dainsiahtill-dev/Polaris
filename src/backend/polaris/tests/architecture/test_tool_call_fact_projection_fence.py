"""Architecture fence for native tool-call fact projection.

Count/name facts must be derived together from the Run Ledger-owned
envelope/lifecycle helpers. Production callers must not reconstruct a parallel
projection in ``roles.kernel``.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ROLES_KERNEL_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel"
TOOL_HELPERS_PATH = ROLES_KERNEL_ROOT / "internal" / "llm_caller" / "tool_helpers.py"
RUN_LEDGER_TOOL_LIFECYCLE_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "control_plane" / "run_ledger" / "public" / "tool_lifecycle.py"
)


def _python_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and "/tests/" not in path.as_posix() and path.name != "tool_helpers.py"
    ]


def _imported_tool_helper_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = str(node.module or "")
        if not module.endswith("llm_caller.tool_helpers") and module != ".tool_helpers":
            continue
        for alias in node.names:
            imported.add(str(alias.asname or alias.name))
    return imported


def test_native_tool_call_count_and_names_projection_is_run_ledger_owned() -> None:
    """Production modules must not rebuild count/name fact projection in pairs."""

    violations: list[str] = []
    for path in _python_source_files(ROLES_KERNEL_ROOT):
        imported = _imported_tool_helper_names(path)
        if {"native_tool_call_count", "native_tool_call_names"}.issubset(imported):
            violations.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert violations == []
    helper_source = TOOL_HELPERS_PATH.read_text(encoding="utf-8")
    canonical_source = RUN_LEDGER_TOOL_LIFECYCLE_PATH.read_text(encoding="utf-8")
    assert "def native_tool_call_facts_from_sources(" in canonical_source
    assert "def project_native_tool_call_facts_to_metadata(" in canonical_source
    assert "def native_tool_call_facts(" not in helper_source
    assert "project_native_tool_call_facts_to_metadata" in helper_source
