"""Neural Syndicate Consensus Engine - Multi-Agent Voting & Self-Correction.

This module implements the consensus and self-correction mechanisms for the
Neural Syndicate multi-agent system. It provides:

1. **ConsensusEngine**: Orchestrates voting among critic agents to reach
   consensus on contested decisions or low-confidence results.

2. **CriticAgent**: A special agent type that evaluates other agents' outputs
   and provides critique, vote decisions, and confidence scores.

3. **VoteResult**: Structured result of a voting round with winner, confidence,
   and reasoning.

Design decisions:
- Uses asynchronous voting to avoid blocking the main agent loop
- Implements quorum-based consensus (minimum votes needed)
- Supports multiple voting strategies (unanimous, majority, weighted)
- Timeout-driven completion to prevent indefinite voting
- Dead letter handling for failed consensus requests

References:
- Implements the Critic Agent pattern from the Neural Syndicate architecture
- Aligns with error_classifier.py RetryExecutor for retry strategy consistency

Usage:
    # Create a critic agent
    critic = CriticAgent(agent_id="critic-1", bus_port=bus_port)

    # Create consensus engine
    engine = ConsensusEngine(
        critic_agents=["critic-1", "critic-2"],
        broker=broker,
        quorum=2,
    )

    # Request consensus on a decision
    result = await engine.request_consensus(
        topic="Should we proceed with this refactoring?",
        options=["yes", "no", "needs_work"],
        timeout=30.0,
    )

    if result.reached:
        print(f"Consensus: {result.winner} (confidence: {result.confidence})")
    else:
        print("No consensus reached")
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.multi_agent.neural_syndicate.base_agent import BaseAgent
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    ConsensusRequest,
    ConsensusResponse,
    Intent,
    MessageType,
    Performative,
)

logger = logging.getLogger(__name__)

# Default consensus timeout (seconds)
_DEFAULT_CONSENSUS_TIMEOUT: float = DEFAULT_SHORT_TIMEOUT_SECONDS

# Minimum quorum for consensus
_DEFAULT_QUORUM: int = 2

# Backoff delay for retry on no consensus
_DEFAULT_RETRY_DELAY: float = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Voting Strategy
# ═══════════════════════════════════════════════════════════════════════════


class VotingStrategy(str, Enum):
    """Strategy for aggregating votes into a consensus decision."""

    UNANIMOUS = "unanimous"  # All must agree
    MAJORITY = "majority"  # > 50% wins
    WEIGHTED = "weighted"  # Weighted by confidence scores
    HIGHEST_CONFIDENCE = "highest_confidence"  # Single highest wins


# ═══════════════════════════════════════════════════════════════════════════
# Consensus Engine
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ConsensusResult:
    """Result of a consensus voting round.

    Attributes:
        request_id: ID of the original consensus request
        reached: Whether consensus was reached
        winner: The winning option (None if no consensus)
        confidence: Confidence score (0.0 to 1.0)
        votes: All votes received
        reasoning: Human-readable explanation
        elapsed_seconds: Time taken to reach consensus
    """

    request_id: str
    reached: bool
    winner: str | None = None
    confidence: float = 0.0
    votes: tuple[ConsensusResponse, ...] = field(default_factory=tuple)
    reasoning: str = ""
    elapsed_seconds: float = 0.0


class ConsensusEngine:
    """Consensus orchestration engine for multi-agent voting.

    The consensus engine:
    1. Receives consensus requests via request_consensus()
    2. Sends VOTE_REQUEST messages to registered critic agents
    3. Collects votes until quorum is reached or timeout expires
    4. Applies the voting strategy to determine the winner
    5. Returns the consensus result

    Thread safety:
        - Uses asyncio.Lock for concurrent consensus requests
        - Each consensus round is independent

    Usage:
        engine = ConsensusEngine(
            critic_agents=["critic-1", "critic-2"],
            broker=broker,
            quorum=2,
        )

        result = await engine.request_consensus(
            topic="Code quality assessment",
            options=["approve", "reject", "needs_changes"],
            timeout=30.0,
        )
    """

    def __init__(
        self,
        critic_agents: list[str],
        broker: Any,  # MessageBroker - avoid circular import
        *,
        quorum: int = _DEFAULT_QUORUM,
        timeout: float = _DEFAULT_CONSENSUS_TIMEOUT,
        strategy: VotingStrategy = VotingStrategy.MAJORITY,
        retry_count: int = 3,
        retry_delay: float = _DEFAULT_RETRY_DELAY,
    ) -> None:
        """Initialize the consensus engine.

        Args:
            critic_agents: List of critic agent IDs to vote
            broker: MessageBroker for sending/receiving messages
            quorum: Minimum votes needed for consensus
            timeout: Maximum seconds to wait for votes
            strategy: Strategy for aggregating votes
            retry_count: Number of retries if no consensus
            retry_delay: Delay between retries (seconds)
        """
        self._critic_agents = list(critic_agents)
        self._broker = broker
        self._quorum = max(1, int(quorum))
        self._timeout = max(1.0, float(timeout))
        self._strategy = VotingStrategy(strategy)
        self._retry_count = max(0, int(retry_count))
        self._retry_delay = max(0.1, float(retry_delay))

        # Active voting rounds: request_id -> VoteCollector
        self._active_rounds: dict[str, VoteCollector] = {}
        self._lock = asyncio.Lock()

        logger.info(
            "ConsensusEngine initialized: critics=%d quorum=%d strategy=%s",
            len(self._critic_agents),
            self._quorum,
            self._strategy.value,
        )

    async def request_consensus(
        self,
        topic: str,
        options: list[str],
        *,
        initial_proposer: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConsensusResult:
        """Request consensus on a topic among critic agents.

        This method:
        1. Creates a unique request ID
        2. Sends VOTE_REQUEST to all registered critic agents
        3. Collects votes until quorum or timeout
        4. Applies voting strategy to determine winner

        Args:
            topic: The question or decision to reach consensus on
            options: List of possible choices
            initial_proposer: Agent that initiated the consensus request
            metadata: Additional context for voters

        Returns:
            ConsensusResult with winner, confidence, and votes
        """
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        logger.info(
            "ConsensusEngine: requesting consensus request_id=%s topic=%s options=%s",
            request_id,
            topic[:50],
            options,
        )

        # Create consensus request payload
        consensus_req = ConsensusRequest(
            topic=topic,
            options=options,
            voters=self._critic_agents,
            quorum=self._quorum,
            deadline_utc=_compute_deadline_utc(self._timeout),
        )

        # Create vote request message
        message = AgentMessage(
            sender=initial_proposer or "consensus_engine",
            receiver="",  # Broadcast to critics
            performative=Performative.VOTE_REQUEST,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload={
                "consensus_request": consensus_req.model_dump(mode="json"),
                "metadata": metadata or {},
            },
            correlation_id=request_id,
            ttl=len(self._critic_agents) + 1,  # Allow for forwarding
        )

        # Initialize vote collector
        collector = VoteCollector(
            request_id=request_id,
            options=frozenset(options),
            quorum=self._quorum,
            timeout=self._timeout,
            strategy=self._strategy,
        )

        async with self._lock:
            self._active_rounds[request_id] = collector

        try:
            # Publish vote request to all critics
            delivered = await self._broker.broadcast(message)
            logger.debug(
                "ConsensusEngine: vote request delivered to %d critics",
                delivered,
            )

            # Wait for votes
            votes = await collector.collect_votes()

            elapsed = time.monotonic() - start_time

            # Apply voting strategy
            result = self._determine_outcome(
                request_id=request_id,
                votes=votes,
                elapsed=elapsed,
            )

            logger.info(
                "ConsensusEngine: consensus result request_id=%s reached=%s winner=%s confidence=%.2f",
                request_id,
                result.reached,
                result.winner,
                result.confidence,
            )

            return result

        finally:
            async with self._lock:
                self._active_rounds.pop(request_id, None)

    async def submit_vote(self, response: ConsensusResponse) -> bool:
        """Submit a vote response to an active consensus round.

        This is called by critic agents when they receive a VOTE_REQUEST.
        Can also be called by an orchestrator that receives VOTE_RESPONSE
        messages on behalf of the consensus engine.

        Args:
            response: The vote response from a critic agent

        Returns:
            True if the vote was accepted for an active round
        """
        async with self._lock:
            collector = self._active_rounds.get(response.request_id)
            if collector is None:
                logger.warning(
                    "ConsensusEngine: vote for unknown request_id=%s",
                    response.request_id,
                )
                return False

            return collector.add_vote(response)

    async def handle_vote_response_message(
        self,
        message: AgentMessage,
    ) -> bool:
        """Handle a VOTE_RESPONSE message from the broker.

        This is called by the OrchestratorAgent when it receives a
        VOTE_RESPONSE message that was sent to this consensus engine.

        Args:
            message: The VOTE_RESPONSE AgentMessage

        Returns:
            True if the vote was accepted
        """
        if message.performative != Performative.VOTE_RESPONSE:
            return False

        try:
            payload = message.payload
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)

            response = ConsensusResponse(**payload)
            return await self.submit_vote(response)

        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "ConsensusEngine: failed to handle vote response: %s",
                exc,
            )
            return False

    async def get_active_round(self, request_id: str) -> ConsensusResult | None:
        """Get the current status of an active consensus round.

        Args:
            request_id: The consensus request ID

        Returns:
            Current ConsensusResult or None if not found
        """
        async with self._lock:
            collector = self._active_rounds.get(request_id)

        if collector is None:
            return None

        votes = collector.get_votes()
        elapsed = collector.elapsed_time()

        return self._determine_outcome(
            request_id=request_id,
            votes=votes,
            elapsed=elapsed,
        )

    def _determine_outcome(
        self,
        request_id: str,
        votes: tuple[ConsensusResponse, ...],
        elapsed: float,
    ) -> ConsensusResult:
        """Apply voting strategy to determine consensus outcome.

        Args:
            request_id: The consensus request ID
            votes: Collected votes
            elapsed: Elapsed time in seconds

        Returns:
            ConsensusResult with winner and confidence
        """
        if not votes:
            return ConsensusResult(
                request_id=request_id,
                reached=False,
                reasoning="No votes received",
                elapsed_seconds=elapsed,
            )

        # Count votes per option
        vote_counts: dict[str, list[ConsensusResponse]] = {}
        total_confidence = 0.0

        for vote in votes:
            choice = vote.choice
            if choice and choice in vote_counts:
                vote_counts[choice].append(vote)
            elif choice:
                vote_counts[choice] = [vote]
            total_confidence += vote.confidence if vote.confidence else 1.0

        if not vote_counts:
            return ConsensusResult(
                request_id=request_id,
                reached=False,
                votes=votes,
                reasoning="All votes abstained",
                elapsed_seconds=elapsed,
            )

        # Apply voting strategy
        if self._strategy == VotingStrategy.UNANIMOUS:
            winner, confidence = self._evaluate_unanimous(vote_counts)
        elif self._strategy == VotingStrategy.MAJORITY:
            winner, confidence = self._evaluate_majority(vote_counts, len(votes))
        elif self._strategy == VotingStrategy.WEIGHTED:
            winner, confidence = self._evaluate_weighted(vote_counts, total_confidence)
        else:  # HIGHEST_CONFIDENCE
            winner, confidence = self._evaluate_highest_confidence(vote_counts)

        reached = winner is not None and confidence >= 0.5

        return ConsensusResult(
            request_id=request_id,
            reached=bool(reached),
            winner=winner,
            confidence=confidence,
            votes=votes,
            reasoning=f"Winner: {winner} with {confidence:.2f} confidence",
            elapsed_seconds=elapsed,
        )

    def _evaluate_unanimous(
        self,
        vote_counts: dict[str, list[ConsensusResponse]],
    ) -> tuple[str | None, float]:
        """Evaluate unanimous consensus - all must agree."""
        if len(vote_counts) == 1:
            winner = next(iter(vote_counts.keys()))
            avg_confidence = sum(v.confidence for v in vote_counts[winner]) / len(vote_counts[winner])
            return winner, avg_confidence
        return None, 0.0

    def _evaluate_majority(
        self,
        vote_counts: dict[str, list[ConsensusResponse]],
        total_votes: int,
    ) -> tuple[str | None, float]:
        """Evaluate majority consensus - > 50% wins."""
        best_option = None
        best_count = 0

        for option, votes in vote_counts.items():
            if len(votes) > best_count:
                best_count = len(votes)
                best_option = option

        if best_option and best_count > total_votes / 2:
            confidence = best_count / total_votes
            return best_option, confidence

        return None, 0.0

    def _evaluate_weighted(
        self,
        vote_counts: dict[str, list[ConsensusResponse]],
        total_confidence: float,
    ) -> tuple[str | None, float]:
        """Evaluate weighted consensus - confidence-weighted winner."""
        weighted_scores: dict[str, float] = {}

        for option, votes in vote_counts.items():
            weighted_scores[option] = sum(v.confidence for v in votes)

        if not weighted_scores or total_confidence == 0:
            return None, 0.0

        best_option = max(weighted_scores.items(), key=lambda x: x[1])[0]
        confidence = weighted_scores[best_option] / total_confidence

        return best_option, confidence

    def _evaluate_highest_confidence(
        self,
        vote_counts: dict[str, list[ConsensusResponse]],
    ) -> tuple[str | None, float]:
        """Evaluate highest confidence - single highest wins."""
        best_option = None
        best_avg_confidence = 0.0

        for option, votes in vote_counts.items():
            avg_confidence = sum(v.confidence for v in votes) / len(votes)
            if avg_confidence > best_avg_confidence:
                best_avg_confidence = avg_confidence
                best_option = option

        return best_option, best_avg_confidence


# ═══════════════════════════════════════════════════════════════════════════
# Vote Collector
# ═══════════════════════════════════════════════════════════════════════════


class VoteCollector:
    """Collects votes for an active consensus round.

    This class is NOT part of the public API; it's an internal implementation
    detail of ConsensusEngine.
    """

    def __init__(
        self,
        request_id: str,
        options: frozenset[str],
        quorum: int,
        timeout: float,
        strategy: VotingStrategy,
    ) -> None:
        """Initialize vote collector.

        Args:
            request_id: Unique request ID
            options: Set of valid voting options
            quorum: Minimum votes needed
            timeout: Maximum seconds to collect
            strategy: Voting strategy
        """
        self.request_id = request_id
        self.options = options
        self.quorum = quorum
        self.timeout = timeout
        self.strategy = strategy

        self._votes: list[ConsensusResponse] = []
        self._lock = asyncio.Lock()
        self._start_time: float = time.monotonic()
        self._completed = asyncio.Event()

    def add_vote(self, response: ConsensusResponse) -> bool:
        """Add a vote to the collection.

        Args:
            response: The vote response

        Returns:
            True if vote was accepted
        """
        # Reject duplicate votes from same voter
        if any(v.voter == response.voter for v in self._votes):
            logger.warning(
                "VoteCollector: duplicate vote from %s for request_id=%s",
                response.voter,
                self.request_id,
            )
            return False

        # Validate vote
        if response.choice is not None and response.choice not in self.options:
            logger.warning(
                "VoteCollector: invalid choice %s for request_id=%s (valid: %s)",
                response.choice,
                self.request_id,
                self.options,
            )
            return False

        self._votes.append(response)
        logger.debug(
            "VoteCollector: vote from %s added to request_id=%s (count=%d/%d)",
            response.voter,
            self.request_id,
            len(self._votes),
            self.quorum,
        )

        # Check if quorum reached
        if len(self._votes) >= self.quorum:
            self._completed.set()

        return True

    async def collect_votes(self) -> tuple[ConsensusResponse, ...]:
        """Wait for votes until quorum or timeout.

        Returns:
            Tuple of collected votes
        """
        # Check if we already have quorum
        if len(self._votes) >= self.quorum:
            return tuple(self._votes)

        # Wait with timeout
        try:
            await asyncio.wait_for(
                self._completed.wait(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "VoteCollector: timeout for request_id=%s (votes=%d/%d)",
                self.request_id,
                len(self._votes),
                self.quorum,
            )

        return tuple(self._votes)

    def get_votes(self) -> tuple[ConsensusResponse, ...]:
        """Get current votes without waiting.

        Returns:
            Tuple of votes collected so far
        """
        return tuple(self._votes)

    def elapsed_time(self) -> float:
        """Get elapsed time since collector creation.

        Returns:
            Elapsed seconds
        """
        return time.monotonic() - self._start_time


# ═══════════════════════════════════════════════════════════════════════════
# Critic Agent
# ═══════════════════════════════════════════════════════════════════════════


class CriticAgent(BaseAgent):
    """Critic agent for evaluating and voting on agent decisions.

    A critic agent:
    1. Receives VOTE_REQUEST messages from the consensus engine
    2. Evaluates the topic and options using its internal logic
    3. Returns a VOTE_RESPONSE with its choice and confidence

    The critic's evaluation logic is implemented in _evaluate_topic(),
    which subclasses can override for domain-specific critique.

    Usage:
        class CodeReviewCritic(CriticAgent):
            async def _evaluate_topic(self, topic: str, options: list[str]) -> tuple[str, float]:
                # Custom critique logic
                ...
    """

    def __init__(
        self,
        agent_id: str,
        bus_port: Any = None,  # AgentBusPort - avoid circular import
        *,
        expertise: list[str] | None = None,
        default_confidence: float = 0.8,
    ) -> None:
        """Initialize the critic agent.

        Args:
            agent_id: Unique agent identifier
            bus_port: AgentBusPort for message transport
            expertise: List of expertise areas for this critic
            default_confidence: Default confidence when no specific assessment
        """
        self._expertise = list(expertise) if expertise else []
        self._default_confidence = float(default_confidence)

        super().__init__(
            agent_id=agent_id,
            bus_port=bus_port,
        )

    @property
    def agent_type(self) -> str:
        return "critic"

    @property
    def capabilities(self) -> list[AgentCapability]:
        """Critic agents support voting and consensus intents."""
        return [
            AgentCapability(
                name="critique",
                intents=[Intent.CRITIQUE, Intent.VOTE, Intent.VALIDATE],
                description="Critic agent for evaluation and voting",
                version="1.0.0",
                metadata={"expertise": self._expertise},
            ),
        ]

    async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming messages.

        For VOTE_REQUEST, evaluates and returns a vote.
        """
        if message.performative == Performative.VOTE_REQUEST:
            return await self._handle_vote_request(message)

        return None

    async def _handle_vote_request(self, message: AgentMessage) -> AgentMessage:
        """Handle a vote request.

        Args:
            message: The VOTE_REQUEST message

        Returns:
            VOTE_RESPONSE message with the critic's vote
        """
        payload = message.payload
        consensus_req_data = payload.get("consensus_request", {})
        metadata = payload.get("metadata", {})

        try:
            consensus_req = ConsensusRequest(**consensus_req_data)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "CriticAgent %s: failed to parse consensus request: %s",
                self.agent_id,
                exc,
            )
            return AgentMessage(
                sender=self.agent_id,
                receiver=message.sender,
                performative=Performative.VOTE_RESPONSE,
                intent=Intent.VOTE,
                message_type=MessageType.VOTE,
                payload=ConsensusResponse(
                    request_id=message.correlation_id or consensus_req_data.get("topic", "unknown"),
                    voter=self.agent_id,
                    choice=None,
                    confidence=0.0,
                    reasoning=f"Parse error: {exc}",
                ).model_dump(mode="json"),
                correlation_id=message.correlation_id,
                in_reply_to=message.message_id,
            )

        # Evaluate the topic
        choice, confidence = await self._evaluate_topic(
            topic=consensus_req.topic,
            options=consensus_req.options,
            metadata=metadata,
        )

        # Create vote response - use message.correlation_id (UUID from request_consensus)
        # NOT consensus_req.topic, which is a different identifier
        vote_response = ConsensusResponse(
            request_id=message.correlation_id or consensus_req.topic,
            voter=self.agent_id,
            choice=choice,
            confidence=confidence,
            reasoning=f"Evaluated based on expertise: {', '.join(self._expertise)}",
        )

        return AgentMessage(
            sender=self.agent_id,
            receiver=message.sender,
            performative=Performative.VOTE_RESPONSE,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload=vote_response.model_dump(mode="json"),
            correlation_id=message.correlation_id,
            in_reply_to=message.message_id,
        )

    async def _evaluate_topic(
        self,
        topic: str,
        options: list[str],
        metadata: dict[str, Any],
    ) -> tuple[str, float]:
        """Evaluate a topic and return a vote.

        This is the core critique logic. Subclasses should override this
        for domain-specific evaluation.

        Args:
            topic: The question or decision
            options: Available choices
            metadata: Additional context

        Returns:
            Tuple of (chosen_option, confidence)
        """
        # Default implementation: random selection with moderate confidence
        import random

        choice = random.choice(options)
        confidence = self._default_confidence * random.uniform(0.5, 1.0)

        logger.debug(
            "CriticAgent %s: evaluated topic='%s' -> choice=%s confidence=%.2f",
            self.agent_id,
            topic[:30],
            choice,
            confidence,
        )

        return choice, confidence


# ═══════════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════════


def _compute_deadline_utc(timeout_seconds: float) -> str:
    """Compute a UTC deadline timestamp.

    Args:
        timeout_seconds: Timeout in seconds from now

    Returns:
        ISO format UTC deadline timestamp
    """

    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    return deadline.isoformat()


__all__ = [
    "ConsensusEngine",
    "ConsensusResponse",
    "ConsensusResult",
    "CriticAgent",
    "VoteCollector",
    "VoteResult",  # Alias for ConsensusResult
    "VotingStrategy",
]

# Alias for backward compatibility with __init__.py exports
VoteResult = ConsensusResult
