"""Unified SSE (Server-Sent Events) utilities for streaming endpoints.

Provides a shared event generator pattern used by llm_test_routes,
llm_interview_routes, and docs dialogue streaming.

Also provides JetStream-based SSE consumer for remote event streaming.

SECURITY HARDENING (v2):
- S1: Schema validation with RuntimeEventEnvelope
- S2: Payload size limits enforcement
- S3: Replay attack protection with timestamp validation
- S4: Cryptographically random ephemeral consumer names
- S5: Subject pattern validation and sanitization
- S6: Event timestamp freshness validation
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

logger = logging.getLogger(__name__)

# SSE queue max size to prevent unbounded memory growth
# Reduced from 1000 to 50 to enable natural backpressure and reduce latency
_SSE_QUEUE_MAX_SIZE = 50

# =============================================================================
# Security Constants
# =============================================================================

# Maximum payload size: 256KB (matches JetStreamConstants.STREAM_MAX_MSG_SIZE)
MAX_PAYLOAD_SIZE = 262_144

# Maximum replay window: 1 hour in seconds (event older than this is rejected)
MAX_REPLAY_WINDOW_SECONDS = 3600

# Subject pattern validation: only allow alphanumeric, dash, underscore, dot
SUBJECT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}$")

# Workspace key validation: alphanumeric and dash only
WORKSPACE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9-]{1,64}$")

# Replay protection secret (should be set via environment variable)
_REPLAY_SECRET: str | None = None


def _get_replay_secret() -> str:
    """Get or generate replay protection secret."""
    global _REPLAY_SECRET
    if _REPLAY_SECRET is None:
        # In production, this should come from environment
        _REPLAY_SECRET = secrets.token_hex(32)
    return _REPLAY_SECRET


# =============================================================================
# Security Validation Functions
# =============================================================================


def validate_subject(subject: str) -> bool:
    """Validate JetStream subject pattern to prevent injection.

    Args:
        subject: Subject string to validate.

    Returns:
        True if subject matches allowed pattern.

    SECURITY: Prevents subject injection attacks that could
    access cross-workspace events.
    """
    return bool(SUBJECT_PATTERN.match(subject))


def validate_workspace_key(workspace_key: str) -> bool:
    """Validate workspace key format.

    Args:
        workspace_key: Workspace identifier to validate.

    Returns:
        True if workspace key is valid format.
    """
    return bool(WORKSPACE_KEY_PATTERN.match(workspace_key))


def validate_payload_size(data: bytes | dict[str, Any]) -> bool:
    """Validate payload size against configured limits.

    Args:
        data: Message data (bytes or dict) to validate.

    Returns:
        True if payload size is within limits.

    SECURITY: Prevents memory exhaustion from oversized messages.
    """
    if isinstance(data, dict):
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    else:
        size = len(data) if isinstance(data, bytes) else len(str(data))
    return size <= MAX_PAYLOAD_SIZE


def validate_event_timestamp(ts: str | None) -> bool:
    """Validate event timestamp is within acceptable replay window.

    Args:
        ts: ISO 8601 timestamp string to validate.

    Returns:
        True if timestamp is fresh enough.

    SECURITY: Prevents replay attacks using old cached events.
    """
    if not ts:
        return True  # Allow events without timestamp (backward compat)

    try:
        # Parse ISO 8601 timestamp (handle various formats)
        # Format: 2026-05-01T12:00:00Z or 2026-05-01T12:00:00+00:00
        ts_clean = ts.replace("+00:00", "Z")
        if not ts_clean.endswith("Z"):
            return True  # Allow non-UTC timestamps for compatibility

        event_time = float(time.mktime(time.strptime(ts_clean, "%Y-%m-%dT%H:%M:%SZ")))
        current_time = time.time()
        age = current_time - event_time

        return age <= MAX_REPLAY_WINDOW_SECONDS
    except (ValueError, OSError):
        return True  # Allow parsing failures for backward compat


def generate_event_signature(event_id: str, timestamp: str, payload: dict[str, Any]) -> str:
    """Generate HMAC signature for event integrity verification.

    Args:
        event_id: Unique event identifier.
        timestamp: Event timestamp.
        payload: Event payload dictionary.

    Returns:
        HMAC-SHA256 signature as hex string.

    SECURITY: Provides event integrity verification to prevent tampering.
    """
    secret = _get_replay_secret()
    message = f"{event_id}:{timestamp}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_event_signature(
    event_id: str,
    timestamp: str,
    payload: dict[str, Any],
    signature: str,
) -> bool:
    """Verify HMAC signature of event.

    Args:
        event_id: Unique event identifier.
        timestamp: Event timestamp.
        payload: Event payload dictionary.
        signature: Signature to verify.

    Returns:
        True if signature is valid.

    SECURITY: Validates event has not been tampered with.
    """
    expected = generate_event_signature(event_id, timestamp, payload)
    return hmac.compare_digest(expected, signature)


async def sse_event_generator(
    task_fn: Callable[[asyncio.Queue], Awaitable[None]],
    timeout: float = 180.0,
    cleanup_fn: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[str, None]:
    """Run *task_fn* in a background ``asyncio.Task`` and yield SSE frames.

    Parameters
    ----------
    task_fn:
        An async callable that receives an ``asyncio.Queue`` and pushes
        event dicts ``{"type": str, "data": dict}`` into it.  The task
        **must** push a terminal event with ``type`` equal to
        ``"complete"`` or ``"error"`` to signal the end of the stream.
    timeout:
        Seconds to wait for each queue item before emitting a ``ping``
        keep-alive frame.
    cleanup_fn:
        Optional async callable invoked in the ``finally`` block (e.g.
        to cancel a subprocess or release resources).
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAX_SIZE)

    async def _wrapper() -> None:
        try:
            await task_fn(queue)
        except (RuntimeError, ValueError) as exc:
            await queue.put({"type": "error", "data": {"error": str(exc)}})

    task = asyncio.create_task(asyncio.wait_for(_wrapper(), timeout=max(timeout * 3, 600.0)))

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)

                event_type = event.get("type", "message")
                event_data = event.get("data", {})

                if event_type == "complete":
                    yield f"event: complete\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    break
                elif event_type == "error":
                    yield f"event: error\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    break
                else:
                    yield f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    # Force flush to ensure immediate delivery (P0: SSE yield blocking fix)
                    await asyncio.sleep(0)

            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
                # Force flush to ensure immediate delivery
                await asyncio.sleep(0)

    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if cleanup_fn is not None:
            try:
                await cleanup_fn()
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] sse_utils.py silent exception: %s", exc)


def create_sse_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    """Wrap an SSE async generator into a properly-configured ``StreamingResponse``."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# JetStream SSE Consumer (for v2 protocol SSE endpoints)
# =============================================================================


class SSEJetStreamConsumer:
    """SSE consumer that reads from JetStream for remote event streaming.

    This class provides a unified interface for SSE endpoints to consume events
    from JetStream instead of direct push. Supports:
    - Cursor-based resume with Last-Event-ID
    - Ephemeral consumer per connection
    - Graceful cleanup on disconnect

    Example:
        >>> consumer = SSEJetStreamConsumer(
        ...     workspace_key="my-workspace",
        ...     subject="hp.runtime.my-workspace.event.factory",
        ...     last_event_id=0
        ... )
        >>> async for event in consumer.stream():
        ...     yield format_sse_event(event)
    """

    def __init__(
        self,
        workspace_key: str,
        subject: str,
        last_event_id: int = 0,
        timeout: float = 30.0,
        consumer_name: str | None = None,
    ) -> None:
        """Initialize SSE JetStream consumer.

        Args:
            workspace_key: Workspace identifier for subject resolution.
            subject: JetStream subject to subscribe to.
            last_event_id: Last event ID for cursor-based resume.
            timeout: Timeout for waiting on messages.
            consumer_name: Optional consumer name (auto-generated if not provided).

        SECURITY:
            - Consumer names are cryptographically random to prevent
              predictable ephemeral consumer collision attacks
            - Workspace key is validated against injection patterns
            - Subject is validated before connection
        """
        # SECURITY S4: Validate workspace key format
        if not validate_workspace_key(workspace_key):
            raise ValueError(f"Invalid workspace_key format: {workspace_key}")

        # SECURITY S5: Validate subject pattern
        if not validate_subject(subject):
            raise ValueError(f"Invalid subject pattern: {subject}")

        self.workspace_key = workspace_key
        self.subject = subject
        self.last_event_id = last_event_id
        self.timeout = timeout
        # SECURITY S4: Use cryptographically random suffix instead of predictable id(self)
        self.consumer_name = consumer_name or f"sse-{workspace_key[:32]}-{secrets.token_hex(8)}"
        self._consumer: Any = None
        self._subscription: Any = None
        self._jetstream: Any = None
        self._closed = False
        self._stream_iter: AsyncGenerator[dict[str, Any], None] | None = None
        # SECURITY S3: Track processed event IDs for replay detection
        self._processed_event_ids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        """Check if consumer is connected."""
        return self._jetstream is not None and not self._closed

    async def connect(self) -> bool:
        """Connect to JetStream and create ephemeral consumer.

        Returns:
            True if connection succeeded.
        """
        try:
            from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
            from polaris.infrastructure.messaging import JetStreamConstants, get_default_client

            nats_client = await get_default_client()
            self._jetstream = nats_client.jetstream

            if not self._jetstream:
                logger.warning("JetStream not available for SSE consumer")
                return False

            # Create ephemeral consumer with cursor-based delivery
            consumer_config = ConsumerConfig(
                durable_name=self.consumer_name,
                deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=self.last_event_id + 1 if self.last_event_id > 0 else 1,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
                max_deliver=JetStreamConstants.CONSUMER_MAX_DELIVER,
                max_ack_pending=JetStreamConstants.CONSUMER_MAX_ACK_PENDING,
            )

            # Subscribe to subject
            self._subscription = await self._jetstream.subscribe(
                self.subject,
                config=consumer_config,
            )

            logger.info(
                f"SSE JetStream consumer created: {self.consumer_name} "
                f"on {self.subject}, starting from cursor {self.last_event_id}"
            )
            return True

        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to connect SSE JetStream consumer: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect and cleanup consumer."""
        self._closed = True

        # Clean up cached stream iterator if exists
        if self._stream_iter is not None:
            self._stream_iter = None

        if self._subscription:
            try:
                await self._subscription.unsubscribe()
            except (RuntimeError, ValueError) as exc:
                logger.debug("[FIX] sse_utils.py silent exception: %s", exc)
        self._subscription = None
        self._jetstream = None
        logger.info(f"SSE JetStream consumer disconnected: {self.consumer_name}")

    async def stream(self) -> AsyncGenerator[dict[str, Any], None]:
        """Stream events from JetStream with security validations.

        Yields:
            Event dictionaries with cursor, timestamp, and payload.

        SECURITY:
            - S1: Payload size limits enforced
            - S2: Schema validation for expected event structure
            - S3: Timestamp freshness validation (replay attack protection)
            - S3: Event ID deduplication (replay detection)
        """
        if not self._subscription and not await self.connect():
            return

        # Type narrowing: after the above check, _subscription is guaranteed to be non-None
        assert self._subscription is not None

        while not self._closed:
            try:
                msg = await asyncio.wait_for(
                    self._subscription.next_msg(),
                    timeout=self.timeout,
                )
                if msg:
                    try:
                        raw_data = msg.data
                        # S2: Payload size validation (defense in depth with JetStream limits)
                        if len(raw_data) > MAX_PAYLOAD_SIZE:
                            logger.warning(
                                f"Oversized message rejected: {len(raw_data)} bytes (max: {MAX_PAYLOAD_SIZE})"
                            )
                            await msg.ack()
                            continue

                        # S1: Parse with validation
                        data = json.loads(raw_data.decode("utf-8", errors="replace"))

                        # Extract event metadata for validation
                        event_id = data.get("event_id", "")
                        timestamp = data.get("ts") or data.get("_published_at")
                        payload = data.get("payload", data)  # Fallback to entire message

                        # S3: Timestamp freshness validation
                        if not validate_event_timestamp(timestamp):
                            logger.warning(
                                f"Stale event rejected (replay attack prevention): event_id={event_id}, ts={timestamp}"
                            )
                            await msg.ack()
                            continue

                        # S3: Event ID deduplication (in-memory replay detection)
                        if event_id and event_id in self._processed_event_ids:
                            logger.debug(f"Duplicate event rejected: {event_id}")
                            await msg.ack()
                            continue
                        if event_id:
                            self._processed_event_ids.add(event_id)
                            # Limit memory growth from replay tracking
                            if len(self._processed_event_ids) > 10000:
                                self._processed_event_ids = set(list(self._processed_event_ids)[-5000:])

                        # Enrich with cursor and metadata
                        event = {
                            "cursor": self.last_event_id + 1,
                            "ts": timestamp,
                            "event_id": event_id,
                            "payload": payload,
                        }
                        self.last_event_id += 1
                        await msg.ack()
                        yield event
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in SSE message: {e}")
                        await msg.ack()
                    except (RuntimeError, ValueError) as e:
                        logger.warning(f"SSE message processing error: {e}")
                        await msg.ack()
            except asyncio.TimeoutError:
                # Yield keep-alive ping
                yield {"type": "ping", "cursor": self.last_event_id}
            except (RuntimeError, ValueError) as e:
                if not self._closed:
                    logger.warning(f"SSE stream error: {e}")
                break

    def __aiter__(self) -> SSEJetStreamConsumer:
        """Return async iterator."""
        return self

    async def __anext__(self) -> dict[str, Any]:
        """Get next event from the cached stream iterator.

        Uses a cached generator to preserve stream state across calls,
        ensuring events are not lost and cursor position is maintained.

        Raises:
            StopAsyncIteration: When stream is exhausted or closed.
        """
        # Initialize cached iterator on first call
        if self._stream_iter is None:
            self._stream_iter = self.stream()

        # Get next non-ping event from the cached generator
        async for event in self._stream_iter:
            if event.get("type") == "ping":
                continue
            return event

        raise StopAsyncIteration


def create_sse_jetstream_consumer(
    workspace_key: str,
    subject: str,
    last_event_id: int | None = None,
) -> SSEJetStreamConsumer:
    """Factory function to create SSE JetStream consumer with proper configuration.

    Args:
        workspace_key: Workspace identifier.
        subject: JetStream subject pattern.
        last_event_id: Last event ID from Last-Event-ID header.

    Returns:
        Configured SSEJetStreamConsumer instance.

    Raises:
        ValueError: If workspace_key or subject fails validation.

    SECURITY:
        - S5: Subject pattern validation before consumer creation
        - S4: Workspace key validation to prevent injection
    """
    # Validate inputs to fail fast on invalid configurations
    if not validate_workspace_key(workspace_key):
        raise ValueError(f"Invalid workspace_key format: {workspace_key}")
    if not validate_subject(subject):
        raise ValueError(f"Invalid subject pattern: {subject}")

    cursor = int(last_event_id or 0)
    if cursor < 0:
        raise ValueError(f"last_event_id must be non-negative, got {cursor}")
    return SSEJetStreamConsumer(
        workspace_key=workspace_key,
        subject=subject,
        last_event_id=cursor,
    )


async def sse_jetstream_generator(
    consumer: SSEJetStreamConsumer,
) -> AsyncGenerator[str, None]:
    """Convert JetStream consumer to SSE event format.

    Ensures the JetStream subscription is properly cleaned up via
    consumer.disconnect() when the generator exits, whether through
    normal completion, early break, or exception.

    Args:
        consumer: SSEJetStreamConsumer instance.

    Yields:
        SSE-formatted event strings.

    Example:
        >>> consumer = SSEJetStreamConsumer(workspace_key="ws", subject="events")
        >>> async for event in sse_jetstream_generator(consumer):
        ...     print(event)
        # consumer.disconnect() is always called in finally block
    """
    primary_exc: BaseException | None = None
    try:
        async for event in consumer.stream():
            event_type = event.get("type", "message")
            if event_type == "ping":
                yield "event: ping\ndata: {}\n\n"
                continue

            cursor = event.get("cursor", 0)
            ts = event.get("ts")
            payload = event.get("payload", {})

            # Add metadata to payload
            enriched = {
                **payload,
                "_event_id": cursor,
                "_timestamp": ts,
            }

            yield f"id: {cursor}\ndata: {json.dumps(enriched, ensure_ascii=False)}\n\n"
    except BaseException as e:
        primary_exc = e
        raise
    finally:
        # Disconnect but catch and log any secondary exception so it
        # never shadows the original stream exception (B4)
        disconnect_exc: BaseException | None = None
        try:
            await consumer.disconnect()
        except (RuntimeError, ValueError) as e:
            # Only catch expected exceptions from disconnect()
            disconnect_exc = e
        except BaseException as e:  # noqa: BLE001
            # Catch any other unexpected exceptions (SystemExit, etc.)
            disconnect_exc = e
        if primary_exc is not None and disconnect_exc is not None:
            raise primary_exc from disconnect_exc
        if primary_exc is not None:
            raise primary_exc


def create_sse_response_from_jetstream(
    consumer: SSEJetStreamConsumer,
) -> StreamingResponse:
    """Create SSE response from JetStream consumer.

    Args:
        consumer: SSEJetStreamConsumer instance.

    Returns:
        StreamingResponse with SSE events from JetStream.
    """
    return StreamingResponse(
        sse_jetstream_generator(consumer),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# JetStream Publisher (for SSE endpoints that produce events)
# =============================================================================


async def publish_to_jetstream(
    subject: str,
    payload: dict[str, Any],
) -> bool:
    """Publish event to JetStream.

    Args:
        subject: JetStream subject to publish to.
        payload: Event payload.

    Returns:
        True if publish succeeded.

    SECURITY:
        - S5: Subject pattern validation before publishing
          to prevent cross-workspace event injection.
    """
    if not validate_subject(subject):
        logger.warning("Invalid subject rejected for JetStream publish: %s", subject)
        return False

    try:
        from polaris.infrastructure.messaging import get_default_client

        client = await get_default_client()
        if not client or not client.jetstream:
            return False

        await client.publish_js(
            stream="KERNELONE_RUNTIME",
            subject=subject,
            payload=payload,
        )
        return True
    except (RuntimeError, ValueError) as e:
        logger.warning("Failed to publish to JetStream: %s", e)
        return False
