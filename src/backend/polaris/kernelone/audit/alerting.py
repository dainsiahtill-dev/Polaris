"""Alerting engine for KernelOne audit events.

Emits alerts when event patterns exceed configurable thresholds.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.audit.contracts import KernelAuditEvent

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass
class AlertCondition:
    """Condition that triggers an alert."""

    event_type: str | None = None
    failure_class: str | None = None
    threshold_count: int = 1
    threshold_window_minutes: int = 5
    task_pattern: str | None = None
    # Storm level trigger: alert fires when storm level enters these levels
    storm_levels: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "storm_levels", tuple(str(s).lower() for s in self.storm_levels))


@dataclass
class AlertRule:
    """A rule that evaluates conditions and produces alerts."""

    id: str
    name: str
    description: str
    condition: AlertCondition
    severity: AlertSeverity
    cooldown_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    enabled: bool = True
    tags: dict[str, str] = field(default_factory=dict)
    # Dynamic storm-level alert: auto-fires when storm level transitions into this rule's condition.storm_levels
    is_dynamic_storm_rule: bool = False


@dataclass
class Alert:
    """An active or historical alert."""

    alert_id: str
    rule_id: str
    rule_name: str
    severity: AlertSeverity
    message: str
    triggered_at: datetime
    event_ids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    count: int = 1
    status: AlertStatus = AlertStatus.ACTIVE
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "message": self.message,
            "triggered_at": self.triggered_at.isoformat(),
            "event_ids": self.event_ids,
            "task_ids": self.task_ids,
            "count": self.count,
            "status": self.status.value,
            "metadata": self.metadata,
        }


# Default rules (initialized lazily)
_DEFAULT_RULES: list[AlertRule] | None = None


def _get_default_rules() -> list[AlertRule]:
    global _DEFAULT_RULES
    if _DEFAULT_RULES is None:
        _DEFAULT_RULES = [
            AlertRule(
                id="high_failure_rate",
                name="High Failure Rate",
                description="Task failed 3+ times in 5 minutes",
                condition=AlertCondition(
                    event_type="task_failed",
                    threshold_count=3,
                    threshold_window_minutes=5,
                ),
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
            AlertRule(
                id="security_violation",
                name="Security Violation Detected",
                description="Any security violation triggers critical alert",
                condition=AlertCondition(
                    event_type="security_violation",
                    threshold_count=1,
                    threshold_window_minutes=1,
                ),
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                id="audit_chain_broken",
                name="Audit Chain Broken",
                description="Chain verification failure",
                condition=AlertCondition(
                    event_type="audit_verdict",
                    threshold_count=1,
                    threshold_window_minutes=1,
                ),
                severity=AlertSeverity.ERROR,
                cooldown_seconds=300,
            ),
            AlertRule(
                id="repeated_policy_block",
                name="Repeated Policy Blocks",
                description="Policy blocked 5+ times in 10 minutes",
                condition=AlertCondition(
                    event_type="policy_check",
                    threshold_count=5,
                    threshold_window_minutes=10,
                ),
                severity=AlertSeverity.WARNING,
                cooldown_seconds=600,
            ),
            # Dynamic storm-level alert rules
            AlertRule(
                id="storm_warning",
                name="Audit Storm Warning",
                description="Warning-level event storm detected",
                condition=AlertCondition(storm_levels=("warning",)),
                severity=AlertSeverity.WARNING,
                cooldown_seconds=60,
                is_dynamic_storm_rule=True,
            ),
            AlertRule(
                id="storm_critical",
                name="Audit Storm Critical",
                description="Critical-level event storm - non-ERROR events being dropped",
                condition=AlertCondition(storm_levels=("critical",)),
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=30,
                is_dynamic_storm_rule=True,
            ),
            AlertRule(
                id="storm_emergency",
                name="Audit Storm Emergency",
                description="Emergency-level event storm - only ERROR events preserved",
                condition=AlertCondition(storm_levels=("emergency",)),
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=10,
                is_dynamic_storm_rule=True,
            ),
        ]
    return _DEFAULT_RULES


class AlertingEngine:
    """Evaluates events against rules and produces alerts."""

    def __init__(
        self,
        rules: list[AlertRule] | None = None,
        max_history: int = 1000,
    ) -> None:
        self._rules = list(rules) if rules else list(_get_default_rules())
        self._active_alerts: dict[str, Alert] = {}
        self._alert_history: list[Alert] = []
        self._max_history = max_history
        self._last_fired: dict[str, datetime] = {}
        self._event_counts: dict[str, list[datetime]] = {}
        self._lock = threading.RLock()

    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        with self._lock:
            for i, r in enumerate(self._rules):
                if r.id == rule_id:
                    self._rules.pop(i)
                    return True
            return False

    def evaluate(
        self,
        event: KernelAuditEvent,
        current_storm_level: str | None = None,
    ) -> list[Alert]:
        """Evaluate one event against all rules. Returns new alerts.

        Args:
            event: The kernel audit event to evaluate.
            current_storm_level: Current storm level string (e.g., "critical").
                                 Used to trigger dynamic storm-level alert rules.
        """
        new_alerts: list[Alert] = []
        event_type_val = event.event_type.value
        task_id = str(event.task.get("task_id") or "")

        with self._lock:
            for rule in self._rules:
                if not rule.enabled:
                    continue

                cond = rule.condition

                # Handle dynamic storm-level rules
                if rule.is_dynamic_storm_rule and current_storm_level:
                    storm_match = current_storm_level.lower() in cond.storm_levels
                    if not storm_match:
                        continue
                    if self._in_cooldown(rule):
                        continue
                    alert = self._fire_storm_alert(rule, current_storm_level)
                    new_alerts.append(alert)
                    continue

                # Standard event-based rules
                if cond.event_type and cond.event_type != event_type_val:
                    continue
                if cond.task_pattern and not fnmatch.fnmatch(task_id, cond.task_pattern):
                    continue

                self._track_event(rule.id, event.timestamp)
                if not self._exceeds_threshold(rule):
                    continue
                if self._in_cooldown(rule):
                    continue

                alert = self._fire_alert(rule, event)
                new_alerts.append(alert)

        return new_alerts

    def _track_event(self, rule_id: str, timestamp: datetime) -> None:
        self._event_counts.setdefault(rule_id, []).append(timestamp)
        max_window = max(
            (r.condition.threshold_window_minutes for r in self._rules if r.id == rule_id),
            default=60,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_window * 2)
        self._event_counts[rule_id] = [ts for ts in self._event_counts[rule_id] if ts >= cutoff]

    def _exceeds_threshold(self, rule: AlertRule) -> bool:
        events = self._event_counts.get(rule.id, [])
        window_min = rule.condition.threshold_window_minutes
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
        recent = [ts for ts in events if ts >= cutoff]
        return len(recent) >= rule.condition.threshold_count

    def _in_cooldown(self, rule: AlertRule) -> bool:
        last = self._last_fired.get(rule.id)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < rule.cooldown_seconds

    def _fire_alert(self, rule: AlertRule, event: KernelAuditEvent) -> Alert:
        self._last_fired[rule.id] = datetime.now(timezone.utc)
        events = self._event_counts.get(rule.id, [])
        window_min = rule.condition.threshold_window_minutes
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
        recent_count = len([ts for ts in events if ts >= cutoff])
        alert_id = uuid.uuid4().hex[:16]
        task_id = str(event.task.get("task_id") or "")
        alert = Alert(
            alert_id=alert_id,
            rule_id=rule.id,
            rule_name=rule.name,
            severity=rule.severity,
            message=f"[{rule.severity.value.upper()}] {rule.name}: {rule.description}",
            triggered_at=datetime.now(timezone.utc),
            event_ids=[event.event_id],
            task_ids=[task_id] if task_id else [],
            count=recent_count,
            metadata={"description": rule.description},
        )
        self._active_alerts[alert_id] = alert
        self._alert_history.append(alert)
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history :]
        self._log_alert(alert)
        return alert

    def _fire_storm_alert(self, rule: AlertRule, storm_level: str) -> Alert:
        """Fire an alert triggered by a specific storm level transition.

        Args:
            rule: The alert rule that triggered.
            storm_level: The storm level that triggered this rule.

        Returns:
            The fired Alert.
        """
        self._last_fired[rule.id] = datetime.now(timezone.utc)
        alert_id = uuid.uuid4().hex[:16]
        alert = Alert(
            alert_id=alert_id,
            rule_id=rule.id,
            rule_name=rule.name,
            severity=rule.severity,
            message=f"[{rule.severity.value.upper()}] {rule.name}: {rule.description} (storm_level={storm_level})",
            triggered_at=datetime.now(timezone.utc),
            event_ids=[],
            task_ids=[],
            count=1,
            metadata={"description": rule.description, "storm_level": storm_level},
        )
        self._active_alerts[alert_id] = alert
        self._alert_history.append(alert)
        if len(self._alert_history) > self._max_history:
            self._alert_history = self._alert_history[-self._max_history :]
        self._log_alert(alert)
        return alert

    def _log_alert(self, alert: Alert) -> None:
        level = self._severity_to_log_level(alert.severity)
        logger.log(level, "ALERT [%s] %s", alert.rule_name, alert.message)

    @staticmethod
    def _severity_to_log_level(severity: AlertSeverity) -> int:
        return {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.ERROR: logging.ERROR,
            AlertSeverity.CRITICAL: logging.CRITICAL,
        }.get(severity, logging.INFO)

    def get_active_alerts(
        self,
        severity: AlertSeverity | None = None,
    ) -> list[Alert]:
        with self._lock:
            alerts = list(self._active_alerts.values())
        if severity is not None:
            alerts = [a for a in alerts if a.severity == severity]
        return sorted(alerts, key=lambda a: a.triggered_at, reverse=True)

    def acknowledge(self, alert_id: str) -> bool:
        with self._lock:
            alert = self._active_alerts.get(alert_id)
            if alert is None:
                return False
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_at = datetime.now(timezone.utc)
            return True

    def resolve(self, alert_id: str) -> bool:
        with self._lock:
            alert = self._active_alerts.pop(alert_id, None)
            if alert is None:
                return False
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = datetime.now(timezone.utc)
            return True

    @property
    def rules(self) -> list[AlertRule]:
        with self._lock:
            return list(self._rules)
