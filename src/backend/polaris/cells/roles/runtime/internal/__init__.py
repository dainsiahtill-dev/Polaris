"""
Polaris Roles Cell - Runtime Internal Package

DEPRECATED MODULE WARNING:
    All modules in this package are deprecated.
    Importing from this package will emit DeprecationWarning.

    Migration:
        Use RoleExecutionKernel via RoleRuntimeService instead.
        The single unified execution path is:

            RoleRuntimeService -> RoleExecutionKernel (CHAT | WORKFLOW mode)

    Legacy modules (deleted in Phase 4 cleanup):
        - standalone_runner.py (StandaloneRoleAgent classes) - DELETED
        - tui_console.py (TUI classes) - DELETED
        - standalone_entry.py (CLI entry) - DELETED

    See: polaris/cells/roles/tech-debt-tracker.md
"""

from __future__ import annotations

import warnings
from importlib import import_module

# Emit deprecation warning when this package is imported
warnings.warn(
    "polaris.cells.roles.runtime.internal is deprecated. "
    "Use polaris.cells.roles.runtime.public instead. "
    "Migration: RoleRuntimeService -> RoleExecutionKernel. "
    "See: polaris/cells/roles/tech-debt-tracker.md",
    DeprecationWarning,
    stacklevel=2,
)

__deprecated__ = True

__all__ = [
    "RoleExecutionKernel",
    "RoleExecutionRequest",
    "RoleExecutionResponse",
    "__deprecated__",
]

_PUBLIC_CONTRACTS_MODULE = "polaris.cells.roles.runtime.public.contracts"


def __getattr__(name: str) -> object:
    if name not in {"RoleExecutionKernel", "RoleExecutionRequest", "RoleExecutionResponse"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_PUBLIC_CONTRACTS_MODULE)
    value = getattr(module, name)
    globals()[name] = value
    return value
