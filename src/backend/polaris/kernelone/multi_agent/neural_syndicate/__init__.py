"""Neural Syndicate - Multi-Agent Orchestration Framework.

A decoupled, intent-based multi-agent collaboration system featuring:
- Intent-based dynamic message routing
- FIPA ACL-inspired standardized messaging protocol
- Consensus and self-correction via Critic Agent
- Stateless agents with 100% message-passing communication
- Multi-agent orchestration with blackboard pattern

Usage:
    from polaris.kernelone.multi_agent.neural_syndicate import (
        BaseAgent,
        MessageRouter,
        AgentMessage,
        Intent,
    )
"""

from __future__ import annotations

from polaris.kernelone.multi_agent.neural_syndicate.base_agent import AgentCapability, BaseAgent
from polaris.kernelone.multi_agent.neural_syndicate.broker import InMemoryBroker, MessageBroker
from polaris.kernelone.multi_agent.neural_syndicate.consensus import ConsensusEngine, CriticAgent, VoteResult
from polaris.kernelone.multi_agent.neural_syndicate.nats_broker import NATSBroker
from polaris.kernelone.multi_agent.neural_syndicate.orchestrator import (
    BlackboardClient,
    MessageForwarder,
    OrchestratorAgent,
    TaskResult,
    TaskState,
    create_orchestrator,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
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
from polaris.kernelone.multi_agent.neural_syndicate.router import MessageRouter, RouteRule
from polaris.kernelone.multi_agent.neural_syndicate.trace_context import (
    TraceContext,
    create_message_span,
    extract_trace_context,
    inject_trace_context,
    propagate_trace_context,
)

__all__ = [
    "AgentCapability",
    # Protocol
    "AgentMessage",
    # Base
    "BaseAgent",
    # Orchestrator
    "BlackboardClient",
    # Consensus
    "ConsensusEngine",
    "ConsensusRequest",
    "ConsensusResponse",
    "CriticAgent",
    "InMemoryBroker",
    "Intent",
    # Broker
    "MessageBroker",
    "MessageForwarder",
    "MessagePriority",
    # Router
    "MessageRouter",
    "MessageType",
    "NATSBroker",
    "OrchestratorAgent",
    "Performative",
    "RouteDecision",
    "RouteRule",
    "RoutingStrategy",
    "TaskResult",
    "TaskState",
    # Trace Context
    "TraceContext",
    "VoteResult",
    "create_message_span",
    "create_orchestrator",
    "extract_trace_context",
    "inject_trace_context",
    "propagate_trace_context",
]
