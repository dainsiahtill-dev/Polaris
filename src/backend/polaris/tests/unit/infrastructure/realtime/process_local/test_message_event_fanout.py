"""Tests for polaris.infrastructure.realtime.process_local.message_event_fanout."""

from __future__ import annotations

import pytest
from polaris.infrastructure.realtime.process_local.message_event_fanout import (
    FILE_EDIT_BUFFER_SIZE,
    RUNTIME_EVENT_FANOUT,
    TASK_TRACE_BUFFER_SIZE,
    ConnectionSink,
    RuntimeEventFanout,
)


class TestConnectionSink:
    """Test ConnectionSink buffer management."""

    def test_init(self) -> None:
        sink = ConnectionSink(
            connection_id="test-conn",
            workspace="/workspace",
            cache_root="/cache",
        )
        assert sink.connection_id == "test-conn"
        assert sink.workspace == "/workspace"
        assert sink.cache_root == "/cache"
        assert sink.file_edit_dropped == 0
        assert sink.task_trace_dropped == 0
        assert sink.sequential_dropped == 0

    def test_add_file_edit(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        result = sink.add_file_edit({"file_path": "/test.py", "operation": "create"})
        assert result is True
        assert len(sink.file_edit_events) == 1

    def test_add_file_edit_buffer_full(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        # Fill buffer
        for i in range(FILE_EDIT_BUFFER_SIZE):
            sink.add_file_edit({"id": f"event-{i}"})
        # Next add should succeed but drop oldest
        result = sink.add_file_edit({"id": "new-event"})
        assert result is True
        assert sink.file_edit_dropped >= 1
        assert len(sink.file_edit_events) == FILE_EDIT_BUFFER_SIZE

    def test_add_task_trace(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        result = sink.add_task_trace({"task_id": "task-1"})
        assert result is True
        assert len(sink.task_trace_events) == 1

    def test_add_task_trace_buffer_full(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        for i in range(TASK_TRACE_BUFFER_SIZE):
            sink.add_task_trace({"id": f"trace-{i}"})
        result = sink.add_task_trace({"id": "new-trace"})
        assert result is True
        assert sink.task_trace_dropped >= 1

    def test_drain_file_edits(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        sink.add_file_edit({"id": "event-1"})
        sink.add_file_edit({"id": "event-2"})
        events, dropped = sink.drain_file_edits()
        assert len(events) == 2
        assert dropped == 0
        assert len(sink.file_edit_events) == 0

    def test_drain_task_traces(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        sink.add_task_trace({"id": "trace-1"})
        events, dropped = sink.drain_task_traces()
        assert len(events) == 1
        assert dropped == 0
        assert len(sink.task_trace_events) == 0

    def test_add_sequential(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        result = sink.add_sequential({"type": "seq.start"})
        assert result is True

    def test_drain_sequential(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        sink.add_sequential({"type": "seq.step"})
        events, dropped = sink.drain_sequential()
        assert len(events) == 1
        assert dropped == 0

    def test_get_stats(self) -> None:
        sink = ConnectionSink(connection_id="test", workspace="/ws", cache_root="/cache")
        sink.add_file_edit({"id": "event-1"})
        stats = sink.get_stats()
        assert stats["file_edit_pending"] == 1
        assert stats["task_trace_pending"] == 0
        assert stats["sequential_pending"] == 0
        assert "last_activity" in stats


class TestRuntimeEventFanout:
    """Test RuntimeEventFanout functionality."""

    def test_init(self) -> None:
        fanout = RuntimeEventFanout()
        assert fanout._closed is False
        assert fanout._subscribed is False
        assert fanout._sinks == {}

    def test_build_file_edit_event(self) -> None:
        fanout = RuntimeEventFanout()
        payload = {
            "file_path": "/test.py",
            "operation": "create",
            "added_lines": 10,
            "deleted_lines": 2,
            "modified_lines": 5,
            "content_size": 100,
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        event = fanout._build_file_edit_event(payload)
        assert event["file_path"] == "/test.py"
        assert event["operation"] == "create"
        assert event["added_lines"] == 10
        assert event["deleted_lines"] == 2
        assert event["modified_lines"] == 5

    def test_build_file_edit_event_invalid_operation(self) -> None:
        fanout = RuntimeEventFanout()
        payload = {"file_path": "/test.py", "operation": "invalid_op"}
        event = fanout._build_file_edit_event(payload)
        assert event["operation"] == "modify"

    def test_build_file_edit_event_missing_fields(self) -> None:
        fanout = RuntimeEventFanout()
        payload = {"file_path": "/test.py"}
        event = fanout._build_file_edit_event(payload)
        assert event["added_lines"] == 0
        assert event["deleted_lines"] == 0
        assert event["modified_lines"] == 0

    def test_get_global_stats(self) -> None:
        fanout = RuntimeEventFanout()
        stats = fanout.get_global_stats()
        assert "file_written_events" in stats
        assert "task_trace_events" in stats
        assert "connections_registered" in stats

    def test_list_connections_empty(self) -> None:
        fanout = RuntimeEventFanout()
        assert fanout.list_connections() == []

    def test_get_sink_stats_not_found(self) -> None:
        fanout = RuntimeEventFanout()
        assert fanout.get_sink_stats("nonexistent") is None


class TestRuntimeEventFanoutAsync:
    """Test RuntimeEventFanout async operations."""

    @pytest.mark.asyncio
    async def test_register_connection(self) -> None:
        fanout = RuntimeEventFanout()
        sink = await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )
        assert sink.connection_id == "conn-1"
        assert fanout.list_connections() == ["conn-1"]

    @pytest.mark.asyncio
    async def test_unregister_connection(self) -> None:
        fanout = RuntimeEventFanout()
        await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )
        result = await fanout.unregister_connection("conn-1")
        assert result is True
        assert fanout.list_connections() == []

    @pytest.mark.asyncio
    async def test_unregister_connection_not_found(self) -> None:
        fanout = RuntimeEventFanout()
        result = await fanout.unregister_connection("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_drain_events_empty(self) -> None:
        fanout = RuntimeEventFanout()
        result = await fanout.drain_events("nonexistent")
        assert result == ([], [], [], 0)

    @pytest.mark.asyncio
    async def test_drain_events_with_data(self) -> None:
        fanout = RuntimeEventFanout()
        sink = await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )
        sink.add_file_edit({"id": "event-1"})
        result = await fanout.drain_events("conn-1")
        file_events, _, _, dropped = result
        assert len(file_events) == 1
        assert dropped == 0


class TestGlobalFanoutSingleton:
    """Test RUNTIME_EVENT_FANOUT singleton."""

    def test_singleton_exists(self) -> None:
        assert RUNTIME_EVENT_FANOUT is not None
        assert isinstance(RUNTIME_EVENT_FANOUT, RuntimeEventFanout)
