from __future__ import annotations

import logging
import re
import threading
from typing import Any

from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event
from polaris.cells.runtime.task_runtime.internal.task_board import Task, TaskBoard, TaskStatus
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

from .execution_session import (
    TaskExecutionSession,
    normalize_positive_int,
    sanitize_summary,
    utc_now,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_TASK_ID_PATTERN = re.compile(r"^task-(\d+)(?:-|$)", re.IGNORECASE)
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class TaskRuntimeService:
    """Runtime task lifecycle service for the ``runtime.task_runtime`` cell.

    Responsibilities:
    - Keep the canonical runtime taskboard rows under ``runtime/tasks/*``
    - Materialize legacy orchestration tasks into canonical task rows
    - Persist execution lease/session facts under ``runtime/tasks/*``
    - Expose a stable, resumable read model for snapshot/observer consumers
    """

    def __init__(self, workspace: str, board: TaskBoard | None = None) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required for TaskRuntimeService")
        self._workspace = workspace_token
        self._board = board or TaskBoard(workspace=workspace_token)
        self._kernel_fs = KernelFileSystem(workspace_token, get_default_adapter())
        # Per-task-id locks guard the read-modify-write cycle on session files.
        self._session_locks: dict[int, threading.Lock] = {}
        self._session_locks_meta = threading.Lock()

    @property
    def workspace(self) -> str:
        return self._workspace

    @property
    def board(self) -> TaskBoard:
        return self._board

    def __getattr__(self, name: str) -> Any:
        """Temporary compatibility proxy for ongoing migration call-sites."""
        return getattr(self._board, name)

    @staticmethod
    def normalize_task_id(task_id: Any) -> int | None:
        token = str(task_id or "").strip()
        if not token:
            return None
        if token.isdigit():
            return int(token)
        match = _TASK_ID_PATTERN.match(token)
        if match:
            return int(match.group(1))
        return None

    def task_exists(self, task_id: Any) -> bool:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return False
        return self._board.get(normalized) is not None

    def create(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        return self._board.create(
            subject=subject,
            description=description,
            blocked_by=blocked_by,
            priority=priority,
            owner=owner,
            assignee=assignee,
            tags=tags,
            estimated_hours=estimated_hours,
            metadata=metadata,
        )

    def ensure_task_row(
        self,
        *,
        external_task_id: str,
        subject: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        priority: int | str = 1,
    ) -> dict[str, Any]:
        """Materialize a legacy/orchestration task into the canonical taskboard."""
        external_id = str(external_task_id or "").strip()
        if not external_id:
            raise ValueError("external_task_id is required")

        existing = self.get_task(external_id)
        if isinstance(existing, dict):
            return existing

        safe_subject = str(subject or "").strip() or external_id
        safe_description = str(description or "").strip()
        created_metadata = dict(metadata or {})
        created_metadata.setdefault("external_task_id", external_id)
        created_metadata.setdefault("source_task_id", external_id)
        created_metadata.setdefault("materialized_by", "runtime.task_runtime")
        created_metadata.setdefault("materialized_at", utc_now_iso())

        task = self.create(
            subject=safe_subject,
            description=safe_description,
            priority=priority,
            metadata=created_metadata,
        )
        row = self._augment_task_row(task.to_dict())
        self._append_execution_event(
            "materialized",
            task_row=row,
            session=None,
            details={"external_task_id": external_id},
        )
        return row

    def get(self, task_id: Any) -> Task | None:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None
        return self._board.get(normalized)

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        normalized = self.normalize_task_id(task_id)
        if normalized is not None:
            task = self._board.get(normalized)
            return self._augment_task_row(task.to_dict()) if task is not None else None

        external_id = str(task_id or "").strip()
        if not external_id:
            return None
        for task in self._board.list_all():
            row = task.to_dict()
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            if str(metadata.get("external_task_id") or "").strip() == external_id:
                return self._augment_task_row(row)
        return None

    def update(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None
        return self._board.update(
            normalized,
            status=status,
            assignee=assignee,
            owner=owner,
            metadata=metadata,
        )

    def update_task(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        owner: str | None = None,
    ) -> Task | None:
        return self.update(
            task_id,
            status=status,
            metadata=metadata,
            assignee=assignee,
            owner=owner,
        )

    def reopen(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None
        task = self._board.reopen(
            normalized,
            reason=reason,
            metadata=metadata,
        )
        if task is not None:
            session = self._read_session(normalized)
            if session is not None:
                session.mark_suspended(reason=reason or "task_reopened", resumable=True)
                self._write_session(session)
        return task

    def list_all(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        return self._board.list_all(
            status=status,
            owner=owner,
            tag=tag,
        )

    def list_task_rows(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in self._board.list_all():
            row = self._augment_task_row(task.to_dict())
            status = str(row.get("status") or "").strip().lower()
            if (not include_terminal) and status in _TERMINAL_STATUSES:
                continue
            rows.append(row)
        rows.sort(key=self._row_sort_key)
        return rows

    def select_next_task(
        self,
        *,
        requested_task_id: Any = None,
        prefer_resumable: bool = True,
    ) -> dict[str, Any] | None:
        """Return the next claimable task row, preferring resumable work."""
        requested = self.get_task(requested_task_id) if requested_task_id else None
        if isinstance(requested, dict) and self._is_row_claimable(requested):
            return requested

        rows = self.list_task_rows(include_terminal=False)
        candidates = [row for row in rows if self._is_row_claimable(row)]
        if not candidates:
            return None

        def _candidate_key(row: dict[str, Any]) -> tuple[int, int, float, int]:
            resume_state = str(row.get("resume_state") or "").strip().lower()
            resume_priority = 0 if prefer_resumable and resume_state == "resumable" else 1
            try:
                priority = -int(row.get("priority") or 0)
            except (RuntimeError, ValueError):
                # Malformed priority field - fallback to 0 (lowest priority)
                logger.debug("Task priority parse failed for task_id=%s, using 0", row.get("id"))
                priority = 0
            created_at = float(row.get("created_at") or 0.0)
            row_task_id = self.normalize_task_id(row.get("id")) or 10**9
            return (resume_priority, priority, created_at, row_task_id)

        candidates.sort(key=_candidate_key)
        return candidates[0]

    def claim_execution(
        self,
        task_id: Any,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        external_task_id: str = "",
        context_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claim a task for execution and persist a lease-backed session."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return {"success": False, "reason": "invalid_task_id"}

        task = self._board.get(normalized)
        if task is None:
            return {"success": False, "reason": "task_not_found"}

        if task.is_terminal:
            return {"success": False, "reason": "task_terminal", "task": self._augment_task_row(task.to_dict())}
        if self._task_has_unresolved_dependencies(task):
            return {"success": False, "reason": "task_blocked", "task": self._augment_task_row(task.to_dict())}

        session_lock = self._get_session_lock(normalized)
        with session_lock:
            existing_session = self._read_session(normalized)
            if (
                existing_session is not None
                and existing_session.status == "active"
                and not existing_session.is_expired(now=utc_now())
            ):
                same_owner = (
                    existing_session.worker_id == str(worker_id or "").strip()
                    and existing_session.role_id == str(role_id or "").strip()
                )
                if not same_owner:
                    return {
                        "success": False,
                        "reason": "lease_conflict",
                        "task": self._augment_task_row(task.to_dict()),
                        "session": existing_session.to_dict(),
                    }
                existing_session.renew(
                    lease_ttl_seconds=lease_ttl_seconds,
                    context_summary=context_summary,
                )
                self._write_session(existing_session)
                updated = self._board.update(
                    normalized,
                    status=TaskStatus.IN_PROGRESS,
                    assignee=str(worker_id or "").strip(),
                    metadata=self._build_runtime_metadata(
                        session=existing_session,
                        effective_status="in_progress",
                        resume_state="resumed" if existing_session.resume_count > 0 else "",
                        extra_metadata=metadata,
                    ),
                )
                row = self._augment_task_row(updated.to_dict() if updated is not None else task.to_dict())
                self._append_execution_event(
                    "claim_renewed",
                    task_row=row,
                    session=existing_session,
                    details={"selection_source": selection_source},
                )
                return {
                    "success": True,
                    "reason": "claim_renewed",
                    "task": row,
                    "session": existing_session.to_dict(),
                    "resumed": existing_session.resume_count > 0,
                    "claim_applied": True,
                }

            resume_from_previous = bool(
                existing_session is not None
                and existing_session.resumable
                and (
                    existing_session.status == "suspended"
                    or (existing_session.status == "active" and existing_session.is_expired(now=utc_now()))
                )
            )
            attempt = self._resolve_next_attempt(task, existing_session)
            resume_count = int(existing_session.resume_count + 1) if resume_from_previous and existing_session else 0

            session = TaskExecutionSession.create(
                task_id=normalized,
                role_id=role_id,
                worker_id=worker_id,
                run_id=run_id,
                lease_ttl_seconds=lease_ttl_seconds,
                attempt=attempt,
                resume_count=resume_count,
                origin="resume" if resume_from_previous else "claim",
                selection_source=selection_source,
                external_task_id=external_task_id or str(task.metadata.get("external_task_id") or "").strip(),
                context_summary=context_summary,
                metadata={
                    "previous_session_id": existing_session.session_id if existing_session is not None else "",
                },
            )
            self._write_session(session)

        updated_task = self._board.update(
            normalized,
            status=TaskStatus.IN_PROGRESS,
            assignee=str(worker_id or "").strip(),
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state="resumed" if resume_from_previous else "",
                extra_metadata=metadata,
            ),
        )
        row = self._augment_task_row(updated_task.to_dict() if updated_task is not None else task.to_dict())
        self._append_execution_event(
            "claimed",
            task_row=row,
            session=session,
            details={"selection_source": selection_source, "resumed": resume_from_previous},
        )
        return {
            "success": True,
            "reason": "claimed",
            "task": row,
            "session": session.to_dict(),
            "resumed": resume_from_previous,
            "claim_applied": True,
        }

    def heartbeat_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        lease_ttl_seconds: int = 120,
        context_summary: str = "",
    ) -> dict[str, Any]:
        """Renew an existing task lease."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return {"success": False, "reason": "invalid_task_id"}

        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return {"success": False, "reason": "session_not_found"}
            if str(session.session_id) != str(session_id or "").strip():
                return {"success": False, "reason": "session_mismatch", "session": session.to_dict()}
            if session.status != "active":
                return {"success": False, "reason": "session_not_active", "session": session.to_dict()}

            session.renew(
                lease_ttl_seconds=lease_ttl_seconds,
                context_summary=context_summary,
            )
            self._write_session(session)
        task = self._board.update(
            normalized,
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state="resumed" if session.resume_count > 0 else "",
            ),
        )
        row = self._augment_task_row(task.to_dict()) if task is not None else self.get_task(normalized)
        return {
            "success": True,
            "reason": "heartbeat_renewed",
            "task": row,
            "session": session.to_dict(),
        }

    def complete_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        result_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finalize a claimed task as completed."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return {"success": False, "reason": "invalid_task_id"}
        task = self._board.get(normalized)
        if task is None:
            return {"success": False, "reason": "task_not_found"}
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return {"success": False, "reason": "session_not_found"}
            if str(session.session_id) != str(session_id or "").strip():
                return {"success": False, "reason": "session_mismatch", "session": session.to_dict()}

            session.mark_completed(result_summary=result_summary)
            self._write_session(session)
        updated = self._board.update(
            normalized,
            status=TaskStatus.COMPLETED,
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="completed",
                resume_state="",
                extra_metadata=metadata,
            ),
        )
        row = self._augment_task_row(updated.to_dict() if updated is not None else task.to_dict())
        self._append_execution_event(
            "completed",
            task_row=row,
            session=session,
            details={"result_summary": sanitize_summary(result_summary)},
        )
        return {
            "success": True,
            "reason": "completed",
            "task": row,
            "session": session.to_dict(),
        }

    def fail_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finalize a claimed task as failed."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return {"success": False, "reason": "invalid_task_id"}
        task = self._board.get(normalized)
        if task is None:
            return {"success": False, "reason": "task_not_found"}
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return {"success": False, "reason": "session_not_found"}
            if str(session.session_id) != str(session_id or "").strip():
                return {"success": False, "reason": "session_mismatch", "session": session.to_dict()}

            session.mark_failed(error=error)
            self._write_session(session)
        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="failed",
                resume_state="",
                extra_metadata=metadata,
            ),
        )
        row = self._augment_task_row(updated.to_dict() if updated is not None else task.to_dict())
        self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={"error": sanitize_summary(error)},
        )
        return {
            "success": True,
            "reason": "failed",
            "task": row,
            "session": session.to_dict(),
        }

    def suspend_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Suspend a claimed task so it can be resumed later."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return {"success": False, "reason": "invalid_task_id"}
        task = self._board.get(normalized)
        if task is None:
            return {"success": False, "reason": "task_not_found"}
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return {"success": False, "reason": "session_not_found"}
            if str(session.session_id) != str(session_id or "").strip():
                return {"success": False, "reason": "session_mismatch", "session": session.to_dict()}

            session.mark_suspended(reason=reason, resumable=True)
            self._write_session(session)
        updated = self._board.update(
            normalized,
            status=TaskStatus.BLOCKED,
            assignee="",
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="pending",
                resume_state="resumable",
                extra_metadata=metadata,
            ),
        )
        row = self._augment_task_row(updated.to_dict() if updated is not None else task.to_dict())
        self._append_execution_event(
            "suspended",
            task_row=row,
            session=session,
            details={"reason": sanitize_summary(reason)},
        )
        return {
            "success": True,
            "reason": "suspended",
            "task": row,
            "session": session.to_dict(),
        }

    def list_ready(self) -> list[Task]:
        return self._board.list_ready()

    def get_ready_tasks(self) -> list[Task]:
        return self._board.get_ready_tasks()

    def get_stats(self) -> dict[str, Any]:
        rows = self.list_task_rows()
        stats = {
            "total": len(rows),
            "ready": 0,
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
            "cancelled": 0,
        }
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status == "pending":
                stats["pending"] += 1
                if not row.get("blocked_by"):
                    stats["ready"] += 1
            elif status == "in_progress":
                stats["in_progress"] += 1
            elif status == "completed":
                stats["completed"] += 1
            elif status == "failed":
                stats["failed"] += 1
            elif status == "blocked":
                stats["blocked"] += 1
            elif status == "cancelled":
                stats["cancelled"] += 1
        return stats

    def _row_sort_key(self, row: dict[str, Any]) -> tuple[int, str]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is not None:
            return (0, f"{task_id:010d}")
        return (1, str(row.get("id") or ""))

    def _is_row_claimable(self, row: dict[str, Any]) -> bool:
        status = str(row.get("status") or "").strip().lower()
        if status != "pending":
            return False
        blocked_by = row.get("blocked_by") if isinstance(row.get("blocked_by"), list) else row.get("blockedBy")
        return not blocked_by

    def _resolve_next_attempt(
        self,
        task: Task,
        session: TaskExecutionSession | None,
    ) -> int:
        if session is not None:
            return int(session.attempt) + 1
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        return normalize_positive_int(metadata.get("claim_attempt"), default=1)

    def _task_has_unresolved_dependencies(self, task: Task) -> bool:
        for dependency_id in list(task.blocked_by or []):
            try:
                dep_id_int = int(dependency_id)
            except ValueError:
                logger.warning("Skipping non-integer dependency_id: %r", dependency_id)
                continue
            dependency = self._board.get(dep_id_int)
            if dependency is None:
                continue
            if dependency.status != TaskStatus.COMPLETED:
                return True
        return False

    def _get_session_lock(self, task_id: int) -> threading.Lock:
        """Return the per-task session lock, creating it on demand."""
        with self._session_locks_meta:
            if task_id not in self._session_locks:
                self._session_locks[task_id] = threading.Lock()
            return self._session_locks[task_id]

    def _session_logical_path(self, task_id: int) -> str:
        return f"runtime/tasks/task_{int(task_id)}.session.json"

    def _read_session(self, task_id: int) -> TaskExecutionSession | None:
        logical_path = self._session_logical_path(task_id)
        if not self._kernel_fs.exists(logical_path):
            return None
        try:
            payload = self._kernel_fs.read_json(logical_path)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to read task runtime session %s: %s", logical_path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return TaskExecutionSession.from_dict(payload)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to parse task runtime session %s: %s", logical_path, exc)
            return None

    def _write_session(self, session: TaskExecutionSession) -> None:
        self._kernel_fs.write_json(
            self._session_logical_path(session.task_id),
            session.to_dict(),
            indent=2,
            ensure_ascii=False,
        )

    def _append_execution_event(
        self,
        event_type: str,
        *,
        task_row: dict[str, Any],
        session: TaskExecutionSession | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        event_type_str = str(event_type or "").strip().lower() or "unknown"
        payload: dict[str, Any] = {
            "event_type": event_type_str,
            "workspace": self.workspace,
            "task_id": str(task_row.get("id") or ""),
            "status": str(task_row.get("status") or ""),
            "subject": str(task_row.get("subject") or ""),
            "session_id": session.session_id if session is not None else "",
            "run_id": session.run_id if session is not None else str(task_row.get("workflow_run_id") or ""),
            "claimed_by": str(task_row.get("claimed_by") or ""),
            "resume_state": str(task_row.get("resume_state") or ""),
            "details": dict(details or {}),
            "timestamp": utc_now_iso(),
        }
        try:
            command = AppendFactEventCommandV1(
                workspace=self.workspace,
                stream="task_runtime.execution",
                event_type=event_type_str,
                payload=payload,
                source="runtime.task_runtime",
                run_id=str(payload.get("run_id") or "").strip() or None,
                task_id=str(payload.get("task_id") or "").strip() or None,
                correlation_id=str(payload.get("session_id") or "").strip() or None,
            )
            append_fact_event(command)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to append task runtime execution event: %s", exc)

    def _augment_task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is None:
            return dict(row)

        task = self._board.get(task_id)
        if task is None:
            return dict(row)

        session = self._read_session(task_id)
        raw_status = str(row.get("status") or "").strip().lower() or task.status.value
        metadata = dict(row.get("metadata") or {})
        effective_status = raw_status
        resume_state = ""
        claimed_by = str(metadata.get("claimed_by") or "").strip()
        last_claimed_by = str(metadata.get("last_claimed_by") or claimed_by).strip()
        workflow_run_id = str(metadata.get("workflow_run_id") or "").strip()

        if session is not None:
            workflow_run_id = workflow_run_id or str(session.run_id or "").strip()
            last_claimed_by = str(session.worker_id or "").strip() or last_claimed_by
            session_expired = session.status == "active" and session.is_expired(now=utc_now())
            if session.status == "completed":
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
        if raw_status in _TERMINAL_STATUSES:
            effective_status = raw_status
            if raw_status != "completed":
                claimed_by = ""
            resume_state = ""

        runtime_execution = dict(metadata.get("runtime_execution") or {})
        if session is not None:
            runtime_execution.update(session.to_dict())
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

    def _build_runtime_metadata(
        self,
        *,
        session: TaskExecutionSession,
        effective_status: str,
        resume_state: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime_execution = session.to_dict()
        runtime_execution["effective_status"] = str(effective_status or "").strip().lower() or "pending"
        runtime_execution["resume_state"] = str(resume_state or "").strip().lower()
        runtime_execution["resume_available"] = runtime_execution["resume_state"] == "resumable"
        metadata: dict[str, Any] = dict(extra_metadata or {})
        metadata["runtime_execution"] = runtime_execution
        metadata["claimed_by"] = session.worker_id if effective_status == "in_progress" else ""
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


__all__ = ["TaskRuntimeService"]
