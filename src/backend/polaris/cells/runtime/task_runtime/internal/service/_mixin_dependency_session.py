"""Collaborator mixin for TaskRuntimeService (_mixin_dependency_session)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, cast

from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    Task,
    TaskBoardFileLockTimeoutError,
    TaskStatus,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    TaskRuntimeExecutionAttemptAuthorityOpenCodeV1,
    TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)

from ..execution_session import (
    TaskExecutionSession,
    TaskExecutionSessionWriteReceipt,
    _json_compatible_copy,
    is_terminal_session_status,
    is_terminal_task_row_status,
    normalize_positive_int,
    sanitize_summary,
    task_row_status_counts,
    terminal_session_timestamp,
)
from ._helpers import (
    _DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH,
    _DIRECTED_EFFECT_RECOVERY_LEASE_SCHEMA_V1,
    TaskExecutionSessionWriteConflictError,
    _canonical_sha256,
    _LockedSessionSuspendResult,
    _terminal_task_status_for_session,
    logger,
)
from ._late_bindings import (
    utc_now,
    utc_now_iso,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        ReconcileAmbiguousDirectedEffectsCommandV1,
    )
    from polaris.cells.runtime.task_runtime.public.service import (
        TaskRuntimeExecutionAttemptAuthorityV1,
    )


class _DependencySessionMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

    def _suspend_active_session_for_run_locked(
        self,
        task_id: int,
        *,
        run_id: str,
        reason: str,
    ) -> _LockedSessionSuspendResult:
        """Suspend one active run-owned session while caller holds session locks."""

        session = self._read_session_locked(task_id)
        if session is None:
            return _LockedSessionSuspendResult(session=None, session_written=False)
        if str(session.run_id or "").strip() != run_id:
            return _LockedSessionSuspendResult(session=None, session_written=False)
        if session.status != "active":
            return _LockedSessionSuspendResult(session=None, session_written=False)

        if self._has_pending_terminal_intent(session):
            terminal_snapshot = self._find_projected_runtime_execution_session_locked(task_id)
            if self._terminal_projection_can_restore_pending_intent_locked(
                task_id,
                active_session=session,
                terminal_session=terminal_snapshot,
            ):
                assert terminal_snapshot is not None
                return _LockedSessionSuspendResult(
                    session=session,
                    session_written=self._write_session_locked(session),
                )

        pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
        if not pre_barrier.allowed:
            return _LockedSessionSuspendResult(
                session=session,
                session_written=False,
                blocker=pre_barrier,
            )
        session.mark_suspended(reason=reason, resumable=True)
        return _LockedSessionSuspendResult(
            session=session,
            session_written=self._write_session_locked(session),
        )

    def _terminal_projection_can_restore_pending_intent_locked(
        self,
        task_id: int,
        *,
        active_session: TaskExecutionSession,
        terminal_session: TaskExecutionSession | None,
    ) -> bool:
        """Authorize compatibility restore only for one exact, already-settled attempt."""

        if not self._same_terminal_session(terminal_session, active_session):
            return False
        assert terminal_session is not None
        task = self._task_entity_for_terminal_session_reconcile(task_id)
        terminal_task_status = _terminal_task_status_for_session(terminal_session.status)
        if task is None or not task.is_terminal or terminal_task_status is None or task.status != terminal_task_status:
            return False
        for field in (
            "task_id",
            "session_id",
            "attempt",
            "run_id",
            "worker_id",
            "role_id",
            "origin",
            "selection_source",
            "external_task_id",
        ):
            if getattr(active_session, field) != getattr(terminal_session, field):
                return False
        if active_session.terminal_transition_id != terminal_session.terminal_transition_id:
            return False
        active_intent = self._pending_terminal_intent(active_session)
        terminal_intent = self._pending_terminal_intent(terminal_session)
        if active_intent is None or terminal_intent is None or dict(active_intent) != dict(terminal_intent):
            return False
        active_proof = active_session.metadata.get("terminal_settlement_proof")
        terminal_proof = terminal_session.metadata.get("terminal_settlement_proof")
        if (
            not isinstance(active_proof, Mapping)
            or not isinstance(terminal_proof, Mapping)
            or dict(active_proof) != dict(terminal_proof)
        ):
            return False
        if active_session.metadata.get("settlement_identity_lease_expires_at") != terminal_session.metadata.get(
            "settlement_identity_lease_expires_at"
        ):
            return False
        return self._fulfilled_terminal_intent_pre_barrier_locked(terminal_session).allowed

    def list_ready(self) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.list_ready is retired; use list_ready_task_rows()")

    def wait_ready(self, timeout: float | None = None) -> bool:
        self.refresh_dependency_unblocks()
        return cast(bool, self._board.wait_ready(timeout=timeout))

    def add_ready_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        return cast(Callable[[], None], self._board.add_ready_listener(listener))

    def list_ready_task_rows(self) -> list[dict[str, Any]]:
        """Return ready rows after the compatibility dependency refresh.

        ``list_observable_task_rows`` is intentionally a read-only projection.
        Legacy worker-pool ready checks still need the old compatibility
        behaviour where dependency unblocks are refreshed before ready rows are
        selected, so the mutation stays explicit at this execution boundary.
        """

        self.refresh_dependency_unblocks()
        rows = self.list_observable_task_rows()
        ready_rows: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status in {"pending", "ready"} and not row.get("blocked_by"):
                ready_rows.append(row)
        return ready_rows

    def get_ready_tasks(self) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.get_ready_tasks is retired; use list_ready_task_rows()")

    def get_observable_task_row_stats(self) -> dict[str, Any]:
        """Return status counts from the task-runtime-owned observable rows.

        Boundary:
            This is a read-only projection over ``list_observable_task_rows()``.
            It intentionally counts the latest ``task_runtime.execution`` fact
            overlay instead of treating file-backed rows as the only truth.
            Selection and mutation paths must continue to use their explicit
            row/session APIs.

        Complexity:
            O(r + c) time and memory over observable rows and delegated coverage
            dictionaries.
        """

        stats = task_row_status_counts(self.list_observable_task_rows())
        stats["read_model_fallback_coverage"] = self.task_row_read_model_fallback_coverage()
        stats["projected_runtime_execution_session_fallback_coverage"] = (
            self.projected_runtime_execution_session_fallback_coverage()
        )
        stats["read_model_cutover_readiness"] = self.task_row_read_model_cutover_readiness()
        return stats

    def get_task_row_stats(self) -> dict[str, Any]:
        """Compatibility entrypoint for observable task-row status counts."""

        return self.get_observable_task_row_stats()

    def get_stats(self) -> dict[str, Any]:
        raise RuntimeError("TaskRuntimeService.get_stats is retired; use get_task_row_stats()")

    def refresh_dependency_unblocks(self) -> dict[str, Any]:
        """Normalize stale BLOCKED rows whose dependencies are now complete.

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the dependency-maintenance mutation owner. The dependency
            status source remains fact-aware; persisted entities are used only
            for row-local ``TaskBoard.update`` mutations and event evidence.

        Dependency status is anchored on the fact-overlay-aware projection
        (``_fact_overlaid_dependency_status_rows``) so that the latest
        authoritative ``task_runtime.execution`` completion facts can
        unblock downstream rows even when the file-backed rows are stale.
        Iteration still walks persisted ``Task`` objects because the mutation
        path needs ``TaskBoard.update`` rather than projected dicts.
        """

        changed: list[int] = []
        refreshed: list[int] = []
        failed: list[dict[str, Any]] = []
        execution_events: list[dict[str, Any]] = []
        inspected = 0
        tasks = self._list_file_task_entities()
        status_by_id = self._fact_overlaid_dependency_status_rows()
        # Backwards-compatible fallback: callers may pass a metadata-derived
        # dependency token that points at a row absent from the overlay. Make
        # sure persisted terminal statuses are still visible to the blocker
        # resolver without leaking unknown status tokens.
        for task in tasks:
            status_by_id.setdefault(int(task.id), task.status)
        for task in tasks:
            inspected += 1
            if task.status != TaskStatus.BLOCKED:
                continue

            explicit_blockers = self._active_dependency_ids(task.blocked_by, status_by_id)
            if explicit_blockers:
                if explicit_blockers != list(task.blocked_by or []):
                    previous_blockers = [int(blocker) for blocker in task.blocked_by or []]
                    updated = self._board.update(
                        int(task.id),
                        blocked_by=explicit_blockers,
                        allow_dependency_status=True,
                    )
                    if updated is None:
                        failed.append({"task_id": int(task.id), "reason": "task_update_failed"})
                    else:
                        row = self._augment_task_row(updated.to_dict())
                        refreshed.append(int(task.id))
                        execution_event = self._append_execution_event(
                            "dependency_blockers_refreshed",
                            task_row=row,
                            session=None,
                            details={
                                "previous_blockers": previous_blockers,
                                "active_blockers": [int(blocker) for blocker in explicit_blockers],
                            },
                        )
                        execution_events.append(execution_event)
                        if not bool(execution_event.get("ok")):
                            failed.append(
                                {
                                    "task_id": int(task.id),
                                    "reason": "execution_event_append_failed",
                                    "failure_class": "ledger_append_failed",
                                    "event_type": "dependency_blockers_refreshed",
                                    "error": str(
                                        execution_event.get("error") or execution_event.get("publish_error") or ""
                                    ),
                                }
                            )
                continue

            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            resolved_dependencies = self._metadata_dependency_task_ids(metadata)
            if resolved_dependencies and self._active_dependency_ids(resolved_dependencies, status_by_id):
                continue

            previous_blockers = [int(blocker) for blocker in task.blocked_by or []]
            updated = self._board.update(
                int(task.id),
                status=TaskStatus.PENDING,
                blocked_by=[],
                allow_dependency_status=True,
            )
            if updated is not None:
                row = self._augment_task_row(updated.to_dict())
                changed.append(int(task.id))
                execution_event = self._append_execution_event(
                    "dependencies_unblocked",
                    task_row=row,
                    session=None,
                    details={
                        "previous_blockers": previous_blockers,
                        "resolved_dependencies": [int(dep_id) for dep_id in resolved_dependencies],
                    },
                )
                execution_events.append(execution_event)
                if not bool(execution_event.get("ok")):
                    failed.append(
                        {
                            "task_id": int(task.id),
                            "reason": "execution_event_append_failed",
                            "failure_class": "ledger_append_failed",
                            "event_type": "dependencies_unblocked",
                            "error": str(execution_event.get("error") or execution_event.get("publish_error") or ""),
                        }
                    )
            else:
                failed.append({"task_id": int(task.id), "reason": "task_update_failed"})

        result: dict[str, Any] = {
            "inspected_count": inspected,
            "unblocked_count": len(changed),
            "unblocked_task_ids": changed,
            "refreshed_count": len(refreshed),
            "refreshed_task_ids": refreshed,
            "failed": failed,
            "execution_events": execution_events,
        }
        if any(str(item.get("failure_class") or "") == "ledger_append_failed" for item in failed):
            result["failure_class"] = "ledger_append_failed"
        return result

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
        """Return whether ``task`` still has a dependency that is not completed.

        Boundary:
            Read-only.  This helper exists so the claim path can reuse the
            same fact-overlaid dependency status projection that
            :meth:`refresh_dependency_unblocks` already trusts
            (:meth:`_fact_overlaid_dependency_status_rows`).  Without it,
            ``claim_execution`` would still consult the raw ``TaskBoard``
            status for each blocker, leaving a row-only dependency decision
            seam in the claim path that can disagree with the refresh step.

        Fail-closed semantics (any of the following => unresolved / blocked):

        * ``task.blocked_by`` is missing or empty, which means there is no
          unresolved dependency for this row;
        * a dependency id cannot be coerced to a positive int (the caller
          supplied a token the runtime cannot resolve);
        * a dependency id is not present in the fact-overlaid status map
          (the row is missing or unreadable);
        * the overlaid status is anything other than ``TaskStatus.COMPLETED``
          (including non-terminal, terminal-failed, terminal-cancelled,
          and unknown tokens).

        The overlay map itself falls back to the file-backed status when no
        authoritative ``task_runtime.execution`` fact exists for a row. This
        helper intentionally does not perform its own raw ``TaskBoard`` walk:
        a missing dependency in the overlay is treated as unresolved, keeping
        the fact-overlaid projection as the single dependency status source.

        Complexity:
            O(d + r + f) time and memory where ``d`` is the number of
            blockers for ``task``, ``r`` is the number of file-backed rows,
            and ``f`` is the number of latest fact rows; bounded by the
            ``_fact_overlaid_dependency_status_rows`` walk and so amortised
            once per call.
        """

        blocked_by = task.blocked_by if task.blocked_by is not None else []
        if not blocked_by:
            return False
        try:
            status_by_id = self._fact_overlaid_dependency_status_rows()
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Failing closed on unresolved dependency check for task_id=%s: overlay unavailable: %s",
                getattr(task, "id", None),
                exc,
            )
            return True

        for dependency_id in list(blocked_by):
            try:
                dep_id_int = int(dependency_id)
            except (TypeError, ValueError):
                logger.warning("Skipping non-integer dependency_id: %r", dependency_id)
                return True
            if dep_id_int <= 0:
                logger.warning("Skipping non-positive dependency_id: %r", dependency_id)
                return True
            dependency_status = status_by_id.get(dep_id_int)
            if dependency_status is None:
                return True
            if dependency_status != TaskStatus.COMPLETED:
                return True
        return False

    def _get_session_lock(self, task_id: int) -> threading.RLock:
        """Return the per-task session lock, creating it on demand."""
        with self._session_locks_meta:
            if task_id not in self._session_locks:
                self._session_locks[task_id] = threading.RLock()
            return self._session_locks[task_id]

    def _get_settlement_projection_lock(self, task_id: int) -> threading.RLock:
        """Return the per-task lock for effects after session winner selection."""

        with self._settlement_projection_locks_meta:
            if task_id not in self._settlement_projection_locks:
                self._settlement_projection_locks[task_id] = threading.RLock()
            return self._settlement_projection_locks[task_id]

    def _session_file_lock_path(self, task_id: int) -> Path:
        """Return the cooperative cross-process lock path for one session file."""

        return Path(self._kernel_fs.resolve_path(f"runtime/tasks/.task_{int(task_id)}.session.json.lock"))

    def _directed_effect_recovery_lease_file_lock_path(self) -> Path:
        """Return the single cross-process recovery authority lock for this workspace."""

        return Path(self._kernel_fs.resolve_path("runtime/tasks/.directed_effect_recovery.lease.lock"))

    @staticmethod
    def _directed_effect_recovery_lease_record(body: Mapping[str, Any]) -> dict[str, Any]:
        detached = _json_compatible_copy(dict(body))
        if not isinstance(detached, dict):
            raise TypeError("directed effect recovery lease body must be an object")
        return {**detached, "record_hash": _canonical_sha256(detached)}

    def _read_directed_effect_recovery_lease_locked(self) -> dict[str, Any] | None:
        if not self._kernel_fs.exists(_DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH):
            return None
        payload = self._kernel_fs.read_json(_DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH)
        if not isinstance(payload, dict):
            raise ValueError("directed effect recovery lease must be an object")
        body = {key: value for key, value in payload.items() if key != "record_hash"}
        if payload.get("record_hash") != _canonical_sha256(body):
            raise ValueError("directed effect recovery lease hash mismatch")
        if payload.get("schema_version") != _DIRECTED_EFFECT_RECOVERY_LEASE_SCHEMA_V1:
            raise ValueError("directed effect recovery lease schema mismatch")
        if payload.get("workspace") != str(Path(self.workspace).expanduser().resolve()):
            raise ValueError("directed effect recovery lease workspace mismatch")
        if payload.get("status") not in {"active", "released"}:
            raise ValueError("directed effect recovery lease status invalid")
        for field_name in ("lease_id", "owner_epoch"):
            value = payload.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"directed effect recovery lease {field_name} invalid")
        owner_pid = payload.get("owner_pid")
        if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
            raise ValueError("directed effect recovery lease owner_pid invalid")
        expires_at_epoch = payload.get("expires_at_epoch")
        if isinstance(expires_at_epoch, bool) or not isinstance(expires_at_epoch, (int, float)):
            raise ValueError("directed effect recovery lease expires_at_epoch invalid")
        return dict(payload)

    def _claim_directed_effect_recovery_lease_locked(
        self,
        *,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        lease_id: str,
        owner_epoch: str,
        owner_pid: int,
        deadline_monotonic: float,
    ) -> dict[str, Any] | None:
        existing = self._read_directed_effect_recovery_lease_locked()
        now_epoch = time.time()
        if (
            existing is not None
            and existing.get("status") == "active"
            and float(existing["expires_at_epoch"]) > now_epoch
        ):
            return {
                "code": "recovery_lease_active",
                "lease_id": str(existing["lease_id"]),
                "owner_pid": int(existing["owner_pid"]),
                "owner_epoch": str(existing["owner_epoch"]),
                "expires_at_epoch": float(existing["expires_at_epoch"]),
                "factory_run_id": str(existing.get("factory_run_id") or ""),
            }
        remaining_seconds = deadline_monotonic - time.monotonic()
        if remaining_seconds <= 0:
            return {"code": "recovery_deadline_exceeded"}
        body = {
            "schema_version": _DIRECTED_EFFECT_RECOVERY_LEASE_SCHEMA_V1,
            "workspace": str(Path(self.workspace).expanduser().resolve()),
            "status": "active",
            "lease_id": lease_id,
            "owner_epoch": owner_epoch,
            "owner_pid": owner_pid,
            "factory_run_id": command.factory_run_id,
            "authority_kind": command.authority_kind,
            "actor": command.actor,
            "reason": command.reason,
            "acquired_at_epoch": now_epoch,
            "expires_at_epoch": now_epoch + remaining_seconds,
            "replaced_expired_lease_id": (
                str(existing.get("lease_id") or "")
                if existing is not None and existing.get("status") == "active"
                else ""
            ),
        }
        self._kernel_fs.write_json_atomic(
            _DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH,
            self._directed_effect_recovery_lease_record(body),
            indent=2,
            ensure_ascii=False,
        )
        return None

    def _release_directed_effect_recovery_lease_locked(
        self,
        *,
        lease_id: str,
        owner_epoch: str,
        owner_pid: int,
    ) -> None:
        current = self._read_directed_effect_recovery_lease_locked()
        if current is None:
            raise ValueError("directed effect recovery lease disappeared before release")
        if (
            current.get("status") != "active"
            or current.get("lease_id") != lease_id
            or current.get("owner_epoch") != owner_epoch
            or current.get("owner_pid") != owner_pid
        ):
            raise ValueError("directed effect recovery lease authority changed before release")
        body = {key: value for key, value in current.items() if key != "record_hash"}
        body["status"] = "released"
        body["released_at_epoch"] = time.time()
        self._kernel_fs.write_json_atomic(
            _DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH,
            self._directed_effect_recovery_lease_record(body),
            indent=2,
            ensure_ascii=False,
        )

    def _settlement_projection_file_lock_path(self, task_id: int) -> Path:
        """Return the independent cooperative lock for settlement projections."""

        return Path(self._kernel_fs.resolve_path(f"runtime/tasks/.task_{int(task_id)}.settlement.lock"))

    def _session_logical_path(self, task_id: int) -> str:
        return f"runtime/tasks/task_{int(task_id)}.session.json"

    def _execution_attempt_identity_from_session(
        self,
        session: TaskExecutionSession,
    ) -> TaskRuntimeExecutionAttemptIdentityV1:
        """Project the canonical execution-attempt identity from a session."""

        return TaskRuntimeExecutionAttemptIdentityV1(
            workspace=self.workspace,
            task_id=int(session.task_id),
            external_task_id=str(session.external_task_id or "").strip(),
            session_id=session.session_id,
            attempt=int(session.attempt),
            role_id=session.role_id,
            worker_id=session.worker_id,
            run_id=session.run_id,
            lease_expires_at=session.lease_expires_at,
        )

    def _claim_result_with_execution_attempt(
        self,
        result: dict[str, Any],
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Add a stable typed attempt projection without changing claim semantics."""

        if session is not None:
            result["execution_attempt"] = self._execution_attempt_identity_from_session(session).to_record()
        return result

    def _execution_attempt_validation_verdict(
        self,
        *,
        valid: bool,
        code: TaskRuntimeExecutionAttemptValidationCodeV1,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        evidence: Mapping[str, Any] | None = None,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Build a detached, fail-closed execution-attempt verdict."""

        return TaskRuntimeExecutionAttemptValidationVerdictV1(
            valid=valid,
            code=code,
            workspace=self.workspace,
            identity=identity,
            evidence=dict(evidence or {}),
        )

    def validate_execution_attempt(
        self,
        query: ValidateTaskRuntimeExecutionAttemptQueryV1,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Validate one persisted execution attempt without renewing or writing it."""

        identity = query.identity
        if query.workspace != self.workspace or identity.workspace != query.workspace:
            return self._execution_attempt_validation_verdict(
                valid=False,
                code="workspace_mismatch",
                identity=identity,
                evidence={
                    "query_workspace": query.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=query.lock_timeout_seconds):
            return self._execution_attempt_validation_verdict(
                valid=False,
                code="file_lock_timeout",
                identity=identity,
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": query.lock_timeout_seconds},
            )
        try:
            remaining = query.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": query.lock_timeout_seconds},
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    return self._validate_execution_attempt_locked(identity)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": query.lock_timeout_seconds,
                    },
                )
        finally:
            session_lock.release()

    def open_execution_attempt_authority(
        self,
        command: OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Open a non-durable authority only while validation locks are held.

        This operation deliberately performs no heartbeat, session write, row
        projection, or FactStream append. Constructing the local handle inside
        the validation critical section linearizes open against terminal settle.
        """

        if not isinstance(command, OpenTaskRuntimeExecutionAttemptAuthorityCommandV1):
            raise TypeError("command must be OpenTaskRuntimeExecutionAttemptAuthorityCommandV1")
        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            validation = self._execution_attempt_validation_verdict(
                valid=False,
                code="workspace_mismatch",
                identity=identity,
                evidence={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )
            return self._execution_attempt_authority_open_verdict(validation)

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=command.lock_timeout_seconds):
            validation = self._execution_attempt_validation_verdict(
                valid=False,
                code="file_lock_timeout",
                identity=identity,
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": command.lock_timeout_seconds},
            )
            return self._execution_attempt_authority_open_verdict(validation)
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                validation = self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": command.lock_timeout_seconds},
                )
                return self._execution_attempt_authority_open_verdict(validation)
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    try:
                        validation = self._validate_execution_attempt_locked(
                            identity,
                            raise_infrastructure_errors=True,
                        )
                    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
                        return self._execution_attempt_authority_open_infrastructure_failure(
                            identity,
                            stage="session_read",
                            exc=exc,
                        )
                    return self._execution_attempt_authority_open_verdict(validation)
            except TaskBoardFileLockTimeoutError:
                validation = self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
                return self._execution_attempt_authority_open_verdict(validation)
            except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
                return self._execution_attempt_authority_open_infrastructure_failure(
                    identity,
                    stage="cooperative_session_file_lock",
                    exc=exc,
                )
        finally:
            session_lock.release()

    def _execution_attempt_authority_open_infrastructure_failure(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        stage: str,
        exc: BaseException,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Return a detached, typed refusal for authority-open infrastructure failures."""

        return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
            success=False,
            code="authority_open_internal_error",
            workspace=self.workspace,
            identity=identity,
            evidence={
                "stage": stage,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )

    def _execution_attempt_authority_open_verdict(
        self,
        validation: TaskRuntimeExecutionAttemptValidationVerdictV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Map a locked validation result to a detached, fail-closed open verdict."""

        if not validation.valid:
            return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
                success=False,
                code=cast(TaskRuntimeExecutionAttemptAuthorityOpenCodeV1, validation.code),
                workspace=self.workspace,
                identity=validation.identity,
                evidence=validation.evidence,
            )
        try:
            authority = self._create_execution_attempt_authority_locked(validation.identity)
        except Exception as exc:  # noqa: BLE001 - construction must not claim authority on failure.
            return self._execution_attempt_authority_open_infrastructure_failure(
                validation.identity,
                stage="authority_construction",
                exc=exc,
            )
        return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
            success=True,
            code="valid",
            workspace=self.workspace,
            identity=validation.identity,
            authority=authority,
            evidence=validation.evidence,
        )

    @staticmethod
    def _create_execution_attempt_authority_locked(
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityV1:
        """Construct the process-local capability after the durable check passes."""

        from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeExecutionAttemptAuthorityV1

        return TaskRuntimeExecutionAttemptAuthorityV1(identity)

    def _validate_execution_attempt_locked(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        raise_infrastructure_errors: bool = False,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Validate one attempt while its local and cooperative locks are held."""

        with self._board.transaction():
            session_path = self._session_logical_path(identity.task_id)
            session = self._read_session_locked(
                identity.task_id,
                raise_infrastructure_errors=raise_infrastructure_errors,
            )
            if session is None:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_not_found",
                    identity=identity,
                    evidence={"task_id": identity.task_id, "session_path": session_path},
                )

            observed_identity = self._execution_attempt_identity_from_session(session)
            evidence = {"observed": observed_identity.to_record()}
            if session.task_id != identity.task_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_task_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.session_id != identity.session_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.attempt != identity.attempt:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="attempt_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.role_id != identity.role_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="role_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.worker_id != identity.worker_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="worker_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.run_id != identity.run_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="run_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.external_task_id != identity.external_task_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="external_task_id_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            # R145: lease_expires_at is a renewable same-owner TTL, not a fencing
            # token. Concurrent heartbeats (director loop, DEO pre-claim, batch
            # prepare) advance the stored lease while multi-step DEO prepare still
            # holds the pre-heartbeat identity. Exact equality here caused
            # deo_inventory_ready_failed after seal+admit left orphan parents and
            # dropped write batches (r144 TASK-2). Authority steal is already
            # covered by session/attempt/worker/role/run mismatches above.
            if session.status != "active":
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_not_active",
                    identity=identity,
                    evidence=evidence,
                )
            if session.is_expired(now=utc_now()):
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_lease_expired",
                    identity=identity,
                    evidence=evidence,
                )
            return self._execution_attempt_validation_verdict(
                valid=True,
                code="valid",
                identity=identity,
                evidence=evidence,
            )

    @staticmethod
    def _session_payload_text(payload: Any) -> str:
        """Return the exact UTF-8 JSON text used by ``write_json_atomic``."""

        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    @classmethod
    def _session_payload_hash(cls, payload: Any) -> str:
        """Return the write-format UTF-8 JSON payload hash for session receipts."""

        return hashlib.sha256(cls._session_payload_text(payload).encode("utf-8")).hexdigest()

    def _read_current_session_payload_hash(self, logical_path: str) -> str:
        """Return the current UTF-8 session file hash, or empty string when absent."""

        if not self._kernel_fs.exists(logical_path):
            return ""
        try:
            session_text = self._kernel_fs.read_text(logical_path, encoding="utf-8")
            return hashlib.sha256(session_text.encode("utf-8")).hexdigest()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Failed to hash task runtime session text %s: %s", logical_path, exc)
            return ""

    def _assert_session_payload_unchanged(self, session_path: str, *, before_hash: str) -> None:
        """Fail closed if a session JSON file changed before atomic replacement."""

        current_hash = self._read_current_session_payload_hash(session_path)
        if current_hash == before_hash:
            return

        before_label = before_hash or "<absent>"
        current_label = current_hash or "<absent>"
        logger.warning(
            "TaskRuntime session write conflict: session_path=%s before_hash=%s current_hash=%s",
            session_path,
            before_label,
            current_label,
        )
        raise TaskExecutionSessionWriteConflictError(
            "TaskRuntime session write conflict: "
            f"session_path={session_path!r} before_hash={before_label!r} "
            f"current_hash={current_label!r}"
        )

    def _record_session_write_receipt(
        self,
        *,
        session: TaskExecutionSession,
        session_path: str,
        before_hash: str,
        after_hash: str,
        operation: str,
        preserved_terminal_session: bool,
    ) -> None:
        receipt = TaskExecutionSessionWriteReceipt(
            task_id=session.task_id,
            session_id=session.session_id,
            session_path=session_path,
            before_hash=before_hash,
            after_hash=after_hash,
            operation=operation,
            written_at=utc_now_iso(),
            preserved_terminal_session=preserved_terminal_session,
        )
        with self._session_write_receipt_lock:
            self._last_session_write_receipt = receipt
            task_id = self.normalize_task_id(session.task_id)
            session_id = str(session.session_id or "").strip()
            if task_id is not None and session_id:
                self._session_write_receipts_by_identity[(task_id, session_id)] = receipt

    def _read_session(self, task_id: int) -> TaskExecutionSession | None:
        """Read a session under the per-task local and cooperative file locks."""

        task_id = int(task_id)
        with (
            self._get_session_lock(task_id),
            self._board._file_lock(self._session_file_lock_path(task_id)),
        ):
            return self._read_session_locked(task_id)

    def _read_session_locked(
        self,
        task_id: int,
        *,
        raise_infrastructure_errors: bool = False,
    ) -> TaskExecutionSession | None:
        """Read a session while the caller holds both per-task session locks."""

        logical_path = self._session_logical_path(task_id)
        if not self._kernel_fs.exists(logical_path):
            return None
        try:
            payload = self._kernel_fs.read_json(logical_path)
        except (OSError, UnicodeError, RuntimeError):
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to read task runtime session %s", logical_path, exc_info=True)
            return None
        except ValueError as exc:
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to read task runtime session %s: %s", logical_path, exc)
            return None
        if not isinstance(payload, dict):
            if raise_infrastructure_errors:
                raise ValueError("task runtime session payload must be an object")
            return None
        try:
            return TaskExecutionSession.from_dict(payload)
        except (OSError, UnicodeError, RuntimeError):
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to parse task runtime session %s", logical_path, exc_info=True)
            return None
        except ValueError as exc:
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to parse task runtime session %s: %s", logical_path, exc)
            return None

    def _write_session(
        self,
        session: TaskExecutionSession,
        *,
        allow_terminal_downgrade: bool = False,
    ) -> bool:
        task_id = int(session.task_id)
        with (
            self._get_session_lock(task_id),
            self._board._file_lock(self._session_file_lock_path(task_id)),
        ):
            return self._write_session_locked(
                session,
                allow_terminal_downgrade=allow_terminal_downgrade,
            )

    def _write_session_locked(
        self,
        session: TaskExecutionSession,
        *,
        allow_terminal_downgrade: bool = False,
    ) -> bool:
        session_path = self._session_logical_path(session.task_id)
        if is_terminal_session_status(session.status):
            persisted_session = self._read_session_locked(session.task_id)
            same_session = (
                persisted_session is not None
                and str(persisted_session.session_id or "").strip() == str(session.session_id or "").strip()
            )
            persisted_transition_id = (
                str(persisted_session.terminal_transition_id or "").strip()
                if same_session and persisted_session is not None
                else ""
            )
            if persisted_transition_id:
                session.terminal_transition_id = persisted_transition_id
            else:
                session.ensure_terminal_transition_id()
        if not allow_terminal_downgrade and not is_terminal_session_status(session.status):
            terminal_session = self._find_terminal_session_snapshot_locked(session)
            if terminal_session is not None:
                self._copy_session_state(session, terminal_session)
                terminal_payload = terminal_session.to_dict()
                before_hash = self._read_current_session_payload_hash(session_path)
                after_hash = self._session_payload_hash(terminal_payload)
                self._assert_session_payload_unchanged(session_path, before_hash=before_hash)
                self._kernel_fs.write_json_atomic(
                    session_path,
                    terminal_payload,
                    indent=2,
                    ensure_ascii=False,
                )
                self._record_session_write_receipt(
                    session=terminal_session,
                    session_path=session_path,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    operation="replace",
                    preserved_terminal_session=True,
                )
                return False
        session_payload = session.to_dict()
        before_hash = self._read_current_session_payload_hash(session_path)
        after_hash = self._session_payload_hash(session_payload)
        self._assert_session_payload_unchanged(session_path, before_hash=before_hash)
        self._kernel_fs.write_json_atomic(
            session_path,
            session_payload,
            indent=2,
            ensure_ascii=False,
        )
        self._record_session_write_receipt(
            session=session,
            session_path=session_path,
            before_hash=before_hash,
            after_hash=after_hash,
            operation="replace",
            preserved_terminal_session=False,
        )
        return True

    def _find_terminal_session_snapshot_locked(
        self,
        incoming: TaskExecutionSession,
    ) -> TaskExecutionSession | None:
        """Find a terminal snapshot while the caller holds session locks."""

        disk_session = self._read_session_locked(incoming.task_id)
        if self._same_terminal_session(disk_session, incoming):
            return disk_session

        metadata_session = self._find_projected_runtime_execution_session_locked(incoming.task_id)
        if self._same_terminal_session(metadata_session, incoming):
            return metadata_session
        return None

    def _find_terminal_session_snapshot(
        self,
        incoming: TaskExecutionSession,
    ) -> TaskExecutionSession | None:
        """Find a terminal snapshot from session JSON or row projections."""

        disk_session = self._read_session(incoming.task_id)
        if self._same_terminal_session(disk_session, incoming):
            return disk_session

        metadata_session = self._find_projected_runtime_execution_session(incoming.task_id)
        if self._same_terminal_session(metadata_session, incoming):
            return metadata_session
        return None

    def _find_projected_runtime_execution_session(
        self,
        task_id: int,
    ) -> TaskExecutionSession | None:
        """Return ``metadata.runtime_execution`` from read-model projections only."""

        fact_row = self._find_latest_execution_fact_row_for_task(task_id)
        fact_session = self._runtime_execution_session_from_projected_row(fact_row)
        if fact_session is not None:
            return fact_session

        if not self._projected_runtime_execution_session_file_fallback_allowed():
            return None
        return self._find_projected_runtime_execution_session_from_file_rows(
            task_id,
            augment_runtime_state=True,
        )

    def _find_projected_runtime_execution_session_locked(
        self,
        task_id: int,
    ) -> TaskExecutionSession | None:
        """Return projected runtime metadata without session row augmentation.

        This locked path is used while session writes are evaluating terminal
        snapshots. It intentionally preserves the non-augmenting file fallback
        without consulting cutover readiness because readiness may scan
        file-backed rows and re-enter session state projection.
        """

        fact_row = self._find_latest_execution_fact_row_for_task(task_id)
        fact_session = self._runtime_execution_session_from_projected_row(fact_row)
        if fact_session is not None:
            return fact_session

        return self._find_projected_runtime_execution_session_from_file_rows(
            task_id,
            augment_runtime_state=False,
        )

    def _projected_runtime_execution_session_file_fallback_allowed(self) -> bool:
        """Gate the migration-period file fallback without reading file rows directly.

        The readiness projection owns the compatibility signal. During migration,
        malformed or older readiness payloads fail open so existing deployments do
        not lose projected runtime-execution sessions before the read model is
        fully cut over.
        """

        readiness = self.task_row_read_model_cutover_readiness()
        if not isinstance(readiness, dict):
            return True
        if "projected_session_file_fallback_required" not in readiness:
            return True
        return readiness["projected_session_file_fallback_required"] is True

    def _find_projected_runtime_execution_session_from_file_rows(
        self,
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> TaskExecutionSession | None:
        """Return legacy file-row ``metadata.runtime_execution`` projection."""

        normalized_id = self.normalize_task_id(task_id)
        if normalized_id is None:
            return None
        target_task_id = str(normalized_id).strip()
        if not target_task_id:
            return None

        for row in self._list_file_task_rows(
            include_terminal=True,
            augment_runtime_state=augment_runtime_state,
        ):
            if self._observable_row_task_id(row) != target_task_id:
                continue
            return self._runtime_execution_session_from_projected_row(row)
        return None

    @staticmethod
    def _runtime_execution_session_from_projected_row(
        row: Mapping[str, Any] | None,
    ) -> TaskExecutionSession | None:
        if not isinstance(row, Mapping):
            return None
        metadata_raw = row.get("metadata")
        if not isinstance(metadata_raw, Mapping):
            return None
        runtime_execution_raw = metadata_raw.get("runtime_execution")
        if not isinstance(runtime_execution_raw, dict):
            return None
        try:
            return TaskExecutionSession.from_dict(runtime_execution_raw)
        except (TypeError, ValueError) as exc:
            logger.debug("invalid projected runtime_execution session metadata: %s", exc)
            return None

    @staticmethod
    def _same_terminal_session(
        candidate: TaskExecutionSession | None,
        incoming: TaskExecutionSession,
    ) -> bool:
        if candidate is None:
            return False
        return str(candidate.session_id or "").strip() == str(
            incoming.session_id or ""
        ).strip() and is_terminal_session_status(candidate.status)

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
        target.terminal_transition_id = source.terminal_transition_id
        target.metadata = dict(source.metadata)

    def _reconcile_terminal_task_row(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row, _reconcile_error, _execution_event = self._apply_terminal_session_reconcile(
            task_id,
            session=session,
            extra_metadata=metadata,
        )
        return row

    def _task_entity_for_terminal_session_reconcile(self, task_id: int) -> Task | None:
        """Resolve raw owner-cell task entity for terminal-session reconcile.

        Boundary:
            Terminal-session reconcile is the owner-cell path that projects a
            terminal execution session back onto the persisted task row. This is
            the only raw ``TaskBoard.get`` read boundary for that reconcile
            flow; observable readers must continue using fact-overlaid row
            projections, and claim/dependency/transition helpers keep their own
            narrower raw-entity boundaries.

        Complexity:
            O(1) over the in-memory ``TaskBoard`` cache for the already
            normalized numeric ``task_id`` used by this reconcile path. Missing
            rows return ``None`` so existing ``task_not_found`` and empty-row
            fallback semantics remain unchanged.

        Extension point:
            Future terminal-session compare-and-swap, row-version validation, or
            audit receipt binding should attach here before reconcile writes,
            keeping those checks local to this owner-cell boundary without
            changing event payloads or rejection error codes.
        """

        return self._board.get(task_id)

    def _apply_terminal_session_reconcile(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
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
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), "", None
        runtime_metadata = self._build_runtime_metadata(
            session=session,
            effective_status=terminal_status.value,
            resume_state="",
            extra_metadata=extra_metadata,
        )
        try:
            updated = self._board.update(
                task_id,
                status=terminal_status,
                metadata=runtime_metadata,
                allow_terminal_status=True,
            )
        except InvalidTaskStateTransitionError:
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            if task is None:
                return None, "task_not_found", None
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
                return self._augment_task_row(task.to_dict()), "terminal_row_conflict", None
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
                return self._augment_task_row(task.to_dict()), "terminal_reconcile_rejected", None
            if forced is None:
                return None, "task_not_found", None
            updated = self._board.update(task_id, metadata=runtime_metadata) or forced
        if updated is None:
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), "", None
        row = self._augment_task_row(updated.to_dict())
        execution_event = self._append_execution_event(
            "terminal_session_reconciled",
            task_row=row,
            session=session,
            details={
                "terminal_status": terminal_status.value,
                "source": "runtime.task_runtime.terminal_session_reconcile",
            },
        )
        return row, "", execution_event

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

    def _row_mapping_authorizes_retry_over_terminal_session(
        self,
        row: Mapping[str, Any],
        session: TaskExecutionSession,
    ) -> bool:
        """Return True when a non-terminal read-model row supersedes a terminal session.

        ``_augment_task_row`` operates on the observable row projection. It must
        not re-read the private ``TaskBoard`` row just to decide whether a retry
        authorization exists, otherwise the board becomes a hidden second read
        source for runtime state. This row-oriented variant intentionally mirrors
        ``_row_authorizes_retry_over_terminal_session`` while accepting only the
        fields already present in the supplied row.
        """
        raw_status = str(row.get("status") or "").strip().lower()
        if not raw_status or is_terminal_task_row_status(raw_status):
            return False
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
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
        with (
            self._get_session_lock(session.task_id),
            self._board._file_lock(self._session_file_lock_path(session.task_id)),
        ):
            return self._rotate_terminal_session_for_retry_locked(session)

    def _rotate_terminal_session_for_retry_locked(
        self,
        session: TaskExecutionSession,
    ) -> TaskExecutionSession:
        """Rotate a terminal session while the caller owns both session locks."""

        session.metadata["rotated_from_terminal_status"] = str(session.status or "")
        session.metadata["rotated_reason"] = "deliberate_row_reset_retry"
        session.mark_suspended(reason="terminal_session_rotated_for_deliberate_retry", resumable=False)
        self._write_session_locked(session, allow_terminal_downgrade=True)
        return session

    def _dependent_rows_blocked_by(self, task_id: int) -> list[dict[str, Any]]:
        """Return task-row snapshots that currently depend on ``task_id``.

        Boundary:
            This is pre-mutation evidence for dependency side effects owned by
            ``TaskRuntimeService.settle_execution_attempt()``. Raw ``TaskBoard``
            updates are row-local; dependency fan-out must stay in this service
            so every cross-row mutation can emit execution facts.

        Complexity:
            O(t) time and memory over task rows in the current workspace.
        """

        rows: list[dict[str, Any]] = []
        for row in self.list_observable_task_rows():
            try:
                blockers = [int(blocker) for blocker in row.get("blocked_by") or []]
            except (TypeError, ValueError):
                blockers = []
            if task_id in blockers:
                rows.append(dict(row))
        return rows

    @staticmethod
    def _row_blocker_ids(row: dict[str, Any]) -> list[int]:
        blockers_raw = row.get("blocked_by") or row.get("blockedBy") or []
        blocker_ids: list[int] = []
        if not isinstance(blockers_raw, list):
            return blocker_ids
        for blocker in blockers_raw:
            try:
                blocker_id = int(blocker)
            except (TypeError, ValueError):
                continue
            if blocker_id not in blocker_ids:
                blocker_ids.append(blocker_id)
        return blocker_ids

    @staticmethod
    def _row_blocks_ids(row: dict[str, Any]) -> list[int]:
        blocks_raw = row.get("blocks") or []
        block_ids: list[int] = []
        if not isinstance(blocks_raw, list):
            return block_ids
        for block in blocks_raw:
            try:
                block_id = int(block)
            except (TypeError, ValueError):
                continue
            if block_id not in block_ids:
                block_ids.append(block_id)
        return block_ids

    def _apply_reverse_dependency_links(
        self,
        *,
        created_task_id: int,
        blocker_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Link a newly created dependent task into each blocker row.

        The operation is O(b) over direct blockers supplied by the task row.
        Missing blocker rows preserve legacy create semantics and are ignored,
        but every persisted reverse-link mutation emits an execution fact.
        """

        events: list[dict[str, Any]] = []
        for blocker_id in blocker_ids:
            _normalized_blocker_id, blocker = self._task_entity_for_dependency_side_effect(blocker_id)
            if blocker is None:
                continue
            before_row = self._augment_task_row(blocker.to_dict())
            previous_blocks = self._row_blocks_ids(before_row)
            if created_task_id in previous_blocks:
                continue
            next_blocks = [*previous_blocks, created_task_id]
            updated = self._board.update_blocks(blocker_id, next_blocks)
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "reverse_dependency_link_failed",
                        "task_id": blocker_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue
            after_row = self._augment_task_row(updated.to_dict())
            events.append(
                self._append_execution_event(
                    "reverse_dependency_linked",
                    task_row=after_row,
                    session=None,
                    details={
                        "dependent_task_id": created_task_id,
                        "previous_blocks": previous_blocks,
                        "blocks": self._row_blocks_ids(after_row),
                    },
                )
            )
        return events
