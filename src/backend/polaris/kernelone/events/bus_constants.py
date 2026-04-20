r"""Unified event bus constants and architectural constants for KernelOne.

This module provides constants for the 7 intentionally-separate event bus
implementations, along with type constants used across the event system.

Design Decision (P0-005):
    The 7 event bus implementations are intentionally separate for different
    use cases. They are NOT merged into one class hierarchy because:

    1. MessageBus (kernelone/events/message_bus.py)
       - Async pub/sub for Actor model communication
       - Used by Workflow/Turn engines
       - Single-process, asyncio-native

    2. EventRegistry (kernelone/events/typed/registry.py)
       - Typed event system with pattern matching
       - Used for structured observability events
       - Supports filtering by event type

    3. TypedEventBusAdapter (kernelone/events/typed/bus_adapter.py)
       - Bridge between TypedEvent and MessageBus
       - Enables gradual migration from MessageBus to TypedEvent
       - Dual-write during transition
       - NOTE: Event type mappings are defined in bus_adapter.py

    4. InMemoryAgentBusPort (cells/roles/runtime/internal/bus_port.py)
       - Thread-safe synchronous in-memory queue
       - Used by Roles runtime for agent messaging
       - Implements AgentBusPort protocol

    5. KernelOneMessageBusPort (cells/roles/runtime/internal/kernel_one_bus_port.py)
       - NATS-backed distributed messaging
       - Used for cross-process agent communication
       - Falls back to in-memory when NATS unavailable

    6. InMemoryBroker / NATSBroker (kernelone/agent_runtime/neural_syndicate/)
       - ACL-layer message broker for Neural Syndicate
       - Wraps AgentBusPort implementations
       - Provides topic-based routing

    7. UEPEventPublisher (kernelone/events/uep_publisher.py)
       - Unified Event Pipeline v2.0 publisher
       - Bridges external events into the system
       - Handles event schema normalization

Event Type Mappings:
    The TypedEvent-to-MessageType mappings are defined in
    polaris.kernelone.events.typed.bus_adapter as _EVENT_NAME_TO_MESSAGE_TYPE.
    This file re-exports them for convenience via TYPED_EVENT_TO_MESSAGE_TYPE
    and MESSAGE_TYPE_TO_TYPED_EVENT.
"""

from __future__ import annotations

# Import mappings from bus_adapter.py (single source of truth)
from polaris.kernelone.events.typed import (
    MESSAGE_TYPE_TO_TYPED_EVENT,
    TYPED_EVENT_TO_MESSAGE_TYPE,
)

# =============================================================================
# Bus Implementation Identifiers
# =============================================================================

# Canonical paths for the 7 event bus implementations
BUS_IMPL_MESSAGE_BUS = "polaris.kernelone.events.message_bus.MessageBus"
"""Async pub/sub for Actor model communication."""

BUS_IMPL_EVENT_REGISTRY = "polaris.kernelone.events.typed.registry.EventRegistry"
"""Typed event system with pattern matching."""

BUS_IMPL_TYPED_ADAPTER = "polaris.kernelone.events.typed.bus_adapter.TypedEventBusAdapter"
"""Bridge between TypedEvent and MessageBus."""

BUS_IMPL_IN_MEMORY_PORT = "polaris.cells.roles.runtime.internal.bus_port.InMemoryAgentBusPort"
"""Thread-safe synchronous in-memory queue for Roles runtime."""

BUS_IMPL_NATS_PORT = "polaris.cells.roles.runtime.internal.kernel_one_bus_port.KernelOneMessageBusPort"
"""NATS-backed distributed messaging for cross-process communication."""

BUS_IMPL_IN_MEMORY_BROKER = "polaris.kernelone.agent_runtime.neural_syndicate.broker.InMemoryBroker"
"""ACL-layer broker wrapping InMemoryAgentBusPort for Neural Syndicate."""

BUS_IMPL_NATS_BROKER = "polaris.kernelone.agent_runtime.neural_syndicate.nats_broker.NATSBroker"
"""NATS-backed broker for cross-process Neural Syndicate messaging."""

BUS_IMPL_UEP_PUBLISHER = "polaris.kernelone.events.uep_publisher.UEPEventPublisher"
"""Unified Event Pipeline v2.0 event publisher."""

# Mapping of implementation categories
BUS_CATEGORY_SYNC = "sync"
"""Synchronous, thread-safe implementations (InMemoryAgentBusPort)."""

BUS_CATEGORY_ASYNC = "async"
"""Async-native implementations (MessageBus, EventRegistry)."""

BUS_CATEGORY_BRIDGE = "bridge"
"""Adapter/bridge implementations (TypedEventBusAdapter)."""

BUS_CATEGORY_BROKER = "broker"
"""ACL-layer broker implementations (InMemoryBroker, NATSBroker)."""

BUS_CATEGORY_EXTERNAL = "external"
"""External event ingestion (UEPEventPublisher)."""

BUS_IMPL_TO_CATEGORY: dict[str, str] = {
    BUS_IMPL_MESSAGE_BUS: BUS_CATEGORY_ASYNC,
    BUS_IMPL_EVENT_REGISTRY: BUS_CATEGORY_ASYNC,
    BUS_IMPL_TYPED_ADAPTER: BUS_CATEGORY_BRIDGE,
    BUS_IMPL_IN_MEMORY_PORT: BUS_CATEGORY_SYNC,
    BUS_IMPL_NATS_PORT: BUS_CATEGORY_SYNC,
    BUS_IMPL_IN_MEMORY_BROKER: BUS_CATEGORY_BROKER,
    BUS_IMPL_NATS_BROKER: BUS_CATEGORY_BROKER,
    BUS_IMPL_UEP_PUBLISHER: BUS_CATEGORY_EXTERNAL,
}


# =============================================================================
# Protocol Constants for AgentBusPort
# =============================================================================

# Default values for bus port implementations
DEFAULT_MAX_QUEUE_SIZE = 512
"""Default maximum inbox size per receiver."""

DEFAULT_MAX_DEAD_LETTERS = 256
"""Default maximum dead letter records to retain."""

DEFAULT_MAX_ATTEMPTS = 3
"""Default maximum delivery attempts."""

DEFAULT_POLL_TIMEOUT = 1.0
"""Default poll timeout in seconds."""

DEFAULT_POLL_INTERVAL = 0.05
"""Default async poll interval in seconds."""


# =============================================================================
# Bridge Configuration
# =============================================================================

# Enable dual-write by default during migration
DEFAULT_DUAL_WRITE = True
"""Default dual-write mode for TypedEventBusAdapter."""

# Enable bridge logging
DEFAULT_BRIDGE_LOGGING = True
"""Default logging state for event bus bridges."""


__all__ = [
    # Bus implementation identifiers
    "BUS_IMPL_MESSAGE_BUS",
    "BUS_IMPL_EVENT_REGISTRY",
    "BUS_IMPL_TYPED_ADAPTER",
    "BUS_IMPL_IN_MEMORY_PORT",
    "BUS_IMPL_NATS_PORT",
    "BUS_IMPL_IN_MEMORY_BROKER",
    "BUS_IMPL_NATS_BROKER",
    "BUS_IMPL_UEP_PUBLISHER",
    # Bus categories
    "BUS_CATEGORY_SYNC",
    "BUS_CATEGORY_ASYNC",
    "BUS_CATEGORY_BRIDGE",
    "BUS_CATEGORY_BROKER",
    "BUS_CATEGORY_EXTERNAL",
    "BUS_IMPL_TO_CATEGORY",
    # Protocol constants
    "DEFAULT_MAX_QUEUE_SIZE",
    "DEFAULT_MAX_DEAD_LETTERS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_POLL_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    # Event type mappings (re-exported from bus_adapter.py)
    "TYPED_EVENT_TO_MESSAGE_TYPE",
    "MESSAGE_TYPE_TO_TYPED_EVENT",
    # Bridge configuration
    "DEFAULT_DUAL_WRITE",
    "DEFAULT_BRIDGE_LOGGING",
]
