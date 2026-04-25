"""Unit tests for polaris.kernelone.audit.omniscient.metrics."""

from __future__ import annotations

from polaris.kernelone.audit.omniscient.metrics import (
    AuditMetricsCollector,
    CircuitBreakerState,
    StormLevel,
    get_metrics_collector,
)
from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent, EventDomain


class TestCircuitBreakerState:
    def test_values(self) -> None:
        assert CircuitBreakerState.CLOSED == 0
        assert CircuitBreakerState.OPEN == 1
        assert CircuitBreakerState.HALF_OPEN == 2


class TestStormLevel:
    def test_values(self) -> None:
        assert StormLevel.NORMAL.value == "normal"
        assert StormLevel.CRITICAL.value == "critical"

    def test_repr(self) -> None:
        assert "StormLevel.CRITICAL" in repr(StormLevel.CRITICAL)


class TestAuditMetricsCollector:
    def test_record_event(self) -> None:
        collector = AuditMetricsCollector()
        collector.record_event("llm", "call", "info", latency_ms=150.0)
        summary = collector.get_summary()
        assert summary.get("llm") == 1

    def test_record_event_from_audit(self) -> None:
        from polaris.kernelone.audit.omniscient.bus import AuditPriority

        collector = AuditMetricsCollector()
        event = AuditEvent(domain=EventDomain.TOOL, event_type="exec", priority=AuditPriority.INFO)
        collector.record_event_from_audit(event, latency_ms=50.0)
        summary = collector.get_summary()
        assert summary.get("tool") == 1

    def test_histogram_buckets(self) -> None:
        collector = AuditMetricsCollector()
        collector.record_event("llm", "call", "info", latency_ms=50.0)
        output = collector.get_prometheus_format()
        assert "audit_events_total" in output
        assert "audit_events_latency_seconds_bucket" in output
        assert "audit_events_latency_seconds_count" in output
        assert "audit_events_latency_seconds_sum" in output

    def test_set_buffer_size(self) -> None:
        collector = AuditMetricsCollector()
        collector.set_buffer_size(42)
        output = collector.get_prometheus_format()
        assert "audit_buffer_size 42" in output

    def test_set_circuit_breaker_state(self) -> None:
        collector = AuditMetricsCollector()
        collector.set_circuit_breaker_state(CircuitBreakerState.OPEN)
        output = collector.get_prometheus_format()
        assert "audit_circuit_breaker_state 1" in output

    def test_set_storm_level(self) -> None:
        collector = AuditMetricsCollector()
        collector.set_storm_level(StormLevel.CRITICAL)
        output = collector.get_prometheus_format()
        assert "audit_storm_level critical" in output

    def test_reset(self) -> None:
        collector = AuditMetricsCollector()
        collector.record_event("llm", "call", "info")
        collector.reset()
        assert collector.get_summary() == {}

    def test_get_summary_multiple_domains(self) -> None:
        collector = AuditMetricsCollector()
        collector.record_event("llm", "call", "info")
        collector.record_event("llm", "call", "info")
        collector.record_event("tool", "exec", "info")
        summary = collector.get_summary()
        assert summary["llm"] == 2
        assert summary["tool"] == 1

    def test_prometheus_format_includes_uptime(self) -> None:
        collector = AuditMetricsCollector()
        output = collector.get_prometheus_format()
        assert "audit_uptime_seconds" in output


class TestGetMetricsCollector:
    def test_singleton(self) -> None:
        c1 = get_metrics_collector()
        c2 = get_metrics_collector()
        assert c1 is c2
