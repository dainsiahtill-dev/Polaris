"""Repair Service - Automatic repair loop for failed QA.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.repair_service``
    (Phase 4, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.
"""

from __future__ import annotations

import warnings

from polaris.cells.director.tasking.internal.repair_service import (
    RepairContext,
    RepairResult,
    RepairService,
)

warnings.warn(
    "polaris.cells.director.execution.internal.repair_service is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "RepairContext",
    "RepairResult",
    "RepairService",
]
