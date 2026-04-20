"""Neural Syndicate Base Agent - Stateless Agent with Mailbox.

This module provides the foundational BaseAgent abstract class that all
Neural Syndicate agents inherit from. Key design principles:

1. **100% Message Passing**: Agents communicate exclusively via messages through
   an asyncio.Queue mailbox. No direct method calls between agents.

2. **Stateless Processing**: Agents maintain no internal state between messages.
   All context is carried in the message payload or retrieved from the blackboard.
   This enables horizontal scaling and crash recovery.

3. **Independent Mailbox Consumer**: Each agent runs a dedicated
   `_mailbox_consumer()` task that polls its mailbox and processes messages.
   This loop is cancellation-safe (handles asyncio.CancelledError).

4. **AgentBusPort Integration**: The agent uses AgentBusPort for message
   transport, enabling both in-memory and NATS-based communication.

5. **Capability Declaration**: Agents declare their capabilities at startup
   for dynamic routing by MessageRouter.

6. **OpenTelemetry Tracing**: All message processing carries trace context
   for distributed observability.

Usage:
    class MyAgent(BaseAgent):
        @property
        def agent_type(self) -> str:
            return "my_agent"

        @property
        def capabilities(self) -> list[AgentCapability]:
            return [AgentCapability(
                name="my_capability",
                intents=[Intent.EXECUTE_TASK],
            )]

        async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
            # Process message, return response if needed
            ...

    agent = MyAgent(agent_id="my-agent-1", bus_port=bus_port)
    await agent.start()
    # ... use agent
    await agent.stop()
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Any

from polaris.kernelone.constants import (
    AGENT_ERROR_BACKOFF_DELAY_SECONDS,
    AGENT_MAILBOX_POLL_INTERVAL_SECONDS,
    AGENT_MAX_CONSECUTIVE_ERRORS,
)

# Import core types from KernelOne (no Cells dependency)
from polaris.kernelone.multi_agent.bus_port import (
    AgentBusPort,
    AgentEnvelope,
)
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    Intent,
    MessageType,
    Performative,
)

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all Neural Syndicate agents.

    This class provides:
    - Asyncio.Queue-based mailbox for incoming messages
    - Independent mailbox consumer loop with cancellation safety
    - AgentBusPort integration for message transport
    - Capability declaration for dynamic routing
    - Lifecycle management (start/stop)

    Subclasses MUST implement:
    - agent_type: Unique agent type identifier
    - capabilities: List of capabilities this agent provides
    - _handle_message(): Process incoming message and return optional response

    Subclasses MAY override:
    - mailbox_poll_interval: Custom poll interval for this agent

    Example:
        class CodeReviewerAgent(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "code_reviewer"

            @property
            def capabilities(self) -> list[AgentCapability]:
                return [AgentCapability(
                    name="code_review",
                    intents=[Intent.CODE_REVIEW],
                    description="Expert code reviewer agent",
                )]

            async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
                if message.intent == Intent.CODE_REVIEW:
                    result = await self._do_code_review(message.payload)
                    return AgentMessage.create_inform(
                        sender=self.agent_id,
                        receiver=message.sender,
                        intent=Intent.CODE_REVIEW,
                        payload={"review": result},
                        correlation_id=message.message_id,
                    )
                return None

    Notes:
        - Agents are designed to be STATELESS between messages
        - All context must come from the message or external storage
        - The mailbox consumer loop handles asyncio.CancelledError gracefully
    """

    def __init__(
        self,
        agent_id: str,
        bus_port: AgentBusPort | None = None,
        *,
        mailbox_size: int = 256,
        default_ttl: int = 10,
        mailbox_poll_interval: float = AGENT_MAILBOX_POLL_INTERVAL_SECONDS,
    ) -> None:
        """Initialize the agent.

        Args:
            agent_id: Unique identifier for this agent instance
            bus_port: AgentBusPort implementation for message transport
                      (defaults to InMemoryAgentBusPort from Cells)
            mailbox_size: Maximum size of the internal mailbox queue
            default_ttl: Default TTL for outgoing messages
            mailbox_poll_interval: How often to poll the bus for messages (seconds)
        """
        self.agent_id = str(agent_id).strip()
        if not self.agent_id:
            raise ValueError("agent_id cannot be empty")

        if bus_port is None:
            # Use KernelOne factory to maintain import fence
            from polaris.kernelone.multi_agent.bus_port import create_in_memory_bus_port

            bus_port = create_in_memory_bus_port()
        self._bus_port: AgentBusPort = bus_port
        self._mailbox: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=mailbox_size)
        self._default_ttl = max(1, int(default_ttl))
        self._mailbox_poll_interval = max(0.001, float(mailbox_poll_interval))

        # Lifecycle state
        self._running: bool = False
        self._consumer_task: asyncio.Task[None] | None = None
        self._stopping: bool = False

        # Statistics
        self._messages_processed: int = 0
        self._consecutive_errors: int = 0

        logger.info(
            "BaseAgent initialized: id=%s type=%s mailbox_size=%d",
            self.agent_id,
            self.agent_type,
            mailbox_size,
        )

    # ─── Abstract Properties (MUST implement) ───────────────────────────────

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Unique agent type identifier (e.g., 'worker', 'critic', 'orchestrator')."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> list[AgentCapability]:
        """Capabilities this agent provides for dynamic routing."""
        ...

    # ─── Abstract Methods (MUST implement) ─────────────────────────────────

    @abstractmethod
    async def _handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Process an incoming message and optionally return a response.

        This is the core message handler. Subclasses implement their
        business logic here. The method must be:
        - Non-blocking (async)
        - Idempotent (safe to call multiple times with same message)
        - Stateless (no internal state modification beyond processing)

        Args:
            message: The incoming AgentMessage to process

        Returns:
            An AgentMessage response if the sender expects a reply, None otherwise.
            The response will be automatically routed back to the original sender.

        Notes:
            - DO NOT modify the incoming message
            - DO NOT access internal agent state for routing decisions
            - Use message.correlation_id for request-response linking
        """
        ...

    # ─── Lifecycle Management ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start the agent's mailbox consumer loop.

        This method is idempotent. Calling start() on an already-running
        agent is a no-op.

        Raises:
            RuntimeError: If the agent is currently stopping
        """
        if self._running:
            logger.debug("Agent %s already running", self.agent_id)
            return

        if self._stopping:
            raise RuntimeError(f"Agent {self.agent_id} is stopping; cannot start until stop() completes")

        self._running = True
        self._consumer_task = asyncio.create_task(
            self._mailbox_consumer(),
            name=f"mailbox-consumer-{self.agent_id}",
        )
        logger.info("Agent %s started (type=%s)", self.agent_id, self.agent_type)

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the agent's mailbox consumer loop gracefully.

        This initiates a graceful shutdown:
        1. Marks the agent as not running
        2. Cancels the consumer task
        3. Waits for in-flight messages to complete (up to timeout)

        Args:
            timeout: Maximum seconds to wait for graceful shutdown

        Raises:
            asyncio.TimeoutError: If shutdown exceeds timeout
        """
        if not self._running:
            logger.debug("Agent %s already stopped", self.agent_id)
            return

        logger.info("Agent %s stopping (timeout=%.1fs)", self.agent_id, timeout)
        self._running = False
        self._stopping = True

        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await asyncio.wait_for(self._consumer_task, timeout=timeout)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning(
                    "Agent %s shutdown timed out after %.1fs",
                    self.agent_id,
                    timeout,
                )
                # TimeoutError means wait_for elapsed but task is still running
                # Explicitly cancel and await to clean up
                self._consumer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._consumer_task
            finally:
                self._consumer_task = None

        self._stopping = False
        logger.info(
            "Agent %s stopped (processed %d messages)",
            self.agent_id,
            self._messages_processed,
        )

    @property
    def is_running(self) -> bool:
        """True if the agent's consumer loop is active."""
        return self._running and not self._stopping

    # ─── Mailbox Consumer Loop ───────────────────────────────────────────────

    async def _mailbox_consumer(self) -> None:
        """Independent mailbox consumer loop.

        This loop runs as an asyncio.Task. It polls the AgentBusPort for
        messages addressed to this agent and enqueues them to the internal
        mailbox for processing.

        Cancellation safety:
        - Handles asyncio.CancelledError gracefully
        - Uses try/except around all awaits
        - Ensures mailbox is drained on shutdown

        Loop invariant:
        - MUST NOT raise unhandled exceptions
        - Errors are logged and trigger backoff
        """
        logger.debug(
            "Mailbox consumer started for agent %s (poll_interval=%.3fs)",
            self.agent_id,
            self._mailbox_poll_interval,
        )

        while self._running:
            try:
                # Poll for messages from the bus
                envelope = await self._bus_port.poll_async(
                    self.agent_id,
                    block=False,
                    timeout=self._mailbox_poll_interval,
                )

                if envelope is not None:
                    # Convert envelope to AgentMessage
                    message = self._envelope_to_message(envelope)
                    if message is None:
                        logger.warning(
                            "Agent %s: failed to parse envelope message_id=%s",
                            self.agent_id,
                            envelope.message_id,
                        )
                        self._bus_port.nack(
                            envelope.message_id,
                            self.agent_id,
                            reason="parse_error",
                            requeue=False,
                        )
                        continue

                    # Check TTL
                    if message.is_expired:
                        logger.info(
                            "Agent %s: dropping expired message_id=%s (ttl=%d hops=%d)",
                            self.agent_id,
                            message.message_id,
                            message.ttl,
                            message.hop_count,
                        )
                        self._bus_port.ack(envelope.message_id, self.agent_id)
                        continue

                    # Enqueue to mailbox for processing
                    try:
                        self._mailbox.put_nowait(message)
                        self._bus_port.ack(envelope.message_id, self.agent_id)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Agent %s mailbox full, dropping message_id=%s",
                            self.agent_id,
                            message.message_id,
                        )
                        self._bus_port.nack(
                            envelope.message_id,
                            self.agent_id,
                            reason="mailbox_full",
                            requeue=False,
                        )

                # Process messages from mailbox
                while not self._mailbox.empty():
                    msg_for_log: str = "unknown"
                    try:
                        message = self._mailbox.get_nowait()
                        msg_for_log = message.message_id
                        await self._process_message(message)
                    except asyncio.QueueEmpty:
                        break
                    except (RuntimeError, ValueError) as exc:
                        logger.exception(
                            "Agent %s: error processing message_id=%s",
                            self.agent_id,
                            msg_for_log,
                        )
                        await self._handle_processing_error(exc)

                # Reset error counter on successful iteration
                self._consecutive_errors = 0

            except asyncio.CancelledError:
                # Graceful shutdown - drain mailbox before exiting
                logger.debug(
                    "Agent %s mailbox consumer cancelled, draining mailbox",
                    self.agent_id,
                )
                await self._drain_mailbox()
                raise  # Re-raise for proper task cancellation

            except (RuntimeError, ValueError) as exc:
                logger.exception(
                    "Agent %s mailbox consumer error: %s",
                    self.agent_id,
                    exc,
                )
                await self._handle_processing_error(exc)
                # Continue loop with backoff

        # Final drain on normal exit
        await self._drain_mailbox()
        logger.debug("Mailbox consumer stopped for agent %s", self.agent_id)

    async def _drain_mailbox(self) -> None:
        """Drain remaining messages from mailbox during shutdown."""
        drained = 0
        while not self._mailbox.empty():
            try:
                self._mailbox.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained > 0:
            logger.info("Agent %s: drained %d messages from mailbox", self.agent_id, drained)

    async def _process_message(self, message: AgentMessage) -> None:
        """Process a single message from the mailbox.

        This method:
        1. Calls the subclass _handle_message()
        2. If a response is returned, sends it back via the bus

        Args:
            message: The message to process
        """
        logger.debug(
            "Agent %s processing message_id=%s performative=%s intent=%s",
            self.agent_id,
            message.message_id,
            message.performative.value,
            message.intent.value,
        )

        try:
            response = await self._handle_message(message)

            if response is not None:
                # Send response back to original sender
                await self._send_message(response)

            self._messages_processed += 1
            self._consecutive_errors = 0

        except (RuntimeError, ValueError) as exc:
            logger.exception(
                "Agent %s: error handling message_id=%s: %s",
                self.agent_id,
                message.message_id,
                exc,
            )
            # Send error response if this was a request
            if message.performative in (
                Performative.REQUEST,
                Performative.QUERY,
                Performative.SUBSCRIBE,
            ):
                error_response = AgentMessage(
                    sender=self.agent_id,
                    receiver=message.sender,
                    performative=Performative.FAILURE,
                    intent=message.intent,
                    message_type=MessageType.ERROR,
                    payload={"error": str(exc), "original_message_id": message.message_id},
                    correlation_id=message.correlation_id,
                    in_reply_to=message.message_id,
                    trace_id=message.trace_id,
                )
                await self._send_message(error_response)

    async def _handle_processing_error(self, exc: Exception) -> None:
        """Handle consecutive processing errors with backoff.

        Args:
            exc: The exception that was raised
        """
        self._consecutive_errors += 1
        if self._consecutive_errors >= AGENT_MAX_CONSECUTIVE_ERRORS:
            logger.warning(
                "Agent %s: %d consecutive errors, applying backoff of %.1fs",
                self.agent_id,
                self._consecutive_errors,
                AGENT_ERROR_BACKOFF_DELAY_SECONDS,
            )
            await asyncio.sleep(AGENT_ERROR_BACKOFF_DELAY_SECONDS)

    # ─── Message Sending ───────────────────────────────────────────────────

    async def _send_message(self, message: AgentMessage) -> bool:
        """Send a message via the bus port.

        This is the primary method for sending messages. It wraps the
        AgentMessage in an AgentEnvelope and publishes via AgentBusPort.

        Args:
            message: The message to send

        Returns:
            True if the message was successfully queued for delivery
        """
        if not self._running:
            logger.warning(
                "Agent %s: cannot send message %s - agent not running",
                self.agent_id,
                message.message_id,
            )
            return False

        # Create envelope from message
        envelope = self._message_to_envelope(message)

        # Publish via bus port
        success = self._bus_port.publish(envelope)

        if success:
            logger.debug(
                "Agent %s sent message_id=%s to %s (performative=%s)",
                self.agent_id,
                message.message_id,
                message.receiver or "BROADCAST",
                message.performative.value,
            )
        else:
            logger.warning(
                "Agent %s failed to send message_id=%s to %s",
                self.agent_id,
                message.message_id,
                message.receiver or "BROADCAST",
            )

        return success

    def _message_to_envelope(self, message: AgentMessage) -> AgentEnvelope:
        """Convert AgentMessage to AgentEnvelope for transport.

        Args:
            message: The ACL message

        Returns:
            AgentEnvelope compatible with AgentBusPort
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

    def _envelope_to_message(self, envelope: AgentEnvelope) -> AgentMessage | None:
        """Convert AgentEnvelope to AgentMessage.

        Args:
            envelope: The transport envelope

        Returns:
            AgentMessage or None if parsing fails
        """
        try:
            return AgentMessage.from_envelope_dict(
                {
                    "message_id": envelope.message_id,
                    "msg_type": envelope.msg_type,
                    "sender": envelope.sender,
                    "receiver": envelope.receiver,
                    "payload": envelope.payload,
                    "timestamp_utc": envelope.timestamp_utc,
                    "correlation_id": envelope.correlation_id,
                    "attempt": envelope.attempt,
                    "max_attempts": envelope.max_attempts,
                    "last_error": envelope.last_error,
                }
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Agent %s: failed to convert envelope to message: %s",
                self.agent_id,
                exc,
            )
            return None

    # ─── Capability Helpers ────────────────────────────────────────────────

    def has_capability_for_intent(self, intent: Intent) -> bool:
        """Check if this agent supports the given intent.

        Args:
            intent: The intent to check

        Returns:
            True if any of this agent's capabilities support the intent
        """
        return any(cap.supports_intent(intent) for cap in self.capabilities)

    # ─── Statistics ────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return agent statistics for observability.

        Returns:
            Dictionary with processing stats and state
        """
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "running": self._running,
            "messages_processed": self._messages_processed,
            "consecutive_errors": self._consecutive_errors,
            "mailbox_size": self._mailbox.qsize(),
            "capabilities": [cap.model_dump(mode="json") for cap in self.capabilities],
        }


__all__ = [
    "AgentCapability",
    "BaseAgent",
]
