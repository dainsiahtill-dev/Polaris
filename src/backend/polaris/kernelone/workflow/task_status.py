"""Workflow task status enum for KernelOne workflow runtime.

This module provides the canonical WorkflowTaskStatus enum used by
WorkflowEngine, TaskRuntimeState, and ActivityRunner.

These statuses are distinct from domain/entities/task.py:TaskStatus because
they represent runtime execution states (pending, running, completed, etc.)
rather than Polaris business task lifecycle states (QUEUED, PENDING, etc.).
"""

from __future__ import annotations

from enum import StrEnum


class WorkflowTaskStatus(StrEnum):
    """Workflow task runtime statuses (StrEnum for serialization).

    These statuses represent the execution state of a task within a workflow:

    PENDING       - Task waiting for execution (dependencies not met or no slot available)
    RUNNING       - Task actively executing
    RETRYING      - Task failed but retry scheduled
    COMPLETED     - Task successfully finished
    FAILED        - Task failed after all retry attempts exhausted
    CANCELLED     - Task manually cancelled
    BLOCKED       - Task blocked due to dependency failure
    SKIPPED       - Task skipped (fail-fast triggered or explicitly skipped)
    WAITING_HUMAN - Suspended awaiting human approval (Chronos Hourglass)
    """

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    WAITING_HUMAN = "waiting_human"

    @property
    def is_terminal(self) -> bool:
        """Return True if this is a terminal state (no further transitions)."""
        return self in {
            WorkflowTaskStatus.COMPLETED,
            WorkflowTaskStatus.FAILED,
            WorkflowTaskStatus.CANCELLED,
            WorkflowTaskStatus.SKIPPED,
        }

    @property
    def is_active(self) -> bool:
        """Return True if this is an active state (task is executing or waiting)."""
        return self in {
            WorkflowTaskStatus.PENDING,
            WorkflowTaskStatus.RUNNING,
            WorkflowTaskStatus.RETRYING,
            WorkflowTaskStatus.WAITING_HUMAN,  # Human-in-loop: waiting is active
        }


class ActivityStatus(StrEnum):
    """Canonical activity execution states for ActivityRunner.

    Used by ActivityExecution in kernelone/workflow/activity_runner.py.
    Simpler state machine focused on activity-level execution.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Return True if this is a terminal state."""
        return self in {
            ActivityStatus.COMPLETED,
            ActivityStatus.FAILED,
            ActivityStatus.CANCELLED,
        }

    @property
    def is_active(self) -> bool:
        """Return True if this is an active state."""
        return not self.is_terminal


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "ActivityStatus",
    "WorkflowTaskStatus",
]


# Terminal statuses set for fast membership checks
TERMINAL_STATUSES: frozenset[str] = frozenset(
    s.value
    for s in (
        WorkflowTaskStatus.COMPLETED,
        WorkflowTaskStatus.FAILED,
        WorkflowTaskStatus.CANCELLED,
        WorkflowTaskStatus.SKIPPED,
    )
)

# Active statuses set for fast membership checks
ACTIVE_STATUSES: frozenset[str] = frozenset(
    s.value
    for s in (
        WorkflowTaskStatus.PENDING,
        WorkflowTaskStatus.RUNNING,
        WorkflowTaskStatus.RETRYING,
        WorkflowTaskStatus.WAITING_HUMAN,
    )
)
