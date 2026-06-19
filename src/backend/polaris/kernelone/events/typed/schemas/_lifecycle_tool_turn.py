"""Lifecycle, Tool and Turn event triplets.

Bodies moved verbatim from the original
``polaris/kernelone/events/typed/schemas.py`` module to preserve behavior.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ._base import EventBase, EventCategory, EventPayload, ToolErrorKind

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
