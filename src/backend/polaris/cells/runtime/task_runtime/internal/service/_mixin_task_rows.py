"""Collaborator mixin for TaskRuntimeService (_mixin_task_rows)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Callable, Mapping

from polaris.cells.events.fact_stream.public.contracts import (
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
)
from polaris.cells.runtime.task_runtime.internal.task_board import (
    Task,
    TaskStatus,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_STREAM_V1,
    QuerySameTaskLocalReworkAuthorizationV1,
    SameTaskLocalReworkAuthorizationQueryResultV1,
)

from ..directed_effect_operation import (
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from ..execution_session import (
    _coerce_fact_event_seq,
    is_terminal_session_status,
    is_terminal_task_row_status,
    project_task_row_execution_event,
    project_task_row_from_execution_fact_payload,
    sanitize_summary,
)
from ._helpers import (
    _is_execution_task_row_update_status,
    _is_terminal_task_row_update_status,
    _raise_retired_entity_api,
    logger,
)
from ._late_bindings import (
    query_fact_events,
    query_fact_stream_head,
    utc_now,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        ObservableTaskRowsProjectionV1,
    )


_EXECUTION_FACT_ROWS_PROJECTION_CACHE: ContextVar[dict[tuple[str, int], tuple[dict[str, Any], ...]] | None] = (
    ContextVar("task_runtime_execution_fact_rows_projection_cache", default=None)
)

# Cross-call projection cache, keyed by the durable FactStream head.  Factory
# polling creates a fresh TaskRuntimeService on every observation, so the
# ContextVar above only deduplicates reads *inside* one projection composition.
# Without this head-bound cache a 40 MiB L1-04 execution stream was parsed on
# every 100 ms claimability poll, keeping the backend event-loop thread at 100%
# CPU.  The cheap descriptor-bound head query invalidates the cache immediately
# after any append, including appends from another process.
_EXECUTION_FACT_ROWS_HEAD_CACHE_MAX = 16
_EXECUTION_FACT_ROWS_HEAD_CACHE: OrderedDict[tuple[str, int, int], tuple[dict[str, Any], ...]] = OrderedDict()
_EXECUTION_FACT_ROWS_HEAD_CACHE_LOCK = threading.RLock()
# Latest-per-task snapshot keyed by workspace.  The 500-event tail window is
# only a query page size; Factory cutover treats this projection as complete
# coverage.  A long rematerialize stream (L2-12: 1645 events) otherwise drops
# the oldest still-present file rows (101-103) and fail-closes director_dispatch.
_EXECUTION_FACT_ROWS_COMPLETE_CACHE_MAX = 16
_EXECUTION_FACT_ROWS_COMPLETE_CACHE: OrderedDict[str, tuple[int, dict[str, dict[str, Any]]]] = OrderedDict()


class _TaskRowsMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

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
        if _is_terminal_task_row_update_status(status):
            raise RuntimeError(
                "terminal_task_status_requires_task_runtime_owner_transition:"
                f"{str(status.value if isinstance(status, TaskStatus) else status).strip().lower()}"
            )
        if _is_execution_task_row_update_status(status):
            raise RuntimeError(
                "execution_task_status_requires_task_runtime_owner_transition:"
                f"{str(status.value if isinstance(status, TaskStatus) else status).strip().lower()}"
            )
        updated = self._board.update(
            normalized,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
            allow_dependency_status=True,
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
        _raise_retired_entity_api("update_task", "update_task_row")

    def reopen(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        _raise_retired_entity_api("reopen", "reopen_task_row")

    def reopen_task_row(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Reopen a task and return the runtime row projection with event evidence."""

        _task, row, execution_event, downstream_events, blocker = self._reopen_with_execution_event(
            task_id,
            reason=reason,
            metadata=metadata,
        )
        if blocker is not None:
            normalized = self.normalize_task_id(task_id)
            if normalized is None:
                return None
            return self._directed_effect_inactive_block_record(normalized, blocker)
        if row is None:
            return None
        execution_events = ((execution_event,) if execution_event is not None else ()) + tuple(downstream_events)
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=execution_events,
        )

    def fail_task_row_after_rework_exhausted(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "task_rework",
    ) -> dict[str, Any] | None:
        """Fail a terminal task after sanctioned rework retries are exhausted.

        This is the owner-cell transition for QA or task-boundary flows that
        first reopen a completed task for rework accounting, then determine
        that no more retries are allowed.  Callers must not compose this from
        ``reopen_task_row()`` plus ``update_task_row(status="failed")`` because
        that second step would be a sessionless terminal row update.

        Complexity:
            O(d) time and memory for dependency reblock projection, where d is
            the number of downstream rows blocked by the task.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None

        _task, _reopened_row, reopened_event, downstream_events, blocker = self._reopen_with_execution_event(
            normalized,
            reason=reason,
            metadata=metadata,
        )
        if blocker is not None:
            return self._directed_effect_inactive_block_record(normalized, blocker)
        if _reopened_row is None:
            return None

        session = self._read_session(normalized)
        failure_reason = sanitize_summary(reason or "rework_retry_exhausted")
        if session is not None:
            session.mark_failed(error=failure_reason)
            self._write_session(session, allow_terminal_downgrade=True)

        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        failed_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={
                "reason": failure_reason,
                "source": sanitize_summary(source or "task_rework"),
                "rework_exhausted": True,
            },
        )
        execution_events = tuple(
            event for event in (reopened_event, *downstream_events, failed_event) if event is not None
        )
        return project_task_row_execution_event(
            row,
            failed_event,
            execution_events=execution_events,
        )

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: Any,
        reason: str = "duplicate_task",
        metadata: dict[str, Any] | None = None,
        source: str = "task_deduplication",
    ) -> dict[str, Any] | None:
        """Cancel a duplicate task row through the task-runtime owner.

        PM planning can discover duplicate pending task contracts after rows
        already exist.  The dedupe decision is row lifecycle metadata, not an
        execution result, but entering a terminal row state must still be owned
        by ``TaskRuntimeService`` so observers receive a first-class
        ``cancelled`` execution fact rather than a generic ``updated`` event.

        Complexity:
            O(1) task-row/session work for the target duplicate row.
        """

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None

        cancel_reason = sanitize_summary(reason or "duplicate_task")
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return self._directed_effect_inactive_block_record(
                        normalized,
                        pre_barrier,
                    )
            if session is not None and not is_terminal_session_status(session.status):
                session.mark_suspended(reason=cancel_reason, resumable=False)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )

        merged_metadata = {
            "dedup_merged_into": primary_task_id,
            "dedup_reason": cancel_reason,
            "dedup_source": sanitize_summary(source or "task_deduplication"),
        }
        merged_metadata.update(dict(metadata or {}))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="cancelled",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.CANCELLED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        cancelled_event = self._append_execution_event(
            "cancelled",
            task_row=row,
            session=session,
            details={
                "reason": cancel_reason,
                "source": sanitize_summary(source or "task_deduplication"),
                "dedup_merged_into": str(primary_task_id or "").strip(),
            },
        )
        return project_task_row_execution_event(
            row,
            cancelled_event,
            execution_events=(cancelled_event,) if cancelled_event is not None else (),
        )

    def cancel_task_row_for_factory_abort(
        self,
        task_id: Any,
        *,
        factory_run_id: str,
        reason: str = "factory_run_failed",
        metadata: dict[str, Any] | None = None,
        source: str = "factory_terminal_drain",
    ) -> dict[str, Any] | None:
        """Cancel one open task because its Factory orchestration run aborted.

        Early Factory failures (provider quota at CE, stage fail-closed before
        Director) leave PM-created rows open. Settlement barrier treats those
        open lifecycles as ``lifecycle_open`` and refuses workspace lease
        release. This owner-cell transition appends a first-class ``cancelled``
        execution fact so the barrier can close while preserving failure
        evidence. Active non-expired sessions are refused so live Director
        children keep their settle-first contract.

        Complexity:
            O(1) task-row/session work for the target row.
        """

        authority = str(factory_run_id or "").strip()
        if not authority:
            raise ValueError("factory_run_id must be a non-empty string")

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None
        existing_status = task.status.value if isinstance(task.status, TaskStatus) else task.status
        if is_terminal_task_row_status(existing_status):
            return {
                "ok": True,
                "already_terminal": True,
                "task_id": str(normalized),
                "status": str(existing_status or "").strip().lower(),
            }

        cancel_reason = sanitize_summary(reason or "factory_run_failed")
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None and session.status == "active" and not session.is_expired(now=utc_now()):
                return {
                    "ok": False,
                    "code": "factory_abort_active_session",
                    "task_id": str(normalized),
                    "session_id": session.session_id,
                    "reason": "active execution session must settle before factory abort cancel",
                }
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return self._directed_effect_inactive_block_record(
                        normalized,
                        pre_barrier,
                    )
            if session is not None and not is_terminal_session_status(session.status):
                session.mark_suspended(reason=cancel_reason, resumable=False)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )

        merged_metadata = {
            "factory_abort_reason": cancel_reason,
            "factory_abort_source": sanitize_summary(source or "factory_terminal_drain"),
            "factory_run_id": authority,
        }
        merged_metadata.update(dict(metadata or {}))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="cancelled",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.CANCELLED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        cancelled_event = self._append_execution_event(
            "cancelled",
            task_row=row,
            session=session,
            details={
                "reason": cancel_reason,
                "source": sanitize_summary(source or "factory_terminal_drain"),
                "factory_run_id": authority,
                "factory_abort": True,
            },
        )
        return project_task_row_execution_event(
            row,
            cancelled_event,
            execution_events=(cancelled_event,) if cancelled_event is not None else (),
        )

    def force_fail_active_session_for_factory_abort(
        self,
        task_id: Any,
        *,
        factory_run_id: str,
        reason: str = "factory_run_failed",
        source: str = "factory_terminal_drain",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Force-fail one still-active TaskRuntime session for factory closeout.

        Director settlement-barrier timeouts leave an owned ``active`` session
        that normal cancel refuses (``factory_abort_active_session``). Once the
        Factory run is already FAILED/CANCELLED, those orphaned sessions must
        be force-failed so child-session settlement and lease release can
        complete. Foreign or DEO-gated sessions remain fail-closed.

        Complexity:
            O(1) task-row/session work for the target row.
        """

        authority = str(factory_run_id or "").strip()
        if not authority:
            raise ValueError("factory_run_id must be a non-empty string")

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None
        existing_status = task.status.value if isinstance(task.status, TaskStatus) else task.status
        if is_terminal_task_row_status(existing_status):
            return {
                "ok": True,
                "already_terminal": True,
                "task_id": str(normalized),
                "status": str(existing_status or "").strip().lower(),
            }

        failure_reason = sanitize_summary(reason or "factory_run_failed")
        source_text = sanitize_summary(source or "factory_terminal_drain")
        session = None
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is None:
                # No session — fail the open row without a session record.
                pass
            else:
                session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
                task_metadata = task.metadata if isinstance(getattr(task, "metadata", None), Mapping) else {}
                # Ownership: task/session factory_run_id first. session.run_id is the
                # Director child workflow id (director-*), NOT the Factory authority.
                owned_factory_run_id = str(
                    task_metadata.get("factory_run_id") or session_metadata.get("factory_run_id") or ""
                ).strip()
                if owned_factory_run_id and owned_factory_run_id != authority:
                    return {
                        "ok": False,
                        "code": "factory_abort_foreign_session",
                        "task_id": str(normalized),
                        "session_id": session.session_id,
                        "existing_factory_run_id": owned_factory_run_id,
                        "requested_factory_run_id": authority,
                    }
                # Force path: skip inactive DEO pre-barrier. Factory terminal
                # closeout owns authority; residual DEO receipts remain ledger
                # evidence, not lease-pin forever.
                if not is_terminal_session_status(session.status):
                    session.mark_failed(error=failure_reason)
                    self._write_session_locked(
                        session,
                        allow_terminal_downgrade=True,
                    )

        merged_metadata = {
            "factory_abort_reason": failure_reason,
            "factory_abort_source": source_text,
            "factory_run_id": authority,
            "factory_abort_force_active_session": True,
        }
        merged_metadata.update(dict(metadata or {}))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="failed",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        failed_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={
                "reason": failure_reason,
                "source": source_text,
                "factory_run_id": authority,
                "factory_abort": True,
                "factory_abort_force_active_session": True,
            },
        )
        return project_task_row_execution_event(
            row,
            failed_event,
            execution_events=(failed_event,) if failed_event is not None else (),
        )

    def terminalize_open_tasks_for_factory_abort(
        self,
        *,
        factory_run_id: str,
        reason: str = "factory_run_failed",
        source: str = "factory_terminal_drain",
        force_active_sessions: bool = False,
    ) -> dict[str, object]:
        """Cancel all open TaskRuntime rows owned by a terminal Factory run.

        Used by Factory terminal drain when the Run Ledger settlement barrier
        is blocked solely by open (never-dispatched) task lifecycles after the
        orchestration session has already failed or been cancelled. Active
        sessions are not force-cancelled unless ``force_active_sessions`` is
        True (FAILED/CANCELLED factory drain after Director timeout; R64).

        Complexity:
            O(n) over observable rows for the factory_run_id.
        """

        authority = str(factory_run_id or "").strip()
        if not authority:
            raise ValueError("factory_run_id must be a non-empty string")
        reason_text = sanitize_summary(reason or "factory_run_failed")
        source_text = sanitize_summary(source or "factory_terminal_drain")

        projection = self.query_observable_task_rows_projection()
        rows = projection.rows_for_factory_run(authority)
        terminalized: list[str] = []
        already_terminal: list[str] = []
        blocked_active: list[dict[str, Any]] = []
        force_failed_active: list[str] = []
        failed: list[dict[str, Any]] = []

        for row_source in rows:
            row = dict(row_source)
            task_id = self.normalize_task_id(row.get("id") or row.get("task_id"))
            if task_id is None:
                continue
            status_token = str(row.get("status") or row.get("execution_state") or "").strip().lower()
            if is_terminal_task_row_status(status_token):
                already_terminal.append(str(task_id))
                continue
            result = self.cancel_task_row_for_factory_abort(
                task_id,
                factory_run_id=authority,
                reason=reason_text,
                source=source_text,
            )
            if result is None:
                failed.append({"task_id": str(task_id), "code": "task_row_missing_or_update_failed"})
                continue
            if result.get("already_terminal") is True:
                already_terminal.append(str(task_id))
                continue
            if result.get("ok") is False:
                # Live L2-12: an expired Director session still looked
                # "inactive" to cancel, so DEO pre-barrier returned
                # settlement_parent_close_required and force_fail never ran.
                # Factory FAILED/CANCELLED closeout must force-fail that
                # orphan the same way as a still-active session.
                force_code = str(result.get("code") or "")
                if force_active_sessions and force_code in {
                    "factory_abort_active_session",
                    "settlement_parent_close_required",
                    "settlement_parent_close_proof_required",
                    "settlement_parent_registry_invalid",
                    "settlement_parent_registry_unavailable",
                    "settlement_directed_effect_unresolved",
                    "settlement_effect_outcome_conflict",
                    "settlement_terminal_intent_conflict",
                    "settlement_parent_close_failed",
                }:
                    forced = self.force_fail_active_session_for_factory_abort(
                        task_id,
                        factory_run_id=authority,
                        reason=reason_text,
                        source=source_text,
                    )
                    if forced is None:
                        failed.append(
                            {
                                "task_id": str(task_id),
                                "code": "factory_abort_force_active_failed",
                            }
                        )
                        continue
                    if forced.get("ok") is False:
                        blocked_active.append(dict(forced))
                        continue
                    force_failed_active.append(str(task_id))
                    terminalized.append(str(task_id))
                    continue
                # Active session (no force), directed-effect pre-barrier, or other owner block.
                blocked_active.append(dict(result))
                continue
            terminalized.append(str(task_id))

        return {
            "ok": not blocked_active and not failed,
            "code": (
                "factory_task_runtime_abort_completed"
                if not blocked_active and not failed
                else "factory_task_runtime_abort_incomplete"
            ),
            "factory_run_id": authority,
            "reason": reason_text,
            "source": source_text,
            "force_active_sessions": bool(force_active_sessions),
            "force_failed_active_task_ids": force_failed_active,
            "force_failed_active_count": len(force_failed_active),
            "terminalized_task_ids": terminalized,
            "terminalized_count": len(terminalized),
            "already_terminal_task_ids": already_terminal,
            "already_terminal_count": len(already_terminal),
            "blocked_active": blocked_active,
            "blocked_active_count": len(blocked_active),
            "failed": failed,
            "failed_count": len(failed),
            "observable_row_count": len(rows),
        }

    def fail_task_row_from_role_adapter(
        self,
        task_id: Any,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        role_id: str = "",
        source: str = "role_adapter",
        failure_class: str = "",
    ) -> dict[str, Any] | None:
        """Fail a task row because its owning role adapter failed.

        Role adapters may fail before a Director-style execution lease exists,
        for example PM contract validation or PM runtime exceptions.  Those
        terminal row transitions still belong to ``TaskRuntimeService`` so the
        failure appears as a first-class ``task_runtime.execution`` fact rather
        than a generic ``updated`` row event.

        Complexity:
            O(1) task-row/session work for the target row.
        """

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None

        failure_reason = sanitize_summary(reason or "role_adapter_failed")
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return self._directed_effect_inactive_block_record(
                        normalized,
                        pre_barrier,
                    )
            if session is not None and not is_terminal_session_status(session.status):
                session.mark_failed(error=failure_reason)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )

        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("role_adapter_failure_reason", failure_reason)
        if role_id:
            merged_metadata.setdefault("role_adapter_failure_role", sanitize_summary(role_id))
        if failure_class:
            merged_metadata.setdefault("role_adapter_failure_class", sanitize_summary(failure_class))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="failed",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        failed_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={
                "reason": failure_reason,
                "source": sanitize_summary(source or "role_adapter"),
                "role": sanitize_summary(role_id),
                "failure_class": sanitize_summary(failure_class),
            },
        )
        return project_task_row_execution_event(
            row,
            failed_event,
            execution_events=(failed_event,) if failed_event is not None else (),
        )

    def _reopen_with_execution_event(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[
        Task | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        list[dict[str, Any]],
        DirectedEffectSettlementPreBarrierVerdictV1 | None,
    ]:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None, None, [], None
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return None, None, None, [], pre_barrier
            if session is not None:
                session.mark_suspended(reason=reason or "task_reopened", resumable=True)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )
        task = self._board.reopen(
            normalized,
            reason=reason,
            metadata=metadata,
            allow_terminal_reopen=True,
        )
        if task is None:
            return None, None, None, [], None
        row = self._augment_task_row(task.to_dict())
        execution_event = self._append_execution_event(
            "reopened",
            task_row=row,
            session=session,
            details={"reason": sanitize_summary(reason or "task_reopened")},
        )
        downstream_events = self._apply_reopen_downstream_reblocks(
            reopened_task_id=normalized,
            dependent_ids=self._row_blocks_ids(row),
        )
        return task, row, execution_event, downstream_events, None

    def list_all(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.list_all is retired; use list_task_rows()")

    def list_task_rows(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        """Return task rows through the execution compatibility path.

        Boundary:
            This method is a compatibility entrypoint, not the read-only
            observable task-row SSoT for status, UI, selection, or read-model
            consumers. It calls ``refresh_dependency_unblocks()`` before
            reading rows, so observation-only callers must use
            ``list_observable_task_rows()`` or a more specific projection helper
            when they need a side-effect-free task projection.

        Use this method only for execution/worker compatibility paths that
        require refresh-before-read behavior. Row construction still delegates
        to ``_list_file_task_rows(include_terminal=...)`` so the legacy
        file-backed filtering, sorting, and runtime augmentation semantics stay
        centralized there.
        """

        self.refresh_dependency_unblocks()
        return self._list_file_task_rows(include_terminal=include_terminal)

    def _list_file_task_rows(
        self,
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        """Return file-backed task rows without triggering ``refresh_dependency_unblocks``.

        This helper is the recursion-free source of truth for file-backed row
        projection: it preserves the existing ``include_terminal`` filtering and
        sort behaviour that ``list_task_rows`` relied on, while letting
        ``list_observable_task_rows`` and ``refresh_dependency_unblocks`` skip
        the recursive refresh side effect. Callers that already hold the
        execution-session file lock may disable runtime-state augmentation to
        avoid re-entering ``_read_session()`` from the same lock scope.

        Complexity:
            O(t log t) time where t is the number of file-backed rows.
        """

        rows: list[dict[str, Any]] = []
        for task in self._list_file_task_entities():
            row = task.to_dict()
            if augment_runtime_state:
                row = self._augment_task_row(row)
            status = str(row.get("status") or "").strip().lower()
            if (not include_terminal) and is_terminal_task_row_status(status):
                continue
            rows.append(row)
        rows.sort(key=self._row_sort_key)
        return rows

    def _transitional_task_row_read_model_rows(self) -> list[dict[str, Any]]:
        """Load transitional task-row read-model rows.

        This transitional read model loader combines file-backed TaskBoard rows
        with append-only ``task_runtime.execution`` fact rows and returns their
        observable projection. It is not a mutation, claim, or write API.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        return self._project_observable_task_rows(file_rows, fact_rows)

    def _fact_only_task_row_read_model_rows(self) -> list[dict[str, Any]]:
        """Load fact-only task-row read-model rows.

        Boundary:
            This helper reads only append-only ``task_runtime.execution`` fact
            rows and projects them through the shared observable row projector.
            It must not call file row/entity loaders, dependency refresh APIs,
            mutation APIs, claim/selection APIs, or session writers.

        Complexity:
            O(f) time and memory over latest fact rows.
        """

        fact_rows = self.list_task_rows_from_execution_facts()
        return self._project_observable_task_rows([], fact_rows)

    def task_row_read_model_fallback_coverage(self) -> dict[str, Any]:
        """Return structured read-only coverage for the transitional fallback.

        The projection reuses the same file row loader, execution-fact loader,
        and observable row overlay used by ``_transitional_task_row_read_model_rows``.
        It is intentionally side-effect free: it does not claim tasks, mutate
        sessions, append facts, or refresh dependency unblocks.

        Complexity:
            O(r + f + p) time and memory over file rows, fact rows, and projected rows.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        projected_rows = self._project_observable_task_rows(file_rows, fact_rows)

        file_row_ids = self._task_row_read_model_task_id_set(file_rows)
        fact_row_ids = self._task_row_read_model_task_id_set(fact_rows)
        file_row_ids_without_execution_fact = sorted(
            file_row_ids - fact_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_row_ids_without_file_row = sorted(
            fact_row_ids - file_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )

        file_rows_count = len(file_rows)
        coverage_ratio = 1.0 if file_rows_count == 0 else len(file_row_ids & fact_row_ids) / file_rows_count

        return {
            "file_rows_count": file_rows_count,
            "fact_rows_count": len(fact_rows),
            "projected_rows_count": len(projected_rows),
            "file_row_ids_without_execution_fact": file_row_ids_without_execution_fact,
            "fact_row_ids_without_file_row": fact_row_ids_without_file_row,
            "coverage_ratio": coverage_ratio,
            "transitional_file_fallback_required": bool(file_row_ids_without_execution_fact),
        }

    def task_row_read_model_projection_parity_coverage(self) -> dict[str, Any]:
        """Return observable row parity coverage for fact-only cutover.

        Boundary:
            This is a read-only projection audit. It loads file-backed task rows
            and latest execution-fact rows through the existing read boundaries,
            then compares the current transitional observable projection with
            the future fact-only observable projection. It must not claim tasks,
            mutate sessions, append facts, refresh dependency unblocks, or write
            task rows.

        Complexity:
            O(r + f + p) time and memory over file rows, fact rows, and the two
            projected observable row sets.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        transitional_rows = self._project_observable_task_rows(file_rows, fact_rows)
        fact_only_rows = self._project_observable_task_rows([], fact_rows)

        transitional_rows_by_id = type(self)._task_row_read_model_rows_by_task_id(
            transitional_rows,
            self._task_row_read_model_task_id,
        )
        fact_only_rows_by_id = type(self)._task_row_read_model_rows_by_task_id(
            fact_only_rows,
            self._task_row_read_model_task_id,
        )
        transitional_row_ids = set(transitional_rows_by_id)
        fact_only_row_ids = set(fact_only_rows_by_id)
        shared_row_ids = transitional_row_ids & fact_only_row_ids

        transitional_only_row_ids = sorted(
            transitional_row_ids - fact_only_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_only_row_ids_without_transitional = sorted(
            fact_only_row_ids - transitional_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        row_ids_with_projection_mismatch = sorted(
            (
                task_id
                for task_id in shared_row_ids
                if not type(self)._task_row_read_model_rows_equal(
                    transitional_rows_by_id[task_id],
                    fact_only_rows_by_id[task_id],
                )
            ),
            key=self._task_row_read_model_task_id_sort_key,
        )

        parity_denominator = len(transitional_row_ids | fact_only_row_ids)
        if parity_denominator == 0:
            parity_ratio = 1.0
        else:
            parity_matches = len(shared_row_ids) - len(row_ids_with_projection_mismatch)
            parity_ratio = parity_matches / parity_denominator
        observable_projection_parity_ready = (
            not transitional_only_row_ids
            and not fact_only_row_ids_without_transitional
            and not row_ids_with_projection_mismatch
        )

        return {
            "transitional_rows_count": len(transitional_rows),
            "fact_only_rows_count": len(fact_only_rows),
            "transitional_only_row_ids": transitional_only_row_ids,
            "fact_only_row_ids": fact_only_row_ids_without_transitional,
            "row_ids_with_projection_mismatch": row_ids_with_projection_mismatch,
            "parity_ratio": parity_ratio,
            "observable_projection_parity_ready": observable_projection_parity_ready,
        }

    def projected_runtime_execution_session_fallback_coverage(self) -> dict[str, Any]:
        """Return structured coverage for projected runtime-execution sessions.

        This read model compares file-backed ``metadata.runtime_execution``
        projections with append-only execution-fact projections. It is strictly
        observational and must not refresh dependencies, append events, mutate
        task rows, claim work, or write execution sessions.

        Complexity:
            O(r + f) time and memory over file-backed and execution-fact rows.
        """

        file_rows = self._list_file_task_rows(
            include_terminal=True,
            augment_runtime_state=True,
        )
        fact_rows = self.list_task_rows_from_execution_facts()

        file_projected_session_rows = [
            row for row in file_rows if self._runtime_execution_session_from_projected_row(row) is not None
        ]
        fact_projected_session_rows = [
            row for row in fact_rows if self._runtime_execution_session_from_projected_row(row) is not None
        ]

        file_projected_session_task_ids = self._task_row_read_model_task_id_set(file_projected_session_rows)
        fact_projected_session_task_ids = self._task_row_read_model_task_id_set(fact_projected_session_rows)
        file_projected_session_task_ids_without_execution_fact = sorted(
            file_projected_session_task_ids - fact_projected_session_task_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_projected_session_task_ids_without_file_row = sorted(
            fact_projected_session_task_ids - file_projected_session_task_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )

        file_projected_session_rows_count = len(file_projected_session_rows)
        if file_projected_session_rows_count == 0:
            coverage_ratio = 1.0
        else:
            coverage_ratio = (
                len(file_projected_session_task_ids & fact_projected_session_task_ids)
                / file_projected_session_rows_count
            )

        return {
            "file_projected_session_rows_count": file_projected_session_rows_count,
            "fact_projected_session_rows_count": len(fact_projected_session_rows),
            "file_projected_session_task_ids_without_execution_fact": (
                file_projected_session_task_ids_without_execution_fact
            ),
            "fact_projected_session_task_ids_without_file_row": (fact_projected_session_task_ids_without_file_row),
            "coverage_ratio": coverage_ratio,
            "projected_session_file_fallback_required": bool(file_projected_session_task_ids_without_execution_fact),
        }

    def task_row_read_model_cutover_readiness(self) -> dict[str, Any]:
        """Return read-only readiness for future fact-only task-row cutover.

        Boundary:
            This projection only composes the task-row fallback, projected
            runtime-execution session fallback, and observable projection parity
            coverage read models. It must not call file row/entity loaders,
            refresh APIs, mutation APIs, or session writers directly; the
            underlying coverage methods remain the only data boundary for this
            readiness signal.

        Complexity:
            O(c) additional time and memory over the three coverage dictionaries,
            excluding the cost already owned by the delegated coverage methods.
        """

        task_row_read_model_fallback_coverage = self.task_row_read_model_fallback_coverage()
        task_row_read_model_projection_parity_coverage = self.task_row_read_model_projection_parity_coverage()
        projected_runtime_execution_session_fallback_coverage = (
            self.projected_runtime_execution_session_fallback_coverage()
        )

        task_row_file_fallback_required = bool(
            task_row_read_model_fallback_coverage.get("transitional_file_fallback_required")
        )
        projected_session_file_fallback_required = bool(
            projected_runtime_execution_session_fallback_coverage.get("projected_session_file_fallback_required")
        )
        observable_projection_parity_ready = bool(
            task_row_read_model_projection_parity_coverage.get("observable_projection_parity_ready")
        )

        blocking_reasons: list[str] = []
        if task_row_file_fallback_required:
            blocking_reasons.append("task_row_file_fallback_required")
        if projected_session_file_fallback_required:
            blocking_reasons.append("projected_session_file_fallback_required")
        if not observable_projection_parity_ready:
            blocking_reasons.append("observable_projection_parity_mismatch")

        return {
            "ready": (
                not task_row_file_fallback_required
                and not projected_session_file_fallback_required
                and observable_projection_parity_ready
            ),
            "blocking_reasons": blocking_reasons,
            "task_row_file_fallback_required": task_row_file_fallback_required,
            "projected_session_file_fallback_required": projected_session_file_fallback_required,
            "observable_projection_parity_ready": observable_projection_parity_ready,
            "task_row_read_model_fallback_coverage": task_row_read_model_fallback_coverage,
            "task_row_read_model_projection_parity_coverage": task_row_read_model_projection_parity_coverage,
            "projected_runtime_execution_session_fallback_coverage": (
                projected_runtime_execution_session_fallback_coverage
            ),
        }

    def _dependency_status_read_model_rows(self) -> list[dict[str, Any]]:
        """Load transitional dependency-status read-model rows.

        This helper is the dependency-status loader seam for the transitional
        read model and can be replaced when the file-backed fallback is removed.
        It is not a mutation API and does not authorize task claims, writes, or
        dependency transitions.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        return self._transitional_task_row_read_model_rows()

    def _fact_overlaid_dependency_status_rows(self) -> dict[int, TaskStatus]:
        """Return ``task_id -> TaskStatus`` using the fact-overlay-aware read model.

        This projection consumes ``_dependency_status_read_model_rows`` so the
        transitional row loading is isolated behind one helper. It does not call
        ``list_task_rows`` (which triggers ``refresh_dependency_unblocks``) or
        ``list_observable_task_rows`` (which is the external read-only
        projection API). Callers that need to mutate persisted tasks still
        iterate the ``TaskBoard.list_all()`` output; this helper only provides
        the status anchor they should consult for
        dependency decisions.

        Unknown or non-terminal fact statuses fall back to the file-backed
        status so that the dependency decision matches what a downstream
        caller would observe.  A terminal-failed Director row remains failed
        everywhere else, but counts as dependency-complete here when its
        TaskRuntime-owned, hash-bound materialization-satisfaction receipt is
        valid.  This preserves the narrower capability decision made by
        settlement instead of letting another process-local TaskBoard cache
        re-block the released child.
        """

        overlay_source = self._dependency_status_read_model_rows()

        status_by_id: dict[int, TaskStatus] = {}
        for row in overlay_source:
            task_id = self.normalize_task_id(row.get("id"))
            if task_id is None:
                continue
            status_token = str(row.get("status") or "").strip().lower()
            if not status_token:
                continue
            try:
                status = TaskStatus(status_token)
            except ValueError:
                logger.debug(
                    "Skipping unknown dependency status token from overlay for task_id=%s: %r",
                    task_id,
                    status_token,
                )
                continue
            if status == TaskStatus.FAILED and self._dependency_satisfaction_receipt_for_status_projection(
                row,
                expected_task_id=task_id,
            ):
                status = TaskStatus.COMPLETED
            status_by_id[task_id] = status
        return status_by_id

    def _project_execution_fact_event_row(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        """Project one FactStream event into the task-runtime row read model.

        The event wrapper may carry the canonical append sequence separately
        from the payload.  This helper applies the same seq carry-forward rule
        for every fact-derived TaskRuntime read model so direct lookup,
        observable rows, and list projections cannot drift.
        """

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        fact = dict(payload)
        fact.setdefault("event_id", str(event.get("event_id") or ""))
        fact.setdefault("occurred_at", str(event.get("occurred_at") or event.get("timestamp") or ""))
        if _coerce_fact_event_seq(fact.get("fact_event_seq")) is None:
            wrapper_seq = _coerce_fact_event_seq(event.get("seq"))
            if wrapper_seq is not None:
                fact["fact_event_seq"] = wrapper_seq
        return project_task_row_from_execution_fact_payload(fact)

    def _query_execution_fact_events(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> FactStreamQueryResultV1:
        """Query the task-runtime execution FactStream through one local gateway."""

        return query_fact_events(
            QueryFactEventsV1(
                workspace=self.workspace,
                stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                limit=limit,
                offset=offset,
            )
        )

    def query_same_task_local_rework_authorization(
        self,
        query: QuerySameTaskLocalReworkAuthorizationV1,
        *,
        page_size: int = 500,
    ) -> SameTaskLocalReworkAuthorizationQueryResultV1:
        """Recover one exact committed rework authorization from append-only facts."""

        base = {
            "workspace": str(self.workspace),
            "factory_run_id": query.factory_run_id,
            "external_task_id": query.external_task_id,
            "action_id": query.action_id,
        }
        try:
            first_page = self._query_execution_fact_events(limit=1, offset=0)
        except (FactStreamError, RuntimeError, TypeError, ValueError):
            return SameTaskLocalReworkAuthorizationQueryResultV1(
                ok=False,
                code="same_task_local_rework_authorization_fact_query_failed",
                **base,
            )
        total = int(getattr(first_page, "total", 0) or 0)
        if total <= 0:
            return SameTaskLocalReworkAuthorizationQueryResultV1(
                ok=True,
                code="same_task_local_rework_authorization_not_found",
                **base,
            )

        per_page = max(1, int(page_size))
        remaining = total
        matches: list[tuple[dict[str, Any], str, int | None]] = []
        malformed = False
        while remaining > 0:
            page_limit = min(per_page, remaining)
            offset = remaining - page_limit
            try:
                result = self._query_execution_fact_events(limit=page_limit, offset=offset)
            except (FactStreamError, RuntimeError, TypeError, ValueError):
                return SameTaskLocalReworkAuthorizationQueryResultV1(
                    ok=False,
                    code="same_task_local_rework_authorization_fact_query_failed",
                    **base,
                )
            events = [event for event in tuple(getattr(result, "events", ()) or ()) if isinstance(event, dict)]
            for event in reversed(events):
                if str(event.get("event_type") or "").strip() != "same_task_local_rework_prepared":
                    continue
                payload_raw = event.get("payload")
                payload = payload_raw if isinstance(payload_raw, Mapping) else {}
                details_raw = payload.get("details")
                details = details_raw if isinstance(details_raw, Mapping) else {}
                if (
                    str(payload.get("factory_run_id") or "").strip() != query.factory_run_id
                    or str(details.get("external_task_id") or "").strip() != query.external_task_id
                    or str(details.get("action_id") or "").strip() != query.action_id
                ):
                    continue
                authorization: dict[str, Any] = {
                    "schema_version": "task-runtime.same-task-local-rework-record/1",
                    "factory_run_id": str(details.get("factory_run_id") or "").strip(),
                    "external_task_id": str(details.get("external_task_id") or "").strip(),
                    "action_id": str(details.get("action_id") or "").strip(),
                    "diagnostic_id": str(details.get("diagnostic_id") or "").strip(),
                    "dispatch_claim_id": str(details.get("dispatch_claim_id") or "").strip(),
                    "effect_hash": str(details.get("effect_hash") or "").strip(),
                    "rework_attempt": int(details.get("rework_attempt") or 0),
                }
                event_id = str(event.get("event_id") or "").strip()
                event_seq_raw = event.get("seq")
                try:
                    event_seq = int(event_seq_raw) if event_seq_raw is not None else None
                except (TypeError, ValueError):
                    event_seq = None
                if (
                    authorization["factory_run_id"] != query.factory_run_id
                    or authorization["external_task_id"] != query.external_task_id
                    or authorization["action_id"] != query.action_id
                    or not authorization["diagnostic_id"]
                    or len(authorization["dispatch_claim_id"]) != 64
                    or len(authorization["effect_hash"]) != 64
                    or authorization["rework_attempt"] < 1
                    or not event_id
                    or event_seq is None
                ):
                    malformed = True
                    continue
                matches.append((authorization, event_id, event_seq))
            remaining = offset

        if malformed:
            return SameTaskLocalReworkAuthorizationQueryResultV1(
                ok=False,
                code="same_task_local_rework_authorization_malformed",
                **base,
            )
        if not matches:
            return SameTaskLocalReworkAuthorizationQueryResultV1(
                ok=True,
                code="same_task_local_rework_authorization_not_found",
                **base,
            )
        if len(matches) != 1:
            return SameTaskLocalReworkAuthorizationQueryResultV1(
                ok=False,
                code="same_task_local_rework_authorization_ambiguous",
                **base,
            )
        authorization, event_id, event_seq = matches[0]
        return SameTaskLocalReworkAuthorizationQueryResultV1(
            ok=True,
            code="same_task_local_rework_authorization_found",
            authorization=authorization,
            fact_event_id=event_id,
            fact_event_seq=event_seq,
            **base,
        )

    def _query_execution_fact_stream_head(self) -> int | None:
        """Return the durable execution-stream head for cache invalidation.

        A missing/unavailable head only disables the cross-call optimization;
        it never fabricates authority or suppresses the canonical event query.
        """

        try:
            projection = query_fact_stream_head(
                QueryFactStreamHeadV1(
                    workspace=self.workspace,
                    stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                )
            )
        except (FactStreamError, RuntimeError, TypeError, ValueError):
            return None
        head = getattr(projection, "current_seq", None)
        if isinstance(head, bool) or not isinstance(head, int) or head < 0:
            return None
        return head

    def _cached_execution_fact_rows(
        self,
        *,
        event_limit: int,
        stream_head: int | None,
    ) -> list[dict[str, Any]] | None:
        if stream_head is None:
            return None
        key = (self.workspace, event_limit, stream_head)
        with _EXECUTION_FACT_ROWS_HEAD_CACHE_LOCK:
            rows = _EXECUTION_FACT_ROWS_HEAD_CACHE.get(key)
            if rows is None:
                return None
            _EXECUTION_FACT_ROWS_HEAD_CACHE.move_to_end(key)
            return [dict(row) for row in rows]

    def _cache_execution_fact_rows(
        self,
        *,
        event_limit: int,
        stream_head: int | None,
        rows: list[dict[str, Any]],
    ) -> None:
        if stream_head is None:
            return
        key = (self.workspace, event_limit, stream_head)
        with _EXECUTION_FACT_ROWS_HEAD_CACHE_LOCK:
            stale_keys = [
                candidate
                for candidate in _EXECUTION_FACT_ROWS_HEAD_CACHE
                if candidate[0] == self.workspace and candidate[1] == event_limit and candidate != key
            ]
            for stale_key in stale_keys:
                _EXECUTION_FACT_ROWS_HEAD_CACHE.pop(stale_key, None)
            _EXECUTION_FACT_ROWS_HEAD_CACHE[key] = tuple(dict(row) for row in rows)
            _EXECUTION_FACT_ROWS_HEAD_CACHE.move_to_end(key)
            while len(_EXECUTION_FACT_ROWS_HEAD_CACHE) > _EXECUTION_FACT_ROWS_HEAD_CACHE_MAX:
                _EXECUTION_FACT_ROWS_HEAD_CACHE.popitem(last=False)

    def _complete_execution_fact_snapshot(
        self,
    ) -> tuple[int, dict[str, dict[str, Any]]] | None:
        with _EXECUTION_FACT_ROWS_HEAD_CACHE_LOCK:
            snapshot = _EXECUTION_FACT_ROWS_COMPLETE_CACHE.get(self.workspace)
            if snapshot is None:
                return None
            _EXECUTION_FACT_ROWS_COMPLETE_CACHE.move_to_end(self.workspace)
            head, latest_by_task = snapshot
            return head, {task_id: dict(row) for task_id, row in latest_by_task.items()}

    def _store_complete_execution_fact_snapshot(
        self,
        *,
        stream_head: int,
        latest_by_task: dict[str, dict[str, Any]],
    ) -> None:
        with _EXECUTION_FACT_ROWS_HEAD_CACHE_LOCK:
            _EXECUTION_FACT_ROWS_COMPLETE_CACHE[self.workspace] = (
                stream_head,
                {task_id: dict(row) for task_id, row in latest_by_task.items()},
            )
            _EXECUTION_FACT_ROWS_COMPLETE_CACHE.move_to_end(self.workspace)
            while len(_EXECUTION_FACT_ROWS_COMPLETE_CACHE) > _EXECUTION_FACT_ROWS_COMPLETE_CACHE_MAX:
                _EXECUTION_FACT_ROWS_COMPLETE_CACHE.popitem(last=False)

    def _merge_execution_fact_events(
        self,
        latest_by_task: dict[str, dict[str, Any]],
        events: list[Any],
    ) -> None:
        for event in events:
            if not isinstance(event, dict):
                continue
            row = self._project_execution_fact_event_row(event)
            if row is None:
                continue
            task_id = str(row.get("task_id") or row.get("id") or "").strip()
            if not task_id:
                continue
            existing = latest_by_task.get(task_id)
            if existing is not None:
                incoming_seq = _coerce_fact_event_seq(row.get("fact_event_seq")) or -1
                existing_seq = _coerce_fact_event_seq(existing.get("fact_event_seq")) or -1
                if incoming_seq < existing_seq:
                    continue
            latest_by_task[task_id] = row

    def _finalize_execution_fact_rows(
        self,
        latest_by_task: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in latest_by_task.values()
            if str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
        ]
        rows.sort(key=self._row_sort_key)
        return rows

    def _query_execution_fact_event_span(
        self,
        *,
        offset: int,
        count: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        remaining = max(0, int(count))
        cursor = max(0, int(offset))
        events: list[dict[str, Any]] = []
        while remaining > 0:
            page_limit = min(max(1, int(page_size)), remaining)
            result = self._query_execution_fact_events(limit=page_limit, offset=cursor)
            page = [event for event in list(result.events or []) if isinstance(event, dict)]
            if not page:
                break
            events.extend(page)
            consumed = len(page)
            cursor += consumed
            remaining -= consumed
            if consumed < page_limit:
                break
        return events

    def list_task_rows_from_execution_facts(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Return latest-per-task read models from ``task_runtime.execution`` facts.

        Boundary:
            This is a read projection only. It does not authorize claims,
            writes, or dependency transitions. Transitional claim paths must
            continue to use the row/session APIs until the storage owner is fully
            event-sourced.

        ``limit`` is the FactStream *page size*, not an authority window.
        Factory cutover/coverage compares every file-backed TaskRuntime row
        against this projection. Query the newest page first so later facts
        win, then walk older pages so a task whose latest event sits behind a
        long rematerialize tail remains visible. After the first complete
        snapshot, later observers merge only events after the cached head.

        The queried FactStream event wrapper exposes the canonical
        ``FactEventAppendedV1.appended_seq`` value as the ``seq`` field on
        the event envelope. When the persisted fact payload lacks a valid
        positive ``fact_event_seq`` field, the wrapper's ``seq`` is copied
        onto the payload before projection so the read-only
        ``project_task_row_from_execution_fact_payload`` can expose it as
        the row-level ``fact_event_seq`` marker. The seq is never
        fabricated — payloads that already carry a valid positive
        ``fact_event_seq`` keep that value, and missing/invalid seq values
        cause the top-level field to be omitted entirely.

        Complexity:
            O(e + t log t) first rebuild over the stream; O(delta + t log t)
            after the complete snapshot is warm; O(t) memory for latest-by-task
            rows.
        """

        event_limit = max(1, int(limit))
        cache = _EXECUTION_FACT_ROWS_PROJECTION_CACHE.get()
        cache_key = (self.workspace, event_limit)
        if cache is not None and cache_key in cache:
            return [dict(row) for row in cache[cache_key]]
        stream_head = self._query_execution_fact_stream_head()
        cached_rows = self._cached_execution_fact_rows(
            event_limit=event_limit,
            stream_head=stream_head,
        )
        if cached_rows is not None:
            if cache is not None:
                cache[cache_key] = tuple(dict(row) for row in cached_rows)
            return cached_rows

        latest_by_task: dict[str, dict[str, Any]] = {}
        complete_head: int | None = None
        snapshot = self._complete_execution_fact_snapshot()
        if snapshot is not None and stream_head is not None and snapshot[0] == stream_head:
            latest_by_task = snapshot[1]
            complete_head = stream_head
        elif snapshot is not None and stream_head is not None and 0 <= snapshot[0] < stream_head:
            try:
                delta_events = self._query_execution_fact_event_span(
                    offset=snapshot[0],
                    count=stream_head - snapshot[0],
                    page_size=event_limit,
                )
            except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("failed to incrementally load task runtime execution fact rows: %s", exc)
            else:
                latest_by_task = snapshot[1]
                self._merge_execution_fact_events(latest_by_task, delta_events)
                complete_head = stream_head

        if complete_head is None:
            try:
                result = self._query_execution_fact_events(limit=event_limit)
                total = int(getattr(result, "total", 0) or 0)
                latest_events = [event for event in list(result.events or []) if isinstance(event, dict)]
                if total > len(latest_events):
                    latest_offset = max(0, total - event_limit)
                    if latest_offset:
                        result = self._query_execution_fact_events(
                            limit=event_limit,
                            offset=latest_offset,
                        )
                        latest_events = [event for event in list(result.events or []) if isinstance(event, dict)]
                self._merge_execution_fact_events(latest_by_task, latest_events)
                older_cursor = max(0, total - event_limit)
                while older_cursor > 0:
                    older_offset = max(0, older_cursor - event_limit)
                    older_page = self._query_execution_fact_events(
                        limit=event_limit,
                        offset=older_offset,
                    )
                    self._merge_execution_fact_events(
                        latest_by_task,
                        [event for event in list(older_page.events or []) if isinstance(event, dict)],
                    )
                    if older_offset == 0:
                        break
                    older_cursor = older_offset
            except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("failed to load task runtime execution fact rows: %s", exc)
                return []
            if stream_head is not None:
                complete_head = stream_head

        rows = self._finalize_execution_fact_rows(latest_by_task)
        if cache is not None:
            cache[cache_key] = tuple(dict(row) for row in rows)
        self._cache_execution_fact_rows(
            event_limit=event_limit,
            stream_head=stream_head,
            rows=rows,
        )
        if complete_head is not None:
            self._store_complete_execution_fact_snapshot(
                stream_head=complete_head,
                latest_by_task=latest_by_task,
            )
        return rows

    def _find_latest_execution_fact_row_for_task(
        self,
        task_id: int,
        *,
        page_size: int = 500,
    ) -> dict[str, Any] | None:
        """Return the latest fact-derived row for one ``task_id`` via exact paging.

        Boundary:
            Read-only projection that locates the authoritative
            ``task_runtime.execution`` fact for a single task.  It must not
            write to workspace, mint new events, or fabricate session state.
            Direct ``claim_execution(task_id=...)`` callers use the returned row
            to detect a terminal verdict that the stale file row may not yet
            reflect, so we page backward through the fact stream until we find
            a matching task_id rather than scanning a single latest window.

        The projection reuses ``project_task_row_from_execution_fact_payload``
        and the wrapper-level ``seq`` carry-forward logic from
        ``list_task_rows_from_execution_facts`` so the read model stays
        consistent with the public list read model.  We also reuse
        ``_coerce_fact_event_seq`` to avoid inventing a second inconsistent
        projection for ``fact_event_seq``.

        Complexity:
            O(e * p) time where ``e`` is the events inspected per page and
            ``p`` is the number of pages walked before either finding the task
            id or exhausting the stream; O(1) additional memory.
        """

        normalized_id = self.normalize_task_id(task_id)
        if normalized_id is None:
            return None
        target_task_id = str(normalized_id).strip()
        if not target_task_id:
            return None

        per_page = max(1, int(page_size))
        try:
            first_page = self._query_execution_fact_events(limit=1, offset=0)
        except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug(
                "failed to inspect task runtime execution facts for task_id=%s: %s",
                target_task_id,
                exc,
            )
            return None

        total = int(getattr(first_page, "total", 0) or 0)
        if total <= 0:
            return None

        offset = max(0, total - per_page)
        while True:
            try:
                result = self._query_execution_fact_events(limit=per_page, offset=offset)
            except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug(
                    "failed to load task runtime execution fact row for task_id=%s: %s",
                    target_task_id,
                    exc,
                )
                return None

            events = [event for event in list(getattr(result, "events", []) or []) if isinstance(event, dict)]
            for event in reversed(events):
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
                if payload_task_id != target_task_id:
                    continue
                row = self._project_execution_fact_event_row(event)
                if row is None:
                    return None
                if str(row.get("execution_state") or row.get("status") or "").strip().lower() == "removed":
                    return None
                return row

            if offset <= 0:
                return None
            offset = max(0, offset - per_page)

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        """Return task rows for read-only runtime projections.

        Boundary:
            This method is the task-runtime-owned read model for status,
            snapshot, and UI projection consumers. It performs a gated
            fact-only cutover: when ``task_row_read_model_cutover_readiness``
            reports ready, observable rows are projected only from append-only
            ``task_runtime.execution`` facts; otherwise the transitional
            file-backed fallback remains active. This does not change mutation,
            claim, selection, dependency-transition, or repair APIs. Execution
            paths that need to select or mutate work must continue to call the
            explicit row/session APIs.

        The implementation intentionally avoids ``refresh_dependency_unblocks``
        and ``list_task_rows`` so read-only observers cannot trigger dependency
        state writes. ``list_task_rows`` remains the compatibility entry point
        for callers that expect dependency unblocks to be refreshed before
        reading rows.

        Complexity:
            O(c) additional time and memory over cutover readiness selection,
            excluding delegated readiness checks; selected projection is O(f)
            when ready and O(r + f) while transitional fallback is required.
        """

        readiness = self.task_row_read_model_cutover_readiness()
        if readiness.get("ready") is True:
            return self._fact_only_task_row_read_model_rows()
        return self._transitional_task_row_read_model_rows()

    def query_observable_task_rows_projection(self) -> ObservableTaskRowsProjectionV1:
        """Return observable rows together with their authority provenance.

        Completion, QA, and dependency-control consumers must require
        ``authoritative``. Compatibility and UI consumers may display degraded
        rows, but the source marker prevents them from becoming a second
        execution truth.

        Complexity:
            O(r + f) time and memory in degraded migration mode; O(f) when the
            fact-only cutover is ready.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            ObservableTaskRowsProjectionV1,
        )

        cache_token: Token[dict[tuple[str, int], tuple[dict[str, Any], ...]] | None] | None = None
        if _EXECUTION_FACT_ROWS_PROJECTION_CACHE.get() is None:
            cache_token = _EXECUTION_FACT_ROWS_PROJECTION_CACHE.set({})
        try:
            readiness = self.task_row_read_model_cutover_readiness()
            authoritative = readiness.get("ready") is True
            if authoritative:
                rows = self._fact_only_task_row_read_model_rows()
                source = "task_runtime.execution_fact"
            else:
                rows = self._transitional_task_row_read_model_rows()
                source = "task_runtime.transitional_file_fallback"
            return ObservableTaskRowsProjectionV1(
                workspace=self.workspace,
                source=source,
                authoritative=authoritative,
                degraded=not authoritative,
                rows=tuple(rows),
                readiness=readiness,
            )
        finally:
            if cache_token is not None:
                _EXECUTION_FACT_ROWS_PROJECTION_CACHE.reset(cache_token)

    def _project_observable_task_rows(
        self,
        file_rows: list[dict[str, Any]],
        fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge file-backed rows with execution facts without I/O or writes.

        ``file_rows`` is the transitional TaskBoard projection and
        ``fact_rows`` is the append-only ``task_runtime.execution`` projection.
        The fact rows overlay matching file rows, while facts for tasks no
        longer present in files remain observable. Inputs are shallow-copied so
        callers can safely reuse their loaded rows after projection. This
        helper is the shared pure projection point for observable rows and
        dependency-status read-model synthesis; callers load their own inputs
        so mutation paths do not depend on external read APIs.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        if not file_rows:
            return [dict(row) for row in fact_rows]
        if not fact_rows:
            return [dict(row) for row in file_rows]
        return self._overlay_execution_fact_rows(
            [dict(row) for row in file_rows],
            [dict(row) for row in fact_rows],
        )

    @staticmethod
    def _observable_row_task_id(row: dict[str, Any]) -> str:
        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        return str(
            row.get("task_id")
            or row.get("id")
            or row.get("taskId")
            or metadata.get("task_id")
            or metadata.get("pm_task_id")
            or metadata.get("workflow_task_id")
            or ""
        ).strip()

    def _task_row_read_model_task_id(self, row: dict[str, Any]) -> str | None:
        raw_task_id = self._observable_row_task_id(row)
        if not raw_task_id:
            return None
        normalized_task_id = self.normalize_task_id(raw_task_id)
        if normalized_task_id is not None:
            return str(normalized_task_id)
        return raw_task_id

    def _task_row_read_model_task_id_set(self, rows: list[dict[str, Any]]) -> set[str]:
        return {task_id for row in rows if (task_id := self._task_row_read_model_task_id(row))}

    @staticmethod
    def _task_row_read_model_rows_by_task_id(
        rows: list[dict[str, Any]],
        task_id_for_row: Callable[[dict[str, Any]], str | None],
    ) -> dict[str, dict[str, Any]]:
        """Return task rows keyed by normalized read-model task id.

        Boundary:
            This pure helper does not load, mutate, or normalize rows itself; it
            only applies the provided task-id reader to already projected rows.

        Complexity:
            O(r) time and memory over the provided rows.
        """

        return {task_id: row for row in rows if (task_id := task_id_for_row(row))}

    @staticmethod
    def _task_row_read_model_rows_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
        """Return full row-dict equality for observable projection parity.

        Boundary:
            This pure helper compares already loaded row dictionaries only. It
            performs no projection, I/O, mutation, or lossy field normalization.

        Complexity:
            O(s) time over the nested row payload size.
        """

        return left == right

    def _task_row_read_model_task_id_sort_key(self, task_id: str) -> tuple[int, str]:
        normalized_task_id = self.normalize_task_id(task_id)
        if normalized_task_id is not None:
            return (0, f"{normalized_task_id:010d}")
        return (1, task_id)

    def _overlay_execution_fact_rows(
        self,
        rows: list[dict[str, Any]],
        fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project facts as authority and retain files only for uncovered tasks.

        A task row backed by ``task_runtime.execution`` must be projected from
        that fact without merging mutable file state back into it.  Merging the
        two representations made the transitional projection structurally
        different from the fact-only projection even when every task had a
        complete fact snapshot, permanently preventing the SSoT cutover.

        File rows remain a migration fallback solely for task ids that have no
        execution fact.  Once a fact exists, its complete ``task_row_snapshot``
        and event fields are the canonical observable representation.

        Complexity:
            O(r + f) time and memory over file rows and latest fact rows.
        """

        latest_by_task: dict[str, dict[str, Any]] = {
            task_id: dict(fact_row) for fact_row in fact_rows if (task_id := self._observable_row_task_id(fact_row))
        }
        if not latest_by_task:
            return rows

        overlaid: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            row_map = dict(row)
            task_id = self._observable_row_task_id(row_map)
            fact_row = latest_by_task.get(task_id)
            if not fact_row:
                overlaid.append(row_map)
                continue
            overlaid.append(dict(fact_row))
            seen.add(task_id)

        for task_id, fact_row in latest_by_task.items():
            if task_id not in seen:
                overlaid.append(fact_row)
        return overlaid

    def select_next_task(
        self,
        *,
        requested_task_id: Any = None,
        prefer_resumable: bool = True,
    ) -> dict[str, Any] | None:
        """Return the next claimable task row, preferring resumable work.

        Selection consumes the task-runtime-owned observable read model
        (``list_observable_task_rows``) so that ``task_runtime.execution``
        fact overlays can override stale file-backed status before the
        claimable filter is applied. Requested-task lookup also resolves
        from the observable rows for the same reason; if the latest fact
        says terminal/non-claimable, the requested row is rejected even if
        the underlying file row still looks pending.

        This is a deterministic preview API. Concurrent Director fanout must
        use ``claim_next_execution`` so selection and claim stay in one retryable
        operation.
        """
        self.refresh_dependency_unblocks()
        observable_rows = self.list_observable_task_rows()
        if requested_task_id:
            normalized_requested = self.normalize_task_id(requested_task_id)
            for row in observable_rows:
                if self.normalize_task_id(row.get("id")) == normalized_requested and self._is_row_claimable(row):
                    return row
            return None

        candidates = [row for row in observable_rows if self._is_row_claimable(row)]
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
