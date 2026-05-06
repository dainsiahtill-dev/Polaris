"""Worker executor for code generation tasks.

.. deprecated::
    Implementation migrated to ``polaris.cells.director.tasking.internal.worker_executor``
    (Phase 3, director.tasking sub-Cell).

    This module is kept as a backward-compatibility stub.
    Update imports to use ``polaris.cells.director.tasking.internal``.

# TODO: remove after 2026-06-30
"""

from __future__ import annotations

import warnings

# TODO: Cross-cell internal import — worker_executor symbols are not
# yet exposed in director.tasking.public. Add to public contract when stabilised.
from polaris.cells.director.tasking.public import (
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
