"""Unit tests for polaris.kernelone.audit.alerting."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.kernelone.audit.alerting import (
    Alert,
    AlertCondition,
    AlertingEngine,
    AlertRule,
    AlertSeverity,
    AlertStatus,
)
from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType


def _make_event(
    event_type: KernelAuditEventType,
    task_id: str = "",
    timestamp: datetime | None = None,
) -> KernelAuditEvent:
    return KernelAuditEvent(
        event_id="e1",
        timestamp=timestamp or datetime.now(timezone.utc),
        event_type=event_type,
        task={"task_id": task_id},
    )


class TestAlertSeverity:
    def test_values(self) -> None:
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.ERROR.value == "error"
        assert AlertSeverity.CRITICAL.value == "critical"


class TestAlertStatus:
    def test_values(self) -> None:
        assert AlertStatus.ACTIVE.value == "active"
        assert AlertStatus.ACKNOWLEDGED.value == "acknowledged"
        assert AlertStatus.RESOLVED.value == "resolved"


class TestAlertCondition:
    def test_storm_levels_normalized(self) -> None:
        cond = AlertCondition(storm_levels=("WARNING", "Critical"))
        assert cond.storm_levels == ("warning", "critical")


class TestAlert:
    def test_to_dict(self) -> None:
        alert = Alert(
            alert_id="a1",
            rule_id="r1",
            rule_name="test",
            severity=AlertSeverity.ERROR,
            message="msg",
            triggered_at=datetime.now(timezone.utc),
            event_ids=["e1"],
            task_ids=["t1"],
            count=2,
        )
        d = alert.to_dict()
        assert d["alert_id"] == "a1"
        assert d["severity"] == "error"
        assert d["count"] == 2


class TestAlertingEngine:
    def test_default_rules_loaded(self) -> None:
        engine = AlertingEngine()
        assert len(engine.rules) > 0

    def test_add_and_remove_rule(self) -> None:
        engine = AlertingEngine()
        rule = AlertRule(
            id="custom",
            name="Custom",
            description="d",
            condition=AlertCondition(event_type="task_failed"),
            severity=AlertSeverity.INFO,
        )
        engine.add_rule(rule)
        assert any(r.id == "custom" for r in engine.rules)
        assert engine.remove_rule("custom") is True
        assert engine.remove_rule("custom") is False

    def test_evaluate_standard_rule_no_match(self) -> None:
        engine = AlertingEngine()
        event = _make_event(KernelAuditEventType.TASK_START)
        alerts = engine.evaluate(event)
        assert alerts == []

    def test_evaluate_standard_rule_triggers(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(
                event_type="task_failed",
                threshold_count=1,
                threshold_window_minutes=5,
            ),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_FAILED)
        alerts = engine.evaluate(event)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "test"

    def test_evaluate_disabled_rule(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1),
            severity=AlertSeverity.WARNING,
            enabled=False,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_FAILED)
        assert engine.evaluate(event) == []

    def test_evaluate_storm_rule(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="storm_test",
            name="Storm Test",
            description="d",
            condition=AlertCondition(storm_levels=("critical",)),
            severity=AlertSeverity.CRITICAL,
            is_dynamic_storm_rule=True,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_START)
        alerts = engine.evaluate(event, current_storm_level="critical")
        assert len(alerts) == 1
        assert alerts[0].rule_id == "storm_test"

    def test_evaluate_storm_rule_no_match(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="storm_test",
            name="Storm Test",
            description="d",
            condition=AlertCondition(storm_levels=("critical",)),
            severity=AlertSeverity.CRITICAL,
            is_dynamic_storm_rule=True,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_START)
        alerts = engine.evaluate(event, current_storm_level="normal")
        assert alerts == []

    def test_cooldown_prevents_duplicate(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=3600,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_FAILED)
        alerts1 = engine.evaluate(event)
        alerts2 = engine.evaluate(event)
        assert len(alerts1) == 1
        assert len(alerts2) == 0

    def test_acknowledge_and_resolve(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_FAILED)
        alerts = engine.evaluate(event)
        alert_id = alerts[0].alert_id

        assert engine.acknowledge(alert_id) is True
        assert engine.acknowledge("bogus") is False

        assert engine.resolve(alert_id) is True
        assert engine.resolve(alert_id) is False

    def test_get_active_alerts(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(event_type="task_failed", threshold_count=1),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        event = _make_event(KernelAuditEventType.TASK_FAILED)
        engine.evaluate(event)
        active = engine.get_active_alerts()
        assert len(active) == 1
        assert engine.get_active_alerts(AlertSeverity.ERROR) == []

    def test_task_pattern_filter(self) -> None:
        engine = AlertingEngine(rules=[])
        rule = AlertRule(
            id="test",
            name="Test",
            description="d",
            condition=AlertCondition(
                event_type="task_failed",
                threshold_count=1,
                task_pattern="task-*",
            ),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        )
        engine.add_rule(rule)
        match_event = _make_event(KernelAuditEventType.TASK_FAILED, task_id="task-123")
        no_match_event = _make_event(KernelAuditEventType.TASK_FAILED, task_id="other")
        assert len(engine.evaluate(match_event)) == 1
        assert len(engine.evaluate(no_match_event)) == 0
