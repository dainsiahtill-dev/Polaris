"""Internal implementation for director.task_consumer cell.

.. deprecated::
    This module and its exports are deprecated. Use the DirectorPool cell instead.

Public contracts are defined in the parent module's public contracts (if any).

# TODO: remove after 2026-06-30
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
