"""Architecture fence for retired Director interface import shims."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
ROOT_SHIM = BACKEND_ROOT / "director_interface.py"
PM_SHIM = POLARIS_ROOT / "delivery" / "cli" / "pm" / "director_interface.py"

FORBIDDEN_MODULES = {
    "director_interface",
    "polaris.delivery.cli.pm.director_interface",
}


def test_director_interface_shim_files_are_retired() -> None:
    """Director interface callers must import the canonical core module."""
    assert not ROOT_SHIM.exists()
    assert not PM_SHIM.exists()


def test_production_code_does_not_import_retired_director_interface_shims() -> None:
    """No production path may import the retired root or PM shim modules."""
    offenders: list[str] = []

    for path in POLARIS_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FORBIDDEN_MODULES:
                    offenders.append(path.relative_to(BACKEND_ROOT).as_posix())
                    break
            elif isinstance(node, ast.Import):
                if any(alias.name in FORBIDDEN_MODULES for alias in node.names):
                    offenders.append(path.relative_to(BACKEND_ROOT).as_posix())
                    break

    assert offenders == []


def test_director_interface_core_remains_canonical_owner() -> None:
    """The canonical implementation must remain available after shim removal."""
    core = POLARIS_ROOT / "delivery" / "cli" / "pm" / "director_interface_core.py"
    source = core.read_text(encoding="utf-8")

    assert "class DirectorInterface" in source
    assert "def create_director" in source
