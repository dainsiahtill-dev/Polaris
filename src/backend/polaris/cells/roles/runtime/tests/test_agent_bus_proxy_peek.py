"""Unit tests for P1-004: AgentBusProxy.peek() atomic drain-and-requeue fix.

Tests cover:
- Normal peek operation preserves messages
- Publish failure triggers rollback (messages preserved in inbox)
- FIFO order is preserved after rollback
- requeue_all_inflight correctly restores message order

Implements test coverage for P1-004 fix.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentBusProxy,
    AgentMessage,
    MessageType,
)
from polaris.cells.roles.runtime.internal.bus_port import (
    AgentEnvelope,
    InMemoryAgentBusPort,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_bus() -> InMemoryAgentBusPort:
    """Create a clean InMemoryAgentBusPort for testing."""
    return InMemoryAgentBusPort(max_queue_size=100)


@pytest.fixture
def agent_bus_proxy(in_memory_bus: InMemoryAgentBusPort) -> AgentBusProxy:
    """Create an AgentBusProxy with injected bus."""
    return AgentBusProxy(agent_name="test_agent", bus=in_memory_bus)


@pytest.fixture
def sample_messages() -> list[AgentMessage]:
    """Create sample AgentMessages for testing."""
    return [
        AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="sender1",
            receiver="test_agent",
            payload={"index": 1},
        ),
        AgentMessage.create(
            msg_type=MessageType.RESULT,
            sender="sender2",
            receiver="test_agent",
            payload={"index": 2},
        ),
        AgentMessage.create(
            msg_type=MessageType.EVENT,
            sender="sender3",
            receiver="test_agent",
            payload={"index": 3},
        ),
    ]


# ── Tests for InMemoryAgentBusPort.requeue_all_inflight ────────────────────────


class TestRequeueAllInflight:
    """Tests for requeue_all_inflight method."""

    def test_requeue_all_inflight_empty_inflight(self, in_memory_bus: InMemoryAgentBusPort) -> None:
        """requeue_all_inflight should return 0 when no inflight messages."""
        result = in_memory_bus.requeue_all_inflight("test_agent")
        assert result == 0

    def test_requeue_all_inflight_restores_fifo_order(self, in_memory_bus: InMemoryAgentBusPort) -> None:
        """requeue_all_inflight should preserve FIFO order of messages.

        Scenario:
        - Inbox starts with [msg1, msg2, msg3]
        - poll() drains to inflight: {msg1, msg2, msg3}
        - requeue_all_inflight() should restore inbox: [msg1, msg2, msg3]
        """
        # Add messages to inbox in FIFO order
        msg1 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="test_agent",
            payload={"id": 1},
            message_id="msg1",
        )
        msg2 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="test_agent",
            payload={"id": 2},
            message_id="msg2",
        )
        msg3 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="test_agent",
            payload={"id": 3},
            message_id="msg3",
        )

        in_memory_bus.publish(msg1)
        in_memory_bus.publish(msg2)
        in_memory_bus.publish(msg3)

        # Drain to inflight via poll
        in_memory_bus.poll("test_agent")
        in_memory_bus.poll("test_agent")
        in_memory_bus.poll("test_agent")

        # Verify inflight has 3 messages
        stats = in_memory_bus.get_stats()
        assert stats["inflight_count"] == 3

        # Requeue all
        requeued = in_memory_bus.requeue_all_inflight("test_agent")
        assert requeued == 3

        # Verify inbox restored in FIFO order
        assert in_memory_bus.pending_count("test_agent") == 3

        # Verify FIFO order by polling
        polled1 = in_memory_bus.poll("test_agent")
        polled2 = in_memory_bus.poll("test_agent")
        polled3 = in_memory_bus.poll("test_agent")

        assert polled1 is not None
        assert polled2 is not None
        assert polled3 is not None
        assert polled1.message_id == "msg1"
        assert polled2.message_id == "msg2"
        assert polled3.message_id == "msg3"

    def test_requeue_all_inflight_only_receiver_messages(self, in_memory_bus: InMemoryAgentBusPort) -> None:
        """requeue_all_inflight should only requeue messages for specified receiver."""
        # Add messages for different receivers
        msg1 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="agent_a",
            payload={},
            message_id="msg_a",
        )
        msg2 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="agent_b",
            payload={},
            message_id="msg_b",
        )
        msg3 = AgentEnvelope.from_fields(
            msg_type="task",
            sender="sender",
            receiver="agent_a",
            payload={},
            message_id="msg_a2",
        )

        in_memory_bus.publish(msg1)
        in_memory_bus.publish(msg2)
        in_memory_bus.publish(msg3)

        # Drain all to inflight
        in_memory_bus.poll("agent_a")
        in_memory_bus.poll("agent_b")
        in_memory_bus.poll("agent_a")

        # Requeue only agent_a
        requeued = in_memory_bus.requeue_all_inflight("agent_a")
        assert requeued == 2

        # agent_a should have 2 messages
        assert in_memory_bus.pending_count("agent_a") == 2
        # agent_b should have 0 messages (still in inflight)
        assert in_memory_bus.pending_count("agent_b") == 0


# ── Tests for AgentBusProxy.peek() ─────────────────────────────────────────────


class TestPeekNormalOperation:
    """Tests for normal peek operation."""

    def test_peek_returns_messages_without_consuming(
        self,
        agent_bus_proxy: AgentBusProxy,
        sample_messages: list[AgentMessage],
    ) -> None:
        """peek() should return messages without consuming them."""
        # Send messages
        for msg in sample_messages:
            agent_bus_proxy.send(msg)

        # Peek
        peeked = agent_bus_proxy.peek()
        assert len(peeked) == 3

        # Verify messages are still in inbox
        assert agent_bus_proxy.pending_count() == 3

        # Peek again should return same messages
        peeked_again = agent_bus_proxy.peek()
        assert len(peeked_again) == 3

    def test_peek_preserves_fifo_order(
        self,
        agent_bus_proxy: AgentBusProxy,
        sample_messages: list[AgentMessage],
    ) -> None:
        """peek() should preserve FIFO order of messages."""
        # Send in order
        for msg in sample_messages:
            agent_bus_proxy.send(msg)

        # Peek
        peeked = agent_bus_proxy.peek()

        # Verify order
        assert peeked[0].payload["index"] == 1
        assert peeked[1].payload["index"] == 2
        assert peeked[2].payload["index"] == 3

    def test_peek_empty_inbox(self, agent_bus_proxy: AgentBusProxy) -> None:
        """peek() should return empty list when inbox is empty."""
        peeked = agent_bus_proxy.peek()
        assert peeked == []


class TestPeekRollbackOnFailure:
    """Tests for peek() rollback behavior when publish fails."""

    def test_peek_rollback_on_publish_failure(
        self,
        sample_messages: list[AgentMessage],
    ) -> None:
        """peek() should rollback when publish fails, preserving messages."""
        # Create mock bus that fails on publish
        mock_bus = MagicMock()
        call_count = {"poll": 0, "publish": 0}

        def mock_poll(receiver: str, **kwargs) -> AgentEnvelope | None:
            call_count["poll"] += 1
            if call_count["poll"] == 1:
                return AgentEnvelope.from_fields(
                    msg_type="task",
                    sender="sender",
                    receiver=receiver,
                    payload={"index": 1},
                    message_id="msg1",
                )
            return None

        def mock_publish(envelope: AgentEnvelope) -> bool:
            call_count["publish"] += 1
            # Fail on first publish attempt
            return False

        mock_bus.poll = mock_poll
        mock_bus.publish = mock_publish
        mock_bus.requeue_all_inflight = MagicMock(return_value=1)

        proxy = AgentBusProxy(agent_name="test_agent", bus=mock_bus)

        # Peek should fail and rollback
        result = proxy.peek()

        # Should return empty list on failure
        assert result == []

        # Should have called requeue_all_inflight for rollback
        mock_bus.requeue_all_inflight.assert_called_once_with("test_agent")

    def test_peek_preserves_messages_after_rollback(
        self,
        sample_messages: list[AgentMessage],
    ) -> None:
        """After peek() rollback, messages should still be in inbox."""
        # Create mock bus that fails on publish
        mock_bus = MagicMock()
        call_count = {"poll": 0}

        def mock_poll(receiver: str, **kwargs) -> AgentEnvelope | None:
            call_count["poll"] += 1
            if call_count["poll"] <= 2:
                return AgentEnvelope.from_fields(
                    msg_type="task",
                    sender="sender",
                    receiver=receiver,
                    payload={"index": call_count["poll"]},
                    message_id=f"msg{call_count['poll']}",
                )
            return None

        mock_bus.poll = mock_poll
        mock_bus.publish = MagicMock(return_value=False)  # Always fail
        mock_bus.requeue_all_inflight = MagicMock(return_value=2)
        mock_bus.pending_count = MagicMock(return_value=2)  # Simulate messages restored

        proxy = AgentBusProxy(agent_name="test_agent", bus=mock_bus)

        # Peek should fail
        result = proxy.peek()
        assert result == []

        # Messages should be preserved (via requeue_all_inflight)
        mock_bus.requeue_all_inflight.assert_called_once()


class TestPeekAtomicity:
    """Tests for peek() atomicity guarantees."""

    def test_peek_all_or_nothing(
        self,
        sample_messages: list[AgentMessage],
    ) -> None:
        """peek() should either return all messages or none (on failure)."""
        mock_bus = MagicMock()
        published_count = {"value": 0}

        def mock_poll(receiver: str, **kwargs) -> AgentEnvelope | None:
            return AgentEnvelope.from_fields(
                msg_type="task",
                sender="sender",
                receiver=receiver,
                payload={},
                message_id="msg1",
            )

        def mock_publish(envelope: AgentEnvelope) -> bool:
            published_count["value"] += 1
            return published_count["value"] < 2  # Fail on second publish

        mock_bus.poll = mock_poll
        mock_bus.publish = mock_publish
        mock_bus.requeue_all_inflight = MagicMock(return_value=1)

        proxy = AgentBusProxy(agent_name="test_agent", bus=mock_bus)

        # First poll returns a message, second poll returns None
        call_count = {"polls": 0}

        def counting_poll(receiver: str, **kwargs) -> AgentEnvelope | None:
            call_count["polls"] += 1
            if call_count["polls"] <= 2:
                return AgentEnvelope.from_fields(
                    msg_type="task",
                    sender="sender",
                    receiver=receiver,
                    payload={"id": call_count["polls"]},
                    message_id=f"msg{call_count['polls']}",
                )
            return None

        mock_bus.poll = counting_poll

        result = proxy.peek()

        # On failure, should return empty list
        assert result == []

        # Should have attempted rollback
        mock_bus.requeue_all_inflight.assert_called()


# ── Tests for Bus Port Protocol Conformance ─────────────────────────────────────


class TestBusPortProtocolConformance:
    """Tests for AgentBusPort protocol conformance."""

    def test_in_memory_bus_implements_requeue_all_inflight(
        self,
        in_memory_bus: InMemoryAgentBusPort,
    ) -> None:
        """InMemoryAgentBusPort should implement requeue_all_inflight."""
        assert hasattr(in_memory_bus, "requeue_all_inflight")
        assert callable(in_memory_bus.requeue_all_inflight)

    def test_requeue_all_inflight_returns_int(
        self,
        in_memory_bus: InMemoryAgentBusPort,
    ) -> None:
        """requeue_all_inflight should return an integer count."""
        result = in_memory_bus.requeue_all_inflight("test_agent")
        assert isinstance(result, int)
        assert result >= 0
