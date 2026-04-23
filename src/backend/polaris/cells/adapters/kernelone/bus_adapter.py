"""KernelOneBusPortAdapter - Implements IBusPort using Cells' bus_port module.

ACGA 2.0 Section 6.3: Cells provide implementations of KernelOne port interfaces.

This adapter wraps the Cells' KernelOneMessageBusPort to implement the IBusPort
interface defined in KernelOne.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.ports.bus_port import (
    AgentEnvelope,
    DeadLetterRecord,
    IBusPort,
)

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
        KernelOneMessageBusPort as _KernelOneMessageBusPort,
    )


class KernelOneBusPortAdapter(IBusPort):
    """Adapter that implements IBusPort using Cells' KernelOneMessageBusPort.

    This adapter maintains the KernelOne → Cells dependency direction by
    implementing the abstract port interface defined in KernelOne.

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
        from polaris.cells.roles.runtime.internal.kernel_one_bus_port import (
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
        # Convert KernelOne AgentEnvelope to Cells' AgentMessage format
        from polaris.cells.roles.runtime.internal.agent_runtime_base import AgentMessage

        msg = AgentMessage(
            type=envelope.msg_type,
            sender=envelope.sender,
            receiver=envelope.receiver,
            payload=envelope.payload,
            correlation_id=envelope.correlation_id,
            attempt=envelope.attempt,
            max_attempts=envelope.max_attempts,
        )
        return self._impl.publish(msg)

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
        msg = self._impl.poll(receiver, block=block, timeout=timeout)
        if msg is None:
            return None
        return self._message_to_envelope(msg)

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
        msg = await self._impl.poll_async(receiver, block=block, timeout=timeout)
        if msg is None:
            return None
        return self._message_to_envelope(msg)

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
        return [
            DeadLetterRecord(
                envelope=self._message_to_envelope(dlr.envelope),
                reason=dlr.reason,
                failed_at_utc=dlr.failed_at_utc,
            )
            for dlr in self._impl.dead_letters
        ]

    def _message_to_envelope(self, msg: Any) -> AgentEnvelope:
        """Convert Cells' AgentMessage to KernelOne AgentEnvelope."""
        return AgentEnvelope(
            message_id=str(getattr(msg, "message_id", msg.id if hasattr(msg, "id") else "")),
            msg_type=str(getattr(msg, "type", msg.msg_type if hasattr(msg, "msg_type") else "unknown")),
            sender=str(getattr(msg, "sender", "")),
            receiver=str(getattr(msg, "receiver", "")),
            payload=dict(getattr(msg, "payload", {})),
            timestamp_utc=str(getattr(msg, "timestamp_utc", "")),
            correlation_id=getattr(msg, "correlation_id", None),
            attempt=getattr(msg, "attempt", 0),
            max_attempts=getattr(msg, "max_attempts", 3),
            last_error=str(getattr(msg, "last_error", "")),
        )
