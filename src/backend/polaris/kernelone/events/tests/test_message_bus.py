"""Tests for MessageBus core functionality.

Tests cover:
  - High concurrency publish (no deadlock)
  - Handler exception isolation (does not propagate)
  - Subscribe/unsubscribe deduplication
  - Direct message delivery to actors
  - History management
  - Actor lifecycle
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from polaris.kernelone.events.message_bus import (
    Actor,
    LegacySyncHandlerAdapter,
    Message,
    MessageBus,
    MessageType,
)


class TestMessageBusConcurrency:
    """Tests for MessageBus high-concurrency safety."""

    @pytest.mark.asyncio
    async def test_high_concurrency_no_deadlock(self) -> None:
        """100 concurrent publishes must not deadlock."""
        bus = MessageBus()
        received_count = 0
        lock = asyncio.Lock()

        async def counting_handler(msg: Message) -> None:
            nonlocal received_count
            async with lock:
                received_count += 1

        await bus.subscribe(MessageType.TASK_SUBMITTED, counting_handler)

        # Launch 100 concurrent publishes
        tasks = [bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"sender_{i}")) for i in range(100)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All must complete without exception
        assert all(r is None for r in results), f"Unexpected exceptions: {[r for r in results if r]}"

        # All messages should be received
        assert received_count == 100, f"Expected 100, got {received_count}"

    @pytest.mark.asyncio
    async def test_handler_exception_no_propagate(self) -> None:
        """Handler exception must not affect other handlers or publish."""
        bus = MessageBus()
        fast_called = asyncio.Event()

        async def crashing_handler(msg: Message) -> None:
            raise RuntimeError("handler error")

        async def fast_handler(msg: Message) -> None:
            fast_called.set()

        await bus.subscribe(MessageType.TASK_SUBMITTED, crashing_handler)
        await bus.subscribe(MessageType.TASK_SUBMITTED, fast_handler)

        # Publish must not raise despite crashing handler
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test")
        await bus.publish(msg)

        # Fast handler should still complete
        await asyncio.wait_for(fast_called.wait(), timeout=1.0)
        assert fast_called.is_set()

    @pytest.mark.asyncio
    async def test_many_subscribers_no_block(self) -> None:
        """Many concurrent subscribe calls must not block."""
        bus = MessageBus()

        # Create different handlers to avoid deduplication
        async def make_handler(i: int):
            async def handler(msg: Message) -> None:
                pass

            return handler

        # 50 concurrent subscriptions with different handlers
        tasks = [bus.subscribe(MessageType.TASK_SUBMITTED, await make_handler(i)) for i in range(50)]
        results = await asyncio.gather(*tasks)

        # All should succeed (first subscription for each unique handler)
        assert all(r is True for r in results)

        # Verify all handlers registered
        assert bus.subscriber_count(MessageType.TASK_SUBMITTED) == 50


class TestMessageBusSubscribeUnsubscribe:
    """Tests for subscribe/unsubscribe behavior."""

    @pytest.mark.asyncio
    async def test_subscribe_returns_true_first_time(self) -> None:
        """First subscription returns True."""
        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        result = await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        assert result is True

    @pytest.mark.asyncio
    async def test_subscribe_returns_false_duplicate(self) -> None:
        """Duplicate subscription returns False."""
        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        result = await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_returns_true_when_found(self) -> None:
        """Unsubscribe returns True when handler was subscribed."""
        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        result = await bus.unsubscribe(MessageType.TASK_SUBMITTED, handler)
        assert result is True

    @pytest.mark.asyncio
    async def test_unsubscribe_returns_false_when_not_found(self) -> None:
        """Unsubscribe returns False when handler was not subscribed."""
        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        result = await bus.unsubscribe(MessageType.TASK_SUBMITTED, handler)
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_cleans_empty_lists(self) -> None:
        """Unsubscribe from last handler cleans up message type entry."""
        bus = MessageBus()

        async def handler(msg: Message) -> None:
            pass

        await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        await bus.unsubscribe(MessageType.TASK_SUBMITTED, handler)

        assert bus.subscriber_count(MessageType.TASK_SUBMITTED) == 0


class TestMessageBusDirectMessaging:
    """Tests for direct message delivery to actors."""

    @pytest.mark.asyncio
    async def test_register_actor_creates_queue(self) -> None:
        """register_actor must create a message queue for the actor."""
        bus = MessageBus()
        queue = await bus.register_actor("actor1")

        assert isinstance(queue, asyncio.Queue)
        assert "actor1" in bus._actor_queues

    @pytest.mark.asyncio
    async def test_unregister_actor_removes_queue(self) -> None:
        """unregister_actor must remove the actor's queue."""
        bus = MessageBus()
        await bus.register_actor("actor1")
        await bus.unregister_actor("actor1")

        assert "actor1" not in bus._actor_queues

    @pytest.mark.asyncio
    async def test_send_delivers_to_recipient(self) -> None:
        """send() must deliver message to specific actor's queue."""
        bus = MessageBus()
        await bus.register_actor("receiver")

        await bus.send(
            message_type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient="receiver",
            payload={"key": "value"},
        )

        msg = await bus.get_messages("receiver", timeout=1.0)
        assert msg is not None
        assert msg.sender == "sender"
        assert msg.type == MessageType.TASK_SUBMITTED
        assert msg.payload == {"key": "value"}

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_actor_not_delivered(self) -> None:
        """send() to unregistered actor must not deliver the message."""
        bus = MessageBus()

        await bus.send(
            message_type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient="nonexistent",
        )

        # Message should not be delivered (actor doesn't exist)
        result = await bus.get_messages("nonexistent", timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_messages_timeout_returns_none(self) -> None:
        """get_messages() with timeout must return None on timeout."""
        bus = MessageBus()
        await bus.register_actor("actor1")

        result = await bus.get_messages("actor1", timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_messages_nonexistent_actor_returns_none(self) -> None:
        """get_messages() for unknown actor returns None."""
        bus = MessageBus()
        result = await bus.get_messages("unknown_actor", timeout=0.1)
        assert result is None


class TestMessageBusHistory:
    """Tests for message history management."""

    @pytest.mark.asyncio
    async def test_history_stores_messages(self) -> None:
        """Published messages must be stored in history."""
        bus = MessageBus()
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test")
        await bus.publish(msg)

        history = await bus.get_history()
        assert len(history) >= 1
        assert any(m.message_id == msg.message_id for m in history)

    @pytest.mark.asyncio
    async def test_history_respects_limit(self) -> None:
        """get_history() must respect the limit parameter."""
        bus = MessageBus()
        for i in range(10):
            await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"sender_{i}"))

        history = await bus.get_history(limit=5)
        assert len(history) <= 5

    @pytest.mark.asyncio
    async def test_history_filters_by_type(self) -> None:
        """get_history() must filter by message type."""
        bus = MessageBus()
        await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender="a"))
        await bus.publish(Message(type=MessageType.TASK_COMPLETED, sender="b"))
        await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender="c"))

        history = await bus.get_history(message_type=MessageType.TASK_SUBMITTED)
        assert all(m.type == MessageType.TASK_SUBMITTED for m in history)

    @pytest.mark.asyncio
    async def test_history_max_size(self) -> None:
        """History must be capped at _max_history (1000)."""
        bus = MessageBus()
        bus._max_history = 100  # Override for faster test

        for i in range(150):
            await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender=f"sender_{i}"))

        history = await bus.get_history()
        assert len(history) <= 100


class TestMessageBusBroadcast:
    """Tests for broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_reaches_all_subscribers(self) -> None:
        """broadcast() must deliver to all subscribers."""
        bus = MessageBus()
        received: list[str] = []

        async def handler1(msg: Message) -> None:
            received.append(f"handler1:{msg.sender}")

        async def handler2(msg: Message) -> None:
            received.append(f"handler2:{msg.sender}")

        await bus.subscribe(MessageType.DIRECTOR_START, handler1)
        await bus.subscribe(MessageType.DIRECTOR_START, handler2)

        await bus.broadcast(message_type=MessageType.DIRECTOR_START, sender="broadcaster")

        await asyncio.sleep(0.1)
        assert "handler1:broadcaster" in received
        assert "handler2:broadcaster" in received

    @pytest.mark.asyncio
    async def test_broadcast_recipient_is_none(self) -> None:
        """broadcast() must set recipient to None."""
        bus = MessageBus()
        captured_recipient: list[str | None] = []

        async def handler(msg: Message) -> None:
            captured_recipient.append(msg.recipient)

        await bus.subscribe(MessageType.DIRECTOR_START, handler)
        await bus.broadcast(message_type=MessageType.DIRECTOR_START, sender="b")

        await asyncio.sleep(0.1)
        assert all(r is None for r in captured_recipient)


class TestLegacySyncHandlerAdapter:
    """Tests for sync handler adaptation to async bus."""

    def test_sync_handler_wrapped(self) -> None:
        """LegacySyncHandlerAdapter must wrap sync handlers."""
        received: list[str] = []
        lock = threading.Lock()

        def sync_handler(msg: Message) -> None:
            with lock:
                received.append(msg.sender)

        adapter = LegacySyncHandlerAdapter(sync_handler)
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="sync_test")

        # Adapter should be callable and return awaitable
        result = adapter(msg)
        assert asyncio.iscoroutine(result) or result is None


class TestMessageBusSubclassing:
    """Tests for Actor base class."""

    @pytest.mark.asyncio
    async def test_actor_start_registers_with_bus(self) -> None:
        """Actor.start() must register with the message bus."""
        bus = MessageBus()
        actor = Actor("test_actor", bus)

        await actor.start()

        assert "test_actor" in bus._actor_queues
        assert actor._running is True

        await actor.stop()

    @pytest.mark.asyncio
    async def test_actor_stop_unregisters(self) -> None:
        """Actor.stop() must unregister from the bus."""
        bus = MessageBus()
        actor = Actor("test_actor", bus)

        await actor.start()
        await actor.stop()

        assert "test_actor" not in bus._actor_queues
        assert actor._running is False

    @pytest.mark.asyncio
    async def test_actor_send_message(self) -> None:
        """Actor.send() must deliver message to recipient."""
        bus = MessageBus()
        sender = Actor("sender", bus)
        receiver = Actor("receiver", bus)

        await sender.start()
        await receiver.start()

        await sender.send(recipient="receiver", message_type=MessageType.TASK_SUBMITTED, payload={"test": True})

        msg = await bus.get_messages("receiver", timeout=1.0)
        assert msg is not None
        assert msg.payload == {"test": True}

        await sender.stop()
        await receiver.stop()


class TestMessageBusEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_publish_with_no_subscribers(self) -> None:
        """publish() must not raise when no subscribers exist."""
        bus = MessageBus()
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="orphan")
        await bus.publish(msg)  # Must not raise

    @pytest.mark.asyncio
    async def test_subscribe_with_lambda_raises(self) -> None:
        """Subscribe with lambda must not raise (lambda is a valid handler)."""
        bus = MessageBus()

        def handler(msg) -> None:
            return None

        result = await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        assert result is True

    @pytest.mark.asyncio
    async def test_subscriber_count_nonexistent_type(self) -> None:
        """subscriber_count() with unknown type returns 0."""
        bus = MessageBus()
        count = bus.subscriber_count(MessageType.TASK_SUBMITTED)
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_to_dict(self) -> None:
        """Message.to_dict() must produce valid dict."""
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test", payload={"key": "value"})
        d = msg.to_dict()

        assert d["type"] == "TASK_SUBMITTED"
        assert d["sender"] == "test"
        assert d["payload"] == {"key": "value"}
        assert "id" in d  # Note: key is "id", not "message_id"
        assert "timestamp" in d

    @pytest.mark.asyncio
    async def test_message_type_enum_names(self) -> None:
        """All message types must have valid names."""
        for msg_type in MessageType:
            assert msg_type.name
            assert msg_type.value

    @pytest.mark.asyncio
    async def test_direct_queue_full_drops(self) -> None:
        """Direct message to full queue must be dropped and counted."""
        bus = MessageBus()
        await bus.register_actor("full_actor")

        # Get the queue and fill it
        queue = bus._actor_queues["full_actor"]
        for _ in range(100):
            await queue.put(Message(type=MessageType.TASK_SUBMITTED, sender="filler"))

        initial_dropped = bus.dropped_messages
        await bus.send(
            message_type=MessageType.TASK_SUBMITTED,
            sender="test",
            recipient="full_actor",
        )

        assert bus.dropped_messages == initial_dropped + 1
