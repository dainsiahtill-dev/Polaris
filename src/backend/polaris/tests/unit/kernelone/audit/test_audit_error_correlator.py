"""Tests for polaris.kernelone.audit.error_correlator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from polaris.kernelone.audit.error_correlator import (
    ErrorCorrelationResult,
    ErrorCorrelator,
    FailureClass,
    _classify_event,
    _map_to_resolution_hint,
)
from polaris.kernelone.audit.error_correlator_rules import (
    CorrelationType,
    EventTypeMatchRule,
)


class TestFailureClass:
    def test_values(self) -> None:
        assert FailureClass.TOOL_EXECUTION_FAILURE == "tool_execution_failure"
        assert FailureClass.LLM_CALL_FAILURE == "llm_call_failure"
        assert FailureClass.POLICY_BLOCKED == "policy_blocked"
        assert FailureClass.SECURITY_VIOLATION == "security_violation"
        assert FailureClass.VERIFICATION_FAILURE == "verification_failure"
        assert FailureClass.TASK_FAILURE == "task_failure"
        assert FailureClass.RESOURCE_EXHAUSTION == "resource_exhaustion"
        assert FailureClass.UNKNOWN == "unknown"


class TestClassifyEvent:
    def test_security_violation(self) -> None:
        event = MagicMock()
        event.event_type.value = "security_violation"
        event.action = {}
        assert _classify_event(event) == FailureClass.SECURITY_VIOLATION

    def test_policy_blocked(self) -> None:
        event = MagicMock()
        event.event_type.value = "policy_check"
        event.action = {}
        assert _classify_event(event) == FailureClass.POLICY_BLOCKED

    def test_llm_call_failure(self) -> None:
        event = MagicMock()
        event.event_type.value = "llm_call"
        event.action = {}
        assert _classify_event(event) == FailureClass.LLM_CALL_FAILURE

    def test_tool_execution_failure(self) -> None:
        event = MagicMock()
        event.event_type.value = "tool_execution"
        event.action = {}
        assert _classify_event(event) == FailureClass.TOOL_EXECUTION_FAILURE

    def test_task_failure(self) -> None:
        event = MagicMock()
        event.event_type.value = "task_failed"
        event.action = {}
        assert _classify_event(event) == FailureClass.TASK_FAILURE

    def test_unknown_with_failure_result(self) -> None:
        event = MagicMock()
        event.event_type.value = "other"
        event.action = {"result": "failure"}
        assert _classify_event(event) == FailureClass.UNKNOWN

    def test_unknown_default(self) -> None:
        event = MagicMock()
        event.event_type.value = "other"
        event.action = {"result": "success"}
        assert _classify_event(event) == FailureClass.UNKNOWN


class TestMapToResolutionHint:
    def test_all_classes_have_hints(self) -> None:
        for fc in FailureClass:
            hint = _map_to_resolution_hint(fc)
            assert isinstance(hint, str)
            assert len(hint) > 0


class TestErrorCorrelationResult:
    def test_dict_behavior(self) -> None:
        event = MagicMock()
        event.to_dict.return_value = {"id": "e1"}
        result = ErrorCorrelationResult(
            primary_cause=FailureClass.TOOL_EXECUTION_FAILURE,
            upstream_events=[event],
            affected_downstream=[event],
            resolution_hint="fix it",
            confidence=0.85,
            correlation_type="causal",
        )
        assert result["primary_cause"] == "tool_execution_failure"
        assert result["confidence"] == 0.85
        assert result["resolution_hint"] == "fix it"
        assert result["correlation_type"] == "causal"
        # Attribute access
        assert result.primary_cause == FailureClass.TOOL_EXECUTION_FAILURE
        assert result.confidence == 0.85


class TestErrorCorrelator:
    def _make_event(
        self,
        event_id: str,
        event_type: str = "tool_execution",
        timestamp: datetime | None = None,
        task_id: str = "t1",
        action: dict | None = None,
    ) -> MagicMock:
        event = MagicMock()
        event.event_id = event_id
        event.event_type.value = event_type
        event.timestamp = timestamp or datetime.now(timezone.utc)
        event.task = {"task_id": task_id}
        event.action = action or {}
        event.to_dict.return_value = {"id": event_id}
        return event

    def test_correlate_empty_events(self) -> None:
        correlator = ErrorCorrelator()
        error_event = self._make_event("e1", "task_failed")
        result = correlator.correlate("t1", error_event, all_events=[])
        assert result.primary_cause == FailureClass.TASK_FAILURE
        assert result.upstream_events == []
        assert result.affected_downstream == []

    def test_correlate_finds_upstream(self) -> None:
        correlator = ErrorCorrelator()
        now = datetime.now(timezone.utc)
        upstream = self._make_event("e0", "tool_execution", timestamp=now - __import__("datetime").timedelta(minutes=1))
        error_event = self._make_event("e1", "task_failed", timestamp=now)
        result = correlator.correlate("t1", error_event, all_events=[upstream, error_event])
        assert len(result.upstream_events) == 1
        assert result.upstream_events[0].event_id == "e0"

    def test_correlate_finds_downstream(self) -> None:
        correlator = ErrorCorrelator()
        now = datetime.now(timezone.utc)
        error_event = self._make_event("e1", "task_failed", timestamp=now)
        downstream = self._make_event("e2", "verification", timestamp=now + __import__("datetime").timedelta(minutes=1))
        result = correlator.correlate("t1", error_event, all_events=[error_event, downstream])
        assert len(result.affected_downstream) == 1
        assert result.affected_downstream[0].event_id == "e2"

    def test_correlate_limits_upstream(self) -> None:
        correlator = ErrorCorrelator(max_upstream=2)
        now = datetime.now(timezone.utc)
        events = [
            self._make_event(f"e{i}", "tool_execution", timestamp=now - __import__("datetime").timedelta(minutes=i))
            for i in range(1, 10)
        ]
        error_event = self._make_event("e_err", "task_failed", timestamp=now)
        result = correlator.correlate("t1", error_event, all_events=events + [error_event])
        assert len(result.upstream_events) <= 2

    def test_correlate_limits_downstream(self) -> None:
        correlator = ErrorCorrelator(max_downstream=2)
        now = datetime.now(timezone.utc)
        error_event = self._make_event("e_err", "task_failed", timestamp=now)
        events = [
            self._make_event(f"e{i}", "verification", timestamp=now + __import__("datetime").timedelta(minutes=i))
            for i in range(1, 10)
        ]
        result = correlator.correlate("t1", error_event, all_events=[error_event] + events)
        assert len(result.affected_downstream) <= 2

    def test_correlate_with_rule_match(self) -> None:
        rule = EventTypeMatchRule(
            name="tool_failure",
            description="tool failed",
            correlation_type=CorrelationType.CAUSAL,
            weight=0.9,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        correlator = ErrorCorrelator(rules=[rule])
        error_event = self._make_event("e1", "tool_execution", action={"result": "failure"})
        result = correlator.correlate("t1", error_event, all_events=[error_event])
        assert result.confidence >= 0.75
        assert result.correlation_type == "causal"

    def test_register_rule(self) -> None:
        correlator = ErrorCorrelator(rules=[])
        rule = EventTypeMatchRule(
            name="test",
            description="d",
            correlation_type=CorrelationType.CORRELATED,
            trigger_event_type="x",
        )
        correlator.register_rule(rule)
        assert len(correlator._rules) == 1

    def test_security_violation_high_confidence(self) -> None:
        correlator = ErrorCorrelator()
        error_event = self._make_event("e1", "security_violation")
        result = correlator.correlate("t1", error_event, all_events=[error_event])
        assert result.confidence == 1.0

    def test_policy_blocked_high_confidence(self) -> None:
        correlator = ErrorCorrelator()
        error_event = self._make_event("e1", "policy_check")
        result = correlator.correlate("t1", error_event, all_events=[error_event])
        assert result.confidence >= 0.95

    def test_no_primary_cause_zero_confidence(self) -> None:
        correlator = ErrorCorrelator()
        result = correlator._compute_confidence(None, [], None)
        assert result == 0.0
