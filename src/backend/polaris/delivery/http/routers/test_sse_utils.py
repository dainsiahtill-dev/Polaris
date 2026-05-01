"""Tests for polaris.delivery.http.routers.sse_utils module.

Covers:
- sse_jetstream_generator: normal iteration, early break, exception exit
- SSEJetStreamConsumer: connect, disconnect, stream
- sse_event_generator: cleanup on exit
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.delivery.http.routers.sse_utils import (
    SSEJetStreamConsumer,
    create_sse_jetstream_consumer,
    create_sse_response,
    sse_event_generator,
    sse_jetstream_generator,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncGenerator

# -----------------------------------------------------------------------------
# Tests for sse_event_generator
# -----------------------------------------------------------------------------


class TestSseEventGenerator:
    """Tests for sse_event_generator utility."""

    @pytest.mark.asyncio
    async def test_normal_completion(self) -> None:
        """Verify normal completion with complete event."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "message", "data": {"text": "hello"}})
            await queue.put({"type": "complete", "data": {}})

        cleanup_called = False

        async def cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        events = []
        async for event in sse_event_generator(task_fn, cleanup_fn=cleanup):
            events.append(event)

        assert len(events) == 2
        assert "data:" in events[0]
        assert "complete" in events[1]
        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_error_completion(self) -> None:
        """Verify error event triggers cleanup."""

        async def task_fn(queue: asyncio.Queue) -> None:
            await queue.put({"type": "error", "data": {"message": "failed"}})

        cleanup_called = False

        async def cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        events = []
        async for event in sse_event_generator(task_fn, cleanup_fn=cleanup):
            events.append(event)

        assert len(events) == 1
        assert "error" in events[0]
        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_early_break(self) -> None:
        """Verify early break triggers cleanup.

        Note: Python does not automatically close async generators when
        iteration exits early. Our finally block schedules cleanup, which
        runs when the generator is garbage collected or explicitly closed.
        We verify cleanup by explicitly closing the generator.
        """

        async def task_fn(queue: asyncio.Queue) -> None:
            for i in range(100):
                await queue.put({"type": "message", "data": {"text": str(i)}})

        cleanup_called = False

        async def cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        generator = sse_event_generator(task_fn, cleanup_fn=cleanup)
        events = []
        async for event in generator:
            events.append(event)
            if len(events) >= 3:
                break

        # Explicitly close the generator to trigger finally block
        await generator.aclose()

        assert len(events) == 3
        assert cleanup_called is True


# -----------------------------------------------------------------------------
# Tests for SSEJetStreamConsumer
# -----------------------------------------------------------------------------


class TestSSEJetStreamConsumer:
    """Tests for SSEJetStreamConsumer class."""

    def test_consumer_initialization(self) -> None:
        """Verify consumer initializes with correct defaults."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test-workspace",
            subject="events.test",
            last_event_id=10,
        )

        assert consumer.workspace_key == "test-workspace"
        assert consumer.subject == "events.test"
        assert consumer.last_event_id == 10
        assert consumer.is_connected is False

    def test_consumer_is_connected_property(self) -> None:
        """Verify is_connected reflects connection state."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Initially not connected
        assert consumer.is_connected is False

        # After setting _jetstream (simulating connection)
        consumer._jetstream = MagicMock()
        assert consumer.is_connected is True

        # After setting _closed
        consumer._closed = True
        assert consumer.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up_subscription(self) -> None:
        """Verify disconnect unsubscribes and clears references."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Mock subscription
        mock_subscription = AsyncMock()
        consumer._subscription = mock_subscription
        consumer._jetstream = MagicMock()

        await consumer.disconnect()

        mock_subscription.unsubscribe.assert_called_once()
        assert consumer._subscription is None
        assert consumer._jetstream is None
        assert consumer._closed is True

    @pytest.mark.asyncio
    async def test_disconnect_handles_missing_subscription(self) -> None:
        """Verify disconnect works without subscription."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Should not raise
        await consumer.disconnect()

        assert consumer._closed is True


# -----------------------------------------------------------------------------
# Tests for sse_jetstream_generator (the main fix)
# -----------------------------------------------------------------------------


class TestSseJetstreamGenerator:
    """Tests for sse_jetstream_generator cleanup behavior."""

    @pytest.mark.asyncio
    async def test_normal_iteration(self) -> None:
        """Verify normal iteration completes and disconnects.

        The mock stream yields 2 events but the first one is a "message" type
        and the second is a "complete" type. The sse_jetstream_generator
        only yields non-ping events, so we expect 1 event.
        """
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Mock the stream method - only yields message events
        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}

        consumer.stream = mock_stream
        disconnect_called = False

        original_disconnect = consumer.disconnect

        async def tracked_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True
            await original_disconnect()

        consumer.disconnect = tracked_disconnect

        events = []
        async for event in sse_jetstream_generator(consumer):
            events.append(event)

        # Generator should complete after consuming all events
        assert len(events) == 1
        assert disconnect_called is True

    @pytest.mark.asyncio
    async def test_early_break(self) -> None:
        """Verify early break still calls disconnect.

        Note: Python does not automatically close async generators when
        iteration exits early. We verify cleanup by explicitly closing
        the generator.
        """
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Mock stream with many events
        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            for i in range(100):
                yield {"type": "message", "payload": {"text": str(i)}, "cursor": i + 1, "ts": None}

        consumer.stream = mock_stream
        disconnect_called = False

        original_disconnect = consumer.disconnect

        async def tracked_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True
            await original_disconnect()

        consumer.disconnect = tracked_disconnect

        generator = sse_jetstream_generator(consumer)
        events = []
        async for event in generator:
            events.append(event)
            if len(events) >= 2:
                break

        # Explicitly close the generator to trigger finally block
        await generator.aclose()

        assert len(events) == 2
        assert disconnect_called is True

    @pytest.mark.asyncio
    async def test_exception_during_iteration(self) -> None:
        """Verify exception triggers disconnect."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Mock stream that raises
        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}
            raise RuntimeError("stream error")

        consumer.stream = mock_stream
        disconnect_called = False

        original_disconnect = consumer.disconnect

        async def tracked_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True
            await original_disconnect()

        consumer.disconnect = tracked_disconnect

        events = []
        with pytest.raises(RuntimeError):
            async for event in sse_jetstream_generator(consumer):
                events.append(event)

        # disconnect should still be called even on exception
        assert disconnect_called is True

    @pytest.mark.asyncio
    async def test_ping_events_handled(self) -> None:
        """Verify ping events are converted to SSE ping format."""
        consumer = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "ping", "cursor": 0}
            yield {"type": "message", "payload": {"text": "data"}, "cursor": 1, "ts": None}

        consumer.stream = mock_stream
        consumer.disconnect = AsyncMock()

        events = []
        async for event in sse_jetstream_generator(consumer):
            events.append(event)

        assert "ping" in events[0]
        assert "id: 1" in events[1]


# -----------------------------------------------------------------------------
# Tests for create_sse_jetstream_consumer factory
# -----------------------------------------------------------------------------


class TestCreateSseJetstreamConsumer:
    """Tests for create_sse_jetstream_consumer factory."""

    def test_creates_consumer_with_defaults(self) -> None:
        """Verify factory creates consumer with default settings."""
        consumer = create_sse_jetstream_consumer(
            workspace_key="my-workspace",
            subject="events.my-workspace",
        )

        assert consumer.workspace_key == "my-workspace"
        assert consumer.subject == "events.my-workspace"
        assert consumer.last_event_id == 0

    def test_creates_consumer_with_last_event_id(self) -> None:
        """Verify factory respects last_event_id parameter."""
        consumer = create_sse_jetstream_consumer(
            workspace_key="ws",
            subject="events",
            last_event_id=50,
        )

        assert consumer.last_event_id == 50


# =============================================================================
# Regression tests for confirmed defects
# =============================================================================
# M4: sse_jetstream_generator finally block shadowing original exception
#     The disconnect() call in the finally block can raise its own exception,
#     which in Python's async generator cleanup replaces the original one,
#     making debugging harder and masking root-cause errors.


class TestJetstreamGeneratorExceptionPreservation:
    """Regression tests for M4: exception shadowing in sse_jetstream_generator."""

    @pytest.mark.asyncio
    async def test_jetstream_stream_exception_not_shadowed_by_disconnect_error(self) -> None:
        """Verify the original stream exception is preserved when disconnect() also fails.

        Bug (M4): The finally block in sse_jetstream_generator is:
            finally:
                await consumer.disconnect()
        If consumer.disconnect() raises an exception (e.g. cleanup failure),
        it can shadow the original RuntimeError from the stream.

        After fix: disconnect errors should be caught and logged, preserving
        the original stream exception as the propagated error.
        """
        consumer = SSEJetStreamConsumer(workspace_key="test", subject="events")

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}
            raise RuntimeError("stream_error_original")  # original exception

        async def mock_disconnect() -> None:
            raise RuntimeError("disconnect_error_secondary")  # shadowing exception

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = mock_disconnect  # type: ignore[method-assign]

        gen = sse_jetstream_generator(consumer)
        collected: list[str] = []

        with pytest.raises(RuntimeError) as exc_info:
            async for event in gen:
                collected.append(event)

        # The original stream exception must be preserved, not the disconnect error
        assert "stream_error_original" in str(exc_info.value), (
            f"BUG M4: Expected 'stream_error_original' in raised exception, "
            f"got: {exc_info.value!s}. The disconnect error is shadowing the root cause."
        )
        # Disconnect error must NOT be the primary exception
        assert "disconnect_error_secondary" not in str(exc_info.value), (
            "BUG M4: disconnect_error_secondary should not be the raised exception; "
            "it masks the original stream error."
        )

    @pytest.mark.asyncio
    async def test_jetstream_disconnect_error_on_normal_exit_still_raises(self) -> None:
        """Verify disconnect error during normal completion is also handled.

        Even when the stream completes normally, a failing disconnect()
        should not prevent clean exit or should be logged, not raised.
        """
        consumer = SSEJetStreamConsumer(workspace_key="test", subject="events")

        disconnect_called = False

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            # Stream has only one message event, then completes
            yield {"type": "message", "payload": {"text": "done"}, "cursor": 1, "ts": None}

        async def mock_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True
            raise RuntimeError("disconnect_cleanup_error")

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = mock_disconnect  # type: ignore[method-assign]

        gen = sse_jetstream_generator(consumer)
        collected: list[str] = []

        # After fix: should not raise; disconnect error is caught and logged
        try:
            async for event in gen:
                collected.append(event)
        except RuntimeError as e:
            # Before fix, this would raise the disconnect error
            pytest.fail(
                f"BUG M4: sse_jetstream_generator leaked disconnect error as: {e!s}. "
                "Disconnect errors during cleanup should be caught and logged."
            )

        assert disconnect_called, "disconnect() should still be called even on normal exit"
        assert len(collected) == 1, "Should have collected exactly one event"


# -----------------------------------------------------------------------------
# Tests for create_sse_response
# -----------------------------------------------------------------------------


class TestCreateSseResponse:
    """Tests for create_sse_response utility."""

    @pytest.mark.asyncio
    async def test_response_has_correct_headers(self) -> None:
        """Verify SSE response has correct content-type and headers."""

        async def gen() -> AsyncGenerator[str, None]:
            yield "event: message\ndata: {}\n\n"

        response = create_sse_response(gen())

        assert response.media_type == "text/event-stream"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["Connection"] == "keep-alive"
