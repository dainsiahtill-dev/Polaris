"""Tests for KernelAuditRuntime."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.audit.contracts import (
    GENESIS_HASH,
    KernelAuditEvent,
    KernelAuditEventType,
    KernelChainVerificationResult,
)
from polaris.kernelone.audit.runtime import (
    AuditIndex,
    KernelAuditRuntime,
)


class MockAuditStore:
    """Mock audit store for testing."""

    def __init__(self, runtime_root: Path) -> None:
        self._runtime_root = runtime_root
        self._events: list[KernelAuditEvent] = []

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
        self._events.append(event)
        return event

    def query(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]:
        results = list(self._events)
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if task_id:
            results = [e for e in results if e.task.get("task_id") == task_id]
        return results[offset : offset + limit]

    def export_json(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]:
        return {"events": [e.to_dict() for e in self._events], "count": len(self._events)}

    def export_csv(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str:
        return "event_id,timestamp,event_type\n" + "\n".join(
            f"{e.event_id},{e.timestamp.isoformat()},{e.event_type.value}" for e in self._events
        )

    def verify_chain(self) -> KernelChainVerificationResult:
        if not self._events:
            return KernelChainVerificationResult(
                is_valid=True,
                first_hash=GENESIS_HASH,
                last_hash=GENESIS_HASH,
                total_events=0,
                gap_count=0,
                verified_at=datetime.now(timezone.utc),
            )
        return KernelChainVerificationResult(
            is_valid=True,
            first_hash=self._hash_event(self._events[0]),
            last_hash=self._hash_event(self._events[-1]),
            total_events=len(self._events),
            gap_count=0,
            verified_at=datetime.now(timezone.utc),
        )

    def get_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "total_events": len(self._events),
            "event_types": {},
            "time_range": {"start": None, "end": None},
        }

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        return {"evicted": 0, "dry_run": dry_run}

    @staticmethod
    def _hash_event(event: KernelAuditEvent) -> str:
        payload = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def temp_runtime_root(tmp_path: Path) -> Path:
    """Create a temporary runtime root."""
    return tmp_path / "runtime"


@pytest.fixture
def mock_store(temp_runtime_root: Path) -> MockAuditStore:
    """Create a mock audit store."""
    return MockAuditStore(temp_runtime_root)


@pytest.fixture
def runtime(temp_runtime_root: Path, mock_store: MockAuditStore) -> KernelAuditRuntime:
    """Create a KernelAuditRuntime instance with mock store."""
    # Clear any existing singletons
    KernelAuditRuntime._instances.clear()

    # Create runtime with mock store
    rt = object.__new__(KernelAuditRuntime)
    rt._runtime_root = temp_runtime_root.resolve()
    rt._store = mock_store
    rt._index = AuditIndex(max_entries=50_000, window_hours=24)
    rt._hmac_key = b"test-secret-key-for-testing-purposes-only"
    rt._initialized = True
    return rt


class TestKernelAuditRuntimeCreation:
    """Test suite for KernelAuditRuntime creation."""

    def test_singleton_instances_tracked(self, temp_runtime_root: Path) -> None:
        """Test that singleton instances are tracked in _instances dict."""
        KernelAuditRuntime._instances.clear()

        # Add instances directly
        key = str(temp_runtime_root.resolve())
        store = MockAuditStore(temp_runtime_root)
        rt = object.__new__(KernelAuditRuntime)
        rt._runtime_root = temp_runtime_root.resolve()
        rt._store = store
        rt._index = AuditIndex()
        rt._hmac_key = b"key"
        rt._initialized = True
        KernelAuditRuntime._instances[key] = rt

        # Verify instance is in the dict
        assert key in KernelAuditRuntime._instances
        assert KernelAuditRuntime._instances[key] is rt

    def test_runtime_root_property(self, runtime: KernelAuditRuntime, temp_runtime_root: Path) -> None:
        """Test runtime_root property returns correct path."""
        assert runtime.runtime_root == temp_runtime_root.resolve()

    def test_raw_store_property_with_wrapper(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test raw_store property when store has raw_store attribute."""
        # The mock store doesn't have raw_store, so raw_store returns None
        # This is expected behavior when the store doesn't wrap another store
        assert runtime.raw_store is None


class TestKernelAuditRuntimeEmitEvent:
    """Test suite for emit_event method."""

    def test_emit_minimal_event(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test emitting minimal event."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-123",
        )
        assert result.success is True
        assert result.event_id is not None
        assert len(mock_store._events) == 1

    def test_emit_full_event(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test emitting event with all fields."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TOOL_EXECUTION,
            role="director",
            workspace="/workspace/project",
            task_id="task-abc",
            run_id="run-xyz",
            trace_id="trace-123",
            resource={"type": "file", "path": "/tmp/test.txt"},
            action={"name": "read_file", "result": "success"},
            data={"bytes": 1024},
            context={"custom": "value"},
        )
        assert result.success is True
        assert result.event_id is not None
        assert len(mock_store._events) == 1

        event = mock_store._events[0]
        assert event.event_type == KernelAuditEventType.TOOL_EXECUTION
        assert event.source["role"] == "director"
        assert event.task["task_id"] == "task-abc"
        assert event.task["run_id"] == "run-xyz"
        assert event.context.get("trace_id") == "trace-123"

    def test_emit_event_derives_task_id(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that task_id is derived when not provided."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="pm",
            workspace="/tmp/test",
            run_id="run-derive",
        )
        assert result.success is True
        assert "task_id_missing:derived" in result.warnings

        event = mock_store._events[0]
        assert event.task["task_id"].startswith("task-run-derive")

    def test_emit_event_derives_trace_id(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that trace_id is derived when not provided."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="pm",
            workspace="/tmp/test",
            run_id="run-trace",
        )
        assert result.success is True
        assert "trace_id_missing:derived" in result.warnings

        event = mock_store._events[0]
        assert event.context.get("trace_id") is not None
        assert len(event.context.get("trace_id", "")) > 0

    def test_emit_event_signs_chain(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that event is signed with HMAC."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role="director",
            workspace="/tmp/test",
            run_id="run-sign",
        )
        assert result.success is True

        event = mock_store._events[0]
        assert event.signature != ""
        assert len(event.signature) == 64  # SHA-256 hex

    def test_emit_event_sets_prev_hash(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that prev_hash is set correctly."""
        # First event should have GENESIS_HASH
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-chain",
        )
        assert mock_store._events[0].prev_hash == GENESIS_HASH

        # Second event should have hash of first event
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,
            role="director",
            workspace="/tmp/test",
            run_id="run-chain",
        )
        first_hash = KernelAuditRuntime._hash_event(mock_store._events[0])
        assert mock_store._events[1].prev_hash == first_hash

    def test_emit_event_normalizes_event_type_string(
        self, runtime: KernelAuditRuntime, mock_store: MockAuditStore
    ) -> None:
        """Test that string event type is normalized to enum."""
        result = runtime.emit_event(
            event_type="llm_call",  # string, not enum
            role="director",
            workspace="/tmp/test",
            run_id="run-type",
        )
        assert result.success is True
        assert mock_store._events[0].event_type == KernelAuditEventType.LLM_CALL

    def test_emit_event_strips_whitespace(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that whitespace is stripped from inputs."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="  director  ",
            workspace="  /tmp/test  ",
            run_id="  run-123  ",
        )
        assert result.success is True
        event = mock_store._events[0]
        assert event.source["role"] == "director"

    def test_emit_event_indexes_event(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that emitted event is added to index."""
        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-index",
            task_id="task-index",
        )
        assert result.success is True
        assert len(runtime._index) == 1

        indexed = runtime._index.query_by_task("task-index")
        assert len(indexed) == 1


class TestKernelAuditRuntimeEmitLlmEvent:
    """Test suite for emit_llm_event method."""

    def test_emit_llm_event_basic(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test emitting basic LLM event."""
        result = runtime.emit_llm_event(
            role="director",
            workspace="/tmp/test",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            duration_ms=1500.0,
            run_id="run-llm",
        )
        assert result.success is True
        assert mock_store._events[0].event_type == KernelAuditEventType.LLM_CALL

    def test_emit_llm_event_with_error(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test emitting LLM event with error."""
        result = runtime.emit_llm_event(
            role="director",
            workspace="/tmp/test",
            model="gpt-4",
            prompt_tokens=100,
            success=False,
            error="Rate limit exceeded",
            run_id="run-llm-err",
        )
        assert result.success is True
        event = mock_store._events[0]
        assert event.data.get("error") == "Rate limit exceeded"
        assert event.action.get("result") == "failure"

    def test_emit_llm_event_calculates_total_tokens(
        self, runtime: KernelAuditRuntime, mock_store: MockAuditStore
    ) -> None:
        """Test that total_tokens is calculated correctly."""
        runtime.emit_llm_event(
            role="director",
            workspace="/tmp/test",
            model="claude-3",
            prompt_tokens=500,
            completion_tokens=300,
            run_id="run-tokens",
        )
        event = mock_store._events[0]
        assert event.data.get("total_tokens") == 800


class TestKernelAuditRuntimeEmitDialogue:
    """Test suite for emit_dialogue method."""

    def test_emit_dialogue_basic(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test emitting basic dialogue event."""
        result = runtime.emit_dialogue(
            role="pm",
            workspace="/tmp/test",
            dialogue_type="task_request",
            message_summary="Create a new feature",
            run_id="run-dialog",
        )
        assert result.success is True
        event = mock_store._events[0]
        assert event.event_type == KernelAuditEventType.DIALOGUE
        assert event.action.get("name") == "task_request"
        assert event.data.get("message_summary") == "Create a new feature"

    def test_emit_dialogue_truncates_long_summary(
        self, runtime: KernelAuditRuntime, mock_store: MockAuditStore
    ) -> None:
        """Test that long message summary is truncated to 500 chars."""
        long_summary = "x" * 1000
        runtime.emit_dialogue(
            role="pm",
            workspace="/tmp/test",
            dialogue_type="task_request",
            message_summary=long_summary,
            run_id="run-long",
        )
        event = mock_store._events[0]
        assert len(event.data.get("message_summary", "")) == 500


class TestKernelAuditRuntimeQueryEvents:
    """Test suite for query_events method."""

    def test_query_events_empty(self, runtime: KernelAuditRuntime) -> None:
        """Test querying when no events exist."""
        results = runtime.query_events()
        assert results == []

    def test_query_events_returns_all(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test querying returns all events."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-q1",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,
            role="director",
            workspace="/tmp/test",
            run_id="run-q2",
        )
        results = runtime.query_events()
        assert len(results) == 2

    def test_query_events_by_type(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test querying by event type."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-type1",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,
            role="director",
            workspace="/tmp/test",
            run_id="run-type2",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role="director",
            workspace="/tmp/test",
            run_id="run-type3",
        )
        results = runtime.query_events(event_type=KernelAuditEventType.TASK_START)
        assert len(results) == 1
        assert results[0].event_type == KernelAuditEventType.TASK_START

    def test_query_events_with_limit(self, runtime: KernelAuditRuntime) -> None:
        """Test querying with limit."""
        for i in range(10):
            runtime.emit_event(
                event_type=KernelAuditEventType.TASK_START,
                role="director",
                workspace="/tmp/test",
                run_id=f"run-limit-{i}",
            )
        results = runtime.query_events(limit=5)
        assert len(results) == 5


class TestKernelAuditRuntimeQueryByRunId:
    """Test suite for query_by_run_id method."""

    def test_query_by_run_id(self, runtime: KernelAuditRuntime) -> None:
        """Test querying events by run_id."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-specific",
            task_id="task-1",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role="director",
            workspace="/tmp/test",
            run_id="run-other",
        )
        results = runtime.query_by_run_id("run-specific")
        assert len(results) == 1
        assert results[0].task["run_id"] == "run-specific"


class TestKernelAuditRuntimeQueryByTaskId:
    """Test suite for query_by_task_id method."""

    def test_query_by_task_id(self, runtime: KernelAuditRuntime) -> None:
        """Test querying events by task_id using index."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-task",
            task_id="task-find",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,
            role="director",
            workspace="/tmp/test",
            run_id="run-task",
            task_id="task-find",
        )
        results = runtime.query_by_task_id("task-find")
        assert len(results) == 2

    def test_query_by_task_id_empty(self, runtime: KernelAuditRuntime) -> None:
        """Test querying non-existent task_id."""
        results = runtime.query_by_task_id("nonexistent")
        assert results == []


class TestKernelAuditRuntimeQueryByTraceId:
    """Test suite for query_by_trace_id method."""

    def test_query_by_trace_id(self, runtime: KernelAuditRuntime) -> None:
        """Test querying events by trace_id using index."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-trace",
            trace_id="trace-find",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role="director",
            workspace="/tmp/test",
            run_id="run-trace",
            trace_id="trace-find",
        )
        results = runtime.query_by_trace_id("trace-find")
        assert len(results) == 2

    def test_query_by_trace_id_empty(self, runtime: KernelAuditRuntime) -> None:
        """Test querying non-existent trace_id."""
        results = runtime.query_by_trace_id("nonexistent")
        assert results == []


class TestKernelAuditRuntimeExport:
    """Test suite for export methods."""

    def test_export_json(self, runtime: KernelAuditRuntime) -> None:
        """Test JSON export."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-export",
        )
        result = runtime.export_json()
        assert "events" in result
        assert result["count"] == 1

    def test_export_csv(self, runtime: KernelAuditRuntime) -> None:
        """Test CSV export."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-csv",
        )
        result = runtime.export_csv()
        assert "event_id" in result
        assert "timestamp" in result
        assert "event_type" in result


class TestKernelAuditRuntimeVerifyChain:
    """Test suite for verify_chain method."""

    def test_verify_chain_empty(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test chain verification with no events."""
        result = runtime.verify_chain()
        assert result.is_valid is True
        assert result.total_events == 0

    def test_verify_chain_with_events(self, runtime: KernelAuditRuntime) -> None:
        """Test chain verification with events."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-verify",
        )
        result = runtime.verify_chain()
        assert result.is_valid is True
        assert result.total_events == 1


class TestKernelAuditRuntimeGetStats:
    """Test suite for get_stats method."""

    def test_get_stats(self, runtime: KernelAuditRuntime) -> None:
        """Test getting stats."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-stats",
        )
        result = runtime.get_stats()
        assert "total_events" in result


class TestKernelAuditRuntimeHealthCheck:
    """Test suite for health_check method."""

    def test_health_check_healthy(self, runtime: KernelAuditRuntime) -> None:
        """Test health check when system is healthy."""
        result = runtime.health_check()
        assert result["status"] in ("healthy", "degraded")
        assert "runtime_root" in result
        assert "store_path" in result
        assert "chain_valid" in result
        assert "index_size" in result

    def test_health_check_with_events(self, runtime: KernelAuditRuntime) -> None:
        """Test health check after emitting events."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-health",
        )
        result = runtime.health_check()
        assert result["recent_event_count"] >= 1


class TestKernelAuditRuntimeCleanup:
    """Test suite for cleanup_old_logs method."""

    def test_cleanup_dry_run(self, runtime: KernelAuditRuntime) -> None:
        """Test cleanup in dry-run mode."""
        result = runtime.cleanup_old_logs(dry_run=True)
        assert "evicted" in result
        assert result["dry_run"] is True


class TestKernelAuditRuntimeSignatureVerification:
    """Test suite for HMAC signature verification."""

    def test_signature_is_hmac_sha256(self, runtime: KernelAuditRuntime, mock_store: MockAuditStore) -> None:
        """Test that signature is HMAC-SHA256."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-sig",
        )
        event = mock_store._events[0]

        # Verify signature format (64 hex chars = SHA-256)
        assert len(event.signature) == 64
        assert all(c in "0123456789abcdef" for c in event.signature)

    def test_signature_changes_with_different_event(
        self, runtime: KernelAuditRuntime, mock_store: MockAuditStore
    ) -> None:
        """Test that different events produce different signatures."""
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="director",
            workspace="/tmp/test",
            run_id="run-sig1",
        )
        sig1 = mock_store._events[0].signature

        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,  # Different type
            role="director",
            workspace="/tmp/test",
            run_id="run-sig2",
        )
        sig2 = mock_store._events[1].signature

        assert sig1 != sig2


class TestKernelAuditRuntimeShutdown:
    """Test suite for shutdown_all method."""

    def test_shutdown_all_clears_instances(self, temp_runtime_root: Path) -> None:
        """Test that shutdown_all clears all singleton instances."""
        KernelAuditRuntime._instances.clear()

        store = MockAuditStore(temp_runtime_root)
        rt = object.__new__(KernelAuditRuntime)
        rt._runtime_root = temp_runtime_root.resolve()
        rt._store = store
        rt._index = AuditIndex()
        rt._hmac_key = b"key"
        rt._initialized = True

        key = str(temp_runtime_root.resolve())
        KernelAuditRuntime._instances[key] = rt

        KernelAuditRuntime.shutdown_all()

        assert len(KernelAuditRuntime._instances) == 0
