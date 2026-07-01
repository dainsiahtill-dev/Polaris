"""Architecture fence for the retired accel verify-orchestrator facade."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_VERIFY_ORCHESTRATOR_MODULE = "polaris.infrastructure.accel.verify.orchestrator"
CANONICAL_VERIFY_MODULE = "polaris.infrastructure.accel.verify.verify.core"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_verify_orchestrator_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_VERIFY_ORCHESTRATOR_MODULE:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_VERIFY_ORCHESTRATOR_MODULE:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def test_accel_verify_orchestrator_facade_is_removed() -> None:
    """Verification orchestration must use the refactored core module directly."""
    retired_path = POLARIS_ROOT / "infrastructure/accel/verify/orchestrator.py"
    assert not retired_path.exists(), "Retired accel verify orchestrator facade was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_verify_orchestrator_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import accel verification from "
        f"{CANONICAL_VERIFY_MODULE!r}; retired verify orchestrator imports remain:\n"
        + "\n".join(violations)
    )
