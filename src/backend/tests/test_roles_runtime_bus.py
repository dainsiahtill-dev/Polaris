"""Tests for roles.runtime Bus Port (KernelOne Bus migration).

Covers:
- AgentEnvelope creation and field validation
- InMemoryAgentBusPort: publish, poll, ack, nack, pending_count, dead_letters
- Failure path: nack → requeue (within max_attempts), nack → dead_letter
- AgentBusProxy: send/receive/ack/nack/peek/pending_count bridge
- Integration: AgentBusProxy round-trip with AgentMessage types
- IAgentBusPort Protocol structural check (no real Bus dependency)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentBusProxy,
    AgentMessage,
    MessageType,
)
from polaris.cells.roles.runtime.internal.bus_port import (
    AgentEnvelope,
    DeadLetterRecord,
    InMemoryAgentBusPort,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(
    receiver: str = "agent_b",
    sender: str = "agent_a",
    msg_type: str = "task",
    payload: dict | None = None,
    max_attempts: int = 3,
) -> AgentEnvelope:
    return AgentEnvelope.from_fields(
        msg_type=msg_type,
        sender=sender,
        receiver=receiver,
        payload=payload or {"key": "value"},
        max_attempts=max_attempts,
    )


def _agent_message(
    receiver: str = "agent_b",
    sender: str = "agent_a",
    msg_type: MessageType = MessageType.TASK,
) -> AgentMessage:
    return AgentMessage.create(
        msg_type=msg_type,
        sender=sender,
        receiver=receiver,
        payload={"test": True},
    )


# ---------------------------------------------------------------------------
# AgentEnvelope
# ---------------------------------------------------------------------------

class TestAgentEnvelope:
    def test_from_fields_sets_required_fields(self):
        env = _envelope(receiver="rx", sender="tx", msg_type="result")
        assert env.receiver == "rx"
        assert env.sender == "tx"
        assert env.msg_type == "result"
        assert env.message_id  # non-empty
        assert env.timestamp_utc  # non-empty ISO string
        assert env.attempt == 0
        assert env.max_attempts == 3

    def test_from_fields_accepts_explicit_message_id(self):
        mid = str(uuid.uuid4())
        env = AgentEnvelope.from_fields(
            msg_type="event",
            sender="s",
            receiver="r",
            payload={},
            message_id=mid,
        )
        assert env.message_id == mid

    def test_from_fields_empty_payload_defaults_to_dict(self):
        env = AgentEnvelope.from_fields(
            msg_type="heartbeat",
            sender="s",
            receiver="r",
            payload=None,  # type: ignore[arg-type]
        )
        assert env.payload == {}

    def test_from_fields_max_attempts_floor_is_1(self):
        env = AgentEnvelope.from_fields(
            msg_type="task", sender="s", receiver="r", payload={}, max_attempts=0
        )
        assert env.max_attempts == 1


# ---------------------------------------------------------------------------
# InMemoryAgentBusPort — publish / poll
# ---------------------------------------------------------------------------

class TestInMemoryBusPortBasic:
    def test_publish_then_poll(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx")
        assert bus.publish(env) is True
        result = bus.poll("rx")
        assert result is not None
        assert result.message_id == env.message_id

    def test_poll_empty_returns_none(self):
        bus = InMemoryAgentBusPort()
        assert bus.poll("nobody") is None

    def test_publish_increments_pending_count(self):
        bus = InMemoryAgentBusPort()
        assert bus.pending_count("rx") == 0
        bus.publish(_envelope(receiver="rx"))
        assert bus.pending_count("rx") == 1
        bus.publish(_envelope(receiver="rx"))
        assert bus.pending_count("rx") == 2

    def test_poll_decrements_pending_count(self):
        bus = InMemoryAgentBusPort()
        bus.publish(_envelope(receiver="rx"))
        bus.publish(_envelope(receiver="rx"))
        bus.poll("rx")
        assert bus.pending_count("rx") == 1

    def test_publish_respects_max_queue_size(self):
        bus = InMemoryAgentBusPort(max_queue_size=2)
        assert bus.publish(_envelope(receiver="rx")) is True
        assert bus.publish(_envelope(receiver="rx")) is True
        # 3rd publish exceeds max
        assert bus.publish(_envelope(receiver="rx")) is False
        assert bus.pending_count("rx") == 2

    def test_multiple_receivers_are_isolated(self):
        bus = InMemoryAgentBusPort()
        bus.publish(_envelope(receiver="alpha"))
        bus.publish(_envelope(receiver="beta"))
        msg = bus.poll("alpha")
        assert msg is not None
        assert bus.pending_count("alpha") == 0
        assert bus.pending_count("beta") == 1

    def test_fifo_order_preserved(self):
        bus = InMemoryAgentBusPort()
        for i in range(3):
            env = _envelope(receiver="ordered", payload={"seq": i})
            bus.publish(env)
        for i in range(3):
            msg = bus.poll("ordered")
            assert msg is not None
            assert msg.payload["seq"] == i


# ---------------------------------------------------------------------------
# InMemoryAgentBusPort — ack
# ---------------------------------------------------------------------------

class TestInMemoryBusPortAck:
    def test_ack_removes_inflight(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx")
        bus.publish(env)
        polled = bus.poll("rx")
        assert polled is not None
        assert bus.ack(polled.message_id, "rx") is True

    def test_ack_unknown_id_returns_false(self):
        bus = InMemoryAgentBusPort()
        assert bus.ack("no-such-id", "rx") is False

    def test_ack_idempotent(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx")
        bus.publish(env)
        polled = bus.poll("rx")
        assert polled is not None
        assert bus.ack(polled.message_id, "rx") is True
        # Second ack on same id should return False (not in inflight)
        assert bus.ack(polled.message_id, "rx") is False


# ---------------------------------------------------------------------------
# InMemoryAgentBusPort — nack / requeue / dead-letter
# ---------------------------------------------------------------------------

class TestInMemoryBusPortNack:
    def test_nack_requeues_within_max_attempts(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx", max_attempts=3)
        bus.publish(env)

        polled = bus.poll("rx")
        assert polled is not None
        # First attempt; nack with requeue
        assert bus.nack(polled.message_id, "rx", reason="transient", requeue=True) is True
        # Message should be back in inbox
        assert bus.pending_count("rx") == 1

    def test_nack_without_requeue_goes_to_dead_letter(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx", max_attempts=3)
        bus.publish(env)

        polled = bus.poll("rx")
        assert polled is not None
        assert bus.nack(polled.message_id, "rx", reason="permanent", requeue=False) is True
        assert bus.pending_count("rx") == 0
        assert len(bus.dead_letters) == 1
        assert bus.dead_letters[0].reason == "permanent"

    def test_nack_exceeds_max_attempts_goes_to_dead_letter(self):
        bus = InMemoryAgentBusPort()
        # max_attempts=3: poll increments attempt to 1, nack increments to 2 (<3 → requeue);
        # second poll increments to 3, nack increments to 4 (>=3 → dead-letter)
        env = _envelope(receiver="rx", max_attempts=3)
        bus.publish(env)

        # First poll (attempt becomes 1)
        polled1 = bus.poll("rx")
        assert polled1 is not None
        # nack requeue=True: attempt=2 < max_attempts=3, so re-queued
        bus.nack(polled1.message_id, "rx", reason="err1", requeue=True)
        assert bus.pending_count("rx") == 1

        # Second poll (attempt becomes 3)
        polled2 = bus.poll("rx")
        assert polled2 is not None
        # nack requeue=True: attempt would become 4 >= max_attempts=3 → dead-letter
        bus.nack(polled2.message_id, "rx", reason="err2", requeue=True)
        assert bus.pending_count("rx") == 0
        assert len(bus.dead_letters) == 1
        assert bus.dead_letters[0].reason == "err2"

    def test_nack_unknown_id_returns_false(self):
        bus = InMemoryAgentBusPort()
        assert bus.nack("ghost-id", "rx") is False

    def test_dead_letters_snapshot_is_copy(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx")
        bus.publish(env)
        polled = bus.poll("rx")
        assert polled is not None
        bus.nack(polled.message_id, "rx", reason="test", requeue=False)

        snap1 = bus.dead_letters
        snap2 = bus.dead_letters
        assert snap1 is not snap2  # separate list instances


# ---------------------------------------------------------------------------
# AgentBusProxy — bridge
# ---------------------------------------------------------------------------

class TestAgentBusProxy:
    def _make_proxy(self, name: str = "agent_a") -> AgentBusProxy:
        bus = InMemoryAgentBusPort()
        return AgentBusProxy(agent_name=name, bus=bus)

    def test_send_then_receive_round_trip(self):
        proxy_a = AgentBusProxy(agent_name="agent_a", bus=InMemoryAgentBusPort())
        # Create a shared bus so agent_b can receive from agent_a
        shared_bus = InMemoryAgentBusPort()
        proxy_a2 = AgentBusProxy(agent_name="agent_a", bus=shared_bus)
        proxy_b = AgentBusProxy(agent_name="agent_b", bus=shared_bus)

        msg = _agent_message(receiver="agent_b", sender="agent_a", msg_type=MessageType.TASK)
        assert proxy_a2.send(msg) is True

        received = proxy_b.receive(auto_ack=True)
        assert received is not None
        assert received.id == msg.id
        assert received.type == MessageType.TASK
        assert received.sender == "agent_a"

    def test_receive_returns_none_when_empty(self):
        proxy = self._make_proxy("lone_agent")
        assert proxy.receive() is None

    def test_receive_auto_ack_false_leaves_inflight(self):
        shared_bus = InMemoryAgentBusPort()
        proxy_sender = AgentBusProxy("sender", bus=shared_bus)
        proxy_recv = AgentBusProxy("receiver", bus=shared_bus)

        msg = _agent_message(receiver="receiver")
        proxy_sender.send(msg)

        received = proxy_recv.receive(auto_ack=False)
        assert received is not None
        # Inbox empty but inflight has it
        assert proxy_recv.pending_count() == 0
        # Explicit ack
        assert proxy_recv.ack(received.id) is True

    def test_nack_requeues_via_proxy(self):
        shared_bus = InMemoryAgentBusPort()
        proxy_sender = AgentBusProxy("sender", bus=shared_bus)
        proxy_recv = AgentBusProxy("receiver", bus=shared_bus)

        msg = _agent_message(receiver="receiver")
        proxy_sender.send(msg)

        received = proxy_recv.receive(auto_ack=False)
        assert received is not None
        assert proxy_recv.nack(received.id, reason="retry", requeue=True) is True
        assert proxy_recv.pending_count() == 1

    def test_nack_dead_letter_via_proxy(self):
        shared_bus = InMemoryAgentBusPort()
        proxy_sender = AgentBusProxy("sender", bus=shared_bus)
        proxy_recv = AgentBusProxy("receiver", bus=shared_bus)

        msg = _agent_message(receiver="receiver")
        proxy_sender.send(msg)

        received = proxy_recv.receive(auto_ack=False)
        assert received is not None
        assert proxy_recv.nack(received.id, reason="fatal", requeue=False) is True
        assert len(shared_bus.dead_letters) == 1

    def test_peek_returns_messages_without_consuming(self):
        shared_bus = InMemoryAgentBusPort()
        proxy_sender = AgentBusProxy("sender", bus=shared_bus)
        proxy_recv = AgentBusProxy("target", bus=shared_bus)

        for i in range(3):
            msg = _agent_message(receiver="target", msg_type=MessageType.TASK)
            proxy_sender.send(msg)

        peeked = proxy_recv.peek()
        assert len(peeked) == 3
        # Messages should be back in inbox (best-effort order)
        assert proxy_recv.pending_count() == 3

    def test_pending_count_reflects_inbox(self):
        shared_bus = InMemoryAgentBusPort()
        proxy_sender = AgentBusProxy("sender", bus=shared_bus)
        proxy_recv = AgentBusProxy("recv", bus=shared_bus)

        assert proxy_recv.pending_count() == 0
        proxy_sender.send(_agent_message(receiver="recv"))
        assert proxy_recv.pending_count() == 1

    def test_receive_unknown_msg_type_is_nacked_to_dead_letter(self):
        """Unknown msg_type values are dead-lettered, not silently swallowed."""
        shared_bus = InMemoryAgentBusPort()
        # Inject an envelope with unknown type directly
        bad_env = AgentEnvelope.from_fields(
            msg_type="__unknown_type__",
            sender="s",
            receiver="target",
            payload={},
            max_attempts=1,  # will dead-letter immediately
        )
        shared_bus.publish(bad_env)

        proxy_recv = AgentBusProxy("target", bus=shared_bus)
        result = proxy_recv.receive()
        # Should return None (could not construct AgentMessage)
        assert result is None
        # Dead-letter observable
        assert len(shared_bus.dead_letters) == 1
        assert "unknown_msg_type" in shared_bus.dead_letters[0].reason


# ---------------------------------------------------------------------------
# IAgentBusPort Protocol structural check (no coupling to real Bus)
# ---------------------------------------------------------------------------

class TestIAgentBusPortProtocol:
    def test_in_memory_bus_port_satisfies_protocol(self):
        """InMemoryAgentBusPort must satisfy IAgentBusPort structurally."""
        bus = InMemoryAgentBusPort()
        protocol_methods = {"publish", "poll", "ack", "nack", "pending_count", "dead_letters"}
        for method_name in protocol_methods:
            assert hasattr(bus, method_name), f"Missing method: {method_name}"

    def test_mock_can_implement_protocol(self):
        """Mocks implementing the bus port interface should be usable in tests."""
        mock_bus = MagicMock()
        mock_bus.pending_count.return_value = 5
        proxy = AgentBusProxy("agent_a", bus=mock_bus)

        # pending_count proxies through
        assert proxy.pending_count() == 5
        mock_bus.pending_count.assert_called_once_with("agent_a")


# ---------------------------------------------------------------------------
# DeadLetterRecord observability
# ---------------------------------------------------------------------------

class TestDeadLetterRecord:
    def test_dead_letter_has_envelope_and_reason(self):
        bus = InMemoryAgentBusPort()
        env = _envelope(receiver="rx", max_attempts=1)
        bus.publish(env)
        polled = bus.poll("rx")
        assert polled is not None
        bus.nack(polled.message_id, "rx", reason="test_reason", requeue=True)
        # attempt == max_attempts → dead-letter
        records = bus.dead_letters
        assert len(records) == 1
        record = records[0]
        assert isinstance(record, DeadLetterRecord)
        assert record.envelope.message_id == env.message_id
        assert record.reason == "test_reason"
        assert record.failed_at_utc  # non-empty ISO timestamp

    def test_dead_letter_store_bounded(self):
        from polaris.cells.roles.runtime.internal.bus_port import _MAX_DEAD_LETTER
        bus = InMemoryAgentBusPort()
        # Publish and nack more than _MAX_DEAD_LETTER messages
        count = _MAX_DEAD_LETTER + 5
        for _ in range(count):
            env = _envelope(receiver="rx", max_attempts=1)
            bus.publish(env)
            polled = bus.poll("rx")
            bus.nack(polled.message_id, "rx", reason="flood", requeue=True)
        assert len(bus.dead_letters) <= _MAX_DEAD_LETTER
