"""Public contract for the runtime.task_runtime cell's TaskBoard capability.

This module re-exports the canonical TaskBoard implementation from the
cell's internal layer, making it accessible to all external consumers
(delivery/, other cells/, tests/) through the ACGA 2.0 public interface.

Architecture rule (ACGA 2.0):
    Cross-cell and delivery-layer callers MUST import from this module,
    NOT from the cell's internal/ path.

State ownership:
    TaskBoard is owned by the ``runtime.task_runtime`` cell.
    State paths: ``runtime/tasks/*`` and ``runtime/events/taskboard.terminal.events.jsonl``.
    No other cell should write to these paths.

Types exported:
    - TaskStatus, TaskPriority: lifecycle enums (string-valued for JSON serialisation)
    - Task: task entity
    - TaskBoard: file-backed task board with DAG dependency tracking
    - TaskBoardToolInterface: tool gateway interface
    - InvalidTaskStateTransitionError: state machine exception
    - create_taskboard(): factory function
"""

from __future__ import annotations

from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    Task,
    TaskBoard,
    TaskBoardToolInterface,
    TaskPriority,
    TaskStatus,
    create_taskboard,
)

__all__ = [
    "InvalidTaskStateTransitionError",
    "Task",
    "TaskBoard",
    "TaskBoardToolInterface",
    "TaskPriority",
    "TaskStatus",
    "create_taskboard",
]
