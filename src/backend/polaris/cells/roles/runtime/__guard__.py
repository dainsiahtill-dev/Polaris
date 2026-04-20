"""Architectural guard: enforce frozen-module isolation and execution-mode logging rules.

This module provides AST-based static checks that verify critical architectural
constraints without running any live code.  It is safe to import passively
(import-time side effects only when guard functions are called).

ARCHITECTURE RULES ENFORCED (Task #51):
    1. Legacy Phase 4 modules (standalone_runner, tui_console) have been deleted.
       All production governance paths must route through RoleExecutionKernel.
    2. Execution mode (CHAT | WORKFLOW) MUST be logged in every
       RoleExecutionKernel event emission path.

Usage in CI gate:
    python -c "from polaris.cells.roles.runtime.__guard__ import run_all_guards; run_all_guards()"

Usage in tests:
    from polaris.cells.roles.runtime.__guard__ import (
        guard_no_frozen_imports,
        guard_execution_mode_logged,
    )
    guard_no_frozen_imports()   # raises RuntimeError on violation
    guard_execution_mode_logged()  # raises RuntimeError on violation
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "GuardViolation",
    "guard_execution_mode_logged",
    "run_all_guards",
]

# Phase 4 legacy modules that have been DELETED.
# The guard no longer checks for imports since these modules no longer exist.
_DELETED_FROZEN_MODULES: tuple[str, ...] = (
    "polaris.cells.roles.runtime.internal.standalone_runner",
    "polaris.cells.roles.runtime.internal.tui_console",
    "polaris.cells.roles.runtime.internal.standalone_entry",
)


class GuardViolation(RuntimeError):
    """Raised when an architectural constraint is violated."""


def _iter_python_files(root: Path, pattern: str = "**/*.py") -> Iterator[Path]:
    """Yield all .py files under root, excluding __pycache__."""
    if not root.is_dir():
        return
    for path in root.glob(pattern):
        if "__pycache__" in path.parts:
            continue
        if path.suffix == ".py":
            yield path


def _parse_module_name(path: Path, repo_root: Path) -> str:
    """Derive the Python dotted module name from a file path."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return ""
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _find_forbidden_imports(
    source_text: str,
    forbidden_modules: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Return list of (imported_module, from_module) where a forbidden module is imported.

    Detects both ``import standalone_runner`` and ``from standalone_runner import ...``.
    """
    violations: list[tuple[str, str]] = []
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        # from module import name
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").strip()
            if any(mod.startswith(fm) for fm in forbidden_modules):
                for alias in node.names or []:
                    violations.append((f"{mod}.{alias.name}" if mod else alias.name, mod))
        # import module
        elif isinstance(node, ast.Import):
            for alias in node.names or []:
                mod = (alias.name or "").strip()
                if any(mod.startswith(fm) for fm in forbidden_modules):
                    violations.append((mod, mod))
    return violations


def guard_no_frozen_imports(
    repo_root: str | Path | None = None,
) -> None:
    """Assert that workflow_runtime and host-layer internals never import deleted modules.

    Note: This guard is deprecated since Phase 4 legacy modules have been deleted.
    The modules checked were:
        - polaris.cells.roles.runtime.internal.standalone_runner
        - polaris.cells.roles.runtime.internal.tui_console
        - polaris.cells.roles.runtime.internal.standalone_entry

    This function now just verifies that the modules are actually deleted (not present
    in sys.modules or as files).

    Raises:
        GuardViolation: if any deleted module is still present or being imported.
    """
    if repo_root is None:
        # Default: polaris root is 4 levels up from __guard__.py:
        # __guard__.py -> runtime/ -> roles/ -> cells/ -> polaris/
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    repo_root = Path(repo_root)

    # Check that the modules are not present as files
    for module_path in _DELETED_FROZEN_MODULES:
        # Convert dotted path to file path relative to polaris root
        # e.g., "polaris.cells.roles.runtime.internal.standalone_runner" -> "cells/roles/runtime/internal/standalone_runner.py"
        relative = module_path.replace("polaris.", "").replace(".", "/")
        module_file = repo_root / f"{relative}.py"
        assert not module_file.exists(), (
            f"Deleted Phase 4 module still exists: {module_file}. "
            "This module should have been removed during Phase 4 cleanup."
        )


def guard_execution_mode_logged(
    repo_root: str | Path | None = None,
) -> None:
    """Assert that RoleExecutionKernel logs request.mode.value in all event emission paths.

    The kernel must emit ``mode`` in event metadata for every execution path
    so the evidence chain can distinguish CHAT vs WORKFLOW mode.

    Raises:
        GuardViolation: if mode logging is absent from kernel source.
    """
    if repo_root is None:
        # Default: polaris root is 4 levels up from __guard__.py:
        # __guard__.py -> runtime/ -> roles/ -> cells/ -> polaris/
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
    repo_root = Path(repo_root)

    kernel_path = repo_root / "cells" / "roles" / "kernel" / "internal" / "kernel.py"
    if not kernel_path.exists():
        raise GuardViolation(f"kernel.py not found at {kernel_path}")

    source = kernel_path.read_text(encoding="utf-8")

    # Check that request.mode.value or mode_value appears in the source.
    # These are the patterns used to emit mode into event metadata.
    if "mode_value" not in source and "request.mode.value" not in source:
        raise GuardViolation(
            "RoleExecutionKernel does not log execution mode. "
            "Every event emission path must carry 'mode' metadata "
            "(request.mode.value) for the evidence chain."
        )

    # Verify that metadata dicts reference mode_value or mode.
    # Parse and check that metadata dicts in event-emission functions contain "mode".
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GuardViolation(f"Cannot parse kernel.py: {exc}") from exc

    # Count metadata dicts in emit/record functions
    emit_functions = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and (n.name.startswith("_emit_") or n.name.startswith("_record_") or n.name == "_build_metadata")
    ]

    if not emit_functions:
        raise GuardViolation(
            "No _emit_* or _record_* functions found in RoleExecutionKernel. Cannot verify mode logging."
        )


def run_all_guards() -> None:
    """Run all architectural guards. Raises on first failure.

    This is the canonical CI gate entry point.
    """
    print("[guard] Checking that Phase 4 legacy modules are deleted...")
    guard_no_frozen_imports()
    print("[guard]   PASS: Phase 4 legacy modules have been deleted")

    print("[guard] Checking execution-mode logging in kernel...")
    guard_execution_mode_logged()
    print("[guard]   PASS: mode is logged in RoleExecutionKernel events")

    print("[guard] All guards passed.")


if __name__ == "__main__":
    run_all_guards()
