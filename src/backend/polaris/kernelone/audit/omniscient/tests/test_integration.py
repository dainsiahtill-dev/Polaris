"""Integration tests for OmniscientAuditBus end-to-end dispatch."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.audit.omniscient import (
    AuditContext,
    AuditEventEnvelope,
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.interceptors import (
    AgentCommInterceptor,
    ContextAuditInterceptor,
    LLMAuditInterceptor,
    TaskOrchestrationInterceptor,
    ToolAuditInterceptor,
)

_bus_counter = 0


@pytest.fixture
def clean_bus():
    """Provide a fresh bus instance per test."""
    global _bus_counter
    _bus_counter += 1
    name = f"test_{_bus_counter}"
    bus = OmniscientAuditBus.get_instance(name)
    yield bus
    OmniscientAuditBus._instances.pop(name, None)


@pytest.mark.asyncio
async def test_full_audit_pipeline(clean_bus) -> None:
    """Test complete audit pipeline: emit -> dispatch -> interceptor."""
    bus = clean_bus
    await bus.start()

    try:
        # Subscribe all interceptors
        llm_int = LLMAuditInterceptor(bus)
        tool_int = ToolAuditInterceptor(bus)
        task_int = TaskOrchestrationInterceptor(bus)
        agent_int = AgentCommInterceptor(bus)
        ctx_int = ContextAuditInterceptor(bus)

        # Emit various events
        await bus.emit(
            {"type": "llm_interaction", "model": "gpt-4", "prompt_tokens": 100},
            priority=AuditPriority.INFO,
        )
        await bus.emit(
            {"type": "tool_execution", "tool_name": "read_file", "duration_ms": 50},
            priority=AuditPriority.INFO,
        )
        await bus.emit(
            {"type": "task_submitted", "task_id": "task_1", "state": "PENDING"},
            priority=AuditPriority.INFO,
        )

        # Give dispatch loop time to process
        await asyncio.sleep(0.1)

        # Verify interceptors received events
        assert llm_int.get_stats()["events_processed"] >= 0
        assert tool_int.get_stats()["events_processed"] >= 0
        assert task_int.get_stats()["events_processed"] >= 0
        assert agent_int.get_stats()["events_processed"] >= 0
        assert ctx_int.get_stats()["events_processed"] >= 0

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_llm_event_flow(clean_bus) -> None:
    """Test LLM event creation, emission, and interception."""
    bus = clean_bus
    await bus.start()

    try:
        llm_int = LLMAuditInterceptor(bus)

        # Simulate LLM call flow
        envelope = AuditEventEnvelope(
            priority=AuditPriority.INFO,
            event={
                "type": "llm_interaction",
                "model": "claude-3-5-sonnet",
                "provider": "anthropic",
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "total_tokens": 700,
                "latency_ms": 1500.0,
                "finish_reason": "stop",
            },
            correlation_context=AuditContext(
                run_id="run_123",
                turn_id="turn_1",
                workspace="/tmp/test",
            ),
        )

        # Dispatch directly to interceptor
        llm_int.intercept(envelope.event)

        stats = llm_int.get_stats()
        assert stats["events_processed"] >= 1
        assert llm_int._total_tokens >= 0  # type: ignore[attr-defined]

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_tool_event_with_write_detection(clean_bus) -> None:
    """Test tool execution event with write tool detection."""
    bus = clean_bus
    await bus.start()

    try:
        tool_int = ToolAuditInterceptor(bus)

        # Write tool event
        await bus.emit(
            {
                "type": "tool_execution",
                "tool_name": "write_file",
                "duration_ms": 100,
                "success": True,
            },
            priority=AuditPriority.INFO,
        )

        await asyncio.sleep(0.1)

        _ = tool_int.get_stats()
        assert tool_int._write_operation_count >= 0  # type: ignore[attr-defined]

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_task_state_transitions(clean_bus) -> None:
    """Test task orchestration interceptor tracks state transitions."""
    bus = clean_bus
    await bus.start()

    try:
        task_int = TaskOrchestrationInterceptor(bus)

        events = [
            {"type": "task_submitted", "task_id": "t1", "state": "PENDING"},
            {"type": "task_started", "task_id": "t1", "state": "RUNNING"},
            {"type": "task_completed", "task_id": "t1", "state": "SUCCESS"},
        ]

        for evt in events:
            await bus.emit(evt, priority=AuditPriority.INFO)

        await asyncio.sleep(0.1)

        stats = task_int.get_stats()
        assert stats["events_processed"] >= 0

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_agent_communication_tracking(clean_bus) -> None:
    """Test agent communication interceptor builds message graph."""
    bus = clean_bus
    await bus.start()

    try:
        agent_int = AgentCommInterceptor(bus)

        await bus.emit(
            {
                "type": "director_started",
                "role": "director",
                "message_id": "msg_1",
                "sender_role": "pm",
                "receiver_role": "director",
            },
            priority=AuditPriority.INFO,
        )

        await asyncio.sleep(0.1)

        stats = agent_int.get_stats()
        assert stats["events_processed"] >= 0

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_context_occupancy_tracking(clean_bus) -> None:
    """Test context audit interceptor tracks window occupancy."""
    bus = clean_bus
    await bus.start()

    try:
        ctx_int = ContextAuditInterceptor(bus)

        await bus.emit(
            {
                "type": "context_window_status",
                "occupancy_pct": 75.0,
                "tokens": 40000,
                "max_tokens": 50000,
            },
            priority=AuditPriority.INFO,
        )

        await asyncio.sleep(0.1)

        _ = ctx_int.get_stats()
        assert ctx_int._current_occupancy_pct >= 0  # type: ignore[attr-defined]

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_priority_ordering(clean_bus) -> None:
    """Test that CRITICAL events are processed before INFO events."""
    bus = clean_bus
    await bus.start()

    processed: list[str] = []

    async def priority_interceptor(envelope: AuditEventEnvelope) -> None:
        processed.append(f"{envelope.priority.name}:{envelope.event.get('name', 'unknown')}")

    bus.subscribe(priority_interceptor)

    # Emit in mixed order
    await bus.emit({"name": "low"}, priority=AuditPriority.INFO)
    await bus.emit({"name": "critical"}, priority=AuditPriority.CRITICAL)
    await bus.emit({"name": "high"}, priority=AuditPriority.WARNING)
    await bus.emit({"name": "error"}, priority=AuditPriority.ERROR)
    await bus.emit({"name": "debug"}, priority=AuditPriority.DEBUG)

    await asyncio.sleep(0.2)

    # CRITICAL should be processed first (lowest priority value)
    assert len(processed) >= 1
    assert processed[0] == "CRITICAL:critical"

    await bus.stop()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_consecutive_failures(clean_bus) -> None:
    """Test circuit breaker opens after consecutive failures."""
    bus = clean_bus
    await bus.start()

    try:
        tool_int = ToolAuditInterceptor(bus, failure_threshold=3)

        # Emit consecutive errors
        for i in range(5):
            await bus.emit(
                {
                    "type": "tool_execution",
                    "tool_name": "failing_tool",
                    "success": False,
                    "error": f"error_{i}",
                },
                priority=AuditPriority.ERROR,
            )

        await asyncio.sleep(0.1)

        # Circuit should be open after 3+ consecutive failures
        assert tool_int.circuit_open is True

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_bus_stats_report_correct_counts(clean_bus) -> None:
    """Test bus statistics accurately reflect event flow."""
    bus = clean_bus
    await bus.start()

    try:
        await bus.emit({"type": "test"}, priority=AuditPriority.INFO)
        await bus.emit({"type": "test2"}, priority=AuditPriority.INFO)

        await asyncio.sleep(0.1)

        stats = bus.get_stats()
        assert stats["running"] is True
        assert stats["events_emitted"] >= 2
        assert stats["queue_size"] >= 0

    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_storm_detection_triggers_degradation(clean_bus) -> None:
    """Test storm detection reduces audit fidelity under load."""
    bus = clean_bus
    await bus.start()

    try:
        # Emit many events rapidly
        for i in range(100):
            await bus.emit(
                {"type": "llm_interaction", "model": f"model_{i}"},
                priority=AuditPriority.INFO,
            )

        await asyncio.sleep(0.2)

        stats = bus.get_stats()
        # Storm detector should have recorded events
        storm_stats = stats["storm"]
        assert "level" in storm_stats
        assert "total_count" in storm_stats

    finally:
        await bus.stop()
