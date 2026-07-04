from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_MAX_SUMMARY_LENGTH = 400
_VALID_SESSION_STATUSES = frozenset({"active", "completed", "failed", "suspended"})
_TERMINAL_SESSION_STATUS_TO_TASK_STATUS = {
    "completed": "completed",
    "failed": "failed",
    # Historical service-level projections recognized cancelled terminal
    # sessions even though normal persisted sessions are suspended on run
    # cancellation. Keep the projection fact here so consumers do not rebuild
    # their own terminal-status tables.
    "cancelled": "cancelled",
}
_TASK_ROW_STATUS_COUNT_KEYS = (
    "pending",
    "in_progress",
    "completed",
    "failed",
    "blocked",
    "cancelled",
)


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return utc_now().isoformat()


def parse_utc_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp."""
    token = str(value or "").strip()
    if not token:
        return None
    try:
        if token.endswith("Z"):
            token = token[:-1] + "+00:00"
        return datetime.fromisoformat(token)
    except (RuntimeError, ValueError) as exc:
        logger.warning("parse_utc_iso: failed to parse %r: %s", value, exc)
        return None


def sanitize_summary(value: Any, *, max_chars: int = _MAX_SUMMARY_LENGTH) -> str:
    """Normalize short human-readable summary text."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    compact = " ".join(part.strip() for part in text.split("\n") if part.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def normalize_positive_int(value: Any, *, default: int, minimum: int = 1) -> int:
    """Convert arbitrary input into a bounded positive integer."""
    if value is None:
        return max(minimum, int(default))
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError) as exc:
        logger.warning("normalize_positive_int: failed to convert %r: %s", value, exc)
    return max(minimum, int(default))


def require_non_empty_field(payload: dict[str, Any], field_name: str) -> str:
    """Return a required non-empty persisted session field."""
    token = str(payload.get(field_name) or "").strip()
    if not token:
        raise ValueError(f"TaskExecutionSession field {field_name!r} is required")
    return token


def require_positive_int_field(payload: dict[str, Any], field_name: str) -> int:
    """Return a required positive integer persisted session field."""
    if field_name not in payload:
        raise ValueError(f"TaskExecutionSession field {field_name!r} is required")
    try:
        parsed = int(payload[field_name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TaskExecutionSession field {field_name!r} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"TaskExecutionSession field {field_name!r} must be >= 1")
    return parsed


def normalize_session_status(payload: dict[str, Any]) -> str:
    """Return a required persisted session status."""
    status = require_non_empty_field(payload, "status").lower()
    if status not in _VALID_SESSION_STATUSES:
        raise ValueError(f"TaskExecutionSession field 'status' must be one of: {sorted(_VALID_SESSION_STATUSES)}")
    return status


def terminal_task_status_value_for_session_status(status: Any) -> str:
    """Return the task-row status value implied by a terminal session status."""

    return _TERMINAL_SESSION_STATUS_TO_TASK_STATUS.get(str(status or "").strip().lower(), "")


def is_terminal_session_status(status: Any) -> bool:
    """Return whether a session status carries a terminal task-row verdict."""

    return bool(terminal_task_status_value_for_session_status(status))


def terminal_session_timestamp(session: TaskExecutionSession) -> float | None:
    """Return the best terminal timestamp carried by a persisted session.

    The timestamp is read-only projection evidence for reconciling task rows
    against terminal execution sessions. It is not a state transition trigger.
    """

    for token in (
        session.released_at,
        session.lease_expires_at,
        session.last_heartbeat_at,
        session.claimed_at,
    ):
        parsed = parse_utc_iso(token)
        if parsed is not None:
            return parsed.timestamp()
    return None


def task_row_status_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Project task-row status counts for runtime read models.

    This helper is a read-only projection over persisted task rows. It keeps
    status bucket semantics beside the execution-session projections so service
    and API callers do not maintain separate status vocabularies.
    """

    stats: dict[str, Any] = {
        "total": len(rows),
        "ready": 0,
        **dict.fromkeys(_TASK_ROW_STATUS_COUNT_KEYS, 0),
    }
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if status not in _TASK_ROW_STATUS_COUNT_KEYS:
            continue
        stats[status] += 1
        if status == "pending" and not row.get("blocked_by"):
            stats["ready"] += 1
    return stats


def build_task_runtime_metadata(
    *,
    session: TaskExecutionSession,
    effective_status: str,
    resume_state: str,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project execution-session state into a task-row metadata payload.

    Boundary:
        This is the canonical projection from a persisted execution session to
        task-row runtime metadata. Service methods may choose when to persist a
        task-row update, but they should not duplicate the ``runtime_execution``
        shape or status/resume projection rules.

    Complexity:
        O(m) time and memory over ``extra_metadata`` size; session projection is
        fixed-size.
    """

    normalized_status = str(effective_status or "").strip().lower() or "pending"
    normalized_resume_state = str(resume_state or "").strip().lower()
    runtime_execution = session.to_dict()
    runtime_execution["effective_status"] = normalized_status
    runtime_execution["resume_state"] = normalized_resume_state
    runtime_execution["resume_available"] = normalized_resume_state == "resumable"
    metadata: dict[str, Any] = dict(extra_metadata or {})
    metadata["runtime_execution"] = runtime_execution
    metadata["claimed_by"] = session.worker_id if normalized_status == "in_progress" else ""
    metadata["last_claimed_by"] = session.worker_id
    metadata["claimed_at"] = session.claimed_at
    metadata["claim_attempt"] = int(session.attempt)
    metadata["resume_count"] = int(session.resume_count)
    metadata["resume_state"] = runtime_execution["resume_state"]
    metadata["resume_available"] = runtime_execution["resume_available"]
    metadata["workflow_run_id"] = session.run_id
    metadata["external_task_id"] = (
        str(metadata.get("external_task_id") or "").strip() or str(session.external_task_id or "").strip()
    )
    metadata["last_execution_error"] = sanitize_summary(session.last_error)
    metadata["last_execution_summary"] = sanitize_summary(session.last_result_summary)
    if session.context_summary:
        metadata["last_context_summary"] = sanitize_summary(session.context_summary)
    return metadata


@dataclass(slots=True)
class TaskExecutionSession:
    """Persisted execution session for a runtime task."""

    session_id: str
    task_id: int
    role_id: str
    worker_id: str
    run_id: str
    status: str
    claimed_at: str
    last_heartbeat_at: str
    lease_expires_at: str
    attempt: int = 1
    resume_count: int = 0
    resumable: bool = True
    origin: str = ""
    selection_source: str = ""
    external_task_id: str = ""
    context_summary: str = ""
    last_error: str = ""
    last_result_summary: str = ""
    released_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        task_id: int,
        role_id: str,
        worker_id: str,
        run_id: str,
        lease_ttl_seconds: int,
        attempt: int,
        resume_count: int,
        origin: str,
        selection_source: str,
        external_task_id: str = "",
        context_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskExecutionSession:
        """Create a fresh active session."""
        now = utc_now()
        expires_at = now + timedelta(seconds=normalize_positive_int(lease_ttl_seconds, default=120))
        now_iso = now.isoformat()
        return cls(
            session_id=f"tx-{uuid4().hex}",
            task_id=int(task_id),
            role_id=str(role_id or "").strip() or "unknown",
            worker_id=str(worker_id or "").strip() or "unknown",
            run_id=str(run_id or "").strip(),
            status="active",
            claimed_at=now_iso,
            last_heartbeat_at=now_iso,
            lease_expires_at=expires_at.isoformat(),
            attempt=max(1, int(attempt)),
            resume_count=max(0, int(resume_count)),
            resumable=True,
            origin=str(origin or "").strip(),
            selection_source=str(selection_source or "").strip(),
            external_task_id=str(external_task_id or "").strip(),
            context_summary=sanitize_summary(context_summary),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskExecutionSession:
        """Hydrate a session from storage."""
        return cls(
            session_id=require_non_empty_field(payload, "session_id"),
            task_id=require_positive_int_field(payload, "task_id"),
            role_id=require_non_empty_field(payload, "role_id"),
            worker_id=require_non_empty_field(payload, "worker_id"),
            run_id=str(payload.get("run_id") or "").strip(),
            status=normalize_session_status(payload),
            claimed_at=str(payload.get("claimed_at") or "").strip(),
            last_heartbeat_at=str(payload.get("last_heartbeat_at") or "").strip(),
            lease_expires_at=require_non_empty_field(payload, "lease_expires_at"),
            attempt=normalize_positive_int(payload.get("attempt"), default=1),
            resume_count=max(0, int(payload.get("resume_count") or 0)),
            resumable=bool(payload.get("resumable", True)),
            origin=str(payload.get("origin") or "").strip(),
            selection_source=str(payload.get("selection_source") or "").strip(),
            external_task_id=str(payload.get("external_task_id") or "").strip(),
            context_summary=sanitize_summary(payload.get("context_summary")),
            last_error=sanitize_summary(payload.get("last_error")),
            last_result_summary=sanitize_summary(payload.get("last_result_summary")),
            released_at=str(payload.get("released_at") or "").strip(),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session."""
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "role_id": self.role_id,
            "worker_id": self.worker_id,
            "run_id": self.run_id,
            "status": self.status,
            "claimed_at": self.claimed_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "lease_expires_at": self.lease_expires_at,
            "attempt": self.attempt,
            "resume_count": self.resume_count,
            "resumable": self.resumable,
            "origin": self.origin,
            "selection_source": self.selection_source,
            "external_task_id": self.external_task_id,
            "context_summary": self.context_summary,
            "last_error": self.last_error,
            "last_result_summary": self.last_result_summary,
            "released_at": self.released_at,
            "metadata": dict(self.metadata),
        }

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return whether the lease has expired."""
        expires_at = parse_utc_iso(self.lease_expires_at)
        if expires_at is None:
            return True
        reference = now or utc_now()
        return expires_at <= reference

    def renew(self, *, lease_ttl_seconds: int, context_summary: str = "") -> None:
        """Renew the active lease."""
        now = utc_now()
        self.last_heartbeat_at = now.isoformat()
        self.lease_expires_at = (
            now + timedelta(seconds=normalize_positive_int(lease_ttl_seconds, default=120))
        ).isoformat()
        if context_summary:
            self.context_summary = sanitize_summary(context_summary)

    def mark_completed(self, *, result_summary: str = "") -> None:
        """Mark the session as completed."""
        now_iso = utc_now_iso()
        self.status = "completed"
        self.released_at = now_iso
        self.last_heartbeat_at = now_iso
        self.lease_expires_at = now_iso
        self.last_result_summary = sanitize_summary(result_summary)
        self.resumable = False

    def mark_failed(self, *, error: str) -> None:
        """Mark the session as failed."""
        now_iso = utc_now_iso()
        self.status = "failed"
        self.released_at = now_iso
        self.last_heartbeat_at = now_iso
        self.lease_expires_at = now_iso
        self.last_error = sanitize_summary(error)
        self.resumable = False

    def mark_suspended(self, *, reason: str, resumable: bool = True) -> None:
        """Mark the session as suspended and optionally resumable."""
        now_iso = utc_now_iso()
        self.status = "suspended"
        self.released_at = now_iso
        self.last_heartbeat_at = now_iso
        self.lease_expires_at = now_iso
        self.last_error = sanitize_summary(reason)
        self.resumable = bool(resumable)


__all__ = [
    "TaskExecutionSession",
    "build_task_runtime_metadata",
    "is_terminal_session_status",
    "normalize_positive_int",
    "parse_utc_iso",
    "sanitize_summary",
    "task_row_status_counts",
    "terminal_task_status_value_for_session_status",
    "utc_now",
    "utc_now_iso",
]
