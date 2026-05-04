"""Quick validation tests for truth_log_service.py fixes."""

import asyncio
from datetime import datetime, timezone

import pytest

from polaris.kernelone.context.truth_log_service import TruthLogIndex, TruthLogService


class TestTruthLogServiceLocks:
    """Verify threading.Lock protection for mutable state."""

    def test_append_and_query_sync(self):
        service = TruthLogService(enable_semantic_index=False)
        service.append({"role": "user", "content": "hello"})
        service.append({"role": "assistant", "content": "world"})

        entries = service.replay()
        assert len(entries) == 2
        assert entries[0]["content"] == "hello"

    def test_query_by_role_sync(self):
        service = TruthLogService(enable_semantic_index=False)
        service.append({"role": "director", "content": "task1"})
        service.append({"role": "pm", "content": "plan1"})
        service.append({"role": "director", "content": "task2"})

        results = service.query_by_role("director")
        assert len(results) == 2

    def test_query_by_event_type_sync(self):
        service = TruthLogService(enable_semantic_index=False)
        service.append({"type": "decision", "content": "d1"})
        service.append({"type": "tool_call", "content": "t1"})

        results = service.query_by_event_type("decision")
        assert len(results) == 1

    def test_query_by_time_range_sync(self):
        service = TruthLogService(enable_semantic_index=False)
        now = datetime.now(timezone.utc)
        service.append({"timestamp": now, "content": "now"})

        results = service.query_by_time_range(
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(results) == 1

    def test_get_recent_sync(self):
        service = TruthLogService(enable_semantic_index=False)
        for i in range(5):
            service.append({"content": f"msg{i}"})

        recent = service.get_recent(3)
        assert len(recent) == 3

    def test_replace_clears_index(self):
        service = TruthLogService(enable_semantic_index=True)
        service.append({"role": "user", "content": "old"})

        service.replace([{"role": "system", "content": "new"}])

        entries = service.replay()
        assert len(entries) == 1
        assert entries[0]["content"] == "new"

    def test_get_entries_returns_copy(self):
        service = TruthLogService(enable_semantic_index=False)
        service.append({"content": "orig"})

        entries = service.get_entries()
        assert len(entries) == 1
        # Should be a deep copy - mutating returned entry shouldn't affect internal state
        entries[0]["content"] = "mutated"  # type: ignore[index]
        replayed = service.replay()
        assert replayed[0]["content"] == "orig"


class TestTruthLogIndexLocks:
    """Verify threading.Lock protection in TruthLogIndex."""

    def test_query_by_role_with_lock(self):
        index = TruthLogIndex(enable_vector_search=False)
        asyncio.run(index.add_entry({"role": "director", "content": "hello"}))
        results = index.query_by_role("director")
        assert len(results) == 1

    def test_query_by_event_type_with_lock(self):
        index = TruthLogIndex(enable_vector_search=False)
        asyncio.run(index.add_entry({"type": "decision", "content": "hello"}))
        results = index.query_by_event_type("decision")
        assert len(results) == 1

    def test_query_by_time_range_with_lock(self):
        index = TruthLogIndex(enable_vector_search=False)
        now = datetime.now(timezone.utc)
        asyncio.run(index.add_entry({"timestamp": now.isoformat(), "content": "hello"}))
        results = index.query_by_time_range(
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        assert len(results) == 1

    def test_get_recent_with_lock(self):
        index = TruthLogIndex(enable_vector_search=False)
        asyncio.run(index.add_entry({"content": "first"}))
        asyncio.run(index.add_entry({"content": "second"}))
        results = index.get_recent(1)
        assert len(results) == 1

    def test_clear_with_lock(self):
        index = TruthLogIndex(enable_vector_search=False)
        asyncio.run(index.add_entry({"content": "hello"}))
        index.clear()
        results = index.query_by_role("any")
        assert len(results) == 0


class TestAppendAsync:
    """Verify async append works correctly."""

    @pytest.mark.asyncio
    async def test_append_async(self):
        service = TruthLogService(enable_semantic_index=False)
        await service.append_async({"role": "user", "content": "async"})
        entries = service.replay()
        assert len(entries) == 1
        assert entries[0]["content"] == "async"
