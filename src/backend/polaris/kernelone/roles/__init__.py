"""KernelOne roles subsystem.

Provides shared role contracts used by multiple cells.
"""

from polaris.kernelone.roles.shared_contracts import (
    AgentMessage,
    AgentStatus,
    MessageType,
    RoleAgent,
    create_protocol_fsm,
)

__all__ = [
    "AgentMessage",
    "AgentStatus",
    "MessageType",
    "RoleAgent",
    "create_protocol_fsm",
]
