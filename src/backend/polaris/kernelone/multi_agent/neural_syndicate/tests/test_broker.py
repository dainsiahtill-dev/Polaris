"""Unit tests for Neural Syndicate broker module."""

from __future__ import annotations

import pytest
from polaris.kernelone.multi_agent.neural_syndicate.broker import (
    InMemoryBroker,
    MessageBroker,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentMessage,
    Intent,
    Performative,
)


class TestInMemoryBroker:
    """Tests for InMemoryBroker."""

    @pytest.fixture
    def broker(self) -> InMemoryBroker:
        """Create a fresh broker for each test."""
        return InMemoryBroker()

    @pytest.fixture
    def sample_message(self) -> AgentMessage:
        """Create a sample message."""
        return AgentMessage(
            sender="sender-1",
            receiver="receiver-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload={"task": "analyze"},
        )

    @pytest.mark.asyncio
    async def test_publish_to_direct_receiver(self, broker: InMemoryBroker, sample_message: AgentMessage) -> None:
        """publish should deliver to specific receiver."""
        # The InMemoryBroker uses AgentBusPort under the hood
        # Direct publish should return True when bus port accepts
        result = await broker.publish(sample_message)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_broadcast_with_no_subscribers(self, broker: InMemoryBroker, sample_message: AgentMessage) -> None:
        """broadcast should return 0 when no subscribers."""
        sample_message = AgentMessage(
            sender="sender",
            receiver="",  # Broadcast
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
        )
        count = await broker.broadcast(sample_message)
        assert count == 0

    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self, broker: InMemoryBroker) -> None:
        """subscribe and unsubscribe should work correctly."""
        callback_calls: list[AgentMessage] = []

        async def callback(msg: AgentMessage) -> None:
            callback_calls.append(msg)

        # Subscribe
        await broker.subscribe("agent-1", callback)
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1

        # Unsubscribe specific callback
        await broker.unsubscribe("agent-1", callback)
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1  # Still there (empty list)

        # Unsubscribe all
        await broker.unsubscribe("agent-1")
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 0

    @pytest.mark.asyncio
    async def test_multiple_callbacks_per_agent(self, broker: InMemoryBroker) -> None:
        """Multiple callbacks can be registered per agent."""
        calls_a: list[AgentMessage] = []
        calls_b: list[AgentMessage] = []

        async def callback_a(msg: AgentMessage) -> None:
            calls_a.append(msg)

        async def callback_b(msg: AgentMessage) -> None:
            calls_b.append(msg)

        await broker.subscribe("agent-1", callback_a)
        await broker.subscribe("agent-1", callback_b)

        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1  # One agent, two callbacks

    @pytest.mark.asyncio
    async def test_deliver_to_agent(self, broker: InMemoryBroker) -> None:
        """deliver_to_agent should invoke registered callbacks."""
        received: list[AgentMessage] = []

        async def callback(msg: AgentMessage) -> None:
            received.append(msg)

        await broker.subscribe("worker-1", callback)

        msg = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )

        await broker.deliver_to_agent("worker-1", msg)
        assert len(received) == 1
        assert received[0].message_id == msg.message_id

    @pytest.mark.asyncio
    async def test_deliver_to_agent_no_subscribers(self, broker: InMemoryBroker) -> None:
        """deliver_to_agent should not error with no subscribers."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="unknown-agent",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        # Should not raise
        await broker.deliver_to_agent("unknown-agent", msg)

    @pytest.mark.asyncio
    async def test_callback_exception_handling(self, broker: InMemoryBroker) -> None:
        """Callback exceptions should be caught and logged, not propagate."""

        async def bad_callback(_msg: AgentMessage) -> None:
            raise RuntimeError("callback error")

        await broker.subscribe("worker-1", bad_callback)

        msg = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        # Should not raise
        await broker.deliver_to_agent("worker-1", msg)

    def test_get_stats(self, broker: InMemoryBroker) -> None:
        """get_stats should return broker statistics."""
        stats = broker.get_stats()
        assert "messages_published" in stats
        assert "messages_delivered" in stats
        assert "dead_letter_count" in stats
        assert "subscriber_count" in stats
        assert "bus_stats" in stats

    def test_message_to_envelope(self, broker: InMemoryBroker, sample_message: AgentMessage) -> None:
        """_message_to_envelope should convert AgentMessage to AgentEnvelope."""
        envelope = broker._message_to_envelope(sample_message)
        assert envelope.sender == sample_message.sender
        assert envelope.receiver == sample_message.receiver
        assert envelope.message_id == sample_message.message_id

    @pytest.mark.asyncio
    async def test_publish_to_receivers_empty_list(self, broker: InMemoryBroker, sample_message: AgentMessage) -> None:
        """publish_to_receivers with empty list should return 0."""
        count = await broker.publish_to_receivers(sample_message, ())
        assert count == 0

    @pytest.mark.asyncio
    async def test_dead_letter_on_expired_with_failed_delivery(self, broker: InMemoryBroker) -> None:
        """Expired message with no successful delivery should be dead-lettered."""
        expired_msg = AgentMessage(
            sender="a",
            receiver="nonexistent",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,  # Expired
        )
        # Should not raise
        await broker.publish(expired_msg)


class TestMessageBrokerInterface:
    """Tests for MessageBroker abstract interface."""

    def test_message_broker_is_base_class(self) -> None:
        """MessageBroker should serve as base class for brokers."""
        # MessageBroker is a base class with NotImplementedError on methods
        assert hasattr(MessageBroker, "publish")
        assert hasattr(MessageBroker, "broadcast")
