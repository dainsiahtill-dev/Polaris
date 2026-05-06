"""KernelOneBusPortAdapter - Implements IBusPort using Cells' KernelOneMessageBusPort.

ACGA 2.0 Section 6.3: Cells provide implementations of KernelOne port interfaces.

This adapter wraps the Cells' KernelOneMessageBusPort to implement the IBusPort
interface defined in KernelOne.

Note: The Cells' bus_port module already imports AgentEnvelope from KernelOne,
so no conversion is needed - we can delegate directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.kernelone.ports.bus_port import (
    AgentEnvelope,
    DeadLetterRecord,
    IBusPort,
)

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.public import (
        KernelOneMessageBusPort as _KernelOneMessageBusPort,
    )


class KernelOneBusPortAdapter(IBusPort):
    """Adapter that implements IBusPort using Cells' KernelOneMessageBusPort.

    This adapter maintains the KernelOne → Cells dependency direction by
    implementing the abstract port interface defined in KernelOne.

    Note:
        Both KernelOne and Cells use the same AgentEnvelope type from
        kernelone/ports/bus_port.py, so no conversion is needed.

    Example:
        >>> from polaris.cells.adapters.kernelone import KernelOneBusPortAdapter
        >>> adapter = KernelOneBusPortAdapter()
        >>> # Use adapter.publish(), adapter.poll(), etc.
    """

    def __init__(
        self,
        *,
        nats_url: str | None = None,
        nats_enabled: bool | None = None,
        max_queue_size: int = 512,
    ) -> None:
        """Initialize the adapter with optional NATS configuration.

        Args:
            nats_url: NATS server URL (default: from env or nats://127.0.0.1:4222)
            nats_enabled: Enable NATS transport (default: from env or True)
            max_queue_size: Maximum inbox size per receiver
        """
        from polaris.cells.roles.runtime.public import (
            KernelOneMessageBusPort,
        )

        self._impl: _KernelOneMessageBusPort = KernelOneMessageBusPort(
            nats_url=nats_url,
            nats_enabled=nats_enabled,
            max_queue_size=max_queue_size,
        )

    def publish(self, envelope: AgentEnvelope) -> bool:
        """Deliver `envelope` to the receiver's inbox.

        Args:
            envelope: The message envelope to deliver.

        Returns:
            True on success, False if the bus is full / unavailable.
        """
        return self._impl.publish(envelope)

    def poll(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
    ) -> AgentEnvelope | None:
        """Poll the next message for `receiver` using blocking sleep.

        Args:
            receiver: The receiver's name to poll messages for.
            block: If True, block until a message is available or timeout.
            timeout: Maximum time to wait when block=True (seconds).

        Returns:
            The next `AgentEnvelope` for `receiver`, or None if no message
            is available within the timeout.
        """
        return self._impl.poll(receiver, block=block, timeout=timeout)

    async def poll_async(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
    ) -> AgentEnvelope | None:
        """Poll the next message for `receiver` using async sleep.

        Args:
            receiver: The receiver's name to poll messages for.
            block: If True, block until a message is available or timeout.
            timeout: Maximum time to wait when block=True (seconds).

        Returns:
            The next `AgentEnvelope` for `receiver`, or None if no message
            is available within the timeout.
        """
        return await self._impl.poll_async(receiver, block=block, timeout=timeout)

    def ack(self, message_id: str, receiver: str) -> bool:
        """Acknowledge successful processing of a message.

        Args:
            message_id: The message ID to acknowledge.
            receiver: The receiver's name.

        Returns:
            True if the message was found and removed.
        """
        return self._impl.ack(message_id, receiver)

    def nack(
        self,
        message_id: str,
        receiver: str,
        *,
        reason: str = "",
        requeue: bool = True,
    ) -> bool:
        """Negative-acknowledge a message.

        Args:
            message_id: The message ID to negative-acknowledge.
            receiver: The receiver's name.
            reason: Optional reason for the negative acknowledgment.
            requeue: Whether to requeue the message.

        Returns:
            True if the message was found.
        """
        return self._impl.nack(message_id, receiver, reason=reason, requeue=requeue)

    def pending_count(self, receiver: str) -> int:
        """Return number of pending messages in inbox.

        Args:
            receiver: The receiver's name.

        Returns:
            Number of pending messages.
        """
        return self._impl.pending_count(receiver)

    def requeue_all_inflight(self, receiver: str) -> int:
        """Requeue ALL inflight messages for a receiver back to inbox.

        Args:
            receiver: Agent name whose inflight messages to requeue.

        Returns:
            Number of messages requeued.
        """
        return self._impl.requeue_all_inflight(receiver)

    @property
    def dead_letters(self) -> list[DeadLetterRecord]:
        """Snapshot of all dead-letter records accumulated so far.

        Returns:
            List of dead letter records.
        """
        return self._impl.dead_letters
