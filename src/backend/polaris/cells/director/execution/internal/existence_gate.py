"""existence_gate.py — Pure-Python pre-flight mode detector for the Director.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.existence_gate``
    (Phase 4, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.
"""

from __future__ import annotations

import warnings

from polaris.cells.director.tasking.internal.existence_gate import (
    ExecutionMode,
    GateResult,
    check_mode,
    is_any_missing,
    is_pure_create,
)

warnings.warn(
    "polaris.cells.director.execution.internal.existence_gate is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ExecutionMode",
    "GateResult",
    "check_mode",
    "is_any_missing",
    "is_pure_create",
]
