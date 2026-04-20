"""Typed Event Schemas for KernelOne.

Design principles:
1. Zod-style discriminated union using Pydantic discriminated_union
2. Each event has explicit name, version, and payload schema
3. Schema evolution via versioned events

Reference: OpenCode packages/opencode/src/bus/bus-event.ts
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

# =============================================================================
# Event Category Enum (for wildcard subscription)
# =============================================================================


class EventCategory(str, Enum):
    """Event categories for wildcard subscription patterns.

    Note: This enum is intentionally separate from ErrorCategory in
    polaris/kernelone/errors.py. EventCategory classifies events for
    subscription/filtering purposes (lifecycle, tool, turn, etc.),
    while ErrorCategory classifies errors for handling/routing purposes
    (provider_error, timeout, validation, etc.).
    """

    LIFECYCLE = "lifecycle"  # Instance, session lifecycle
    TOOL = "tool"  # Tool execution
    TURN = "turn"  # Turn engine
    DIRECTOR = "director"  # Director execution
    CONTEXT = "context"  # Context management
    AUDIT = "audit"  # Audit events
    AUDIT_EXTENDED = "audit_extended"  # Extended audit events (LLM, tool, task, etc.)
    SYSTEM = "system"  # System events
    COGNITIVE = "cognitive"  # Cognitive life form events (thinking, reflection, evolution)


# =============================================================================
# Base Event Model
# =============================================================================


class EventPayload(BaseModel):
    """Base class for all event payloads."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class EventBase(BaseModel):
    """Base class for all typed events.

    Attributes:
        event_id: Unique event identifier (UUID)
        event_name: Event type name (discriminator)
        event_version: Schema version for evolution
        category: Event category for pattern matching
        timestamp: Event timestamp (UTC)
        run_id: Run identifier for correlation
        workspace: Workspace path
        correlation_id: Optional correlation ID for tracing
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
        frozen=True,
        use_enum_values=True,
    )

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_name: str = Field(..., description="Event type name (discriminator)")
    event_version: int = Field(default=1, ge=1, description="Schema version")
    category: EventCategory = Field(..., description="Event category")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str = Field(default="", description="Run identifier")
    workspace: str = Field(default="", description="Workspace path")
    correlation_id: str | None = Field(default=None, description="Correlation ID for tracing")


# =============================================================================
# Instance Lifecycle Events
# =============================================================================


class InstanceStartedPayload(EventPayload):
    """Payload for instance started event."""

    instance_id: str = Field(..., description="Instance unique identifier")
    instance_type: str = Field(..., description="Instance type (kernel, agent, etc.)")
    config: dict[str, Any] = Field(default_factory=dict, description="Instance configuration")


class InstanceStarted(EventBase):
    """Instance started event.

    Emitted when a new instance is initialized.
    """

    event_name: Literal["instance_started"] = "instance_started"
    category: EventCategory = EventCategory.LIFECYCLE
    payload: InstanceStartedPayload = Field(default_factory=InstanceStartedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        instance_id: str,
        instance_type: str,
        run_id: str = "",
        workspace: str = "",
        config: dict[str, Any] | None = None,
    ) -> InstanceStarted:
        """Factory method to create an InstanceStarted event."""
        return cls(
            payload=InstanceStartedPayload(
                instance_id=instance_id,
                instance_type=instance_type,
                config=config or {},
            ),
            run_id=run_id,
            workspace=workspace,
        )


class InstanceDisposedPayload(EventPayload):
    """Payload for instance disposed event."""

    directory: str = Field(..., description="Instance directory")
    reason: str | None = Field(default=None, description="Disposal reason")
    duration_ms: int | None = Field(default=None, description="Instance lifetime in ms")


class InstanceDisposed(EventBase):
    """Instance disposed event.

    Emitted when an instance is shut down.
    Reference: OpenCode BusEvent.InstanceDisposed
    """

    event_name: Literal["instance_disposed"] = "instance_disposed"
    category: EventCategory = EventCategory.LIFECYCLE
    payload: InstanceDisposedPayload = Field(default_factory=InstanceDisposedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        directory: str,
        reason: str | None = None,
        duration_ms: int | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> InstanceDisposed:
        """Factory method to create an InstanceDisposed event."""
        return cls(
            payload=InstanceDisposedPayload(
                directory=directory,
                reason=reason,
                duration_ms=duration_ms,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Tool Execution Events (OpenCode-style ToolState tracking)
# =============================================================================


class ToolInvokedPayload(EventPayload):
    """Payload for tool invoked event.

    Emitted when a tool call is initiated (pending -> running).
    """

    tool_name: str = Field(..., description="Tool name")
    tool_call_id: str = Field(..., description="Unique tool call identifier")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    execution_lane: str = Field(default="direct", description="Execution lane (direct, batch, etc.)")


class ToolInvoked(EventBase):
    """Tool invoked event (ToolState: pending -> running)."""

    event_name: Literal["tool_invoked"] = "tool_invoked"
    category: EventCategory = EventCategory.TOOL
    payload: ToolInvokedPayload = Field(default_factory=ToolInvokedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any] | None = None,
        execution_lane: str = "direct",
        run_id: str = "",
        workspace: str = "",
        correlation_id: str | None = None,
    ) -> ToolInvoked:
        """Factory method to create a ToolInvoked event."""
        return cls(
            payload=ToolInvokedPayload(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=arguments or {},
                execution_lane=execution_lane,
            ),
            run_id=run_id,
            workspace=workspace,
            correlation_id=correlation_id,
        )


class ToolCompletedPayload(EventPayload):
    """Payload for tool completed event."""

    tool_name: str = Field(..., description="Tool name")
    tool_call_id: str = Field(..., description="Unique tool call identifier")
    result: Any = Field(default=None, description="Tool execution result")
    duration_ms: int | None = Field(default=None, description="Execution duration in ms")
    output_size: int = Field(default=0, description="Result output size in bytes")


class ToolCompleted(EventBase):
    """Tool completed event (ToolState: running -> completed)."""

    event_name: Literal["tool_completed"] = "tool_completed"
    category: EventCategory = EventCategory.TOOL
    payload: ToolCompletedPayload = Field(default_factory=ToolCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        tool_name: str,
        tool_call_id: str,
        result: Any = None,
        duration_ms: int | None = None,
        output_size: int = 0,
        run_id: str = "",
        workspace: str = "",
        correlation_id: str | None = None,
    ) -> ToolCompleted:
        """Factory method to create a ToolCompleted event."""
        return cls(
            payload=ToolCompletedPayload(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                result=result,
                duration_ms=duration_ms,
                output_size=output_size,
            ),
            run_id=run_id,
            workspace=workspace,
            correlation_id=correlation_id,
        )


class ToolErrorKind(str, Enum):
    """Classification of tool errors."""

    EXCEPTION = "exception"  # Unhandled exception
    VALIDATION = "validation"  # Invalid arguments
    PERMISSION = "permission"  # Permission denied
    NOT_FOUND = "not_found"  # Tool not found
    RUNTIME = "runtime"  # Runtime error
    TIMEOUT = "timeout"  # Execution timeout
    CANCELLED = "cancelled"  # Execution cancelled
    UNKNOWN = "unknown"  # Unknown error


class ToolErrorPayload(EventPayload):
    """Payload for tool error event."""

    tool_name: str = Field(..., description="Tool name")
    tool_call_id: str = Field(..., description="Unique tool call identifier")
    error: str = Field(..., description="Error message")
    error_type: ToolErrorKind | None = Field(default=None, description="Error classification")
    error_kind: str | None = Field(default=None, description="Error kind (for JSON serialization)")
    stack_trace: str | None = Field(default=None, description="Stack trace if available")
    duration_ms: int | None = Field(default=None, description="Execution duration in ms")


class ToolError(EventBase):
    """Tool error event (ToolState: running -> error)."""

    event_name: Literal["tool_error"] = "tool_error"
    category: EventCategory = EventCategory.TOOL
    payload: ToolErrorPayload = Field(default_factory=ToolErrorPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        tool_name: str,
        tool_call_id: str,
        error: str,
        error_type: ToolErrorKind | None = None,
        stack_trace: str | None = None,
        duration_ms: int | None = None,
        run_id: str = "",
        workspace: str = "",
        correlation_id: str | None = None,
    ) -> ToolError:
        """Factory method to create a ToolError event."""
        return cls(
            payload=ToolErrorPayload(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                error=error,
                error_type=error_type,
                error_kind=error_type.value if error_type else None,
                stack_trace=stack_trace,
                duration_ms=duration_ms,
            ),
            run_id=run_id,
            workspace=workspace,
            correlation_id=correlation_id,
        )


class ToolBlockedPayload(EventPayload):
    """Payload for tool blocked event."""

    tool_name: str = Field(..., description="Tool name")
    tool_call_id: str = Field(..., description="Unique tool call identifier")
    reason: str = Field(..., description="Blocking reason")
    policy: str | None = Field(default=None, description="Policy that blocked the tool")


class ToolBlocked(EventBase):
    """Tool blocked event (ToolState: blocked by policy)."""

    event_name: Literal["tool_blocked"] = "tool_blocked"
    category: EventCategory = EventCategory.TOOL
    payload: ToolBlockedPayload = Field(default_factory=ToolBlockedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        tool_name: str,
        tool_call_id: str,
        reason: str,
        policy: str | None = None,
        run_id: str = "",
        workspace: str = "",
        correlation_id: str | None = None,
    ) -> ToolBlocked:
        """Factory method to create a ToolBlocked event."""
        return cls(
            payload=ToolBlockedPayload(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                reason=reason,
                policy=policy,
            ),
            run_id=run_id,
            workspace=workspace,
            correlation_id=correlation_id,
        )


class ToolTimeoutPayload(EventPayload):
    """Payload for tool timeout event."""

    tool_name: str = Field(..., description="Tool name")
    tool_call_id: str = Field(..., description="Unique tool call identifier")
    timeout_seconds: int = Field(..., description="Configured timeout in seconds")
    duration_ms: int | None = Field(default=None, description="Actual duration in ms")


class ToolTimeout(EventBase):
    """Tool timeout event (ToolState: running -> timeout)."""

    event_name: Literal["tool_timeout"] = "tool_timeout"
    category: EventCategory = EventCategory.TOOL
    payload: ToolTimeoutPayload = Field(default_factory=ToolTimeoutPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        tool_name: str,
        tool_call_id: str,
        timeout_seconds: int,
        duration_ms: int | None = None,
        run_id: str = "",
        workspace: str = "",
        correlation_id: str | None = None,
    ) -> ToolTimeout:
        """Factory method to create a ToolTimeout event."""
        return cls(
            payload=ToolTimeoutPayload(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                timeout_seconds=timeout_seconds,
                duration_ms=duration_ms,
            ),
            run_id=run_id,
            workspace=workspace,
            correlation_id=correlation_id,
        )


# =============================================================================
# Turn Events
# =============================================================================


class TurnStartedPayload(EventPayload):
    """Payload for turn started event."""

    turn_id: str = Field(..., description="Turn identifier")
    agent: str = Field(..., description="Agent name")
    prompt: str = Field(..., description="Turn prompt")
    tools: list[str] = Field(default_factory=list, description="Available tools")


class TurnStarted(EventBase):
    """Turn started event."""

    event_name: Literal["turn_started"] = "turn_started"
    category: EventCategory = EventCategory.TURN
    payload: TurnStartedPayload = Field(default_factory=TurnStartedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        turn_id: str,
        agent: str,
        prompt: str,
        tools: list[str] | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> TurnStarted:
        """Factory method to create a TurnStarted event."""
        return cls(
            payload=TurnStartedPayload(
                turn_id=turn_id,
                agent=agent,
                prompt=prompt,
                tools=tools or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TurnCompletedPayload(EventPayload):
    """Payload for turn completed event."""

    turn_id: str = Field(..., description="Turn identifier")
    agent: str = Field(..., description="Agent name")
    tool_calls_count: int = Field(default=0, description="Number of tool calls made")
    duration_ms: int | None = Field(default=None, description="Turn duration in ms")
    tokens_used: int = Field(default=0, description="Tokens consumed")


class TurnCompleted(EventBase):
    """Turn completed event."""

    event_name: Literal["turn_completed"] = "turn_completed"
    category: EventCategory = EventCategory.TURN
    payload: TurnCompletedPayload = Field(default_factory=TurnCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        turn_id: str,
        agent: str,
        tool_calls_count: int = 0,
        duration_ms: int | None = None,
        tokens_used: int = 0,
        run_id: str = "",
        workspace: str = "",
    ) -> TurnCompleted:
        """Factory method to create a TurnCompleted event."""
        return cls(
            payload=TurnCompletedPayload(
                turn_id=turn_id,
                agent=agent,
                tool_calls_count=tool_calls_count,
                duration_ms=duration_ms,
                tokens_used=tokens_used,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TurnFailedPayload(EventPayload):
    """Payload for turn failed event."""

    turn_id: str = Field(..., description="Turn identifier")
    agent: str = Field(..., description="Agent name")
    error: str = Field(..., description="Error message")
    error_type: str | None = Field(default=None, description="Error type")


class TurnFailed(EventBase):
    """Turn failed event."""

    event_name: Literal["turn_failed"] = "turn_failed"
    category: EventCategory = EventCategory.TURN
    payload: TurnFailedPayload = Field(default_factory=TurnFailedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        turn_id: str,
        agent: str,
        error: str,
        error_type: str | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> TurnFailed:
        """Factory method to create a TurnFailed event."""
        return cls(
            payload=TurnFailedPayload(
                turn_id=turn_id,
                agent=agent,
                error=error,
                error_type=error_type,
            ),
            run_id=run_id,
            workspace=workspace,
        )


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
# System Events
# =============================================================================


class SystemErrorPayload(EventPayload):
    """Payload for system error event."""

    component: str = Field(..., description="Component that generated the error")
    error: str = Field(..., description="Error message")
    stack_trace: str | None = Field(default=None, description="Stack trace if available")


class SystemError(EventBase):
    """System error event.

    Emitted when a system-level error occurs.
    """

    event_name: Literal["system_error"] = "system_error"
    category: EventCategory = EventCategory.SYSTEM
    payload: SystemErrorPayload = Field(default_factory=SystemErrorPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        component: str,
        error: str,
        stack_trace: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> SystemError:
        """Factory method to create a SystemError event."""
        return cls(
            payload=SystemErrorPayload(
                component=component,
                error=error,
                stack_trace=stack_trace,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Audit Events
# =============================================================================


class AuditCompletedPayload(EventPayload):
    """Payload for audit completed event."""

    audit_id: str = Field(..., description="Audit identifier")
    target: str = Field(..., description="Target of the audit (e.g., task_id)")
    verdict: str = Field(..., description="Audit verdict (pass/fail/warn)")
    issue_count: int = Field(default=0, description="Number of issues found")


class AuditCompleted(EventBase):
    """Audit completed event.

    Emitted when a QA audit completes.
    """

    event_name: Literal["audit_completed"] = "audit_completed"
    category: EventCategory = EventCategory.AUDIT
    payload: AuditCompletedPayload = Field(default_factory=AuditCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        audit_id: str,
        target: str,
        verdict: str,
        issue_count: int = 0,
        workspace: str = "",
        run_id: str = "",
    ) -> AuditCompleted:
        """Factory method to create an AuditCompleted event."""
        return cls(
            payload=AuditCompletedPayload(
                audit_id=audit_id,
                target=target,
                verdict=verdict,
                issue_count=issue_count,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Audit Extended Events (Omniscient Audit System - Phase 1)
# =============================================================================


class LLMInteractionPayload(EventPayload):
    """Payload for LLM interaction audit events.

    CloudEvents-v1.3 inspired schema for comprehensive LLM call tracking.
    """

    call_id: str = Field(..., description="Unique LLM call identifier")
    model: str = Field(..., description="Model used for the call")
    provider: str = Field(..., description="LLM provider name")
    prompt_tokens: int = Field(default=0, description="Number of prompt tokens")
    completion_tokens: int = Field(default=0, description="Number of completion tokens")
    total_tokens: int = Field(default=0, description="Total tokens used")
    latency_ms: float = Field(default=0.0, description="Latency in milliseconds")
    prompt_hash: str = Field(default="", description="SHA256 hash of prompt (for deduplication)")
    prompt_preview: str = Field(default="", description="First 200 characters of prompt")
    response_preview: str = Field(default="", description="First 500 characters of response")
    finish_reason: str = Field(default="", description="Completion finish reason")
    model_downgrade: bool = Field(default=False, description="Whether model was downgraded")
    safety_flagged: bool = Field(default=False, description="Whether response was safety-flagged")
    safety_categories: list[str] = Field(default_factory=list, description="Safety categories triggered")
    error: str | None = Field(default=None, description="Error message if any")
    temperature: float = Field(default=0.0, description="Temperature parameter")
    max_tokens: int = Field(default=0, description="Max tokens parameter")
    streaming: bool = Field(default=False, description="Whether streaming was enabled")
    system_prompt_hash: str | None = Field(default=None, description="Hash of system prompt")
    turn_id: str | None = Field(default=None, description="Associated turn ID")
    span_id: str | None = Field(default=None, description="Associated span ID")


class LLMInteractionEvent(EventBase):
    """Audit event for LLM interactions.

    Emitted for comprehensive LLM call tracking (CloudEvents-v1.3 inspired).
    """

    event_name: Literal["llm_interaction"] = "llm_interaction"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: LLMInteractionPayload = Field(default_factory=LLMInteractionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        call_id: str,
        model: str,
        provider: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float = 0.0,
        prompt_hash: str = "",
        prompt_preview: str = "",
        response_preview: str = "",
        finish_reason: str = "",
        model_downgrade: bool = False,
        safety_flagged: bool = False,
        safety_categories: list[str] | None = None,
        error: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 0,
        streaming: bool = False,
        system_prompt_hash: str | None = None,
        turn_id: str | None = None,
        span_id: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> LLMInteractionEvent:
        """Factory method to create an LLMInteractionEvent."""
        return cls(
            payload=LLMInteractionPayload(
                call_id=call_id,
                model=model,
                provider=provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                prompt_hash=prompt_hash,
                prompt_preview=prompt_preview,
                response_preview=response_preview,
                finish_reason=finish_reason,
                model_downgrade=model_downgrade,
                safety_flagged=safety_flagged,
                safety_categories=safety_categories or [],
                error=error,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                system_prompt_hash=system_prompt_hash,
                turn_id=turn_id,
                span_id=span_id,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ToolExecutionPayload(EventPayload):
    """Payload for tool execution audit events."""

    call_id: str = Field(..., description="Unique tool call identifier")
    tool_name: str = Field(..., description="Tool name")
    arguments_hash: str = Field(default="", description="SHA256 hash of arguments (for deduplication)")
    arguments_preview: str = Field(default="", description="First 200 characters of arguments")
    result_preview: str = Field(default="", description="First 500 characters of result")
    duration_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    success: bool = Field(default=True, description="Whether execution succeeded")
    error_type: str | None = Field(default=None, description="Error type if failed")
    error_message: str | None = Field(default=None, description="Error message if failed")
    error_stack: str | None = Field(default=None, description="Error stack trace if failed")
    api_status_code: int | None = Field(default=None, description="API status code if applicable")
    turn_id: str | None = Field(default=None, description="Associated turn ID")
    span_id: str | None = Field(default=None, description="Associated span ID")
    is_write_operation: bool = Field(default=False, description="Whether this is a write operation")
    file_paths_affected: list[str] = Field(default_factory=list, description="File paths affected by the operation")


class ToolExecutionEvent(EventBase):
    """Audit event for tool executions.

    Emitted for comprehensive tool execution tracking.
    """

    event_name: Literal["tool_execution"] = "tool_execution"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: ToolExecutionPayload = Field(default_factory=ToolExecutionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        call_id: str,
        tool_name: str,
        arguments_hash: str = "",
        arguments_preview: str = "",
        result_preview: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        error_type: str | None = None,
        error_message: str | None = None,
        error_stack: str | None = None,
        api_status_code: int | None = None,
        turn_id: str | None = None,
        span_id: str | None = None,
        is_write_operation: bool = False,
        file_paths_affected: list[str] | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> ToolExecutionEvent:
        """Factory method to create a ToolExecutionEvent."""
        return cls(
            payload=ToolExecutionPayload(
                call_id=call_id,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                arguments_preview=arguments_preview,
                result_preview=result_preview,
                duration_ms=duration_ms,
                success=success,
                error_type=error_type,
                error_message=error_message,
                error_stack=error_stack,
                api_status_code=api_status_code,
                turn_id=turn_id,
                span_id=span_id,
                is_write_operation=is_write_operation,
                file_paths_affected=file_paths_affected or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class TaskOrchestrationPayload(EventPayload):
    """Payload for task orchestration audit events."""

    dag_id: str = Field(..., description="DAG identifier")
    task_id: str = Field(..., description="Task identifier")
    parent_task_ids: list[str] = Field(default_factory=list, description="Parent task IDs")
    state_before: str = Field(default="", description="State before transition")
    state_after: str = Field(default="", description="State after transition")
    state_change_reason: str = Field(default="", description="Reason for state change")
    duration_ms: float | None = Field(default=None, description="Execution duration in milliseconds")
    retry_count: int = Field(default=0, description="Current retry count")
    max_retries: int = Field(default=0, description="Maximum retry attempts")
    deadlock_detected: bool = Field(default=False, description="Whether deadlock was detected")
    timeout_warnings: int = Field(default=0, description="Number of timeout warnings")
    parallel_sync_points: list[str] = Field(default_factory=list, description="Parallel sync points")


class TaskOrchestrationEvent(EventBase):
    """Audit event for task DAG orchestration.

    Emitted for comprehensive task orchestration tracking.
    """

    event_name: Literal["task_orchestration"] = "task_orchestration"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: TaskOrchestrationPayload = Field(default_factory=TaskOrchestrationPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        dag_id: str,
        task_id: str,
        parent_task_ids: list[str] | None = None,
        state_before: str = "",
        state_after: str = "",
        state_change_reason: str = "",
        duration_ms: float | None = None,
        retry_count: int = 0,
        max_retries: int = 0,
        deadlock_detected: bool = False,
        timeout_warnings: int = 0,
        parallel_sync_points: list[str] | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> TaskOrchestrationEvent:
        """Factory method to create a TaskOrchestrationEvent."""
        return cls(
            payload=TaskOrchestrationPayload(
                dag_id=dag_id,
                task_id=task_id,
                parent_task_ids=parent_task_ids or [],
                state_before=state_before,
                state_after=state_after,
                state_change_reason=state_change_reason,
                duration_ms=duration_ms,
                retry_count=retry_count,
                max_retries=max_retries,
                deadlock_detected=deadlock_detected,
                timeout_warnings=timeout_warnings,
                parallel_sync_points=parallel_sync_points or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class AgentCommunicationPayload(EventPayload):
    """Payload for agent/role communication audit events."""

    message_id: str = Field(..., description="Unique message identifier")
    sender_role: str = Field(..., description="Sender role (e.g., pm, architect)")
    receiver_role: str = Field(..., description="Receiver role")
    intent: str = Field(default="", description="Communication intent (delegate, report, query, coordinate)")
    routing_path: list[str] = Field(default_factory=list, description="List of agents in routing path")
    message_type: str = Field(default="", description="Message type (task_delegation, status_report, etc.)")
    turn_id: str | None = Field(default=None, description="Associated turn ID")
    span_id: str | None = Field(default=None, description="Associated span ID")
    in_response_to_message_id: str | None = Field(default=None, description="Message ID this is responding to")
    topic: str | None = Field(default=None, description="Communication topic")


class AgentCommunicationEvent(EventBase):
    """Audit event for multi-agent communication.

    Emitted for comprehensive agent/role communication tracking.
    """

    event_name: Literal["agent_communication"] = "agent_communication"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: AgentCommunicationPayload = Field(default_factory=AgentCommunicationPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        message_id: str,
        sender_role: str,
        receiver_role: str,
        intent: str = "",
        routing_path: list[str] | None = None,
        message_type: str = "",
        turn_id: str | None = None,
        span_id: str | None = None,
        in_response_to_message_id: str | None = None,
        topic: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> AgentCommunicationEvent:
        """Factory method to create an AgentCommunicationEvent."""
        return cls(
            payload=AgentCommunicationPayload(
                message_id=message_id,
                sender_role=sender_role,
                receiver_role=receiver_role,
                intent=intent,
                routing_path=routing_path or [],
                message_type=message_type,
                turn_id=turn_id,
                span_id=span_id,
                in_response_to_message_id=in_response_to_message_id,
                topic=topic,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ContextManagementPayload(EventPayload):
    """Payload for context management audit events."""

    operation: str = Field(..., description="Operation type (render, compact, evict, load, save)")
    template_name: str | None = Field(default=None, description="Template name if applicable")
    window_occupancy_before_pct: float = Field(default=0.0, description="Window occupancy before operation (%)")
    window_occupancy_after_pct: float = Field(default=0.0, description="Window occupancy after operation (%)")
    tokens_before: int = Field(default=0, description="Token count before operation")
    tokens_after: int = Field(default=0, description="Token count after operation")
    max_window_tokens: int = Field(default=0, description="Maximum window token capacity")
    compaction_triggered: bool = Field(default=False, description="Whether compaction was triggered")
    evicted_entries: int = Field(default=0, description="Number of entries evicted")
    loaded_entries: int = Field(default=0, description="Number of entries loaded")
    llm_call_triggered: bool = Field(default=False, description="Whether LLM call was triggered")
    oom_intercepted: bool = Field(default=False, description="Whether OOM was intercepted")
    turn_id: str | None = Field(default=None, description="Associated turn ID")
    span_id: str | None = Field(default=None, description="Associated span ID")


class ContextManagementEvent(EventBase):
    """Audit event for context assembly and management.

    Emitted for comprehensive context tracking.
    """

    event_name: Literal["context_management"] = "context_management"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: ContextManagementPayload = Field(default_factory=ContextManagementPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        operation: str,
        template_name: str | None = None,
        window_occupancy_before_pct: float = 0.0,
        window_occupancy_after_pct: float = 0.0,
        tokens_before: int = 0,
        tokens_after: int = 0,
        max_window_tokens: int = 0,
        compaction_triggered: bool = False,
        evicted_entries: int = 0,
        loaded_entries: int = 0,
        llm_call_triggered: bool = False,
        oom_intercepted: bool = False,
        turn_id: str | None = None,
        span_id: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> ContextManagementEvent:
        """Factory method to create a ContextManagementEvent."""
        return cls(
            payload=ContextManagementPayload(
                operation=operation,
                template_name=template_name,
                window_occupancy_before_pct=window_occupancy_before_pct,
                window_occupancy_after_pct=window_occupancy_after_pct,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                max_window_tokens=max_window_tokens,
                compaction_triggered=compaction_triggered,
                evicted_entries=evicted_entries,
                loaded_entries=loaded_entries,
                llm_call_triggered=llm_call_triggered,
                oom_intercepted=oom_intercepted,
                turn_id=turn_id,
                span_id=span_id,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class BudgetAuditPayload(EventPayload):
    """Payload for budget consumption audit events.

    Complements the existing BudgetExceeded event with granular tracking.
    """

    budget_type: str = Field(..., description="Budget type (tokens, calls, time, file_writes)")
    budget_limit: int = Field(..., description="Budget limit")
    consumed: int = Field(default=0, description="Amount consumed")
    remaining: int = Field(default=0, description="Amount remaining")
    consumption_pct: float = Field(default=0.0, description="Consumption percentage")
    threshold_warn_pct: float = Field(default=0.0, description="Warning threshold percentage")
    threshold_exceeded: bool = Field(default=False, description="Whether threshold was exceeded")
    model: str | None = Field(default=None, description="Model for token budgets")
    window_seconds: int | None = Field(default=None, description="Time window in seconds")


class BudgetAuditEvent(EventBase):
    """Audit event for budget consumption tracking.

    Emitted for granular budget tracking (complements BudgetExceeded).
    """

    event_name: Literal["budget_audit"] = "budget_audit"
    category: EventCategory = EventCategory.AUDIT_EXTENDED
    payload: BudgetAuditPayload = Field(default_factory=BudgetAuditPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        budget_type: str,
        budget_limit: int,
        consumed: int = 0,
        remaining: int = 0,
        consumption_pct: float = 0.0,
        threshold_warn_pct: float = 0.0,
        threshold_exceeded: bool = False,
        model: str | None = None,
        window_seconds: int | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> BudgetAuditEvent:
        """Factory method to create a BudgetAuditEvent."""
        return cls(
            payload=BudgetAuditPayload(
                budget_type=budget_type,
                budget_limit=budget_limit,
                consumed=consumed,
                remaining=remaining,
                consumption_pct=consumption_pct,
                threshold_warn_pct=threshold_warn_pct,
                threshold_exceeded=threshold_exceeded,
                model=model,
                window_seconds=window_seconds,
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
# Planning Events
# =============================================================================


class PlanCreatedPayload(EventPayload):
    """Payload for plan created event."""

    plan_id: str = Field(..., description="Plan identifier")
    target: str = Field(..., description="Target of the plan")
    summary: str = Field(default="", description="Plan summary")


class PlanCreated(EventBase):
    """Plan created event.

    Emitted when a new plan is created.
    """

    event_name: Literal["plan_created"] = "plan_created"
    category: EventCategory = EventCategory.CONTEXT
    payload: PlanCreatedPayload = Field(default_factory=PlanCreatedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        plan_id: str,
        target: str,
        summary: str = "",
        workspace: str = "",
        run_id: str = "",
    ) -> PlanCreated:
        """Factory method to create a PlanCreated event."""
        return cls(
            payload=PlanCreatedPayload(
                plan_id=plan_id,
                target=target,
                summary=summary,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# File Events
# =============================================================================


class FileWrittenPayload(EventPayload):
    """Payload for file written event."""

    filepath: str = Field(..., description="Path to the file")
    size_bytes: int = Field(default=0, description="File size in bytes")
    content_hash: str | None = Field(default=None, description="Content hash")


class FileWritten(EventBase):
    """File written event.

    Emitted when a file is written to the workspace.
    """

    event_name: Literal["file_written"] = "file_written"
    category: EventCategory = EventCategory.CONTEXT
    payload: FileWrittenPayload = Field(default_factory=FileWrittenPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        filepath: str,
        size_bytes: int = 0,
        content_hash: str | None = None,
        workspace: str = "",
        run_id: str = "",
    ) -> FileWritten:
        """Factory method to create a FileWritten event."""
        return cls(
            payload=FileWrittenPayload(
                filepath=filepath,
                size_bytes=size_bytes,
                content_hash=content_hash,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Context Events (Extended)
# =============================================================================


class CompactRequestedPayload(EventPayload):
    """Payload for compact requested event."""

    reason: str = Field(default="", description="Reason for compaction")
    current_tokens: int = Field(default=0, description="Current token count")
    threshold: int = Field(default=0, description="Threshold that triggered compaction")


class CompactRequested(EventBase):
    """Compact requested event.

    Emitted when context compaction is requested.
    """

    event_name: Literal["compact_requested"] = "compact_requested"
    category: EventCategory = EventCategory.CONTEXT
    payload: CompactRequestedPayload = Field(default_factory=CompactRequestedPayload)

    @classmethod
    def create(
        cls,
        reason: str = "",
        current_tokens: int = 0,
        threshold: int = 0,
        workspace: str = "",
        run_id: str = "",
    ) -> CompactRequested:
        """Factory method to create a CompactRequested event."""
        return cls(
            payload=CompactRequestedPayload(
                reason=reason,
                current_tokens=current_tokens,
                threshold=threshold,
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


# =============================================================================
# Settings Events
# =============================================================================


class SettingsChangedPayload(EventPayload):
    """Payload for settings changed event."""

    workspace: str = Field(..., description="Current workspace path")
    previous_workspace: str = Field(default="", description="Previous workspace path")
    changed_fields: list[str] = Field(default_factory=list, description="List of changed settings fields")


class SettingsChanged(EventBase):
    """Settings changed event.

    Emitted when application settings change.
    """

    event_name: Literal["settings_changed"] = "settings_changed"
    category: EventCategory = EventCategory.SYSTEM
    payload: SettingsChangedPayload = Field(default_factory=SettingsChangedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        workspace: str,
        previous_workspace: str = "",
        changed_fields: list[str] | None = None,
        run_id: str = "",
    ) -> SettingsChanged:
        """Factory method to create a SettingsChanged event."""
        return cls(
            payload=SettingsChangedPayload(
                workspace=workspace,
                previous_workspace=previous_workspace,
                changed_fields=changed_fields or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Discriminated Union for All Events
# =============================================================================


class ContextWindowStatusPayload(EventPayload):
    """Payload for context window status event.

    Emitted to display current context usage and remaining capacity.
    Useful for monitoring how close the context is to the limit.
    """

    current_tokens: int = Field(..., description="Current token count in context")
    max_tokens: int = Field(..., description="Maximum context window size")
    remaining_tokens: int = Field(..., description="Remaining token capacity")
    usage_percentage: float = Field(..., ge=0.0, le=100.0, description="Usage percentage (0-100)")
    is_critical: bool = Field(..., description="True if usage > 80% (approaching limit)")
    is_exhausted: bool = Field(..., description="True if usage >= 100% (at or over limit)")
    segment_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Token breakdown by segment (system, history, tools, etc.)",
    )


class ContextWindowStatus(EventBase):
    """Context window status event.

    Emitted when context window status changes or is queried.
    Category: CONTEXT
    """

    event_name: Literal["context_window_status"] = "context_window_status"
    category: EventCategory = EventCategory.CONTEXT
    payload: ContextWindowStatusPayload = Field(default_factory=ContextWindowStatusPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        current_tokens: int,
        max_tokens: int,
        segment_breakdown: dict[str, int] | None = None,
        critical_threshold: float = 80.0,
        run_id: str = "",
        workspace: str = "",
    ) -> ContextWindowStatus:
        """Factory method to create a ContextWindowStatus event.

        Args:
            current_tokens: Current token count in context
            max_tokens: Maximum context window size
            segment_breakdown: Optional breakdown of tokens by segment
            critical_threshold: Percentage threshold for is_critical flag
            run_id: Run identifier
            workspace: Workspace path

        Returns:
            ContextWindowStatus event
        """
        remaining = max(0, max_tokens - current_tokens)
        usage_pct = min(100.0, (current_tokens / max_tokens * 100.0) if max_tokens > 0 else 0.0)

        return cls(
            payload=ContextWindowStatusPayload(
                current_tokens=current_tokens,
                max_tokens=max_tokens,
                remaining_tokens=remaining,
                usage_percentage=round(usage_pct, 2),
                is_critical=usage_pct >= critical_threshold,
                is_exhausted=current_tokens >= max_tokens,
                segment_breakdown=segment_breakdown or {},
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Cognitive Life Form Events
# =============================================================================


class ThinkingPhasePayload(EventPayload):
    """Payload for thinking phase event."""

    phase: str = Field(..., description="Thinking phase name")
    content: str = Field(default="", description="Thinking content")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence level")
    intent_type: str = Field(default="", description="Detected intent type")


class ThinkingPhaseEvent(EventBase):
    """Thinking phase event.

    Emitted during the thinking phase of cognitive processing.
    """

    event_name: Literal["thinking_phase"] = "thinking_phase"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ThinkingPhasePayload = Field(default_factory=ThinkingPhasePayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        phase: str,
        content: str = "",
        confidence: float = 0.5,
        intent_type: str = "",
        run_id: str = "",
        workspace: str = "",
    ) -> ThinkingPhaseEvent:
        """Factory method to create a ThinkingPhaseEvent."""
        return cls(
            payload=ThinkingPhasePayload(
                phase=phase,
                content=content,
                confidence=confidence,
                intent_type=intent_type,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ReflectionPayload(EventPayload):
    """Payload for reflection event."""

    reflection_type: str = Field(..., description="Type of reflection (pre|post|meta)")
    insights: list[str] = Field(default_factory=list, description="Reflection insights")
    knowledge_gaps: list[str] = Field(default_factory=list, description="Identified knowledge gaps")
    patterns_identified: list[str] = Field(default_factory=list, description="Patterns identified")


class ReflectionEvent(EventBase):
    """Reflection event.

    Emitted when the cognitive system performs self-reflection.
    """

    event_name: Literal["reflection"] = "reflection"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ReflectionPayload = Field(default_factory=ReflectionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        reflection_type: str,
        insights: list[str] | None = None,
        knowledge_gaps: list[str] | None = None,
        patterns_identified: list[str] | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> ReflectionEvent:
        """Factory method to create a ReflectionEvent."""
        return cls(
            payload=ReflectionPayload(
                reflection_type=reflection_type,
                insights=insights or [],
                knowledge_gaps=knowledge_gaps or [],
                patterns_identified=patterns_identified or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class EvolutionPayload(EventPayload):
    """Payload for evolution event."""

    trigger_type: str = Field(..., description="Trigger type for evolution")
    adaptation: str = Field(default="", description="Adaptation description")
    learning_recorded: bool = Field(default=False, description="Whether learning was recorded")


class EvolutionEvent(EventBase):
    """Evolution event.

    Emitted when the cognitive system evolves/adapts.
    """

    event_name: Literal["evolution"] = "evolution"
    category: EventCategory = EventCategory.COGNITIVE
    payload: EvolutionPayload = Field(default_factory=EvolutionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        trigger_type: str,
        adaptation: str = "",
        learning_recorded: bool = False,
        run_id: str = "",
        workspace: str = "",
    ) -> EvolutionEvent:
        """Factory method to create an EvolutionEvent."""
        return cls(
            payload=EvolutionPayload(
                trigger_type=trigger_type,
                adaptation=adaptation,
                learning_recorded=learning_recorded,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class BeliefChangePayload(EventPayload):
    """Payload for belief change event."""

    belief_key: str = Field(..., description="Belief identifier")
    old_value: float = Field(default=0.0, description="Previous belief value")
    new_value: float = Field(default=0.0, description="New belief value")
    reason: str = Field(default="", description="Reason for belief change")


class BeliefChangeEvent(EventBase):
    """Belief change event.

    Emitted when the cognitive system's beliefs are updated.
    """

    event_name: Literal["belief_change"] = "belief_change"
    category: EventCategory = EventCategory.COGNITIVE
    payload: BeliefChangePayload = Field(default_factory=BeliefChangePayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        belief_key: str,
        old_value: float = 0.0,
        new_value: float = 0.0,
        reason: str = "",
        run_id: str = "",
        workspace: str = "",
    ) -> BeliefChangeEvent:
        """Factory method to create a BeliefChangeEvent."""
        return cls(
            payload=BeliefChangePayload(
                belief_key=belief_key,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ConfidenceCalibrationPayload(EventPayload):
    """Payload for confidence calibration event."""

    original_confidence: float = Field(..., ge=0.0, le=1.0, description="Original confidence")
    calibrated_confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence")
    calibration_factor: float = Field(default=1.0, description="Calibration factor applied")


class ConfidenceCalibrationEvent(EventBase):
    """Confidence calibration event.

    Emitted when confidence scores are calibrated.
    """

    event_name: Literal["confidence_calibration"] = "confidence_calibration"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ConfidenceCalibrationPayload = Field(default_factory=ConfidenceCalibrationPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        original_confidence: float,
        calibrated_confidence: float,
        calibration_factor: float = 1.0,
        run_id: str = "",
        workspace: str = "",
    ) -> ConfidenceCalibrationEvent:
        """Factory method to create a ConfidenceCalibrationEvent."""
        return cls(
            payload=ConfidenceCalibrationPayload(
                original_confidence=original_confidence,
                calibrated_confidence=calibrated_confidence,
                calibration_factor=calibration_factor,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class PerceptionCompletedPayload(EventPayload):
    """Payload for perception completed event."""

    intent_type: str = Field(..., description="Detected intent type")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in detection")
    uncertainty_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Uncertainty score")


class PerceptionCompletedEvent(EventBase):
    """Perception completed event.

    Emitted when the perception layer completes processing.
    """

    event_name: Literal["perception_completed"] = "perception_completed"
    category: EventCategory = EventCategory.COGNITIVE
    payload: PerceptionCompletedPayload = Field(default_factory=PerceptionCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        intent_type: str,
        confidence: float,
        uncertainty_score: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> PerceptionCompletedEvent:
        """Factory method to create a PerceptionCompletedEvent."""
        return cls(
            payload=PerceptionCompletedPayload(
                intent_type=intent_type,
                confidence=confidence,
                uncertainty_score=uncertainty_score,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ReasoningCompletedPayload(EventPayload):
    """Payload for reasoning completed event."""

    reasoning_type: str = Field(..., description="Type of reasoning performed")
    conclusion: str = Field(default="", description="Reasoning conclusion")
    blockers: list[str] = Field(default_factory=list, description="Identified blockers")


class ReasoningCompletedEvent(EventBase):
    """Reasoning completed event.

    Emitted when the reasoning engine completes analysis.
    """

    event_name: Literal["reasoning_completed"] = "reasoning_completed"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ReasoningCompletedPayload = Field(default_factory=ReasoningCompletedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        reasoning_type: str,
        conclusion: str = "",
        blockers: list[str] | None = None,
        run_id: str = "",
        workspace: str = "",
    ) -> ReasoningCompletedEvent:
        """Factory method to create a ReasoningCompletedEvent."""
        return cls(
            payload=ReasoningCompletedPayload(
                reasoning_type=reasoning_type,
                conclusion=conclusion,
                blockers=blockers or [],
            ),
            run_id=run_id,
            workspace=workspace,
        )


class IntentDetectedPayload(EventPayload):
    """Payload for intent detected event."""

    intent_type: str = Field(..., description="Detected intent type")
    surface_intent: str = Field(default="", description="Surface level intent")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Detection confidence")


class IntentDetectedEvent(EventBase):
    """Intent detected event.

    Emitted when an intent is detected from user input.
    """

    event_name: Literal["intent_detected"] = "intent_detected"
    category: EventCategory = EventCategory.COGNITIVE
    payload: IntentDetectedPayload = Field(default_factory=IntentDetectedPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        intent_type: str,
        surface_intent: str = "",
        confidence: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> IntentDetectedEvent:
        """Factory method to create an IntentDetectedEvent."""
        return cls(
            payload=IntentDetectedPayload(
                intent_type=intent_type,
                surface_intent=surface_intent,
                confidence=confidence,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class CriticalThinkingPayload(EventPayload):
    """Payload for critical thinking event."""

    analysis_type: str = Field(..., description="Type of critical analysis")
    findings: list[str] = Field(default_factory=list, description="Analysis findings")
    risk_level: str = Field(default="low", description="Assessed risk level")


class CriticalThinkingEvent(EventBase):
    """Critical thinking event.

    Emitted when critical thinking analysis is performed.
    """

    event_name: Literal["critical_thinking"] = "critical_thinking"
    category: EventCategory = EventCategory.COGNITIVE
    payload: CriticalThinkingPayload = Field(default_factory=CriticalThinkingPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        analysis_type: str,
        findings: list[str] | None = None,
        risk_level: str = "low",
        run_id: str = "",
        workspace: str = "",
    ) -> CriticalThinkingEvent:
        """Factory method to create a CriticalThinkingEvent."""
        return cls(
            payload=CriticalThinkingPayload(
                analysis_type=analysis_type,
                findings=findings or [],
                risk_level=risk_level,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class CautiousExecutionPayload(EventPayload):
    """Payload for cautious execution event."""

    execution_path: str = Field(..., description="Execution path taken")
    requires_confirmation: bool = Field(default=False, description="Whether confirmation is required")
    stakes_level: str = Field(default="low", description="Stakes level (low|medium|high)")


class CautiousExecutionEvent(EventBase):
    """Cautious execution event.

    Emitted when cautious execution policy is applied.
    """

    event_name: Literal["cautious_execution"] = "cautious_execution"
    category: EventCategory = EventCategory.COGNITIVE
    payload: CautiousExecutionPayload = Field(default_factory=CautiousExecutionPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        execution_path: str,
        requires_confirmation: bool = False,
        stakes_level: str = "low",
        run_id: str = "",
        workspace: str = "",
    ) -> CautiousExecutionEvent:
        """Factory method to create a CautiousExecutionEvent."""
        return cls(
            payload=CautiousExecutionPayload(
                execution_path=execution_path,
                requires_confirmation=requires_confirmation,
                stakes_level=stakes_level,
            ),
            run_id=run_id,
            workspace=workspace,
        )


class ValueAlignmentPayload(EventPayload):
    """Payload for value alignment event."""

    action: str = Field(..., description="Action being evaluated")
    verdict: str = Field(..., description="Alignment verdict (APPROVED|REJECTED|PENDING)")
    conflicts: list[str] = Field(default_factory=list, description="Value conflicts identified")
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Alignment score")


class ValueAlignmentEvent(EventBase):
    """Value alignment event.

    Emitted when value alignment check is performed.
    """

    event_name: Literal["value_alignment"] = "value_alignment"
    category: EventCategory = EventCategory.COGNITIVE
    payload: ValueAlignmentPayload = Field(default_factory=ValueAlignmentPayload)  # type: ignore[arg-type]

    @classmethod
    def create(
        cls,
        action: str,
        verdict: str,
        conflicts: list[str] | None = None,
        overall_score: float = 0.0,
        run_id: str = "",
        workspace: str = "",
    ) -> ValueAlignmentEvent:
        """Factory method to create a ValueAlignmentEvent."""
        return cls(
            payload=ValueAlignmentPayload(
                action=action,
                verdict=verdict,
                conflicts=conflicts or [],
                overall_score=overall_score,
            ),
            run_id=run_id,
            workspace=workspace,
        )


# =============================================================================
# Discriminated Union for All Events
# =============================================================================


def _event_discriminator(event: EventBase) -> str:
    """Discriminator function for event union.

    Uses event_name field to discriminate between event types.
    This enables type-safe pattern matching on event types.
    """
    return event.event_name


# Type alias for all typed events
TypedEvent = Annotated[
    InstanceStarted
    | InstanceDisposed
    | ToolInvoked
    | ToolCompleted
    | ToolError
    | ToolBlocked
    | ToolTimeout
    | TurnStarted
    | TurnCompleted
    | TurnFailed
    | ContextWindowStatus
    | CompactRequested
    | PlanCreated
    | FileWritten
    | DirectorStarted
    | DirectorStopped
    | DirectorPaused
    | DirectorResumed
    | TaskSubmitted
    | TaskClaimed
    | TaskStarted
    | TaskCompleted
    | TaskFailed
    | TaskCancelled
    | TaskRetry
    | TaskProgress
    | WorkerSpawned
    | WorkerReady
    | WorkerBusy
    | WorkerStopping
    | WorkerStopped
    | NagReminder
    | BudgetExceeded
    | SystemError
    | SettingsChanged
    | AuditCompleted
    | LLMInteractionEvent
    | ToolExecutionEvent
    | TaskOrchestrationEvent
    | AgentCommunicationEvent
    | ContextManagementEvent
    | BudgetAuditEvent
    # Cognitive events
    | ThinkingPhaseEvent
    | ReflectionEvent
    | EvolutionEvent
    | BeliefChangeEvent
    | ConfidenceCalibrationEvent
    | PerceptionCompletedEvent
    | ReasoningCompletedEvent
    | IntentDetectedEvent
    | CriticalThinkingEvent
    | CautiousExecutionEvent
    | ValueAlignmentEvent,
    Discriminator(_event_discriminator),
]


# =============================================================================
# Event Registry Helpers
# =============================================================================


# Event name to type mapping for dynamic event creation
_EVENT_TYPE_MAP: dict[str, type[EventBase]] = {
    # Lifecycle
    "instance_started": InstanceStarted,
    "instance_disposed": InstanceDisposed,
    # Tool events
    "tool_invoked": ToolInvoked,
    "tool_completed": ToolCompleted,
    "tool_error": ToolError,
    "tool_blocked": ToolBlocked,
    "tool_timeout": ToolTimeout,
    # Turn events
    "turn_started": TurnStarted,
    "turn_completed": TurnCompleted,
    "turn_failed": TurnFailed,
    # Context events
    "context_window_status": ContextWindowStatus,
    "compact_requested": CompactRequested,
    "plan_created": PlanCreated,
    "file_written": FileWritten,
    # Director events
    "director_started": DirectorStarted,
    "director_stopped": DirectorStopped,
    "director_paused": DirectorPaused,
    "director_resumed": DirectorResumed,
    "task_submitted": TaskSubmitted,
    "task_claimed": TaskClaimed,
    "task_started": TaskStarted,
    "task_completed": TaskCompleted,
    "task_failed": TaskFailed,
    "task_cancelled": TaskCancelled,
    "task_retry": TaskRetry,
    "task_progress": TaskProgress,
    "worker_spawned": WorkerSpawned,
    "worker_ready": WorkerReady,
    "worker_busy": WorkerBusy,
    "worker_stopping": WorkerStopping,
    "worker_stopped": WorkerStopped,
    "nag_reminder": NagReminder,
    "budget_exceeded": BudgetExceeded,
    # System events
    "system_error": SystemError,
    "settings_changed": SettingsChanged,
    # Audit events
    "audit_completed": AuditCompleted,
    # Audit Extended events (Omniscient Audit)
    "llm_interaction": LLMInteractionEvent,
    "tool_execution": ToolExecutionEvent,
    "task_orchestration": TaskOrchestrationEvent,
    "agent_communication": AgentCommunicationEvent,
    "context_management": ContextManagementEvent,
    "budget_audit": BudgetAuditEvent,
    # Cognitive events
    "thinking_phase": ThinkingPhaseEvent,
    "reflection": ReflectionEvent,
    "evolution": EvolutionEvent,
    "belief_change": BeliefChangeEvent,
    "confidence_calibration": ConfidenceCalibrationEvent,
    "perception_completed": PerceptionCompletedEvent,
    "reasoning_completed": ReasoningCompletedEvent,
    "intent_detected": IntentDetectedEvent,
    "critical_thinking": CriticalThinkingEvent,
    "cautious_execution": CautiousExecutionEvent,
    "value_alignment": ValueAlignmentEvent,
}

# Static mapping from event type to category for get_events_by_category()
# This avoids using Pydantic internal __pydantic_generic_metadata__ API
_CATEGORY_BY_EVENT_TYPE: dict[type[EventBase], EventCategory] = {
    # Lifecycle
    InstanceStarted: EventCategory.LIFECYCLE,
    InstanceDisposed: EventCategory.LIFECYCLE,
    # Tool events
    ToolInvoked: EventCategory.TOOL,
    ToolCompleted: EventCategory.TOOL,
    ToolError: EventCategory.TOOL,
    ToolBlocked: EventCategory.TOOL,
    ToolTimeout: EventCategory.TOOL,
    # Turn events
    TurnStarted: EventCategory.TURN,
    TurnCompleted: EventCategory.TURN,
    TurnFailed: EventCategory.TURN,
    # Context events
    ContextWindowStatus: EventCategory.CONTEXT,
    CompactRequested: EventCategory.CONTEXT,
    PlanCreated: EventCategory.CONTEXT,
    FileWritten: EventCategory.CONTEXT,
    # Director events
    DirectorStarted: EventCategory.DIRECTOR,
    DirectorStopped: EventCategory.DIRECTOR,
    DirectorPaused: EventCategory.DIRECTOR,
    DirectorResumed: EventCategory.DIRECTOR,
    TaskSubmitted: EventCategory.DIRECTOR,
    TaskClaimed: EventCategory.DIRECTOR,
    TaskStarted: EventCategory.DIRECTOR,
    TaskCompleted: EventCategory.DIRECTOR,
    TaskFailed: EventCategory.DIRECTOR,
    TaskCancelled: EventCategory.DIRECTOR,
    TaskRetry: EventCategory.DIRECTOR,
    WorkerSpawned: EventCategory.DIRECTOR,
    WorkerReady: EventCategory.DIRECTOR,
    WorkerBusy: EventCategory.DIRECTOR,
    WorkerStopping: EventCategory.DIRECTOR,
    WorkerStopped: EventCategory.DIRECTOR,
    NagReminder: EventCategory.DIRECTOR,
    BudgetExceeded: EventCategory.DIRECTOR,
    # System events
    SystemError: EventCategory.SYSTEM,
    # Audit events
    AuditCompleted: EventCategory.AUDIT,
    # Audit Extended events (Omniscient Audit)
    LLMInteractionEvent: EventCategory.AUDIT_EXTENDED,
    ToolExecutionEvent: EventCategory.AUDIT_EXTENDED,
    TaskOrchestrationEvent: EventCategory.AUDIT_EXTENDED,
    AgentCommunicationEvent: EventCategory.AUDIT_EXTENDED,
    ContextManagementEvent: EventCategory.AUDIT_EXTENDED,
    BudgetAuditEvent: EventCategory.AUDIT_EXTENDED,
    # Cognitive events
    ThinkingPhaseEvent: EventCategory.COGNITIVE,
    ReflectionEvent: EventCategory.COGNITIVE,
    EvolutionEvent: EventCategory.COGNITIVE,
    BeliefChangeEvent: EventCategory.COGNITIVE,
    ConfidenceCalibrationEvent: EventCategory.COGNITIVE,
    PerceptionCompletedEvent: EventCategory.COGNITIVE,
    ReasoningCompletedEvent: EventCategory.COGNITIVE,
    IntentDetectedEvent: EventCategory.COGNITIVE,
    CriticalThinkingEvent: EventCategory.COGNITIVE,
    CautiousExecutionEvent: EventCategory.COGNITIVE,
    ValueAlignmentEvent: EventCategory.COGNITIVE,
}


def get_event_type(event_name: str) -> type[EventBase] | None:
    """Get event class by event name."""
    return _EVENT_TYPE_MAP.get(event_name)


def get_all_event_names() -> list[str]:
    """Get all registered event names."""
    return list(_EVENT_TYPE_MAP.keys())


def get_events_by_category(category: EventCategory) -> list[type[EventBase]]:
    """Get all event types in a category.

    Args:
        category: The event category to filter by

    Returns:
        List of event types matching the category
    """
    return [event_type for event_type, cat in _CATEGORY_BY_EVENT_TYPE.items() if cat == category]


# =============================================================================
# Pydantic Model Rebuild (fix forward reference issues)
# =============================================================================
# Rebuild all event models to resolve forward references in the TypedEvent union.
# This must be called after all event types are fully defined.

_all_event_classes: list[type[EventBase]] = [
    # Lifecycle
    InstanceStarted,
    InstanceDisposed,
    # Tool events
    ToolInvoked,
    ToolCompleted,
    ToolError,
    ToolBlocked,
    ToolTimeout,
    # Turn events
    TurnStarted,
    TurnCompleted,
    TurnFailed,
    # Context events
    ContextWindowStatus,
    CompactRequested,
    PlanCreated,
    FileWritten,
    # Director events
    DirectorStarted,
    DirectorStopped,
    DirectorPaused,
    DirectorResumed,
    TaskSubmitted,
    TaskClaimed,
    TaskStarted,
    TaskCompleted,
    TaskFailed,
    TaskCancelled,
    TaskRetry,
    TaskProgress,
    WorkerSpawned,
    WorkerReady,
    WorkerBusy,
    WorkerStopping,
    WorkerStopped,
    NagReminder,
    BudgetExceeded,
    # System events
    SystemError,
    SettingsChanged,
    # Audit events
    AuditCompleted,
    # Audit Extended events (Omniscient Audit)
    LLMInteractionEvent,
    ToolExecutionEvent,
    TaskOrchestrationEvent,
    AgentCommunicationEvent,
    ContextManagementEvent,
    BudgetAuditEvent,
    # Cognitive events
    ThinkingPhaseEvent,
    ReflectionEvent,
    EvolutionEvent,
    BeliefChangeEvent,
    ConfidenceCalibrationEvent,
    PerceptionCompletedEvent,
    ReasoningCompletedEvent,
    IntentDetectedEvent,
    CriticalThinkingEvent,
    CautiousExecutionEvent,
    ValueAlignmentEvent,
]

for _event_cls in _all_event_classes:
    with contextlib.suppress(Exception):
        _event_cls.model_rebuild()
