"""Worker executor for code generation tasks.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.worker_executor``
    (Phase 3, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.
"""

from __future__ import annotations

import warnings

from polaris.cells.director.tasking.internal.worker_executor import (
    CodeGenerationResult,
    WorkerExecutor,
)

warnings.warn(
    "polaris.cells.director.execution.internal.worker_executor is deprecated. "
    "Implementation migrated to polaris.cells.director.tasking.internal. "
    "Update imports accordingly.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CodeGenerationResult",
    "WorkerExecutor",
]
