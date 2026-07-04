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
_TERMINAL_TASK_ROW_STATUSES = frozenset({"completed", "failed", "cancelled"})
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


def is_terminal_task_row_status(status: Any) -> bool:
    """Return whether a task-row status is terminal in runtime projections."""

    return str(status or "").strip().lower() in _TERMINAL_TASK_ROW_STATUSES


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


def build_task_runtime_execution_event_payload(
    *,
    event_type: Any,
    workspace: str,
    task_row: dict[str, Any],
    session: TaskExecutionSession | None,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Project a task row/session pair into a task-runtime execution event.

    Boundary:
        This helper owns the structured event payload shape. The service layer
        owns I/O: fact-stream append, runtime.v2 publish, and error handling.
        Keeping the projection pure prevents TaskBoard/session status readers
        from rebuilding execution-state, resume, attempt, and factory metadata
        fields independently.

    Complexity:
        O(m + d) time and memory over task-row metadata and details size.
    """

    event_type_str = str(event_type or "").strip().lower() or "unknown"
    task_metadata_raw = task_row.get("metadata")
    task_metadata: dict[str, Any] = task_metadata_raw if isinstance(task_metadata_raw, dict) else {}
    runtime_execution_raw = task_metadata.get("runtime_execution")
    runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
    effective_status = str(
        runtime_execution.get("effective_status")
        or task_row.get("status")
        or ""
    ).strip()
    resume_state = str(
        runtime_execution.get("resume_state")
        or task_row.get("resume_state")
        or ""
    ).strip()
    payload: dict[str, Any] = {
        "event_type": event_type_str,
        "workspace": str(workspace or "").strip(),
        "task_id": str(task_row.get("id") or ""),
        "status": str(task_row.get("status") or ""),
        "execution_state": effective_status,
        "subject": str(task_row.get("subject") or ""),
        "session_id": session.session_id if session is not None else "",
        "run_id": session.run_id if session is not None else str(task_row.get("workflow_run_id") or ""),
        "claimed_by": str(task_row.get("claimed_by") or ""),
        "last_claimed_by": str(task_row.get("last_claimed_by") or ""),
        "attempt": int(session.attempt)
        if session is not None
        else normalize_positive_int(runtime_execution.get("attempt"), default=0, minimum=0),
        "resume_count": int(session.resume_count)
        if session is not None
        else normalize_positive_int(runtime_execution.get("resume_count"), default=0, minimum=0),
        "resume_state": resume_state,
        "resume_available": bool(task_row.get("resume_available")) or bool(runtime_execution.get("resume_available")),
        "lease_expires_at": session.lease_expires_at if session is not None else str(task_row.get("lease_expires_at") or ""),
        "last_heartbeat_at": session.last_heartbeat_at if session is not None else str(task_row.get("last_heartbeat_at") or ""),
        "last_error": sanitize_summary(session.last_error if session is not None else task_row.get("last_error")),
        "last_result_summary": sanitize_summary(
            session.last_result_summary if session is not None else task_row.get("last_result_summary")
        ),
        "details": dict(details or {}),
        "timestamp": str(timestamp or "").strip() or utc_now_iso(),
    }
    factory_run_id = str(task_metadata.get("factory_run_id") or "").strip()
    if factory_run_id:
        payload["factory_run_id"] = factory_run_id
    bench_session_id = str(task_metadata.get("factory_bench_session_id") or "").strip()
    if bench_session_id:
        payload["factory_bench_session_id"] = bench_session_id
    factory_project_id = str(task_metadata.get("factory_bench_project_id") or "").strip()
    if factory_project_id:
        payload["factory_bench_project_id"] = factory_project_id
    return payload


def build_task_runtime_execution_event_append_result(
    *,
    event_type: Any,
    fact_event_id: Any = "",
    fact_stream: Any = "",
    fact_storage_path: Any = "",
    published: bool = False,
    append_error: Any = "",
    publish_error: Any = "",
) -> dict[str, Any]:
    """Project fact-stream append evidence for a task-runtime execution event.

    Boundary:
        The task runtime service owns state transitions and persistence. This
        helper owns the stable append-evidence shape so ledger failures do not
        disappear behind debug logs or grow ad-hoc result fields per caller.
    """

    clean_event_type = str(event_type or "unknown").strip() or "unknown"
    clean_fact_event_id = str(fact_event_id or "").strip()
    clean_fact_stream = str(fact_stream or "").strip()
    clean_storage_path = str(fact_storage_path or "").strip()
    clean_append_error = str(append_error or "").strip()
    clean_publish_error = str(publish_error or "").strip()

    result: dict[str, Any] = {
        "ok": bool(clean_fact_event_id) and not clean_append_error,
        "event_type": clean_event_type,
        "published": bool(published),
    }
    if clean_fact_event_id:
        result["fact_event_id"] = clean_fact_event_id
    if clean_fact_stream:
        result["fact_stream"] = clean_fact_stream
    if clean_storage_path:
        result["fact_storage_path"] = clean_storage_path
    if clean_append_error:
        result["error"] = sanitize_summary(clean_append_error, max_chars=300)
    if clean_publish_error:
        result["publish_error"] = sanitize_summary(clean_publish_error, max_chars=300)
    return result


def _with_execution_event_projection(
    payload: dict[str, Any],
    execution_event: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of ``payload`` with optional execution-event evidence."""

    projected = dict(payload)
    if execution_event is not None:
        projected["execution_event"] = dict(execution_event)
    return projected


def project_task_row_execution_event(
    task_row: dict[str, Any],
    execution_event: dict[str, Any] | None,
    *,
    execution_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Return a task-row projection with optional execution-event evidence."""

    projected = _with_execution_event_projection(task_row, execution_event)
    event_payload = [dict(item) for item in execution_events]
    if event_payload:
        projected["execution_events"] = event_payload
    return projected


def _build_task_execution_result(
    *,
    success: bool,
    reason: Any,
    task_row: dict[str, Any] | None = None,
    session: TaskExecutionSession | dict[str, Any] | None = None,
    default_success_reason: str,
    execution_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project common TaskRuntime execution result fields.

    Boundary:
        This private helper owns only the shared public result shape. Semantic
        wrappers remain responsible for naming the lifecycle action so service
        methods do not collapse claim, heartbeat, and terminal transitions into
        an untyped generic call.
    """

    result: dict[str, Any] = {
        "success": bool(success),
        "reason": str(reason or "").strip() or (default_success_reason if success else "unknown"),
    }
    if task_row is not None:
        result["task"] = dict(task_row)
    if session is not None:
        result["session"] = session.to_dict() if isinstance(session, TaskExecutionSession) else dict(session)
    return _with_execution_event_projection(result, execution_event)


def build_task_execution_claim_result(
    *,
    success: bool,
    reason: Any,
    task_row: dict[str, Any] | None = None,
    session: TaskExecutionSession | dict[str, Any] | None = None,
    resumed: bool | None = None,
    claim_applied: bool | None = None,
    reconciled_from_terminal_session: bool | None = None,
    reconcile_error: str = "",
    execution_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a claim attempt into the stable TaskRuntime claim-result shape.

    Boundary:
        Claim execution decides state transitions and persistence. This helper
        only owns the public result projection so callers do not learn multiple
        ad-hoc shapes for lease conflicts, terminal rejects, renewals, and
        fresh claims.

    Complexity:
        O(t + s) time and memory over task/session payload sizes.
    """

    result = _build_task_execution_result(
        success=success,
        reason=reason,
        task_row=task_row,
        session=session,
        default_success_reason="claimed",
        execution_event=execution_event,
    )
    if resumed is not None:
        result["resumed"] = bool(resumed)
    if claim_applied is not None:
        result["claim_applied"] = bool(claim_applied)
    if reconciled_from_terminal_session is not None:
        result["reconciled_from_terminal_session"] = bool(reconciled_from_terminal_session)
    clean_reconcile_error = str(reconcile_error or "").strip()
    if clean_reconcile_error:
        result["reconcile_error"] = clean_reconcile_error
    return result


def build_task_execution_claim_attempt(
    *,
    task_id: Any,
    claim_result: dict[str, Any],
) -> dict[str, Any]:
    """Project one candidate claim attempt into the claim-next shape.

    Boundary:
        Candidate enumeration decides which tasks are attempted. This helper
        owns only the per-attempt result projection consumed by Director claim
        fanout and tests.

    Complexity:
        O(1) time and memory.
    """

    return {
        "task_id": task_id,
        "success": bool(claim_result.get("success")),
        "reason": str(claim_result.get("reason") or "").strip(),
    }


def build_task_execution_claim_next_result(
    *,
    success: bool,
    reason: Any,
    task_row: dict[str, Any] | None = None,
    session: TaskExecutionSession | dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Project atomic claim-next aggregation into its stable result shape.

    Boundary:
        ``claim_next_execution`` owns candidate ordering and state changes.
        This helper owns the aggregate response shape so dispatcher consumers
        do not learn separate ad-hoc formats for empty queues, successful
        claims, and exhausted candidate sets.

    Complexity:
        O(a + t + s) time and memory over attempts and optional payload sizes.
    """

    if isinstance(session, TaskExecutionSession):
        session_payload: dict[str, Any] | None = session.to_dict()
    elif session is not None:
        session_payload = dict(session)
    else:
        session_payload = None

    result: dict[str, Any] = {
        "success": bool(success),
        "task": dict(task_row) if task_row is not None else None,
        "session": session_payload,
        "attempts": [dict(attempt) for attempt in attempts],
        "reason": str(reason or "").strip(),
    }
    return result


def build_task_execution_heartbeat_result(
    *,
    success: bool,
    reason: Any,
    task_row: dict[str, Any] | None = None,
    session: TaskExecutionSession | dict[str, Any] | None = None,
    execution_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a heartbeat attempt into the stable TaskRuntime result shape.

    Boundary:
        Heartbeat execution renews leases and reconciles terminal session
        conflicts. This helper only owns the public result projection so
        session mismatch, inactive session, terminal preservation, and renewed
        lease responses cannot drift into separate ad-hoc dictionaries.

    Complexity:
        O(t + s) time and memory over task/session payload sizes.
    """

    return _build_task_execution_result(
        success=success,
        reason=reason,
        task_row=task_row,
        session=session,
        default_success_reason="heartbeat_renewed",
        execution_event=execution_event,
    )


def build_task_execution_transition_result(
    *,
    success: bool,
    reason: Any,
    task_row: dict[str, Any] | None = None,
    session: TaskExecutionSession | dict[str, Any] | None = None,
    execution_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project completed, failed, and suspended execution transitions.

    Boundary:
        Terminal/suspended transition methods decide state changes and ledger
        events. This helper only owns the shared public result projection for
        invalid tasks, missing sessions, session mismatches, preserved terminal
        sessions, and successful task state transitions.

    Complexity:
        O(t + s) time and memory over task/session payload sizes.
    """

    result = _build_task_execution_result(
        success=success,
        reason=reason,
        task_row=task_row,
        session=session,
        default_success_reason="transition_applied",
        execution_event=execution_event,
    )
    return result


def build_task_execution_bulk_suspend_result(
    *,
    run_id: Any,
    suspended_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    failed: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    execution_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    reason: Any = "",
    success: bool | None = None,
) -> dict[str, Any]:
    """Project run-scoped bulk suspension into its stable result shape.

    Boundary:
        Bulk suspension owns orchestration-run cancellation results. It reports
        aggregate row ids, per-task failures, and execution-event append
        evidence, while individual task/session state transitions remain owned
        by the service method and ledger events.

    Complexity:
        O(s + f + e) time and memory for suspended rows, failed task records,
        and execution-event append evidence.
    """

    normalized_run_id = str(run_id or "").strip()
    suspended_payload = [dict(row) for row in suspended_rows]
    failed_payload = [dict(item) for item in failed]
    event_payload = [dict(item) for item in execution_events]
    resolved_success = not failed_payload if success is None else bool(success)
    resolved_reason = str(reason or "").strip()
    if not resolved_reason:
        resolved_reason = "suspended" if suspended_payload else "no_active_sessions_for_run"
    return {
        "success": resolved_success,
        "reason": resolved_reason,
        "run_id": normalized_run_id,
        "suspended_count": len(suspended_payload),
        "task_ids": [str(row.get("id") or "") for row in suspended_payload],
        "failed": failed_payload,
        "execution_events": event_payload,
    }


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


def project_task_row_runtime_state(
    row: dict[str, Any],
    *,
    task_status_value: Any,
    session: TaskExecutionSession | None,
    terminal_session_superseded: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project a task row and optional execution session into runtime state.

    Boundary:
        This is the read-model counterpart to :func:`build_task_runtime_metadata`.
        It owns the augmented task-row shape consumed by runtime projections.
        Callers still decide whether a terminal session is superseded by a
        newer task-row retry authorization; this helper only applies that
        already-authoritative decision.

    Complexity:
        O(m) time and memory over task-row metadata size.
    """

    raw_status = str(row.get("status") or "").strip().lower() or str(task_status_value or "").strip().lower()
    metadata = dict(row.get("metadata") or {})
    effective_status = raw_status
    resume_state = ""
    claimed_by = str(metadata.get("claimed_by") or "").strip()
    last_claimed_by = str(metadata.get("last_claimed_by") or claimed_by).strip()
    workflow_run_id = str(metadata.get("workflow_run_id") or "").strip()

    if session is not None:
        workflow_run_id = workflow_run_id or str(session.run_id or "").strip()
        last_claimed_by = str(session.worker_id or "").strip() or last_claimed_by
        session_expired = session.status == "active" and session.is_expired(now=now or utc_now())
        if terminal_session_superseded:
            claimed_by = ""
            resume_state = ""
        elif session.status == "completed":
            effective_status = "completed"
            claimed_by = str(session.worker_id or "").strip()
        elif session.status == "failed":
            effective_status = "failed"
            claimed_by = ""
        elif session.status == "suspended" or (session.status == "active" and session_expired):
            effective_status = "pending"
            resume_state = "resumable"
            claimed_by = ""
        elif session.status == "active":
            effective_status = "in_progress"
            resume_state = "resumed" if session.resume_count > 0 else ""
            claimed_by = str(session.worker_id or "").strip()

    if raw_status == "blocked" and not resume_state:
        effective_status = "blocked"
    if raw_status in _TERMINAL_TASK_ROW_STATUSES:
        effective_status = raw_status
        if raw_status != "completed":
            claimed_by = ""
        resume_state = ""

    runtime_execution = dict(metadata.get("runtime_execution") or {})
    if session is not None:
        runtime_execution.update(session.to_dict())
    if terminal_session_superseded and session is not None:
        runtime_execution["superseded_terminal_session_status"] = str(session.status or "")
        runtime_execution["session_projection_authority"] = "row_reset_after_terminal_session"
    runtime_execution["effective_status"] = effective_status
    runtime_execution["resume_state"] = resume_state
    runtime_execution["resume_available"] = resume_state == "resumable"
    runtime_execution["raw_status"] = raw_status

    metadata["runtime_execution"] = runtime_execution
    metadata["claimed_by"] = claimed_by
    metadata["last_claimed_by"] = last_claimed_by
    metadata["resume_state"] = resume_state
    metadata["resume_available"] = resume_state == "resumable"
    if workflow_run_id:
        metadata["workflow_run_id"] = workflow_run_id

    augmented = dict(row)
    augmented["raw_status"] = raw_status
    augmented["status"] = effective_status
    augmented["metadata"] = metadata
    augmented["claimed_by"] = claimed_by
    augmented["last_claimed_by"] = last_claimed_by
    augmented["resume_state"] = resume_state
    augmented["resume_available"] = resume_state == "resumable"
    augmented["workflow_run_id"] = workflow_run_id
    if session is not None:
        augmented["session_id"] = session.session_id
        augmented["claim_attempt"] = session.attempt
        augmented["resume_count"] = session.resume_count
        augmented["lease_expires_at"] = session.lease_expires_at
        augmented["last_heartbeat_at"] = session.last_heartbeat_at
        augmented["last_error"] = session.last_error
        augmented["last_result_summary"] = session.last_result_summary
    return augmented


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
    "build_task_execution_bulk_suspend_result",
    "build_task_execution_claim_attempt",
    "build_task_execution_claim_next_result",
    "build_task_execution_claim_result",
    "build_task_execution_heartbeat_result",
    "build_task_execution_transition_result",
    "build_task_runtime_execution_event_payload",
    "build_task_runtime_metadata",
    "is_terminal_session_status",
    "is_terminal_task_row_status",
    "normalize_positive_int",
    "parse_utc_iso",
    "project_task_row_runtime_state",
    "sanitize_summary",
    "task_row_status_counts",
    "terminal_task_status_value_for_session_status",
    "utc_now",
    "utc_now_iso",
]
