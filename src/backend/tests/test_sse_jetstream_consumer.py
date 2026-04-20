"""Tests for SSEJetStreamConsumer generator caching fix (P0-003).

This module tests that __anext__() properly caches the stream iterator
to prevent event loss and state reset issues.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockJetStreamMessage:
    """Mock NATS JetStream message."""

    def __init__(self, data: dict[str, Any]):
        self.data = data if isinstance(data, bytes) else str(data).encode("utf-8")
        self._acked = False

    async def ack(self) -> None:
        self._acked = True


class TestSSEJetStreamConsumerGeneratorCaching:
    """Test suite for SSEJetStreamConsumer.__anext__() generator caching.

    Validates:
    1. Sequential __anext__() calls return different events (no state reset)
    2. Early disconnect properly cleans up resources
    3. StopAsyncIteration is raised after exhausting events
    4. Ping events are filtered out
    """

    @pytest.fixture
    def mock_consumer_config(self) -> dict[str, Any]:
        """Default consumer configuration."""
        return {
            "workspace_key": "test-workspace",
            "subject": "hp.test.events",
            "last_event_id": 0,
            "timeout": 30.0,
        }

    @pytest.fixture
    def sample_events(self) -> list[dict[str, Any]]:
        """Sample events for testing."""
        return [
            {"type": "event_a", "data": {"value": 1}},
            {"type": "event_b", "data": {"value": 2}},
            {"type": "event_c", "data": {"value": 3}},
        ]

    def _create_mock_stream(
        self, events: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Create a mock async generator that yields the given events."""

        async def mock_generator() -> AsyncGenerator[dict[str, Any], None]:
            for event in events:
                yield event

        return mock_generator()

    @pytest.mark.asyncio
    async def test_sequential_anext_returns_different_events(
        self, mock_consumer_config: dict[str, Any], sample_events: list[dict[str, Any]]
    ) -> None:
        """Test that consecutive __anext__() calls return different events.

        This is the primary regression test for P0-003: without the fix,
        each __anext__() call would create a new generator and restart
        from the beginning, causing event loss.
        """
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        # Create mock events
        mock_events = [
            {"cursor": 1, "ts": "2024-01-01T00:00:00Z", "payload": {"type": "event_a", "data": {"value": 1}}},
            {"cursor": 2, "ts": "2024-01-01T00:00:01Z", "payload": {"type": "event_b", "data": {"value": 2}}},
            {"cursor": 3, "ts": "2024-01-01T00:00:02Z", "payload": {"type": "event_c", "data": {"value": 3}}},
        ]

        # Create a mock stream generator
        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for event in mock_events:
                yield event

        # Patch the stream method to use our mock
        with patch.object(consumer, "stream", mock_stream):
            # Consume all events via __anext__()
            received_events = []
            for _ in range(len(mock_events)):
                try:
                    event = await consumer.__anext__()
                    received_events.append(event)
                except StopAsyncIteration:
                    break

            # Verify: all events received, in order
            assert len(received_events) == len(mock_events), (
                f"Expected {len(mock_events)} events, got {len(received_events)}. "
                "This indicates generator state is being reset on each __anext__() call."
            )

            for i, (received, expected) in enumerate(zip(received_events, mock_events)):
                assert received["payload"]["type"] == expected["payload"]["type"], (
                    f"Event {i} mismatch: expected type '{expected['payload']['type']}', "
                    f"got '{received['payload']['type']}'"
                )
                assert received["cursor"] == expected["cursor"], (
                    f"Event {i} cursor mismatch"
                )

    @pytest.mark.asyncio
    async def test_anext_raises_stop_iteration_after_exhaustion(
        self, mock_consumer_config: dict[str, Any], sample_events: list[dict[str, Any]]
    ) -> None:
        """Test that StopAsyncIteration is raised after consuming all events."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        mock_events = [
            {"cursor": 1, "payload": {"type": "event_a", "data": {"value": 1}}},
            {"cursor": 2, "payload": {"type": "event_b", "data": {"value": 2}}},
        ]

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for event in mock_events:
                yield event

        with patch.object(consumer, "stream", mock_stream):
            # Consume all events
            for _ in range(len(mock_events)):
                await consumer.__anext__()

            # Next call should raise StopAsyncIteration
            with pytest.raises(StopAsyncIteration):
                await consumer.__anext__()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up_iterator(
        self, mock_consumer_config: dict[str, Any]
    ) -> None:
        """Test that disconnect() properly cleans up the cached iterator."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        mock_events = [
            {"cursor": 1, "payload": {"type": "event_a", "data": {}}},
        ]

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield mock_events[0]

        with patch.object(consumer, "stream", mock_stream):
            # Get first event to initialize the cached iterator
            first_event = await consumer.__anext__()
            assert first_event is not None
            assert consumer._stream_iter is not None, (
                "Cached iterator should be initialized after first __anext__() call"
            )

            # Disconnect should clean up the iterator
            await consumer.disconnect()

            # Iterator should be None after disconnect
            assert consumer._stream_iter is None, (
                "Cached iterator should be None after disconnect()"
            )

            # Consumer should be marked as closed
            assert consumer._closed is True

    @pytest.mark.asyncio
    async def test_anext_filters_ping_events(
        self, mock_consumer_config: dict[str, Any]
    ) -> None:
        """Test that ping events are filtered out by __anext__()."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        # Create sequence with ping events in between
        mock_events = [
            {"cursor": 1, "payload": {"type": "event_a", "data": {"value": 1}}},
            {"type": "ping", "cursor": 1},  # Should be filtered
            {"cursor": 2, "payload": {"type": "event_b", "data": {"value": 2}}},
            {"type": "ping", "cursor": 2},  # Should be filtered
        ]

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for event in mock_events:
                yield event

        with patch.object(consumer, "stream", mock_stream):
            # Get first event
            event1 = await consumer.__anext__()
            assert event1["payload"]["type"] == "event_a"

            # Get second event (should skip ping)
            event2 = await consumer.__anext__()
            assert event2["payload"]["type"] == "event_b"

    @pytest.mark.asyncio
    async def test_iterator_reuse_across_multiple_consumers(
        self, mock_consumer_config: dict[str, Any]
    ) -> None:
        """Test that each consumer instance maintains its own iterator state."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        # Create two separate consumers
        consumer1 = SSEJetStreamConsumer(**mock_consumer_config)
        consumer2 = SSEJetStreamConsumer(**{**mock_consumer_config, "workspace_key": "other"})

        mock_events1 = [{"cursor": 1, "payload": {"type": "event_a", "data": {}}}]
        mock_events2 = [{"cursor": 10, "payload": {"type": "event_c", "data": {}}}]

        async def mock_stream1() -> AsyncGenerator[dict[str, Any], None]:
            for event in mock_events1:
                yield event

        async def mock_stream2() -> AsyncGenerator[dict[str, Any], None]:
            for event in mock_events2:
                yield event

        with patch.object(consumer1, "stream", mock_stream1), patch.object(
            consumer2, "stream", mock_stream2
        ):
            # Consume one event from consumer1
            event1 = await consumer1.__anext__()

            # Consume events from consumer2
            event2 = await consumer2.__anext__()

            # Verify: each consumer has its own state
            assert event1["payload"]["type"] == "event_a"
            assert event2["payload"]["type"] == "event_c"

            # Verify: iterators are different objects
            assert consumer1._stream_iter is not consumer2._stream_iter

    @pytest.mark.asyncio
    async def test_initially_none_stream_iter(
        self, mock_consumer_config: dict[str, Any]
    ) -> None:
        """Test that _stream_iter is None before any iteration."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        # Before any iteration, _stream_iter should be None
        assert consumer._stream_iter is None

    @pytest.mark.asyncio
    async def test_stream_iter_initialized_on_first_anext(
        self, mock_consumer_config: dict[str, Any]
    ) -> None:
        """Test that _stream_iter is initialized after first __anext__() call."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(**mock_consumer_config)

        mock_events = [{"cursor": 1, "payload": {"type": "test", "data": {}}}]

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield mock_events[0]

        with patch.object(consumer, "stream", mock_stream):
            # Before calling __anext__, iterator is None
            assert consumer._stream_iter is None

            # Call __anext__
            await consumer.__anext__()

            # After calling __anext__, iterator should be set
            assert consumer._stream_iter is not None


class TestSSEEventGenerator:
    """Tests for the SSE event generator function."""

    @pytest.mark.asyncio
    async def test_sse_event_generator_yields_completion(self) -> None:
        """Test that sse_event_generator yields completion event."""
        from polaris.delivery.http.routers.sse_utils import sse_event_generator

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "complete", "data": {"status": "done"}})

        frames = []
        async for frame in sse_event_generator(task_fn, timeout=1.0):
            frames.append(frame)

        assert len(frames) == 1
        assert "complete" in frames[0]
        assert "done" in frames[0]

    @pytest.mark.asyncio
    async def test_sse_event_generator_yields_error(self) -> None:
        """Test that sse_event_generator yields error event on exception."""
        from polaris.delivery.http.routers.sse_utils import sse_event_generator

        async def task_fn(queue: asyncio.Queue) -> None:
            raise ValueError("test error")

        frames = []
        async for frame in sse_event_generator(task_fn, timeout=1.0):
            frames.append(frame)

        assert len(frames) == 1
        assert "error" in frames[0]

    @pytest.mark.asyncio
    async def test_sse_event_generator_multiple_events(self) -> None:
        """Test that sse_event_generator yields multiple events."""
        from polaris.delivery.http.routers.sse_utils import sse_event_generator

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {"step": 1}})
            await queue.put({"type": "message", "data": {"step": 2}})
            await queue.put({"type": "complete", "data": {}})

        frames = []
        async for frame in sse_event_generator(task_fn, timeout=1.0):
            frames.append(frame)

        assert len(frames) == 3
        assert "step" in frames[0]
        assert "step" in frames[1]
        assert "complete" in frames[2]


class TestCreateSSEJetStreamConsumer:
    """Tests for create_sse_jetstream_consumer factory function."""

    def test_creates_consumer_with_correct_config(self) -> None:
        """Test that factory creates consumer with correct configuration."""
        from polaris.delivery.http.routers.sse_utils import (
            SSEJetStreamConsumer,
            create_sse_jetstream_consumer,
        )

        consumer = create_sse_jetstream_consumer(
            workspace_key="my-workspace",
            subject="hp.my.events",
            last_event_id=42,
        )

        assert isinstance(consumer, SSEJetStreamConsumer)
        assert consumer.workspace_key == "my-workspace"
        assert consumer.subject == "hp.my.events"
        assert consumer.last_event_id == 42

    def test_defaults_last_event_id_to_zero(self) -> None:
        """Test that last_event_id defaults to 0 when not provided."""
        from polaris.delivery.http.routers.sse_utils import (
            create_sse_jetstream_consumer,
        )

        consumer = create_sse_jetstream_consumer(
            workspace_key="my-workspace",
            subject="hp.my.events",
        )

        assert consumer.last_event_id == 0

    def test_handles_none_last_event_id(self) -> None:
        """Test that None last_event_id is treated as 0."""
        from polaris.delivery.http.routers.sse_utils import (
            create_sse_jetstream_consumer,
        )

        consumer = create_sse_jetstream_consumer(
            workspace_key="my-workspace",
            subject="hp.my.events",
            last_event_id=None,
        )

        assert consumer.last_event_id == 0


class TestSSEJetStreamConsumerEdgeCases:
    """Test edge cases for SSEJetStreamConsumer."""

    @pytest.mark.asyncio
    async def test_multiple_anext_calls_share_generator(self) -> None:
        """Verify that multiple __anext__ calls use the same generator instance."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="hp.test",
        )

        call_count = 0

        async def counting_stream() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            for i in range(5):
                call_count += 1
                yield {"cursor": i + 1, "payload": {"type": f"event_{i}", "data": {}}}
                # Simulate some async work between yields
                await asyncio.sleep(0)

        with patch.object(consumer, "stream", counting_stream):
            # First call - should initialize generator
            await consumer.__anext__()
            first_iter = consumer._stream_iter

            # Second call - should use same generator
            await consumer.__anext__()
            second_iter = consumer._stream_iter

            # Verify same generator is used
            assert first_iter is second_iter, (
                "Multiple __anext__ calls should share the same generator"
            )

            # Verify stream was only initialized once
            # (counting_stream is called once, yielding 5 items)
            assert call_count == 2, f"Expected 2 calls, got {call_count}"

    @pytest.mark.asyncio
    async def test_disconnect_sets_closed_flag(self) -> None:
        """Test that disconnect() properly sets the _closed flag."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="hp.test",
        )

        # Create mock subscription
        mock_sub = AsyncMock()
        mock_sub.unsubscribe = AsyncMock()
        consumer._subscription = mock_sub

        # Before disconnect
        assert consumer._closed is False

        # Disconnect
        await consumer.disconnect()

        # After disconnect
        assert consumer._closed is True
        mock_sub.unsubscribe.assert_called_once()

    def test_consumer_name_auto_generated(self) -> None:
        """Test that consumer name is auto-generated when not provided."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(
            workspace_key="my-workspace",
            subject="hp.my.events",
        )

        assert consumer.consumer_name is not None
        assert "my-workspace" in consumer.consumer_name
        assert consumer.consumer_name.startswith("sse-")

    def test_consumer_name_preserved_when_provided(self) -> None:
        """Test that provided consumer name is preserved."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(
            workspace_key="my-workspace",
            subject="hp.my.events",
            consumer_name="my-custom-consumer",
        )

        assert consumer.consumer_name == "my-custom-consumer"

    def test_is_connected_property(self) -> None:
        """Test is_connected property behavior."""
        from polaris.delivery.http.routers.sse_utils import SSEJetStreamConsumer

        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="hp.test",
        )

        # Initially not connected
        assert consumer.is_connected is False

        # After setting jetstream
        consumer._jetstream = MagicMock()
        assert consumer.is_connected is True

        # After closing
        consumer._closed = True
        assert consumer.is_connected is False
