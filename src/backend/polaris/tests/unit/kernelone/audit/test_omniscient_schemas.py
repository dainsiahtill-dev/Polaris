"""Unit tests for polaris.kernelone.audit.omniscient schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent, EventDomain
from polaris.kernelone.audit.omniscient.schemas.context_event import ContextEvent, ContextOperation
from polaris.kernelone.audit.omniscient.schemas.dialogue_event import DialogueEvent, MessageDirection, MessageType
from polaris.kernelone.audit.omniscient.schemas.llm_event import LLMEvent, LLMFinishReason, LLMStrategy
from polaris.kernelone.audit.omniscient.schemas.task_event import TaskEvent, TaskState
from polaris.kernelone.audit.omniscient.schemas.tool_event import ToolCategory, ToolEvent


class TestAuditEvent:
    def test_default_construction(self) -> None:
        event = AuditEvent()
        assert event.version == "3.0"
        assert event.domain == EventDomain.SYSTEM
        assert event.event_type == ""
        assert len(event.event_id) == 32

    def test_to_audit_dict(self) -> None:
        event = AuditEvent(domain=EventDomain.LLM, event_type="llm_call", role="director")
        d = event.to_audit_dict()
        assert d["domain"] == "llm"
        assert d["event_type"] == "llm_call"
        assert d["role"] == "director"
        assert d["timestamp"].endswith("Z")

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        data = {
            "event_id": "e1",
            "version": "3.0",
            "domain": "llm",
            "event_type": "call",
            "timestamp": ts,
            "trace_id": "abcd1234abcd1234",
            "run_id": "r1",
            "priority": "info",
            "workspace": "/ws",
            "role": "pm",
            "data": {"key": "val"},
            "correlation_context": {"cc": "v"},
        }
        event = AuditEvent.from_audit_dict(data)
        assert event.event_id == "e1"
        assert event.domain == EventDomain.LLM
        assert event.trace_id == "abcd1234abcd1234"

    def test_trace_id_validator_too_short(self) -> None:
        with pytest.raises(ValueError, match="trace_id must be 16-32 chars"):
            AuditEvent(trace_id="short")

    def test_trace_id_validator_non_hex(self) -> None:
        with pytest.raises(ValueError, match="trace_id must be hexadecimal"):
            AuditEvent(trace_id="gggggggggggggggg")

    def test_with_trace(self) -> None:
        event = AuditEvent(trace_id="abcd1234abcd1234")
        new_event = event.with_trace("efgh5678efgh5678")
        assert new_event.trace_id == "efgh5678efgh5678"
        assert new_event.span_id != ""

    def test_with_priority(self) -> None:
        from polaris.kernelone.audit.omniscient.bus import AuditPriority

        event = AuditEvent()
        new_event = event.with_priority(AuditPriority.CRITICAL)
        assert new_event.priority == AuditPriority.CRITICAL

    def test_with_data(self) -> None:
        event = AuditEvent(data={"a": 1})
        new_event = event.with_data(b=2)
        assert new_event.data == {"a": 1, "b": 2}


class TestContextEvent:
    def test_defaults(self) -> None:
        event = ContextEvent()
        assert event.domain == EventDomain.CONTEXT
        assert event.operation == ContextOperation.READ

    def test_utilization_computed(self) -> None:
        event = ContextEvent.create(
            operation=ContextOperation.RENDER,
            context_window_used=50,
            context_window_limit=100,
        )
        assert event.utilization_percent == 50.0

    def test_utilization_zero_limit(self) -> None:
        event = ContextEvent.create(operation=ContextOperation.READ, context_window_used=10, context_window_limit=0)
        assert event.utilization_percent == 0.0

    def test_to_audit_dict(self) -> None:
        event = ContextEvent.create(operation=ContextOperation.WRITE, context_window_used=10, context_window_limit=100)
        d = event.to_audit_dict()
        assert d["operation"] == "write"
        assert d["utilization_percent"] == 10.0

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "operation": "compact",
            "template_name": "t1",
            "context_window_used": 80,
            "context_window_limit": 100,
            "utilization_percent": 80.0,
            "memory_items": 5,
            "compaction_triggered": True,
            "items_removed": 2,
        }
        event = ContextEvent.from_audit_dict(data)
        assert event.operation == ContextOperation.COMPACT
        assert event.compaction_triggered is True


class TestDialogueEvent:
    def test_defaults(self) -> None:
        event = DialogueEvent()
        assert event.domain == EventDomain.DIALOGUE
        assert event.message_type == MessageType.REQUEST
        assert event.direction == MessageDirection.SENT

    def test_create(self) -> None:
        event = DialogueEvent.create(from_role="pm", to_role="director", message_summary="hello")
        assert event.from_role == "pm"
        assert event.to_role == "director"
        assert event.role == "pm"

    def test_to_audit_dict(self) -> None:
        event = DialogueEvent.create(from_role="a", to_role="b", message_type=MessageType.ARTIFACT)
        d = event.to_audit_dict()
        assert d["message_type"] == "artifact"
        assert d["direction"] == "sent"

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "from_role": "architect",
            "to_role": "chief_engineer",
            "message_type": "response",
            "direction": "received",
            "channel": "async",
            "message_summary": "design approved",
            "artifact_id": "art1",
            "session_id": "sess1",
        }
        event = DialogueEvent.from_audit_dict(data)
        assert event.from_role == "architect"
        assert event.direction == MessageDirection.RECEIVED


class TestLLMEvent:
    def test_defaults(self) -> None:
        event = LLMEvent()
        assert event.domain == EventDomain.LLM
        assert event.strategy == LLMStrategy.PRIMARY

    def test_total_tokens_computed(self) -> None:
        event = LLMEvent(prompt_tokens=100, completion_tokens=50)
        assert event.total_tokens == 150

    def test_tokens_per_second(self) -> None:
        event = LLMEvent(prompt_tokens=100, completion_tokens=100, latency_ms=1000.0)
        assert event.tokens_per_second == 200.0

    def test_tokens_per_second_zero_latency(self) -> None:
        event = LLMEvent(latency_ms=0.0)
        assert event.tokens_per_second == 0.0

    def test_is_success(self) -> None:
        assert LLMEvent(error="", finish_reason=LLMFinishReason.STOP).is_success is True
        assert LLMEvent(error="boom").is_success is False
        assert LLMEvent(finish_reason=LLMFinishReason.ERROR).is_success is False

    def test_preview_truncation(self) -> None:
        long_text = "x" * 600
        event = LLMEvent(prompt_preview=long_text)
        assert len(event.prompt_preview) == 500

    def test_create(self) -> None:
        event = LLMEvent.create(model="claude", provider="anthropic", prompt_tokens=10)
        assert event.model == "claude"
        assert event.provider == "anthropic"

    def test_to_audit_dict(self) -> None:
        event = LLMEvent.create(
            model="m",
            provider="p",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=100.0,
            strategy=LLMStrategy.FALLBACK,
            finish_reason=LLMFinishReason.STOP,
        )
        d = event.to_audit_dict()
        assert d["model"] == "m"
        assert d["strategy"] == "fallback"
        assert d["total_tokens"] == 15
        assert d["is_success"] is True

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "model": "gpt-4",
            "provider": "openai",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "latency_ms": 500.0,
            "first_token_latency_ms": 50.0,
            "strategy": "cache_hit",
            "fallback_model": "",
            "finish_reason": "stop",
            "error": "",
            "error_type": "",
            "prompt_preview": "hello",
            "completion_preview": "world",
            "safety_flags": [],
            "thinking_enabled": False,
            "temperature": 0.7,
            "max_tokens": 1000,
        }
        event = LLMEvent.from_audit_dict(data)
        assert event.model == "gpt-4"
        assert event.strategy == LLMStrategy.CACHE_HIT
        assert event.finish_reason == LLMFinishReason.STOP


class TestTaskEvent:
    def test_defaults(self) -> None:
        event = TaskEvent()
        assert event.domain == EventDomain.TASK
        assert event.state == TaskState.PENDING

    def test_create(self) -> None:
        event = TaskEvent.create(task_id="t1", state=TaskState.RUNNING, task_name="my_task")
        assert event.task_id == "t1"
        assert event.task_name == "my_task"

    def test_to_audit_dict(self) -> None:
        event = TaskEvent.create(
            task_id="t1",
            state=TaskState.COMPLETED,
            previous_state=TaskState.RUNNING,
        )
        d = event.to_audit_dict()
        assert d["state"] == "completed"
        assert d["previous_state"] == "running"

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "task_id": "t1",
            "task_name": "task",
            "state": "failed",
            "previous_state": "running",
            "assigned_role": "director",
            "claim_time_ms": 100.0,
            "execution_time_ms": 500.0,
            "retry_count": 1,
            "max_retries": 3,
            "deadline": "2024-01-01T00:00:00Z",
            "timeout_warning": False,
            "deadlock_detected": False,
        }
        event = TaskEvent.from_audit_dict(data)
        assert event.state == TaskState.FAILED
        assert event.previous_state == TaskState.RUNNING

    def test_from_audit_dict_invalid_previous_state(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "state": "pending",
            "previous_state": "invalid_state",
        }
        event = TaskEvent.from_audit_dict(data)
        assert event.previous_state is None


class TestToolEvent:
    def test_defaults(self) -> None:
        event = ToolEvent()
        assert event.domain == EventDomain.TOOL
        assert event.category == ToolCategory.OTHER
        assert event.read_only is True

    def test_total_latency_ms(self) -> None:
        event = ToolEvent(latency_ms=100.0, queue_latency_ms=50.0)
        assert event.total_latency_ms == 150.0

    def test_is_success_no_error(self) -> None:
        assert ToolEvent(error="", status_code=0).is_success is True

    def test_is_success_with_error(self) -> None:
        assert ToolEvent(error="fail").is_success is False

    def test_is_success_http_error(self) -> None:
        assert ToolEvent(error="", status_code=500).is_success is False

    def test_is_success_http_ok(self) -> None:
        assert ToolEvent(error="", status_code=200).is_success is True

    def test_create(self) -> None:
        event = ToolEvent.create(
            tool_name="read_file",
            category=ToolCategory.FILE,
            input_args={"path": "/tmp"},
            latency_ms=5.0,
        )
        assert event.tool_name == "read_file"
        assert event.category == ToolCategory.FILE

    def test_to_audit_dict(self) -> None:
        event = ToolEvent.create(tool_name="t", latency_ms=10.0, error="", status_code=0)
        d = event.to_audit_dict()
        assert d["tool_name"] == "t"
        assert d["is_success"] is True

    def test_from_audit_dict(self) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        data = {
            "event_id": "e1",
            "timestamp": ts,
            "tool_name": "search",
            "category": "search",
            "input_args": {"query": "foo"},
            "output_summary": "found",
            "status_code": 0,
            "latency_ms": 20.0,
            "queue_latency_ms": 5.0,
            "cpu_time_ms": 10.0,
            "error": "",
            "error_type": "",
            "exception_stack": "",
            "cache_hit": False,
            "read_only": True,
        }
        event = ToolEvent.from_audit_dict(data)
        assert event.tool_name == "search"
        assert event.category == ToolCategory.SEARCH
