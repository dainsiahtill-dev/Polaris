"""DialogueEvent — Pydantic schema for role communication audit.

Captures inter-role messaging:
- Message direction (sent/received)
- Role attribution
- Message type (request, response, artifact)
- Channel (sync, async, broadcast)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

from polaris.kernelone.audit.omniscient.schemas.base import (
    AuditEvent,
    AuditPriority,
    EventDomain,
)
from pydantic import ConfigDict, Field


class MessageDirection(str, Enum):
    """Direction of message flow."""

    SENT = "sent"
    RECEIVED = "received"


class MessageType(str, Enum):
    """Type of message."""

    REQUEST = "request"
    RESPONSE = "response"
    ARTIFACT = "artifact"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class DialogueEvent(AuditEvent):  # type: ignore[call-arg]  # frozen=True inherited from AuditEvent model_config
    """Role communication audit event.

    Tracks inter-role messaging for:
    - Communication pattern analysis
    - Bottleneck detection
    - Role workload balancing
    - Artifact flow tracking

    Attributes:
        from_role: Source role (pm, architect, director, qa, etc.).
        to_role: Destination role.
        message_type: Type of message.
        direction: SENT or RECEIVED.
        channel: Communication channel (sync, async, broadcast).
        message_summary: First 500 chars of message content.
        artifact_id: Associated artifact ID if message is artifact.
        session_id: Role session identifier.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    domain: EventDomain = Field(default=EventDomain.DIALOGUE)
    event_type: str = Field(default="dialogue")

    from_role: str = Field(default="", max_length=32)
    to_role: str = Field(default="", max_length=32)
    message_type: MessageType = Field(default=MessageType.REQUEST)
    direction: MessageDirection = Field(default=MessageDirection.SENT)
    channel: str = Field(default="sync", max_length=32)
    message_summary: str = Field(default="", max_length=500)
    artifact_id: str = Field(default="", max_length=64)
    session_id: str = Field(default="", max_length=64)

    def to_audit_dict(self) -> dict[str, Any]:
        base = super().to_audit_dict()
        base.update(
            {
                "from_role": self.from_role,
                "to_role": self.to_role,
                "message_type": self.message_type.value,
                "direction": self.direction.value,
                "channel": self.channel,
                "message_summary": self.message_summary,
                "artifact_id": self.artifact_id,
                "session_id": self.session_id,
            }
        )
        return base

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            event_id=data.get("event_id", ""),
            version=data.get("version", "3.0"),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id", ""),
            priority=AuditPriority[data.get("priority", "info").upper()],
            workspace=data.get("workspace", ""),
            role=data.get("role", ""),
            from_role=data.get("from_role", ""),
            to_role=data.get("to_role", ""),
            message_type=MessageType(data.get("message_type", "request").lower()),
            direction=MessageDirection(data.get("direction", "sent").lower()),
            channel=data.get("channel", "sync"),
            message_summary=data.get("message_summary", ""),
            artifact_id=data.get("artifact_id", ""),
            session_id=data.get("session_id", ""),
            data=data.get("data", {}),
            correlation_context=data.get("correlation_context", {}),
        )

    @classmethod
    def create(
        cls,
        from_role: str,
        to_role: str,
        message_type: MessageType = MessageType.REQUEST,
        direction: MessageDirection = MessageDirection.SENT,
        message_summary: str = "",
        session_id: str = "",
        role: str = "",
        workspace: str = "",
        trace_id: str = "",
        run_id: str = "",
        **kwargs: Any,
    ) -> Self:
        return cls(
            from_role=from_role,
            to_role=to_role,
            message_type=message_type,
            direction=direction,
            message_summary=message_summary,
            session_id=session_id,
            role=role or from_role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            **kwargs,
        )
