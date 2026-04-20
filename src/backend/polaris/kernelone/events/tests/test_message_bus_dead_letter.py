"""T8: Dead letter queue tests for message_bus.

Verifies that:
1. Messages dropped due to full queues are recorded as dead letters.
2. Dead letters can be retrieved with filtering and limits.
3. Dead letter count is tracked correctly.
4. Clear dead letters works properly.
5. Backward compatibility with dropped_messages property.
"""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_dead_letter_on_queue_full() -> None:
    """When recipient queue is full, message should be recorded as dead letter."""
    from polaris.kernelone.events.message_bus import (
        DeadLetterMessage,
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()
    queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
    bus._actor_queues["test_recipient"] = queue

    # Fill the queue
    await queue.put(
        Message(
            type=MessageType.TASK_SUBMITTED,
            sender="test_sender",
            recipient="test_recipient",
        )
    )

    # Attempting to publish another message should trigger dead letter
    msg = Message(
        type=MessageType.TASK_SUBMITTED,
        sender="test_sender",
        recipient="test_recipient",
    )
    await bus.publish(msg)

    # Verify dead letter was recorded
    assert bus.dropped_messages_count == 1, "dropped_messages_count should be 1"
    assert len(bus._dead_letters) == 1, "dead_letters deque should contain 1 message"
    assert bus.dead_letter_count == 1, "dead_letter_count property should be 1"

    # Verify dead letter content
    dead_letter = bus._dead_letters[0]
    assert isinstance(dead_letter, DeadLetterMessage)
    assert dead_letter.message == msg
    assert dead_letter.reason == "queue_full"
    assert dead_letter.metadata.get("queue_size") == 1
    assert dead_letter.metadata.get("queue_qsize") == 1


@pytest.mark.asyncio
async def test_dead_letter_retrieval_with_limit() -> None:
    """Dead letters can be retrieved with a limit."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus(max_dead_letters=100)

    # Create multiple dead letters
    for i in range(20):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    # Verify all were recorded
    assert bus.dropped_messages_count == 20

    # Retrieve with limit
    letters = await bus.get_dead_letters(limit=10)
    assert len(letters) == 10, "Should return at most 10 dead letters"
    # Newest first
    assert letters[0].message.recipient == "recipient_19"


@pytest.mark.asyncio
async def test_dead_letter_retrieval_with_reason_filter() -> None:
    """Dead letters can be filtered by reason."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Create dead letters with different reasons (simulated)
    for i in range(5):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    # All should have reason "queue_full"
    filtered = await bus.get_dead_letters(reason_filter="queue_full")
    assert len(filtered) == 5

    # Non-matching filter should return empty
    empty_filtered = await bus.get_dead_letters(reason_filter="nonexistent")
    assert len(empty_filtered) == 0


@pytest.mark.asyncio
async def test_dead_letter_retrieval_combined_filter_and_limit() -> None:
    """Dead letters can be filtered by reason and limited simultaneously."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Create 10 dead letters
    for i in range(10):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    # Filter and limit
    letters = await bus.get_dead_letters(limit=5, reason_filter="queue_full")
    assert len(letters) == 5


@pytest.mark.asyncio
async def test_backward_compatibility_dropped_messages_property() -> None:
    """The dropped_messages property still works for backward compatibility."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Create some dead letters
    for i in range(3):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    # dropped_messages property should still work
    assert bus.dropped_messages == 3
    assert bus.dropped_messages_count == 3


@pytest.mark.asyncio
async def test_clear_dead_letters() -> None:
    """clear_dead_letters should remove all dead letters and return count."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Create dead letters
    for i in range(5):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    assert bus.dead_letter_count == 5

    # Clear and verify
    cleared_count = bus.clear_dead_letters()
    assert cleared_count == 5
    assert bus.dead_letter_count == 0
    assert len(await bus.get_dead_letters()) == 0

    # dropped_messages_count should still reflect total (not reset by clear)
    assert bus.dropped_messages_count == 5


@pytest.mark.asyncio
async def test_dead_letter_max_size_limit() -> None:
    """Dead letter deque should respect max_dead_letters limit."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    max_dead_letters = 5
    bus = MessageBus(max_dead_letters=max_dead_letters)

    # Create more dead letters than the limit
    for i in range(10):
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
        bus._actor_queues[f"recipient_{i}"] = queue
        await queue.put(
            Message(
                type=MessageType.TASK_SUBMITTED,
                sender="sender",
                recipient=f"recipient_{i}",
            )
        )
        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient=f"recipient_{i}",
        )
        await bus.publish(msg)

    # Queue should be bounded
    assert bus.dead_letter_count == max_dead_letters
    # But counter should still track total
    assert bus.dropped_messages_count == 10


@pytest.mark.asyncio
async def test_dead_letter_timestamp() -> None:
    """Dead letter should record timestamp when created."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()
    before_time = time.time()

    queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)  # type: ignore[assignment]
    bus._actor_queues["recipient"] = queue
    await queue.put(
        Message(
            type=MessageType.TASK_SUBMITTED,
            sender="sender",
            recipient="recipient",
        )
    )
    msg = Message(
        type=MessageType.TASK_SUBMITTED,
        sender="sender",
        recipient="recipient",
    )
    await bus.publish(msg)

    after_time = time.time()

    letters = await bus.get_dead_letters()
    assert len(letters) == 1
    assert before_time <= letters[0].timestamp <= after_time


@pytest.mark.asyncio
async def test_dead_letter_to_dict() -> None:
    """DeadLetterMessage.to_dict() should serialize correctly."""
    from polaris.kernelone.events.message_bus import (
        DeadLetterMessage,
        Message,
        MessageType,
    )

    msg = Message(
        type=MessageType.TASK_SUBMITTED,
        sender="test_sender",
        recipient="test_recipient",
        payload={"key": "value"},
    )
    dead_letter = DeadLetterMessage(
        message=msg,
        reason="test_reason",
        metadata={"extra": "data"},
    )

    d = dead_letter.to_dict()

    assert d["reason"] == "test_reason"
    assert d["metadata"] == {"extra": "data"}
    assert d["message"]["sender"] == "test_sender"
    assert d["message"]["recipient"] == "test_recipient"


@pytest.mark.asyncio
async def test_broadcast_no_dead_letter() -> None:
    """Broadcast messages (no recipient) should not create dead letters."""
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Broadcast without any subscriber
    msg = Message(
        type=MessageType.TASK_SUBMITTED,
        sender="test_sender",
        recipient=None,  # Broadcast
    )
    await bus.publish(msg)

    # Should not create dead letters for broadcast
    assert bus.dropped_messages_count == 0
    assert bus.dead_letter_count == 0


@pytest.mark.asyncio
async def test_message_to_unknown_recipient_no_dead_letter() -> None:
    """Messages to unregistered recipients should NOT create dead letters.

    This is intentional: sending to a non-existent recipient is not an error,
    it's just a no-op (pub/sub semantics). Dead letters are only for when
    a queue EXISTS but is FULL.
    """
    from polaris.kernelone.events.message_bus import (
        Message,
        MessageBus,
        MessageType,
    )

    bus = MessageBus()

    # Send to non-existent recipient
    msg = Message(
        type=MessageType.TASK_SUBMITTED,
        sender="test_sender",
        recipient="nonexistent_recipient",
    )
    await bus.publish(msg)

    # Should NOT create dead letters - unknown recipient is valid scenario
    assert bus.dropped_messages_count == 0
    assert bus.dead_letter_count == 0
