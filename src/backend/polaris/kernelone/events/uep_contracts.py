"""UEP (Unified Event Pipeline) v2.0 Contracts.

Defines the canonical payload schemas for events flowing through
the Polaris Event Bus in the Unified Event Pipeline architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .topics import (
    TOPIC_RUNTIME_AUDIT,
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_STREAM,
)


@dataclass(frozen=True)
class UEPStreamEventPayload:
    """Payload for a stream event (chunk, tool_call, complete, error)."""

    topic: str = TOPIC_RUNTIME_STREAM
    workspace: str = ""
    run_id: str = ""
    role: str = ""
    turn_id: str | None = None
    event_type: str = ""  # content_chunk, thinking_chunk, tool_call, tool_result, complete, error
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


@dataclass(frozen=True)
class UEPLifecycleEventPayload:
    """Payload for an LLM lifecycle event (call_start, call_end, call_error, call_retry)."""

    topic: str = TOPIC_RUNTIME_LLM
    workspace: str = ""
    run_id: str = ""
    role: str = ""
    event_type: str = ""  # call_start, call_end, call_error, call_retry
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


@dataclass(frozen=True)
class UEPFingerprintEventPayload:
    """Payload for a strategy fingerprint event."""

    topic: str = TOPIC_RUNTIME_FINGERPRINT
    workspace: str = ""
    run_id: str = ""
    role: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


@dataclass(frozen=True)
class UEPAuditEventPayload:
    """Payload for an audit event requiring HMAC chain persistence."""

    topic: str = TOPIC_RUNTIME_AUDIT
    workspace: str = ""
    run_id: str = ""
    role: str = ""
    event_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


# Backward compatibility aliases (deprecated, use topics.py constants directly)
UEP_TOPIC_STREAM = TOPIC_RUNTIME_STREAM
UEP_TOPIC_LLM = TOPIC_RUNTIME_LLM
UEP_TOPIC_FINGERPRINT = TOPIC_RUNTIME_FINGERPRINT
UEP_TOPIC_AUDIT = TOPIC_RUNTIME_AUDIT
