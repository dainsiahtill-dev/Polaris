"""Tests for polaris.kernelone.audit.alerting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from polaris.kernelone.audit.alerting import (
    Alert,
    AlertCondition,
    AlertingEngine,
    AlertRule,
    AlertSeverity,
    AlertStatus,
)


class TestAlertSeverity:
    def test_values(self) -> None:
        assert AlertSeverity.INFO == "info"
        assert AlertSeverity.WARNING == "warning"
        assert AlertSeverity.ERROR == "error"
        assert AlertSeverity.CRITICAL == "critical"


class TestAlertStatus:
    def test_values(self) -> None:
        assert AlertStatus.ACTIVE == "active"
        assert AlertStatus.ACKNOWLEDGED == "acknowledged"
        assert AlertStatus.RESOLVED == "resolved"


class TestAlertCondition:
    def test_defaults(self) -> None:
        cond = AlertCondition()
        assert cond.event_type is None
        assert cond.failure_class is None
        assert cond.threshold_count == 1
        assert cond.threshold_window_minutes == 5
        assert cond.task_pattern is None
        assert cond.storm_levels == ()

    def test_storm_levels_normalization(self) -> None:
        cond = AlertCondition(storm_levels=("Warning", "CRITICAL"))
        assert cond.storm_levels == ("warning", "critical")


class TestAlertRule:
    def test_defaults(self) -> None:
        rule = AlertRule(
            id="r1",
            name="Test Rule",
            description="desc",
            condition=AlertCondition(),
            severity=AlertSeverity.WARNING,
        )
        assert rule.cooldown_seconds == 30
        assert rule.enabled is True
        assert rule.tags == {}
        assert rule.is_dynamic_storm_rule is False


class TestAlert:
    def test_to_dict(self) -> None:
        now = datetime.now(timezone.utc)
        alert = Alert(
            alert_id="a1",
            rule_id="r1",
            rule_name="Test",
            severity=AlertSeverity.ERROR,
            message="msg",
            triggered_at=now,
            event_ids=["e1"],
            task_ids=["t1"],
            count=2,
            status=AlertStatus.ACTIVE,
            metadata={"key": "val"},
        )
        d = alert.to_dict()
        assert d["alert_id"] == "a1"
        assert d["severity"] == "error"
        assert d["status"] == "active"
        assert d["count"] == 2
        assert d["triggered_at"] == now.isoformat()


class TestAlertingEngine:
    def _make_event(self, event_type: str = "task_failed", task_id: str = "t1") -> MagicMock:
        event = MagicMock()
        event.event_type.value = event_type
        event.task = {"task_id": task_id}
        event.timestamp = datetime.now(timezone.utc)
        event.event_id = "e1"
        return event

    def test_default_rules_loaded(self) -> None:
        engine = AlertingEngine()
        rules = engine.rules
        assert len(rules) >= 4
        ids = {r.id for r in rules}
        assert "high_failure_rate" in ids

    def test_add_and_remove_rule(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="custom",
            name="Custom",
            description="d",
            condition=AlertCondition(event_type="x"),
            severity=AlertSeverity.INFO,
        )
        engine.add_rule(rule)
        assert len(engine.rules) == 1
        assert engine.remove_rule("custom") is True
        assert engine.remove_rule("missing") is False
        assert len(engine.rules) == 0

    def test_evaluate_no_match(self) -> None:
        engine = AlertingEngine(rules=[])
        event = self._make_event(event_type="other")
        assert engine.evaluate(event) == []

    def test_evaluate_threshold_not_met(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=3, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        assert engine.evaluate(event) == []

    def test_evaluate_fires_alert(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        alerts = engine.evaluate(event)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "r1"
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_cooldown_prevents_duplicate(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=3600,
        )
        engine.add_rule(rule)
        event = self._make_event()
        alerts1 = engine.evaluate(event)
        assert len(alerts1) == 1
        alerts2 = engine.evaluate(event)
        assert len(alerts2) == 0

    def test_disabled_rule_skipped(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            enabled=False,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        assert engine.evaluate(event) == []

    def test_storm_rule_fires(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="storm_test",
            name="Storm",
            description="d",
            condition=AlertCondition(storm_levels=("critical",)),
            severity=AlertSeverity.CRITICAL,
            is_dynamic_storm_rule=True,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        alerts = engine.evaluate(event, current_storm_level="critical")
        assert len(alerts) == 1
        assert alerts[0].rule_id == "storm_test"
        assert "storm_level=critical" in alerts[0].message

    def test_storm_rule_no_match(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="storm_test",
            name="Storm",
            description="d",
            condition=AlertCondition(storm_levels=("critical",)),
            severity=AlertSeverity.CRITICAL,
            is_dynamic_storm_rule=True,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        assert engine.evaluate(event, current_storm_level="warning") == []

    def test_task_pattern_filter(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", task_pattern="t*", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event_match = self._make_event(task_id="task-1")
        event_no_match = self._make_event(task_id="other")
        assert len(engine.evaluate(event_match)) == 1
        assert len(engine.evaluate(event_no_match)) == 0

    def test_acknowledge_and_resolve(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = self._make_event()
        alerts = engine.evaluate(event)
        alert_id = alerts[0].alert_id

        assert engine.acknowledge(alert_id) is True
        assert engine.acknowledge("missing") is False

        assert engine.resolve(alert_id) is True
        assert engine.resolve("missing") is False

    def test_get_active_alerts_filter_by_severity(self) -> None:
        engine = AlertingEngine(rules=[])
        rule_warn = AlertRule(
            id="rw",
            name="W",
            description="d",
            condition=AlertCondition(event_type="warn", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        rule_crit = AlertRule(
            id="rc",
            name="C",
            description="d",
            condition=AlertCondition(event_type="crit", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=0,
        )
        engine.add_rule(rule_warn)
        engine.add_rule(rule_crit)
        engine.evaluate(self._make_event(event_type="warn"))
        engine.evaluate(self._make_event(event_type="crit"))

        crit_alerts = engine.get_active_alerts(severity=AlertSeverity.CRITICAL)
        assert len(crit_alerts) == 1
        assert crit_alerts[0].severity == AlertSeverity.CRITICAL

    def test_max_history_trimming(self) -> None:
        engine = AlertingEngine(rules=[], max_history=2)
        rule = AlertRule(
            id="r1",
            name="R",
            description="d",
            condition=AlertCondition(event_type="x", threshold_count=1, threshold_window_minutes=5),
            severity=AlertSeverity.INFO,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        for i in range(5):
            event = self._make_event(event_type="x")
            event.event_id = f"e{i}"
            engine.evaluate(event)
        # History should be trimmed to max_history
        assert len(engine.get_active_alerts()) <= 2
