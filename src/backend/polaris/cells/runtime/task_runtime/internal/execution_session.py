from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_MAX_SUMMARY_LENGTH = 400


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
            session_id=str(payload.get("session_id") or "").strip(),
            task_id=int(payload.get("task_id") or 0),
            role_id=str(payload.get("role_id") or "").strip(),
            worker_id=str(payload.get("worker_id") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            status=str(payload.get("status") or "").strip().lower() or "active",
            claimed_at=str(payload.get("claimed_at") or "").strip(),
            last_heartbeat_at=str(payload.get("last_heartbeat_at") or "").strip(),
            lease_expires_at=str(payload.get("lease_expires_at") or "").strip(),
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
    "normalize_positive_int",
    "parse_utc_iso",
    "sanitize_summary",
    "utc_now",
    "utc_now_iso",
]
