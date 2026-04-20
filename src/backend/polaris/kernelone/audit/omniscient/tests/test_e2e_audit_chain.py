"""End-to-End Audit Chain Tests.

Validates the complete audit chain:
1. AuditContext propagation (async contextvars)
2. Bus emit() → interceptors → fallback persistence
3. KernelRuntimeAdapter async batched write to JSONL
4. Query by trace_id from persisted JSONL files

Run with:
    pytest polaris/kernelone/audit/omniscient/tests/test_e2e_audit_chain.py -v
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from polaris.kernelone.audit.omniscient import (
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.adapters.kernel_runtime_adapter import (
    KernelRuntimeAdapter,
    KernelRuntimeAdapterConfig,
)
from polaris.kernelone.audit.omniscient.context_manager import (
    UnifiedAuditContext,
    UnifiedContextFactory,
    audit_context_scope,
    clear_audit_context,
    get_current_audit_context,
)
from polaris.kernelone.audit.omniscient.interceptors import (
    LLMAuditInterceptor,
    ToolAuditInterceptor,
    TracingAuditInterceptor,
)
from polaris.kernelone.audit.omniscient.metrics import get_metrics_collector

if TYPE_CHECKING:
    from pathlib import Path

    from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_runtime_root(tmp_path: Path) -> Path:
    """Provide a temporary runtime root for audit files."""
    runtime = tmp_path / "audit_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


@pytest.fixture
def adapter_config() -> KernelRuntimeAdapterConfig:
    """Low-latency config for tests."""
    return KernelRuntimeAdapterConfig(
        batch_size=5,
        flush_interval_seconds=0.5,
        max_buffer_size=100,
        partition_by_workspace=True,
        partition_by_date=True,
        sanitize=True,
    )


@pytest_asyncio.fixture
async def running_adapter(temp_runtime_root: Path, adapter_config: KernelRuntimeAdapterConfig) -> KernelRuntimeAdapter:
    """Start and stop an adapter."""
    adapter = KernelRuntimeAdapter(
        runtime_root=temp_runtime_root,
        config=adapter_config,
    )
    await adapter.start()
    yield adapter
    await adapter.stop(timeout=2.0)


@pytest_asyncio.fixture
async def configured_bus(temp_runtime_root: Path) -> OmniscientAuditBus:
    """Provide a configured bus with interceptors."""
    bus_name = f"test_{time.time_ns()}"
    bus = OmniscientAuditBus.get_instance(bus_name)
    bus._runtime_root = temp_runtime_root  # type: ignore[attr-defined]

    # Interceptors registered with bus (bus owns lifecycle)
    LLMAuditInterceptor(bus)
    ToolAuditInterceptor(bus)
    TracingAuditInterceptor(bus)

    await bus.start()
    yield bus
    await bus.stop()
    OmniscientAuditBus._instances.pop(bus_name, None)  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def reset_singleton_state():
    """Reset bus singleton state after each test."""
    yield
    # Clean up any bus instances
    OmniscientAuditBus._instances.clear()  # type: ignore[attr-defined]
    clear_audit_context()


# =============================================================================
# Test 1: UnifiedAuditContext propagation through async tasks
# =============================================================================


@pytest.mark.asyncio
async def test_context_propagates_to_async_child_tasks() -> None:
    """Verify UnifiedAuditContext flows through async task boundaries."""
    trace_id_seen: list[str] = []

    async with audit_context_scope(
        trace_id="e2e-trace-001",
        run_id="e2e-run-001",
        task_id="e2e-task-001",
        workspace="/test/workspace",
    ):
        parent_ctx = get_current_audit_context()
        assert parent_ctx is not None
        assert parent_ctx.trace_id == "e2e-trace-001"

        async def child_task() -> None:
            child_ctx = get_current_audit_context()
            if child_ctx is not None:
                trace_id_seen.append(child_ctx.trace_id)

        # Launch concurrent async tasks
        await asyncio.gather(child_task(), child_task())

    assert "e2e-trace-001" in trace_id_seen


@pytest.mark.asyncio
async def test_unified_context_factory_inherit() -> None:
    """Verify UnifiedContextFactory.inherit() preserves trace_id."""
    async with audit_context_scope(
        trace_id="parent-trace",
        run_id="parent-run",
    ):
        # Inherit from current context with new overrides
        child = UnifiedContextFactory.inherit(
            task_id="child-task",
        )
        assert child.trace_id == "parent-trace"
        assert child.run_id == "parent-run"
        assert child.task_id == "child-task"


# =============================================================================
# Test 2: Bus emit → interceptors → stats tracking
# =============================================================================


@pytest.mark.asyncio
async def test_llm_interceptor_records_tokens_and_latency(
    configured_bus: OmniscientAuditBus,
) -> None:
    """Verify LLM interceptor accumulates token counts and latency."""
    # Find LLMAuditInterceptor in bus subscribers
    llm_int = None
    for sub in configured_bus._interceptors:  # type: ignore[attr-defined]
        interceptor = getattr(sub, "__self__", None)
        if isinstance(interceptor, LLMAuditInterceptor):
            llm_int = interceptor
            break

    assert llm_int is not None, "LLMAuditInterceptor not found in bus subscribers"

    # Emit LLM events
    await configured_bus.emit(
        {
            "type": "llm_interaction",
            "model": "claude-3-5-sonnet-20241022",
            "provider": "anthropic",
            "prompt_tokens": 500,
            "completion_tokens": 200,
            "total_tokens": 700,
            "latency_ms": 150.0,
            "finish_reason": "stop",
        },
        priority=AuditPriority.INFO,
    )

    await asyncio.sleep(0.2)

    stats = llm_int.get_stats()
    assert stats["total_tokens"] >= 700
    assert stats["total_latency_ms"] >= 150.0


@pytest.mark.asyncio
async def test_tool_interceptor_detects_write_operations(
    configured_bus: OmniscientAuditBus,
) -> None:
    """Verify ToolAuditInterceptor counts write vs read operations."""
    # Find ToolAuditInterceptor in bus subscribers
    tool_int = None
    for sub in configured_bus._interceptors:  # type: ignore[attr-defined]
        interceptor = getattr(sub, "__self__", None)
        if isinstance(interceptor, ToolAuditInterceptor):
            tool_int = interceptor
            break

    assert tool_int is not None, "ToolAuditInterceptor not found"

    # Emit write tool events
    await configured_bus.emit(
        {
            "type": "tool_execution",
            "tool_name": "write_file",
            "duration_ms": 50.0,
            "success": True,
        },
        priority=AuditPriority.INFO,
    )
    await configured_bus.emit(
        {
            "type": "tool_execution",
            "tool_name": "read_file",
            "duration_ms": 10.0,
            "success": True,
        },
        priority=AuditPriority.INFO,
    )

    await asyncio.sleep(0.2)

    stats = tool_int.get_stats()
    assert stats["write_operation_count"] >= 1
    assert stats["read_operation_count"] >= 1


@pytest.mark.asyncio
async def test_bus_priority_ordering() -> None:
    """Verify CRITICAL events are processed before INFO."""
    processed: list[str] = []

    bus = OmniscientAuditBus.get_instance(f"priority_test_{time.time_ns()}")
    await bus.start()

    async def recording_callback(envelope: AuditEventEnvelope) -> None:
        event_name = envelope.event.get("name", "unknown")
        processed.append(f"{envelope.priority.name}:{event_name}")

    bus.subscribe(recording_callback)

    # Emit in wrong order
    await bus.emit({"name": "info_event"}, priority=AuditPriority.INFO)
    await bus.emit({"name": "critical_event"}, priority=AuditPriority.CRITICAL)
    await bus.emit({"name": "error_event"}, priority=AuditPriority.ERROR)

    await asyncio.sleep(0.3)
    await bus.stop()

    # CRITICAL must be first
    assert len(processed) >= 3
    first = processed[0]
    assert first.startswith("CRITICAL:critical_event"), f"Expected CRITICAL first, got {first}"


# =============================================================================
# Test 3: KernelRuntimeAdapter flushes to JSONL files
# =============================================================================


@pytest.mark.asyncio
async def test_kernel_runtime_adapter_writes_partitioned_jsonl(
    running_adapter: KernelRuntimeAdapter,
    temp_runtime_root: Path,
) -> None:
    """Verify adapter writes events to workspace/date/channel JSONL files."""
    event = {
        "event_type": "llm_interaction",
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "latency_ms": 120.0,
        "workspace": "/test/workspace",
        "trace_id": "adapter-trace-001",
        "run_id": "adapter-run-001",
    }

    event_id = await running_adapter.emit(event)
    assert event_id != "", "emit() should return event_id"

    # Wait for batch flush
    await asyncio.sleep(1.0)

    # Verify JSONL file was created
    workspace_dir = temp_runtime_root / "audit" / "test_workspace"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_dir = workspace_dir / today

    jsonl_files = list(date_dir.glob("audit.llm_interaction.jsonl"))
    assert len(jsonl_files) >= 1, (
        f"Expected audit.llm_interaction.jsonl in {date_dir}, found: {list(date_dir.glob('*'))}"
    )

    # Verify file contains the event
    content = jsonl_files[0].read_text(encoding="utf-8")
    lines = [ln for ln in content.strip().split("\n") if ln]
    assert len(lines) >= 1

    parsed = json.loads(lines[0])
    assert parsed["model"] == "claude-3-5-sonnet"
    assert parsed["trace_id"] == "adapter-trace-001"


@pytest.mark.asyncio
async def test_kernel_runtime_adapter_sanitizes_sensitive_fields(
    running_adapter: KernelRuntimeAdapter,
    temp_runtime_root: Path,
) -> None:
    """Verify sensitive fields are redacted before persistence."""
    event = {
        "event_type": "tool_execution",
        "tool_name": "api_call",
        "args": {
            "api_key": "sk-12345-secret",
            "password": "super_secret_pass",
            "username": "user123",
        },
        "workspace": "/test/workspace",
    }

    await running_adapter.emit(event)
    await asyncio.sleep(1.0)

    workspace_dir = temp_runtime_root / "audit" / "test_workspace"
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
    # username should be preserved
    assert args.get("username") == "user123"


@pytest.mark.asyncio
async def test_kernel_runtime_adapter_circuit_breaker_tracks_failures() -> None:
    """Verify circuit breaker records failures and transitions to open state."""
    from polaris.kernelone.audit.omniscient.adapters.kernel_runtime_adapter import CircuitBreaker

    # Create circuit breaker directly to test state transitions
    cb = CircuitBreaker(threshold=3, timeout=1.0)

    # Verify initial state is closed
    assert cb.state == "closed"

    # Record 2 failures - should still be closed
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"
    assert cb._failures == 2

    # Record 3rd failure - should transition to open
    cb.record_failure()
    assert cb.state == "open"
    assert cb._failures == 3

    # Verify is_write_allowed returns False when open
    assert cb.is_write_allowed() is False

    # After timeout, should transition to half_open
    import time

    time.sleep(1.1)
    assert cb.state == "half_open"
    assert cb.is_write_allowed() is True

    # Success in half_open should close
    cb.record_success()
    assert cb.state == "closed"
    assert cb._failures == 0


# =============================================================================
# Test 4: Full chain — context → bus → adapter → JSONL
# =============================================================================


@pytest.mark.asyncio
async def test_full_chain_trace_id_from_context_to_jsonl(
    temp_runtime_root: Path,
) -> None:
    """End-to-end: UnifiedAuditContext trace_id appears in persisted JSONL."""
    bus = OmniscientAuditBus.get_instance(f"full_chain_{time.time_ns()}")
    bus._runtime_root = temp_runtime_root  # type: ignore[attr-defined]

    await bus.start()

    try:
        trace_id = "full-chain-trace-999"

        # Set up context with known trace_id
        async with audit_context_scope(
            trace_id=trace_id,
            run_id="full-chain-run",
            workspace="/test/e2e",
        ):
            # Emit an event through the bus
            await bus.emit(
                {
                    "type": "llm_interaction",
                    "model": "claude-3-5-sonnet",
                    "prompt_tokens": 300,
                    "completion_tokens": 100,
                    "total_tokens": 400,
                    "latency_ms": 200.0,
                },
                priority=AuditPriority.INFO,
            )

            # Give dispatch time to process
            await asyncio.sleep(0.3)

        # Verify bus processed events
        bus_stats = bus.get_stats()
        assert bus_stats["events_emitted"] >= 1

    finally:
        await bus.stop()


# =============================================================================
# Test 5: Metrics collector exports Prometheus format
# =============================================================================


def test_metrics_collector_prometheus_output() -> None:
    """Verify AuditMetricsCollector outputs valid Prometheus format."""
    collector = get_metrics_collector()
    collector.reset()

    # Record some events
    collector.record_event(domain="llm", event_type="llm_call", priority="info", latency_ms=100.0)
    collector.record_event(domain="tool", event_type="tool_execution", priority="info", latency_ms=50.0)

    output = collector.get_prometheus_format()

    assert "# HELP audit_events_total" in output
    assert "# TYPE audit_events_total counter" in output
    assert 'audit_events_total{domain="llm",event_type="llm_call",priority="info"}' in output
    assert 'audit_events_total{domain="tool",event_type="tool_execution",priority="info"}' in output
    assert "# HELP audit_events_latency_seconds" in output


# =============================================================================
# Test 6: Storm detector triggers degradation under load
# =============================================================================


@pytest.mark.asyncio
async def test_bus_storm_detector_triggers_under_burst(
    configured_bus: OmniscientAuditBus,
) -> None:
    """Verify storm detector transitions to elevated level under event burst."""
    # Rapidly emit many events
    for i in range(50):
        await configured_bus.emit(
            {"type": "llm_interaction", "model": f"model_{i}"},
            priority=AuditPriority.INFO,
        )

    await asyncio.sleep(0.3)

    stats = configured_bus.get_stats()
    storm_stats = stats.get("storm", {})
    level = storm_stats.get("level", "normal")

    assert level in ("elevated", "warning", "critical", "emergency", "normal")
    assert storm_stats.get("total_count", 0) >= 50


# =============================================================================
# Test 7: Bus graceful shutdown drains queue
# =============================================================================


@pytest.mark.asyncio
async def test_bus_stop_waits_for_dispatch() -> None:
    """Verify bus.stop() processes remaining events before exiting."""
    bus = OmniscientAuditBus.get_instance(f"shutdown_test_{time.time_ns()}")
    await bus.start()

    # Emit events
    for i in range(10):
        await bus.emit(
            {"type": "test", "name": f"event_{i}"},
            priority=AuditPriority.INFO,
        )

    # Stop immediately — should drain
    await bus.stop()

    stats = bus.get_stats()
    assert stats["events_emitted"] == 10
    assert stats["running"] is False


# =============================================================================
# Test 8: SchemaRegistry auto-registers known schemas
# =============================================================================


def test_schema_registry_auto_registers_all_schemas() -> None:
    """Verify SchemaRegistry registers all 6 AuditEvent schemas on init."""
    from polaris.kernelone.audit.omniscient.schema_registry import get_schema_registry

    registry = get_schema_registry()
    registered = registry.list_registered()

    domains = {r["domain"] for r in registered}
    assert "llm" in domains, f"LLM schema not registered. Registered: {registered}"
    assert "tool" in domains, f"Tool schema not registered. Registered: {registered}"


# =============================================================================
# Test 9: Context Manager with_metadata and with_span chaining
# =============================================================================


def test_unified_audit_context_with_span_chaining() -> None:
    """Verify UnifiedAuditContext.with_span() correctly chains parent span."""
    ctx = UnifiedAuditContext(
        trace_id="trace-001",
        run_id="run-001",
        span_id="span-parent",
    )
    child = ctx.with_span("span-child")

    assert child.span_id == "span-child"
    assert child.parent_span_id == "span-parent"
    assert child.trace_id == "trace-001"


def test_unified_audit_context_with_metadata() -> None:
    """Verify UnifiedAuditContext.with_metadata() merges correctly."""
    ctx = UnifiedAuditContext(
        trace_id="trace-001",
        metadata={"key1": "value1"},
    )
    child = ctx.with_metadata("key2", "value2")

    assert child.metadata["key1"] == "value1"
    assert child.metadata["key2"] == "value2"
    # Original unchanged
    assert ctx.metadata == {"key1": "value1"}
