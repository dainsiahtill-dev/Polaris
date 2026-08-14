"""Collaborator mixin for TaskRuntimeService (_mixin_recovery_reexec)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from polaris.cells.runtime.task_runtime.internal.task_board import (
    Task,
    TaskBoardFileLockTimeoutError,
    TaskFactoryRunBindingConflictError,
    TaskStatus,
)
from polaris.kernelone.storage import resolve_runtime_path

from ..execution_session import (
    TaskExecutionSession,
    _coerce_fact_event_seq,
    is_terminal_task_row_status,
    project_task_row_execution_event,
    sanitize_summary,
)
from ._helpers import (
    _REEXECUTION_METADATA_DROP_KEYS,
    _TASK_ID_PATTERN,
    _TASK_SESSION_FILE_PATTERN,
    _build_factory_run_binding_result,
    _DirectedEffectRecoverySessionSweep,
    _DirectedEffectRecoveryTaskCatalog,
    _raise_retired_entity_api,
    logger,
)
from ._late_bindings import (
    utc_now,
    utc_now_iso,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        BindRuntimeTaskToFactoryRunCommandV1,
        DirectedEffectRecoverySweepItemV1,
        DirectedEffectRecoverySweepResultV1,
        ExpiredFactoryRunSessionFenceResultV1,
        FenceExpiredFactoryRunSessionsCommandV1,
        ReconcileAmbiguousDirectedEffectsCommandV1,
        RuntimeTaskFactoryRunBindingResultV1,
    )


class _RecoveryReexecMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

    @staticmethod
    def _directed_effect_recovery_session_is_expired(session: TaskExecutionSession) -> bool:
        """Evaluate active-session recovery eligibility without changing attempt identity."""

        return session.is_expired(now=utc_now())

    def _reconcile_ambiguous_directed_effects_under_lease(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        *,
        owner_epoch: str,
        deadline_monotonic: float,
    ) -> DirectedEffectRecoverySweepResultV1:
        """Run one recovery sweep while the durable maintenance lock is held."""

        from polaris.cells.runtime.task_runtime.public.contracts import (
            DirectedEffectRecoverySweepResultV1,
        )

        catalog = self._discover_directed_effect_recovery_tasks(
            command,
            owner_epoch=owner_epoch,
            deadline_monotonic=deadline_monotonic,
        )
        if isinstance(catalog, DirectedEffectRecoverySweepResultV1):
            return catalog
        items: list[DirectedEffectRecoverySweepItemV1] = []
        failures: list[dict[str, Any]] = []
        scanned_session_count = 0
        scanned_operation_count = 0
        for task_id in catalog.task_ids:
            session_sweep = self._reconcile_directed_effect_recovery_task(
                command,
                task_id=task_id,
                task_row=catalog.task_rows_by_id.get(task_id, {}),
                owner_epoch=owner_epoch,
                deadline_monotonic=deadline_monotonic,
                scanned_operation_count=scanned_operation_count,
            )
            items.extend(session_sweep.items)
            failures.extend(session_sweep.failures)
            scanned_session_count += session_sweep.scanned_session_count
            scanned_operation_count += session_sweep.scanned_operation_count
            if session_sweep.stop_sweep:
                break
        if self._recovery_result_needs_deadline_failure(failures, deadline_monotonic=deadline_monotonic):
            failures.append(
                {
                    "code": "recovery_deadline_exceeded",
                    "stage": "before_recovery_result",
                    "owner_epoch": owner_epoch,
                }
            )
        return DirectedEffectRecoverySweepResultV1(
            ok=not failures,
            code="reconciled" if not failures else "partial_failure",
            workspace=str(Path(self.workspace).expanduser().resolve()),
            scanned_session_count=scanned_session_count,
            items=tuple(items),
            failures=tuple(failures),
        )

    def _discover_directed_effect_recovery_tasks(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        *,
        owner_epoch: str,
        deadline_monotonic: float,
    ) -> _DirectedEffectRecoveryTaskCatalog | DirectedEffectRecoverySweepResultV1:
        from polaris.cells.runtime.task_runtime.public.contracts import (
            DirectedEffectRecoverySweepResultV1,
        )

        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="before_tasks_directory", owner_epoch=owner_epoch
            )
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="before_task_projection", owner_epoch=owner_epoch
            )
        projection = self.query_observable_task_rows_projection()
        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="after_task_projection", owner_epoch=owner_epoch
            )
        rows = {
            task_id: dict(row)
            for row in projection.rows
            if (task_id := self.normalize_task_id(row.get("id"))) is not None
        }
        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="after_task_projection_materialization", owner_epoch=owner_epoch
            )
        discovered = self._scan_directed_effect_recovery_session_catalog(
            tasks_dir, owner_epoch=owner_epoch, deadline_monotonic=deadline_monotonic
        )
        if isinstance(discovered, DirectedEffectRecoverySweepResultV1):
            return discovered
        if len(discovered) > command.max_sessions:
            failure = {
                "code": "recovery_session_limit_exceeded",
                "session_count": len(discovered),
                "max_sessions": command.max_sessions,
                "owner_epoch": owner_epoch,
            }
            return DirectedEffectRecoverySweepResultV1(
                ok=False,
                code="partial_failure",
                workspace=str(Path(self.workspace).expanduser().resolve()),
                scanned_session_count=0,
                failures=(failure,),
            )
        return _DirectedEffectRecoveryTaskCatalog(rows, tuple(sorted(discovered)))

    def _scan_directed_effect_recovery_session_catalog(
        self,
        tasks_dir: Path,
        *,
        owner_epoch: str,
        deadline_monotonic: float,
    ) -> set[int] | DirectedEffectRecoverySweepResultV1:
        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="before_session_catalog_scan", owner_epoch=owner_epoch
            )
        task_ids: set[int] = set()
        for session_path in tasks_dir.glob("task_*.session.json"):
            if time.monotonic() >= deadline_monotonic:
                return self._directed_effect_recovery_deadline_result(
                    stage="during_session_catalog_scan", owner_epoch=owner_epoch
                )
            match = _TASK_SESSION_FILE_PATTERN.fullmatch(session_path.name)
            if match is not None:
                task_ids.add(int(match.group(1)))
        if time.monotonic() >= deadline_monotonic:
            return self._directed_effect_recovery_deadline_result(
                stage="after_session_catalog_scan", owner_epoch=owner_epoch
            )
        return task_ids

    def _reconcile_directed_effect_recovery_task(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        *,
        task_id: int,
        task_row: Mapping[str, Any],
        owner_epoch: str,
        deadline_monotonic: float,
        scanned_operation_count: int,
    ) -> _DirectedEffectRecoverySessionSweep:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            return _DirectedEffectRecoverySessionSweep(
                failures=({"code": "recovery_deadline_exceeded", "task_id": task_id, "owner_epoch": owner_epoch},),
                stop_sweep=True,
            )
        session_lock = self._get_session_lock(task_id)
        if not session_lock.acquire(timeout=min(command.lock_timeout_seconds, remaining)):
            if time.monotonic() >= deadline_monotonic:
                return _DirectedEffectRecoverySessionSweep(
                    failures=(
                        {
                            "code": "recovery_deadline_exceeded",
                            "stage": "after_session_lock_wait",
                            "task_id": task_id,
                            "owner_epoch": owner_epoch,
                        },
                    ),
                    stop_sweep=True,
                )
            return _DirectedEffectRecoverySessionSweep(
                failures=({"code": "recovery_session_lock_timeout", "task_id": task_id, "owner_epoch": owner_epoch},)
            )
        try:
            if time.monotonic() >= deadline_monotonic:
                return _DirectedEffectRecoverySessionSweep(
                    failures=(
                        {
                            "code": "recovery_deadline_exceeded",
                            "stage": "after_session_lock_acquire",
                            "task_id": task_id,
                            "owner_epoch": owner_epoch,
                        },
                    ),
                    stop_sweep=True,
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(task_id),
                    timeout_seconds=min(command.lock_timeout_seconds, max(0.0, deadline_monotonic - time.monotonic())),
                ):
                    return self._reconcile_directed_effect_recovery_task_file_locked(
                        command,
                        task_id=task_id,
                        task_row=task_row,
                        owner_epoch=owner_epoch,
                        deadline_monotonic=deadline_monotonic,
                        remaining_operations=command.max_operations - scanned_operation_count,
                    )
            except TaskBoardFileLockTimeoutError:
                if time.monotonic() >= deadline_monotonic:
                    return _DirectedEffectRecoverySessionSweep(
                        failures=(
                            {
                                "code": "recovery_deadline_exceeded",
                                "stage": "after_session_file_lock_wait",
                                "task_id": task_id,
                                "owner_epoch": owner_epoch,
                            },
                        ),
                        stop_sweep=True,
                    )
                return _DirectedEffectRecoverySessionSweep(
                    failures=({"code": "recovery_file_lock_timeout", "task_id": task_id, "owner_epoch": owner_epoch},)
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                return _DirectedEffectRecoverySessionSweep(
                    failures=(
                        {
                            "code": "recovery_file_lock_failed",
                            "task_id": task_id,
                            "owner_epoch": owner_epoch,
                            "error": str(exc),
                        },
                    )
                )
        finally:
            session_lock.release()

    def _reconcile_directed_effect_recovery_task_file_locked(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        *,
        task_id: int,
        task_row: Mapping[str, Any],
        owner_epoch: str,
        deadline_monotonic: float,
        remaining_operations: int,
    ) -> _DirectedEffectRecoverySessionSweep:
        self._after_directed_effect_recovery_session_file_lock_acquired(task_id=task_id)
        if self._directed_effect_recovery_deadline_reached(deadline_monotonic):
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_deadline_exceeded",
                        "stage": "before_session_read",
                        "task_id": task_id,
                        "owner_epoch": owner_epoch,
                    },
                ),
                stop_sweep=True,
            )
        try:
            current = self._read_directed_effect_recovery_session_locked(task_id)
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {"code": "session_corrupt", "task_id": task_id, "owner_epoch": owner_epoch, "error": str(exc)},
                )
            )
        self._after_directed_effect_recovery_session_read(
            task_id=task_id, session_id=current.session_id if current is not None else ""
        )
        try:
            return self._reconcile_ambiguous_directed_effect_session_locked(
                command,
                task_id=task_id,
                current=current,
                task_row=task_row,
                owner_epoch=owner_epoch,
                deadline_monotonic=deadline_monotonic,
                remaining_operations=remaining_operations,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_repository_failure",
                        "task_id": task_id,
                        "session_id": current.session_id if current is not None else "",
                        "owner_epoch": owner_epoch,
                        "error": str(exc),
                    },
                ),
                stop_sweep=True,
            )

    @staticmethod
    def _recovery_result_needs_deadline_failure(
        failures: list[dict[str, Any]],
        *,
        deadline_monotonic: float,
    ) -> bool:
        return time.monotonic() >= deadline_monotonic and not any(
            failure.get("code") == "recovery_deadline_exceeded" for failure in failures
        )

    def fence_expired_factory_run_sessions(
        self,
        command: FenceExpiredFactoryRunSessionsCommandV1,
    ) -> ExpiredFactoryRunSessionFenceResultV1:
        """Fence expired active sessions under explicit Factory authority.

        The operation is fail-closed: any active unexpired or foreign session
        prevents stale-owner recovery. Expired sessions are changed to
        non-resumable suspension and carry durable execution-fact evidence.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            ExpiredFactoryRunSessionFenceResultV1,
        )

        authority = command.factory_run_id
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            task_rows_by_id = {
                task_id: dict(row)
                for row in projection.rows
                if (task_id := self.normalize_task_id(row.get("id"))) is not None
            }
            task_ids = set(task_rows_by_id)
            for session_path in tasks_dir.glob("task_*.session.json"):
                match = _TASK_SESSION_FILE_PATTERN.fullmatch(session_path.name)
                if match is not None:
                    task_ids.add(int(match.group(1)))

            conflicts: list[dict[str, Any]] = []
            candidates: list[tuple[int, TaskExecutionSession]] = []
            observed_at = utc_now()
            for task_id in sorted(task_ids):
                session = self._read_session(task_id)
                if session is None or session.status != "active":
                    continue
                task_row = task_rows_by_id.get(task_id, {})
                owner = self._session_factory_run_id(session, task_row)
                expired = session.is_expired(now=observed_at)
                if owner != authority or not expired:
                    conflicts.append(
                        {
                            "kind": ("active_foreign_session" if owner != authority else "active_unexpired_session"),
                            "task_id": str(task_id),
                            "session_id": session.session_id,
                            "existing_factory_run_id": owner,
                            "requested_factory_run_id": authority,
                            "lease_expires_at": session.lease_expires_at,
                            "lease_expired": expired,
                        }
                    )
                    continue
                candidates.append((task_id, session))

            if conflicts:
                return ExpiredFactoryRunSessionFenceResultV1(
                    ok=False,
                    code="active_session_conflict",
                    workspace=str(self.workspace),
                    factory_run_id=authority,
                    conflicts=tuple(conflicts),
                )

            fenced_session_ids: list[str] = []
            execution_events: list[dict[str, Any]] = []
            fence_failures: list[dict[str, Any]] = []
            for task_id, session in candidates:
                with (
                    self._get_session_lock(session.task_id),
                    self._board._file_lock(self._session_file_lock_path(session.task_id)),
                ):
                    current = self._read_session_locked(task_id)
                    if current is None:
                        fence_failures.append(
                            {
                                "kind": "session_disappeared_before_fence",
                                "task_id": str(task_id),
                                "session_id": session.session_id,
                            }
                        )
                        continue
                    owner = self._session_factory_run_id(
                        current,
                        task_rows_by_id.get(task_id, {}),
                    )
                    if (
                        current.session_id != session.session_id
                        or current.status != "active"
                        or owner != authority
                        or not current.is_expired(now=utc_now())
                    ):
                        fence_failures.append(
                            {
                                "kind": "session_changed_before_fence",
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                                "session_status": current.status,
                                "existing_factory_run_id": owner,
                                "lease_expires_at": current.lease_expires_at,
                            }
                        )
                        continue
                    if self._has_pending_terminal_intent(current):
                        pending_intent = self._pending_terminal_intent(current)
                        fulfillment = self._fulfilled_terminal_intent_pre_barrier_locked(current)
                        if not fulfillment.allowed:
                            fence_failures.append(
                                {
                                    "kind": "terminal_fence_pending",
                                    "code": "terminal_fence_pending",
                                    "task_id": str(task_id),
                                    "session_id": current.session_id,
                                    "evidence": {
                                        "pending_terminal_intent": dict(pending_intent or {}),
                                        "pending_terminal_intent_valid": pending_intent is not None,
                                        "fulfillment_code": fulfillment.code,
                                        "fulfillment_evidence": dict(fulfillment.evidence),
                                    },
                                }
                            )
                            continue
                    pre_barrier = self._directed_effect_inactive_pre_barrier_locked(current)
                    if not pre_barrier.allowed:
                        fence_failures.append(
                            {
                                "kind": pre_barrier.code,
                                "code": pre_barrier.code,
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                                "evidence": dict(pre_barrier.evidence),
                            }
                        )
                        continue
                    previous_expiry = current.lease_expires_at
                    current.mark_suspended(reason=command.reason, resumable=False)
                    current.metadata["factory_stale_session_fence"] = {
                        "schema_version": "task-runtime.factory-stale-session-fence/1",
                        "factory_run_id": authority,
                        "reason": command.reason,
                        "previous_lease_expires_at": previous_expiry,
                        "fenced_at": current.released_at,
                    }
                    if not self._write_session_locked(current):
                        fence_failures.append(
                            {
                                "kind": "session_write_rejected",
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                            }
                        )
                        continue

                fence_metadata = self._build_runtime_metadata(
                    session=current,
                    effective_status="blocked",
                    resume_state="fenced",
                    extra_metadata={
                        "factory_run_id": authority,
                        "factory_stale_session_fenced": True,
                    },
                )
                runtime_execution = dict(fence_metadata.get("runtime_execution") or {})
                runtime_execution.update(
                    {
                        "effective_status": "blocked",
                        "raw_status": "blocked",
                        "resume_state": "fenced",
                        "resume_available": False,
                    }
                )
                fence_metadata["runtime_execution"] = runtime_execution
                fence_metadata["resume_state"] = "fenced"
                fence_metadata["resume_available"] = False
                updated = self._board.update(
                    task_id,
                    status=TaskStatus.BLOCKED,
                    assignee="",
                    metadata=fence_metadata,
                )
                row = self._augment_task_row(
                    updated.to_dict() if updated is not None else {"id": task_id, "status": "blocked"}
                )
                if updated is None:
                    fence_failures.append(
                        {
                            "kind": "task_row_update_rejected",
                            "task_id": str(task_id),
                            "session_id": current.session_id,
                        }
                    )
                    continue
                row_metadata = dict(row.get("metadata") or {})
                row_runtime_execution = dict(row_metadata.get("runtime_execution") or {})
                row_runtime_execution.update(
                    {
                        "effective_status": "blocked",
                        "raw_status": "blocked",
                        "resume_state": "fenced",
                        "resume_available": False,
                    }
                )
                row_metadata["runtime_execution"] = row_runtime_execution
                row.update(
                    {
                        "status": "blocked",
                        "state": "blocked",
                        "execution_state": "blocked",
                        "running": False,
                        "resume_state": "fenced",
                        "resume_available": False,
                        "metadata": row_metadata,
                    }
                )
                event = self._append_execution_event(
                    "factory_stale_session_fenced",
                    task_row=row,
                    session=current,
                    details={
                        "factory_run_id": authority,
                        "reason": sanitize_summary(command.reason),
                        "previous_lease_expires_at": previous_expiry,
                    },
                )
                if (
                    event.get("ok") is not True
                    or not str(event.get("fact_event_id") or "").strip()
                    or _coerce_fact_event_seq(event.get("fact_event_seq")) is None
                ):
                    fence_failures.append(
                        {
                            "kind": "execution_event_append_failed",
                            "task_id": str(task_id),
                            "session_id": current.session_id,
                        }
                    )
                    continue
                fenced_session_ids.append(current.session_id)
                execution_events.append(event)

            if fence_failures:
                return ExpiredFactoryRunSessionFenceResultV1(
                    ok=False,
                    code="session_fence_failed",
                    workspace=str(self.workspace),
                    factory_run_id=authority,
                    fenced_session_ids=tuple(fenced_session_ids),
                    conflicts=tuple(fence_failures),
                    execution_events=tuple(execution_events),
                )

            return ExpiredFactoryRunSessionFenceResultV1(
                ok=True,
                code=("expired_sessions_fenced" if candidates else "no_expired_sessions"),
                workspace=str(self.workspace),
                factory_run_id=authority,
                fenced_session_ids=tuple(fenced_session_ids),
                execution_events=tuple(execution_events),
            )

    def query_factory_run_settlement(self, *, factory_run_id: str) -> dict[str, object]:
        """Return stable TaskRuntime evidence for Factory child settlement."""

        authority = str(factory_run_id or "").strip()
        if not authority:
            raise ValueError("factory_run_id must be a non-empty string")
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            observable_rows = projection.rows_for_factory_run(authority)
            conflicts = self._reset_authority_conflicts(
                projection.rows,
                factory_run_id=authority,
            )
        active_sessions = [
            dict(conflict) for conflict in conflicts if str(conflict.get("kind") or "").startswith("active_")
        ]
        return {
            "schema_version": "task-runtime.factory-run-settlement/1",
            "factory_run_id": authority,
            "settled": not conflicts,
            "active_session_count": len(active_sessions),
            "active_sessions": active_sessions,
            "conflict_count": len(conflicts),
            "conflicts": [dict(conflict) for conflict in conflicts],
            "observable_source": projection.source,
            "observable_authoritative": projection.authoritative,
            "observable_row_count": len(observable_rows),
            "proof_sources": [
                "task_runtime.observable_task_rows",
                "task_runtime.execution_session_files",
            ],
        }

    @staticmethod
    def _reset_conflict_result(
        *,
        factory_run_id: str,
        conflicts: Sequence[Mapping[str, Any]],
    ) -> dict[str, object]:
        return {
            "ok": False,
            "code": "task_runtime_reset_authority_conflict",
            "reason": "TaskRuntime reset refused foreign ownership or an active execution session",
            "factory_run_id": factory_run_id,
            "conflicts": [dict(conflict) for conflict in conflicts],
            "conflict_count": len(conflicts),
            "cleared_paths": [],
            "failed_paths": [],
            "cleared_count": 0,
            "failed_count": 0,
            "tombstone_events": [],
            "tombstone_count": 0,
        }

    def reset_records(
        self,
        *,
        keep_plan: bool = False,
        factory_run_id: str | None = None,
    ) -> dict[str, object]:
        """Clear canonical taskboard rows and execution sessions.

        This intentionally lives in the runtime.task_runtime cell because
        ``runtime/tasks/*`` is task-runtime-owned state. Delivery-level reset
        orchestration may call this public capability, but other cells must not
        delete these files directly.
        """
        authority = str(factory_run_id or "").strip()
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            conflicts = self._reset_authority_conflicts(projection.rows, factory_run_id=authority)
            if conflicts:
                logger.warning(
                    "TaskRuntime reset rejected: factory_run_id=%s conflicts=%s",
                    authority or "<missing>",
                    len(conflicts),
                )
                return self._reset_conflict_result(
                    factory_run_id=authority,
                    conflicts=conflicts,
                )
            return self._reset_records_authorized(
                keep_plan=keep_plan,
                factory_run_id=authority,
            )

    def _reset_records_authorized(
        self,
        *,
        keep_plan: bool,
        factory_run_id: str,
    ) -> dict[str, object]:
        """Commit one preflight-approved reset while the stable reset lock is held."""

        cleared_paths: list[str] = []
        failed_paths: list[str] = []
        tombstone_events: list[dict[str, Any]] = []
        tombstoned_task_files: set[str] = set()

        tasks = self._list_file_task_entities()
        for task in tasks:
            task_id = int(task.id)
            task_file_name = f"task_{task_id}.json"
            task_row = self._augment_task_row(task.to_dict())
            previous_status = str(task_row.get("status") or "")
            task_metadata = dict(task_row.get("metadata") or {})
            runtime_execution = dict(task_metadata.get("runtime_execution") or {})
            runtime_execution.update(
                {
                    "effective_status": "removed",
                    "raw_status": "removed",
                    "resume_available": False,
                }
            )
            task_metadata["runtime_execution"] = runtime_execution
            tombstone_row = {
                **task_row,
                "status": "removed",
                "state": "removed",
                "execution_state": "removed",
                "running": False,
                "resume_available": False,
                "metadata": task_metadata,
            }
            event = self._append_execution_event(
                "runtime_reset_removed",
                task_row=tombstone_row,
                session=None,
                details={
                    "previous_status": previous_status,
                    "reset_keep_plan": bool(keep_plan),
                    "reset_factory_run_id": factory_run_id,
                },
            )
            fact_event_seq = _coerce_fact_event_seq(event.get("fact_event_seq"))
            if not str(event.get("fact_event_id") or "").strip() or fact_event_seq is None:
                logger.warning(
                    "TaskRuntime reset refused to delete task %s because its tombstone fact was not committed",
                    task_id,
                )
                failed_paths.append(str(self._board.tasks_dir / task_file_name))
                continue
            tombstone_events.append(event)
            tombstoned_task_files.add(task_file_name)

        with self._board.transaction():
            tasks_dir = self._board.tasks_dir
            tasks_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(tasks_dir.iterdir(), key=lambda item: str(item)):
                if keep_plan and child.name == "plan.json":
                    continue
                if child.name == ".max_id" or child.name.endswith(".lock"):
                    continue
                if (
                    child.name.startswith("task_")
                    and child.name.endswith(".json")
                    and not child.name.endswith(".session.json")
                    and child.name not in tombstoned_task_files
                ):
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
            "ok": not unique_failed,
            "code": "task_runtime_reset_completed" if not unique_failed else "task_runtime_reset_incomplete",
            "reason": "TaskRuntime reset completed" if not unique_failed else "TaskRuntime reset had failed paths",
            "factory_run_id": factory_run_id,
            "conflicts": [],
            "conflict_count": 0,
            "cleared_paths": unique_cleared,
            "failed_paths": unique_failed,
            "cleared_count": len(unique_cleared),
            "failed_count": len(unique_failed),
            "tombstone_events": tombstone_events,
            "tombstone_count": len(tombstone_events),
        }

    def reset_task_rows_for_reexecution(
        self,
        *,
        source: str = "",
        preserve_completed: bool = False,
        eligible_external_task_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Reset current task rows to a clean pre-execution state.

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the reexecution mutation owner. It preserves task ids and
            dependency fields, removes stale execution/session state, and must
            append one ``task_runtime.execution`` fact per row mutation.
            ``preserve_completed`` supports Director-local recovery: already
            verified task rows remain authoritative while failed, blocked, or
            incomplete rows are made claimable again.
            ``eligible_external_task_ids`` confines owner-local recovery to the
            canonical PM contract. Platform coordination rows (CE portfolio,
            settlement, verifier, repair) must retain their own lifecycle and
            must never be reopened as Director work.
        """

        eligible_ids: set[str] | None = None
        if eligible_external_task_ids is not None:
            normalized_ids = tuple(str(item or "").strip() for item in eligible_external_task_ids)
            if any(not item for item in normalized_ids):
                raise ValueError("eligible_external_task_ids must contain non-empty strings")
            eligible_ids = set(normalized_ids)

        reset_files: list[str] = []
        preserved_files: list[str] = []
        excluded_files: list[str] = []
        skipped_files: list[str] = []
        deleted_session_files: list[str] = []
        execution_events: list[dict[str, Any]] = []
        for task in self._list_file_task_entities():
            task_id = int(task.id)
            task_file_name = f"task_{task_id}.json"
            external_task_id = str(task.metadata.get("external_task_id") or task_id).strip()
            if eligible_ids is not None and external_task_id not in eligible_ids:
                excluded_files.append(task_file_name)
                continue
            previous_status = str(task.status.value if isinstance(task.status, TaskStatus) else task.status)
            if preserve_completed and previous_status == TaskStatus.COMPLETED.value:
                preserved_files.append(task_file_name)
                continue
            try:
                replaced = self._replace_task_row_for_reexecution(task.to_dict())
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Failed to reset task row %s for reexecution: %s", task_id, exc)
                skipped_files.append(task_file_name)
                continue
            deleted_session = self._delete_session_file(task_id)
            if deleted_session:
                deleted_session_files.append(Path(deleted_session).name)
            row = self._augment_task_row(replaced.to_dict())
            execution_events.append(
                self._append_execution_event(
                    "reexecution_reset",
                    task_row=row,
                    session=None,
                    details={
                        "source": str(source or "runtime.task_runtime.reexecution_reset"),
                        "previous_status": previous_status,
                    },
                )
            )
            reset_files.append(task_file_name)
        result = self._project_reexecution_prepare_result(
            operation="reset",
            changed_files=reset_files,
            skipped_files=skipped_files,
            deleted_session_files=deleted_session_files,
            execution_events=execution_events,
        )
        result["preserved_files"] = preserved_files
        result["excluded_files"] = excluded_files
        result["preserve_completed"] = bool(preserve_completed)
        result["eligible_external_task_ids"] = sorted(eligible_ids) if eligible_ids is not None else None
        return result

    def import_task_rows_for_reexecution(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        *,
        source: str = "",
        source_task_dir: str = "",
    ) -> dict[str, Any]:
        """Import existing task rows for retry/resume preparation.

        The source rows may come from a trusted runtime snapshot.  The task
        runtime cell still owns persistence: rows are normalized for
        reexecution, numeric task ids are preserved, stale sessions are removed,
        max-id bookkeeping is updated, and every imported row receives
        ``task_runtime.execution`` evidence.
        """

        imported_files: list[str] = []
        skipped_files: list[str] = []
        deleted_session_files: list[str] = []
        execution_events: list[dict[str, Any]] = []
        for payload in task_rows:
            try:
                task_id = self.normalize_task_id(payload.get("id"))
                if task_id is None:
                    raise ValueError("task row id is required")
                task_file_name = f"task_{task_id}.json"
                replaced = self._replace_task_row_for_reexecution(dict(payload))
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Failed to import task row for reexecution: %s", exc)
                skipped_files.append(str(payload.get("id") or "unknown"))
                continue
            deleted_session = self._delete_session_file(task_id)
            if deleted_session:
                deleted_session_files.append(Path(deleted_session).name)
            row = self._augment_task_row(replaced.to_dict())
            execution_events.append(
                self._append_execution_event(
                    "reexecution_imported",
                    task_row=row,
                    session=None,
                    details={
                        "source": str(source or "runtime.task_runtime.reexecution_import"),
                        "source_task_dir": str(source_task_dir or ""),
                    },
                )
            )
            imported_files.append(task_file_name)
        return self._project_reexecution_prepare_result(
            operation="import",
            changed_files=imported_files,
            skipped_files=skipped_files,
            deleted_session_files=deleted_session_files,
            execution_events=execution_events,
        )

    @staticmethod
    def inspect_reexecution_source_task_rows(task_dir: str | Path) -> dict[str, Any]:
        """Read source task-row payloads for controlled reexecution import.

        This is a task-runtime-owned read helper for Director resume.  Factory
        and bench entrypoints may discover candidate ``runtime/tasks``
        directories, but the task row JSON loading itself remains in the owner
        cell so raw ``task_*.json`` file layout knowledge does not leak into
        orchestration code.

        The helper is intentionally read-only and does not validate authority to
        import the rows.  Mutation remains in ``import_task_rows_for_reexecution``.

        Complexity:
            O(f + b) time over task row files and their JSON bodies, O(r) memory
            for accepted row payloads.
        """

        source_dir = Path(task_dir)
        if source_dir.name != "tasks" or not source_dir.is_dir():
            return {
                "task_rows": [],
                "task_files": [],
                "task_count": 0,
                "latest_mtime": 0.0,
            }
        try:
            task_files = sorted(
                path
                for path in source_dir.glob("task_*.json")
                if path.is_file() and not path.name.endswith(".session.json")
            )
        except OSError:
            task_files = []

        rows: list[dict[str, Any]] = []
        accepted_files: list[str] = []
        latest_mtime = 0.0
        for task_file in task_files:
            try:
                latest_mtime = max(latest_mtime, float(task_file.stat().st_mtime))
                payload = json.loads(task_file.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                rows.append(dict(payload))
                accepted_files.append(task_file.name)
        return {
            "task_rows": rows,
            "task_files": accepted_files,
            "task_count": len(rows),
            "latest_mtime": latest_mtime,
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

    def _task_entity_for_transition(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve the raw task entity required by execution transitions.

        Boundary:
            Execution finalization transitions need the persisted ``Task``
            entity for legacy fallback row projection when ``TaskBoard.update``
            returns ``None``.  Keep that raw owner-cell read centralized here;
            observable readers must continue using fact-overlaid task-row
            projections.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_claim_execution(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell task entity for claim/lease execution.

        Boundary:
            Claim execution owns the lease-backed transition from a raw task row
            into an execution session. This helper is the claim/lease owner-cell
            raw ``Task`` entity boundary: it normalizes caller input and performs
            the single ``TaskBoard.get`` lookup. It is not an execution
            finalization transition boundary; finalization paths must continue
            using ``_task_entity_for_transition``. Dependency-unblock refresh
            remains owned by ``claim_execution`` because it is a claim policy
            side effect, not a raw entity lookup concern. Observable readers
            must keep using fact-overlaid task-row projections.

        Complexity:
            O(k) to normalize the task-id token plus one O(1) in-memory
            ``TaskBoard`` lookup. Invalid ids return ``(None, None)``; missing
            rows return ``(normalized_id, None)`` so claim result shapes remain
            ``invalid_task_id`` / ``task_not_found``.

        Extension point:
            Future compare-and-swap or version checks for claim/lease ownership
            should attach here before session or lease mutation, keeping version
            validation local to the owner cell without changing downstream
            claim, renew, rejection, or execution-event semantics.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_owner_terminal_transition(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell task entity for row-only terminal transitions.

        Boundary:
            Owner-cell terminal row transitions without an execution lease need
            an O(1) raw ``TaskBoard.get`` pre-read to preserve missing-row
            ``None`` semantics.  Centralizing that boundary keeps future
            compare-and-swap/version checks local to the owner cell.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_dependency_side_effect(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell entity for dependency fan-out side effects.

        Boundary:
            Dependency fan-out belongs to ``runtime.task_runtime`` because it
            mutates sibling rows derived from ``blocked_by`` / ``blocks``. This
            helper is the owner-cell raw ``TaskBoard`` entity boundary for the
            pre-read before those row-local writes; observable readers must keep
            using fact-overlaid task-row projections.

        Complexity:
            O(k) to normalize the task-id token plus O(1) over the in-memory
            ``TaskBoard`` cache for one numeric row id. Missing rows return
            ``(normalized_id, None)`` so callers preserve legacy skip semantics.

        Extension point:
            Future compare-and-swap or version checks should attach here before
            fan-out writes, keeping version validation local to the owner cell
            without changing downstream update/write semantics.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    @staticmethod
    def _task_row_payload_for_reexecution(payload: Mapping[str, Any]) -> dict[str, Any]:
        reset = dict(payload)
        blocked_by_raw = reset.get("blocked_by")
        if not isinstance(blocked_by_raw, list):
            blocked_by_raw = reset.get("blockedBy") if isinstance(reset.get("blockedBy"), list) else []
        blocked_by_source: list[Any] = blocked_by_raw if isinstance(blocked_by_raw, list) else []
        blocked_by = list(blocked_by_source)
        reset["blocked_by"] = blocked_by
        reset["blockedBy"] = list(blocked_by)
        reset["status"] = "blocked" if blocked_by else "pending"
        reset["claimed_by"] = None
        reset["assignee"] = ""
        reset["started_at"] = None
        reset["completed_at"] = None
        reset["claimed_at"] = None
        reset["result_summary"] = ""
        reset["error_message"] = None
        metadata_raw = reset.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        for key in _REEXECUTION_METADATA_DROP_KEYS:
            metadata.pop(key, None)
        reset["metadata"] = metadata
        return reset

    def _replace_task_row_for_reexecution(self, payload: Mapping[str, Any]) -> Task:
        task = Task.from_dict(self._task_row_payload_for_reexecution(payload))
        with self._board.transaction():
            self._board._cache[int(task.id)] = task
            self._board._save_task(task)
            if int(task.id) > self._board._load_max_id():
                self._board._save_max_id(int(task.id))
        return task

    def _delete_session_file(self, task_id: int) -> str:
        session_path = Path(resolve_runtime_path(self._workspace, self._session_logical_path(task_id)))
        with self._get_session_lock(task_id):
            if not session_path.is_file():
                return ""
            session_path.unlink()
        with self._session_locks_meta:
            self._session_locks.pop(task_id, None)
        return str(session_path)

    @staticmethod
    def _project_reexecution_prepare_result(
        *,
        operation: str,
        changed_files: list[str],
        skipped_files: list[str],
        deleted_session_files: list[str],
        execution_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        failed_events = [dict(event) for event in execution_events if not bool(event.get("ok"))]
        return {
            "success": not skipped_files and not failed_events,
            "operation": operation,
            "changed_files": list(changed_files),
            "reset_files": list(changed_files) if operation == "reset" else [],
            "imported_files": list(changed_files) if operation == "import" else [],
            "skipped_files": list(skipped_files),
            "deleted_session_files": list(deleted_session_files),
            "execution_events": [dict(event) for event in execution_events],
            "failed_execution_events": failed_events,
            "changed_count": len(changed_files),
            "skipped_count": len(skipped_files),
            "deleted_session_count": len(deleted_session_files),
        }

    def task_exists(self, task_id: Any) -> bool:
        """Return whether a task row exists in the observable read model.

        Boundary:
            Public existence check.  Resolves through
            :meth:`_resolve_observable_task_row` (the same observable
            projection that powers :meth:`get_task`) so callers consult the
            fact-overlaid read model instead of the raw ``TaskBoard`` row.
            ``normalize_task_id`` semantics are preserved (an unparseable
            id returns ``False``) and the read is a strict subset of the
            ``list_observable_task_rows`` walk, so no extra fact query or
            file scan is triggered beyond what the projection already
            performs.

        Complexity:
            O(r + f) time and memory, inherited from
            :meth:`list_observable_task_rows`.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return False
        return self._resolve_observable_task_row(normalized) is not None

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
        matches: list[dict[str, Any]] = []
        for row in self.list_observable_task_rows():
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            if self._metadata_matches_external_task_id(metadata, token):
                matches.append(dict(row))
        return self._prefer_live_external_task_row(matches)

    @staticmethod
    def _prefer_live_external_task_row(matches: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
        """Prefer a rematerialized pending/ready row over a stale terminal sibling.

        Live L1-10: factory drain reset left TASK-1's numeric row ``1`` terminal
        in the fact overlay, then rematerialized pending row ``6`` with the same
        ``external_task_id``. First-match lookup claimed ``1`` and failed with
        ``task_terminal``.
        """

        if not matches:
            return None

        def sort_key(row: dict[str, Any]) -> tuple[int, int]:
            seq = row.get("fact_event_seq")
            seq_n = int(seq) if isinstance(seq, int) and not isinstance(seq, bool) else 0
            row_id = row.get("id")
            id_n = int(row_id) if isinstance(row_id, int) and not isinstance(row_id, bool) else 0
            return (seq_n, id_n)

        live = [
            row for row in matches if not is_terminal_task_row_status(row.get("execution_state") or row.get("status"))
        ]
        pool: list[dict[str, Any]] = live or list(matches)
        chosen = pool[0]
        chosen_key = sort_key(chosen)
        for row in pool[1:]:
            key = sort_key(row)
            if key > chosen_key:
                chosen = row
                chosen_key = key
        return dict(chosen)

    @staticmethod
    def _execution_fact_factory_run_id(task_row: Mapping[str, Any]) -> str:
        """Return Factory run identity recorded by the latest execution fact."""

        metadata = task_row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        fact = metadata_map.get("task_runtime_execution_fact")
        fact_map = fact if isinstance(fact, Mapping) else {}
        return str(fact_map.get("factory_run_id") or "").strip()

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
        _raise_retired_entity_api("create", "create_task_row")

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

        _task, row, execution_event, reverse_dependency_events = self._create_with_execution_event(
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
            execution_events=(execution_event, *reverse_dependency_events),
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
    ) -> tuple[Task, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
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
        reverse_dependency_events = self._apply_reverse_dependency_links(
            created_task_id=int(task.id),
            blocker_ids=self._row_blocker_ids(row),
        )
        return task, row, execution_event, reverse_dependency_events

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

        _, row, created_event, reverse_dependency_events = self._create_with_execution_event(
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
            execution_events=(created_event, *reverse_dependency_events, execution_event),
        )

    def bind_task_to_factory_run(
        self,
        command: BindRuntimeTaskToFactoryRunCommandV1,
    ) -> RuntimeTaskFactoryRunBindingResultV1:
        """Bind an existing task row to one Factory run with fact evidence.

        ``ensure_task_row`` remains creation-only. This explicit boundary owns
        write-once binding, conflict detection, and recovery when a prior row
        write succeeded but its execution-fact append did not.

        Complexity:
            O(r + f + n) time and O(r + f + n) memory for observable lookup
            plus one O(n) row compare-and-set, where ``n`` is row size.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            BindRuntimeTaskToFactoryRunCommandV1,
        )

        if not isinstance(command, BindRuntimeTaskToFactoryRunCommandV1):
            raise TypeError("command must be BindRuntimeTaskToFactoryRunCommandV1")
        if Path(command.workspace).resolve() != Path(self.workspace).resolve():
            raise ValueError("command workspace must match TaskRuntimeService workspace")

        observable_row = self._resolve_observable_task_row(command.task_id)
        if observable_row is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row does not exist",
                workspace=self.workspace,
                task_id=command.task_id,
                factory_run_id=command.factory_run_id,
            )
        normalized_task_id = self.normalize_task_id(observable_row.get("id"))
        if normalized_task_id is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row has no canonical numeric identity",
                workspace=self.workspace,
                task_id=command.task_id,
                factory_run_id=command.factory_run_id,
                task_row=observable_row,
            )

        fact_factory_run_id = self._execution_fact_factory_run_id(observable_row)
        if fact_factory_run_id and fact_factory_run_id != command.factory_run_id:
            return _build_factory_run_binding_result(
                ok=False,
                code="factory_run_binding_conflict",
                reason="TaskRuntime execution fact is bound to another Factory run",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=fact_factory_run_id,
                task_row=observable_row,
            )
        try:
            mutation = self._board.bind_factory_run_id(
                normalized_task_id,
                command.factory_run_id,
            )
        except TaskFactoryRunBindingConflictError as exc:
            return _build_factory_run_binding_result(
                ok=False,
                code="factory_run_binding_conflict",
                reason=str(exc),
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=exc.existing_factory_run_id,
                task_row=observable_row,
            )
        if mutation is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row disappeared before Factory run binding",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
            )

        row = self._augment_task_row(mutation.task.to_dict())
        if not mutation.row_updated and fact_factory_run_id == command.factory_run_id:
            return _build_factory_run_binding_result(
                ok=True,
                code="factory_run_already_bound",
                reason="Factory run binding already has execution-fact evidence",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=command.factory_run_id,
                event_recorded=True,
                idempotent=True,
                task_row=row,
            )

        execution_event = self._append_execution_event(
            "factory_run_bound",
            task_row=row,
            session=None,
            details={
                "factory_run_id": command.factory_run_id,
                "previous_factory_run_id": mutation.previous_factory_run_id,
                "row_updated": mutation.row_updated,
            },
        )
        event_recorded = bool(execution_event.get("fact_event_id"))
        if execution_event.get("ok") is not True or not event_recorded:
            return _build_factory_run_binding_result(
                ok=False,
                code="execution_event_append_failed",
                reason="Factory run binding did not reach the execution fact stream",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=command.factory_run_id,
                row_updated=mutation.row_updated,
                event_recorded=event_recorded,
                task_row=row,
                execution_event=execution_event,
            )

        return _build_factory_run_binding_result(
            ok=True,
            code="factory_run_bound" if mutation.row_updated else "factory_run_binding_recovered",
            reason=(
                "Factory run binding persisted and recorded"
                if mutation.row_updated
                else "Factory run binding execution-fact evidence recovered"
            ),
            workspace=self.workspace,
            task_id=str(normalized_task_id),
            factory_run_id=command.factory_run_id,
            existing_factory_run_id=command.factory_run_id,
            row_updated=mutation.row_updated,
            event_recorded=True,
            task_row=row,
            execution_event=execution_event,
        )

    def get(self, task_id: Any) -> Task | None:
        _raise_retired_entity_api("get", "get_task")

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        """Return the task-runtime observable read model for a single task row.

        This is a public read projection that must surface the
        ``task_runtime.execution`` fact overlay, not raw ``TaskBoard`` state.
        It derives the returned row from ``list_observable_task_rows()`` so
        callers always observe the converged fact-overlaid read model.

        Lookup order preserves the historical external-token priority: an
        external id (matching ``external_task_id`` / ``pm_task_id`` /
        ``source_task_id`` / ``task_id`` metadata aliases on any observable
        row) wins over numeric id matching. Numeric id matching then falls
        back to ``normalize_task_id(row.get("id"))`` against the same
        observable set.

        Boundary:
            Read-only. Never writes to workspace, never mints events, never
            consults ``self._board`` directly. ``ensure_task_row()`` keeps
            using ``_get_task_by_external_task_id()`` for creation
            idempotency so this change does not affect that path.
        """
        return self._resolve_observable_task_row(task_id)

    def _resolve_observable_task_row(self, task_id: Any) -> dict[str, Any] | None:
        """Resolve one task row from the observable read model.

        Helper extracted from :meth:`get_task` so the read projection can be
        reused without reintroducing raw ``TaskBoard`` access. Walks the
        observable rows once, attempting external-token matching first and
        numeric-id matching second; returns the first match as the
        fact-overlaid row.
        """
        try:
            observable_rows = self.list_observable_task_rows()
        except ValueError as exc:
            logger.warning(
                "Failed to load observable task rows for get_task lookup: %s",
                exc,
            )
            return None

        external_token = str(task_id or "").strip()
        if external_token:
            matches: list[dict[str, Any]] = []
            for row in observable_rows:
                if not isinstance(row, dict):
                    continue
                raw_metadata = row.get("metadata")
                metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
                if self._metadata_matches_external_task_id(metadata, external_token):
                    matches.append(dict(row))
            preferred = self._prefer_live_external_task_row(matches)
            if preferred is not None:
                return preferred

        normalized = self.normalize_task_id(task_id)
        if normalized is not None:
            for row in observable_rows:
                if not isinstance(row, dict):
                    continue
                if self.normalize_task_id(row.get("id")) == normalized:
                    return dict(row)

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
        _raise_retired_entity_api("update", "update_task_row")

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
