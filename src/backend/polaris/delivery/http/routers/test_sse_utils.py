"""Tests for polaris.delivery.http.routers.sse_utils module.

Covers:
- sse_jetstream_generator: normal iteration, early break, exception exit
- SSEJetStreamConsumer: connect, disconnect, stream
- sse_event_generator: cleanup on exit
- Security validation functions: subject, workspace key, payload size, timestamp

SECURITY TESTS:
- S1: Payload size limits
- S2: Schema validation
- S3: Replay attack protection (timestamp freshness, event ID deduplication)
- S4: Cryptographically random consumer names
- S5: Subject pattern validation and sanitization
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.delivery.http.routers.sse_utils import (
    SSEJetStreamConsumer,
    create_sse_jetstream_consumer,
    create_sse_response,
    generate_event_signature,
    sse_event_generator,
    sse_jetstream_generator,
    validate_event_timestamp,
    validate_payload_size,
    validate_subject,
    validate_workspace_key,
    verify_event_signature,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# =============================================================================
# Security Validation Tests
# =============================================================================


class TestSecurityValidation:
    """Tests for security validation functions."""

    # S5: Subject pattern validation

    def test_valid_subjects(self) -> None:
        """Verify valid subject patterns are accepted."""
        valid_subjects = [
            "hp.runtime.my-workspace.event.factory",
            "events.test123",
            "a",
            "a.b.c",
            "test-workspace_123.run",
        ]
        for subject in valid_subjects:
            assert validate_subject(subject) is True, f"Subject should be valid: {subject}"

    def test_invalid_subjects(self) -> None:
        """Verify invalid subject patterns are rejected."""
        invalid_subjects = [
            "",  # empty
            "../../../etc/passwd",  # path traversal
            "subject\x00null",  # null byte injection
            "subject with spaces",  # spaces not allowed
            "subject;DROP TABLE",  # command injection attempt
            "subject$(whoami)",  # command substitution
            "a" * 201,  # too long (max 200 chars after first)
            "../workspace",  # path traversal
        ]
        for subject in invalid_subjects:
            assert validate_subject(subject) is False, f"Subject should be rejected: {subject}"

    # S4: Workspace key validation

    def test_valid_workspace_keys(self) -> None:
        """Verify valid workspace key formats are accepted."""
        valid_keys = [
            "my-workspace",
            "test123",
            "a",
            "workspace-with-123-dashes",
        ]
        for key in valid_keys:
            assert validate_workspace_key(key) is True, f"Workspace key should be valid: {key}"

    def test_invalid_workspace_keys(self) -> None:
        """Verify invalid workspace key formats are rejected."""
        invalid_keys = [
            "",  # empty
            "workspace.with.dots",  # dots not allowed
            "workspace_underscore",  # underscores not allowed
            "workspace with spaces",  # spaces not allowed
            "a" * 65,  # too long (max 64 chars)
            "../../etc",  # path traversal
        ]
        for key in invalid_keys:
            assert validate_workspace_key(key) is False, f"Workspace key should be rejected: {key}"

    # S2: Payload size validation

    def test_valid_payload_sizes(self) -> None:
        """Verify valid payload sizes are accepted."""
        from polaris.delivery.http.routers.sse_utils import MAX_PAYLOAD_SIZE

        # Small payload
        assert validate_payload_size({"key": "value"}) is True

        # Empty payload
        assert validate_payload_size({}) is True

        # Exactly at limit
        large_payload = "x" * MAX_PAYLOAD_SIZE
        assert validate_payload_size(large_payload) is True

        # Bytes at limit
        assert validate_payload_size(b"x" * MAX_PAYLOAD_SIZE) is True

    def test_oversized_payload_rejected(self) -> None:
        """Verify oversized payloads are rejected."""
        from polaris.delivery.http.routers.sse_utils import MAX_PAYLOAD_SIZE

        # Dict that's too large when serialized
        oversized = {"data": "x" * (MAX_PAYLOAD_SIZE + 1)}
        assert validate_payload_size(oversized) is False

        # String that's too large
        assert validate_payload_size("x" * (MAX_PAYLOAD_SIZE + 1)) is False

        # Bytes that are too large
        assert validate_payload_size(b"x" * (MAX_PAYLOAD_SIZE + 1)) is False

    # S3: Event timestamp freshness validation

    def test_fresh_timestamps_accepted(self) -> None:
        """Verify fresh timestamps are accepted."""
        # Current time
        now = datetime.now(timezone.utc).isoformat()
        assert validate_event_timestamp(now) is True

        # Recent timestamp (5 minutes ago)
        recent = datetime.fromtimestamp(time.time() - 300, tz=timezone.utc).isoformat()
        assert validate_event_timestamp(recent) is True

    def test_stale_timestamps_rejected(self) -> None:
        """Verify stale timestamps (replay window exceeded) are rejected."""
        from polaris.delivery.http.routers.sse_utils import MAX_REPLAY_WINDOW_SECONDS

        # Timestamp older than replay window
        old_ts = datetime.fromtimestamp(
            time.time() - MAX_REPLAY_WINDOW_SECONDS - 60,
            tz=timezone.utc,
        ).isoformat()
        assert validate_event_timestamp(old_ts) is False

    def test_missing_timestamp_allowed(self) -> None:
        """Verify missing timestamps are allowed for backward compatibility."""
        assert validate_event_timestamp(None) is True

    def test_non_utc_timestamps_allowed(self) -> None:
        """Verify non-UTC timestamps are allowed for compatibility."""
        # Non-UTC timestamps pass through for backward compatibility
        assert validate_event_timestamp("2026-05-01T12:00:00+08:00") is True

    # S1: Event signature generation and verification

    def test_signature_generation_and_verification(self) -> None:
        """Verify event signatures can be generated and verified."""
        event_id = "evt-123"
        timestamp = "2026-05-01T12:00:00Z"
        payload = {"key": "value"}

        signature = generate_event_signature(event_id, timestamp, payload)

        # Signature should be a hex string
        assert len(signature) == 64  # SHA256 hex = 64 chars

        # Same inputs should produce same signature
        assert signature == generate_event_signature(event_id, timestamp, payload)

        # Different inputs should produce different signature
        assert signature != generate_event_signature("evt-456", timestamp, payload)

        # Verification should succeed for valid signature
        assert verify_event_signature(event_id, timestamp, payload, signature) is True

    def test_signature_verification_fails_on_tampering(self) -> None:
        """Verify signature verification fails when payload is tampered."""
        event_id = "evt-123"
        timestamp = "2026-05-01T12:00:00Z"
        original_payload = {"key": "value"}
        tampered_payload = {"key": "tampered"}

        signature = generate_event_signature(event_id, timestamp, original_payload)

        # Verification should fail with tampered payload
        assert verify_event_signature(event_id, timestamp, tampered_payload, signature) is False


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

    def test_consumer_rejects_invalid_workspace_key(self) -> None:
        """Verify consumer raises ValueError for invalid workspace key.

        SECURITY S4: Workspace key validation prevents injection attacks.
        """
        with pytest.raises(ValueError, match="Invalid workspace_key"):
            SSEJetStreamConsumer(
                workspace_key="../../etc",  # path traversal attempt
                subject="events.test",
            )

    def test_consumer_rejects_invalid_subject(self) -> None:
        """Verify consumer raises ValueError for invalid subject pattern.

        SECURITY S5: Subject pattern validation prevents subject injection.
        """
        with pytest.raises(ValueError, match="Invalid subject"):
            SSEJetStreamConsumer(
                workspace_key="valid-workspace",
                subject="../../../dangerous",  # path traversal attempt
            )

    def test_consumer_name_is_cryptographically_random(self) -> None:
        """Verify consumer names use cryptographic randomness.

        SECURITY S4: Predictable ephemeral consumer names can be exploited.
        Using id(self) is predictable; should use secrets.token_hex().
        """
        consumer1 = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )
        consumer2 = SSEJetStreamConsumer(
            workspace_key="test",
            subject="events",
        )

        # Names should be different (cryptographically random)
        assert consumer1.consumer_name != consumer2.consumer_name

        # Name should contain hex characters (random suffix)
        assert len(consumer1.consumer_name) > len("sse-test-")


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
            "BUG M4: disconnect_error_secondary should not be the raised exception; it masks the original stream error."
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


class TestSseEventGeneratorTerminalErrors:
    """Regression tests for non-terminal task failures in direct SSE streams."""

    @pytest.mark.asyncio
    async def test_unexpected_task_exception_emits_terminal_error(self) -> None:
        async def failing_task(_queue: asyncio.Queue[dict[str, Any]]) -> None:
            raise OSError("provider stream failed")

        events: list[str] = []
        async for event in sse_event_generator(failing_task, timeout=0.01):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "provider stream failed" in events[0]

    @pytest.mark.asyncio
    async def test_task_completion_without_terminal_event_emits_error(self) -> None:
        async def no_terminal_event(_queue: asyncio.Queue[dict[str, Any]]) -> None:
            return None

        events: list[str] = []
        async for event in sse_event_generator(no_terminal_event, timeout=0.01):
            events.append(event)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "without a terminal event" in events[0]

    @pytest.mark.asyncio
    async def test_task_completion_without_terminal_event_does_not_wait_for_ping_timeout(self) -> None:
        async def no_terminal_event(_queue: asyncio.Queue[dict[str, Any]]) -> None:
            return None

        async def collect_events() -> list[str]:
            events: list[str] = []
            async for event in sse_event_generator(no_terminal_event, timeout=30.0):
                events.append(event)
            return events

        events = await asyncio.wait_for(collect_events(), timeout=0.5)

        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "without a terminal event" in events[0]


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
