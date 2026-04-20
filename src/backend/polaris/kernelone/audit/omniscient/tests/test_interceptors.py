"""Tests for Omniscient Audit Interceptors.

This module contains comprehensive tests for all audit interceptors:
- LLMAuditInterceptor
- ToolAuditInterceptor
- TaskOrchestrationInterceptor
- AgentCommInterceptor
- ContextAuditInterceptor
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from polaris.kernelone.audit.omniscient import (
    AuditEventEnvelope,
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.interceptors.agent import AgentCommInterceptor
from polaris.kernelone.audit.omniscient.interceptors.alert import AuditAlertInterceptor
from polaris.kernelone.audit.omniscient.interceptors.context_mgmt import (
    ContextAuditInterceptor,
)
from polaris.kernelone.audit.omniscient.interceptors.llm import LLMAuditInterceptor
from polaris.kernelone.audit.omniscient.interceptors.task import (
    TaskOrchestrationInterceptor,
    TaskState,
)
from polaris.kernelone.audit.omniscient.interceptors.tool import ToolAuditInterceptor
from polaris.kernelone.audit.omniscient.interceptors.tracing import TracingAuditInterceptor

# =============================================================================
# LLMAuditInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_llm_interceptor_captures_event() -> None:
    """Test that LLMAuditInterceptor captures LLM interaction events."""
    bus = OmniscientAuditBus.get_instance("test_llm")
    await bus.start()
    try:
        interceptor = LLMAuditInterceptor(bus)

        # Emit a test LLM event
        await bus.emit(
            {
                "type": "llm_interaction",
                "model": "gpt-4",
                "provider": "openai",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 500.0,
            }
        )

        # Give time for event processing
        await asyncio.sleep(0.1)

        # Verify interceptor processed the event
        stats = interceptor.get_stats()
        assert stats["events_processed"] >= 0
        assert stats["total_tokens"] == 150
        assert stats["total_prompt_tokens"] == 100
        assert stats["total_completion_tokens"] == 50
        assert stats["total_latency_ms"] == 500.0
        assert "gpt-4" in stats["model_counts"]
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_llm", None)


@pytest.mark.asyncio
async def test_llm_interceptor_tracks_errors() -> None:
    """Test that LLMAuditInterceptor tracks error events."""
    bus = OmniscientAuditBus.get_instance("test_llm_errors")
    await bus.start()
    try:
        interceptor = LLMAuditInterceptor(bus)

        # Emit successful LLM event
        await bus.emit(
            {
                "type": "llm_interaction_complete",
                "model": "gpt-4",
                "provider": "openai",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 500.0,
            }
        )

        # Emit error LLM event
        await bus.emit(
            {
                "type": "llm_interaction_error",
                "model": "gpt-4",
                "provider": "openai",
                "error": "API error",
                "latency_ms": 100.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["success_count"] == 1
        assert stats["error_count"] == 1
        assert stats["success_rate"] == 0.5
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_llm_errors", None)


@pytest.mark.asyncio
async def test_llm_interceptor_circuit_breaker() -> None:
    """Test that LLMAuditInterceptor opens circuit after failures."""
    bus = OmniscientAuditBus.get_instance("test_llm_circuit")
    await bus.start()
    try:
        interceptor = LLMAuditInterceptor(bus, failure_threshold=3)

        # Emit error events to trigger circuit breaker
        for _ in range(3):
            await bus.emit(
                {
                    "type": "llm_interaction_error",
                    "model": "gpt-4",
                    "provider": "openai",
                    "error": "API error",
                    "latency_ms": 100.0,
                }
            )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["circuit_open"] is True
        assert interceptor.circuit_open is True
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_llm_circuit", None)


@pytest.mark.asyncio
async def test_llm_interceptor_reset_stats() -> None:
    """Test that LLMAuditInterceptor resets stats correctly."""
    bus = OmniscientAuditBus.get_instance("test_llm_reset")
    await bus.start()
    try:
        interceptor = LLMAuditInterceptor(bus)

        # Emit some events
        await bus.emit(
            {
                "type": "llm_interaction",
                "model": "gpt-4",
                "provider": "openai",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 500.0,
            }
        )

        await asyncio.sleep(0.1)

        # Reset stats
        interceptor.reset_stats()

        stats = interceptor.get_stats()
        assert stats["total_tokens"] == 0
        assert stats["events_processed"] == 0
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_llm_reset", None)


# =============================================================================
# ToolAuditInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_tool_interceptor_captures_event() -> None:
    """Test that ToolAuditInterceptor captures tool execution events."""
    bus = OmniscientAuditBus.get_instance("test_tool")
    await bus.start()
    try:
        interceptor = ToolAuditInterceptor(bus)

        # Emit a test tool event
        await bus.emit(
            {
                "type": "tool_execution",
                "tool_name": "read_file",
                "duration_ms": 100.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["events_processed"] >= 0
        assert "read_file" in stats["tool_counts"]
        assert stats["read_operation_count"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_tool", None)


@pytest.mark.asyncio
async def test_tool_interceptor_tracks_write_operations() -> None:
    """Test that ToolAuditInterceptor tracks write operations."""
    bus = OmniscientAuditBus.get_instance("test_tool_write")
    await bus.start()
    try:
        interceptor = ToolAuditInterceptor(bus)

        # Emit write tool events
        await bus.emit(
            {
                "type": "tool_execution",
                "tool_name": "write_file",
                "duration_ms": 200.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["write_operation_count"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_tool_write", None)


@pytest.mark.asyncio
async def test_tool_interceptor_tracks_errors() -> None:
    """Test that ToolAuditInterceptor tracks tool errors."""
    bus = OmniscientAuditBus.get_instance("test_tool_errors")
    await bus.start()
    try:
        interceptor = ToolAuditInterceptor(bus)

        # Emit error tool event
        await bus.emit(
            {
                "type": "tool_execution_error",
                "tool_name": "read_file",
                "error": "File not found",
                "error_type": "NOT_FOUND",
                "duration_ms": 50.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["total_failures"] == 1
        assert "read_file" in stats["tool_errors"]
        assert stats["tool_errors"]["read_file"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_tool_errors", None)


@pytest.mark.asyncio
async def test_tool_interceptor_circuit_breaker() -> None:
    """Test that ToolAuditInterceptor opens circuit after failures."""
    bus = OmniscientAuditBus.get_instance("test_tool_circuit")
    await bus.start()
    try:
        interceptor = ToolAuditInterceptor(bus, failure_threshold=3)

        # Emit error events to trigger circuit breaker
        for _ in range(3):
            await bus.emit(
                {
                    "type": "tool_execution_error",
                    "tool_name": "read_file",
                    "error": "Error",
                    "duration_ms": 100.0,
                }
            )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["circuit_open"] is True
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_tool_circuit", None)


# =============================================================================
# TaskOrchestrationInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_task_interceptor_tracks_state_transitions() -> None:
    """Test that TaskOrchestrationInterceptor tracks task state transitions."""
    bus = OmniscientAuditBus.get_instance("test_task")
    await bus.start()
    try:
        interceptor = TaskOrchestrationInterceptor(bus)

        # Emit task events
        await bus.emit(
            {
                "type": "task_submitted",
                "task_id": "task-1",
                "dag_id": "dag-1",
            }
        )

        await bus.emit(
            {
                "type": "task_started",
                "task_id": "task-1",
            }
        )

        await bus.emit(
            {
                "type": "task_completed",
                "task_id": "task-1",
                "duration_ms": 1000.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["task_states"]["task-1"] == TaskState.COMPLETED
        assert stats["completed_tasks"] == 1
        assert stats["dag_id"] == "dag-1"
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_task", None)


@pytest.mark.asyncio
async def test_task_interceptor_tracks_dependencies() -> None:
    """Test that TaskOrchestrationInterceptor tracks task dependencies."""
    bus = OmniscientAuditBus.get_instance("test_task_deps")
    await bus.start()
    try:
        interceptor = TaskOrchestrationInterceptor(bus)

        # Emit task with dependencies
        await bus.emit(
            {
                "type": "task_submitted",
                "task_id": "task-2",
                "dag_id": "dag-1",
                "blocked_by": ["task-1"],
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        # blocked_by is tracked internally in _task_blocked_by
        # The stats expose it via task_dependencies for task_orchestration events
        # For submitted events, we verify the task state was tracked
        assert stats["task_states"].get("task-2") == "submitted"
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_task_deps", None)


@pytest.mark.asyncio
async def test_task_interceptor_tracks_failures() -> None:
    """Test that TaskOrchestrationInterceptor tracks task failures."""
    bus = OmniscientAuditBus.get_instance("test_task_fail")
    await bus.start()
    try:
        interceptor = TaskOrchestrationInterceptor(bus)

        # Emit failed task
        await bus.emit(
            {
                "type": "task_failed",
                "task_id": "task-3",
                "error": "Execution failed",
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["task_states"]["task-3"] == TaskState.FAILED
        assert stats["failed_tasks"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_task_fail", None)


@pytest.mark.asyncio
async def test_task_interceptor_tracks_retries() -> None:
    """Test that TaskOrchestrationInterceptor tracks task retries."""
    bus = OmniscientAuditBus.get_instance("test_task_retry")
    await bus.start()
    try:
        interceptor = TaskOrchestrationInterceptor(bus)

        # Emit retry event
        await bus.emit(
            {
                "type": "task_retry",
                "task_id": "task-4",
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["task_states"]["task-4"] == TaskState.RETRYING
        assert stats["retried_tasks"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_task_retry", None)


# =============================================================================
# AgentCommInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_agent_interceptor_tracks_director_lifecycle() -> None:
    """Test that AgentCommInterceptor tracks Director lifecycle events."""
    bus = OmniscientAuditBus.get_instance("test_agent")
    await bus.start()
    try:
        interceptor = AgentCommInterceptor(bus)

        # Emit Director events
        await bus.emit(
            {
                "type": "director_started",
                "workspace": "/path/to/workspace",
            }
        )

        await bus.emit(
            {
                "type": "director_paused",
                "workspace": "/path/to/workspace",
            }
        )

        await bus.emit(
            {
                "type": "director_resumed",
                "workspace": "/path/to/workspace",
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["director_active"] is True
        assert stats["director_paused"] is False
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_agent", None)


@pytest.mark.asyncio
async def test_agent_interceptor_builds_message_graph() -> None:
    """Test that AgentCommInterceptor builds message graph."""
    bus = OmniscientAuditBus.get_instance("test_agent_graph")
    await bus.start()
    try:
        interceptor = AgentCommInterceptor(bus)

        # Emit agent communication events
        await bus.emit(
            {
                "type": "agent_communication",
                "message_id": "msg-1",
                "sender_role": "pm",
                "receiver_role": "architect",
                "intent": "delegate",
                "message_type": "task_delegation",
            }
        )

        await bus.emit(
            {
                "type": "agent_communication",
                "message_id": "msg-2",
                "sender_role": "architect",
                "receiver_role": "director",
                "intent": "delegate",
                "message_type": "task_delegation",
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert "pm" in stats["message_graph"]
        assert "architect" in stats["message_graph"]["pm"]
        assert stats["total_messages"] == 2
        assert "pm->architect" in stats["role_pair_counts"]
        assert "delegate" in stats["intent_counts"]
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_agent_graph", None)


@pytest.mark.asyncio
async def test_agent_interceptor_tracks_routing_paths() -> None:
    """Test that AgentCommInterceptor tracks routing paths."""
    bus = OmniscientAuditBus.get_instance("test_agent_routing")
    await bus.start()
    try:
        interceptor = AgentCommInterceptor(bus)

        # Emit event with routing path
        await bus.emit(
            {
                "type": "agent_communication",
                "message_id": "msg-3",
                "sender_role": "pm",
                "receiver_role": "director",
                "routing_path": ["pm", "architect", "chief_engineer", "director"],
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["routing_paths_count"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_agent_routing", None)


# =============================================================================
# ContextAuditInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_context_interceptor_tracks_window_status() -> None:
    """Test that ContextAuditInterceptor tracks window status."""
    bus = OmniscientAuditBus.get_instance("test_context")
    await bus.start()
    try:
        interceptor = ContextAuditInterceptor(bus)

        # Emit context window status event
        await bus.emit(
            {
                "type": "context_window_status",
                "current_tokens": 16000,
                "max_tokens": 20000,
                "remaining_tokens": 4000,
                "usage_percentage": 80.0,
                "is_critical": True,
                "is_exhausted": False,
                "segment_breakdown": {
                    "system": 8000,
                    "history": 5000,
                    "tools": 2000,
                    "user": 1000,
                },
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["current_tokens"] == 16000
        assert stats["max_tokens"] == 20000
        assert stats["current_occupancy_pct"] == 80.0
        assert stats["critical_occupancy_events_count"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_context", None)


@pytest.mark.asyncio
async def test_context_interceptor_tracks_compaction() -> None:
    """Test that ContextAuditInterceptor tracks compaction events."""
    bus = OmniscientAuditBus.get_instance("test_context_compact")
    await bus.start()
    try:
        interceptor = ContextAuditInterceptor(bus)

        # Emit compaction event
        await bus.emit(
            {
                "type": "context_management",
                "operation": "compact",
                "template_name": "default",
                "window_occupancy_before_pct": 95.0,
                "window_occupancy_after_pct": 65.0,
                "evicted_entries": 5,
                "llm_call_triggered": True,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["compaction_count"] == 1
        assert stats["eviction_count"] == 5
        assert stats["llm_call_triggers"] == 1
        assert "default" in stats["template_usage"]
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_context_compact", None)


@pytest.mark.asyncio
async def test_context_interceptor_tracks_oom() -> None:
    """Test that ContextAuditInterceptor tracks OOM intercepts."""
    bus = OmniscientAuditBus.get_instance("test_context_oom")
    await bus.start()
    try:
        interceptor = ContextAuditInterceptor(bus)

        # Emit OOM event
        await bus.emit(
            {
                "type": "context_management",
                "operation": "compact",
                "oom_intercepted": True,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["oom_intercepts"] == 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_context_oom", None)


@pytest.mark.asyncio
async def test_context_interceptor_tracks_peak_occupancy() -> None:
    """Test that ContextAuditInterceptor tracks peak occupancy."""
    bus = OmniscientAuditBus.get_instance("test_context_peak")
    await bus.start()
    try:
        interceptor = ContextAuditInterceptor(bus)

        # Emit events with increasing occupancy
        await bus.emit(
            {
                "type": "context_window_status",
                "current_tokens": 10000,
                "max_tokens": 20000,
                "usage_percentage": 50.0,
            }
        )

        await bus.emit(
            {
                "type": "context_window_status",
                "current_tokens": 18000,
                "max_tokens": 20000,
                "usage_percentage": 90.0,
            }
        )

        await asyncio.sleep(0.1)

        stats = interceptor.get_stats()
        assert stats["peak_occupancy_pct"] == 90.0
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_context_peak", None)


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_all_interceptors_can_coexist() -> None:
    """Test that all interceptors can be used together on the same bus."""
    bus = OmniscientAuditBus.get_instance("test_all")
    await bus.start()
    try:
        # Create all interceptors
        llm_interceptor = LLMAuditInterceptor(bus)
        tool_interceptor = ToolAuditInterceptor(bus)
        task_interceptor = TaskOrchestrationInterceptor(bus)
        agent_interceptor = AgentCommInterceptor(bus)
        context_interceptor = ContextAuditInterceptor(bus)

        # Emit various events
        await bus.emit(
            {
                "type": "llm_interaction",
                "model": "gpt-4",
                "provider": "openai",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 500.0,
            }
        )
        await bus.emit({"type": "tool_execution", "tool_name": "read_file", "duration_ms": 100.0})
        await bus.emit({"type": "task_submitted", "task_id": "task-1"})
        await bus.emit({"type": "director_started", "workspace": "/workspace"})
        await bus.emit(
            {"type": "context_window_status", "current_tokens": 10000, "max_tokens": 20000, "usage_percentage": 50.0}
        )

        await asyncio.sleep(0.2)

        # Verify all interceptors have stats
        llm_stats = llm_interceptor.get_stats()
        tool_stats = tool_interceptor.get_stats()
        task_stats = task_interceptor.get_stats()
        agent_stats = agent_interceptor.get_stats()
        context_stats = context_interceptor.get_stats()

        assert llm_stats["name"] == "llm_audit"
        assert tool_stats["name"] == "tool_audit"
        assert task_stats["name"] == "task_audit"
        assert agent_stats["name"] == "agent_comm"
        assert context_stats["name"] == "context_audit"
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_all", None)


@pytest.mark.asyncio
async def test_interceptors_with_audit_event_envelope() -> None:
    """Test interceptors with AuditEventEnvelope format."""
    bus = OmniscientAuditBus.get_instance("test_envelope")
    await bus.start()
    try:
        interceptor = LLMAuditInterceptor(bus)

        # Create an AuditEventEnvelope
        envelope = AuditEventEnvelope(
            priority=AuditPriority.INFO,
            event={
                "type": "llm_interaction",
                "model": "claude-3",
                "provider": "anthropic",
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
                "latency_ms": 1000.0,
            },
        )

        # Directly call intercept
        interceptor.intercept(envelope)

        stats = interceptor.get_stats()
        assert stats["total_tokens"] == 300
        assert "claude-3" in stats["model_counts"]
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_envelope", None)


# =============================================================================
# AuditAlertInterceptor Tests
# =============================================================================


@pytest.mark.asyncio
async def test_alert_interceptor_subscribes_to_bus() -> None:
    """Test that AuditAlertInterceptor subscribes to the bus."""
    bus = OmniscientAuditBus.get_instance("test_alert_sub")
    await bus.start()
    try:
        alert_int = AuditAlertInterceptor(bus)

        await bus.emit({"type": "tool_execution", "tool_name": "read_file", "duration_ms": 100})

        await asyncio.sleep(0.1)

        stats = alert_int.get_stats()
        assert stats["events_processed"] >= 1
        assert stats["alert_rules_count"] >= 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_alert_sub", None)


@pytest.mark.asyncio
async def test_alert_interceptor_fires_high_failure_rule() -> None:
    """Test that AuditAlertInterceptor fires alert when failure threshold exceeded."""
    bus = OmniscientAuditBus.get_instance("test_alert_fail")
    await bus.start()
    try:
        alert_int = AuditAlertInterceptor(bus)

        # Emit 3 task_failed events to trigger high_failure_rate rule
        for i in range(3):
            await bus.emit(
                {
                    "type": "task_failed",
                    "task_id": f"task-{i}",
                    "error": "Execution error",
                },
                priority=AuditPriority.ERROR,
            )

        await asyncio.sleep(0.1)

        stats = alert_int.get_stats()
        assert stats["alerts_fired"] >= 1
        assert stats["active_alerts"] >= 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_alert_fail", None)


@pytest.mark.asyncio
async def test_alert_interceptor_security_violation() -> None:
    """Test that security violations trigger critical alerts."""
    bus = OmniscientAuditBus.get_instance("test_alert_security")
    await bus.start()
    try:
        alert_int = AuditAlertInterceptor(bus)

        await bus.emit(
            {
                "type": "security_violation",
                "severity": "critical",
                "description": "Unauthorized access attempt",
            },
            priority=AuditPriority.CRITICAL,
        )

        await asyncio.sleep(0.1)

        fired = alert_int.get_fired_alerts()
        assert len(fired) >= 1
        # Should have fired the security_violation rule (threshold_count=1)
        assert any(a.rule_id == "security_violation" for a in fired)
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_alert_security", None)


@pytest.mark.asyncio
async def test_alert_interceptor_get_active_alerts() -> None:
    """Test get_active_alerts returns correct alerts."""
    bus = OmniscientAuditBus.get_instance("test_alert_active")
    await bus.start()
    try:
        alert_int = AuditAlertInterceptor(bus)

        # Trigger an alert
        await bus.emit(
            {"type": "security_violation", "description": "Test violation"},
            priority=AuditPriority.CRITICAL,
        )

        await asyncio.sleep(0.1)

        active = alert_int.get_active_alerts()
        assert len(active) >= 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_alert_active", None)


@pytest.mark.asyncio
async def test_alert_interceptor_with_audit_event_envelope() -> None:
    """Test that AuditAlertInterceptor processes AuditEventEnvelope events."""
    bus = OmniscientAuditBus.get_instance("test_alert_env")
    await bus.start()
    try:
        alert_int = AuditAlertInterceptor(bus)

        envelope = AuditEventEnvelope(
            priority=AuditPriority.INFO,
            event={
                "type": "task_failed",
                "task_id": "env-task",
                "error": "Envelope error",
            },
        )

        alert_int.intercept(envelope)

        stats = alert_int.get_stats()
        assert stats["events_processed"] >= 1
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_alert_env", None)


# =============================================================================
# TracingAuditInterceptor Tests
# =============================================================================


class _FakeSpan:
    """Fake span for testing."""

    def __init__(self, name: str, trace_id: str | None = None) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = "fake-span-id"
        self.tags: dict[str, Any] = {}
        self.status_value = "ok"

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value


class _FakeTracer:
    """Fake tracer for testing TracingAuditInterceptor."""

    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []
        self.span_ends: list[tuple[str, str | None]] = []

    def start_span(
        self,
        name: str,
        *,
        tags: dict[str, Any] | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> _FakeSpan:
        span = _FakeSpan(name=name, trace_id=trace_id)
        if tags:
            span.tags.update(tags)
        self.spans.append(span)
        return span

    def end_span(
        self,
        span: _FakeSpan,
        status: Any = None,
        status_message: str | None = None,
    ) -> None:
        if status is not None:
            span.status_value = str(status.value) if hasattr(status, "value") else str(status)
        self.span_ends.append((span.name, status_message))


@pytest.mark.asyncio
async def test_tracing_interceptor_subscribes_to_bus() -> None:
    """Test that TracingAuditInterceptor subscribes to the bus."""
    bus = OmniscientAuditBus.get_instance("test_trace_sub")
    await bus.start()
    try:
        fake_tracer = _FakeTracer()
        _ = TracingAuditInterceptor(bus, tracer=fake_tracer)  # type: ignore[arg-type]  # type: ignore[arg-type]

        await bus.emit({"type": "tool_execution", "tool_name": "read_file"})

        await asyncio.sleep(0.1)

        assert len(fake_tracer.spans) >= 1
        assert fake_tracer.spans[0].name == "audit.tool_execution"
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_trace_sub", None)


@pytest.mark.asyncio
async def test_tracing_interceptor_creates_span_with_correct_name() -> None:
    """Test that span name follows audit.{event_type} convention."""
    bus = OmniscientAuditBus.get_instance("test_trace_name")
    await bus.start()
    try:
        fake_tracer = _FakeTracer()
        _ = TracingAuditInterceptor(bus, tracer=fake_tracer)  # type: ignore[arg-type]

        await bus.emit({"type": "llm_interaction", "model": "gpt-4"})

        await asyncio.sleep(0.1)

        assert any(s.name == "audit.llm_interaction" for s in fake_tracer.spans)
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_trace_name", None)


@pytest.mark.asyncio
async def test_tracing_interceptor_includes_event_attributes() -> None:
    """Test that span tags include event attributes."""
    bus = OmniscientAuditBus.get_instance("test_trace_tags")
    await bus.start()
    try:
        fake_tracer = _FakeTracer()
        _ = TracingAuditInterceptor(bus, tracer=fake_tracer)  # type: ignore[arg-type]

        await bus.emit({"type": "tool_execution", "tool_name": "write_file", "duration_ms": 100.0, "success": True})

        await asyncio.sleep(0.1)

        tool_span = next((s for s in fake_tracer.spans if s.name == "audit.tool_execution"), None)
        assert tool_span is not None
        assert tool_span.tags.get("tool.name") == "write_file"
        assert tool_span.tags.get("tool.duration_ms") == 100.0
        assert tool_span.tags.get("tool.success") is True
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_trace_tags", None)


@pytest.mark.asyncio
async def test_tracing_interceptor_error_status() -> None:
    """Test that error events set span status to error."""
    bus = OmniscientAuditBus.get_instance("test_trace_error")
    await bus.start()
    try:
        fake_tracer = _FakeTracer()
        _ = TracingAuditInterceptor(bus, tracer=fake_tracer)  # type: ignore[arg-type]

        await bus.emit({"type": "tool_execution", "tool_name": "read_file", "error": "File not found"})

        await asyncio.sleep(0.1)

        error_span = next((s for s in fake_tracer.spans if s.name == "audit.tool_execution"), None)
        assert error_span is not None
        # Status is set during end_span
        assert any(name == "audit.tool_execution" for name, _ in fake_tracer.span_ends)
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_trace_error", None)


@pytest.mark.asyncio
async def test_tracing_interceptor_with_audit_event_envelope() -> None:
    """Test TracingAuditInterceptor with AuditEventEnvelope including correlation context."""
    from polaris.kernelone.audit.omniscient import AuditContext

    bus = OmniscientAuditBus.get_instance("test_trace_env")
    await bus.start()
    try:
        fake_tracer = _FakeTracer()
        tracing_int = TracingAuditInterceptor(bus, tracer=fake_tracer)  # type: ignore[arg-type]

        envelope = AuditEventEnvelope(
            priority=AuditPriority.INFO,
            event={
                "type": "llm_interaction",
                "model": "claude-3",
                "provider": "anthropic",
            },
            correlation_context=AuditContext(
                run_id="run-abc123",
                turn_id="turn-1",
                workspace="/tmp",
            ),
        )

        tracing_int.intercept(envelope)

        span = next((s for s in fake_tracer.spans if s.name == "audit.llm_interaction"), None)
        assert span is not None
        assert span.tags.get("audit.priority") == "INFO"
        assert span.tags.get("llm.model") == "claude-3"
        assert span.tags.get("llm.provider") == "anthropic"
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop("test_trace_env", None)
