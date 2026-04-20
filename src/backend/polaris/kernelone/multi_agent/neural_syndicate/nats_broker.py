"""Neural Syndicate NATS Broker - Cross-Process Messaging via NATS.

This module provides a NATS-backed message broker that enables cross-process
agent communication. It wraps `KernelOneMessageBusPort` to provide the
`MessageBroker` interface expected by Neural Syndicate components.

Key features:
1. **Cross-process messaging**: Uses NATS pub/sub for distributed agent communication
2. **Fallback to in-memory**: If NATS is unavailable, falls back to in-memory queues
3. **Thread-safe**: Implements the same thread-safety guarantees as `KernelOneMessageBusPort`
4. **Transparent failover**: Messages are automatically routed via NATS or in-memory

Design decisions:
- Wraps `KernelOneMessageBusPort` rather than duplicating NATS logic
- Uses the same envelope serialization as `KernelOneMessageBusPort`
- Configuration via environment variables: `NATS_URL`, `NATS_ENABLED`
- Lazy NATS connection: only connects when first publish is attempted

Usage:
    # Create NATS-backed broker
    broker = NATSBroker()

    # Use with OrchestratorAgent
    orchestrator = OrchestratorAgent(
        agent_id="orchestrator-1",
        broker=broker,
        workers=["worker-1", "worker-2"],
    )

    # Explicit NATS connection (optional - broker auto-connects)
    broker.ensure_nats_connected()

Configuration:
    NATS_URL / POLARIS_NATS_URL: NATS server URL (default: nats://127.0.0.1:4222)
    NATS_ENABLED / POLARIS_NATS_ENABLED: Enable NATS transport (default: True)
    NATS_CONNECT_TIMEOUT: Connection timeout in seconds (default: 3.0)
    NATS_RECONNECT_WAIT: Reconnect wait interval (default: 1.0)
    NATS_MAX_RECONNECT: Max reconnect attempts (default: -1 for infinite)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

# Import core types from KernelOne (no Cells dependency)
from polaris.kernelone.multi_agent.bus_port import (
    AgentEnvelope,
    DeadLetterRecord,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
        KernelOneMessageBusPort,
    )
    from polaris.kernelone.multi_agent.neural_syndicate.protocol import AgentMessage

logger = logging.getLogger(__name__)

# Default dead letter TTL
_DEFAULT_DEAD_LETTER_TTL: int = 0


class NATSBroker:
    """NATS-backed message broker for cross-process agent communication.

    This broker uses `KernelOneMessageBusPort` as its underlying transport,
    enabling NATS-based pub/sub for distributed messaging while implementing
    the `MessageBroker` interface expected by Neural Syndicate components.

    When NATS is unavailable or disabled, it transparently falls back to
    the in-memory queue behavior.

    Usage:
        broker = NATSBroker()

        # Use like a regular broker
        await broker.publish(message)

        # Or explicitly connect to NATS
        broker.ensure_nats_connected()
    """

    def __init__(
        self,
        *,
        nats_url: str | None = None,
        nats_enabled: bool | None = None,
        max_queue_size: int = 512,
        dead_letter_ttl: int = _DEFAULT_DEAD_LETTER_TTL,
    ) -> None:
        """Initialize the NATS broker.

        Args:
            nats_url: NATS server URL (default: from env or nats://127.0.0.1:4222)
            nats_enabled: Enable NATS transport (default: from env or True)
            max_queue_size: Maximum inbox size per receiver
            dead_letter_ttl: TTL for dead letter messages
        """
        # Create the NATS-backed bus port using KernelOne factory
        self._bus_port: KernelOneMessageBusPort = self._create_bus_port(
            nats_url=nats_url,
            nats_enabled=nats_enabled,
            max_queue_size=max_queue_size,
        )

        self._dead_letter_ttl = max(0, int(dead_letter_ttl))

        # Subscription registry
        self._subscriptions: dict[str, list[Callable[[AgentMessage], Awaitable[None]]]] = {}
        self._sub_lock = asyncio.Lock()

        # Statistics
        self._messages_published: int = 0
        self._messages_delivered: int = 0
        self._dead_letter_count: int = 0

        # Track NATS connection state
        self._nats_connected: bool = False

        logger.info("NATSBroker initialized")

    @staticmethod
    def _create_bus_port(
        *,
        nats_url: str | None = None,
        nats_enabled: bool | None = None,
        max_queue_size: int = 512,
    ) -> KernelOneMessageBusPort:
        """Create the KernelOne-aware bus port.

        Uses KernelOne factory to maintain the KernelOne → Cells import fence.
        The actual implementation comes from Cells layer.

        Args:
            nats_url: NATS server URL
            nats_enabled: Enable NATS transport
            max_queue_size: Maximum inbox size per receiver

        Returns:
            KernelOne-aware AgentBusPort implementation
        """
        from polaris.kernelone.multi_agent.bus_port import create_kernel_one_bus_port

        return create_kernel_one_bus_port(
            nats_url=nats_url,
            nats_enabled=nats_enabled,
            max_queue_size=max_queue_size,
        )

    # ─── MessageBroker Interface ─────────────────────────────────────────────────

    async def publish(self, message: AgentMessage) -> bool:
        """Publish a message to its intended receiver(s).

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
            envelope = AgentEnvelope.from_fields(
                msg_type=message.message_type.value,
                sender=message.sender,
                receiver=receiver,
                payload=message.model_dump(mode="json"),
                message_id=f"{message.message_id}@{receiver}",
                correlation_id=message.correlation_id,
                max_attempts=message.ttl,
            )

            if self._bus_port.publish(envelope):
                successful += 1
                self._messages_delivered += 1
                logger.debug(
                    "NATSBroker: delivered message_id=%s to %s",
                    message.message_id,
                    receiver,
                )
            else:
                logger.warning(
                    "NATSBroker: failed to deliver message_id=%s to %s",
                    message.message_id,
                    receiver,
                )

        self._messages_published += 1

        # Check for dead letter
        if message.is_expired and successful == 0:
            self._handle_dead_letter(message, "ttl_exceeded")

        return successful

    async def broadcast(self, message: AgentMessage) -> int:
        """Broadcast a message to all relevant subscribers.

        Args:
            message: The AgentMessage to broadcast

        Returns:
            Number of successful deliveries
        """
        async with self._sub_lock:
            all_receivers = tuple(self._subscriptions.keys())

        if not all_receivers:
            logger.debug("NATSBroker: broadcast to %s with no subscribers", message.message_id)
            return 0

        return await self.publish_to_receivers(message, all_receivers)

    async def subscribe(
        self,
        agent_id: str,
        callback: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """Subscribe an agent to receive messages.

        Args:
            agent_id: The subscribing agent's ID
            callback: Async callback to handle delivered messages
        """
        async with self._sub_lock:
            if agent_id not in self._subscriptions:
                self._subscriptions[agent_id] = []
            if callback not in self._subscriptions[agent_id]:
                self._subscriptions[agent_id].append(callback)
                logger.debug("NATSBroker: agent %s subscribed", agent_id)

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
                logger.debug("NATSBroker: agent %s unsubscribed (all callbacks)", agent_id)
            elif callback in self._subscriptions[agent_id]:
                self._subscriptions[agent_id].remove(callback)
                logger.debug("NATSBroker: agent %s unsubscribed (specific callback)", agent_id)

    async def deliver_to_agent(self, agent_id: str, message: AgentMessage) -> None:
        """Deliver a message directly to an agent's callbacks.

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
                    "NATSBroker: callback error for agent %s: %s",
                    agent_id,
                    exc,
                )

    async def poll_async(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
        poll_interval: float = 0.05,
    ) -> AgentEnvelope | None:
        """Poll next message for receiver using async-aware polling.

        Delegates to the internal bus port's poll_async implementation.

        Args:
            receiver: The receiver name to poll messages for.
            block: If True, wait until a message arrives or timeout expires.
            timeout: Maximum time to wait when block=True (seconds).
            poll_interval: Time between polls when blocking (seconds).

        Returns:
            The next AgentEnvelope for receiver, or None if no message available.
        """
        return await self._bus_port.poll_async(
            receiver,
            block=block,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    def get_dead_letters(self) -> list[DeadLetterRecord]:
        """Get all dead letter records.

        Returns:
            List of DeadLetterRecord
        """
        return self._bus_port.dead_letters

    def get_stats(self) -> dict[str, Any]:
        """Get broker statistics.

        Returns:
            Dictionary with delivery stats
        """
        return {
            "messages_published": self._messages_published,
            "messages_delivered": self._messages_delivered,
            "dead_letter_count": self._dead_letter_count,
            "subscriber_count": len(self._subscriptions),
            "nats_connected": self._nats_connected,
        }

    # ─── NATS-Specific Methods ─────────────────────────────────────────────────

    def ensure_nats_connected(self) -> bool:
        """Ensure NATS connection is established.

        Returns:
            True if connected to NATS, False if using in-memory fallback
        """
        connected = self._bus_port.ensure_nats_connected()
        self._nats_connected = connected
        return connected

    def disconnect_nats(self) -> None:
        """Disconnect from NATS (for graceful shutdown)."""
        self._bus_port.disconnect_nats()
        self._nats_connected = False

    @property
    def is_nats_connected(self) -> bool:
        """Return True if connected to NATS."""
        return self._nats_connected

    def subscribe_nats_topic(self, topic: str) -> bool:
        """Subscribe to a NATS subject.

        Args:
            topic: NATS subject to subscribe to (e.g., "roles.runtime.*")

        Returns:
            True if subscribed successfully
        """
        return self._bus_port.subscribe(topic)

    # ─── Internal Methods ─────────────────────────────────────────────────────

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
        logger.info(
            "NATSBroker: dead letter message_id=%s reason=%s sender=%s intent=%s",
            message.message_id,
            reason,
            message.sender,
            message.intent.value,
        )
        self._dead_letter_count += 1


# Alias for backward compatibility
NATSMessageBroker = NATSBroker


__all__ = [
    "NATSBroker",
    "NATSMessageBroker",
]
