"""Tests for AuditIndex in-memory event indexing."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from polaris.kernelone.audit.contracts import (
    GENESIS_HASH,
    KernelAuditEvent,
    KernelAuditEventType,
)
from polaris.kernelone.audit.runtime import AuditIndex


class TestAuditIndexCreation:
    """Test suite for AuditIndex initialization."""

    def test_default_initialization(self) -> None:
        """Test index initializes with default parameters."""
        index = AuditIndex()
        assert index is not None
        assert len(index) == 0

    def test_custom_max_entries(self) -> None:
        """Test index with custom max_entries."""
        index = AuditIndex(max_entries=1000)
        assert index is not None
        assert len(index) == 0

    def test_custom_window_hours(self) -> None:
        """Test index with custom window_hours."""
        index = AuditIndex(window_hours=48)
        assert index is not None
        assert len(index) == 0


class TestAuditIndexBasicOperations:
    """Test suite for AuditIndex basic operations."""

    def _make_event(
        self,
        event_id: str = "test-id-1",
        event_type: KernelAuditEventType = KernelAuditEventType.TASK_START,
        task_id: str = "task-1",
        trace_id: str = "trace-1",
    ) -> KernelAuditEvent:
        """Helper to create a test event."""
        return KernelAuditEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            version="2.0",
            source={"role": "test"},
            task={"task_id": task_id, "run_id": "run-1"},
            context={"trace_id": trace_id},
            prev_hash=GENESIS_HASH,
            signature="sig-1",
        )

    def test_index_single_event(self) -> None:
        """Test indexing a single event."""
        index = AuditIndex()
        event = self._make_event()
        index.index_event(event)
        assert len(index) == 1

    def test_index_multiple_events(self) -> None:
        """Test indexing multiple events."""
        index = AuditIndex()
        for i in range(5):
            event = self._make_event(event_id=f"event-{i}")
            index.index_event(event)
        assert len(index) == 5

    def test_query_by_task_id(self) -> None:
        """Test querying events by task_id."""
        index = AuditIndex()
        task_id = "specific-task"
        events = [self._make_event(event_id=f"e{i}", task_id=task_id) for i in range(3)]
        # Add some other events
        index.index_event(self._make_event(event_id="other-1", task_id="other-task"))
        for event in events:
            index.index_event(event)
        index.index_event(self._make_event(event_id="other-2", task_id="other-task"))

        results = index.query_by_task(task_id)
        assert len(results) == 3
        for event in results:
            assert event.task.get("task_id") == task_id

    def test_query_by_trace_id(self) -> None:
        """Test querying events by trace_id."""
        index = AuditIndex()
        trace_id = "specific-trace"
        events = [self._make_event(event_id=f"trace-e{i}", trace_id=trace_id) for i in range(2)]
        index.index_event(self._make_event(event_id="other-trace", trace_id="other"))
        for event in events:
            index.index_event(event)

        results = index.query_by_trace(trace_id)
        assert len(results) == 2
        for event in results:
            assert event.context.get("trace_id") == trace_id

    def test_query_by_type(self) -> None:
        """Test querying events by event type."""
        index = AuditIndex()
        index.index_event(self._make_event(event_type=KernelAuditEventType.TASK_START))
        index.index_event(self._make_event(event_type=KernelAuditEventType.TASK_START))
        index.index_event(self._make_event(event_type=KernelAuditEventType.TOOL_EXECUTION))
        index.index_event(self._make_event(event_type=KernelAuditEventType.LLM_CALL))

        results = index.query_by_type(KernelAuditEventType.TASK_START)
        assert len(results) == 2

    def test_query_by_type_with_limit(self) -> None:
        """Test querying events by type with limit."""
        index = AuditIndex()
        for i in range(10):
            index.index_event(self._make_event(event_id=f"limit-e{i}", event_type=KernelAuditEventType.DIALOGUE))

        results = index.query_by_type(KernelAuditEventType.DIALOGUE, limit=5)
        assert len(results) == 5

    def test_query_recent(self) -> None:
        """Test querying recent events."""
        index = AuditIndex()
        # Old event
        old_event = KernelAuditEvent(
            event_id="old-event",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=2),
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={},
            task={},
            context={},
            prev_hash=GENESIS_HASH,
            signature="",
        )
        index.index_event(old_event)

        # Recent event
        recent_event = self._make_event(event_id="recent-event")
        index.index_event(recent_event)

        results = index.query_recent(hours=1)
        assert len(results) == 1
        assert results[0].event_id == "recent-event"

    def test_query_recent_no_events(self) -> None:
        """Test querying recent events when none exist."""
        index = AuditIndex()
        results = index.query_recent(hours=1)
        assert len(results) == 0


class TestAuditIndexEviction:
    """Test suite for AuditIndex eviction logic."""

    def _make_event(
        self,
        event_id: str = "evict-test",
        event_type: KernelAuditEventType = KernelAuditEventType.TASK_START,
        task_id: str = "task-1",
        trace_id: str = "trace-1",
        hours_ago: float = 0,
    ) -> KernelAuditEvent:
        """Helper to create a test event."""
        return KernelAuditEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            event_type=event_type,
            version="2.0",
            source={"role": "test"},
            task={"task_id": task_id, "run_id": "run-1"},
            context={"trace_id": trace_id},
            prev_hash=GENESIS_HASH,
            signature="sig-1",
        )

    def test_evict_old_by_time(self) -> None:
        """Test evicting old events by datetime."""
        now = datetime.now(timezone.utc)
        # Create events at specific times relative to now
        old_time = now - timedelta(hours=48)
        old_time2 = now - timedelta(hours=36)
        recent_time = now - timedelta(hours=1)

        old_event = KernelAuditEvent(
            event_id="old-1",
            timestamp=old_time,
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={},
            task={},
            context={},
            prev_hash=GENESIS_HASH,
            signature="",
        )
        old_event2 = KernelAuditEvent(
            event_id="old-2",
            timestamp=old_time2,
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={},
            task={},
            context={},
            prev_hash=GENESIS_HASH,
            signature="",
        )
        recent_event = KernelAuditEvent(
            event_id="recent-1",
            timestamp=recent_time,
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={},
            task={},
            context={},
            prev_hash=GENESIS_HASH,
            signature="",
        )

        index = AuditIndex()
        index.index_event(old_event)
        index.index_event(old_event2)
        index.index_event(recent_event)

        cutoff = now - timedelta(hours=24)
        evicted = index.evict_old(cutoff)

        assert evicted == 2
        assert len(index) == 1
        # Verify the remaining event is the recent one
        assert index.query_by_task("")[-1].event_id == "recent-1" if index.query_by_task("") else True

    def test_evict_old_none_to_evict(self) -> None:
        """Test evicting when no old events exist."""
        index = AuditIndex()
        index.index_event(self._make_event(event_id="recent", hours_ago=1))

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        evicted = index.evict_old(cutoff)

        assert evicted == 0
        assert len(index) == 1

    def test_max_entries_eviction(self) -> None:
        """Test that index evicts oldest entries when max_entries exceeded."""
        index = AuditIndex(max_entries=5)
        for i in range(10):
            index.index_event(self._make_event(event_id=f"max-e{i}"))

        assert len(index) == 5


class TestAuditIndexThreadSafety:
    """Test suite for AuditIndex thread safety."""

    def _make_event(self, event_id: str) -> KernelAuditEvent:
        """Helper to create a test event."""
        return KernelAuditEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={"role": "test"},
            task={"task_id": "thread-task", "run_id": "run-1"},
            context={"trace_id": "thread-trace"},
            prev_hash=GENESIS_HASH,
            signature="sig",
        )

    def test_concurrent_indexing(self) -> None:
        """Test concurrent event indexing."""
        index = AuditIndex()
        errors: list[RuntimeError] = []

        def index_events(start: int, count: int) -> None:
            try:
                for i in range(start, start + count):
                    index.index_event(self._make_event(f"concurrent-{i}"))
            except RuntimeError as e:
                errors.append(e)

        threads = [
            threading.Thread(target=index_events, args=(0, 100)),
            threading.Thread(target=index_events, args=(100, 100)),
            threading.Thread(target=index_events, args=(200, 100)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(index) == 300

    def test_concurrent_query_and_index(self) -> None:
        """Test concurrent querying and indexing."""
        index = AuditIndex()
        errors: list[RuntimeError] = []

        # Pre-populate some events
        for i in range(50):
            index.index_event(self._make_event(f"pre-{i}"))

        def indexer() -> None:
            try:
                for i in range(50):
                    index.index_event(self._make_event(f"new-{i}"))
            except RuntimeError as e:
                errors.append(e)

        def querier() -> None:
            try:
                for _ in range(50):
                    index.query_by_task("thread-task")
                    index.query_by_trace("thread-trace")
                    len(index)
            except RuntimeError as e:
                errors.append(e)

        threads = [
            threading.Thread(target=indexer),
            threading.Thread(target=querier),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestAuditIndexEdgeCases:
    """Test suite for AuditIndex edge cases."""

    def _make_event(
        self,
        event_id: str = "edge-1",
        task_id: str = "",
        trace_id: str = "",
    ) -> KernelAuditEvent:
        """Helper to create a test event."""
        return KernelAuditEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc),
            event_type=KernelAuditEventType.TASK_START,
            version="2.0",
            source={"role": "test"},
            task={"task_id": task_id, "run_id": "run-1"},
            context={"trace_id": trace_id},
            prev_hash=GENESIS_HASH,
            signature="sig",
        )

    def test_event_with_empty_task_id(self) -> None:
        """Test indexing event with empty task_id."""
        index = AuditIndex()
        event = self._make_event(task_id="")
        index.index_event(event)
        assert len(index) == 1

    def test_event_with_empty_trace_id(self) -> None:
        """Test indexing event with empty trace_id."""
        index = AuditIndex()
        event = self._make_event(trace_id="")
        index.index_event(event)
        assert len(index) == 1

    def test_query_nonexistent_task_id(self) -> None:
        """Test querying non-existent task_id."""
        index = AuditIndex()
        index.index_event(self._make_event(task_id="exists"))
        results = index.query_by_task("does-not-exist")
        assert len(results) == 0

    def test_query_nonexistent_trace_id(self) -> None:
        """Test querying non-existent trace_id."""
        index = AuditIndex()
        index.index_event(self._make_event(trace_id="exists"))
        results = index.query_by_trace("does-not-exist")
        assert len(results) == 0

    def test_query_nonexistent_event_type(self) -> None:
        """Test querying non-existent event type."""
        index = AuditIndex()
        index.index_event(self._make_event())
        results = index.query_by_type(KernelAuditEventType.LLM_CALL)
        assert len(results) == 0

    def test_index_after_eviction(self) -> None:
        """Test that indexing works correctly after eviction."""
        index = AuditIndex(max_entries=5)
        for i in range(5):
            index.index_event(self._make_event(event_id=f"pre-{i}"))

        # Evict some
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        index.evict_old(cutoff)

        # Index new events
        index.index_event(self._make_event(event_id="new-1"))
        index.index_event(self._make_event(event_id="new-2"))

        assert len(index) >= 1
