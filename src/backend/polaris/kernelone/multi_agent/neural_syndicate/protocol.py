"""Neural Syndicate Protocol - FIPA ACL Semantic Layer.

This module defines the standardized agent communication language (ACL) based on
FIPA ACL规范. It provides:

- AgentMessage: The primary message envelope with FIPA ACL semantics
- Performative: Speech act primitives (REQUEST, PROPOSE, INFORM, etc.)
- Intent: High-level task/intent classification
- MessageType: Technical message categorization
- Routing metadata: TTL, hop_count, correlation_id for distributed tracing

Design decisions:
- Uses Pydantic V2 for strict payload validation
- Does NOT duplicate AgentEnvelope from bus_port.py; this is the ACL semantic
  layer built on top of the transport layer
- TTL/hop_count are the defense mechanism against infinite loops
- All messages carry OpenTelemetry-compatible trace context

References:
- FIPA ACL Spec: http://www.fipa.org/specs/fipa00037/
- OpenTelemetry Trace Context: https://www.w3.org/TR/trace-context/
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ═══════════════════════════════════════════════════════════════════════════
# Performatives (FIPA ACL Speech Acts)
# ═══════════════════════════════════════════════════════════════════════════


class Performative(str, Enum):
    """FIPA ACL performatives - speech act primitives for agent communication.

    Each performative defines the intended effect on the receiver and the
    preconditions that must hold for the act to be successful.
    """

    # Request actions from another agent
    REQUEST = "request"
    QUERY = "query"
    SUBSCRIBE = "subscribe"

    # Propose and negotiate
    PROPOSE = "propose"
    ACCEPT = "accept"
    REJECT = "reject"

    # Information sharing
    INFORM = "inform"
    CONFIRM = "confirm"
    DISCONFIRM = "disconfirm"
    NOT_UNDERSTOOD = "not_understood"

    # Failure and error handling
    FAILURE = "failure"
    REFUSE = "refuse"

    # Multi-agent coordination
    PROPOSE_MATCH = "propose_match"
    VOTE_REQUEST = "vote_request"
    VOTE_RESPONSE = "vote_response"
    CONSENSUS_REQUEST = "consensus_request"
    CONSENSUS_RESPONSE = "consensus_response"

    # System control
    CANCEL = "cancel"
    IGNORE = "ignore"


# ═══════════════════════════════════════════════════════════════════════════
# Intent Classification (High-Level Task Types)
# ═══════════════════════════════════════════════════════════════════════════


class Intent(str, Enum):
    """High-level intent classification for routing and capability matching.

    These intents are used by the MessageRouter to:
    1. Match messages to agents with suitable capabilities
    2. Build execution DAGs dynamically
    3. Enable intent-based topic subscription
    """

    # Task execution
    EXECUTE_TASK = "execute_task"
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    DATA_ANALYSIS = "data_analysis"

    # Information retrieval
    SEARCH_CODE = "search_code"
    FETCH_CONTEXT = "fetch_context"
    QUERY_KNOWLEDGE = "query_knowledge"

    # Coordination
    COORDINATE = "coordinate"
    DELEGATE = "delegate"
    COLLABORATE = "collaborate"

    # Quality and review
    VALIDATE = "validate"
    AUDIT = "audit"
    CRITIQUE = "critique"

    # Consensus and voting
    VOTE = "vote"
    REACH_CONSENSUS = "reach_consensus"
    RESOLVE_CONFLICT = "resolve_conflict"

    # System
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"
    STATUS_REPORT = "status_report"


# ═══════════════════════════════════════════════════════════════════════════
# Message Type (Technical Classification)
# ═══════════════════════════════════════════════════════════════════════════


class MessageType(str, Enum):
    """Technical message type for internal routing."""

    TASK = "task"
    RESULT = "result"
    ERROR = "error"
    COMMAND = "command"
    EVENT = "event"
    HEARTBEAT = "heartbeat"
    CONSENSUS = "consensus"
    VOTE = "vote"


# ═══════════════════════════════════════════════════════════════════════════
# Message Priority
# ═══════════════════════════════════════════════════════════════════════════


class MessagePriority(int, Enum):
    """Message priority levels for queue ordering."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3
    CRITICAL = 4


# ═══════════════════════════════════════════════════════════════════════════
# Routing Strategy
# ═══════════════════════════════════════════════════════════════════════════


class RoutingStrategy(str, Enum):
    """Routing strategies for message delivery."""

    DIRECT = "direct"  # Send to specific receiver
    BROADCAST = "broadcast"  # Send to all agents
    TOPIC = "topic"  # Send to topic subscribers
    CAPABILITY_MATCH = "capability_match"  # Route to capable agents
    CONSENSUS = "consensus"  # Route to critic agents for voting


# ═══════════════════════════════════════════════════════════════════════════
# Route Decision
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RouteDecision:
    """Immutable routing decision returned by MessageRouter.

    Attributes:
        receivers: List of intended receiver agent names (empty for broadcast)
        strategy: The routing strategy used
        hop_limit: Maximum hops allowed (from message TTL - 1)
        reason: Human-readable explanation of the routing choice
    """

    receivers: tuple[str, ...] = field(default_factory=tuple)
    strategy: RoutingStrategy = RoutingStrategy.DIRECT
    hop_limit: int = 10
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Agent Message (FIPA ACL Envelope)
# ═══════════════════════════════════════════════════════════════════════════


class AgentMessage(BaseModel):
    """FIPA ACL-inspired message envelope for agent communication.

    This is the PRIMARY message type for Neural Syndicate. Unlike AgentEnvelope
    (which is the transport layer), AgentMessage carries FIPA ACL semantics:

    - sender/receiver: Agent identifiers
    - performative: Speech act (what the sender wants the receiver to do)
    - intent: High-level classification for routing
    - payload: The actual content (validated by Pydantic)
    - correlation_id: Links related messages (e.g., response to a request)
    - trace_id/span_id: OpenTelemetry-compatible distributed tracing
    - ttl/hop_count: Defense against infinite loops

    Design notes:
    - Uses Pydantic V2 for strict validation
    - hop_count starts at 0, increments on each forward
    - TTL is the death sentence; when ttl <= 0, message is dead-lettered
    - correlation_id enables request-response tracing across agents

    Examples:
        >>> msg = AgentMessage(
        ...     sender="orchestrator",
        ...     receiver="worker-1",
        ...     performative=Performative.REQUEST,
        ...     intent=Intent.EXECUTE_TASK,
        ...     payload={"task": "analyze_repo", "params": {"path": "./src"}},
        ... )
        >>> msg.model_validate(msg.model_dump())  # Self-validates
    """

    model_config = ConfigDict(
        frozen=True,  # Immutable after creation
        str_strip_whitespace=True,
        validate_default=True,
        extra="forbid",  # No unknown fields
    )

    # Identity fields
    message_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique message identifier",
    )
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Message creation timestamp in UTC",
    )

    # Sender/Receiver (receiver="" means broadcast)
    sender: str = Field(..., min_length=1, max_length=256)
    receiver: str = Field(default="", max_length=256)  # "" = broadcast

    # FIPA ACL semantics
    performative: Performative = Field(...)
    intent: Intent = Field(...)
    message_type: MessageType = Field(default=MessageType.TASK)

    # Payload (flexible content, validated by Pydantic models)
    payload: dict[str, Any] = Field(default_factory=dict)

    # Correlation for distributed tracing
    correlation_id: str | None = Field(
        default=None,
        description="Links to related message (e.g., request ID for response)",
    )
    in_reply_to: str | None = Field(
        default=None,
        description="The message_id this message is a reply to",
    )

    # OpenTelemetry-compatible trace context
    trace_id: str | None = Field(
        default=None,
        description="Distributed trace identifier",
    )
    span_id: str | None = Field(
        default=None,
        description="Span identifier within the trace",
    )

    # Loop defense: TTL and hop tracking
    ttl: int = Field(
        default=10,
        ge=0,
        le=100,
        description="Time-to-live in hops. Message dies when TTL <= 0.",
    )
    hop_count: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Number of times this message has been forwarded.",
    )

    # Priority and deadline
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    deadline_utc: str | None = Field(
        default=None,
        description="Absolute UTC deadline for processing",
    )

    # Metadata (extensible, validated)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ─── Computed Properties ────────────────────────────────────────────────

    @property
    def is_broadcast(self) -> bool:
        """True if this is a broadcast message (no specific receiver)."""
        return self.receiver == ""

    @property
    def is_expired(self) -> bool:
        """True if TTL has been exhausted."""
        return self.ttl <= 0

    @property
    def remaining_hops(self) -> int:
        """Remaining hops before expiration."""
        return max(0, self.ttl - self.hop_count)

    # ─── Factory Methods ────────────────────────────────────────────────────

    @classmethod
    def create_request(
        cls,
        sender: str,
        receiver: str,
        intent: Intent,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        ttl: int = 10,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> AgentMessage:
        """Factory: create a REQUEST message."""
        return cls(
            sender=sender,
            receiver=receiver,
            performative=Performative.REQUEST,
            intent=intent,
            message_type=MessageType.TASK,
            payload=payload,
            correlation_id=correlation_id,
            ttl=ttl,
            trace_id=trace_id,
            span_id=span_id,
        )

    @classmethod
    def create_inform(
        cls,
        sender: str,
        receiver: str,
        intent: Intent,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        in_reply_to: str | None = None,
        trace_id: str | None = None,
    ) -> AgentMessage:
        """Factory: create an INFORM (result) message."""
        return cls(
            sender=sender,
            receiver=receiver,
            performative=Performative.INFORM,
            intent=intent,
            message_type=MessageType.RESULT,
            payload=payload,
            correlation_id=correlation_id,
            in_reply_to=in_reply_to,
            trace_id=trace_id,
        )

    @classmethod
    def create_vote_request(
        cls,
        sender: str,
        topic: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> AgentMessage:
        """Factory: create a VOTE_REQUEST for critic agent consensus."""
        return cls(
            sender=sender,
            receiver="",  # Broadcast to all critics
            performative=Performative.VOTE_REQUEST,
            intent=Intent.VOTE,
            message_type=MessageType.VOTE,
            payload={"topic": topic, **payload},
            correlation_id=correlation_id,
            trace_id=trace_id,
        )

    # ─── Transformation Methods ─────────────────────────────────────────────

    def with_forward(self, next_hop: str) -> AgentMessage:
        """Create a forwarded copy with incremented hop_count.

        Args:
            next_hop: The agent name doing the forwarding

        Returns:
            New AgentMessage with hop_count + 1 and updated metadata
        """
        if self.is_expired:
            raise ValueError(f"Message {self.message_id} is expired (ttl={self.ttl})")

        new_metadata = dict(self.metadata)
        new_metadata.setdefault("forward_history", []).append(
            {
                "hop": self.hop_count,
                "agent": next_hop,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

        return AgentMessage(
            message_id=self.message_id,
            timestamp_utc=self.timestamp_utc,
            sender=self.sender,
            receiver="",  # Clear receiver for rebroadcast
            performative=self.performative,
            intent=self.intent,
            message_type=self.message_type,
            payload=self.payload,
            correlation_id=self.correlation_id,
            in_reply_to=self.in_reply_to,
            trace_id=self.trace_id,
            span_id=self.span_id,
            ttl=self.ttl,
            hop_count=self.hop_count + 1,
            priority=self.priority,
            deadline_utc=self.deadline_utc,
            metadata=new_metadata,
        )

    def to_envelope_dict(self) -> dict[str, Any]:
        """Convert to AgentEnvelope-compatible dict for AgentBusPort.

        This bridges the ACL semantic layer to the transport layer.
        """

        return {
            "message_id": self.message_id,
            "msg_type": self.message_type.value,
            "sender": self.sender,
            "receiver": self.receiver,
            "payload": self.model_dump(mode="json"),
            "timestamp_utc": self.timestamp_utc,
            "correlation_id": self.correlation_id,
            "attempt": self.hop_count,
            "max_attempts": self.ttl,
            "last_error": "",
        }

    @classmethod
    def from_envelope_dict(cls, data: dict[str, Any]) -> AgentMessage:
        """Reconstruct AgentMessage from AgentEnvelope-compatible dict."""
        payload = data.get("payload", {})
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)

        return AgentMessage(
            message_id=data.get("message_id", str(uuid.uuid4())),
            timestamp_utc=data.get(
                "timestamp_utc",
                datetime.now(timezone.utc).isoformat(),
            ),
            sender=data.get("sender", "unknown"),
            receiver=data.get("receiver", ""),
            performative=Performative(payload.get("performative", "inform")),
            intent=Intent(payload.get("intent", "execute_task")),
            message_type=MessageType(payload.get("message_type", "task")),
            payload=payload.get("payload", {}),
            correlation_id=data.get("correlation_id"),
            trace_id=payload.get("trace_id"),
            span_id=payload.get("span_id"),
            ttl=payload.get("ttl", data.get("max_attempts", 10)),
            hop_count=data.get("attempt", 0),
            priority=MessagePriority(payload.get("priority", 1)),
            metadata=payload.get("metadata", {}),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Consensus Messages
# ═══════════════════════════════════════════════════════════════════════════


class ConsensusRequest(BaseModel):
    """Request for multi-agent consensus/voting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str = Field(..., description="Topic to reach consensus on")
    options: list[str] = Field(..., min_length=1)
    voters: list[str] = Field(
        default_factory=list,
        description="List of voter agent names",
    )
    quorum: int = Field(
        default=2,
        ge=1,
        description="Minimum votes needed for consensus",
    )
    deadline_utc: str | None = Field(
        default=None,
        description="Voting deadline in UTC",
    )


class ConsensusResponse(BaseModel):
    """Response containing vote results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(..., description="ID of the ConsensusRequest")
    voter: str = Field(..., description="Voter agent name")
    choice: str | None = Field(
        default=None,
        description="Selected option or None if abstaining",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the choice",
    )
    reasoning: str = Field(default="", description="Explanation of the vote")


# ═══════════════════════════════════════════════════════════════════════════
# Agent Capability (for Dynamic Discovery)
# ═══════════════════════════════════════════════════════════════════════════


class AgentCapability(BaseModel):
    """Capability declaration for agent dynamic discovery.

    Agents declare their capabilities at startup. The MessageRouter
    uses these declarations for intent-based routing.

    Examples:
        >>> cap = AgentCapability(
        ...     name="code_analysis",
        ...     intents=[Intent.CODE_REVIEW, Intent.SEARCH_CODE],
        ...     description="Expert at analyzing code structure",
        ...     version="1.0.0",
        ... )
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    intents: list[Intent] = Field(..., min_length=1)
    description: str = Field(default="", max_length=512)
    version: str = Field(default="1.0.0", max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def supports_intent(self, intent: Intent) -> bool:
        """Check if this capability supports the given intent."""
        return intent in self.intents


__all__ = [
    "AgentCapability",
    "AgentMessage",
    "ConsensusRequest",
    "ConsensusResponse",
    "Intent",
    "MessagePriority",
    "MessageType",
    "Performative",
    "RouteDecision",
    "RoutingStrategy",
]
