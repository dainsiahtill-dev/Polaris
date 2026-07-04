"""Compatibility contract for runtime.task_runtime TaskBoard types.

State ownership:
    TaskBoard is owned by the ``runtime.task_runtime`` cell.
    State paths: ``runtime/tasks/*`` and ``runtime/events/taskboard.terminal.events.jsonl``.
    Production consumers must use ``TaskRuntimeService`` from
    ``polaris.cells.runtime.task_runtime.public.service``.

Types exported:
    - TaskStatus, TaskPriority: lifecycle enums (string-valued for JSON serialisation)
    - Task: task entity for compatibility tests and owner-cell projections
    - TaskBoard: file-backed task board, retained only for owner/test compatibility
    - InvalidTaskStateTransitionError: state machine exception
"""

from __future__ import annotations

from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    Task,
    TaskBoard,
    TaskPriority,
    TaskStatus,
)

__all__ = [
    "InvalidTaskStateTransitionError",
    "Task",
    "TaskBoard",
    "TaskPriority",
    "TaskStatus",
]
