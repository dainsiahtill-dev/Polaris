"""Unit tests for Neural Syndicate NATS broker module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.multi_agent.neural_syndicate.nats_broker import NATSBroker
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentMessage,
    Intent,
    MessageType,
    Performative,
)


class TestNATSBroker:
    """Tests for NATSBroker."""

    @pytest.fixture
    def mock_bus_port(self) -> MagicMock:
        """Create a mock KernelOneMessageBusPort."""
        mock = MagicMock()
        mock.publish.return_value = True
        mock.dead_letters = []
        mock.ensure_nats_connected.return_value = True
        mock.subscribe.return_value = True
        mock.get_stats.return_value = {"nats_connected": True}
        return mock

    @pytest.fixture
    def broker(self, mock_bus_port: MagicMock) -> NATSBroker:
        """Create a NATSBroker with mocked bus port."""
        # Patch at the source module since KernelOneMessageBusPort is imported
        # inside __init__ to avoid circular imports
        with patch(
            "polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort",
            return_value=mock_bus_port,
        ):
            broker = NATSBroker()
            broker._bus_port = mock_bus_port
            return broker

    @pytest.fixture
    def sample_message(self) -> AgentMessage:
        """Create a sample message."""
        return AgentMessage(
            sender="sender-1",
            receiver="receiver-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload={"task": "analyze"},
            message_type=MessageType.TASK,
            trace_id="abcd" * 8,
            span_id="span1234567890ab",
        )

    # ─── Initialization ───────────────────────────────────────────────────────

    def test_init_with_defaults(self) -> None:
        """NATSBroker should initialize with default values."""
        with patch("polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort"):
            broker = NATSBroker()
            assert broker._dead_letter_ttl == 0
            assert broker._messages_published == 0
            assert broker._messages_delivered == 0
            assert broker._dead_letter_count == 0
            assert broker._nats_connected is False

    def test_init_with_custom_params(self) -> None:
        """NATSBroker should accept custom parameters."""
        with patch("polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort"):
            broker = NATSBroker(
                nats_url="nats://custom:4222",
                nats_enabled=True,
                max_queue_size=1024,
                dead_letter_ttl=60,
            )
            assert broker._dead_letter_ttl == 60

    # ─── publish ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_publish_to_direct_receiver(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
        sample_message: AgentMessage,
    ) -> None:
        """publish should deliver to specific receiver."""
        result = await broker.publish(sample_message)
        assert result is True
        assert mock_bus_port.publish.called

    @pytest.mark.asyncio
    async def test_publish_to_broadcast(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """publish with broadcast should call broadcast method."""
        broadcast_msg = AgentMessage(
            sender="sender",
            receiver="",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            message_type=MessageType.EVENT,
        )
        with patch.object(broker, "broadcast", return_value=2) as mock_broadcast:
            result = await broker.publish(broadcast_msg)
            mock_broadcast.assert_called_once_with(broadcast_msg)
            assert result is True

    @pytest.mark.asyncio
    async def test_publish_returns_false_when_no_receivers(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
        sample_message: AgentMessage,
    ) -> None:
        """publish should return False when delivery fails."""
        mock_bus_port.publish.return_value = False
        result = await broker.publish(sample_message)
        assert result is False

    # ─── publish_to_receivers ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_publish_to_receivers_empty_list(
        self,
        broker: NATSBroker,
        sample_message: AgentMessage,
    ) -> None:
        """publish_to_receivers with empty list should return 0."""
        count = await broker.publish_to_receivers(sample_message, ())
        assert count == 0

    @pytest.mark.asyncio
    async def test_publish_to_receivers_multiple(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
        sample_message: AgentMessage,
    ) -> None:
        """publish_to_receivers should deliver to all receivers."""
        receivers = ("worker-1", "worker-2", "worker-3")
        count = await broker.publish_to_receivers(sample_message, receivers)
        assert count == 3
        assert mock_bus_port.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_publish_to_receivers_partial_failure(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
        sample_message: AgentMessage,
    ) -> None:
        """publish_to_receivers should count successful deliveries."""
        receivers = ("worker-1", "worker-2", "worker-3")
        # First two succeed, third fails
        mock_bus_port.publish.side_effect = [True, True, False]
        count = await broker.publish_to_receivers(sample_message, receivers)
        assert count == 2

    @pytest.mark.asyncio
    async def test_publish_increments_stats(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
        sample_message: AgentMessage,
    ) -> None:
        """publish_to_receivers should update statistics."""
        receivers = ("worker-1", "worker-2")
        await broker.publish_to_receivers(sample_message, receivers)
        assert broker._messages_published == 1
        assert broker._messages_delivered == 2

    @pytest.mark.asyncio
    async def test_publish_to_receivers_dead_letter_on_expired(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """Expired message with no successful delivery should trigger dead letter."""
        expired_msg = AgentMessage(
            sender="a",
            receiver="nonexistent",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,  # Expired
        )
        mock_bus_port.publish.return_value = False
        with patch.object(broker, "_handle_dead_letter") as mock_dl:
            await broker.publish_to_receivers(expired_msg, ("nonexistent",))
            mock_dl.assert_called_once()

    # ─── broadcast ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_broadcast_with_no_subscribers(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """broadcast should return 0 when no subscribers."""
        broadcast_msg = AgentMessage(
            sender="sender",
            receiver="",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            message_type=MessageType.EVENT,
        )
        count = await broker.broadcast(broadcast_msg)
        assert count == 0
        assert not mock_bus_port.publish.called

    @pytest.mark.asyncio
    async def test_broadcast_to_subscribers(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """broadcast should deliver to all subscribers."""
        broadcast_msg = AgentMessage(
            sender="sender",
            receiver="",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            message_type=MessageType.EVENT,
        )
        # Add subscribers
        await broker.subscribe("worker-1", MagicMock())
        await broker.subscribe("worker-2", MagicMock())

        with patch.object(broker, "publish_to_receivers", return_value=2) as mock_pub:
            await broker.broadcast(broadcast_msg)
            mock_pub.assert_called_once_with(broadcast_msg, ("worker-1", "worker-2"))

    # ─── subscribe / unsubscribe ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_subscribe_new_agent(
        self,
        broker: NATSBroker,
    ) -> None:
        """subscribe should register callback for agent."""
        callback = MagicMock()
        await broker.subscribe("worker-1", callback)
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1

    @pytest.mark.asyncio
    async def test_subscribe_same_callback_twice(
        self,
        broker: NATSBroker,
    ) -> None:
        """subscribe same callback twice should not duplicate."""
        callback = MagicMock()
        await broker.subscribe("worker-1", callback)
        await broker.subscribe("worker-1", callback)
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_specific_callback(
        self,
        broker: NATSBroker,
    ) -> None:
        """unsubscribe with callback should remove only that callback."""
        callback_a = MagicMock()
        callback_b = MagicMock()
        await broker.subscribe("worker-1", callback_a)
        await broker.subscribe("worker-1", callback_b)
        await broker.unsubscribe("worker-1", callback_a)
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_all_callbacks(
        self,
        broker: NATSBroker,
    ) -> None:
        """unsubscribe with no callback should remove all callbacks."""
        callback = MagicMock()
        await broker.subscribe("worker-1", callback)
        await broker.unsubscribe("worker-1")
        stats = broker.get_stats()
        assert stats["subscriber_count"] == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_agent(
        self,
        broker: NATSBroker,
    ) -> None:
        """unsubscribe for nonexistent agent should not error."""
        await broker.unsubscribe("nonexistent-agent")
        # Should not raise

    # ─── deliver_to_agent ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_deliver_to_agent(
        self,
        broker: NATSBroker,
    ) -> None:
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
    async def test_deliver_to_agent_no_subscribers(
        self,
        broker: NATSBroker,
    ) -> None:
        """deliver_to_agent should not error with no subscribers."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="unknown",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        await broker.deliver_to_agent("unknown", msg)

    @pytest.mark.asyncio
    async def test_deliver_to_agent_callback_exception(
        self,
        broker: NATSBroker,
    ) -> None:
        """Callback exceptions should be caught and logged."""

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

    # ─── NATS-specific methods ────────────────────────────────────────────────

    def test_ensure_nats_connected(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """ensure_nats_connected should delegate to bus_port."""
        mock_bus_port.ensure_nats_connected.return_value = True
        result = broker.ensure_nats_connected()
        assert result is True
        assert broker._nats_connected is True

    def test_disconnect_nats(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """disconnect_nats should set connected to False."""
        broker._nats_connected = True
        broker.disconnect_nats()
        assert broker._nats_connected is False
        mock_bus_port.disconnect_nats.assert_called_once()

    def test_is_nats_connected(
        self,
        broker: NATSBroker,
    ) -> None:
        """is_nats_connected should return current state."""
        broker._nats_connected = True
        assert broker.is_nats_connected is True
        broker._nats_connected = False
        assert broker.is_nats_connected is False

    def test_subscribe_nats_topic(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """subscribe_nats_topic should delegate to bus_port."""
        mock_bus_port.subscribe.return_value = True
        result = broker.subscribe_nats_topic("roles.runtime.*")
        assert result is True
        mock_bus_port.subscribe.assert_called_once_with("roles.runtime.*")

    # ─── Dead letter handling ─────────────────────────────────────────────────

    def test_get_dead_letters(
        self,
        broker: NATSBroker,
        mock_bus_port: MagicMock,
    ) -> None:
        """get_dead_letters should delegate to bus_port."""
        from polaris.cells.roles.runtime.internal.bus_port import DeadLetterRecord

        mock_record = MagicMock(spec=DeadLetterRecord)
        mock_bus_port.dead_letters = [mock_record]
        result = broker.get_dead_letters()
        assert result == [mock_record]

    def test_handle_dead_letter(
        self,
        broker: NATSBroker,
    ) -> None:
        """_handle_dead_letter should increment counter."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            trace_id="abcd" * 8,
            span_id="span1234567890ab",
        )
        initial_count = broker._dead_letter_count
        broker._handle_dead_letter(msg, "ttl_exceeded")
        assert broker._dead_letter_count == initial_count + 1

    # ─── get_stats ────────────────────────────────────────────────────────────

    def test_get_stats(
        self,
        broker: NATSBroker,
    ) -> None:
        """get_stats should return broker statistics."""
        stats = broker.get_stats()
        assert "messages_published" in stats
        assert "messages_delivered" in stats
        assert "dead_letter_count" in stats
        assert "subscriber_count" in stats
        assert "nats_connected" in stats
        assert stats["nats_connected"] is False

    # ─── MessageEnvelope conversion ───────────────────────────────────────────

    def test_message_to_envelope(
        self,
        broker: NATSBroker,
        sample_message: AgentMessage,
    ) -> None:
        """_message_to_envelope should convert AgentMessage to AgentEnvelope."""
        envelope = broker._message_to_envelope(sample_message)
        assert envelope.sender == sample_message.sender
        assert envelope.receiver == sample_message.receiver
        assert envelope.message_id == sample_message.message_id
        assert envelope.msg_type == sample_message.message_type.value
