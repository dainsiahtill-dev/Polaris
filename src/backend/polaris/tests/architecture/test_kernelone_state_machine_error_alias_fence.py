"""Fence KernelOne state-machine abstractions from error-type re-exports."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
STATE_MACHINE_MODULE = ROOT / "src/backend/polaris/kernelone/state_machine.py"


def test_state_machine_module_does_not_reexport_invalid_transition_error() -> None:
    """The state-machine module owns abstractions, not error aliases."""

    import polaris.kernelone.state_machine as state_machine

    assert "InvalidStateTransitionError" not in state_machine.__all__
    assert not hasattr(state_machine, "InvalidStateTransitionError")


def test_production_code_imports_invalid_transition_error_from_errors_module() -> None:
    """Production callers must import the error from its authoritative owner."""

    offenders: list[str] = []
    source_root = ROOT / "src/backend/polaris"
    for path in source_root.rglob("*.py"):
        if "__pycache__" in path.parts or path == STATE_MACHINE_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "polaris.kernelone.state_machine":
                imported_names = {alias.name for alias in node.names}
                if "InvalidStateTransitionError" in imported_names:
                    offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
