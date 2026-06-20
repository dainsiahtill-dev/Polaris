"""Fitness (architecture) test for the ``roles.runtime`` → ``roles.adapters`` break.

``roles.adapters`` legitimately depends on ``roles.runtime`` (its workflow
adapters drive ``RoleRuntimeService`` / ``execute_role_session``). The reverse
edge — ``roles.runtime`` importing ``roles.adapters`` — only ever existed as two
compatibility proxy shims (``WorkflowRoleAdapter`` / ``execute_workflow_role``) in
``roles.runtime.public.service``. Those proxies were deleted so the dependency is
now single-directional.

This guard fails fast if any ``roles.runtime/**`` module re-grows an import of
``polaris.cells.roles.adapters`` at ANY scope (module-level OR function-local —
a deferred import is still a real cell edge).
"""

from __future__ import annotations

import ast
from pathlib import Path

_RUNTIME_CELL_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_IMPORT_PREFIX = "polaris.cells.roles.adapters"


def _iter_runtime_modules() -> list[Path]:
    """Return every ``roles.runtime/**`` Python module except this test tree."""
    tests_dir = Path(__file__).resolve().parent
    return [
        path
        for path in sorted(_RUNTIME_CELL_ROOT.rglob("*.py"))
        if tests_dir not in path.parents and path != Path(__file__).resolve()
    ]


def _imported_modules(tree: ast.AST) -> set[str]:
    """Collect every dotted module name referenced by an import statement."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_roles_runtime_never_imports_roles_adapters() -> None:
    # Arrange
    offenders: dict[str, set[str]] = {}

    # Act
    for module_path in _iter_runtime_modules():
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        hits = {
            module
            for module in _imported_modules(tree)
            if module == _FORBIDDEN_IMPORT_PREFIX or module.startswith(f"{_FORBIDDEN_IMPORT_PREFIX}.")
        }
        if hits:
            offenders[str(module_path.relative_to(_RUNTIME_CELL_ROOT))] = hits

    # Assert: zero ``roles.runtime`` → ``roles.adapters`` import edges (any scope).
    assert offenders == {}, (
        f"roles.runtime must not import roles.adapters (reverse cell edge); offending modules: {offenders}"
    )
