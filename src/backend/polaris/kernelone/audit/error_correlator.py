"""Error Correlation Engine — links KernelAuditEvent into causal chains."""

from __future__ import annotations

import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from .error_correlator_rules import (
    BUILTIN_RULES,
    CorrelationRule,
    CorrelationType,
    EventTypeMatchRule,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .contracts import KernelAuditEvent

logger = logging.getLogger(__name__)


class FailureClass(str, Enum):
    TOOL_EXECUTION_FAILURE = "tool_execution_failure"
    LLM_CALL_FAILURE = "llm_call_failure"
    POLICY_BLOCKED = "policy_blocked"
    SECURITY_VIOLATION = "security_violation"
    VERIFICATION_FAILURE = "verification_failure"
    TASK_FAILURE = "task_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    UNKNOWN = "unknown"


# Map event_type → FailureClass
_EVENT_TYPE_TO_FAILURE: dict[str, FailureClass] = {
    "security_violation": FailureClass.SECURITY_VIOLATION,
    "policy_check": FailureClass.POLICY_BLOCKED,
    "llm_call": FailureClass.LLM_CALL_FAILURE,
    "verification": FailureClass.VERIFICATION_FAILURE,
    "tool_execution": FailureClass.TOOL_EXECUTION_FAILURE,
    "task_failed": FailureClass.TASK_FAILURE,
}


def _classify_event(event: KernelAuditEvent) -> FailureClass:
    """Classify an event into a failure category."""
    event_type_val = event.event_type.value
    fc = _EVENT_TYPE_TO_FAILURE.get(event_type_val)
    if fc is not None:
        return fc
    result = str(event.action.get("result") or "").lower()
    if result == "failure":
        return FailureClass.UNKNOWN
    return FailureClass.UNKNOWN


_RESOLUTION_HINTS: dict[FailureClass, str] = {
    FailureClass.TOOL_EXECUTION_FAILURE: "Tool execution failed. Check tool configuration and permissions. Verify npx allowlist if applicable.",
    FailureClass.LLM_CALL_FAILURE: "LLM call failed. Inspect LLM provider logs. Check model availability and API key validity.",
    FailureClass.POLICY_BLOCKED: "Policy blocked the operation. Review policy configuration for this workspace.",
    FailureClass.SECURITY_VIOLATION: "SECURITY_VIOLATION detected. Review security logs for details immediately.",
    FailureClass.VERIFICATION_FAILURE: "Operation verification failed. Check constraint configuration and preconditions.",
    FailureClass.TASK_FAILURE: "Task failed. Check upstream events for root cause.",
    FailureClass.RESOURCE_EXHAUSTION: "Resource limit reached. Consider increasing timeout or quota.",
    FailureClass.UNKNOWN: "Unknown failure class. Review audit logs for additional context.",
}


def _map_to_resolution_hint(fc: FailureClass) -> str:
    return _RESOLUTION_HINTS.get(fc, _RESOLUTION_HINTS[FailureClass.UNKNOWN])


class ErrorCorrelationResult(dict):
    """Result of error correlation query."""

    __slots__ = (
        "affected_downstream",
        "confidence",
        "correlation_type",
        "primary_cause",
        "resolution_hint",
        "upstream_events",
    )

    def __init__(
        self,
        primary_cause: FailureClass | None,
        upstream_events: list[KernelAuditEvent],
        affected_downstream: list[KernelAuditEvent],
        resolution_hint: str,
        confidence: float,
        correlation_type: str | None,
    ) -> None:
        super().__init__(
            primary_cause=primary_cause.value if primary_cause else None,
            upstream_events=[e.to_dict() for e in upstream_events],
            affected_downstream=[e.to_dict() for e in affected_downstream],
            resolution_hint=resolution_hint,
            confidence=confidence,
            correlation_type=correlation_type,
        )
        object.__setattr__(self, "primary_cause", primary_cause)
        object.__setattr__(self, "upstream_events", upstream_events)
        object.__setattr__(self, "affected_downstream", affected_downstream)
        object.__setattr__(self, "resolution_hint", resolution_hint)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "correlation_type", correlation_type)


class ErrorCorrelator:
    """Links KernelAuditEvent instances into causal chains."""

    def __init__(
        self,
        max_upstream: int = 5,
        max_downstream: int = 10,
        rules: list[CorrelationRule] | None = None,
    ) -> None:
        self._max_upstream = max_upstream
        self._max_downstream = max_downstream
        self._rules: list[CorrelationRule] = list(rules) if rules is not None else list(BUILTIN_RULES)

    def register_rule(self, rule: CorrelationRule) -> None:
        self._rules.append(rule)

    def correlate(
        self,
        task_id: str,
        error_event: KernelAuditEvent,
        all_events: Sequence[KernelAuditEvent] | None = None,
    ) -> ErrorCorrelationResult:
        """Build error correlation chain for an error event."""
        events = list(all_events) if all_events else []
        task_events = [e for e in events if str(e.task.get("task_id") or "") == task_id]

        # Find upstream events
        upstream = self._find_upstream(task_id, error_event, task_events)

        # Find downstream events
        downstream = self._find_downstream(task_id, error_event, task_events)

        # Determine primary cause
        primary_cause, corr_type = self._determine_primary_cause(error_event, upstream)
        confidence = self._compute_confidence(primary_cause, upstream, corr_type)
        resolution = _map_to_resolution_hint(primary_cause) if primary_cause else "No root cause identified."

        return ErrorCorrelationResult(
            primary_cause=primary_cause,
            upstream_events=upstream[: self._max_upstream],
            affected_downstream=downstream[: self._max_downstream],
            resolution_hint=resolution,
            confidence=confidence,
            correlation_type=corr_type.value if corr_type else None,
        )

    def _find_upstream(
        self,
        task_id: str,
        error_event: KernelAuditEvent,
        events: Sequence[KernelAuditEvent],
    ) -> list[KernelAuditEvent]:
        """Find events that preceded the error_event on the same task."""
        upstream: list[KernelAuditEvent] = []
        cutoff = error_event.timestamp - timedelta(hours=24)
        for e in events:
            if e.timestamp >= error_event.timestamp:
                continue
            if e.timestamp < cutoff:
                break
            if e.event_id == error_event.event_id:
                continue
            upstream.append(e)
        upstream.sort(key=lambda x: x.timestamp, reverse=True)
        return upstream

    def _find_downstream(
        self,
        task_id: str,
        error_event: KernelAuditEvent,
        events: Sequence[KernelAuditEvent],
    ) -> list[KernelAuditEvent]:
        """Find events affected by the error_event on the same task."""
        downstream: list[KernelAuditEvent] = []
        cutoff = error_event.timestamp + timedelta(hours=24)
        for e in events:
            if e.timestamp <= error_event.timestamp:
                continue
            if e.timestamp > cutoff:
                break
            if e.event_id == error_event.event_id:
                continue
            downstream.append(e)
        downstream.sort(key=lambda x: x.timestamp)
        return downstream

    def _determine_primary_cause(
        self,
        error_event: KernelAuditEvent,
        upstream: list[KernelAuditEvent],
    ) -> tuple[FailureClass | None, CorrelationType | None]:
        """Find the primary cause from matched rules."""
        matched_rules: list[tuple[CorrelationRule, float]] = []

        for rule in self._rules:
            if not rule.matches(error_event):
                continue
            matched_rules.append((rule, rule.weight))

        if not matched_rules:
            # Fallback: classify by event type
            fc = _classify_event(error_event)
            return fc, None

        # Pick highest weight rule
        best_rule, best_weight = max(matched_rules, key=lambda x: x[1])

        # Look upstream for enabling cause if this is CAUSAL type
        fc = _classify_event(error_event)
        if (
            isinstance(best_rule, EventTypeMatchRule)
            and best_rule.correlation_type == CorrelationType.CAUSAL
            and upstream
        ):
            # Upgrade to upstream cause if it matches a higher-weight rule
            for u_event in upstream[:3]:
                for rule in self._rules:
                    if rule.matches(u_event) and rule.weight > best_weight:
                        fc = _classify_event(u_event)
                        best_weight = rule.weight
                        best_rule = rule
                        break

        return fc, best_rule.correlation_type

    def _compute_confidence(
        self,
        primary_cause: FailureClass | None,
        upstream: list[KernelAuditEvent],
        corr_type: CorrelationType | None,
    ) -> float:
        """Compute confidence score 0.0-1.0."""
        if primary_cause is None:
            return 0.0

        base = 0.5

        if corr_type == CorrelationType.CAUSAL:
            base = 0.75
        elif corr_type == CorrelationType.CORRELATED:
            base = 0.5
        elif corr_type == CorrelationType.ENABLING:
            base = 0.65

        # Boost for upstream events
        if upstream:
            base = min(1.0, base + 0.05 * len(upstream[:3]))

        # SECURITY_VIOLATION is always high confidence
        if primary_cause == FailureClass.SECURITY_VIOLATION:
            base = 1.0

        # POLICY_BLOCKED is high confidence
        if primary_cause == FailureClass.POLICY_BLOCKED:
            base = max(base, 0.95)

        return round(base, 2)
