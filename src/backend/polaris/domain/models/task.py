"""Deprecated: Task domain model moved to domain.entities.task.

.. deprecated::
   ``polaris.domain.models.task`` is deprecated. The canonical source for
   Task, TaskStatus, and TaskPriority is ``polaris.domain.entities.task``.

   This module will be removed in a future release.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "polaris.domain.models.task is deprecated. "
    "Import Task, TaskStatus, TaskPriority from "
    "polaris.domain.entities.task instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from canonical location for backward compat.
from polaris.domain.entities.task import (
    Task,
    TaskPriority,
    TaskStatus,
)

__all__ = ["Task", "TaskPriority", "TaskStatus"]
