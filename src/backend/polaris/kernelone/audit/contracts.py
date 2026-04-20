"""KernelOne audit contracts and ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from polaris.kernelone.utils.constants import GENESIS_HASH

if TYPE_CHECKING:
    from pathlib import Path


class KernelAuditEventType(str, Enum):
    """Canonical event types for runtime audit."""

    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    TOOL_EXECUTION = "tool_execution"
    LLM_CALL = "llm_call"
    DIALOGUE = "dialogue"
    VERIFICATION = "verification"
    POLICY_CHECK = "policy_check"
    AUDIT_VERDICT = "audit_verdict"
    FILE_CHANGE = "file_change"
    SECURITY_VIOLATION = "security_violation"
    INTERNAL_AUDIT_FAILURE = "internal_audit_failure"


class KernelAuditRole(str, Enum):
    """Canonical runtime role sentinel for audit attribution.

    .. deprecated::
        Polaris-specific role constants (PM, ARCHITECT, CHIEF_ENGINEER,
        DIRECTOR, QA) have been migrated out of KernelOne. Use plain ``str``
        for ``role`` fields throughout the audit runtime.

        ``KernelAuditRole.SYSTEM`` is retained as a safe default value for
        Cell-layer callers. All other role attribution must use plain strings.
    """

    SYSTEM = "system"


@dataclass
class KernelAuditEvent:
    """Kernel-level audit event payload."""

    event_id: str
    timestamp: datetime
    event_type: KernelAuditEventType
    version: str = "2.0"
    source: dict[str, Any] = field(default_factory=dict)
    task: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to JSON-friendly mapping."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "version": self.version,
            "source": dict(self.source),
            "task": dict(self.task),
            "resource": dict(self.resource),
            "action": dict(self.action),
            "data": dict(self.data),
            "context": dict(self.context),
            "prev_hash": self.prev_hash,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> KernelAuditEvent:
        """Build event from mapping."""
        timestamp_raw = payload.get("timestamp")
        if not (isinstance(timestamp_raw, str) and timestamp_raw.strip()):
            raise ValueError("invalid audit payload: timestamp is required")
        timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
        return cls(
            event_id=str(payload.get("event_id") or ""),
            timestamp=timestamp,
            event_type=KernelAuditEventType(str(payload.get("event_type") or "")),
            version=str(payload.get("version") or "2.0"),
            source=dict(payload.get("source") or {}),
            task=dict(payload.get("task") or {}),
            resource=dict(payload.get("resource") or {}),
            action=dict(payload.get("action") or {}),
            data=dict(payload.get("data") or {}),
            context=dict(payload.get("context") or {}),
            prev_hash=str(payload.get("prev_hash") or GENESIS_HASH),
            signature=str(payload.get("signature") or ""),
        )


@dataclass(frozen=True)
class KernelChainVerificationResult:
    """Chain integrity verification result."""

    is_valid: bool
    first_hash: str
    last_hash: str
    total_events: int
    gap_count: int
    verified_at: datetime
    invalid_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class KernelAuditWriteResult:
    """Result of one audit write operation."""

    success: bool
    event_id: str | None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    evidence_paths: list[str] = field(default_factory=list)


@runtime_checkable
class KernelAuditStorePort(Protocol):
    """Storage port required by KernelAuditRuntime."""

    @property
    def runtime_root(self) -> Path: ...

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent: ...

    def query(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]: ...

    def export_json(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]: ...

    def export_csv(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str: ...

    def verify_chain(self) -> KernelChainVerificationResult: ...

    def get_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]: ...

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]: ...
