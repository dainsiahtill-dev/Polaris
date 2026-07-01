"""Architecture fence for retired bootstrap port error re-export."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_MODULE = "polaris.bootstrap.ports.backend_bootstrap"
RETIRED_NAME = "BackendBootstrapError"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def test_backend_bootstrap_error_is_not_reexported_from_port_module() -> None:
    """KernelOne owns the bootstrap error type; bootstrap ports own only protocols."""
    module = importlib.import_module(RETIRED_MODULE)

    assert not hasattr(module, RETIRED_NAME), (
        "BackendBootstrapError must be imported from polaris.kernelone.errors, "
        "not re-exported from the bootstrap port protocol module."
    )


def test_production_code_does_not_import_error_from_bootstrap_port() -> None:
    """Production code must not treat the port module as an error authority."""
    violations: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != RETIRED_MODULE:
                continue
            imported = {alias.name for alias in node.names}
            if RETIRED_NAME in imported:
                violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {node.module} import {RETIRED_NAME}")

    assert violations == [], (
        "Production code must import BackendBootstrapError from polaris.kernelone.errors:\n" + "\n".join(violations)
    )
