"""Tests for error correlator rules."""

from __future__ import annotations

from datetime import datetime

import pytest
from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType
from polaris.kernelone.audit.error_correlator_rules import (
    BUILTIN_RULES,
    CorrelationRule,
    CorrelationType,
    EventTypeMatchRule,
    get_rules,
)


class TestCorrelationTypeEnum:
    """Tests for CorrelationType enum."""

    def test_causal_value(self) -> None:
        assert CorrelationType.CAUSAL == "causal"

    def test_enabling_value(self) -> None:
        assert CorrelationType.ENABLING == "enabling"

    def test_correlated_value(self) -> None:
        assert CorrelationType.CORRELATED == "correlated"

    def test_mitigation_value(self) -> None:
        assert CorrelationType.MITIGATION == "mitigation"

    def test_is_str_subclass(self) -> None:
        assert issubclass(CorrelationType, str)

    def test_members_count(self) -> None:
        assert len(list(CorrelationType)) == 4


class TestCorrelationRuleBase:
    """Tests for CorrelationRule base dataclass."""

    def test_dataclass_fields(self) -> None:
        rule = CorrelationRule(
            name="test_rule",
            description="Test description",
            correlation_type=CorrelationType.CAUSAL,
        )
        assert rule.name == "test_rule"
        assert rule.description == "Test description"
        assert rule.correlation_type == CorrelationType.CAUSAL
        assert rule.weight == 1.0
        assert rule.resolution_hint == ""

    def test_custom_weight(self) -> None:
        rule = CorrelationRule(
            name="weighted",
            description="Weighted rule",
            correlation_type=CorrelationType.CORRELATED,
            weight=0.5,
        )
        assert rule.weight == 0.5

    def test_custom_resolution_hint(self) -> None:
        rule = CorrelationRule(
            name="hinted",
            description="Hinted rule",
            correlation_type=CorrelationType.MITIGATION,
            resolution_hint="Check the logs",
        )
        assert rule.resolution_hint == "Check the logs"

    def test_matches_raises_not_implemented(self) -> None:
        rule = CorrelationRule(
            name="base",
            description="Base rule",
            correlation_type=CorrelationType.CAUSAL,
        )
        event = KernelAuditEvent(
            event_id="test-1",
            timestamp=datetime.now(),
            event_type=KernelAuditEventType.TOOL_EXECUTION,
        )
        with pytest.raises(NotImplementedError):
            rule.matches(event)


class TestEventTypeMatchRule:
    """Tests for EventTypeMatchRule implementation."""

    @pytest.fixture
    def security_event(self) -> KernelAuditEvent:
        return KernelAuditEvent(
            event_id="sec-1",
            timestamp=datetime.now(),
            event_type=KernelAuditEventType.SECURITY_VIOLATION,
            action={"result": "blocked"},
        )

    @pytest.fixture
    def tool_failure_event(self) -> KernelAuditEvent:
        return KernelAuditEvent(
            event_id="tool-1",
            timestamp=datetime.now(),
            event_type=KernelAuditEventType.TOOL_EXECUTION,
            action={"result": "failure"},
        )

    @pytest.fixture
    def tool_success_event(self) -> KernelAuditEvent:
        return KernelAuditEvent(
            event_id="tool-2",
            timestamp=datetime.now(),
            event_type=KernelAuditEventType.TOOL_EXECUTION,
            action={"result": "success"},
        )

    def test_matches_event_type_only(self, security_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="security",
            description="Security rule",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        assert rule.matches(security_event) is True

    def test_matches_event_type_and_result(self, tool_failure_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="tool_fail",
            description="Tool failure",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        assert rule.matches(tool_failure_event) is True

    def test_no_match_wrong_event_type(self, tool_failure_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="security",
            description="Security rule",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        assert rule.matches(tool_failure_event) is False

    def test_no_match_wrong_result(self, tool_success_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="tool_fail",
            description="Tool failure",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        assert rule.matches(tool_success_event) is False

    def test_no_trigger_event_type_matches_any(self, tool_failure_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="any_event",
            description="Any event",
            correlation_type=CorrelationType.CAUSAL,
            trigger_result="failure",
        )
        assert rule.matches(tool_failure_event) is True

    def test_no_trigger_result_matches_any_result(self, tool_success_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="any_tool",
            description="Any tool execution",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
        )
        assert rule.matches(tool_success_event) is True

    def test_no_triggers_matches_any_event(self, tool_success_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="catch_all",
            description="Catch all",
            correlation_type=CorrelationType.CAUSAL,
        )
        assert rule.matches(tool_success_event) is True

    def test_empty_action_result_no_match(self, tool_failure_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="tool_fail",
            description="Tool failure",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        event_no_action = KernelAuditEvent(
            event_id="tool-3",
            timestamp=datetime.now(),
            event_type=KernelAuditEventType.TOOL_EXECUTION,
        )
        assert rule.matches(event_no_action) is False

    def test_none_trigger_result_no_match(self, tool_failure_event: KernelAuditEvent) -> None:
        rule = EventTypeMatchRule(
            name="tool_fail",
            description="Tool failure",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result=None,
        )
        assert rule.matches(tool_failure_event) is True


class TestBuiltinRules:
    """Tests for built-in rules registry."""

    def test_builtin_rules_not_empty(self) -> None:
        assert len(BUILTIN_RULES) > 0

    def test_builtin_rules_count(self) -> None:
        assert len(BUILTIN_RULES) == 5

    def test_security_violation_rule_exists(self) -> None:
        rule = next((r for r in BUILTIN_RULES if r.name == "security_violation_root"), None)
        assert rule is not None
        assert rule.correlation_type == CorrelationType.CAUSAL

    def test_tool_then_task_failed_rule_exists(self) -> None:
        rule = next((r for r in BUILTIN_RULES if r.name == "tool_then_task_failed"), None)
        assert rule is not None
        assert rule.trigger_event_type == "tool_execution"
        assert rule.trigger_result == "failure"

    def test_policy_blocked_rule_exists(self) -> None:
        rule = next((r for r in BUILTIN_RULES if r.name == "policy_blocked"), None)
        assert rule is not None
        assert rule.trigger_event_type == "policy_check"

    def test_llm_call_failure_rule_exists(self) -> None:
        rule = next((r for r in BUILTIN_RULES if r.name == "llm_call_failure"), None)
        assert rule is not None
        assert rule.trigger_event_type == "llm_call"
        assert rule.trigger_result == "failure"

    def test_verification_failure_rule_exists(self) -> None:
        rule = next((r for r in BUILTIN_RULES if r.name == "verification_failure"), None)
        assert rule is not None
        assert rule.trigger_event_type == "verification"
        assert rule.trigger_result == "failure"

    def test_all_rules_have_resolution_hint(self) -> None:
        for rule in BUILTIN_RULES:
            assert rule.resolution_hint

    def test_all_rules_have_description(self) -> None:
        for rule in BUILTIN_RULES:
            assert rule.description

    def test_weights_in_valid_range(self) -> None:
        for rule in BUILTIN_RULES:
            assert 0.0 < rule.weight <= 1.0


class TestGetRules:
    """Tests for get_rules function."""

    def test_returns_list(self) -> None:
        rules = get_rules()
        assert isinstance(rules, list)

    def test_returns_copy(self) -> None:
        rules1 = get_rules()
        rules2 = get_rules()
        assert rules1 is not rules2
        assert rules1 == rules2

    def test_returns_builtin_rules(self) -> None:
        rules = get_rules()
        assert len(rules) == len(BUILTIN_RULES)

    def test_modifying_returned_list_does_not_affect_builtin(self) -> None:
        rules = get_rules()
        rules.pop()
        assert len(get_rules()) == len(BUILTIN_RULES)
