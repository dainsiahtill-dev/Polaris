"""Worker service for managing worker lifecycle.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.worker_pool_service``
    (Phase 3, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.
"""

from __future__ import annotations

import warnings

from polaris.cells.director.tasking.internal.worker_pool_service import (
    WorkerPoolConfig,
    WorkerService,
)

warnings.warn(
    "polaris.cells.director.execution.internal.worker_pool_service is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "WorkerPoolConfig",
    "WorkerService",
]
