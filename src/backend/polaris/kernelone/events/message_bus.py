"""Message bus for actor communication.

Implements a lightweight message passing system for the Actor model.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any

from polaris.kernelone.constants import MESSAGE_BUS_MAX_DEAD_LETTERS, MESSAGE_BUS_MAX_HISTORY
from polaris.kernelone.trace import create_task_with_context

logger = logging.getLogger(__name__)

try:
    _ASYNC_HANDLER_TIMEOUT_SECONDS = max(
        0.1,
        float(os.environ.get("KERNELONE_MESSAGE_HANDLER_TIMEOUT_SECONDS", "5.0") or 5.0),
    )
except (ValueError, TypeError):
    _ASYNC_HANDLER_TIMEOUT_SECONDS = 5.0


class MessageType(Enum):
    """Types of messages that can be sent."""

    # Task lifecycle
    TASK_SUBMITTED = auto()
    TASK_CLAIMED = auto()
    TASK_STARTED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    TASK_CANCELLED = auto()

    # Worker lifecycle
    WORKER_SPAWNED = auto()
    WORKER_READY = auto()
    WORKER_BUSY = auto()
    WORKER_STOPPING = auto()
    WORKER_STOPPED = auto()
    WORKER_FAILED = auto()

    # Director commands
    DIRECTOR_START = auto()
    DIRECTOR_STOP = auto()
    DIRECTOR_PAUSE = auto()
    DIRECTOR_RESUME = auto()

    # Planning / QA integration
    PLAN_CREATED = auto()
    FILE_WRITTEN = auto()
    AUDIT_COMPLETED = auto()

    # System events
    NAG_REMINDER = auto()
    BUDGET_EXCEEDED = auto()
    COMPACT_REQUESTED = auto()

    # Task progress
    TASK_PROGRESS = auto()
    TASK_RETRY = auto()

    # Task trace (detailed execution trace)
    TASK_TRACE = auto()

    # Settings changes
    SETTINGS_CHANGED = auto()

    # Unified Event Pipeline v2.0 - generic runtime event
    RUNTIME_EVENT = auto()

    # Sequential engine events (vNext)
    SEQ_START = auto()
    SEQ_STEP = auto()
    SEQ_PROGRESS = auto()
    SEQ_NO_PROGRESS = auto()
    SEQ_TERMINATION = auto()
    SEQ_ERROR = auto()
    SEQ_RESERVED_KEY_VIOLATION = auto()


@dataclass
class Message:
    """A message sent between actors."""

    type: MessageType
    sender: str
    recipient: str | None = None  # None = broadcast
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary."""
        return {
            "id": self.message_id,
            "type": self.type.name,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DeadLetterMessage:
    """Dead letter message for undeliverable messages.

    When a message cannot be delivered due to queue full or other reasons,
    it is recorded in the dead letter queue for later inspection.

    Attributes:
        message: The original message that could not be delivered.
        reason: Description of why delivery failed.
        timestamp: Unix timestamp when the message was recorded.
        metadata: Additional context about the failure.
    """

    message: Message
    reason: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert dead letter to dictionary for inspection."""
        return {
            "message": self.message.to_dict(),
            "reason": self.reason,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


MessageHandler = Callable[[Message], None]
AsyncMessageHandler = Callable[[Message], Any]


class LegacySyncHandlerAdapter:
    """Adapter to wrap synchronous handlers for use with async message bus.

    This adapter allows legacy synchronous handlers to be used with the async
    message bus by wrapping them in an async function that runs in a thread pool
    to avoid blocking the event loop.
    """

    def __init__(self, sync_handler: MessageHandler) -> None:
        self._sync_handler = sync_handler
        self._wrapped: AsyncMessageHandler | None = None

    def _create_async_wrapper(self) -> AsyncMessageHandler:
        """Create an async wrapper for the sync handler."""

        async def wrapper(msg: Message) -> None:
            await asyncio.to_thread(self._sync_handler, msg)

        return wrapper

    def __call__(self, msg: Message) -> Any:
        """Make the adapter callable."""
        if self._wrapped is None:
            self._wrapped = self._create_async_wrapper()
        return self._wrapped(msg)


class MessageBus:
    """Message bus for actor communication.

    Implements pub/sub pattern with optional directed messaging.
    Thread-safe for subscribe/unsubscribe/publish operations.
    Supports dead letter queue for tracking undeliverable messages.
    """

    def __init__(
        self,
        max_history: int = MESSAGE_BUS_MAX_HISTORY,
        max_dead_letters: int = MESSAGE_BUS_MAX_DEAD_LETTERS,
    ) -> None:
        """Initialize the message bus.

        Args:
            max_history: Maximum number of messages to keep in history.
            max_dead_letters: Maximum number of dead letters to retain.
        """
        self._subscribers: dict[MessageType, list[MessageHandler]] = {}
        self._handler_set: dict[MessageType, set[int]] = {}
        self._actor_queues: dict[str, asyncio.Queue[Message]] = {}
        self._history: deque[Message] = deque(maxlen=max_history)
        self._max_history = max_history
        self._dropped_messages: int = 0  # Backward compatibility
        self._dead_letters: deque[DeadLetterMessage] = deque(maxlen=max_dead_letters)
        self._max_dead_letters = max_dead_letters
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return a loop-local lock to avoid cross-loop binding issues."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    @property
    def subscribers(self) -> dict[MessageType, list[MessageHandler]]:
        """Read-only view for diagnostics/tests."""
        return self._subscribers

    @property
    def dropped_messages(self) -> int:
        """Total recipient queue drops observed by this bus (backward compatible)."""
        return self._dropped_messages

    @property
    def dropped_messages_count(self) -> int:
        """Total number of dropped messages."""
        return self._dropped_messages

    def _record_dead_letter(
        self,
        message: Message,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a dead letter for an undeliverable message.

        Args:
            message: The message that could not be delivered.
            reason: The reason why delivery failed.
            metadata: Additional metadata about the failure.
        """
        self._dropped_messages += 1
        dead_letter = DeadLetterMessage(
            message=message,
            reason=reason,
            metadata=metadata or {},
        )
        self._dead_letters.append(dead_letter)

        logger.warning(
            "Message dropped: recipient=%s type=%s reason=%s total_dropped=%d",
            message.recipient,
            message.type.name,
            reason,
            self._dropped_messages,
        )

    async def get_dead_letters(
        self,
        limit: int | None = None,
        reason_filter: str | None = None,
    ) -> list[DeadLetterMessage]:
        """Get dead letter messages.

        Args:
            limit: Maximum number of dead letters to return.
            reason_filter: If provided, only return dead letters containing
                          this substring in the reason.

        Returns:
            List of dead letter messages, newest first.
        """
        async with self._get_lock():
            # Create a snapshot under lock to avoid concurrent modification
            letters = list(self._dead_letters)

        # Filter outside lock (read-only operation on the snapshot)
        if reason_filter:
            letters = [letter for letter in letters if reason_filter in letter.reason]

        # Reverse on the snapshot only (newest first)
        letters.reverse()

        if limit:
            letters = letters[:limit]

        return letters

    @property
    def dead_letter_count(self) -> int:
        """Current number of dead letters stored."""
        return len(self._dead_letters)

    def clear_dead_letters(self) -> int:
        """Clear all dead letters.

        Returns:
            Number of dead letters that were cleared.
        """
        count = len(self._dead_letters)
        self._dead_letters.clear()
        return count

    async def subscribe(
        self,
        message_type: MessageType,
        handler: MessageHandler | AsyncMessageHandler,
    ) -> bool:
        """Subscribe to a message type.

        Args:
            message_type: Type of message to subscribe to
            handler: Handler function to call when message is received (sync or async)

        Returns:
            True if subscribed successfully, False if handler already subscribed
        """
        handler_id = id(handler)
        async with self._get_lock():
            if message_type not in self._subscribers:
                self._subscribers[message_type] = []
                self._handler_set[message_type] = set()

            # Deduplication: check if handler already subscribed
            if handler_id in self._handler_set[message_type]:
                logger.debug(f"Handler {handler_id} already subscribed to {message_type}")
                return False

            self._subscribers[message_type].append(handler)
            self._handler_set[message_type].add(handler_id)
            return True

    async def unsubscribe(
        self,
        message_type: MessageType,
        handler: MessageHandler | AsyncMessageHandler,
    ) -> bool:
        """Unsubscribe from a message type.

        Args:
            message_type: Type of message to unsubscribe from
            handler: Handler function to remove

        Returns:
            True if handler was found and removed, False otherwise
        """
        async with self._get_lock():
            if message_type not in self._subscribers:
                return False

            handler_id = id(handler)

            # Idempotent cleanup: only process if handler was subscribed
            if handler_id not in self._handler_set.get(message_type, set()):
                return False

            self._subscribers[message_type] = [h for h in self._subscribers[message_type] if h != handler]
            self._handler_set[message_type].discard(handler_id)

            # Cleanup empty lists
            if not self._subscribers[message_type]:
                del self._subscribers[message_type]
                del self._handler_set[message_type]

            return True

    def subscriber_count(self, message_type: MessageType | None = None) -> int:
        """Get the number of subscribers.

        Args:
            message_type: If provided, count subscribers for this type only.
                         Otherwise, count total unique subscribers across all types.

        Returns:
            Number of subscribers
        """
        if message_type is not None:
            return len(self._subscribers.get(message_type, []))

        # Count unique handlers across all types
        all_handlers: set[int] = set()
        for handler_set in self._handler_set.values():
            all_handlers.update(handler_set)
        return len(all_handlers)

    async def publish(self, message: Message) -> None:
        """Publish a message to all subscribers.

        Args:
            message: The message to publish.
        """
        direct_queue: asyncio.Queue[Message] | None = None
        async with self._get_lock():
            # Store in history
            self._history.append(message)

            # Direct message to specific recipient
            if message.recipient:
                direct_queue = self._actor_queues.get(message.recipient)
                if direct_queue:
                    try:
                        direct_queue.put_nowait(message)
                    except asyncio.QueueFull:
                        self._record_dead_letter(
                            message,
                            "queue_full",
                            metadata={
                                "queue_size": direct_queue.maxsize,
                                "queue_qsize": direct_queue.qsize(),
                            },
                        )

            # Snapshot distribution: copy handlers list under lock
            handlers = list(self._subscribers.get(message.type, []))

        # Call handlers outside lock to prevent blocking.
        pending_async_handlers: list[Any] = []
        for handler in handlers:
            try:
                result = handler(message)
                if inspect.isawaitable(result):
                    pending_async_handlers.append(result)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Message handler error for %s: %s",
                    message.type.name,
                    e,
                )
                self._record_dead_letter(
                    message,
                    "handler_error",
                    metadata={"handler_type": type(handler).__name__, "error": str(e)},
                )
        if pending_async_handlers:
            handler_tasks = [create_task_with_context(coro) for coro in pending_async_handlers]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*handler_tasks, return_exceptions=True),
                    timeout=_ASYNC_HANDLER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                for t in handler_tasks:
                    t.cancel()
                await asyncio.gather(*handler_tasks, return_exceptions=True)
                logger.warning(
                    "Async message handlers timed out for %s after %.1fs",
                    message.type.name,
                    _ASYNC_HANDLER_TIMEOUT_SECONDS,
                )
                self._record_dead_letter(
                    message,
                    "handler_timeout",
                    metadata={"timeout": _ASYNC_HANDLER_TIMEOUT_SECONDS},
                )
                return
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(
                        "Async message handler error for %s: %s",
                        message.type.name,
                        result,
                    )

    async def send(
        self,
        message_type: MessageType,
        sender: str,
        recipient: str,
        payload: dict | None = None,
    ) -> None:
        """Send a direct message to a specific actor.

        Args:
            message_type: Type of message to send.
            sender: ID of the sender actor.
            recipient: ID of the recipient actor.
            payload: Optional message payload.
        """
        message = Message(
            type=message_type,
            sender=sender,
            recipient=recipient,
            payload=payload or {},
        )
        await self.publish(message)

    async def broadcast(
        self,
        message_type: MessageType,
        sender: str,
        payload: dict | None = None,
    ) -> None:
        """Broadcast a message to all subscribers.

        Args:
            message_type: Type of message to broadcast.
            sender: ID of the sender actor.
            payload: Optional message payload.
        """
        message = Message(
            type=message_type,
            sender=sender,
            recipient=None,
            payload=payload or {},
        )
        await self.publish(message)

    async def register_actor(self, actor_id: str) -> asyncio.Queue[Message]:
        """Register an actor to receive direct messages.

        Args:
            actor_id: Unique identifier for the actor.

        Returns:
            The queue where the actor will receive messages.
        """
        async with self._get_lock():
            queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=100)
            self._actor_queues[actor_id] = queue
            return queue

    async def unregister_actor(self, actor_id: str) -> None:
        """Unregister an actor.

        Args:
            actor_id: ID of the actor to unregister.
        """
        async with self._get_lock():
            self._actor_queues.pop(actor_id, None)

    async def get_messages(
        self,
        actor_id: str,
        timeout: float | None = None,
    ) -> Message | None:
        """Get the next message for an actor.

        Args:
            actor_id: ID of the actor to get messages for.
            timeout: Optional timeout in seconds.

        Returns:
            The next message, or None if timeout or no queue exists.
        """
        queue = self._actor_queues.get(actor_id)
        if not queue:
            return None

        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def get_history(
        self,
        message_type: MessageType | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Get message history.

        Args:
            message_type: If provided, only return messages of this type.
            limit: Maximum number of messages to return.

        Returns:
            List of messages from history, newest first.
        """
        async with self._get_lock():
            messages = list(self._history)[-max(1, int(limit or 1)) :]
            if message_type:
                messages = [m for m in messages if m.type == message_type]
            return messages


class Actor:
    """Base class for actors in the system.

    Actors communicate via message passing only.
    """

    def __init__(self, actor_id: str, message_bus: MessageBus) -> None:
        """Initialize the actor.

        Args:
            actor_id: Unique identifier for this actor.
            message_bus: The message bus instance for communication.
        """
        self.actor_id = actor_id
        self._bus = message_bus
        self._queue: asyncio.Queue[Message] | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the actor."""
        self._queue = await self._bus.register_actor(self.actor_id)
        self._running = True
        self._task = create_task_with_context(self._run())

    async def stop(self) -> None:
        """Stop the actor."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._bus.unregister_actor(self.actor_id)

    async def _run(self) -> None:
        """Main actor loop with graceful shutdown support."""
        while self._running:
            try:
                message = await self._bus.get_messages(self.actor_id, timeout=1.0)
                if message:
                    # Shield the message handling from cancellation during shutdown
                    await asyncio.shield(self.handle_message(message))
            except asyncio.CancelledError:
                # Check if we should continue or exit gracefully
                if not self._running:
                    break
                raise
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Actor {self.actor_id} error: {e}")

        # Graceful shutdown: wait briefly for current message processing to complete
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.sleep(0.1), timeout=0.5)

    async def handle_message(self, message: Message) -> None:
        """Handle incoming message. Override in subclasses.

        Args:
            message: The incoming message to handle.
        """
        pass

    async def send(
        self,
        recipient: str,
        message_type: MessageType,
        payload: dict | None = None,
    ) -> None:
        """Send a message to another actor.

        Args:
            recipient: ID of the recipient actor.
            message_type: Type of message to send.
            payload: Optional message payload.
        """
        await self._bus.send(message_type, self.actor_id, recipient, payload)

    async def broadcast(
        self,
        message_type: MessageType,
        payload: dict | None = None,
    ) -> None:
        """Broadcast a message.

        Args:
            message_type: Type of message to broadcast.
            payload: Optional message payload.
        """
        await self._bus.broadcast(message_type, self.actor_id, payload)
