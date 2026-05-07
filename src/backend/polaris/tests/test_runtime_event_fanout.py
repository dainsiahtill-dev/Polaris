"""Tests for RuntimeEventFanout service.

Test matrix coverage:
1. Single MessageBus subscription invariant
2. Connection registration and cleanup
3. Bounded buffer backpressure
4. Drop counting and resync triggering
5. Event distribution to multiple connections
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.realtime.process_local.message_event_fanout import (
    FILE_EDIT_BUFFER_SIZE,
    TASK_TRACE_BUFFER_SIZE,
    ConnectionSink,
    RuntimeEventFanout,
)
from polaris.kernelone.events.message_bus import Message, MessageType


@pytest.fixture
def fanout():
    """Create a fresh fanout instance."""
    f = RuntimeEventFanout()
    yield f
    # Clean up using asyncio directly in pytest
    try:
        asyncio.get_running_loop()
        # If we're in an async context, close properly
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, f.close()).result()
    except RuntimeError:
        # If no running loop, just close (may not clean up async parts)
        pass


@pytest.fixture
def mock_bus():
    """Create a mock MessageBus."""
    bus = MagicMock()
    bus.subscribers = {}

    async def subscribe(msg_type, handler):
        bus.subscribers.setdefault(msg_type, []).append(handler)
        return True

    async def unsubscribe(msg_type, handler):
        if msg_type in bus.subscribers:
            bus.subscribers[msg_type] = [h for h in bus.subscribers[msg_type] if h != handler]
        return True

    bus.subscribe = subscribe
    bus.unsubscribe = unsubscribe
    return bus


@pytest.fixture
def mock_director_service(mock_bus):
    """Create a mock DirectorService."""
    service = MagicMock()
    service._bus = mock_bus
    return service


class TestConnectionSink:
    """Tests for ConnectionSink class."""

    def test_add_file_edit(self):
        """Test adding file edit events."""
        sink = ConnectionSink(
            connection_id="test-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        event = {"file_path": "/test.py", "operation": "modify"}
        assert sink.add_file_edit(event) is True

        events, dropped = sink.drain_file_edits()
        assert len(events) == 1
        assert events[0] == event
        assert dropped == 0

    def test_file_edit_buffer_bounds(self):
        """Test that file edit buffer respects max size."""
        sink = ConnectionSink(
            connection_id="test-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        # Add more events than buffer size
        for i in range(FILE_EDIT_BUFFER_SIZE + 10):
            sink.add_file_edit({"id": i})

        events, dropped = sink.drain_file_edits()
        assert len(events) == FILE_EDIT_BUFFER_SIZE
        assert dropped == 10

    def test_task_trace_buffer_bounds(self):
        """Test that task trace buffer respects max size."""
        sink = ConnectionSink(
            connection_id="test-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        # Add more events than buffer size
        for i in range(TASK_TRACE_BUFFER_SIZE + 5):
            sink.add_task_trace({"id": i})

        events, dropped = sink.drain_task_traces()
        assert len(events) == TASK_TRACE_BUFFER_SIZE
        assert dropped == 5

    def test_drain_resets_counters(self):
        """Test that drain resets drop counters."""
        sink = ConnectionSink(
            connection_id="test-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        # Overfill to create drops
        for i in range(FILE_EDIT_BUFFER_SIZE + 5):
            sink.add_file_edit({"id": i})

        _, dropped1 = sink.drain_file_edits()
        assert dropped1 == 5

        # Second drain should have no drops
        _, dropped2 = sink.drain_file_edits()
        assert dropped2 == 0

    def test_get_stats(self):
        """Test getting sink statistics."""
        sink = ConnectionSink(
            connection_id="test-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        sink.add_file_edit({"test": "event"})
        sink.add_task_trace({"test": "trace"})

        stats = sink.get_stats()
        assert stats["file_edit_pending"] == 1
        assert stats["task_trace_pending"] == 1
        assert stats["file_edit_dropped"] == 0
        assert stats["task_trace_dropped"] == 0
        assert "last_activity" in stats


class TestRuntimeEventFanout:
    """Tests for RuntimeEventFanout class."""

    @pytest.mark.asyncio
    async def test_register_connection(self, fanout):
        """Test connection registration."""
        sink = await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        assert sink is not None
        assert sink.connection_id == "conn-1"
        assert fanout.list_connections() == ["conn-1"]

    @pytest.mark.asyncio
    async def test_unregister_connection(self, fanout):
        """Test connection unregistration."""
        await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        result = await fanout.unregister_connection("conn-1")
        assert result is True
        assert fanout.list_connections() == []

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, fanout):
        """Test unregistering non-existent connection."""
        result = await fanout.unregister_connection("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_drain_events_empty(self, fanout):
        """Test draining when no events."""
        await fanout.register_connection(
            connection_id="conn-1",
            workspace="/workspace",
            cache_root="/cache",
        )

        file_events, trace_events, sequential_events, dropped = await fanout.drain_events("conn-1")
        assert file_events == []
        assert trace_events == []
        assert sequential_events == []
        assert dropped == 0

    @pytest.mark.asyncio
    async def test_drain_events_nonexistent(self, fanout):
        """Test draining non-existent connection."""
        file_events, trace_events, sequential_events, dropped = await fanout.drain_events("nonexistent")
        assert file_events == []
        assert trace_events == []
        assert sequential_events == []
        assert dropped == 0

    @pytest.mark.asyncio
    async def test_file_written_distribution(self, mock_director_service):
        """Test that FILE_WRITTEN events are distributed to connections."""
        fanout = RuntimeEventFanout()

        try:
            with patch.object(fanout, "_bus", mock_director_service._bus):
                fanout._file_handler = fanout._make_file_written_handler(asyncio.get_event_loop())

                # Register connections
                await fanout.register_connection("conn-1", "/ws1", "/cache1")
                await fanout.register_connection("conn-2", "/ws2", "/cache2")

                # Simulate FILE_WRITTEN message
                message = Message(
                    type=MessageType.FILE_WRITTEN,
                    sender="test",
                    payload={
                        "file_path": "/test.py",
                        "operation": "modify",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # Call handler directly
                fanout._file_handler(message)

                # Both connections should have the event
                file_events1, _, _, _ = await fanout.drain_events("conn-1")
                file_events2, _, _, _ = await fanout.drain_events("conn-2")

                assert len(file_events1) == 1
                assert len(file_events2) == 1
                assert file_events1[0]["file_path"] == "/test.py"
                assert file_events1[0]["event_schema"] == "runtime.event.file_edit.v1"
                assert file_events1[0]["source"] == "message_bus.file_written"
                assert file_events1[0]["schema_version"] == "runtime.v2"
        finally:
            await fanout.close()

    @pytest.mark.asyncio
    async def test_task_trace_distribution(self, mock_director_service):
        """Test that TASK_TRACE events are distributed to connections."""
        fanout = RuntimeEventFanout()

        try:
            with patch.object(fanout, "_bus", mock_director_service._bus):
                fanout._trace_handler = fanout._make_task_trace_handler(asyncio.get_event_loop())

                # Register connection
                await fanout.register_connection("conn-1", "/ws1", "/cache1")

                # Simulate TASK_TRACE message
                message = Message(
                    type=MessageType.TASK_TRACE,
                    sender="director",
                    payload={"task_id": "task-1", "status": "completed"},
                )

                # Call handler directly
                fanout._trace_handler(message)

                # Connection should have the event
                _, trace_events, _, _ = await fanout.drain_events("conn-1")

                assert len(trace_events) == 1
                assert trace_events[0]["event"]["task_id"] == "task-1"
        finally:
            await fanout.close()

    @pytest.mark.asyncio
    async def test_dropped_events_reported(self, mock_director_service):
        """Test that dropped events are reported for resync."""
        fanout = RuntimeEventFanout()

        try:
            with patch.object(fanout, "_bus", mock_director_service._bus):
                fanout._file_handler = fanout._make_file_written_handler(asyncio.get_event_loop())

                # Register connection
                await fanout.register_connection("conn-1", "/ws1", "/cache1")

                # Add more events than buffer size without draining
                for i in range(FILE_EDIT_BUFFER_SIZE + 5):
                    message = Message(
                        type=MessageType.FILE_WRITTEN,
                        sender="test",
                        payload={"file_path": f"/test{i}.py", "operation": "modify"},
                    )
                    fanout._file_handler(message)

                # Drain should report dropped count
                _, _, _, dropped = await fanout.drain_events("conn-1")
                assert dropped == 5
        finally:
            await fanout.close()

    @pytest.mark.asyncio
    async def test_close_clears_all_connections(self):
        """Test that close() clears all connections."""
        fanout = RuntimeEventFanout()

        await fanout.register_connection("conn-1", "/ws1", "/cache1")
        await fanout.register_connection("conn-2", "/ws2", "/cache2")

        assert len(fanout.list_connections()) == 2

        await fanout.close()

        assert len(fanout.list_connections()) == 0

    @pytest.mark.asyncio
    async def test_global_stats_tracking(self, mock_director_service):
        """Test that global stats are tracked."""
        fanout = RuntimeEventFanout()

        try:
            with patch.object(fanout, "_bus", mock_director_service._bus):
                fanout._file_handler = fanout._make_file_written_handler(asyncio.get_event_loop())
                fanout._trace_handler = fanout._make_task_trace_handler(asyncio.get_event_loop())

                await fanout.register_connection("conn-1", "/ws1", "/cache1")

                # File written event
                fanout._file_handler(
                    Message(
                        type=MessageType.FILE_WRITTEN,
                        sender="test",
                        payload={"file_path": "/test.py"},
                    )
                )

                # Task trace event
                fanout._trace_handler(
                    Message(
                        type=MessageType.TASK_TRACE,
                        sender="director",
                        payload={"task_id": "task-1"},
                    )
                )

                stats = fanout.get_global_stats()
                assert stats["file_written_events"] == 1
                assert stats["task_trace_events"] == 1
                assert stats["connections_registered"] == 1
        finally:
            await fanout.close()


class TestRuntimeEventFanoutSubscription:
    """Tests for MessageBus subscription behavior."""

    @pytest.mark.asyncio
    async def test_single_subscription_invariant(self, mock_director_service):
        """Test that only one subscription exists per message type."""
        fanout = RuntimeEventFanout()

        async def mock_resolve(interface):
            return mock_director_service

        try:
            with patch(
                "polaris.infrastructure.realtime.process_local.message_event_fanout.get_container"
            ) as mock_get_container:
                mock_container = MagicMock()
                mock_container.resolve_async = mock_resolve
                mock_get_container.return_value = mock_container

                # Multiple ensure_subscribed calls
                await fanout.ensure_subscribed()
                await fanout.ensure_subscribed()
                await fanout.ensure_subscribed()

                # Should only subscribe once per type
                assert len(mock_director_service._bus.subscribers.get(MessageType.FILE_WRITTEN, [])) == 1
                assert len(mock_director_service._bus.subscribers.get(MessageType.TASK_TRACE, [])) == 1
        finally:
            await fanout.close()

    @pytest.mark.asyncio
    async def test_unsubscribe_on_close(self, mock_director_service):
        """Test that close() unsubscribes handlers."""
        fanout = RuntimeEventFanout()

        async def mock_resolve(interface):
            return mock_director_service

        with patch(
            "polaris.infrastructure.realtime.process_local.message_event_fanout.get_container"
        ) as mock_get_container:
            mock_container = MagicMock()
            mock_container.resolve_async = mock_resolve
            mock_get_container.return_value = mock_container

            await fanout.ensure_subscribed()

            # Verify subscribed
            assert len(mock_director_service._bus.subscribers.get(MessageType.FILE_WRITTEN, [])) == 1

            await fanout.close()

            # Verify unsubscribed
            assert len(mock_director_service._bus.subscribers.get(MessageType.FILE_WRITTEN, [])) == 0


class TestRuntimeEventFanoutConcurrency:
    """Tests for concurrent behavior."""

    @pytest.mark.asyncio
    async def test_concurrent_register_unregister(self):
        """Test concurrent registration and unregistration."""
        fanout = RuntimeEventFanout()

        async def register_unregister(i):
            await fanout.register_connection(f"conn-{i}", f"/ws{i}", f"/cache{i}")
            await asyncio.sleep(0.001)
            await fanout.unregister_connection(f"conn-{i}")

        # Run many concurrent operations
        await asyncio.gather(*[register_unregister(i) for i in range(50)])

        # All should be unregistered
        assert len(fanout.list_connections()) == 0

        await fanout.close()

    @pytest.mark.asyncio
    async def test_concurrent_event_distribution(self, mock_director_service):
        """Test concurrent event handling."""
        fanout = RuntimeEventFanout()

        try:
            with patch.object(fanout, "_bus", mock_director_service._bus):
                fanout._file_handler = fanout._make_file_written_handler(asyncio.get_event_loop())

                # Register multiple connections
                for i in range(10):
                    await fanout.register_connection(f"conn-{i}", f"/ws{i}", f"/cache{i}")

                # Send events concurrently
                async def send_events(n):
                    for i in range(n):
                        message = Message(
                            type=MessageType.FILE_WRITTEN,
                            sender="test",
                            payload={"file_path": f"/test_{i}.py"},
                        )
                        fanout._file_handler(message)
                        await asyncio.sleep(0)  # Yield control

                await asyncio.gather(send_events(20), send_events(20))

                # Each connection should have events
                total_events = 0
                for i in range(10):
                    file_events, _, _, _ = await fanout.drain_events(f"conn-{i}")
                    total_events += len(file_events)

                # All events should be distributed
                assert total_events == 400  # 10 connections * 40 events each
        finally:
            await fanout.close()
