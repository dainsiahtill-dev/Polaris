"""Tests for polaris.kernelone.contracts.technical.master_types.

Covers Envelope, Effect, EffectTracker, TraceContext, Stream events,
Health types, Lock types, and Scheduler types.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.contracts.technical.master_types import (
    Effect,
    EffectTracker,
    EffectType,
    Envelope,
    KernelError,
    LockAcquireResult,
    LockOptions,
    LockReleaseResult,
    RuntimeHealthReport,
    ScheduledTask,
    ScheduleKind,
    ScheduleResult,
    ScheduleSpec,
    StreamChunk,
    StreamDone,
    StreamError,
    SubsystemHealth,
    SubsystemStatus,
    TraceContext,
    _new_event_id,
    _new_run_id,
)


class TestIdentityHelpers:
    def test_new_event_id_is_hex(self) -> None:
        eid = _new_event_id()
        assert len(eid) == 32
        assert int(eid, 16) >= 0

    def test_new_run_id_is_shorter_hex(self) -> None:
        rid = _new_run_id()
        assert len(rid) == 12
        assert int(rid, 16) >= 0

    def test_event_ids_are_unique(self) -> None:
        ids = {_new_event_id() for _ in range(100)}
        assert len(ids) == 100

    def test_run_ids_are_unique(self) -> None:
        ids = {_new_run_id() for _ in range(100)}
        assert len(ids) == 100


class TestEnvelope:
    def test_default_construction(self) -> None:
        env = Envelope()
        assert env.event_id
        assert env.version == "2.0"
        assert env.correlation_id == ""
        assert env.source == ""
        assert env.payload is None
        assert env.metadata == {}

    def test_custom_construction(self) -> None:
        env = Envelope(
            event_id="evt-1",
            correlation_id="corr-1",
            source="test",
            payload={"data": 1},
            metadata={"key": "value"},
        )
        assert env.event_id == "evt-1"
        assert env.correlation_id == "corr-1"
        assert env.source == "test"
        assert env.payload == {"data": 1}
        assert env.metadata == {"key": "value"}

    def test_to_dict(self) -> None:
        env = Envelope(payload="hello", metadata={"k": "v"})
        d = env.to_dict()
        assert d["event_id"] == env.event_id
        assert d["version"] == "2.0"
        assert d["payload"] == "hello"
        assert d["metadata"] == {"k": "v"}
        assert isinstance(d["timestamp"], str)

    def test_from_dict_round_trip(self) -> None:
        original = Envelope(payload={"x": 1}, source="src")
        restored = Envelope.from_dict(original.to_dict())
        assert restored.event_id == original.event_id
        assert restored.version == original.version
        assert restored.source == original.source
        assert restored.payload == original.payload

    def test_from_dict_with_z_timestamp(self) -> None:
        d = {
            "event_id": "abc",
            "timestamp": "2024-01-15T10:30:00Z",
            "version": "1.0",
            "correlation_id": "",
            "source": "",
            "payload": None,
            "metadata": {},
        }
        env = Envelope.from_dict(d)
        assert env.timestamp.year == 2024
        assert env.timestamp.month == 1
        assert env.timestamp.day == 15

    def test_wrap_response(self) -> None:
        req = Envelope(event_id="req-1", source="client")
        resp = req.wrap_response({"result": "ok"})
        assert resp.correlation_id == "req-1"
        assert resp.payload == {"result": "ok"}
        assert resp.event_id != "req-1"
        assert resp.source == "client"

    def test_frozen_immutable(self) -> None:
        env = Envelope()
        with pytest.raises(AttributeError):
            env.version = "3.0"  # type: ignore[misc]


class TestEffect:
    def test_default_construction(self) -> None:
        eff = Effect()
        assert eff.effect_id
        assert eff.effect_type == EffectType.FS_READ
        assert eff.resource == ""
        assert eff.principal == ""
        assert eff.correlation_id == ""
        assert eff.metadata == {}
        assert eff.payload_bytes == 0

    def test_custom_construction(self) -> None:
        eff = Effect(
            effect_type=EffectType.LLM_CALL,
            resource="gpt-4",
            principal="test-agent",
            payload_bytes=1024,
        )
        assert eff.effect_type == EffectType.LLM_CALL
        assert eff.resource == "gpt-4"
        assert eff.principal == "test-agent"
        assert eff.payload_bytes == 1024

    def test_to_dict(self) -> None:
        eff = Effect(effect_type=EffectType.DB_WRITE, resource="users")
        d = eff.to_dict()
        assert d["effect_type"] == "db.write"
        assert d["resource"] == "users"
        assert isinstance(d["timestamp"], str)


class TestEffectTracker:
    def test_empty_tracker(self) -> None:
        tracker = EffectTracker("op-1")
        assert tracker.effects == []

    def test_declare_effect(self) -> None:
        tracker = EffectTracker("op-1")
        eff = tracker.declare(EffectType.FS_READ, "/path/to/file")
        assert len(tracker.effects) == 1
        assert eff.effect_type == EffectType.FS_READ
        assert eff.resource == "/path/to/file"
        assert eff.principal == "kernel"

    def test_declare_fs_read(self) -> None:
        tracker = EffectTracker("op-1")
        eff = tracker.declare_fs_read("/tmp/test.txt")
        assert eff.effect_type == EffectType.FS_READ
        assert eff.resource == "/tmp/test.txt"

    def test_declare_fs_write(self) -> None:
        tracker = EffectTracker("op-1")
        eff = tracker.declare_fs_write("/tmp/out.txt", payload_bytes=256)
        assert eff.effect_type == EffectType.FS_WRITE
        assert eff.payload_bytes == 256

    def test_declare_llm_call(self) -> None:
        tracker = EffectTracker("op-1")
        eff = tracker.declare_llm_call("claude-3", prompt_tokens=100)
        assert eff.effect_type == EffectType.LLM_CALL
        assert eff.resource == "claude-3"
        assert eff.metadata.get("prompt_tokens") == 100

    def test_finalize(self) -> None:
        tracker = EffectTracker("op-1", principal="agent")
        tracker.declare_fs_read("/a")
        tracker.declare_fs_read("/b")
        op_id, effects = tracker.finalize()
        assert op_id == "op-1"
        assert len(effects) == 2

    def test_clear(self) -> None:
        tracker = EffectTracker("op-1")
        tracker.declare_fs_read("/a")
        assert len(tracker.effects) == 1
        tracker.clear()
        assert len(tracker.effects) == 0

    def test_custom_principal(self) -> None:
        tracker = EffectTracker("op-1", principal="custom")
        eff = tracker.declare(EffectType.DB_QUERY, "SELECT 1")
        assert eff.principal == "custom"


class TestTraceContext:
    def test_default_construction(self) -> None:
        ctx = TraceContext()
        assert ctx.trace_id
        assert ctx.span_id
        assert ctx.parent_span_id == ""
        assert ctx.baggage == {}
        assert ctx.sampled is True

    def test_child_inherits_trace(self) -> None:
        parent = TraceContext(trace_id="abc", span_id="parent-span")
        child = parent.child()
        assert child.trace_id == "abc"
        assert child.parent_span_id == "parent-span"
        assert child.span_id != "parent-span"

    def test_with_baggage(self) -> None:
        ctx = TraceContext()
        updated = ctx.with_baggage("user_id", "42")
        assert updated.baggage["user_id"] == "42"
        assert "user_id" not in ctx.baggage  # original unchanged

    def test_to_dict(self) -> None:
        ctx = TraceContext(trace_id="t1", span_id="s1", baggage={"k": "v"})
        d = ctx.to_dict()
        assert d["trace_id"] == "t1"
        assert d["span_id"] == "s1"
        assert d["baggage"] == {"k": "v"}
        assert d["sampled"] is True


class TestStreamEvents:
    def test_stream_chunk(self) -> None:
        chunk = StreamChunk(data="hello", sequence=1)
        assert chunk.data == "hello"
        assert chunk.sequence == 1
        assert chunk.is_final is False

    def test_stream_chunk_final(self) -> None:
        chunk = StreamChunk(data="done", sequence=5, is_final=True)
        assert chunk.is_final is True

    def test_stream_done(self) -> None:
        done = StreamDone(final_value={"result": "ok"})
        assert done.final_value == {"result": "ok"}

    def test_stream_error(self) -> None:
        err = KernelError(message="oops", code="ERR")
        se = StreamError(error=err)
        assert se.error.message == "oops"


class TestHealthTypes:
    def test_subsystem_health_to_dict(self) -> None:
        health = SubsystemHealth(
            subsystem="kernelone.fs",
            status=SubsystemStatus.HEALTHY,
            latency_ms=42,
        )
        d = health.to_dict()
        assert d["subsystem"] == "kernelone.fs"
        assert d["status"] == "healthy"
        assert d["latency_ms"] == 42

    def test_runtime_health_report_to_dict(self) -> None:
        report = RuntimeHealthReport(
            healthy=True,
            subsystems=[
                SubsystemHealth(subsystem="fs", status=SubsystemStatus.HEALTHY),
            ],
        )
        d = report.to_dict()
        assert d["healthy"] is True
        assert d["version"] == "2.0"
        assert len(d["subsystems"]) == 1

    def test_subsystem_status_values(self) -> None:
        assert SubsystemStatus.HEALTHY == "healthy"
        assert SubsystemStatus.UNHEALTHY == "unhealthy"
        assert SubsystemStatus.DEGRADED == "degraded"
        assert SubsystemStatus.INITIALIZING == "initializing"
        assert SubsystemStatus.STOPPED == "stopped"
        assert SubsystemStatus.UNKNOWN == "unknown"


class TestLockTypes:
    def test_lock_options_defaults(self) -> None:
        opts = LockOptions()
        assert opts.timeout_seconds == 30.0
        assert opts.ttl_seconds == 60.0
        assert opts.retry_interval_seconds == 0.1
        assert opts.non_blocking is False

    def test_lock_acquire_result(self) -> None:
        result = LockAcquireResult(acquired=True, lock_id="lock-1")
        assert result.acquired is True
        assert result.lock_id == "lock-1"

    def test_lock_release_result(self) -> None:
        result = LockReleaseResult(released=True, lock_id="lock-1", force_released=True)
        assert result.released is True
        assert result.force_released is True


class TestSchedulerTypes:
    def test_schedule_kind_values(self) -> None:
        assert ScheduleKind.ONCE == "once"
        assert ScheduleKind.PERIODIC == "periodic"
        assert ScheduleKind.CRON == "cron"
        assert ScheduleKind.DELAYED == "delayed"

    def test_schedule_spec_defaults(self) -> None:
        spec = ScheduleSpec()
        assert spec.kind == ScheduleKind.ONCE
        assert spec.interval_seconds == 0.0
        assert spec.max_runs == 0
        assert spec.run_id

    def test_scheduled_task_defaults(self) -> None:
        task = ScheduledTask()
        assert task.task_id
        assert task.run_id
        assert task.handler == ""
        assert task.payload == {}
        assert task.metadata == {}

    def test_schedule_result_success(self) -> None:
        result = ScheduleResult(scheduled=True, task_id="task-1")
        assert result.scheduled is True
        assert result.task_id == "task-1"
        assert result.error is None

    def test_schedule_result_failure(self) -> None:
        result = ScheduleResult(scheduled=False, error="Queue full")
        assert result.scheduled is False
        assert result.error == "Queue full"
