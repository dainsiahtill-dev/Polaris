"""Tests for MessageBus high concurrency scenarios."""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType


@pytest.fixture
def bus() -> MessageBus:
    """Create a MessageBus for testing."""
    return MessageBus(max_history=1000, max_dead_letters=100)


class TestMessageBusConcurrency:
    """Tests for MessageBus high concurrency scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_publish_single_subscriber(self, bus: MessageBus) -> None:
        """Test concurrent publishes to a single subscriber."""
        received: list[Message] = []
        lock = asyncio.Lock()

        async def handler(msg: Message) -> None:
            async with lock:
                received.append(msg)

        await bus.subscribe(MessageType.TASK_COMPLETED, handler)

        async def publish_batch() -> None:
            for i in range(50):
                await bus.publish(Message(type=MessageType.TASK_COMPLETED, sender=f"sender{i}", payload={"index": i}))

        await asyncio.gather(*[publish_batch() for _ in range(5)])

        await asyncio.sleep(0.1)
        assert len(received) == 250

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_multiple_handlers(self, bus: MessageBus) -> None:
        """Test concurrent subscription of multiple handlers."""
        counts = [0, 0, 0]

        async def handler0(msg: Message) -> None:
            counts[0] += 1

        async def handler1(msg: Message) -> None:
            counts[1] += 1

        async def handler2(msg: Message) -> None:
            counts[2] += 1

        await bus.subscribe(MessageType.WORKER_READY, handler0)
        await bus.subscribe(MessageType.WORKER_READY, handler1)
        await bus.subscribe(MessageType.WORKER_READY, handler2)

        for i in range(100):
            await bus.publish(Message(type=MessageType.WORKER_READY, sender=f"sender{i}"))

        await asyncio.sleep(0.1)
        assert counts[0] == 100
        assert counts[1] == 100
        assert counts[2] == 100

    @pytest.mark.asyncio
    async def test_concurrent_broadcast_high_volume(self, bus: MessageBus) -> None:
        """Test high-volume concurrent broadcasts."""
        received_count = 0
        lock = asyncio.Lock()

        async def handler(msg: Message) -> None:
            nonlocal received_count
            async with lock:
                received_count += 1

        await bus.subscribe(MessageType.RUNTIME_EVENT, handler)

        async def broadcaster(n: int) -> None:
            for i in range(n):
                await bus.broadcast(MessageType.RUNTIME_EVENT, sender=f"broadcaster{n}_{i}", payload={"n": n, "i": i})

        await asyncio.gather(*[broadcaster(20) for _ in range(10)])

        await asyncio.sleep(0.2)
        assert received_count == 200

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self, bus: MessageBus) -> None:
        """Test concurrent subscribe and unsubscribe operations."""

        async def handler(msg: Message) -> None:
            pass

        async def subscribe_many() -> None:
            for _ in range(50):
                await bus.subscribe(MessageType.TASK_PROGRESS, handler)

        async def unsubscribe_many() -> None:
            for _ in range(50):
                await bus.unsubscribe(MessageType.TASK_PROGRESS, handler)

        await asyncio.gather(subscribe_many(), unsubscribe_many())

    @pytest.mark.asyncio
    async def test_concurrent_actor_registration(self, bus: MessageBus) -> None:
        """Test concurrent actor registration."""

        async def register_actors(base: int) -> None:
            for i in range(20):
                await bus.register_actor(f"actor{base + i}")

        await asyncio.gather(*[register_actors(i * 20) for i in range(5)])

        assert len(bus._actor_queues) == 100

    @pytest.mark.asyncio
    async def test_concurrent_direct_messages(self, bus: MessageBus) -> None:
        """Test concurrent direct message sending."""
        await bus.register_actor("receiver")
        received: list[Message] = []
        lock = asyncio.Lock()

        async def get_messages() -> None:
            for _ in range(10):
                msg = await bus.get_messages("receiver", timeout=0.1)
                if msg:
                    async with lock:
                        received.append(msg)

        async def send_messages() -> None:
            for i in range(50):
                await bus.send(MessageType.TASK_COMPLETED, sender="sender", recipient="receiver", payload={"index": i})

        await asyncio.gather(
            get_messages(),
            send_messages(),
            get_messages(),
            send_messages(),
        )

    @pytest.mark.asyncio
    async def test_dead_letter_queue_under_concurrent_load(self, bus: MessageBus) -> None:
        """Test dead letter queue behavior under concurrent load."""
        await bus.register_actor("small_queue_actor")
        bus._actor_queues["small_queue_actor"]._maxsize = 5

        async def sender() -> None:
            for i in range(20):
                await bus.send(MessageType.RUNTIME_EVENT, sender=f"sender{i}", recipient="small_queue_actor")

        await asyncio.gather(*[sender() for _ in range(5)])

        await asyncio.sleep(0.1)
        assert bus.dead_letter_count > 0

    @pytest.mark.asyncio
    async def test_history_maintained_under_load(self, bus: MessageBus) -> None:
        """Test message history is maintained correctly under load."""

        async def publisher(n: int) -> None:
            for i in range(n):
                await bus.publish(
                    Message(type=MessageType.TASK_TRACE, sender=f"sender{n}_{i}", payload={"n": n, "i": i})
                )

        await asyncio.gather(*[publisher(100) for _ in range(3)])

        await asyncio.sleep(0.1)
        history = await bus.get_history(MessageType.TASK_TRACE, limit=1000)
        assert len(history) == 300
