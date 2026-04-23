"""UEP v2.0 to TypedEvent Converter.

Converts UEP v2.0 payloads to TypedEvent instances for dual-write
through TypedEventBusAdapter, enabling real-time subscriptions while
maintaining MessageBus-based persistence.

Architecture:
    UEP payload -> UEPToTypedEventConverter -> TypedEvent
                                                          |
                                            TypedEventBusAdapter.emit_to_both()
                                                          |
                               +---------------------------+---------------------------+
                               |                           |                           |
                               v                           v                           v
                        EventRegistry                   ...                          ...

Event Type Mapping Convention:
    - UEP event_type strings (from constants.py) are canonical runtime event types
    - TypedEvent class names are semantic concepts (e.g., ToolInvoked = tool was invoked)
    - TypedEvent.event_name is the runtime identifier (e.g., "tool_invoked")
    - The converter maps canonical UEP event_type -> semantic TypedEvent class

    Example mapping:
        UEP event_type: "tool_call" (constants.EVENT_TYPE_TOOL_CALL)
            -> TypedEvent class: ToolInvoked (semantic: "tool was invoked")
            -> TypedEvent.event_name: "tool_invoked" (runtime identifier)
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_CONTENT_CHUNK,
    EVENT_TYPE_ERROR,
    EVENT_TYPE_LLM_CALL_END,
    EVENT_TYPE_LLM_CALL_START,
    EVENT_TYPE_LLM_ERROR,
    EVENT_TYPE_LLM_RETRY,
    EVENT_TYPE_THINKING_CHUNK,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_ERROR,
    EVENT_TYPE_TOOL_RESULT,
)

from .topics import (
    TOPIC_RUNTIME_AUDIT,
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_STREAM,
)

if TYPE_CHECKING:
    from polaris.kernelone.events.typed import TypedEvent

logger = logging.getLogger(__name__)

# =============================================================================
# Mapping Tables
# =============================================================================

# UEP event_type (from constants.py) -> TypedEvent class name
# NOTE: This maps canonical UEP event_type to semantic TypedEvent class.
# The resulting TypedEvent.event_name is the TypedEvent's internal identifier.
_UEP_STREAM_TO_TYPED: dict[str, str] = {
    # Canonical constants from constants.py
    EVENT_TYPE_TOOL_CALL: "ToolInvoked",
    EVENT_TYPE_TOOL_RESULT: "ToolCompleted",
    EVENT_TYPE_TOOL_ERROR: "ToolError",
    EVENT_TYPE_CONTENT_CHUNK: "TurnStarted",
    EVENT_TYPE_THINKING_CHUNK: "TurnStarted",
    EVENT_TYPE_COMPLETE: "TurnCompleted",
    EVENT_TYPE_ERROR: "TurnFailed",
}

# UEP lifecycle event_type -> TypedEvent class name
_UEP_LIFECYCLE_TO_TYPED: dict[str, str] = {
    # Canonical constants from constants.py
    EVENT_TYPE_LLM_CALL_START: "InstanceStarted",
    EVENT_TYPE_LLM_CALL_END: "InstanceDisposed",
    EVENT_TYPE_LLM_ERROR: "SystemError",
    EVENT_TYPE_LLM_RETRY: "TaskRetry",

    # Aliases explicitly published by events.py (LLM Call Lifecycle)
    "call_start": "InstanceStarted",
    "call_end": "InstanceDisposed",
    "call_error": "SystemError",
    "call_retry": "TaskRetry",
}


# =============================================================================
# Converter Class
# =============================================================================


class UEPToTypedEventConverter:
    """Converts UEP v2.0 payloads to TypedEvent instances.

    This converter bridges the UEP v2.0 event system with the
    TypedEvent system, enabling dual-write through TypedEventBusAdapter.

    Usage:
        converter = UEPToTypedEventConverter()
        typed_event = converter.convert_stream_event(
            payload={
                "topic": "runtime.event.stream",
                "event_type": "tool_call",
                "run_id": "run-123",
                "role": "director",
                "payload": {"tool": "read_file", "args": {}},
            }
        )
        if typed_event:
            await adapter.emit_to_both(typed_event)
    """

    def convert(self, payload: dict[str, Any]) -> TypedEvent | None:
        """Convert any UEP payload to TypedEvent.

        Args:
            payload: UEP event payload dict with keys:
                - topic: str (runtime.event.stream/llm/fingerprint/audit)
                - event_type: str
                - run_id: str
                - workspace: str
                - role: str
                - payload: dict (event-specific data)
                - turn_id: str (optional)
                - timestamp: str (optional)
                - metadata: dict (optional)

        Returns:
            TypedEvent instance if conversion succeeds, None otherwise.
        """
        topic = payload.get("topic", "")

        if topic == TOPIC_RUNTIME_STREAM:
            return self.convert_stream_event(payload)
        elif topic == TOPIC_RUNTIME_LLM:
            return self.convert_llm_event(payload)
        elif topic == TOPIC_RUNTIME_FINGERPRINT:
            return self.convert_fingerprint_event(payload)
        elif topic == TOPIC_RUNTIME_AUDIT:
            return self.convert_audit_event(payload)
        else:
            logger.warning(f"Unknown UEP topic: {topic}")
            return None

    def convert_stream_event(self, payload: dict[str, Any]) -> TypedEvent | None:
        """Convert UEP stream event to TypedEvent.

        Mappings:
            - tool_call -> ToolInvoked
            - tool_result -> ToolCompleted
            - tool_error -> ToolError
            - content_chunk / thinking_chunk -> TurnStarted
            - complete -> TurnCompleted
            - error -> TurnFailed

        Args:
            payload: UEP stream event payload.

        Returns:
            Corresponding TypedEvent or None.
        """
        event_type = payload.get("event_type", "")
        typed_class_name = _UEP_STREAM_TO_TYPED.get(event_type)

        if typed_class_name is None:
            logger.debug(f"No TypedEvent mapping for stream event_type: {event_type}")
            return None

        return self._create_typed_event(
            payload=payload,
            typed_class_name=typed_class_name,
            category="tool",
        )

    def convert_llm_event(self, payload: dict[str, Any]) -> TypedEvent | None:
        """Convert UEP LLM lifecycle event to TypedEvent.

        Mappings:
            - call_start -> InstanceStarted
            - call_end -> InstanceDisposed
            - call_error -> SystemError
            - call_retry -> TaskRetry

        Args:
            payload: UEP LLM lifecycle event payload.

        Returns:
            Corresponding TypedEvent or None.
        """
        event_type = payload.get("event_type", "")
        typed_class_name = _UEP_LIFECYCLE_TO_TYPED.get(event_type)

        if typed_class_name is None:
            logger.debug(f"No TypedEvent mapping for llm event_type: {event_type}")
            return None

        return self._create_typed_event(
            payload=payload,
            typed_class_name=typed_class_name,
            category="lifecycle",
        )

    def convert_fingerprint_event(self, payload: dict[str, Any]) -> TypedEvent | None:
        """Convert UEP fingerprint event to TypedEvent.

        Currently maps to PlanCreated as the closest semantic match.

        Args:
            payload: UEP fingerprint event payload.

        Returns:
            PlanCreated TypedEvent or None.
        """
        return self._create_typed_event(
            payload=payload,
            typed_class_name="PlanCreated",
            category="context",
        )

    def convert_audit_event(self, payload: dict[str, Any]) -> TypedEvent | None:
        """Convert UEP audit event to TypedEvent.

        Currently maps to AuditCompleted.

        Args:
            payload: UEP audit event payload.

        Returns:
            AuditCompleted TypedEvent or None.
        """
        return self._create_typed_event(
            payload=payload,
            typed_class_name="AuditCompleted",
            category="audit",
        )

    def _create_typed_event(
        self,
        payload: dict[str, Any],
        typed_class_name: str,
        category: str,
    ) -> TypedEvent | None:
        """Factory method to create TypedEvent from UEP payload.

        Args:
            payload: UEP event payload.
            typed_class_name: Name of TypedEvent class to create.
            category: Event category.

        Returns:
            TypedEvent instance or None.
        """
        # Lazy import to avoid circular dependency
        from polaris.kernelone.events.typed import (
            AuditCompleted,
            InstanceDisposed,
            InstanceStarted,
            PlanCreated,
            SystemError,
            TaskRetry,
            ToolCompleted,
            ToolError,
            ToolInvoked,
            TurnCompleted,
            TurnFailed,
            TurnStarted,
        )

        # Note: timestamp is parsed but not explicitly passed since
        # TypedEvent factory methods use their own default timestamps.
        # The correlation_id (turn_id) preserves event ordering context.

        run_id = str(payload.get("run_id", ""))
        workspace = str(payload.get("workspace", ""))
        role = str(payload.get("role", ""))
        event_payload = payload.get("payload", {})
        turn_id = str(payload.get("turn_id", "")) or None
        metadata = payload.get("metadata", {})

        try:
            if typed_class_name == "ToolInvoked":
                return ToolInvoked.create(
                    tool_name=event_payload.get("tool", "unknown"),
                    tool_call_id=event_payload.get("call_id", uuid.uuid4().hex),
                    arguments=event_payload.get("args", event_payload.get("arguments", {})),
                    execution_lane=event_payload.get("lane", "direct"),
                    run_id=run_id,
                    workspace=workspace,
                    correlation_id=turn_id,
                )

            elif typed_class_name == "ToolCompleted":
                return ToolCompleted.create(
                    tool_name=event_payload.get("tool", "unknown"),
                    tool_call_id=event_payload.get("call_id", uuid.uuid4().hex),
                    result=event_payload.get("result", event_payload.get("output")),
                    duration_ms=self._extract_duration_ms(event_payload),
                    output_size=self._estimate_output_size(event_payload),
                    run_id=run_id,
                    workspace=workspace,
                    correlation_id=turn_id,
                )

            elif typed_class_name == "ToolError":
                error_info = event_payload.get("error", {})
                error_msg = error_info.get("message") if isinstance(error_info, dict) else str(error_info)
                return ToolError.create(
                    tool_name=event_payload.get("tool", "unknown"),
                    tool_call_id=event_payload.get("call_id", uuid.uuid4().hex),
                    error=error_msg or "Unknown error",
                    error_type=self._classify_tool_error(error_msg),
                    stack_trace=error_info.get("traceback") if isinstance(error_info, dict) else None,
                    duration_ms=self._extract_duration_ms(event_payload),
                    run_id=run_id,
                    workspace=workspace,
                    correlation_id=turn_id,
                )

            elif typed_class_name == "TurnStarted":
                # UEP content_chunk maps to TurnStarted
                return TurnStarted.create(
                    turn_id=turn_id or uuid.uuid4().hex[:8],
                    agent=role or "unknown",
                    prompt=event_payload.get("content", ""),
                    tools=event_payload.get("available_tools"),
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "TurnCompleted":
                return TurnCompleted.create(
                    turn_id=turn_id or uuid.uuid4().hex[:8],
                    agent=role or "unknown",
                    tool_calls_count=event_payload.get("tool_calls_count", 0),
                    duration_ms=self._extract_duration_ms(event_payload),
                    tokens_used=metadata.get("usage", {}).get("total_tokens", 0),
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "TurnFailed":
                return TurnFailed.create(
                    turn_id=turn_id or uuid.uuid4().hex[:8],
                    agent=role or "unknown",
                    error=event_payload.get("error", "Unknown error"),
                    error_type=event_payload.get("error_type"),
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "InstanceStarted":
                return InstanceStarted.create(
                    instance_id=run_id or uuid.uuid4().hex,
                    instance_type=f"llm.{role}" if role else "llm",
                    config={
                        "model": metadata.get("model", "unknown"),
                        "provider": metadata.get("provider", "unknown"),
                    },
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "InstanceDisposed":
                return InstanceDisposed.create(
                    directory=workspace,
                    reason=str(payload.get("event_type", "unknown")),
                    duration_ms=self._extract_duration_ms(metadata),
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "SystemError":
                return SystemError.create(
                    error=event_payload.get("error", "LLM call error"),
                    component=f"llm.{role}" if role else "llm",
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "TaskRetry":
                # TaskRetry requires max_retries, default to DEFAULT_MAX_RETRIES
                max_retries = metadata.get("max_retries", DEFAULT_MAX_RETRIES)
                return TaskRetry.create(
                    task_id=event_payload.get("task_id", uuid.uuid4().hex),
                    attempt=metadata.get("attempt", 1),
                    max_retries=max_retries,
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "PlanCreated":
                # PlanCreated: plan_id maps to profile_id, target maps to bundle_id
                return PlanCreated.create(
                    plan_id=event_payload.get("profile_id", uuid.uuid4().hex),
                    target=event_payload.get("bundle_id", workspace),
                    run_id=run_id,
                    workspace=workspace,
                )

            elif typed_class_name == "AuditCompleted":
                # AuditCompleted: verdict at top level (UEP v2.0) or nested under
                # payload (legacy). Target is the role.
                verdict = event_payload.get("verdict") or payload.get("verdict", "pass")
                data_dict = event_payload.get("data") or payload.get("data", {})
                issue_count = data_dict.get("issue_count", 0) if isinstance(data_dict, dict) else 0
                return AuditCompleted.create(
                    audit_id=run_id or uuid.uuid4().hex,
                    target=role or "unknown",
                    verdict=verdict,
                    issue_count=issue_count,
                    run_id=run_id,
                    workspace=workspace,
                )

            else:
                logger.warning(f"Unknown TypedEvent class: {typed_class_name}")
                return None

        except (RuntimeError, ValueError) as exc:
            logger.error(f"Failed to create TypedEvent {typed_class_name}: {exc}")
            return None

    @staticmethod
    def _extract_duration_ms(data: dict[str, Any]) -> int | None:
        """Extract duration_ms from event data."""
        if isinstance(data, dict):
            if "duration_ms" in data:
                return int(data["duration_ms"])
            if "latency_ms" in data:
                return int(data["latency_ms"])
            if "elapsed_ms" in data:
                return int(data["elapsed_ms"])
        return None

    @staticmethod
    def _estimate_output_size(data: dict[str, Any]) -> int:
        """Estimate output size in bytes."""
        if isinstance(data, dict):
            result = data.get("result") or data.get("output") or ""
            if isinstance(result, str):
                return len(result.encode("utf-8"))
            if isinstance(result, dict):
                import json

                return len(json.dumps(result).encode("utf-8"))
        return 0

    @staticmethod
    def _classify_tool_error(error_msg: str | None) -> Any:
        """Classify tool error into ToolErrorKind."""
        from polaris.kernelone.events.typed import ToolErrorKind

        if not error_msg:
            return ToolErrorKind.UNKNOWN

        error_lower = error_msg.lower()
        if "timeout" in error_lower:
            return ToolErrorKind.TIMEOUT
        if "permission" in error_lower or "denied" in error_lower:
            return ToolErrorKind.PERMISSION
        if "not found" in error_lower or "does not exist" in error_lower:
            return ToolErrorKind.NOT_FOUND
        if "validation" in error_lower or "invalid" in error_lower:
            return ToolErrorKind.VALIDATION
        if "cancelled" in error_lower or "cancel" in error_lower:
            return ToolErrorKind.CANCELLED
        return ToolErrorKind.EXCEPTION


__all__ = ["UEPToTypedEventConverter"]
