"""Unit tests for polaris.kernelone.audit.error_correlator_rules."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType
from polaris.kernelone.audit.error_correlator_rules import (
    BUILTIN_RULES,
    CorrelationRule,
    CorrelationType,
    EventTypeMatchRule,
    get_rules,
)


def _make_event(event_type: KernelAuditEventType, result: str = "") -> KernelAuditEvent:
    return KernelAuditEvent(
        event_id="e1",
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        action={"result": result} if result else {},
    )


class TestCorrelationType:
    def test_values(self) -> None:
        assert CorrelationType.CAUSAL.value == "causal"
        assert CorrelationType.ENABLING.value == "enabling"
        assert CorrelationType.CORRELATED.value == "correlated"
        assert CorrelationType.MITIGATION.value == "mitigation"


class TestEventTypeMatchRule:
    def test_matches_event_type(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        event = _make_event(KernelAuditEventType.SECURITY_VIOLATION)
        assert rule.matches(event) is True

    def test_no_match_different_event_type(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        event = _make_event(KernelAuditEventType.TOOL_EXECUTION)
        assert rule.matches(event) is False

    def test_matches_with_result(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        assert rule.matches(_make_event(KernelAuditEventType.TOOL_EXECUTION, "failure")) is True
        assert rule.matches(_make_event(KernelAuditEventType.TOOL_EXECUTION, "success")) is False

    def test_no_trigger_result_allows_any(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="llm_call",
        )
        assert rule.matches(_make_event(KernelAuditEventType.LLM_CALL, "success")) is True


class TestBuiltinRules:
    def test_rules_populated(self) -> None:
        rules = get_rules()
        assert len(rules) > 0
        assert all(isinstance(r, CorrelationRule) for r in rules)

    def test_builtin_rules_cover_security(self) -> None:
        names = [r.name for r in BUILTIN_RULES]
        assert "security_violation_root" in names

    def test_builtin_rules_cover_tool_failure(self) -> None:
        names = [r.name for r in BUILTIN_RULES]
        assert "tool_then_task_failed" in names

    def test_get_rules_returns_copy(self) -> None:
        a = get_rules()
        b = get_rules()
        assert a is not b
        assert a == b
