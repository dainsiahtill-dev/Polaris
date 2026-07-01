"""Architecture fence for retired bootstrap.runtime_health shim."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_RUNTIME_HEALTH = POLARIS_ROOT / "bootstrap/runtime_health.py"
RETIRED_IMPORT = "polaris.bootstrap.runtime_health"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def test_retired_bootstrap_runtime_health_module_is_removed() -> None:
    """Runtime health helpers are owned by polaris.application.health."""
    assert not RETIRED_RUNTIME_HEALTH.exists(), (
        "Retired bootstrap.runtime_health shim was recreated; import runtime health "
        "helpers from polaris.application.health instead."
    )


def test_production_code_imports_application_health_directly() -> None:
    """Production code must not import through the retired bootstrap shim."""
    violations: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == RETIRED_IMPORT:
                        violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: import {alias.name}")
                continue
            if isinstance(node, ast.ImportFrom) and node.module == RETIRED_IMPORT:
                imported = ", ".join(sorted(alias.name for alias in node.names))
                violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {node.module} import {imported}")

    assert violations == [], (
        "Production code must import runtime health helpers from polaris.application.health, "
        "not the retired bootstrap.runtime_health shim:\n" + "\n".join(violations)
    )
