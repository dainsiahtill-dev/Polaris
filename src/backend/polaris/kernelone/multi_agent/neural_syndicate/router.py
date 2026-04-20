"""Neural Syndicate Message Router - Intent-Based Dynamic Routing.

This module implements the MessageRouter that handles dynamic message routing
based on FIPA ACL performatives, intent classification, and agent capabilities.

Key features:
1. **Rule-Based Routing**: Uses RouteRules with patterns matching performative,
   intent, receiver, and custom predicates
2. **Capability-Based Discovery**: Routes messages to agents based on their
   declared capabilities when no specific receiver is specified
3. **Broadcast Support**: Handles broadcast messages (receiver="") by finding
   all agents that match the message's intent
4. **Dynamic Agent Registry**: Maintains a registry of active agents and their
   capabilities for routing decisions
5. **RouteDecision Generation**: Returns structured routing decisions with
   hop limits and reasons for observability

Design decisions:
- Thread-safe via asyncio.Lock for agent registry modifications
- Does NOT directly deliver messages; returns RouteDecision for the broker
- Supports custom predicates for complex routing logic
- Falls back to capability matching when no explicit receiver

Usage:
    router = MessageRouter()

    # Register agents
    router.register_agent("worker-1", [AgentCapability(name="code_gen", intents=[Intent.CODE_GENERATION])])
    router.register_agent("critic-1", [AgentCapability(name="review", intents=[Intent.CODE_REVIEW])])

    # Route a message
    message = AgentMessage.create_request(
        sender="orchestrator",
        receiver="",  # Broadcast
        intent=Intent.CODE_REVIEW,
        payload={"file": "src/main.py"},
    )
    decision = await router.route(message)
    # decision.receivers = ("critic-1",)  # Routed to capable agent
    # decision.strategy = RoutingStrategy.CAPABILITY_MATCH
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    Intent,
    Performative,
    RouteDecision,
    RoutingStrategy,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Default maximum hops for routed messages
_DEFAULT_HOP_LIMIT: int = 10


# ═══════════════════════════════════════════════════════════════════════════
# Route Rule
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RouteRule:
    """Immutable routing rule for pattern-based message matching.

    RouteRules are evaluated in order of priority. The first matching rule
    wins. If no rules match, the router falls back to capability-based routing.

    Attributes:
        name: Human-readable rule name for debugging
        priority: Higher priority rules are evaluated first (default: 0)
        performative: Match Performative (None = any)
        intent: Match Intent (None = any)
        receiver_pattern: Regex pattern to match receiver (None = any)
        sender_pattern: Regex pattern to match sender (None = any)
        predicate: Optional async predicate for complex matching
        target_agents: Fixed list of target agents if rule matches
        strategy: Routing strategy to use if rule matches
    """

    name: str
    priority: int = 0
    performative: Performative | None = None
    intent: Intent | None = None
    receiver_pattern: str | None = None
    sender_pattern: str | None = None
    predicate: Callable[[AgentMessage], Awaitable[bool]] | Callable[[AgentMessage], bool] | None = None
    target_agents: tuple[str, ...] = ()
    strategy: RoutingStrategy = RoutingStrategy.DIRECT

    def matches(self, message: AgentMessage) -> bool:
        """Check if this rule matches the given message.

        Args:
            message: The message to evaluate

        Returns:
            True if all non-None fields match the message
        """
        # Check performative
        if self.performative is not None and message.performative != self.performative:
            return False

        # Check intent
        if self.intent is not None and message.intent != self.intent:
            return False

        # Check receiver pattern
        if self.receiver_pattern is not None and not re.match(self.receiver_pattern, message.receiver):
            return False

        # Check sender pattern
        return not (self.sender_pattern is not None and not re.match(self.sender_pattern, message.sender))

    async def matches_async(self, message: AgentMessage) -> bool:
        """Async version of matches that also evaluates predicate.

        Args:
            message: The message to evaluate

        Returns:
            True if the rule matches including async predicate
        """
        if not self.matches(message):
            return False

        if self.predicate is not None:
            result = self.predicate(message)
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)

        return True


# ═══════════════════════════════════════════════════════════════════════════
# Message Router
# ═══════════════════════════════════════════════════════════════════════════


class MessageRouter:
    """Intent-based message router with dynamic agent discovery.

    The router maintains:
    1. A registry of agents and their capabilities
    2. A list of routing rules ordered by priority
    3. A default capability matcher for fallback routing

    Routing algorithm:
        1. If message.receiver is non-empty and not broadcast:
           - Return DIRECT routing to that receiver
        2. Else if any RouteRule matches:
           - Return routing based on highest-priority matching rule
        3. Else if message.intent has registered capable agents:
           - Return CAPABILITY_MATCH routing to all capable agents
        4. Else:
           - Return BROADCAST routing (no known recipients)

    Thread safety:
        - Uses asyncio.Lock for agent registry modifications
        - Rule evaluation is read-only and lock-free
    """

    def __init__(self, hop_limit: int = _DEFAULT_HOP_LIMIT) -> None:
        """Initialize the message router.

        Args:
            hop_limit: Default maximum hops for routed messages
        """
        self._hop_limit = max(1, int(hop_limit))

        # Agent registry: agent_id -> list of capabilities
        self._agents: dict[str, list[AgentCapability]] = {}
        # Intent index: intent -> list of agent_ids with that intent
        self._intent_index: dict[Intent, list[str]] = {}
        # Routing rules sorted by priority (descending)
        self._rules: list[RouteRule] = []

        # Lock for thread-safe registry modifications
        self._lock = asyncio.Lock()

        logger.info("MessageRouter initialized (hop_limit=%d)", self._hop_limit)

    # ─── Agent Registry ────────────────────────────────────────────────────

    async def register_agent(
        self,
        agent_id: str,
        capabilities: list[AgentCapability],
    ) -> None:
        """Register an agent with its capabilities.

        Args:
            agent_id: Unique agent identifier
            capabilities: List of capabilities this agent provides
        """
        async with self._lock:
            self._agents[agent_id] = list(capabilities)

            # Update intent index
            for cap in capabilities:
                for intent in cap.intents:
                    if intent not in self._intent_index:
                        self._intent_index[intent] = []
                    if agent_id not in self._intent_index[intent]:
                        self._intent_index[intent].append(agent_id)

        logger.debug(
            "Router: registered agent %s with capabilities: %s",
            agent_id,
            [c.name for c in capabilities],
        )

    async def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent from the registry.

        Args:
            agent_id: The agent to remove
        """
        async with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]

            # Remove from intent index
            for _intent, agents in self._intent_index.items():
                if agent_id in agents:
                    agents.remove(agent_id)

        logger.debug("Router: unregistered agent %s", agent_id)

    async def update_agent_capabilities(
        self,
        agent_id: str,
        capabilities: list[AgentCapability],
    ) -> None:
        """Update an existing agent's capabilities.

        Args:
            agent_id: The agent to update
            capabilities: New list of capabilities
        """
        # Unregister first, then register with new capabilities
        await self.unregister_agent(agent_id)
        await self.register_agent(agent_id, capabilities)

    # ─── Route Rules ────────────────────────────────────────────────────────

    def add_rule(self, rule: RouteRule) -> None:
        """Add a routing rule.

        Rules are sorted by priority after addition.

        Args:
            rule: The RouteRule to add
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        logger.debug("Router: added rule '%s' (priority=%d)", rule.name, rule.priority)

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a routing rule by name.

        Args:
            rule_name: Name of the rule to remove

        Returns:
            True if a rule was removed, False if not found
        """
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                del self._rules[i]
                logger.debug("Router: removed rule '%s'", rule_name)
                return True
        return False

    def clear_rules(self) -> None:
        """Remove all routing rules."""
        self._rules.clear()
        logger.debug("Router: cleared all rules")

    # ─── Routing Logic ──────────────────────────────────────────────────────

    async def route(self, message: AgentMessage) -> RouteDecision:
        """Route a message and return the routing decision.

        This is the main entry point for routing. It implements the
        routing algorithm described in the class docstring.

        Args:
            message: The message to route

        Returns:
            RouteDecision with receivers, strategy, and hop_limit

        Notes:
            - This method does NOT deliver the message; it only returns
              the routing decision for the caller to act upon
            - Broadcast messages (receiver="") are routed based on
              intent/capabilities
        """
        # Step 1: Direct routing if specific receiver
        if message.receiver:
            return RouteDecision(
                receivers=(message.receiver,),
                strategy=RoutingStrategy.DIRECT,
                hop_limit=min(message.remaining_hops, self._hop_limit),
                reason=f"Direct routing to specified receiver: {message.receiver}",
            )

        # Step 2: Check routing rules
        for rule in self._rules:
            if await rule.matches_async(message):
                receivers = tuple(a for a in rule.target_agents if a in self._agents) if rule.target_agents else ()

                if receivers or not rule.target_agents:
                    return RouteDecision(
                        receivers=receivers,
                        strategy=rule.strategy,
                        hop_limit=min(message.remaining_hops, self._hop_limit),
                        reason=f"Rule '{rule.name}' matched",
                    )

        # Step 3: Capability-based routing for intent
        capable_agents = await self._find_capable_agents(message.intent)
        if capable_agents:
            return RouteDecision(
                receivers=tuple(capable_agents),
                strategy=RoutingStrategy.CAPABILITY_MATCH,
                hop_limit=min(message.remaining_hops, self._hop_limit),
                reason=f"Capability match for intent: {message.intent.value}",
            )

        # Step 4: Broadcast as last resort
        return RouteDecision(
            receivers=(),
            strategy=RoutingStrategy.BROADCAST,
            hop_limit=min(message.remaining_hops, self._hop_limit),
            reason="No matching agents; broadcasting to all",
        )

    async def _find_capable_agents(self, intent: Intent) -> list[str]:
        """Find all registered agents capable of handling the given intent.

        Args:
            intent: The intent to match

        Returns:
            List of agent IDs that support the intent
        """
        async with self._lock:
            return list(self._intent_index.get(intent, []))

    # ─── Utility Methods ───────────────────────────────────────────────────

    def get_registered_agents(self) -> dict[str, list[AgentCapability]]:
        """Get a snapshot of all registered agents and their capabilities.

        Returns:
            Dictionary mapping agent_id to list of capabilities
        """
        return dict(self._agents)

    def get_agents_for_intent(self, intent: Intent) -> list[str]:
        """Get all registered agents that support the given intent.

        Args:
            intent: The intent to query

        Returns:
            List of agent IDs
        """
        return list(self._intent_index.get(intent, []))

    def get_stats(self) -> dict[str, Any]:
        """Return router statistics for observability.

        Returns:
            Dictionary with registry and rule stats
        """
        return {
            "registered_agents": len(self._agents),
            "intent_index_size": len(self._intent_index),
            "rules_count": len(self._rules),
            "hop_limit": self._hop_limit,
            "agents_by_intent": {intent.value: len(agents) for intent, agents in self._intent_index.items()},
        }


# ═══════════════════════════════════════════════════════════════════════════
# Built-in Route Rules
# ═══════════════════════════════════════════════════════════════════════════


def create_broadcast_rule(
    intent: Intent,
    name: str | None = None,
    priority: int = 0,
) -> RouteRule:
    """Create a rule that broadcasts to all agents with a given intent.

    Args:
        intent: The intent to broadcast for
        name: Rule name (auto-generated if None)
        priority: Rule priority

    Returns:
        RouteRule that matches the given intent and broadcasts
    """
    return RouteRule(
        name=name or f"broadcast_{intent.value}",
        priority=priority,
        intent=intent,
        strategy=RoutingStrategy.BROADCAST,
        target_agents=(),  # Empty means broadcast to all matched
    )


def create_critic_rule(
    intent: Intent,
    critic_agents: list[str],
    name: str | None = None,
    priority: int = 10,  # Critics get higher priority
) -> RouteRule:
    """Create a rule that routes to specific critic agents.

    Args:
        intent: The intent that requires critic review
        critic_agents: List of critic agent IDs
        name: Rule name (auto-generated if None)
        priority: Rule priority

    Returns:
        RouteRule that routes to specified critics
    """
    return RouteRule(
        name=name or f"critic_{intent.value}",
        priority=priority,
        intent=intent,
        strategy=RoutingStrategy.CONSENSUS,
        target_agents=tuple(critic_agents),
    )


__all__ = [
    "MessageRouter",
    "RouteRule",
    "create_broadcast_rule",
    "create_critic_rule",
]
