"""System, Audit, Audit-Extended and Context event triplets.

Bodies moved verbatim from the original
``polaris/kernelone/events/typed/schemas.py`` module to preserve behavior.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import EventBase, EventCategory, EventPayload

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
# Context Window Status Event
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
