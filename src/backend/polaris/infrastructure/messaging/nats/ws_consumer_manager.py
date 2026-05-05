"""WebSocket-oriented JetStream consumer manager."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from polaris.infrastructure.messaging.nats.client import get_default_client
from polaris.infrastructure.messaging.nats.nats_types import (
    JetStreamConstants,
    RuntimeEventEnvelope,
)

logger = logging.getLogger(__name__)
_THROTTLED_LOG_STATE: dict[str, float] = {}

# Message queue max size to prevent unbounded memory growth
# Reduced from 1000 to 100 for lower latency and natural backpressure
_MESSAGE_QUEUE_MAX_SIZE = 100


def _normalize_channel_filter(channel: str) -> str:
    """Normalize client-facing channel tokens to envelope channel names."""
    token = str(channel or "").strip()
    if token.startswith("log."):
        return token.split(".", 1)[1]
    return token


def _log_throttled(level: str, key: str, message: str, *args: Any, cooldown_sec: float = 5.0) -> None:
    """Log helper with cooldown to avoid repeated noisy errors."""
    now = time.monotonic()
    last = _THROTTLED_LOG_STATE.get(key, 0.0)
    if now - last < max(0.1, float(cooldown_sec)):
        return
    _THROTTLED_LOG_STATE[key] = now
    log_fn = getattr(logger, level, logger.warning)
    log_fn(message, *args)


class JetStreamConsumerManager:
    """Manage JetStream consumer lifecycle for one WebSocket connection."""

    def __init__(
        self,
        workspace_key: str,
        client_id: str,
        channels: list[str],
        initial_cursor: int = 0,
        tail: int = 200,
    ) -> None:
        self.workspace_key = workspace_key
        self.client_id = client_id
        self.channels = [_normalize_channel_filter(ch) for ch in channels if str(ch or "").strip()]
        self.current_cursor = initial_cursor
        self.tail = tail
        self._durable_name = f"{JetStreamConstants.CONSUMER_DELIVERY_PREFIX}{self.client_id}"
        self._consumer: Any = None
        self._subscription: Any = None
        self._jetstream: Any = None
        self._message_queue: asyncio.Queue[RuntimeEventEnvelope] = asyncio.Queue(maxsize=_MESSAGE_QUEUE_MAX_SIZE)
        self._pending_acks: dict[int, Any] = {}
        self._closed = False
        self._consumer_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._jetstream is not None and not self._closed

    async def connect(self) -> bool:
        """Connect to JetStream and create consumer.

        Returns:
            True if connection succeeded.
        """
        try:
            from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig

            # Get or create NATS client
            nats_client = await get_default_client()
            self._jetstream = nats_client.jetstream

            if not self._jetstream:
                logger.error("JetStream is required but unavailable for runtime.v2")
                return False

            # Ensure runtime stream exists before creating consumers.
            try:
                await self._jetstream.stream_info(JetStreamConstants.STREAM_NAME)
            except (RuntimeError, ValueError):
                await self._jetstream.add_stream(
                    StreamConfig(
                        name=JetStreamConstants.STREAM_NAME,
                        subjects=JetStreamConstants.STREAM_SUBJECTS,
                    )
                )

            # Subscribe a single wildcard subject for workspace; channels are filtered per payload.
            subject = f"hp.runtime.{self.workspace_key}.>"

            # Ensure stale consumer from previous reconnect/subscription cycle is removed.
            try:
                await self._jetstream.delete_consumer(
                    JetStreamConstants.STREAM_NAME,
                    self._durable_name,
                )
            except (RuntimeError, ValueError) as exc:
                logger.debug("NATS cleanup (best-effort): %s", exc)

            consumer_config = ConsumerConfig(
                durable_name=self._durable_name,
                deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=self.current_cursor + 1 if self.current_cursor > 0 else 1,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
                max_deliver=JetStreamConstants.CONSUMER_MAX_DELIVER,
                max_ack_pending=JetStreamConstants.CONSUMER_MAX_ACK_PENDING,
            )

            self._subscription = await self._jetstream.subscribe(
                subject,
                config=consumer_config,
            )

            # Start background consumer task for zero-latency message delivery
            self._consumer_task = asyncio.create_task(self._consume_messages_loop())

            logger.info(
                "JetStream consumer created for %s on %s, starting from cursor %s",
                self.client_id,
                subject,
                self.current_cursor,
            )
            return True

        except (ImportError, RuntimeError, ValueError) as e:
            error_text = str(e or "").strip()
            lowered = error_text.lower()
            if "temporarily unavailable" in lowered or "no servers available" in lowered:
                _log_throttled(
                    "warning",
                    "jetstream_connect_unavailable",
                    "JetStream consumer unavailable: %s",
                    error_text or type(e).__name__,
                )
            else:
                logger.error("Failed to connect JetStream consumer: %s", e)
            return False

    async def disconnect(self) -> None:
        """Disconnect and cleanup consumer."""
        self._closed = True

        # Cancel background consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consumer_task
            self._consumer_task = None

        if self._subscription:
            try:
                await self._subscription.unsubscribe()
            except (RuntimeError, ValueError) as exc:
                logger.debug("NATS cleanup (best-effort): %s", exc)
        if self._jetstream and self._durable_name:
            try:
                await self._jetstream.delete_consumer(JetStreamConstants.STREAM_NAME, self._durable_name)
            except (RuntimeError, ValueError) as exc:
                logger.debug("NATS cleanup (best-effort): %s", exc)
        self._subscription = None
        self._jetstream = None
        self._pending_acks.clear()

    async def _consume_messages_loop(self) -> None:
        """Background task that continuously consumes messages from JetStream into internal queue.

        This eliminates the short-timeout polling pattern (0.1s) and enables
        zero-latency message delivery. Messages are buffered in the internal queue
        and retrieved by next_message() with minimal delay.
        """
        if not self._subscription:
            return

        while not self._closed:
            try:
                # Use longer timeout for background polling to reduce CPU usage
                msg = await asyncio.wait_for(
                    self._subscription.next_msg(),
                    timeout=1.0,  # 1s interval for background polling
                )
                if msg:
                    try:
                        data = json.loads(msg.data.decode("utf-8"))
                        envelope = RuntimeEventEnvelope.from_dict(data)

                        stream_seq = 0
                        try:
                            metadata = getattr(msg, "metadata", None)
                            if metadata and getattr(metadata, "sequence", None):
                                stream_seq = int(getattr(metadata.sequence, "stream", 0) or 0)
                        except (RuntimeError, ValueError):
                            stream_seq = 0

                        if stream_seq > 0:
                            envelope = envelope.with_cursor(stream_seq)

                        cursor = int(envelope.cursor or 0)
                        if cursor > 0:
                            self._pending_acks[cursor] = msg

                        # Channel filtering (same logic as before)
                        if (
                            self.channels
                            and "*" not in self.channels
                            and "all" not in self.channels
                            and envelope.channel not in self.channels
                        ):
                            await msg.ack()
                            self._pending_acks.pop(cursor, None)
                            continue

                        # Put message into queue with backpressure when full
                        try:
                            self._message_queue.put_nowait(envelope)
                        except asyncio.QueueFull:
                            # Queue full - wait for space (natural backpressure)
                            try:
                                await asyncio.wait_for(self._message_queue.put(envelope), timeout=5.0)
                            except asyncio.TimeoutError:
                                # Give up after timeout - message is dropped
                                _log_throttled(
                                    "warning",
                                    "queue_full_timeout",
                                    "JetStream message dropped due to queue full: %s",
                                    self.client_id,
                                )
                                await msg.ack()  # Still ACK to avoid redelivery
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JetStream message: {e}")
                        await msg.ack()
                    except (RuntimeError, ValueError) as e:
                        logger.warning(f"Failed to process JetStream message: {e}")
                        await msg.ack()
            except asyncio.TimeoutError:
                # Normal timeout - continue loop
                continue
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as e:
                if not self._closed:
                    _log_throttled(
                        "warning",
                        "consume_loop_error",
                        "JetStream consume loop error for %s: %s",
                        self.client_id,
                        str(e),
                    )
                break

    async def next_message(self, timeout: float = 0.5) -> RuntimeEventEnvelope | None:
        """Get next message from consumer queue with timeout.

        Uses internal queue populated by background consumer task for near-zero latency.
        """
        if not self._subscription:
            return None

        try:
            # Get from internal queue (populated by background consumer)
            envelope = await asyncio.wait_for(
                self._message_queue.get(),
                timeout=timeout,
            )
            return envelope
        except asyncio.TimeoutError:
            pass
        except (RuntimeError, ValueError) as e:
            logger.warning(f"JetStream message fetch error: {e}")

        return None

    async def ack_cursor(self, cursor: int) -> None:
        """Update cursor position after client ACK."""
        if cursor <= 0:
            return

        ack_targets = [seq for seq in self._pending_acks if seq <= cursor]
        for seq in sorted(ack_targets):
            msg = self._pending_acks.pop(seq, None)
            if msg is None:
                continue
            try:
                await msg.ack()
            except (RuntimeError, ValueError):
                logger.debug("Failed to ack JetStream message seq=%s", seq, exc_info=True)

        self.current_cursor = max(self.current_cursor, cursor)

    async def fetch_historical(self, limit: int = 200) -> list[RuntimeEventEnvelope]:
        """Keep compatibility with previous helper API."""
        del limit
        return []

    def get_current_cursor(self) -> int:
        """Get current cursor position."""
        return self.current_cursor
