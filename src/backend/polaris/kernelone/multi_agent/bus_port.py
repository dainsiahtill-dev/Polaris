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

Port Interface (ACGA 2.0):
  KernelOne defines IBusPort in kernelone/ports/bus_port.py
  Cells implement via cells/adapters/kernelone/bus_adapter.py
"""

from __future__ import annotations

# ACGA 2.0: Import port interface from kernelone/ports (no Cells dependency)
from polaris.kernelone.ports.bus_port import AgentEnvelope
from polaris.kernelone.ports.bus_port import DeadLetterRecord
from polaris.kernelone.ports.bus_port import IAgentBusPort as AgentBusPort

# Default poll interval for async polling (seconds)
_DEFAULT_POLL_INTERVAL_SEC: float = 0.05


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
) -> AgentBusPort:
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
