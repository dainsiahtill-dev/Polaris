"""Shared role contracts for ACGA 2.0 Cell architecture.

This module defines the minimal shared types for cross-Cell role communication.
It lives in kernelone (the infrastructure layer) so that cells can import from
here without creating import cycles between roles.runtime and qa.audit_verdict.

Architecture principle:
    Shared types live at the lowest stable layer (kernelone), not in any
    specific cell, so that every cell can depend on them without introducing
    cross-cell import cycles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Message type enumeration for inter-role messaging."""

    TASK = "task"
    RESULT = "result"
    EVENT = "event"
    COMMAND = "command"
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"


class AgentStatus(str, Enum):
    """Agent lifecycle status."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


ProtocolFSMFactory = Callable[[str | None], Any]
_protocol_fsm_factory: ProtocolFSMFactory | None = None


@dataclass(frozen=True)
class AgentMessage:
    """Message passed between agents.

    This is the canonical shared representation used by roles.runtime and
    qa.audit_verdict (and future cross-cell callers).
    """

    id: str
    type: MessageType
    sender: str
    receiver: str
    payload: dict[str, Any]
    timestamp: str
    correlation_id: str | None = None

    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        sender: str,
        receiver: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> AgentMessage:
        import uuid
        from datetime import datetime

        return cls(
            id=str(uuid.uuid4()),
            type=msg_type,
            sender=sender,
            receiver=receiver,
            payload=payload,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id,
        )


class RoleAgent(ABC):
    """Abstract base class for Role Agents.

    Subclasses must implement:
    - setup_toolbox(): Register role-specific tools
    - handle_message(): Process incoming messages
    - run_cycle(): Main processing loop

    This ABC is re-exported here so that qa.audit_verdict can import the
    canonical RoleAgent type without depending on roles.runtime.internal.
    """

    def __init__(self, workspace: str, agent_name: str) -> None:
        self.workspace = workspace
        self.agent_name = agent_name

    @abstractmethod
    def setup_toolbox(self) -> None:
        """Register role-specific tools. Override in subclass."""

    @abstractmethod
    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming message. Override in subclass.

        Returns optional response message.
        """

    @abstractmethod
    def run_cycle(self) -> bool:
        """Main processing cycle. Override in subclass.

        Returns True if work was done, False if idle.
        """

    @property
    def message_queue(self) -> Any:
        """Subclasses may override to provide a message queue."""
        return None

    @property
    def toolbox(self) -> Any:
        """Subclasses may override to provide a toolbox."""
        return None

    def get_status(self) -> dict[str, Any]:
        """Return agent status summary. Subclasses should override."""
        return {"agent_name": self.agent_name}


def create_protocol_fsm(
    workspace: str | None = None,
) -> Any:
    """Create a protocol FSM instance.

    The returned object provides a request/approve/reject state machine
    for cross-role coordination (plan approval, shutdown, budget gate, etc.).

    Runtime cells may register a concrete factory through
    ``register_protocol_fsm_factory``. If no factory is registered,
    KernelOne returns a minimal in-memory fallback for early bootstrap
    or isolated test scenarios.
    """
    if _protocol_fsm_factory is not None:
        return _protocol_fsm_factory(workspace)

    import threading
    from dataclasses import dataclass

    @dataclass
    class _MinimalFSM:
        _workspace: str | None
        _lock: threading.Lock = field(default_factory=threading.Lock)

        def approve(self, request_id: str, approver: str, notes: str = "") -> bool:
            return True

        def reject(self, request_id: str, rejecter: str, reason: str) -> bool:
            return True

        def list_pending(
            self,
            protocol_type: Any = None,
            to_role: str | None = None,
        ) -> list:
            return []

    return _MinimalFSM(workspace)


def register_protocol_fsm_factory(factory: ProtocolFSMFactory | None) -> None:
    """Register the concrete protocol FSM factory from the runtime cell."""

    global _protocol_fsm_factory
    _protocol_fsm_factory = factory


__all__ = [
    "AgentMessage",
    "AgentStatus",
    "MessageType",
    "ProtocolFSMFactory",
    "RoleAgent",
    "create_protocol_fsm",
    "register_protocol_fsm_factory",
]
