"""Architecture fence for the retired accel token-estimator shim."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_ACCEL_TOKEN_ESTIMATOR_MODULE = "polaris.infrastructure.accel.token_estimator"
CANONICAL_TOKEN_ESTIMATOR_MODULE = "polaris.kernelone.llm.engine.token_estimator"
CANONICAL_TOKEN_SERVICE_MODULE = "polaris.domain.services.token_service"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_accel_token_estimator_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_ACCEL_TOKEN_ESTIMATOR_MODULE:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_ACCEL_TOKEN_ESTIMATOR_MODULE:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def test_accel_token_estimator_shim_is_removed() -> None:
    """Token estimation must use the KernelOne estimator or domain token service."""
    retired_path = POLARIS_ROOT / "infrastructure/accel/token_estimator.py"
    assert not retired_path.exists(), "Retired accel token_estimator.py shim was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_accel_token_estimator_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import token estimation from "
        f"{CANONICAL_TOKEN_ESTIMATOR_MODULE!r} or {CANONICAL_TOKEN_SERVICE_MODULE!r}; "
        "retired accel token-estimator imports remain:\n"
        + "\n".join(violations)
    )
