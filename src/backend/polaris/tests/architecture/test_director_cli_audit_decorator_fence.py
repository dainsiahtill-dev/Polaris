"""Architecture fence for retired Director CLI stream-audit decorator."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_AUDIT_DECORATOR_PATH = POLARIS_ROOT / "delivery/cli/director/audit_decorator.py"
RETIRED_AUDIT_DECORATOR_IMPORT = "polaris.delivery.cli.director.audit_decorator"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_audit_decorator_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_AUDIT_DECORATOR_IMPORT:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_AUDIT_DECORATOR_IMPORT:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def test_retired_director_cli_audit_decorator_is_removed() -> None:
    """UEP v2 sinks own stream audit; the old CLI decorator must not return."""
    assert not RETIRED_AUDIT_DECORATOR_PATH.exists(), (
        "Retired Director CLI audit_decorator.py was recreated. Stream audit is "
        "owned by UEP v2 sinks and archive/run-audit public contracts."
    )


def test_production_code_does_not_import_retired_director_cli_audit_decorator() -> None:
    """Production code must not route stream audit through the retired decorator."""
    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_audit_decorator_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must not import the retired Director CLI audit decorator:\n"
        + "\n".join(violations)
    )
