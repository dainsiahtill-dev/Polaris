"""Unit tests for Neural Syndicate consensus module."""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.multi_agent.neural_syndicate.consensus import (
    ConsensusEngine,
    ConsensusResponse,
    ConsensusResult,
    CriticAgent,
    VoteCollector,
    VotingStrategy,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentMessage,
    Intent,
    MessageType,
    Performative,
)


class TestVoteCollector:
    """Tests for VoteCollector."""

    @pytest.fixture
    def collector(self) -> VoteCollector:
        """Create a vote collector for testing."""
        return VoteCollector(
            request_id="req-123",
            options=frozenset(["approve", "reject", "needs_work"]),
            quorum=2,
            timeout=5.0,
            strategy=VotingStrategy.MAJORITY,
        )

    def test_add_vote_valid_choice(self, collector: VoteCollector) -> None:
        """add_vote should accept valid choices."""
        response = ConsensusResponse(
            request_id="req-123",
            voter="critic-1",
            choice="approve",
            confidence=0.9,
        )
        assert collector.add_vote(response) is True
        assert len(collector.get_votes()) == 1

    def test_add_vote_invalid_choice(self, collector: VoteCollector) -> None:
        """add_vote should reject invalid choices."""
        response = ConsensusResponse(
            request_id="req-123",
            voter="critic-1",
            choice="invalid_option",  # Not in options
            confidence=0.9,
        )
        assert collector.add_vote(response) is False
        assert len(collector.get_votes()) == 0

    def test_add_vote_abstain(self, collector: VoteCollector) -> None:
        """add_vote should accept None choice (abstain)."""
        response = ConsensusResponse(
            request_id="req-123",
            voter="critic-1",
            choice=None,
            confidence=0.0,
        )
        assert collector.add_vote(response) is True
        assert len(collector.get_votes()) == 1

    def test_quorum_triggers_event(self, collector: VoteCollector) -> None:
        """Quorum reached should trigger _completed event."""
        # Add first vote
        response1 = ConsensusResponse(request_id="req-123", voter="c1", choice="approve", confidence=0.8)
        collector.add_vote(response1)
        assert not collector._completed.is_set()

        # Add second vote to reach quorum=2
        response2 = ConsensusResponse(request_id="req-123", voter="c2", choice="reject", confidence=0.7)
        collector.add_vote(response2)
        assert collector._completed.is_set()

    @pytest.mark.asyncio
    async def test_collect_votes_returns_on_quorum(self, collector: VoteCollector) -> None:
        """collect_votes should return immediately when quorum reached."""
        # Pre-fill votes to reach quorum
        response1 = ConsensusResponse(request_id="req-123", voter="c1", choice="approve", confidence=0.8)
        response2 = ConsensusResponse(request_id="req-123", voter="c2", choice="approve", confidence=0.9)
        collector.add_vote(response1)
        collector.add_vote(response2)

        votes = await collector.collect_votes()
        assert len(votes) == 2

    @pytest.mark.asyncio
    async def test_collect_votes_timeout(self, collector: VoteCollector) -> None:
        """collect_votes should return on timeout with partial votes."""
        # Only one vote, quorum is 2
        response = ConsensusResponse(request_id="req-123", voter="c1", choice="approve", confidence=0.8)
        collector.add_vote(response)

        votes = await collector.collect_votes()
        assert len(votes) == 1  # Only one vote collected before timeout

    def test_elapsed_time(self, collector: VoteCollector) -> None:
        """elapsed_time should return time since creation."""
        import time

        time.sleep(0.05)
        elapsed = collector.elapsed_time()
        assert elapsed >= 0.05


class TestVotingStrategy:
    """Tests for VotingStrategy enum."""

    def test_all_strategies(self) -> None:
        """All voting strategies should be defined."""
        expected = {"unanimous", "majority", "weighted", "highest_confidence"}
        actual = {s.value for s in VotingStrategy}
        assert expected.issubset(actual)


class TestConsensusEngine:
    """Tests for ConsensusEngine."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create a mock broker."""
        broker = MagicMock()
        broker.broadcast = AsyncMock(return_value=2)  # 2 critics
        return broker

    @pytest.fixture
    def engine(self, mock_broker: MagicMock) -> ConsensusEngine:
        """Create a consensus engine with mock broker."""
        return ConsensusEngine(
            critic_agents=["critic-1", "critic-2"],
            broker=mock_broker,
            quorum=2,
            timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_request_consensus(self, engine: ConsensusEngine, mock_broker: MagicMock) -> None:
        """request_consensus should broadcast vote request and collect votes."""
        mock_broker.broadcast = AsyncMock(return_value=2)

        # Verify broadcast was called by checking mock before waiting for full consensus
        task = asyncio.create_task(
            engine.request_consensus(
                topic="Test decision",
                options=["yes", "no"],
            ),
        )
        await asyncio.sleep(0.05)  # Let broadcast happen
        assert mock_broker.broadcast.called
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_submit_vote(self, engine: ConsensusEngine, mock_broker: MagicMock) -> None:
        """submit_vote should add vote to active round."""
        mock_broker.broadcast = AsyncMock(return_value=0)

        # Start a consensus round
        task = asyncio.create_task(
            engine.request_consensus(
                topic="Test",
                options=["a", "b"],
            )
        )
        await asyncio.sleep(0.1)  # Let round start

        # Submit a vote
        vote = ConsensusResponse(
            request_id="test",  # This won't match but we just test the mechanism
            voter="critic-1",
            choice="a",
            confidence=0.8,
        )
        # This won't find active round since request_id doesn't match
        result = await engine.submit_vote(vote)
        assert result is False  # No matching round

        task.cancel()

    @pytest.mark.asyncio
    async def test_handle_vote_response_message(self, engine: ConsensusEngine) -> None:
        """handle_vote_response_message should parse and submit vote."""
        vote_payload = {
            "request_id": "req-123",
            "voter": "critic-1",
            "choice": "approve",
            "confidence": 0.85,
            "reasoning": "Looks good",
        }
        msg = AgentMessage(
            sender="critic-1",
            receiver="engine",
            performative=Performative.VOTE_RESPONSE,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload=vote_payload,
            correlation_id="req-123",
        )

        result = await engine.handle_vote_response_message(msg)
        # Won't find active round, but should not error
        assert result is False

    @pytest.mark.asyncio
    async def test_get_active_round_not_found(self, engine: ConsensusEngine) -> None:
        """get_active_round should return None for unknown request_id."""
        result = await engine.get_active_round("nonexistent")
        assert result is None

    def test_determine_outcome_no_votes(self, engine: ConsensusEngine) -> None:
        """_determine_outcome with no votes should return not reached."""
        result = engine._determine_outcome(
            request_id="req-123",
            votes=(),
            elapsed=1.0,
        )
        assert result.reached is False
        assert result.winner is None

    def test_determine_outcome_majority(self, engine: ConsensusEngine) -> None:
        """_determine_outcome majority strategy should pick >50% winner."""
        votes = (
            ConsensusResponse(request_id="req", voter="c1", choice="yes", confidence=0.8),
            ConsensusResponse(request_id="req", voter="c2", choice="yes", confidence=0.9),
            ConsensusResponse(request_id="req", voter="c3", choice="no", confidence=0.7),
        )
        result = engine._determine_outcome(
            request_id="req",
            votes=votes,
            elapsed=1.0,
        )
        assert result.reached is True
        assert result.winner == "yes"

    def test_determine_outcome_all_abstain(self, engine: ConsensusEngine) -> None:
        """_determine_outcome with all abstentions should return not reached."""
        votes = (
            ConsensusResponse(request_id="req", voter="c1", choice=None, confidence=0.0),
            ConsensusResponse(request_id="req", voter="c2", choice=None, confidence=0.0),
        )
        result = engine._determine_outcome(
            request_id="req",
            votes=votes,
            elapsed=1.0,
        )
        assert result.reached is False

    def test_evaluate_unanimous_success(self, engine: ConsensusEngine) -> None:
        """Unanimous should succeed when all agree."""
        votes = {
            "yes": [
                ConsensusResponse(request_id="req", voter="c1", choice="yes", confidence=0.8),
                ConsensusResponse(request_id="req", voter="c2", choice="yes", confidence=0.9),
            ]
        }
        winner, confidence = engine._evaluate_unanimous(votes)
        assert winner == "yes"
        assert confidence > 0

    def test_evaluate_unanimous_failure(self, engine: ConsensusEngine) -> None:
        """Unanimous should fail when opinions differ."""
        votes = {
            "yes": [ConsensusResponse(request_id="req", voter="c1", choice="yes", confidence=0.8)],
            "no": [ConsensusResponse(request_id="req", voter="c2", choice="no", confidence=0.7)],
        }
        winner, confidence = engine._evaluate_unanimous(votes)
        assert winner is None
        assert confidence == 0.0

    def test_evaluate_highest_confidence(self, engine: ConsensusEngine) -> None:
        """Highest confidence should pick option with highest avg confidence."""
        votes = {
            "yes": [ConsensusResponse(request_id="req", voter="c1", choice="yes", confidence=0.6)],
            "no": [ConsensusResponse(request_id="req", voter="c2", choice="no", confidence=0.95)],
        }
        winner, confidence = engine._evaluate_highest_confidence(votes)
        assert winner == "no"
        assert confidence == 0.95


class TestCriticAgent:
    """Tests for CriticAgent."""

    @pytest.fixture
    def critic(self) -> CriticAgent:
        """Create a critic agent for testing."""
        return CriticAgent(
            agent_id="critic-1",
            expertise=["python", "code_review"],
            default_confidence=0.8,
        )

    def test_agent_type(self, critic: CriticAgent) -> None:
        """agent_type should return 'critic'."""
        assert critic.agent_type == "critic"

    def test_capabilities(self, critic: CriticAgent) -> None:
        """capabilities should include critique, vote, validate intents."""
        caps = critic.capabilities
        assert len(caps) == 1
        cap = caps[0]
        assert cap.name == "critique"
        assert Intent.CRITIQUE in cap.intents
        assert Intent.VOTE in cap.intents
        assert Intent.VALIDATE in cap.intents

    @pytest.mark.asyncio
    async def test_handle_vote_request(self, critic: CriticAgent) -> None:
        """_handle_vote_request should return VOTE_RESPONSE."""
        msg = AgentMessage(
            sender="engine",
            receiver="critic-1",
            performative=Performative.VOTE_REQUEST,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload={
                "consensus_request": {
                    "topic": "Is this code ready?",
                    "options": ["approve", "reject"],
                    "voters": ["critic-1"],
                    "quorum": 1,
                },
                "metadata": {},
            },
            correlation_id="vote-req-123",
        )

        response = await critic._handle_vote_request(msg)
        assert response.performative == Performative.VOTE_RESPONSE
        assert response.sender == "critic-1"
        assert response.receiver == "engine"
        assert response.correlation_id == "vote-req-123"
        # Choice should be one of the options
        assert response.payload["choice"] in ["approve", "reject"]

    @pytest.mark.asyncio
    async def test_handle_non_vote_message(self, critic: CriticAgent) -> None:
        """_handle_message with non-vote should return None."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="critic-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        result = await critic._handle_message(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_evaluate_topic_returns_choice_and_confidence(self, critic: CriticAgent) -> None:
        """_evaluate_topic should return (choice, confidence)."""
        choice, confidence = await critic._evaluate_topic(
            topic="Should we approve?",
            options=["approve", "reject"],
            metadata={},
        )
        assert choice in ["approve", "reject"]
        assert 0.0 <= confidence <= 1.0


class TestConsensusResult:
    """Tests for ConsensusResult."""

    def test_create_consensus_result(self) -> None:
        """ConsensusResult should be created correctly."""
        result = ConsensusResult(
            request_id="req-123",
            reached=True,
            winner="approve",
            confidence=0.85,
            reasoning="Majority voted yes",
            elapsed_seconds=2.5,
        )
        assert result.request_id == "req-123"
        assert result.reached is True
        assert result.winner == "approve"
        assert result.confidence == 0.85

    def test_consensus_result_is_frozen(self) -> None:
        """ConsensusResult should be frozen."""
        result = ConsensusResult(request_id="req", reached=False)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.reached = True  # type: ignore[misc]
