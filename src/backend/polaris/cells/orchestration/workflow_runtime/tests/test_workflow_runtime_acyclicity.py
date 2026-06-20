"""Acyclicity fitness gate: workflow_runtime must not import roles.adapters.

ARCHITECTURE RULE (CYCLE-13):
    ``orchestration.workflow_runtime`` previously formed an import cycle with
    ``roles.adapters``: two deferred call sites imported ``create_role_adapter``
    from ``polaris.cells.roles.adapters.public.service`` to obtain role adapters.

    The dependency direction is now inverted through the existing port:
      * ``roles.adapters`` registers its factory at import time via
        ``configure_orchestration_role_adapter_factory`` (workflow_runtime port).
      * ``workflow_runtime`` obtains adapters through the registered factory via
        ``get_orchestration_role_adapter_factory`` -- it no longer imports the
        concrete ``roles.adapters`` cell at all.

    This gate asserts NO module under ``workflow_runtime/**`` imports
    ``polaris.cells.roles.adapters`` -- neither at module level nor deferred
    (function-local). A deferred import still counts as a dependency edge, so we
    parse the AST and inspect every ``import`` / ``from ... import`` node
    regardless of where it appears in the file.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_PREFIX = "polaris.cells.roles.adapters"
_WORKFLOW_RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def _iter_workflow_runtime_python_files() -> list[Path]:
    """Return every ``*.py`` file under ``workflow_runtime/`` (this test included)."""
    return sorted(p for p in _WORKFLOW_RUNTIME_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _module_is_forbidden(module: str | None) -> bool:
    """True if ``module`` targets the ``roles.adapters`` cell (any sub-module)."""
    if not module:
        return False
    return module == _FORBIDDEN_PREFIX or module.startswith(f"{_FORBIDDEN_PREFIX}.")


def _forbidden_imports_in_file(path: Path) -> list[str]:
    """Collect any import statements (module-level OR deferred) of roles.adapters."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_is_forbidden(alias.name):
                    violations.append(f"{path}:{node.lineno}: import {alias.name}")
        # Relative imports (node.level > 0) cannot reach another cell, so only
        # absolute ``from polaris.cells.roles.adapters ...`` matters here.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _module_is_forbidden(node.module):
            names = ", ".join(alias.name for alias in node.names)
            violations.append(f"{path}:{node.lineno}: from {node.module} import {names}")

    return violations


def test_workflow_runtime_does_not_import_roles_adapters() -> None:
    """No workflow_runtime module may import polaris.cells.roles.adapters."""
    # Arrange
    files = _iter_workflow_runtime_python_files()
    assert files, "Expected to discover workflow_runtime python files to scan."

    # Act
    violations: list[str] = []
    for path in files:
        violations.extend(_forbidden_imports_in_file(path))

    # Assert
    assert not violations, (
        "workflow_runtime must not import roles.adapters (CYCLE-13). "
        "Obtain role adapters via get_orchestration_role_adapter_factory() instead. "
        "Offending imports:\n" + "\n".join(violations)
    )
