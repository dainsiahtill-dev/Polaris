"""Unit tests for Neural Syndicate protocol module."""

from __future__ import annotations

import dataclasses

import pydantic_core
import pytest
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    ConsensusRequest,
    ConsensusResponse,
    Intent,
    MessagePriority,
    MessageType,
    Performative,
    RouteDecision,
    RoutingStrategy,
)


class TestPerformative:
    """Tests for Performative enum."""

    def test_all_performatives_defined(self) -> None:
        """All FIPA ACL performatives should be defined."""
        expected = {
            "request",
            "query",
            "subscribe",
            "propose",
            "accept",
            "reject",
            "inform",
            "confirm",
            "disconfirm",
            "not_understood",
            "failure",
            "refuse",
            "propose_match",
            "vote_request",
            "vote_response",
            "consensus_request",
            "consensus_response",
            "cancel",
            "ignore",
        }
        actual = {p.value for p in Performative}
        assert expected.issubset(actual)

    def test_performative_is_string_enum(self) -> None:
        """Performative should be a string enum for serialization."""
        assert isinstance(Performative.REQUEST, str)
        assert Performative.REQUEST == "request"


class TestIntent:
    """Tests for Intent enum."""

    def test_all_intents_defined(self) -> None:
        """All intent types should be defined."""
        expected = {
            "execute_task",
            "code_generation",
            "code_review",
            "search_code",
            "fetch_context",
            "query_knowledge",
            "coordinate",
            "delegate",
            "collaborate",
            "validate",
            "audit",
            "critique",
            "vote",
            "reach_consensus",
            "resolve_conflict",
            "heartbeat",
            "shutdown",
            "status_report",
        }
        actual = {i.value for i in Intent}
        assert expected.issubset(actual)


class TestMessageType:
    """Tests for MessageType enum."""

    def test_message_types(self) -> None:
        """All message types should be defined."""
        expected = {"task", "result", "error", "command", "event", "heartbeat", "consensus", "vote"}
        actual = {m.value for m in MessageType}
        assert expected.issubset(actual)


class TestMessagePriority:
    """Tests for MessagePriority enum."""

    def test_priority_ordering(self) -> None:
        """Priority levels should be ordered correctly."""
        assert MessagePriority.LOW < MessagePriority.NORMAL
        assert MessagePriority.NORMAL < MessagePriority.HIGH
        assert MessagePriority.HIGH < MessagePriority.URGENT
        assert MessagePriority.URGENT < MessagePriority.CRITICAL

    def test_priority_is_int(self) -> None:
        """MessagePriority should be usable as int for ordering."""
        assert MessagePriority.NORMAL == 1


class TestRoutingStrategy:
    """Tests for RoutingStrategy enum."""

    def test_all_strategies(self) -> None:
        """All routing strategies should be defined."""
        expected = {"direct", "broadcast", "topic", "capability_match", "consensus"}
        actual = {s.value for s in RoutingStrategy}
        assert expected.issubset(actual)


class TestAgentCapability:
    """Tests for AgentCapability model."""

    def test_create_capability(self) -> None:
        """AgentCapability should be created correctly."""
        cap = AgentCapability(
            name="code_analysis",
            intents=[Intent.CODE_REVIEW, Intent.SEARCH_CODE],
            description="Expert at analyzing code",
            version="1.0.0",
        )
        assert cap.name == "code_analysis"
        assert Intent.CODE_REVIEW in cap.intents
        assert Intent.SEARCH_CODE in cap.intents
        assert cap.version == "1.0.0"

    def test_supports_intent(self) -> None:
        """supports_intent should return True for supported intents."""
        cap = AgentCapability(
            name="test",
            intents=[Intent.CODE_REVIEW, Intent.VALIDATE],
        )
        assert cap.supports_intent(Intent.CODE_REVIEW) is True
        assert cap.supports_intent(Intent.VALIDATE) is True
        assert cap.supports_intent(Intent.EXECUTE_TASK) is False

    def test_capability_is_frozen(self) -> None:
        """AgentCapability should be frozen (immutable)."""
        cap = AgentCapability(name="test", intents=[Intent.EXECUTE_TASK])
        with pytest.raises(pydantic_core.ValidationError):
            cap.name = "changed"  # type: ignore[union-attr]


class TestAgentMessage:
    """Tests for AgentMessage model."""

    def test_create_minimal_message(self) -> None:
        """Create a minimal AgentMessage with required fields only."""
        msg = AgentMessage(
            sender="agent-1",
            receiver="agent-2",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert msg.sender == "agent-1"
        assert msg.receiver == "agent-2"
        assert msg.performative == Performative.REQUEST
        assert msg.intent == Intent.EXECUTE_TASK
        assert msg.message_id  # Should have auto-generated ID
        assert msg.timestamp_utc  # Should have auto-generated timestamp
        assert msg.ttl == 10  # Default TTL
        assert msg.hop_count == 0  # Default hop count

    def test_create_with_payload(self) -> None:
        """Create AgentMessage with payload."""
        payload = {"task": "analyze", "params": {"depth": 3}}
        msg = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload=payload,
        )
        assert msg.payload == payload

    def test_is_broadcast(self) -> None:
        """is_broadcast should be True when receiver is empty."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="",  # Broadcast
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
        )
        assert msg.is_broadcast is True

        msg_direct = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
        )
        assert msg_direct.is_broadcast is False

    def test_is_expired(self) -> None:
        """is_expired should be True when TTL <= 0."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,
        )
        assert msg.is_expired is True

        msg_fresh = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=5,
        )
        assert msg_fresh.is_expired is False

    def test_remaining_hops(self) -> None:
        """remaining_hops should be TTL - hop_count."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=10,
            hop_count=3,
        )
        assert msg.remaining_hops == 7

    def test_remaining_hops_capped_at_zero(self) -> None:
        """remaining_hops should not go below zero."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=3,
            hop_count=10,
        )
        assert msg.remaining_hops == 0

    def test_with_forward_increments_hop_count(self) -> None:
        """with_forward should increment hop_count and update metadata."""
        original = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=10,
            hop_count=0,
        )

        forwarded = original.with_forward("forwarder-agent")

        assert forwarded.hop_count == 1
        assert forwarded.sender == "orchestrator"
        # Receiver cleared for rebroadcast
        assert forwarded.receiver == ""
        # Original message preserved
        assert forwarded.message_id == original.message_id
        # Forward history added
        assert "forward_history" in forwarded.metadata
        assert len(forwarded.metadata["forward_history"]) == 1
        assert forwarded.metadata["forward_history"][0]["agent"] == "forwarder-agent"

    def test_with_forward_on_expired_raises(self) -> None:
        """with_forward on expired message should raise ValueError."""
        expired = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=0,
        )
        with pytest.raises(ValueError, match="expired"):
            expired.with_forward("next_hop")

    def test_factory_create_request(self) -> None:
        """create_request factory should create REQUEST message."""
        msg = AgentMessage.create_request(
            sender="orchestrator",
            receiver="worker-1",
            intent=Intent.SEARCH_CODE,
            payload={"query": "find_files"},
        )
        assert msg.performative == Performative.REQUEST
        assert msg.intent == Intent.SEARCH_CODE
        assert msg.message_type == MessageType.TASK
        assert msg.payload == {"query": "find_files"}

    def test_factory_create_inform(self) -> None:
        """create_inform factory should create INFORM message."""
        msg = AgentMessage.create_inform(
            sender="worker-1",
            receiver="orchestrator",
            intent=Intent.EXECUTE_TASK,
            payload={"result": {"files": ["a.py", "b.py"]}},
            correlation_id="req-123",
        )
        assert msg.performative == Performative.INFORM
        assert msg.intent == Intent.EXECUTE_TASK
        assert msg.message_type == MessageType.RESULT
        assert msg.correlation_id == "req-123"

    def test_factory_create_vote_request(self) -> None:
        """create_vote_request factory should create VOTE_REQUEST message."""
        msg = AgentMessage.create_vote_request(
            sender="engine",
            topic="approve_code_change",
            payload={"code_hash": "abc123"},
            correlation_id="vote-456",
        )
        assert msg.performative == Performative.VOTE_REQUEST
        assert msg.intent == Intent.VOTE
        assert msg.message_type == MessageType.VOTE
        assert msg.receiver == ""  # Broadcast
        assert msg.payload["topic"] == "approve_code_change"
        assert msg.payload["code_hash"] == "abc123"

    def test_to_envelope_dict(self) -> None:
        """to_envelope_dict should return transport-layer compatible dict."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            ttl=5,
            hop_count=2,
        )
        envelope = msg.to_envelope_dict()

        assert envelope["message_id"] == msg.message_id
        assert envelope["sender"] == "a"
        assert envelope["receiver"] == "b"
        assert envelope["msg_type"] == "task"
        assert envelope["attempt"] == 2
        assert envelope["max_attempts"] == 5

    def test_from_envelope_dict(self) -> None:
        """from_envelope_dict should reconstruct AgentMessage."""
        original = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
            payload={"result": "ok"},
            ttl=8,
            hop_count=1,
        )
        envelope = original.to_envelope_dict()
        reconstructed = AgentMessage.from_envelope_dict(envelope)

        assert reconstructed.sender == original.sender
        assert reconstructed.receiver == original.receiver
        assert reconstructed.performative == original.performative
        assert reconstructed.intent == original.intent
        assert reconstructed.ttl == original.ttl
        assert reconstructed.hop_count == original.hop_count

    def test_message_is_frozen(self) -> None:
        """AgentMessage should be frozen (immutable)."""
        msg = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        with pytest.raises(pydantic_core.ValidationError):
            msg.sender = "c"  # type: ignore[union-attr]


class TestConsensusRequest:
    """Tests for ConsensusRequest model."""

    def test_create_consensus_request(self) -> None:
        """ConsensusRequest should be created correctly."""
        req = ConsensusRequest(
            topic="Should we refactor?",
            options=["yes", "no", "defer"],
            voters=["critic-1", "critic-2", "critic-3"],
            quorum=2,
        )
        assert req.topic == "Should we refactor?"
        assert len(req.options) == 3
        assert req.quorum == 2
        assert req.voters == ["critic-1", "critic-2", "critic-3"]

    def test_consensus_request_is_frozen(self) -> None:
        """ConsensusRequest should be frozen."""
        req = ConsensusRequest(topic="test", options=["a", "b"])
        with pytest.raises(pydantic_core.ValidationError):
            req.topic = "changed"  # type: ignore[union-attr]


class TestConsensusResponse:
    """Tests for ConsensusResponse model."""

    def test_create_consensus_response(self) -> None:
        """ConsensusResponse should be created correctly."""
        resp = ConsensusResponse(
            request_id="req-123",
            voter="critic-1",
            choice="approve",
            confidence=0.85,
            reasoning="Code looks good",
        )
        assert resp.request_id == "req-123"
        assert resp.voter == "critic-1"
        assert resp.choice == "approve"
        assert resp.confidence == 0.85

    def test_consensus_response_with_abstain(self) -> None:
        """ConsensusResponse can have None choice for abstention."""
        resp = ConsensusResponse(
            request_id="req-123",
            voter="critic-1",
            choice=None,
            confidence=0.0,
            reasoning="Not enough information",
        )
        assert resp.choice is None


class TestRouteDecision:
    """Tests for RouteDecision dataclass."""

    def test_create_route_decision(self) -> None:
        """RouteDecision should be created correctly."""
        decision = RouteDecision(
            receivers=("worker-1", "worker-2"),
            strategy=RoutingStrategy.CAPABILITY_MATCH,
            hop_limit=5,
            reason="Capability match for intent",
        )
        assert decision.receivers == ("worker-1", "worker-2")
        assert decision.strategy == RoutingStrategy.CAPABILITY_MATCH
        assert decision.hop_limit == 5
        assert "Capability match" in decision.reason

    def test_route_decision_defaults(self) -> None:
        """RouteDecision should have sensible defaults."""
        decision = RouteDecision()
        assert decision.receivers == ()
        assert decision.strategy == RoutingStrategy.DIRECT
        assert decision.hop_limit == 10
        assert decision.reason == ""

    def test_route_decision_is_frozen(self) -> None:
        """RouteDecision should be frozen."""
        decision = RouteDecision(receivers=("a",))
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.receivers = ("b",)  # type: ignore[misc]
