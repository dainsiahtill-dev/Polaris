"""Internal implementation for the Director task-market consumer.

This package backs the current ``pending_exec`` dispatch pipeline. CE-side
Director pools handle assignment/status concerns; they do not consume TaskMarket
leases or replace this execution-stage consumer.
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
