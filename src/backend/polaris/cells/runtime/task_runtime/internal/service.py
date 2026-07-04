from __future__ import annotations

import logging
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event
from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    Task,
    TaskBoard,
    TaskStatus,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots

from .execution_session import (
    TaskExecutionSession,
    build_task_execution_bulk_suspend_result,
    build_task_execution_claim_attempt,
    build_task_execution_claim_next_result,
    build_task_execution_claim_result,
    build_task_execution_heartbeat_result,
    build_task_execution_transition_result,
    build_task_runtime_execution_event_append_result,
    build_task_runtime_execution_event_payload,
    build_task_runtime_metadata,
    is_terminal_session_status,
    is_terminal_task_row_status,
    normalize_positive_int,
    project_task_row_execution_event,
    project_task_row_runtime_state,
    sanitize_summary,
    task_row_status_counts,
    terminal_session_timestamp,
    terminal_task_status_value_for_session_status,
    utc_now,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_TASK_ID_PATTERN = re.compile(r"^task-(\d+)(?:-|$)", re.IGNORECASE)


def _terminal_task_status_for_session(status: Any) -> TaskStatus | None:
    """Adapt canonical session-terminal projection values to TaskBoard enums."""

    task_status_value = terminal_task_status_value_for_session_status(status)
    if not task_status_value:
        return None
    try:
        return TaskStatus(task_status_value)
    except ValueError:
        logger.warning("Unknown task status projected from session status: %r", task_status_value)
        return None


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

    def reset_records(self, *, keep_plan: bool = False) -> dict[str, object]:
        """Clear canonical taskboard rows and execution sessions.

        This intentionally lives in the runtime.task_runtime cell because
        ``runtime/tasks/*`` is task-runtime-owned state. Delivery-level reset
        orchestration may call this public capability, but other cells must not
        delete these files directly.
        """
        cleared_paths: list[str] = []
        failed_paths: list[str] = []

        with self._board.transaction():
            tasks_dir = self._board.tasks_dir
            tasks_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(tasks_dir.iterdir(), key=lambda item: str(item)):
                if keep_plan and child.name == "plan.json":
                    continue
                if child.name in {".max_id", ".max_id.lock"}:
                    continue
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    cleared_paths.append(str(child))
                except OSError as exc:
                    logger.warning("Failed to reset task runtime path %s: %s", child, exc)
                    failed_paths.append(str(child))

            self._board._cache.clear()
            with self._session_locks_meta:
                self._session_locks.clear()

        taskboard_event_path = Path(
            resolve_runtime_path(self._workspace, "runtime/events/taskboard.terminal.events.jsonl")
        )
        if taskboard_event_path.is_file():
            try:
                taskboard_event_path.unlink()
                cleared_paths.append(str(taskboard_event_path))
            except OSError as exc:
                logger.warning("Failed to reset taskboard event path %s: %s", taskboard_event_path, exc)
                failed_paths.append(str(taskboard_event_path))

        unique_cleared = sorted(set(cleared_paths))
        unique_failed = sorted({path for path in failed_paths if path not in set(unique_cleared)})
        return {
            "cleared_paths": unique_cleared,
            "failed_paths": unique_failed,
            "cleared_count": len(unique_cleared),
            "failed_count": len(unique_failed),
        }

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

    @staticmethod
    def _metadata_matches_external_task_id(metadata: dict[str, Any], external_id: str) -> bool:
        token = str(external_id or "").strip()
        if not token:
            return False
        for key in ("external_task_id", "pm_task_id", "source_task_id", "task_id"):
            if str(metadata.get(key) or "").strip() == token:
                return True
        return False

    def _get_task_by_external_task_id(self, external_id: str) -> dict[str, Any] | None:
        token = str(external_id or "").strip()
        if not token:
            return None
        for task in self._board.list_all():
            row = task.to_dict()
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            if self._metadata_matches_external_task_id(metadata, token):
                return self._augment_task_row(row)
        return None

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
        task, _row, _execution_event = self._create_with_execution_event(
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
        return task

    def create_task_row(
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
    ) -> dict[str, Any]:
        """Create a task and return the runtime row projection with event evidence."""

        _task, row, execution_event = self._create_with_execution_event(
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
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(execution_event,),
        )

    def _create_with_execution_event(
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
    ) -> tuple[Task, dict[str, Any], dict[str, Any]]:
        task = self._board.create(
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
        row = self._augment_task_row(task.to_dict())
        execution_event = self._append_execution_event(
            "created",
            task_row=row,
            session=None,
            details={"source": "runtime.task_runtime.create"},
        )
        return task, row, execution_event

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

        existing = self._get_task_by_external_task_id(external_id)
        if isinstance(existing, dict):
            return existing

        safe_subject = str(subject or "").strip() or external_id
        safe_description = str(description or "").strip()
        created_metadata = dict(metadata or {})
        created_metadata.setdefault("external_task_id", external_id)
        created_metadata.setdefault("source_task_id", external_id)
        created_metadata.setdefault("materialized_by", "runtime.task_runtime")
        created_metadata.setdefault("materialized_at", utc_now_iso())

        _, row, created_event = self._create_with_execution_event(
            subject=safe_subject,
            description=safe_description,
            priority=priority,
            metadata=created_metadata,
        )
        execution_event = self._append_execution_event(
            "materialized",
            task_row=row,
            session=None,
            details={"external_task_id": external_id},
        )
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(created_event, execution_event),
        )

    def get(self, task_id: Any) -> Task | None:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None
        return self._board.get(normalized)

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        external_id = str(task_id or "").strip()
        external_row = self._get_task_by_external_task_id(external_id)
        if isinstance(external_row, dict):
            return external_row

        normalized = self.normalize_task_id(task_id)
        if normalized is not None:
            task = self._board.get(normalized)
            return self._augment_task_row(task.to_dict()) if task is not None else None

        return None

    def update(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        updated, _row, _execution_event = self._update_with_execution_event(
            task_id,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
        )
        return updated

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task and return the runtime row projection with event evidence."""

        _, row, execution_event = self._update_with_execution_event(
            task_id,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
        )
        if row is None:
            return None
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(execution_event,) if execution_event is not None else (),
        )

    def _update_with_execution_event(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Task | None, dict[str, Any] | None, dict[str, Any] | None]:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None, None
        updated = self._board.update(
            normalized,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
        )
        if updated is None:
            return None, None, None
        row = self._augment_task_row(updated.to_dict())
        execution_event = self._append_execution_event(
            "updated",
            task_row=row,
            session=None,
            details={
                "status": str(status.value if isinstance(status, TaskStatus) else status or ""),
                "assignee": str(assignee or ""),
                "owner": str(owner or ""),
                "metadata_updated": metadata is not None,
            },
        )
        return updated, row, execution_event

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
        task, _row, _execution_event = self._reopen_with_execution_event(
            task_id,
            reason=reason,
            metadata=metadata,
        )
        return task

    def reopen_task_row(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Reopen a task and return the runtime row projection with event evidence."""

        _task, row, execution_event = self._reopen_with_execution_event(
            task_id,
            reason=reason,
            metadata=metadata,
        )
        if row is None:
            return None
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(execution_event,) if execution_event is not None else (),
        )

    def _reopen_with_execution_event(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Task | None, dict[str, Any] | None, dict[str, Any] | None]:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None, None
        task = self._board.reopen(
            normalized,
            reason=reason,
            metadata=metadata,
        )
        if task is None:
            return None, None, None
        session = self._read_session(normalized)
        if session is not None:
            session.mark_suspended(reason=reason or "task_reopened", resumable=True)
            self._write_session(session, allow_terminal_downgrade=True)
        row = self._augment_task_row(task.to_dict())
        execution_event = self._append_execution_event(
            "reopened",
            task_row=row,
            session=session,
            details={"reason": sanitize_summary(reason or "task_reopened")},
        )
        return task, row, execution_event

    def list_all(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.list_all is retired; use list_task_rows()")

    def list_task_rows(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        self.refresh_dependency_unblocks()
        rows: list[dict[str, Any]] = []
        for task in self._board.list_all():
            row = self._augment_task_row(task.to_dict())
            status = str(row.get("status") or "").strip().lower()
            if (not include_terminal) and is_terminal_task_row_status(status):
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
        """Return the next claimable task row, preferring resumable work.

        This is a deterministic preview API. Concurrent Director fanout must
        use ``claim_next_execution`` so selection and claim stay in one retryable
        operation.
        """
        self.refresh_dependency_unblocks()
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

    def claim_next_execution(
        self,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        prefer_resumable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically select and claim the next executable task.

        Enumerates claimable candidates in priority order and attempts to claim
        each one. If a candidate has a lease_conflict, is terminal, or is blocked,
        the next candidate is tried. This eliminates the race window between
        ``select_next_task`` and ``claim_execution``.

        Returns:
            A dict with keys:
            - success (bool): Whether a task was successfully claimed
            - task (dict | None): The claimed task row, if successful
            - session (dict | None): The execution session, if successful
            - attempts (list[dict]): Details of each claim attempt
            - reason (str): Reason for failure (if success is False)
        """
        self.refresh_dependency_unblocks()
        rows = self.list_task_rows(include_terminal=False)
        candidates = [row for row in rows if self._is_row_claimable(row)]
        if not candidates:
            return build_task_execution_claim_next_result(
                success=False,
                reason="no_claimable_tasks",
            )

        def _candidate_key(row: dict[str, Any]) -> tuple[int, int, float, int]:
            resume_state = str(row.get("resume_state") or "").strip().lower()
            resume_priority = 0 if prefer_resumable and resume_state == "resumable" else 1
            try:
                priority = -int(row.get("priority") or 0)
            except (RuntimeError, ValueError):
                logger.debug("Task priority parse failed for task_id=%s, using 0", row.get("id"))
                priority = 0
            created_at = float(row.get("created_at") or 0.0)
            row_task_id = self.normalize_task_id(row.get("id")) or 10**9
            return (resume_priority, priority, created_at, row_task_id)

        candidates.sort(key=_candidate_key)
        attempts: list[dict[str, Any]] = []

        for candidate in candidates:
            task_id = self.normalize_task_id(candidate.get("id"))
            if task_id is None:
                continue

            claim_result = self.claim_execution(
                task_id,
                worker_id=worker_id,
                role_id=role_id,
                run_id=run_id,
                lease_ttl_seconds=lease_ttl_seconds,
                selection_source=selection_source,
                metadata=metadata,
            )

            attempts.append(build_task_execution_claim_attempt(task_id=task_id, claim_result=claim_result))

            if claim_result.get("success"):
                claim_task = claim_result.get("task")
                claim_session = claim_result.get("session")
                return build_task_execution_claim_next_result(
                    success=True,
                    reason="",
                    task_row=claim_task if isinstance(claim_task, dict) else None,
                    session=claim_session if isinstance(claim_session, dict) else None,
                    attempts=attempts,
                )

            # Continue to next candidate on lease_conflict, task_terminal, task_blocked
            reason = str(claim_result.get("reason") or "").strip()
            if reason in ("lease_conflict", "task_terminal", "task_blocked"):
                continue

            # For other failures (invalid_task_id, task_not_found), also continue
            continue

        return build_task_execution_claim_next_result(
            success=False,
            reason="all_candidates_unavailable",
            attempts=attempts,
        )

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
            return build_task_execution_claim_result(success=False, reason="invalid_task_id")

        task = self._board.get(normalized)
        if task is None:
            return build_task_execution_claim_result(success=False, reason="task_not_found")

        session_lock = self._get_session_lock(normalized)
        with session_lock:
            existing_session = self._read_session(normalized)
            if existing_session is not None:
                terminal_session_status = _terminal_task_status_for_session(existing_session.status)
                if terminal_session_status is not None:
                    if self._row_authorizes_retry_over_terminal_session(task, existing_session):
                        # Deliberate retry: the row left its terminal state
                        # through the sanctioned state-machine path AFTER the
                        # session terminalised, so the row is authoritative.
                        # Rotate the stale terminal session through the
                        # explicit downgrade path and continue with the claim.
                        existing_session = self._rotate_terminal_session_for_retry(existing_session)
                    else:
                        # Stale row: the terminal session is authoritative.
                        # Reconcile the row to the terminal verdict and reject
                        # the claim; reconcile failures become structured
                        # rejection evidence, never an exception.
                        row, reconcile_error = self._apply_terminal_session_reconcile(
                            normalized,
                            session=existing_session,
                            extra_metadata=metadata,
                        )
                        if row is None:
                            row = self._augment_task_row(task.to_dict())
                        return build_task_execution_claim_result(
                            success=False,
                            reason="task_terminal",
                            task_row=row,
                            session=existing_session,
                            reconciled_from_terminal_session=not reconcile_error,
                            reconcile_error=reconcile_error,
                        )

            if task.is_terminal:
                return build_task_execution_claim_result(
                    success=False,
                    reason="task_terminal",
                    task_row=self._augment_task_row(task.to_dict()),
                )
            if self._task_has_unresolved_dependencies(task):
                return build_task_execution_claim_result(
                    success=False,
                    reason="task_blocked",
                    task_row=self._augment_task_row(task.to_dict()),
                )

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
                    return build_task_execution_claim_result(
                        success=False,
                        reason="lease_conflict",
                        task_row=self._augment_task_row(task.to_dict()),
                        session=existing_session,
                    )
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
                execution_event = self._append_execution_event(
                    "claim_renewed",
                    task_row=row,
                    session=existing_session,
                    details={"selection_source": selection_source},
                )
                return build_task_execution_claim_result(
                    success=True,
                    reason="claim_renewed",
                    task_row=row,
                    session=existing_session,
                    resumed=existing_session.resume_count > 0,
                    claim_applied=True,
                    execution_event=execution_event,
                )

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
        execution_event = self._append_execution_event(
            "claimed",
            task_row=row,
            session=session,
            details={"selection_source": selection_source, "resumed": resume_from_previous},
        )
        return build_task_execution_claim_result(
            success=True,
            reason="claimed",
            task_row=row,
            session=session,
            resumed=resume_from_previous,
            claim_applied=True,
            execution_event=execution_event,
        )

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
            return build_task_execution_heartbeat_result(success=False, reason="invalid_task_id")

        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return build_task_execution_heartbeat_result(success=False, reason="session_not_found")
            if str(session.session_id) != str(session_id or "").strip():
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_mismatch",
                    session=session,
                )
            if session.status != "active":
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_not_active",
                    session=session,
                )

            session.renew(
                lease_ttl_seconds=lease_ttl_seconds,
                context_summary=context_summary,
            )
            session_written = self._write_session(session)
            if not session_written:
                row = self._reconcile_terminal_task_row(normalized, session=session)
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_terminal_preserved",
                    task_row=row,
                    session=session,
                )
        task = self._board.update(
            normalized,
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state="resumed" if session.resume_count > 0 else "",
            ),
        )
        row = self._augment_task_row(task.to_dict()) if task is not None else self.get_task(normalized)
        event_row = row if isinstance(row, dict) else {"id": normalized, "status": "in_progress"}
        execution_event = self._append_execution_event(
            "heartbeat_renewed",
            task_row=event_row,
            session=session,
            details={
                "lease_ttl_seconds": lease_ttl_seconds,
                "context_summary": sanitize_summary(context_summary),
            },
        )
        return build_task_execution_heartbeat_result(
            success=True,
            reason="heartbeat_renewed",
            task_row=row,
            session=session,
            execution_event=execution_event,
        )

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
            return build_task_execution_transition_result(success=False, reason="invalid_task_id")
        task = self._board.get(normalized)
        if task is None:
            return build_task_execution_transition_result(success=False, reason="task_not_found")
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return build_task_execution_transition_result(success=False, reason="session_not_found")
            if str(session.session_id) != str(session_id or "").strip():
                return build_task_execution_transition_result(
                    success=False,
                    reason="session_mismatch",
                    session=session,
                )

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
        execution_event = self._append_execution_event(
            "completed",
            task_row=row,
            session=session,
            details={"result_summary": sanitize_summary(result_summary)},
        )
        return build_task_execution_transition_result(
            success=True,
            reason="completed",
            task_row=row,
            session=session,
            execution_event=execution_event,
        )

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
            return build_task_execution_transition_result(success=False, reason="invalid_task_id")
        task = self._board.get(normalized)
        if task is None:
            return build_task_execution_transition_result(success=False, reason="task_not_found")
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return build_task_execution_transition_result(success=False, reason="session_not_found")
            if str(session.session_id) != str(session_id or "").strip():
                return build_task_execution_transition_result(
                    success=False,
                    reason="session_mismatch",
                    session=session,
                )

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
        execution_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={"error": sanitize_summary(error)},
        )
        return build_task_execution_transition_result(
            success=True,
            reason="failed",
            task_row=row,
            session=session,
            execution_event=execution_event,
        )

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
            return build_task_execution_transition_result(success=False, reason="invalid_task_id")
        task = self._board.get(normalized)
        if task is None:
            return build_task_execution_transition_result(success=False, reason="task_not_found")
        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return build_task_execution_transition_result(success=False, reason="session_not_found")
            if str(session.session_id) != str(session_id or "").strip():
                return build_task_execution_transition_result(
                    success=False,
                    reason="session_mismatch",
                    session=session,
                )

            session.mark_suspended(reason=reason, resumable=True)
            session_written = self._write_session(session)
            if not session_written:
                row = self._reconcile_terminal_task_row(normalized, session=session)
                return build_task_execution_transition_result(
                    success=False,
                    reason="session_terminal_preserved",
                    task_row=row,
                    session=session,
                )
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
        execution_event = self._append_execution_event(
            "suspended",
            task_row=row,
            session=session,
            details={"reason": sanitize_summary(reason)},
        )
        return build_task_execution_transition_result(
            success=True,
            reason="suspended",
            task_row=row,
            session=session,
            execution_event=execution_event,
        )

    def suspend_active_executions_for_run(
        self,
        run_id: str,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Suspend every active task lease owned by an orchestration run.

        Factory and orchestration cancellation is not allowed to leave task
        leases active. The role kernel guard checks these leases immediately
        before tool execution; suspending here makes late LLM responses
        fail-closed instead of writing files after the run has been cancelled.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return build_task_execution_bulk_suspend_result(
                success=False,
                reason="invalid_run_id",
                run_id=normalized_run_id,
            )

        suspended_rows: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        execution_events: list[dict[str, Any]] = []
        for task in self._board.list_all():
            task_id = self.normalize_task_id(task.id)
            if task_id is None:
                continue
            session_lock = self._get_session_lock(task_id)
            with session_lock:
                session = self._read_session(task_id)
                if session is None:
                    continue
                if str(session.run_id or "").strip() != normalized_run_id:
                    continue
                if session.status != "active":
                    continue

                session.mark_suspended(reason=reason, resumable=True)
                session_written = self._write_session(session)
                if not session_written:
                    self._reconcile_terminal_task_row(task_id, session=session)
                    continue

            task_row = task.to_dict()
            existing_metadata = dict(task_row.get("metadata") or {})
            updated = self._board.update(
                task_id,
                status=TaskStatus.BLOCKED,
                assignee="",
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status="pending",
                    resume_state="resumable",
                    extra_metadata={
                        **existing_metadata,
                        **dict(metadata or {}),
                        "cancellation_run_id": normalized_run_id,
                        "cancellation_reason": str(reason or "").strip(),
                    },
                ),
            )
            if updated is None:
                failed.append({"task_id": task_id, "reason": "task_update_failed"})
                continue
            row = self._augment_task_row(updated.to_dict())
            suspended_rows.append(row)
            execution_events.append(
                self._append_execution_event(
                    "suspended",
                    task_row=row,
                    session=session,
                    details={
                        "reason": sanitize_summary(reason),
                        "run_id": normalized_run_id,
                        "source": "runtime.task_runtime.suspend_active_executions_for_run",
                    },
                )
            )

        return build_task_execution_bulk_suspend_result(
            run_id=normalized_run_id,
            suspended_rows=suspended_rows,
            failed=failed,
            execution_events=execution_events,
        )

    def list_ready(self) -> list[Task]:
        self.refresh_dependency_unblocks()
        return self._board.list_ready()

    def wait_ready(self, timeout: float | None = None) -> bool:
        self.refresh_dependency_unblocks()
        return self._board.wait_ready(timeout=timeout)

    def add_ready_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._board.add_ready_listener(listener)

    def get_ready_tasks(self) -> list[Task]:
        self.refresh_dependency_unblocks()
        return self._board.get_ready_tasks()

    def get_stats(self) -> dict[str, Any]:
        return task_row_status_counts(self.list_task_rows())

    def refresh_dependency_unblocks(self) -> dict[str, Any]:
        """Normalize stale BLOCKED rows whose dependencies are now complete."""

        changed: list[int] = []
        inspected = 0
        tasks = self._board.list_all()
        status_by_id = {int(task.id): task.status for task in tasks}
        for task in tasks:
            inspected += 1
            if task.status != TaskStatus.BLOCKED:
                continue

            explicit_blockers = self._active_dependency_ids(task.blocked_by, status_by_id)
            if explicit_blockers:
                if explicit_blockers != list(task.blocked_by or []):
                    self._board.update(int(task.id), blocked_by=explicit_blockers)
                continue

            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            resolved_dependencies = self._metadata_dependency_task_ids(metadata)
            if resolved_dependencies and self._active_dependency_ids(resolved_dependencies, status_by_id):
                continue

            updated = self._board.update(int(task.id), status=TaskStatus.PENDING, blocked_by=[])
            if updated is not None:
                changed.append(int(task.id))

        return {
            "inspected_count": inspected,
            "unblocked_count": len(changed),
            "unblocked_task_ids": changed,
        }

    @staticmethod
    def _active_dependency_ids(dependency_ids: list[int], status_by_id: dict[int, TaskStatus]) -> list[int]:
        active: list[int] = []
        for dependency_id in dependency_ids:
            try:
                normalized = int(dependency_id)
            except (TypeError, ValueError):
                continue
            if status_by_id.get(normalized) != TaskStatus.COMPLETED:
                active.append(normalized)
        return active

    @staticmethod
    def _metadata_dependency_task_ids(metadata: dict[str, Any]) -> list[int]:
        for key in ("resolved_depends_on_task_ids", "depends_on_task_ids"):
            raw = metadata.get(key)
            if not isinstance(raw, list):
                continue
            result: list[int] = []
            for item in raw:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value not in result:
                    result.append(value)
            if result:
                return result
        return []

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
                return True
            dependency = self._board.get(dep_id_int)
            if dependency is None:
                return True
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

    def _write_session(
        self,
        session: TaskExecutionSession,
        *,
        allow_terminal_downgrade: bool = False,
    ) -> bool:
        if not allow_terminal_downgrade and not is_terminal_session_status(session.status):
            terminal_session = self._find_terminal_session_snapshot(session)
            if terminal_session is not None:
                self._copy_session_state(session, terminal_session)
                self._kernel_fs.write_json_atomic(
                    self._session_logical_path(session.task_id),
                    terminal_session.to_dict(),
                    indent=2,
                    ensure_ascii=False,
                )
                return False
        self._kernel_fs.write_json_atomic(
            self._session_logical_path(session.task_id),
            session.to_dict(),
            indent=2,
            ensure_ascii=False,
        )
        return True

    def _find_terminal_session_snapshot(
        self,
        incoming: TaskExecutionSession,
    ) -> TaskExecutionSession | None:
        disk_session = self._read_session(incoming.task_id)
        if self._same_terminal_session(disk_session, incoming):
            return disk_session

        task = self._board.get(incoming.task_id)
        metadata = task.metadata if task is not None and isinstance(task.metadata, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution") if isinstance(metadata, dict) else None
        if isinstance(runtime_execution_raw, dict):
            try:
                metadata_session = TaskExecutionSession.from_dict(runtime_execution_raw)
            except (TypeError, ValueError):
                metadata_session = None
            if self._same_terminal_session(metadata_session, incoming):
                return metadata_session
        return None

    @staticmethod
    def _same_terminal_session(
        candidate: TaskExecutionSession | None,
        incoming: TaskExecutionSession,
    ) -> bool:
        if candidate is None:
            return False
        return (
            str(candidate.session_id or "").strip() == str(incoming.session_id or "").strip()
            and is_terminal_session_status(candidate.status)
        )

    @staticmethod
    def _copy_session_state(target: TaskExecutionSession, source: TaskExecutionSession) -> None:
        target.status = source.status
        target.claimed_at = source.claimed_at
        target.last_heartbeat_at = source.last_heartbeat_at
        target.lease_expires_at = source.lease_expires_at
        target.attempt = source.attempt
        target.resume_count = source.resume_count
        target.resumable = source.resumable
        target.origin = source.origin
        target.selection_source = source.selection_source
        target.external_task_id = source.external_task_id
        target.context_summary = source.context_summary
        target.last_error = source.last_error
        target.last_result_summary = source.last_result_summary
        target.released_at = source.released_at
        target.metadata = dict(source.metadata)

    def _reconcile_terminal_task_row(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row, _reconcile_error = self._apply_terminal_session_reconcile(
            task_id,
            session=session,
            extra_metadata=metadata,
        )
        return row

    def _apply_terminal_session_reconcile(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """Project a terminal session onto its task row without ever raising.

        Returns ``(row, error_code)``. ``error_code`` is empty when the row now
        reflects the terminal session (or already did); otherwise it is a
        structured token describing why reconciliation was rejected, and the
        row is returned unchanged. Board transition validation failures are
        recorded, never propagated, so lease/claim paths cannot crash on a
        stale row shape.
        """
        terminal_status = _terminal_task_status_for_session(session.status)
        if terminal_status is None:
            task = self._board.get(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), ""
        runtime_metadata = self._build_runtime_metadata(
            session=session,
            effective_status=terminal_status.value,
            resume_state="",
            extra_metadata=extra_metadata,
        )
        try:
            updated = self._board.update(task_id, status=terminal_status, metadata=runtime_metadata)
        except InvalidTaskStateTransitionError:
            task = self._board.get(task_id)
            if task is None:
                return None, "task_not_found"
            if task.is_terminal:
                # Never rewrite one terminal verdict with another here:
                # reopen is the only sanctioned terminal-downgrade path.
                logger.warning(
                    "Task %s row is terminal %r but session %s is terminal %r; keeping row verdict",
                    task_id,
                    task.status.value,
                    session.session_id,
                    terminal_status.value,
                )
                return self._augment_task_row(task.to_dict()), "terminal_row_conflict"
            try:
                forced = self._board.reconcile_terminal_status(
                    task_id,
                    terminal_status,
                    result_summary=sanitize_summary(session.last_result_summary or session.last_error),
                )
            except InvalidTaskStateTransitionError as exc:
                logger.warning(
                    "Task %s terminal reconcile to %r rejected: %s",
                    task_id,
                    terminal_status.value,
                    exc,
                )
                return self._augment_task_row(task.to_dict()), "terminal_reconcile_rejected"
            if forced is None:
                return None, "task_not_found"
            updated = self._board.update(task_id, metadata=runtime_metadata) or forced
        if updated is None:
            task = self._board.get(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), ""
        return self._augment_task_row(updated.to_dict()), ""

    def _row_authorizes_retry_over_terminal_session(
        self,
        task: Task,
        session: TaskExecutionSession,
    ) -> bool:
        """Return True when a non-terminal row supersedes a terminal session.

        A row only wins over terminal session evidence when it left its
        terminal state through the sanctioned state-machine paths
        (``TaskBoard.update_status`` / ``TaskBoard.reopen`` stamp
        ``metadata.terminal_reset_at``) *after* the session reached its
        terminal state. Anything else is a stale row and the terminal session
        stays authoritative, so a genuinely completed/failed task cannot be
        re-claimed through a stale byte-level row rewrite.
        """
        if task.is_terminal:
            return False
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        raw_reset_at = metadata.get("terminal_reset_at")
        if not isinstance(raw_reset_at, (int, float, str)) or isinstance(raw_reset_at, bool):
            return False
        try:
            reset_at = float(raw_reset_at)
        except ValueError:
            return False
        if reset_at <= 0.0:
            return False
        terminal_at = terminal_session_timestamp(session)
        if terminal_at is None:
            # Fail closed: without a trustworthy terminal timestamp the
            # terminal session evidence stays authoritative.
            return False
        return reset_at > terminal_at

    def _rotate_terminal_session_for_retry(self, session: TaskExecutionSession) -> TaskExecutionSession:
        """Rotate a superseded terminal session via the explicit downgrade path.

        The task row deliberately left its terminal state for a retry, so the
        stale terminal session must not keep vetoing claims. Suspending it
        with ``allow_terminal_downgrade=True`` mirrors what ``reopen`` does and
        keeps the terminal-monotonic write guard intact; ``resumable=False``
        makes the retry a fresh attempt instead of a resume.
        """
        session.metadata["rotated_from_terminal_status"] = str(session.status or "")
        session.metadata["rotated_reason"] = "deliberate_row_reset_retry"
        session.mark_suspended(reason="terminal_session_rotated_for_deliberate_retry", resumable=False)
        self._write_session(session, allow_terminal_downgrade=True)
        return session

    def _append_execution_event(
        self,
        event_type: str,
        *,
        task_row: dict[str, Any],
        session: TaskExecutionSession | None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = build_task_runtime_execution_event_payload(
            event_type=event_type,
            workspace=self.workspace,
            task_row=task_row,
            session=session,
            details=details,
        )
        event_type_str = str(payload.get("event_type") or "unknown")
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
            appended = append_fact_event(command)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to append task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                append_error=str(exc),
            )
        payload["fact_event_id"] = appended.event_id
        payload["fact_stream"] = appended.stream
        payload["fact_storage_path"] = appended.storage_path
        try:
            self._publish_factory_execution_event(payload)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to publish task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                fact_event_id=appended.event_id,
                fact_stream=appended.stream,
                fact_storage_path=appended.storage_path,
                publish_error=str(exc),
            )
        return build_task_runtime_execution_event_append_result(
            event_type=event_type_str,
            fact_event_id=appended.event_id,
            fact_stream=appended.stream,
            fact_storage_path=appended.storage_path,
            published=True,
        )

    def _publish_factory_execution_event(self, payload: dict[str, Any]) -> bool:
        factory_run_id = str(payload.get("factory_run_id") or "").strip()
        if not factory_run_id:
            return False
        try:
            roots = resolve_storage_roots(self.workspace)
            workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
            if not workspace_key:
                return False
            from polaris.infrastructure.log_pipeline.jetstream_publisher import (
                get_log_jetstream_publisher,
            )

            event_payload = dict(payload)
            director_run_id = str(event_payload.get("run_id") or "").strip()
            if director_run_id and director_run_id != factory_run_id:
                event_payload["director_run_id"] = director_run_id
            event_payload["type"] = "task_runtime_execution"
            event_payload["stage"] = "director_dispatch"
            event_payload["message"] = (
                f"Director task {event_payload.get('task_id') or '<unknown>'} "
                f"{event_payload.get('event_type') or 'updated'}"
            )
            envelope = {
                "schema_version": "runtime.v2",
                "event_id": f"task-runtime-{uuid.uuid4().hex[:12]}",
                "workspace_key": workspace_key,
                "run_id": factory_run_id,
                "channel": f"event.factory:{factory_run_id}",
                "kind": "task_runtime_execution",
                "ts": event_payload.get("timestamp") or utc_now_iso(),
                "cursor": 0,
                "trace_id": None,
                "payload": event_payload,
                "meta": {"source": "runtime.task_runtime"},
            }
            return get_log_jetstream_publisher().publish(
                subject=f"hp.runtime.{workspace_key}.event.factory.{factory_run_id}",
                payload=envelope,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Task runtime factory progress publish failed: %s", exc)
            return False

    def _augment_task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is None:
            return dict(row)

        task = self._board.get(task_id)
        if task is None:
            return dict(row)

        session = self._read_session(task_id)
        terminal_session_superseded = False
        if session is not None:
            terminal_session_superseded = (
                is_terminal_session_status(session.status)
                and self._row_authorizes_retry_over_terminal_session(task, session)
            )
        return project_task_row_runtime_state(
            row,
            task_status_value=task.status.value,
            session=session,
            terminal_session_superseded=terminal_session_superseded,
        )

    def _build_runtime_metadata(
        self,
        *,
        session: TaskExecutionSession,
        effective_status: str,
        resume_state: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_task_runtime_metadata(
            session=session,
            effective_status=effective_status,
            resume_state=resume_state,
            extra_metadata=extra_metadata,
        )


def reset_runtime_task_records(workspace: str) -> dict[str, object]:
    """Clear runtime taskboard state through the owning cell service."""
    return TaskRuntimeService(workspace).reset_records()


__all__ = ["TaskRuntimeService", "reset_runtime_task_records"]
