"""KernelOne Agent Bus Port — Core Protocol Definitions.

This module defines the core Protocol and data types for inter-agent messaging
that belongs to the KernelOne infrastructure layer.

Architecture principle (ACGA 2.0):
  KernelOne defines the interface contracts (Protocol, dataclasses).
  Cells provide concrete implementations (InMemoryAgentBusPort, KernelOneMessageBusPort).

This separation ensures:
  1. KernelOne components can use messaging without importing Cells
  2. Cells can provide different implementations (in-memory, NATS, etc.)
  3. Tests can mock the Protocol without Cells dependencies

Types defined here:
  - `AgentBusPort`: Protocol for message transport
  - `AgentEnvelope`: Message envelope dataclass
  - `DeadLetterRecord`: Failure record dataclass

Implementations in Cells:
  - `polaris.cells.roles.runtime.internal.bus_port.InMemoryAgentBusPort`
  - `polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort`
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
        KernelOneMessageBusPort,
    )

# Default poll interval for async polling (seconds)
_DEFAULT_POLL_INTERVAL_SEC: float = 0.05


@dataclass
class AgentEnvelope:
    """Message envelope stored in bus queues.

    Keeps the same logical fields as AgentMessage so callers need
    no changes, but removes all file-system artifacts.

    Attributes:
        message_id: Unique identifier for this message
        msg_type: Message type (e.g., "task", "result")
        sender: Agent ID that sent this message
        receiver: Agent ID that should receive this message
        payload: Message payload as dict
        timestamp_utc: ISO timestamp when message was created
        correlation_id: Optional correlation ID for request-response
        attempt: Current attempt count
        max_attempts: Maximum allowed attempts
        last_error: Last error message if any
    """

    message_id: str
    msg_type: str  # AgentMessage.type.value (e.g., "task", "result")
    sender: str
    receiver: str
    payload: dict[str, Any]
    timestamp_utc: str
    correlation_id: str | None = None
    attempt: int = 0
    max_attempts: int = 3
    last_error: str = ""

    @classmethod
    def from_fields(
        cls,
        msg_type: str,
        sender: str,
        receiver: str,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
        correlation_id: str | None = None,
        max_attempts: int = 3,
    ) -> AgentEnvelope:
        """Create an AgentEnvelope from individual fields.

        Args:
            msg_type: Message type string
            sender: Sender agent ID
            receiver: Receiver agent ID
            payload: Message payload dict
            message_id: Optional message ID (auto-generated if None)
            correlation_id: Optional correlation ID
            max_attempts: Maximum delivery attempts

        Returns:
            A new AgentEnvelope instance
        """
        return cls(
            message_id=message_id or str(uuid.uuid4()),
            msg_type=str(msg_type or "").strip() or "unknown",
            sender=str(sender or "").strip() or "unknown",
            receiver=str(receiver or "").strip() or "unknown",
            payload=dict(payload) if isinstance(payload, dict) else {},
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            max_attempts=max(1, int(max_attempts)),
        )


@dataclass
class DeadLetterRecord:
    """Observable failure record for undeliverable messages.

    Emitted to logger.warning at WARNING level. Callers can inspect
    `dead_letters` property on bus port implementations for post-mortem
    analysis. All failures are logged immediately so they are never silent.

    Attributes:
        envelope: The failed AgentEnvelope
        reason: Reason for failure
        failed_at_utc: ISO timestamp when failure occurred
    """

    envelope: AgentEnvelope
    reason: str
    failed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@runtime_checkable
class AgentBusPort(Protocol):
    """Stable Bus Port contract for inter-agent messaging.

    Methods are synchronous wrappers so that existing synchronous
    `RoleAgent` thread model can use them without converting to async.
    Implementations must be thread-safe.

    For async contexts, use `poll_async()` which yields to the event loop
    and can be properly cancelled via asyncio.cancel().
    """

    def publish(self, envelope: AgentEnvelope) -> bool:
        """Deliver `envelope` to the receiver's inbox.

        Returns True on success, False if the bus is full / unavailable.
        Must not raise; failures are observable via dead_letters.
        """
        ...

    def poll(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
    ) -> AgentEnvelope | None:
        """Poll the next message for `receiver` using blocking sleep.

        Returns None if inbox is empty (or timeout expired when block=True).
        This method blocks the calling thread with `time.sleep()` and
        cannot be cancelled by asyncio. Use `poll_async()` for async contexts.

        Must not raise.
        """
        ...

    async def poll_async(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_SEC,
    ) -> AgentEnvelope | None:
        """Poll the next message for `receiver` using async sleep.

        This method is async-aware and can be cancelled via asyncio.cancel().

        Args:
            receiver: The receiver's name to poll messages for.
            block: If True, block until a message is available or timeout.
            timeout: Maximum time to wait when block=True (seconds).
            poll_interval: Time between polls when blocking (seconds).

        Returns:
            The next `AgentEnvelope` for `receiver`, or None if no message
            is available within the timeout.

        Raises:
            asyncio.CancelledError: When cancelled by the caller.
        """
        ...

    def ack(self, message_id: str, receiver: str) -> bool:
        """Acknowledge successful processing of a message.

        Returns True if the message was found and removed.
        """
        ...

    def nack(
        self,
        message_id: str,
        receiver: str,
        *,
        reason: str = "",
        requeue: bool = True,
    ) -> bool:
        """Negative-acknowledge a message.

        Requeues (up to max_attempts) or moves to dead-letter.
        Returns True if the message was found.
        """
        ...

    def pending_count(self, receiver: str) -> int:
        """Return number of pending messages in inbox."""
        ...

    def requeue_all_inflight(self, receiver: str) -> int:
        """Requeue ALL inflight messages for a receiver back to inbox.

        Used for atomic rollback when peek() fails to re-publish all messages.
        Preserves FIFO order by requeuing in reverse inflight order.

        Args:
            receiver: Agent name whose inflight messages to requeue.

        Returns:
            Number of messages requeued.
        """
        ...

    @property
    def dead_letters(self) -> list[DeadLetterRecord]:
        """Snapshot of all dead-letter records accumulated so far."""
        ...


def create_in_memory_bus_port() -> AgentBusPort:
    """Factory function to create the default in-memory bus port.

    This factory is provided by KernelOne but the actual implementation
    is supplied by the Cells layer. This maintains the KernelOne → Cells
    dependency direction while avoiding direct import of Cells internal modules.

    Returns:
        An in-memory AgentBusPort implementation.

    Note:
        The implementation is loaded lazily to maintain the KernelOne → Cells
        fence. This function can be called without triggering Cells import
        until the returned port is actually used.
    """
    from polaris.cells.roles.runtime.internal.bus_port import InMemoryAgentBusPort

    return InMemoryAgentBusPort()


def create_kernel_one_bus_port(
    *,
    nats_url: str | None = None,
    nats_enabled: bool | None = None,
    max_queue_size: int = 512,
) -> KernelOneMessageBusPort:
    """Factory function to create a KernelOne-aware message bus port.

    This factory creates a bus port that supports both in-memory messaging
    and optional NATS transport for cross-process communication.

    Args:
        nats_url: NATS server URL (default: from env or nats://127.0.0.1:4222)
        nats_enabled: Enable NATS transport (default: from env or True)
        max_queue_size: Maximum inbox size per receiver

    Returns:
        A KernelOne-aware AgentBusPort implementation.

    Note:
        The implementation is loaded lazily to maintain the KernelOne → Cells
        fence. This function can be called without triggering Cells import
        until the returned port is actually used.
    """
    from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
        KernelOneMessageBusPort,
    )

    return KernelOneMessageBusPort(
        nats_url=nats_url,
        nats_enabled=nats_enabled,
        max_queue_size=max_queue_size,
    )


__all__ = [
    "_DEFAULT_POLL_INTERVAL_SEC",
    "AgentBusPort",
    "AgentEnvelope",
    "DeadLetterRecord",
    "create_in_memory_bus_port",
    "create_kernel_one_bus_port",
]
