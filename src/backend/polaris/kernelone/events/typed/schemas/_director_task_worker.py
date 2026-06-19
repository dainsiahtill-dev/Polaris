"""Director, Task and Worker orchestration event triplets.

Bodies moved verbatim from the original
``polaris/kernelone/events/typed/schemas.py`` module to preserve behavior.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ._base import EventBase, EventCategory, EventPayload

# =============================================================================
# Director Execution Events
# =============================================================================


class DirectorStartedPayload(EventPayload):
    """Payload for director started event."""

    workspace: str = Field(..., description="Director workspace path")
    max_workers: int = Field(default=1, description="Maximum worker count")
    config: dict[str, Any] = Field(default_factory=dict, description="Director configuration")


class DirectorStarted(EventBase):
    """Director started event.

    Emitted when the Director service starts.
    """

    event_name: Literal["director_started"] = "director_started"
    category: EventCategory = EventCategory.DIRECTOR
    payload: DirectorStartedPayload = Field(default_factory=DirectorStartedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        workspace: str,
        max_workers: int = 1,
        config: dict[str, Any] | None = None,
        run_id: str = "",
    ) -> DirectorStarted:
        """Factory method to create a DirectorStarted event."""
        return cls(
            payload=DirectorStartedPayload(
                workspace=workspace,
                max_workers=max_workers,
                config=config or {},
            ),
            run_id=run_id,
            workspace=workspace,
        )


class DirectorStoppedPayload(EventPayload):
    """Payload for director stopped event."""

    workspace: str = Field(..., description="Director workspace path")
    reason: str = Field(default="", description="Stop reason")
    auto: bool = Field(default=False, description="Whether stop was automatic")
    metrics: dict[str, Any] = Field(default_factory=dict, description="Final director metrics")


class DirectorStopped(EventBase):
    """Director stopped event.

    Emitted when the Director service stops.
    """

    event_name: Literal["director_stopped"] = "director_stopped"
    category: EventCategory = EventCategory.DIRECTOR
    payload: DirectorStoppedPayload = Field(default_factory=DirectorStoppedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        workspace: str,
        reason: str = "",
        auto: bool = False,
        metrics: dict[str, Any] | None = None,
        run_id: str = "",
    ) -> DirectorStopped:
        """Factory method to create a DirectorStopped event."""
        return cls(
            payload=DirectorStoppedPayload(
                workspace=workspace,
                reason=reason,
                auto=auto,
                metrics=metrics or {},
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskSubmittedPayload(EventPayload):
    """Payload for task submitted event."""

    task_id: str = Field(..., description="Task identifier")
    subject: str = Field(..., description="Task subject")
    priority: str = Field(default="MEDIUM", description="Task priority")
    timeout_seconds: int | None = Field(default=None, description="Configured timeout")
    blocked_by: list[str] = Field(default_factory=list, description="Blocked by task IDs")


class TaskSubmitted(EventBase):
    """Task submitted event.

    Emitted when a new task is submitted to the Director.
    """

    event_name: Literal["task_submitted"] = "task_submitted"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskSubmittedPayload = Field(default_factory=TaskSubmittedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        subject: str,
        priority: str = "MEDIUM",
        timeout_seconds: int | None = None,
        blocked_by: list[str] | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskSubmitted:
        """Factory method to create a TaskSubmitted event."""
        return cls(
            payload=TaskSubmittedPayload(
                task_id=task_id,
                subject=subject,
                priority=priority,
                timeout_seconds=timeout_seconds,
                blocked_by=blocked_by or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskStartedPayload(EventPayload):
    """Payload for task started event."""

    task_id: str = Field(..., description="Task identifier")
    worker_id: str = Field(..., description="Worker assigned to the task")


class TaskStarted(EventBase):
    """Task started event.

    Emitted when a task starts execution on a worker.
    """

    event_name: Literal["task_started"] = "task_started"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskStartedPayload = Field(default_factory=TaskStartedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        worker_id: str,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskStarted:
        """Factory method to create a TaskStarted event."""
        return cls(
            payload=TaskStartedPayload(
                task_id=task_id,
                worker_id=worker_id,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskCompletedPayload(EventPayload):
    """Payload for task completed event."""

    task_id: str = Field(..., description="Task identifier")
    success: bool = Field(..., description="Whether task succeeded")
    changed_files: list[str] = Field(default_factory=list, description="Files modified by task")
    duration_ms: int | None = Field(default=None, description="Execution duration in ms")


class TaskCompleted(EventBase):
    """Task completed event.

    Emitted when a task completes execution.
    """

    event_name: Literal["task_completed"] = "task_completed"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskCompletedPayload = Field(default_factory=TaskCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        success: bool,
        changed_files: list[str] | None = None,
        duration_ms: int | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskCompleted:
        """Factory method to create a TaskCompleted event."""
        return cls(
            payload=TaskCompletedPayload(
                task_id=task_id,
                success=success,
                changed_files=changed_files or [],
                duration_ms=duration_ms,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskFailedPayload(EventPayload):
    """Payload for task failed event."""

    task_id: str = Field(..., description="Task identifier")
    error: str = Field(..., description="Error message")
    duration_ms: int | None = Field(default=None, description="Execution duration in ms")


class TaskFailed(EventBase):
    """Task failed event.

    Emitted when a task fails during execution.
    """

    event_name: Literal["task_failed"] = "task_failed"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskFailedPayload = Field(default_factory=TaskFailedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        error: str,
        duration_ms: int | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskFailed:
        """Factory method to create a TaskFailed event."""
        return cls(
            payload=TaskFailedPayload(
                task_id=task_id,
                error=error,
                duration_ms=duration_ms,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class WorkerSpawnedPayload(EventPayload):
    """Payload for worker spawned event."""

    worker_id: str = Field(..., description="Worker identifier")
    workspace: str = Field(..., description="Worker workspace")


class WorkerSpawned(EventBase):
    """Worker spawned event.

    Emitted when a new worker is spawned.
    """

    event_name: Literal["worker_spawned"] = "worker_spawned"
    category: EventCategory = EventCategory.DIRECTOR
    payload: WorkerSpawnedPayload = Field(default_factory=WorkerSpawnedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        worker_id: str,
        workspace: str,
        run_id: str = "",
    ) -> WorkerSpawned:
        """Factory method to create a WorkerSpawned event."""
        return cls(
            payload=WorkerSpawnedPayload(
                worker_id=worker_id,
                workspace=workspace,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class WorkerStoppedPayload(EventPayload):
    """Payload for worker stopped event."""

    worker_id: str = Field(..., description="Worker identifier")
    reason: str = Field(default="", description="Stop reason")


class WorkerStopped(EventBase):
    """Worker stopped event.

    Emitted when a worker stops.
    """

    event_name: Literal["worker_stopped"] = "worker_stopped"
    category: EventCategory = EventCategory.DIRECTOR
    payload: WorkerStoppedPayload = Field(default_factory=WorkerStoppedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        worker_id: str,
        reason: str = "",
        workspace: str = "",
        run_id: str = "",
    ) -> WorkerStopped:
        """Factory method to create a WorkerStopped event."""
        return cls(
            payload=WorkerStoppedPayload(
                worker_id=worker_id,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class NagReminderPayload(EventPayload):
    """Payload for nag reminder event."""

    message: str = Field(..., description="Reminder message")


class NagReminder(EventBase):
    """Nag reminder event.

    Emitted when the Director sends a nag reminder.
    """

    event_name: Literal["nag_reminder"] = "nag_reminder"
    category: EventCategory = EventCategory.DIRECTOR
    payload: NagReminderPayload = Field(default_factory=NagReminderPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        message: str,
        workspace: str = "",
        run_id: str = "",
    ) -> NagReminder:
        """Factory method to create a NagReminder event."""
        return cls(
            payload=NagReminderPayload(message=message),
            run_id=run_id,
            workspace=workspace,
        )


class BudgetExceededPayload(EventPayload):
    """Payload for budget exceeded event."""

    used_tokens: int = Field(..., description="Tokens used")
    budget_limit: int = Field(..., description="Budget limit")


class BudgetExceeded(EventBase):
    """Budget exceeded event.

    Emitted when the token budget is exceeded.
    """

    event_name: Literal["budget_exceeded"] = "budget_exceeded"
    category: EventCategory = EventCategory.DIRECTOR
    payload: BudgetExceededPayload = Field(default_factory=BudgetExceededPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        used_tokens: int,
        budget_limit: int,
        workspace: str = "",
        run_id: str = "",
    ) -> BudgetExceeded:
        """Factory method to create a BudgetExceeded event."""
        return cls(
            payload=BudgetExceededPayload(
                used_tokens=used_tokens,
                budget_limit=budget_limit,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Worker Lifecycle Events
# =============================================================================


class WorkerReadyPayload(EventPayload):
    """Payload for worker ready event."""

    worker_id: str = Field(..., description="Worker identifier")
    workspace: str = Field(..., description="Worker workspace")


class WorkerReady(EventBase):
    """Worker ready event.

    Emitted when a worker is ready to accept tasks.
    """

    event_name: Literal["worker_ready"] = "worker_ready"
    category: EventCategory = EventCategory.DIRECTOR
    payload: WorkerReadyPayload = Field(default_factory=WorkerReadyPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        worker_id: str,
        workspace: str,
        run_id: str = "",
    ) -> WorkerReady:
        """Factory method to create a WorkerReady event."""
        return cls(
            payload=WorkerReadyPayload(
                worker_id=worker_id,
                workspace=workspace,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class WorkerBusyPayload(EventPayload):
    """Payload for worker busy event."""

    worker_id: str = Field(..., description="Worker identifier")
    task_id: str = Field(..., description="Task being executed")


class WorkerBusy(EventBase):
    """Worker busy event.

    Emitted when a worker starts executing a task.
    """

    event_name: Literal["worker_busy"] = "worker_busy"
    category: EventCategory = EventCategory.DIRECTOR
    payload: WorkerBusyPayload = Field(default_factory=WorkerBusyPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        worker_id: str,
        task_id: str,
        workspace: str = "",
        run_id: str = "",
    ) -> WorkerBusy:
        """Factory method to create a WorkerBusy event."""
        return cls(
            payload=WorkerBusyPayload(
                worker_id=worker_id,
                task_id=task_id,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class WorkerStoppingPayload(EventPayload):
    """Payload for worker stopping event."""

    worker_id: str = Field(..., description="Worker identifier")
    reason: str = Field(default="", description="Stop reason")


class WorkerStopping(EventBase):
    """Worker stopping event.

    Emitted when a worker begins graceful shutdown.
    """

    event_name: Literal["worker_stopping"] = "worker_stopping"
    category: EventCategory = EventCategory.DIRECTOR
    payload: WorkerStoppingPayload = Field(default_factory=WorkerStoppingPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        worker_id: str,
        reason: str = "",
        workspace: str = "",
        run_id: str = "",
    ) -> WorkerStopping:
        """Factory method to create a WorkerStopping event."""
        return cls(
            payload=WorkerStoppingPayload(
                worker_id=worker_id,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Director Lifecycle Events (Extended)
# =============================================================================


class DirectorPausedPayload(EventPayload):
    """Payload for director paused event."""

    workspace: str = Field(..., description="Director workspace")
    reason: str = Field(default="", description="Pause reason")


class DirectorPaused(EventBase):
    """Director paused event.

    Emitted when the Director service pauses.
    """

    event_name: Literal["director_paused"] = "director_paused"
    category: EventCategory = EventCategory.DIRECTOR
    payload: DirectorPausedPayload = Field(default_factory=DirectorPausedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        workspace: str,
        reason: str = "",
        run_id: str = "",
    ) -> DirectorPaused:
        """Factory method to create a DirectorPaused event."""
        return cls(
            payload=DirectorPausedPayload(
                workspace=workspace,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class DirectorResumedPayload(EventPayload):
    """Payload for director resumed event."""

    workspace: str = Field(..., description="Director workspace")


class DirectorResumed(EventBase):
    """Director resumed event.

    Emitted when the Director service resumes from pause.
    """

    event_name: Literal["director_resumed"] = "director_resumed"
    category: EventCategory = EventCategory.DIRECTOR
    payload: DirectorResumedPayload = Field(default_factory=DirectorResumedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        workspace: str,
        run_id: str = "",
    ) -> DirectorResumed:
        """Factory method to create a DirectorResumed event."""
        return cls(
            payload=DirectorResumedPayload(workspace=workspace),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Task Lifecycle Events (Extended)
# =============================================================================


class TaskClaimedPayload(EventPayload):
    """Payload for task claimed event."""

    task_id: str = Field(..., description="Task identifier")
    worker_id: str = Field(..., description="Worker that claimed the task")


class TaskClaimed(EventBase):
    """Task claimed event.

    Emitted when a task is claimed by a worker.
    """

    event_name: Literal["task_claimed"] = "task_claimed"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskClaimedPayload = Field(default_factory=TaskClaimedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        worker_id: str,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskClaimed:
        """Factory method to create a TaskClaimed event."""
        return cls(
            payload=TaskClaimedPayload(
                task_id=task_id,
                worker_id=worker_id,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskCancelledPayload(EventPayload):
    """Payload for task cancelled event."""

    task_id: str = Field(..., description="Task identifier")
    reason: str = Field(default="", description="Cancellation reason")


class TaskCancelled(EventBase):
    """Task cancelled event.

    Emitted when a task is cancelled.
    """

    event_name: Literal["task_cancelled"] = "task_cancelled"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskCancelledPayload = Field(default_factory=TaskCancelledPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        reason: str = "",
        workspace: str = "",
        run_id: str = "",
    ) -> TaskCancelled:
        """Factory method to create a TaskCancelled event."""
        return cls(
            payload=TaskCancelledPayload(
                task_id=task_id,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskRetryPayload(EventPayload):
    """Payload for task retry event."""

    task_id: str = Field(..., description="Task identifier")
    attempt: int = Field(..., description="Retry attempt number")
    max_retries: int = Field(..., description="Maximum retry attempts")


class TaskRetry(EventBase):
    """Task retry event.

    Emitted when a task is being retried.
    """

    event_name: Literal["task_retry"] = "task_retry"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskRetryPayload = Field(default_factory=TaskRetryPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        attempt: int,
        max_retries: int,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskRetry:
        """Factory method to create a TaskRetry event."""
        return cls(
            payload=TaskRetryPayload(
                task_id=task_id,
                attempt=attempt,
                max_retries=max_retries,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# UI-Specific Events
# =============================================================================


class TaskProgressPayload(EventPayload):
    """Payload for task progress event.

    Emitted to update UI on task execution progress.
    Used for progress bars, status displays, and real-time updates.
    """

    task_id: str = Field(..., description="Task identifier")
    phase: str = Field(..., description="Current phase (prepare/validate/implement/verify/report)")
    phase_index: int = Field(..., ge=0, description="Current phase index (0-based)")
    phase_total: int = Field(..., ge=1, description="Total number of phases")
    retry_count: int = Field(default=0, description="Number of retries")
    max_retries: int = Field(default=0, description="Maximum retry attempts")
    current_file: str = Field(default="", description="Currently processed file")
    changed_files: list[str] = Field(default_factory=list, description="Files modified in this phase")
    files_modified: int = Field(default=0, description="Count of files modified")
    retry_phase: str | None = Field(default=None, description="Phase being retried, if any")
    status_note: str | None = Field(default=None, description="Additional status note")


class TaskProgress(EventBase):
    """Task progress event.

    Emitted to update UI on task execution progress.
    Category: Used by orchestrators for real-time UI updates.
    """

    event_name: Literal["task_progress"] = "task_progress"
    category: EventCategory = EventCategory.DIRECTOR
    payload: TaskProgressPayload = Field(default_factory=TaskProgressPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        task_id: str,
        phase: str,
        phase_index: int,
        phase_total: int,
        retry_count: int = 0,
        max_retries: int = 0,
        current_file: str = "",
        changed_files: list[str] | None = None,
        files_modified: int = 0,
        retry_phase: str | None = None,
        status_note: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskProgress:
        """Factory method to create a TaskProgress event."""
        return cls(
            payload=TaskProgressPayload(
                task_id=task_id,
                phase=phase,
                phase_index=phase_index,
                phase_total=phase_total,
                retry_count=retry_count,
                max_retries=max_retries,
                current_file=current_file,
                changed_files=changed_files or [],
                files_modified=files_modified,
                retry_phase=retry_phase,
                status_note=status_note,
            ),
            run_id=run_id,
            workspace=workspace,
        )
