"""Director task consumer cell.

.. deprecated::
    This cell and its exports are deprecated. Use the DirectorPool cell instead.

This cell provides the DirectorExecutionConsumer that polls the task market
for PENDING_EXEC tasks and coordinates execution with Safe Parallel support.

PR-05: Director consumer from PENDING_EXEC + Safe Parallel

# TODO: remove after 2026-06-30
"""

from __future__ import annotations

from polaris.cells.director.task_consumer.internal import (
    DirectorExecutionConsumer,
    ScopeConflictDetector,
    UnrecoverableExecutionError,
)

__all__ = [
    "DirectorExecutionConsumer",
    "ScopeConflictDetector",
    "UnrecoverableExecutionError",
]
