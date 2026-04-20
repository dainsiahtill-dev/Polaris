"""Neural Syndicate Message Broker - Bus Port Abstraction.

This module provides the MessageBroker interface and InMemoryBroker implementation
that bridges the Neural Syndicate ACL layer with the AgentBusPort transport layer.

Key responsibilities:
1. **Message Translation**: Convert between AgentMessage (ACL) and AgentEnvelope (transport)
2. **Address Resolution**: Resolve route decisions to actual delivery
3. **Dead Letter Handling**: Forward expired/undeliverable messages to dead letter queue
4. **Observability**: Record delivery metrics and trace context

Design decisions:
- InMemoryBroker is the default broker backed by InMemoryAgentBusPort
- Broker does NOT implement its own queue; it delegates to AgentBusPort
- Supports forwarding to NATS-backed KernelOneMessageBusPort for cross-process
- Dead letter messages are logged and tracked for debugging

Usage:
    broker = InMemoryBroker()

    # Publish a message
    message = AgentMessage.create_request(
        sender="orchestrator",
        receiver="worker-1",
        intent=Intent.EXECUTE_TASK,
        payload={"task": "analyze"},
    )
    await broker.publish(message)

    # Subscribe to topics
    await broker.subscribe("worker-1", callback_handler)

    # Get dead letters for debugging
    dead_letters = broker.get_dead_letters()
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

# Import core types from KernelOne (no Cells dependency)
from polaris.kernelone.multi_agent.bus_port import (
    AgentBusPort,
    AgentEnvelope,
    DeadLetterRecord,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.kernelone.multi_agent.neural_syndicate.protocol import AgentMessage

logger = logging.getLogger(__name__)

# Default dead letter TTL (messages exceeding this are truly dead)
_DEFAULT_DEAD_LETTER_TTL: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Message Broker Interface
# ═══════════════════════════════════════════════════════════════════════════


class MessageBroker:
    """Abstract message broker interface.

    A broker receives routing decisions from the MessageRouter and delivers
    messages to their destinations via the underlying AgentBusPort.

    The broker is responsible for:
    1. Converting AgentMessage to AgentEnvelope for transport
    2. Delivering to single or multiple receivers based on RouteDecision
    3. Handling delivery failures and dead lettering
    4. Maintaining delivery statistics

    Implementations:
    - InMemoryBroker: Default in-memory implementation
    - (Future) NATSBroker: NATS-backed for cross-process messaging
    """

    async def publish(self, message: AgentMessage) -> bool:
        """Publish a message based on its routing metadata.

        Args:
            message: The AgentMessage to publish

        Returns:
            True if the message was successfully queued for at least one receiver
        """
        raise NotImplementedError

    async def publish_to_receivers(
        self,
        message: AgentMessage,
        receivers: tuple[str, ...],
    ) -> int:
        """Publish a message to specific receivers.

        Args:
            message: The AgentMessage to publish
            receivers: Tuple of receiver agent IDs

        Returns:
            Number of successful deliveries
        """
        raise NotImplementedError

    async def broadcast(self, message: AgentMessage) -> int:
        """Broadcast a message to all subscribers.

        Args:
            message: The AgentMessage to broadcast

        Returns:
            Number of successful deliveries
        """
        raise NotImplementedError

    def get_dead_letters(self) -> list[DeadLetterRecord]:
        """Get all dead letter records.

        Returns:
            List of DeadLetterRecord for undeliverable messages
        """
        raise NotImplementedError

    def get_stats(self) -> dict[str, Any]:
        """Get broker statistics.

        Returns:
            Dictionary with delivery stats
        """
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════
# In-Memory Broker
# ═══════════════════════════════════════════════════════════════════════════


class InMemoryBroker(MessageBroker):
    """In-memory message broker backed by AgentBusPort.

    This broker:
    1. Uses AgentBusPort for message storage and delivery
    2. Maintains a subscription registry for topic-based delivery
    3. Tracks dead letters for observability
    4. Supports both direct and broadcast delivery

    Thread safety:
        - Uses the underlying AgentBusPort's thread safety
        - Additional asyncio.Lock for subscription management
    """

    def __init__(
        self,
        bus_port: AgentBusPort | None = None,
        *,
        dead_letter_ttl: int = _DEFAULT_DEAD_LETTER_TTL,
    ) -> None:
        """Initialize the in-memory broker.

        Args:
            bus_port: AgentBusPort implementation (defaults to InMemoryAgentBusPort from Cells)
            dead_letter_ttl: TTL for dead letter messages
        """
        if bus_port is None:
            # Use KernelOne factory to maintain import fence
            from polaris.kernelone.multi_agent.bus_port import create_in_memory_bus_port

            bus_port = create_in_memory_bus_port()
        self._bus_port: AgentBusPort = bus_port
        self._dead_letter_ttl = max(0, int(dead_letter_ttl))

        # Subscription registry: agent_id -> list of callbacks
        self._subscriptions: dict[str, list[Callable[[AgentMessage], Awaitable[None]]]] = {}
        self._sub_lock = asyncio.Lock()

        # Statistics
        self._messages_published: int = 0
        self._messages_delivered: int = 0
        self._dead_letter_count: int = 0

        logger.info("InMemoryBroker initialized")

    async def publish(self, message: AgentMessage) -> bool:
        """Publish a message to its intended receiver(s).

        If the message has a specific receiver, it delivers to that agent.
        If the message is a broadcast (receiver=""), it delivers to all
        subscribers that match the message's intent.

        Args:
            message: The AgentMessage to publish

        Returns:
            True if at least one delivery succeeded
        """
        if message.is_broadcast:
            count = await self.broadcast(message)
            return count > 0

        return await self.publish_to_receivers(message, (message.receiver,)) == 1

    async def publish_to_receivers(
        self,
        message: AgentMessage,
        receivers: tuple[str, ...],
    ) -> int:
        """Publish a message to specific receivers.

        This method creates individual AgentEnvelope copies for each
        receiver and publishes them via the bus port.

        Args:
            message: The AgentMessage to publish
            receivers: Tuple of receiver agent IDs

        Returns:
            Number of successful deliveries
        """
        if not receivers:
            return 0

        successful = 0

        for receiver in receivers:
            # Create receiver-specific envelope
            recv_envelope = AgentEnvelope.from_fields(
                msg_type=message.message_type.value,
                sender=message.sender,
                receiver=receiver,
                payload=message.model_dump(mode="json"),
                message_id=f"{message.message_id}@{receiver}",  # Unique per receiver
                correlation_id=message.correlation_id,
                max_attempts=message.ttl,
            )

            if self._bus_port.publish(recv_envelope):
                successful += 1
                self._messages_delivered += 1
                logger.debug(
                    "Broker: delivered message_id=%s to %s",
                    message.message_id,
                    receiver,
                )
            else:
                logger.warning(
                    "Broker: failed to deliver message_id=%s to %s",
                    message.message_id,
                    receiver,
                )

        self._messages_published += 1

        # Check for dead letter (TTL exceeded AND all deliveries failed)
        if message.is_expired and successful == 0:
            self._handle_dead_letter(message, "ttl_exceeded")

        return successful

    async def broadcast(self, message: AgentMessage) -> int:
        """Broadcast a message to all relevant subscribers.

        A broadcast delivers to all agents that have subscribed to receive
        messages. Subscriptions are matched based on agent ID.

        Args:
            message: The AgentMessage to broadcast

        Returns:
            Number of successful deliveries
        """
        async with self._sub_lock:
            # Get all subscribers
            all_receivers = tuple(self._subscriptions.keys())

        if not all_receivers:
            logger.debug("Broker: broadcast to %s with no subscribers", message.message_id)
            return 0

        # For broadcast, we deliver to all subscribers
        # Note: In a real implementation, you'd want topic-based filtering
        return await self.publish_to_receivers(message, all_receivers)

    async def subscribe(
        self,
        agent_id: str,
        callback: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """Subscribe an agent to receive messages.

        The callback will be invoked for each message delivered to this agent.

        Args:
            agent_id: The subscribing agent's ID
            callback: Async callback to handle delivered messages
        """
        async with self._sub_lock:
            if agent_id not in self._subscriptions:
                self._subscriptions[agent_id] = []
            if callback not in self._subscriptions[agent_id]:
                self._subscriptions[agent_id].append(callback)
                logger.debug("Broker: agent %s subscribed", agent_id)

    async def unsubscribe(
        self,
        agent_id: str,
        callback: Callable[[AgentMessage], Awaitable[None]] | None = None,
    ) -> None:
        """Unsubscribe an agent from receiving messages.

        Args:
            agent_id: The agent to unsubscribe
            callback: Specific callback to remove (if None, removes all callbacks)
        """
        async with self._sub_lock:
            if agent_id not in self._subscriptions:
                return

            if callback is None:
                del self._subscriptions[agent_id]
                logger.debug("Broker: agent %s unsubscribed (all callbacks)", agent_id)
            elif callback in self._subscriptions[agent_id]:
                self._subscriptions[agent_id].remove(callback)
                logger.debug("Broker: agent %s unsubscribed (specific callback)", agent_id)

    async def deliver_to_agent(self, agent_id: str, message: AgentMessage) -> None:
        """Deliver a message directly to an agent's callbacks.

        This is called by the agent's mailbox consumer when a message
        is received from the bus port.

        Args:
            agent_id: The agent to deliver to
            message: The message to deliver
        """
        async with self._sub_lock:
            callbacks = list(self._subscriptions.get(agent_id, []))

        for callback in callbacks:
            try:
                await callback(message)
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Broker: callback error for agent %s: %s",
                    agent_id,
                    exc,
                )

    def _message_to_envelope(self, message: AgentMessage) -> AgentEnvelope:
        """Convert AgentMessage to AgentEnvelope.

        Args:
            message: The ACL message

        Returns:
            AgentEnvelope for transport
        """
        return AgentEnvelope.from_fields(
            msg_type=message.message_type.value,
            sender=message.sender,
            receiver=message.receiver,
            payload=message.model_dump(mode="json"),
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            max_attempts=message.ttl,
        )

    def _handle_dead_letter(self, message: AgentMessage, reason: str) -> None:
        """Handle a message that has become a dead letter.

        Args:
            message: The expired message
            reason: Reason for dead lettering
        """
        # The bus port's dead_letters property tracks these
        # We just log here for observability
        logger.info(
            "Broker: dead letter message_id=%s reason=%s sender=%s intent=%s",
            message.message_id,
            reason,
            message.sender,
            message.intent.value,
        )
        self._dead_letter_count += 1

    def get_dead_letters(self) -> list[DeadLetterRecord]:
        """Get all dead letter records from the underlying bus port.

        Returns:
            List of DeadLetterRecord
        """
        return self._bus_port.dead_letters

    def get_stats(self) -> dict[str, Any]:
        """Get broker statistics.

        Returns:
            Dictionary with delivery and queue stats
        """
        return {
            "messages_published": self._messages_published,
            "messages_delivered": self._messages_delivered,
            "dead_letter_count": self._dead_letter_count,
            "subscriber_count": len(self._subscriptions),
            "bus_stats": self._bus_port.get_stats(),  # type: ignore[attr-defined]
        }


__all__ = [
    "AgentBusPort",
    "DeadLetterRecord",
    "InMemoryBroker",
    "MessageBroker",
]
