"""Typed Event Schemas for KernelOne.

Design principles:
1. Zod-style discriminated union using Pydantic discriminated_union
2. Each event has explicit name, version, and payload schema
3. Schema evolution via versioned events

Reference: OpenCode packages/opencode/src/bus/bus-event.ts

This package is a lossless module->package split of the original
``schemas.py``. Every symbol that was importable from the old module path
(``polaris.kernelone.events.typed.schemas``) remains importable here, and the
union/registry tail plus the Pydantic forward-reference resolution loop are
reconstructed verbatim AFTER all event models are imported.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

from ._base import (
    EventBase,
    EventCategory,
    EventPayload,
    ToolErrorKind,
)
from ._cognitive import (
    BeliefChangeEvent,
    BeliefChangePayload,
    CautiousExecutionEvent,
    CautiousExecutionPayload,
    ConfidenceCalibrationEvent,
    ConfidenceCalibrationPayload,
    CriticalThinkingEvent,
    CriticalThinkingPayload,
    EvolutionEvent,
    EvolutionPayload,
    IntentDetectedEvent,
    IntentDetectedPayload,
    PerceptionCompletedEvent,
    PerceptionCompletedPayload,
    ReasoningCompletedEvent,
    ReasoningCompletedPayload,
    ReflectionEvent,
    ReflectionPayload,
    ThinkingPhaseEvent,
    ThinkingPhasePayload,
    ValueAlignmentEvent,
    ValueAlignmentPayload,
)
from ._director_task_worker import (
    BudgetExceeded,
    BudgetExceededPayload,
    DirectorPaused,
    DirectorPausedPayload,
    DirectorResumed,
    DirectorResumedPayload,
    DirectorStarted,
    DirectorStartedPayload,
    DirectorStopped,
    DirectorStoppedPayload,
    NagReminder,
    NagReminderPayload,
    TaskCancelled,
    TaskCancelledPayload,
    TaskClaimed,
    TaskClaimedPayload,
    TaskCompleted,
    TaskCompletedPayload,
    TaskFailed,
    TaskFailedPayload,
    TaskProgress,
    TaskProgressPayload,
    TaskRetry,
    TaskRetryPayload,
    TaskStarted,
    TaskStartedPayload,
    TaskSubmitted,
    TaskSubmittedPayload,
    WorkerBusy,
    WorkerBusyPayload,
    WorkerReady,
    WorkerReadyPayload,
    WorkerSpawned,
    WorkerSpawnedPayload,
    WorkerStopped,
    WorkerStoppedPayload,
    WorkerStopping,
    WorkerStoppingPayload,
)
from ._lifecycle_tool_turn import (
    InstanceDisposed,
    InstanceDisposedPayload,
    InstanceStarted,
    InstanceStartedPayload,
    ToolBlocked,
    ToolBlockedPayload,
    ToolCompleted,
    ToolCompletedPayload,
    ToolError,
    ToolErrorPayload,
    ToolInvoked,
    ToolInvokedPayload,
    ToolTimeout,
    ToolTimeoutPayload,
    TurnCompleted,
    TurnCompletedPayload,
    TurnFailed,
    TurnFailedPayload,
    TurnStarted,
    TurnStartedPayload,
)
from ._system_audit_context import (
    AgentCommunicationEvent,
    AgentCommunicationPayload,
    AuditCompleted,
    AuditCompletedPayload,
    BudgetAuditEvent,
    BudgetAuditPayload,
    CompactRequested,
    CompactRequestedPayload,
    ContextManagementEvent,
    ContextManagementPayload,
    ContextWindowStatus,
    ContextWindowStatusPayload,
    FileWritten,
    FileWrittenPayload,
    LLMInteractionEvent,
    LLMInteractionPayload,
    PlanCreated,
    PlanCreatedPayload,
    SettingsChanged,
    SettingsChangedPayload,
    SystemError,
    SystemErrorPayload,
    TaskOrchestrationEvent,
    TaskOrchestrationPayload,
    ToolExecutionEvent,
    ToolExecutionPayload,
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


__all__ = [
    "AgentCommunicationEvent",
    "AgentCommunicationPayload",
    "AuditCompleted",
    "AuditCompletedPayload",
    "BeliefChangeEvent",
    "BeliefChangePayload",
    "BudgetAuditEvent",
    "BudgetAuditPayload",
    "BudgetExceeded",
    "BudgetExceededPayload",
    "CautiousExecutionEvent",
    "CautiousExecutionPayload",
    "CompactRequested",
    "CompactRequestedPayload",
    "ConfidenceCalibrationEvent",
    "ConfidenceCalibrationPayload",
    "ContextManagementEvent",
    "ContextManagementPayload",
    "ContextWindowStatus",
    "ContextWindowStatusPayload",
    "CriticalThinkingEvent",
    "CriticalThinkingPayload",
    "DirectorPaused",
    "DirectorPausedPayload",
    "DirectorResumed",
    "DirectorResumedPayload",
    "DirectorStarted",
    "DirectorStartedPayload",
    "DirectorStopped",
    "DirectorStoppedPayload",
    "EventBase",
    "EventCategory",
    "EventPayload",
    "EvolutionEvent",
    "EvolutionPayload",
    "FileWritten",
    "FileWrittenPayload",
    "InstanceDisposed",
    "InstanceDisposedPayload",
    "InstanceStarted",
    "InstanceStartedPayload",
    "IntentDetectedEvent",
    "IntentDetectedPayload",
    "LLMInteractionEvent",
    "LLMInteractionPayload",
    "NagReminder",
    "NagReminderPayload",
    "PerceptionCompletedEvent",
    "PerceptionCompletedPayload",
    "PlanCreated",
    "PlanCreatedPayload",
    "ReasoningCompletedEvent",
    "ReasoningCompletedPayload",
    "ReflectionEvent",
    "ReflectionPayload",
    "SettingsChanged",
    "SettingsChangedPayload",
    "SystemError",
    "SystemErrorPayload",
    "TaskCancelled",
    "TaskCancelledPayload",
    "TaskClaimed",
    "TaskClaimedPayload",
    "TaskCompleted",
    "TaskCompletedPayload",
    "TaskFailed",
    "TaskFailedPayload",
    "TaskOrchestrationEvent",
    "TaskOrchestrationPayload",
    "TaskProgress",
    "TaskProgressPayload",
    "TaskRetry",
    "TaskRetryPayload",
    "TaskStarted",
    "TaskStartedPayload",
    "TaskSubmitted",
    "TaskSubmittedPayload",
    "ThinkingPhaseEvent",
    "ThinkingPhasePayload",
    "ToolBlocked",
    "ToolBlockedPayload",
    "ToolCompleted",
    "ToolCompletedPayload",
    "ToolError",
    "ToolErrorKind",
    "ToolErrorPayload",
    "ToolExecutionEvent",
    "ToolExecutionPayload",
    "ToolInvoked",
    "ToolInvokedPayload",
    "ToolTimeout",
    "ToolTimeoutPayload",
    "TurnCompleted",
    "TurnCompletedPayload",
    "TurnFailed",
    "TurnFailedPayload",
    "TurnStarted",
    "TurnStartedPayload",
    "TypedEvent",
    "ValueAlignmentEvent",
    "ValueAlignmentPayload",
    "WorkerBusy",
    "WorkerBusyPayload",
    "WorkerReady",
    "WorkerReadyPayload",
    "WorkerSpawned",
    "WorkerSpawnedPayload",
    "WorkerStopped",
    "WorkerStoppedPayload",
    "WorkerStopping",
    "WorkerStoppingPayload",
    "get_all_event_names",
    "get_event_type",
    "get_events_by_category",
]
