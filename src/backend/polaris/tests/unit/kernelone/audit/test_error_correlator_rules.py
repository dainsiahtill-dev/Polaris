"""Tests for polaris.kernelone.audit.error_correlator_rules."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.kernelone.audit.error_correlator_rules import (
    CorrelationType,
    EventTypeMatchRule,
    get_rules,
)


class TestCorrelationType:
    def test_values(self) -> None:
        assert CorrelationType.CAUSAL == "causal"
        assert CorrelationType.ENABLING == "enabling"
        assert CorrelationType.CORRELATED == "correlated"
        assert CorrelationType.MITIGATION == "mitigation"


class TestEventTypeMatchRule:
    def test_matches_event_type(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        event = MagicMock()
        event.event_type.value = "security_violation"
        event.action = {}
        assert rule.matches(event) is True

    def test_no_match_different_event_type(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="security_violation",
        )
        event = MagicMock()
        event.event_type.value = "other"
        event.action = {}
        assert rule.matches(event) is False

    def test_matches_with_result(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        event = MagicMock()
        event.event_type.value = "tool_execution"
        event.action = {"result": "failure"}
        assert rule.matches(event) is True

    def test_no_match_wrong_result(self) -> None:
        rule = EventTypeMatchRule(
            name="test",
            description="test",
            correlation_type=CorrelationType.CAUSAL,
            trigger_event_type="tool_execution",
            trigger_result="failure",
        )
        event = MagicMock()
        event.event_type.value = "tool_execution"
        event.action = {"result": "success"}
        assert rule.matches(event) is False


class TestGetRules:
    def test_returns_list(self) -> None:
        rules = get_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 5

    def test_rules_have_names(self) -> None:
        for rule in get_rules():
            assert rule.name
            assert rule.correlation_type
