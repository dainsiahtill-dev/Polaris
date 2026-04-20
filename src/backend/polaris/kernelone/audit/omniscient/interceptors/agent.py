"""Agent Communication Audit Interceptor for tracking multi-agent interactions.

This interceptor subscribes to the OmniscientAuditBus and processes
Director and agent communication events, building a message graph
and tracking communication patterns.

Design:
- Subscribes to Director lifecycle events
- Subscribes to agent communication events
- Builds a message graph (who communicates with whom)
- Tracks communication intents and routing
- Aggregates agent communication metrics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

logger = logging.getLogger(__name__)


class AgentCommInterceptor(BaseAuditInterceptor):
    """Interceptor for auditing multi-agent communication.

    Captures:
    - Director lifecycle events (started, stopped, paused, resumed)
    - Agent-to-agent message patterns
    - Communication intents and routing paths
    - Message counts by role pair

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = AgentCommInterceptor(bus)
        # Events will be automatically processed
    """

    def __init__(self, bus: OmniscientAuditBus) -> None:
        """Initialize the agent communication interceptor.

        Args:
            bus: The audit bus to subscribe to.
        """
        super().__init__(name="agent_comm", priority=AuditPriority.INFO)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Director state
        self._director_active = False
        self._director_paused = False
        self._director_start_time: float | None = None

        # Message graph: sender -> list of receivers
        self._message_graph: dict[str, list[str]] = {}

        # Message counts by role pair
        self._role_pair_counts: dict[tuple[str, str], int] = {}

        # Communication intents
        self._intent_counts: dict[str, int] = {}

        # Message types
        self._message_type_counts: dict[str, int] = {}

        # Message routing paths
        self._routing_paths: list[list[str]] = []

        # Track unique messages
        self._message_ids: set[str] = set()
        self._total_messages = 0

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process an agent communication audit event.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        # Call base implementation first for stats tracking
        super().intercept(event)

        # Extract event data
        if isinstance(event, AuditEventEnvelope):
            event_data = event.event
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        # Process agent communication events
        if isinstance(event_data, dict):
            event_type = event_data.get("type", "")
            if event_type in (
                "director_started",
                "director_stopped",
                "director_paused",
                "director_resumed",
                "agent_communication",
                "agent_message",
            ):
                self._process_agent_event(event_data)

    def _process_agent_event(self, event: dict[str, Any]) -> None:
        """Process a single agent communication event.

        Args:
            event: The agent communication event dict.
        """

        event_type = event.get("type", "")

        # Process Director lifecycle events
        if event_type == "director_started":
            self._handle_director_started(event)
        elif event_type == "director_stopped":
            self._handle_director_stopped(event)
        elif event_type == "director_paused":
            self._handle_director_paused(event)
        elif event_type == "director_resumed":
            self._handle_director_resumed(event)
        elif event_type in ("agent_communication", "agent_message"):
            self._handle_agent_communication(event)

        logger.debug(
            "[agent_comm] Processed event: type=%s",
            event_type,
        )

    def _handle_director_started(self, event: dict[str, Any]) -> None:
        """Handle Director started event.

        Args:
            event: The Director started event.
        """
        import time

        self._director_active = True
        self._director_paused = False
        self._director_start_time = time.time()
        logger.info("[agent_comm] Director started")

    def _handle_director_stopped(self, event: dict[str, Any]) -> None:
        """Handle Director stopped event.

        Args:
            event: The Director stopped event.
        """
        import time

        self._director_active = False
        self._director_paused = False
        if self._director_start_time:
            duration = time.time() - self._director_start_time
            logger.info("[agent_comm] Director stopped after %.2fs", duration)
        self._director_start_time = None

    def _handle_director_paused(self, event: dict[str, Any]) -> None:
        """Handle Director paused event.

        Args:
            event: The Director paused event.
        """
        self._director_paused = True
        logger.info("[agent_comm] Director paused")

    def _handle_director_resumed(self, event: dict[str, Any]) -> None:
        """Handle Director resumed event.

        Args:
            event: The Director resumed event.
        """
        self._director_paused = False
        logger.info("[agent_comm] Director resumed")

    def _handle_agent_communication(self, event: dict[str, Any]) -> None:
        """Handle agent communication event.

        Args:
            event: The agent communication event.
        """
        sender = event.get("sender_role", "unknown")
        receiver = event.get("receiver_role", "unknown")
        intent = event.get("intent", "")
        message_type = event.get("message_type", "")
        routing_path = event.get("routing_path", [])
        message_id = event.get("message_id", "")

        # Skip if already seen
        if message_id and message_id in self._message_ids:
            return
        if message_id:
            self._message_ids.add(message_id)

        # Update message graph
        if sender not in self._message_graph:
            self._message_graph[sender] = []
        if receiver not in self._message_graph[sender]:
            self._message_graph[sender].append(receiver)

        # Update role pair counts
        pair = (sender, receiver)
        self._role_pair_counts[pair] = self._role_pair_counts.get(pair, 0) + 1

        # Update intent counts
        if intent:
            self._intent_counts[intent] = self._intent_counts.get(intent, 0) + 1

        # Update message type counts
        if message_type:
            self._message_type_counts[message_type] = self._message_type_counts.get(message_type, 0) + 1

        # Track routing paths
        if routing_path and len(routing_path) > 1:
            self._routing_paths.append(routing_path)

        # Keep only recent paths
        if len(self._routing_paths) > 100:
            self._routing_paths = self._routing_paths[-100:]

        self._total_messages += 1

        logger.debug(
            "[agent_comm] Message: %s -> %s (intent=%s, type=%s)",
            sender,
            receiver,
            intent,
            message_type,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get agent communication audit statistics.

        Returns:
            Dictionary with agent communication metrics.
        """
        base_stats = super().get_stats()

        # Convert tuple keys to strings for JSON serialization
        role_pair_counts_serializable = {
            f"{sender}->{receiver}": count for (sender, receiver), count in self._role_pair_counts.items()
        }

        return {
            **base_stats,
            "director_active": self._director_active,
            "director_paused": self._director_paused,
            "message_graph": dict(self._message_graph),
            "role_pair_counts": role_pair_counts_serializable,
            "intent_counts": dict(self._intent_counts),
            "message_type_counts": dict(self._message_type_counts),
            "routing_paths_count": len(self._routing_paths),
            "total_messages": self._total_messages,
            "unique_messages": len(self._message_ids),
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        super().reset_stats()
        self._director_active = False
        self._director_paused = False
        self._director_start_time = None
        self._message_graph.clear()
        self._role_pair_counts.clear()
        self._intent_counts.clear()
        self._message_type_counts.clear()
        self._routing_paths.clear()
        self._message_ids.clear()
        self._total_messages = 0
