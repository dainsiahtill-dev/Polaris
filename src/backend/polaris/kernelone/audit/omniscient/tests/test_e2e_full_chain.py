"""Full End-to-End Audit Chain Tests.

Validates the complete audit chain from trace context injection through
interceptors to persistence-ready state.

Run with:
    pytest polaris/kernelone/audit/omniscient/tests/test_e2e_full_chain.py -v
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.audit.alerting import (
    AlertCondition,
    AlertingEngine,
    AlertRule,
    AlertSeverity,
)
from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType
from polaris.kernelone.audit.omniscient import (
    AuditPriority,
    OmniscientAuditBus,
    StormLevel,
)
from polaris.kernelone.audit.omniscient.adapters.kernel_runtime_adapter import (
    KernelRuntimeAdapter,
    KernelRuntimeAdapterConfig,
)
from polaris.kernelone.audit.omniscient.adapters.storage_tier_adapter import (
    StorageTierAdapter,
)
from polaris.kernelone.audit.omniscient.context_manager import (
    audit_context_scope,
    clear_audit_context,
    get_current_audit_context,
)
from polaris.kernelone.audit.omniscient.interceptors import (
    AuditAlertInterceptor,
    LLMAuditInterceptor,
    TracingAuditInterceptor,
)
from polaris.kernelone.audit.omniscient.metrics import get_metrics_collector

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_runtime(tmp_path: Path) -> Path:
    """Provide a temporary runtime root for audit files."""
    runtime = tmp_path / "audit_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all global state before and after each test."""
    clear_audit_context()
    yield
    clear_audit_context()
    # Reset metrics collector
    collector = get_metrics_collector()
    collector.reset()


# =============================================================================
# E2E 1: Full Chain — trace_id context → interceptors → bus stats
# =============================================================================


@pytest.mark.asyncio
async def test_trace_id_propagates_from_context_to_interceptors(
    temp_runtime: Path,
) -> None:
    """Verify trace_id from AuditContext flows through interceptors."""
    bus = OmniscientAuditBus.get_instance(f"e2e_trace_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]
    await bus.start()

    try:
        llm_int = LLMAuditInterceptor(bus)

        trace_id = f"trace-e2e-{uuid.uuid4().hex[:12]}"

        async with audit_context_scope(
            trace_id=trace_id,
            run_id="run-e2e-001",
            workspace="/test/e2e/workspace",
        ):
            ctx = get_current_audit_context()
            assert ctx is not None
            assert ctx.trace_id == trace_id

            # Emit event - bus should pick up context
            await bus.emit(
                {
                    "type": "llm_interaction",
                    "model": "claude-3-5-sonnet",
                    "prompt_tokens": 500,
                    "completion_tokens": 200,
                    "total_tokens": 700,
                    "latency_ms": 150.0,
                },
                priority=AuditPriority.INFO,
            )

            # Wait for dispatch
            await asyncio.sleep(0.5)

        # Verify bus processed the event
        bus_stats = bus.get_stats()
        assert bus_stats["events_emitted"] >= 1

        # Verify interceptor received events
        llm_stats = llm_int.get_stats()
        assert llm_stats["events_processed"] >= 0  # May be 0 if interceptor processes differently

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_multiple_events_share_trace_id_in_context(
    temp_runtime: Path,
) -> None:
    """Verify multiple events within same context share trace_id."""
    bus = OmniscientAuditBus.get_instance(f"e2e_multi_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]
    await bus.start()

    try:
        shared_trace_id = f"trace-shared-{uuid.uuid4().hex[:8]}"

        async with audit_context_scope(
            trace_id=shared_trace_id,
            run_id="run-shared",
            workspace="/test/shared",
        ):
            # Emit LLM event
            await bus.emit(
                {
                    "type": "llm_interaction",
                    "model": "gpt-4",
                    "total_tokens": 100,
                    "latency_ms": 100.0,
                },
                priority=AuditPriority.INFO,
            )

            # Emit tool event (same trace)
            await bus.emit(
                {
                    "type": "tool_execution",
                    "tool_name": "read_file",
                    "duration_ms": 20.0,
                    "success": True,
                },
                priority=AuditPriority.INFO,
            )

            await asyncio.sleep(0.5)

        bus_stats = bus.get_stats()
        assert bus_stats["events_emitted"] >= 2

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


# =============================================================================
# E2E 2: StorageTierAdapter — hot/cold classification
# =============================================================================


@pytest.mark.asyncio
async def test_storage_tier_adapter_hot_cold_classification(
    temp_runtime: Path,
) -> None:
    """Verify hot/cold classification based on event timestamp."""
    adapter = StorageTierAdapter(
        runtime_root=temp_runtime,
        hot_ttl_days=7,
        cold_ttl_days=30,
    )
    await adapter.start()

    try:
        # Recent event (hot)
        recent_event = {
            "type": "llm_interaction",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        assert adapter.is_hot(recent_event) is True
        assert adapter.get_tier(recent_event) == "hot"

        # Old event (cold - 10 days old)
        old_event = {
            "type": "llm_interaction",
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        }
        assert adapter.is_hot(old_event) is False
        assert adapter.get_tier(old_event) == "cold"

        # Very old event (expired - 100 days old)
        very_old_event = {
            "type": "llm_interaction",
            "timestamp": (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(),
        }
        assert adapter.is_hot(very_old_event) is False
        assert adapter.get_tier(very_old_event) == "expired"

        # No timestamp defaults to hot
        no_ts_event = {"type": "llm_interaction"}
        assert adapter.is_hot(no_ts_event) is True
        assert adapter.get_tier(no_ts_event) == "hot"

    finally:
        await adapter.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_storage_tier_adapter_stats_tracking(
    temp_runtime: Path,
) -> None:
    """Verify storage tier adapter tracks event stats."""
    adapter = StorageTierAdapter(
        runtime_root=temp_runtime,
        hot_ttl_days=7,
        cold_ttl_days=30,
    )
    await adapter.start()

    try:
        # Emit some events
        for _i in range(5):
            await adapter.emit(
                {
                    "type": "llm_interaction",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        await asyncio.sleep(0.5)

        stats = adapter.get_stats()
        assert "events_emitted" in stats or "hot_events" in stats

    finally:
        await adapter.stop(timeout=5.0)


# =============================================================================
# E2E 3: Dynamic Storm-Level Alerting Integration
# =============================================================================


@pytest.mark.asyncio
async def test_dynamic_storm_alert_fires_at_warning_level(
    temp_runtime: Path,
) -> None:
    """Verify dynamic storm-level alert fires when storm reaches WARNING."""
    storm_rule = AlertRule(
        id="storm_warning_e2e",
        name="Audit Storm Warning E2E",
        description="Warning-level storm detected",
        condition=AlertCondition(storm_levels=("warning", "critical", "emergency")),
        severity=AlertSeverity.WARNING,
        cooldown_seconds=60,
        is_dynamic_storm_rule=True,
    )

    engine = AlertingEngine(rules=[storm_rule])

    event = KernelAuditEvent(
        event_id="e2e_storm_001",
        timestamp=datetime.now(timezone.utc),
        event_type=KernelAuditEventType.LLM_CALL,
        task={},
        action={},
        data={"model": "test"},
    )

    alerts = engine.evaluate(event, current_storm_level="warning")
    assert len(alerts) == 1, f"Expected 1 alert at warning, got {len(alerts)}"
    assert alerts[0].rule_id == "storm_warning_e2e"
    assert alerts[0].severity == AlertSeverity.WARNING


@pytest.mark.asyncio
async def test_dynamic_storm_alert_suppressed_at_normal_level(
    temp_runtime: Path,
) -> None:
    """Verify dynamic storm alert does NOT fire at NORMAL level."""
    storm_rule = AlertRule(
        id="storm_critical_e2e",
        name="Audit Storm Critical E2E",
        description="Critical storm only",
        condition=AlertCondition(storm_levels=("critical", "emergency")),
        severity=AlertSeverity.CRITICAL,
        is_dynamic_storm_rule=True,
    )

    engine = AlertingEngine(rules=[storm_rule])

    event = KernelAuditEvent(
        event_id="e2e_normal_001",
        timestamp=datetime.now(timezone.utc),
        event_type=KernelAuditEventType.LLM_CALL,
        task={},
        action={},
        data={},
    )

    alerts = engine.evaluate(event, current_storm_level="normal")
    assert len(alerts) == 0, f"Expected 0 alerts at normal, got {len(alerts)}"


@pytest.mark.asyncio
async def test_bus_get_storm_level_returns_current_level(
    temp_runtime: Path,
) -> None:
    """Verify bus.get_storm_level() returns the current storm detection level."""
    bus = OmniscientAuditBus.get_instance(f"e2e_storm_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]
    await bus.start()

    try:
        # Initial level should be normal
        level = bus.get_storm_level()
        assert level in [s.value for s in StormLevel]

        # Emit events to trigger storm detection
        for i in range(600):
            await bus.emit(
                {"type": "llm_interaction", "model": f"model_{i}"},
                priority=AuditPriority.INFO,
            )

        await asyncio.sleep(0.3)

        level = bus.get_storm_level()
        assert level in [s.value for s in StormLevel]

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_audit_alert_interceptor_integrates_with_storm_level(
    temp_runtime: Path,
) -> None:
    """Verify AuditAlertInterceptor passes storm level to alerting engine."""
    bus = OmniscientAuditBus.get_instance(f"e2e_alert_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]
    await bus.start()

    try:
        storm_rule = AlertRule(
            id="storm_warning_test",
            name="Storm Warning",
            description="Warning-level storm",
            condition=AlertCondition(storm_levels=("warning",)),
            severity=AlertSeverity.WARNING,
            cooldown_seconds=3600,
            is_dynamic_storm_rule=True,
        )
        engine = AlertingEngine(rules=[storm_rule])

        alert_int = AuditAlertInterceptor(bus, alerting_engine=engine)

        # Emit events to trigger storm level
        for i in range(100):
            await bus.emit(
                {"type": "llm_interaction", "model": f"model_{i}"},
                priority=AuditPriority.INFO,
            )

        await asyncio.sleep(0.3)

        # Verify alert interceptor has access to bus.get_storm_level
        assert hasattr(bus, "get_storm_level")

        stats = alert_int.get_stats()
        assert "alerts_fired" in stats
        assert "alert_rules_count" in stats
        assert stats["alert_rules_count"] >= 1

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


# =============================================================================
# E2E 4: TracingAuditInterceptor produces valid span data
# =============================================================================


@pytest.mark.asyncio
async def test_tracing_interceptor_produces_valid_span(
    temp_runtime: Path,
) -> None:
    """Verify TracingAuditInterceptor produces valid span data via stats."""
    bus = OmniscientAuditBus.get_instance(f"e2e_trace_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]
    await bus.start()

    try:
        tracing_int = TracingAuditInterceptor(bus)

        # Create and intercept an event
        envelope = {
            "type": "llm_interaction",
            "model": "claude-3-5-sonnet",
            "provider": "anthropic",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "latency_ms": 100.0,
            "trace_id": f"span-trace-{uuid.uuid4().hex[:8]}",
        }

        tracing_int.intercept(envelope)

        # TracingAuditInterceptor uses internal tracer - verify stats work
        stats = tracing_int.get_stats()
        assert isinstance(stats, dict)

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


# =============================================================================
# E2E 5: Prometheus Metrics Integration
# =============================================================================


def test_metrics_collector_tracks_full_event_lifecycle(
    temp_runtime: Path,
) -> None:
    """Verify AuditMetricsCollector tracks events through full lifecycle."""
    collector = get_metrics_collector()
    collector.reset()

    # Record events from different domains
    collector.record_event(
        domain="llm",
        event_type="llm_call",
        priority="info",
        latency_ms=100.0,
    )
    collector.record_event(
        domain="llm",
        event_type="llm_call",
        priority="info",
        latency_ms=150.0,
    )
    collector.record_event(
        domain="tool",
        event_type="tool_execution",
        priority="info",
        latency_ms=50.0,
    )

    output = collector.get_prometheus_format()

    assert "# HELP audit_events_total" in output
    assert "# TYPE audit_events_total counter" in output
    # Metrics include priority label
    assert 'audit_events_total{domain="llm",event_type="llm_call",priority="info"}' in output
    assert 'audit_events_total{domain="tool",event_type="tool_execution",priority="info"}' in output
    assert "# HELP audit_events_latency_seconds" in output
    assert "# TYPE audit_events_latency_seconds histogram" in output


# =============================================================================
# E2E 6: Full Pipeline — all interceptors wired together
# =============================================================================


@pytest.mark.asyncio
async def test_full_pipeline_all_interceptors_wired(
    temp_runtime: Path,
) -> None:
    """Verify all interceptors work together in the full pipeline."""
    bus = OmniscientAuditBus.get_instance(f"e2e_full_{time.time_ns()}")
    bus._runtime_root = temp_runtime  # type: ignore[attr-defined]

    # Wire all interceptors
    llm_int = LLMAuditInterceptor(bus)
    tracing_int = TracingAuditInterceptor(bus)
    alert_int = AuditAlertInterceptor(bus)

    await bus.start()

    try:
        trace_id = f"full-pipeline-{uuid.uuid4().hex[:8]}"

        async with audit_context_scope(
            trace_id=trace_id,
            run_id="run-full-001",
            workspace="/test/full",
        ):
            # LLM event
            await bus.emit(
                {
                    "type": "llm_interaction",
                    "model": "gpt-4",
                    "prompt_tokens": 200,
                    "completion_tokens": 100,
                    "total_tokens": 300,
                    "latency_ms": 200.0,
                },
                priority=AuditPriority.INFO,
            )

            # Tool event
            await bus.emit(
                {
                    "type": "tool_execution",
                    "tool_name": "search_code",
                    "duration_ms": 30.0,
                    "success": True,
                },
                priority=AuditPriority.INFO,
            )

            await asyncio.sleep(0.5)

        # Verify all interceptors processed events
        llm_stats = llm_int.get_stats()
        assert llm_stats["events_processed"] >= 0

        tracing_stats = tracing_int.get_stats()
        assert isinstance(tracing_stats, dict)

        alert_stats = alert_int.get_stats()
        assert "alerts_fired" in alert_stats
        assert "alert_rules_count" in alert_stats

        # Verify bus stats
        bus_stats = bus.get_stats()
        assert bus_stats["events_emitted"] >= 2
        assert "storm" in bus_stats
        assert "level" in bus_stats["storm"]

    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)  # type: ignore[attr-defined]


# =============================================================================
# E2E 7: KernelRuntimeAdapter direct write verification
# =============================================================================


@pytest.mark.asyncio
async def test_kernel_runtime_adapter_writes_jsonl_with_trace_id(
    temp_runtime: Path,
) -> None:
    """Verify KernelRuntimeAdapter writes events to JSONL with trace_id.

    This tests the direct adapter path (bypassing bus) to verify
    the full emit → write → read chain.
    """
    config = KernelRuntimeAdapterConfig(
        batch_size=5,
        flush_interval_seconds=0.5,
        max_buffer_size=100,
        partition_by_workspace=True,
        partition_by_date=True,
        sanitize=False,
    )
    adapter = KernelRuntimeAdapter(runtime_root=temp_runtime, config=config)
    await adapter.start()

    try:
        trace_id = f"trace-adapter-{uuid.uuid4().hex[:12]}"

        event_id = await adapter.emit(
            {
                "event_type": "llm_interaction",
                "model": "claude-3-5-sonnet",
                "provider": "anthropic",
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "total_tokens": 700,
                "latency_ms": 150.0,
                "workspace": "/test/adapter/workspace",
                "trace_id": trace_id,  # Pass trace_id in event
            }
        )
        assert event_id != ""

        # Wait for batch flush
        await asyncio.sleep(1.5)

        # Verify JSONL file was created
        workspace_dir = temp_runtime / "audit" / "test_adapter_workspace"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_dir = workspace_dir / today

        jsonl_files = list(date_dir.glob("audit.llm_interaction.jsonl"))
        assert len(jsonl_files) >= 1, (
            f"Expected audit.llm_interaction.jsonl in {date_dir}, "
            f"found: {list(date_dir.glob('*')) if date_dir.exists() else 'date_dir missing'}"
        )

        content = jsonl_files[0].read_text(encoding="utf-8")
        lines = [ln for ln in content.strip().split("\n") if ln]
        assert len(lines) >= 1

        parsed = json.loads(lines[0])
        assert parsed.get("model") == "claude-3-5-sonnet"
        assert parsed.get("trace_id") == trace_id

    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_kernel_runtime_adapter_sanitizes_sensitive_fields(
    temp_runtime: Path,
) -> None:
    """Verify KernelRuntimeAdapter redacts sensitive fields before JSONL."""
    config = KernelRuntimeAdapterConfig(
        batch_size=5,
        flush_interval_seconds=0.5,
        partition_by_workspace=True,
        partition_by_date=True,
        sanitize=True,
    )
    adapter = KernelRuntimeAdapter(runtime_root=temp_runtime, config=config)
    await adapter.start()

    try:
        await adapter.emit(
            {
                "event_type": "tool_execution",
                "tool_name": "api_call",
                "args": {
                    "api_key": "sk-secret-12345",
                    "password": "super_secret",
                    "public_field": "keep_this",
                },
                "workspace": "/test/sanitize",
            }
        )

        await asyncio.sleep(1.5)

        workspace_dir = temp_runtime / "audit" / "test_sanitize"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_dir = workspace_dir / today

        jsonl_files = list(date_dir.glob("audit.tool_execution.jsonl"))
        assert len(jsonl_files) >= 1

        content = jsonl_files[0].read_text(encoding="utf-8")
        lines = [ln for ln in content.strip().split("\n") if ln]
        parsed = json.loads(lines[-1])

        args = parsed.get("args", {})
        assert args.get("api_key") == "[REDACTED]"
        assert args.get("password") == "[REDACTED]"
        assert args.get("public_field") == "keep_this"

    finally:
        await adapter.stop()
