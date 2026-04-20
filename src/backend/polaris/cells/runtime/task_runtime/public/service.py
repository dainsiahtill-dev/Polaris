"""Public service export for the ``runtime.task_runtime`` cell.

Primary implementation lives in
``polaris.cells.runtime.task_runtime.internal.service``.
"""

from __future__ import annotations

from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

__all__ = ["TaskRuntimeService"]
