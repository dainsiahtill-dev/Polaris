"""Alert Interceptor — bridges OmniscientAuditBus to AlertingEngine.

This interceptor subscribes to the audit bus, converts audit envelope events
to KernelAuditEvent-compatible structures, and forwards them to the existing
AlertingEngine for rule evaluation.

Design:
- Subscribes to the OmniscientAuditBus
- Maps omniscient event types to KernelAuditEventType enum values
- Handles circuit breaker state changes as critical alerts
- Non-blocking: alert evaluation happens asynchronously in the bus dispatch loop
- Builds on existing AlertingEngine without modifying it
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.alerting import (
    Alert,
    AlertingEngine,
    AlertSeverity,
)
from polaris.kernelone.audit.contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
)
from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

logger = logging.getLogger(__name__)


# Mapping from omniscient event type strings to KernelAuditEventType enum
_EVENT_TYPE_MAP: dict[str, KernelAuditEventType] = {
    "llm_interaction": KernelAuditEventType.LLM_CALL,
    "llm_interaction_complete": KernelAuditEventType.LLM_CALL,
    "llm_interaction_error": KernelAuditEventType.LLM_CALL,
    "tool_execution": KernelAuditEventType.TOOL_EXECUTION,
    "tool_execution_start": KernelAuditEventType.TOOL_EXECUTION,
    "tool_execution_complete": KernelAuditEventType.TOOL_EXECUTION,
    "tool_execution_error": KernelAuditEventType.TOOL_EXECUTION,
    "task_submitted": KernelAuditEventType.TASK_START,
    "task_started": KernelAuditEventType.TASK_START,
    "task_completed": KernelAuditEventType.TASK_COMPLETE,
    "task_failed": KernelAuditEventType.TASK_FAILED,
    "director_started": KernelAuditEventType.DIALOGUE,
    "director_completed": KernelAuditEventType.DIALOGUE,
    "policy_check": KernelAuditEventType.POLICY_CHECK,
    "verification": KernelAuditEventType.VERIFICATION,
    "security_violation": KernelAuditEventType.SECURITY_VIOLATION,
    "audit_verdict": KernelAuditEventType.AUDIT_VERDICT,
}


class AuditAlertInterceptor(BaseAuditInterceptor):
    """Interceptor that bridges audit bus to AlertingEngine.

    Captures audit events from the bus, converts them to KernelAuditEvent
    format, and evaluates them against AlertingEngine rules.

    Also tracks interceptor-level circuit breaker state changes and emits
    them as internal audit failure alerts.

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        alert_int = AuditAlertInterceptor(bus)
        # AlertingEngine.evaluate() is called automatically for each event

        # Get active alerts
        alerts = alert_int.get_active_alerts()
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        alerting_engine: AlertingEngine | None = None,
    ) -> None:
        """Initialize the alert interceptor.

        Args:
            bus: The audit bus to subscribe to.
            alerting_engine: Optional existing engine. Creates default if None.
        """
        super().__init__(name="alert_interceptor", priority=AuditPriority.WARNING)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        self._alerting_engine = alerting_engine or AlertingEngine()

        # Track alerts generated
        self._alerts_fired: list[Alert] = []

        # Track circuit breaker state for critical alerts
        self._previous_circuit_state: dict[str, bool] = {}

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process an audit event and forward to alerting engine.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        super().intercept(event)

        if isinstance(event, AuditEventEnvelope):
            event_data = event.event
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        if not isinstance(event_data, dict):
            return

        # Convert to KernelAuditEvent-compatible dict
        kernel_event = self._to_kernel_event(event_data)

        # Get current storm level from bus for dynamic alerting
        storm_level: str | None = None
        if hasattr(self._bus, "get_storm_level"):
            storm_level = self._bus.get_storm_level()

        # Evaluate against alerting rules (with storm level for dynamic rules)
        new_alerts = self._alerting_engine.evaluate(kernel_event, current_storm_level=storm_level)
        self._alerts_fired.extend(new_alerts)

        # Log fired alerts
        for alert in new_alerts:
            logger.info(
                "[alert_interceptor] Alert fired: rule=%s severity=%s count=%d",
                alert.rule_name,
                alert.severity.value,
                alert.count,
            )

    def _to_kernel_event(self, event_data: dict[str, Any]) -> KernelAuditEvent:
        """Convert omniscient event dict to KernelAuditEvent.

        Args:
            event_data: The raw event dict from the audit envelope.

        Returns:
            A KernelAuditEvent-compatible object.
        """
        event_type_str = event_data.get("type", "")
        kernel_event_type = _EVENT_TYPE_MAP.get(event_type_str, KernelAuditEventType.LLM_CALL)

        # Build task dict
        task_id = event_data.get("task_id", "")
        task: dict[str, Any] = {}
        if task_id:
            task["task_id"] = task_id

        # Build action dict
        action: dict[str, Any] = {}
        if "tool_name" in event_data:
            action["tool_name"] = event_data["tool_name"]
        if "model" in event_data:
            action["model"] = event_data["model"]
        if "error" in event_data:
            action["error"] = event_data["error"]
        if "success" in event_data:
            action["success"] = event_data["success"]

        # Build data dict
        data: dict[str, Any] = dict(event_data)

        # Map omniscient priority to alert severity
        priority = event_data.get("priority")
        if isinstance(priority, str):
            try:
                priority = AuditPriority[priority.upper()]
            except KeyError:
                priority = AuditPriority.INFO
        elif priority is None:
            priority = AuditPriority.INFO

        # Inject priority into data for rule evaluation
        data["_omniscient_priority"] = priority.value

        return KernelAuditEvent(
            event_id=event_data.get("event_id", uuid.uuid4().hex[:16]),
            timestamp=datetime.now(timezone.utc),
            event_type=kernel_event_type,
            task=task,
            action=action,
            data=data,
        )

    def _check_circuit_breaker_alerts(self) -> None:
        """Check for circuit breaker state changes and emit alerts."""
        for interceptor_name, is_open in self._get_interceptor_circuit_states().items():
            prev = self._previous_circuit_state.get(interceptor_name, False)
            if is_open and not prev:
                # Circuit just opened — emit internal audit failure
                self._fire_circuit_alert(interceptor_name)
            self._previous_circuit_state[interceptor_name] = is_open

    def _get_interceptor_circuit_states(self) -> dict[str, bool]:
        """Get circuit breaker state for all registered interceptors.

        Bus stores bound methods (interceptor._handle_envelope). Access the
        underlying interceptor instance via __self__ to read circuit state.

        Returns:
            Dict of interceptor name -> circuit open state.
        """
        states: dict[str, bool] = {}
        for callback in self._bus._interceptors:  # type: ignore[attr-defined]
            # Bound method: callback.__self__ is the interceptor instance
            if hasattr(callback, "__self__"):
                instance = callback.__self__
                if hasattr(instance, "name") and hasattr(instance, "circuit_open"):
                    name = str(instance.name)
                    open_state = bool(instance.circuit_open)
                    states[name] = open_state
            # Direct interceptor instance
            elif hasattr(callback, "name") and hasattr(callback, "circuit_open"):
                name = str(callback.name)
                open_state = bool(callback.circuit_open)
                states[name] = open_state
        return states

    def _fire_circuit_alert(self, interceptor_name: str) -> None:
        """Fire an internal audit failure alert for circuit breaker open.

        Args:
            interceptor_name: Name of the interceptor whose circuit opened.
        """
        alert = Alert(
            alert_id=uuid.uuid4().hex[:16],
            rule_id="interceptor_circuit_open",
            rule_name="Interceptor Circuit Open",
            severity=AlertSeverity.CRITICAL,
            message=f"[CRITICAL] Audit interceptor '{interceptor_name}' circuit breaker opened",
            triggered_at=datetime.now(timezone.utc),
            metadata={"interceptor": interceptor_name, "type": "internal_audit_failure"},
        )
        self._alerts_fired.append(alert)
        logger.warning(
            "[alert_interceptor] Circuit breaker open: interceptor=%s",
            interceptor_name,
        )

    def get_active_alerts(
        self,
        severity: Any = None,
    ) -> list[Alert]:
        """Get all active alerts from the alerting engine.

        Args:
            severity: Optional severity filter.

        Returns:
            List of active alerts.
        """
        return self._alerting_engine.get_active_alerts(severity)

    def get_fired_alerts(self) -> list[Alert]:
        """Get all alerts fired by this interceptor this session.

        Returns:
            List of all fired alerts.
        """
        return list(self._alerts_fired)

    def get_stats(self) -> dict[str, Any]:
        """Get alert interceptor statistics.

        Returns:
            Dictionary with alert-specific metrics.
        """
        base_stats = super().get_stats()
        active = self._alerting_engine.get_active_alerts()
        return {
            **base_stats,
            "alerts_fired": len(self._alerts_fired),
            "active_alerts": len(active),
            "alert_rules_count": len(self._alerting_engine.rules),
        }
