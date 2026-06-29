"""Internal implementation for the Director task-consumer compatibility adapter.

The long-term execution worker is DirectorPool. This package still backs the
current dispatch pipeline, so it is not a dead shim. Do not add new consumers;
new orchestration should target DirectorPool/public execution contracts and keep
this adapter as a migration boundary until dispatch cutover is complete.
"""

from __future__ import annotations

from polaris.cells.director.task_consumer.internal.director_consumer import (
    DirectorExecutionConsumer as DirectorExecutionConsumer,
    ScopeConflictDetector as ScopeConflictDetector,
    UnrecoverableExecutionError as UnrecoverableExecutionError,
)

__all__ = [
    "DirectorExecutionConsumer",
    "ScopeConflictDetector",
    "UnrecoverableExecutionError",
]
