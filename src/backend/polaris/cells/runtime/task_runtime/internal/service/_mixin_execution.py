"""Collaborator mixin for TaskRuntimeService (_mixin_execution)."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, cast

from polaris.cells.runtime.task_runtime.internal.task_board import (
    Task,
    TaskBoardFileLockTimeoutError,
    TaskStatus,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatCodeV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementCodeV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
)

from ..directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from ..execution_session import (
    TaskExecutionSession,
    build_task_execution_bulk_suspend_result,
    build_task_execution_claim_attempt,
    build_task_execution_claim_next_result,
    build_task_execution_claim_result,
    build_task_execution_heartbeat_result,
    is_terminal_task_row_status,
    project_task_row_runtime_state,
    sanitize_summary,
)
from ._helpers import (
    _DEPENDENCY_SATISFACTION_METADATA_KEY,
    _DEPENDENCY_SATISFACTION_SCHEMA_V1,
    _PENDING_TERMINAL_INTENT_METADATA_KEY,
    _canonical_sha256,
    _DependencySatisfactionDecision,
    _PreparedTerminalSettlement,
    _terminal_task_status_for_session,
    logger,
)
from ._late_bindings import (
    utc_now,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    pass


class _ExecutionMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

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
        observable_rows = self.list_observable_task_rows()
        candidates = [row for row in observable_rows if self._is_row_claimable(row)]
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
                result = build_task_execution_claim_next_result(
                    success=True,
                    reason="",
                    task_row=claim_task if isinstance(claim_task, dict) else None,
                    session=claim_session if isinstance(claim_session, dict) else None,
                    attempts=attempts,
                )
                attempt_record = claim_result.get("execution_attempt")
                if isinstance(attempt_record, dict):
                    result["execution_attempt"] = dict(attempt_record)
                return result

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
        """Claim a task for execution and persist a lease-backed session.

        Direct ``claim_execution(task_id=...)`` paths must respect both
        terminal session evidence and the authoritative
        ``task_runtime.execution`` fact stream, not only the raw
        ``TaskBoard`` file row.  Before reading or mutating the raw row we
        refresh dependency unblocks so a child whose blocker is only complete
        in the latest fact becomes claimable.  Terminal session reconciliation
        remains the first terminal authority because it can repair stale rows;
        the latest terminal fact is the fail-closed fallback when no terminal
        session handled the row.
        """
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return build_task_execution_claim_result(success=False, reason="invalid_task_id")

        # Refresh dependency unblocks before reading/mutating the raw row so
        # a child whose blocker is only complete in the latest fact can be
        # claimed.  ``refresh_dependency_unblocks`` is idempotent and uses the
        # fact-overlay-aware status projection under the hood.
        self.refresh_dependency_unblocks()
        normalized, task = self._task_entity_for_claim_execution(task_id)
        if normalized is None:
            return build_task_execution_claim_result(success=False, reason="invalid_task_id")
        if task is None:
            return build_task_execution_claim_result(success=False, reason="task_not_found")

        latest_fact_row = self._find_latest_execution_fact_row_for_task(normalized)
        # The dependency projection may read task rows, execution facts, and
        # session-backed runtime state.  Take this fail-closed read snapshot
        # before the session RMW lock: re-entering the cooperative session
        # file lock from inside that critical section is not supported.
        dependencies_unresolved = self._task_has_unresolved_dependencies(task)
        existing_session: TaskExecutionSession | None = None
        session: TaskExecutionSession | None = None
        resume_from_previous = False
        claim_renewed = False
        rejection_reason = ""
        terminal_session_to_reconcile: TaskExecutionSession | None = None
        fact_terminal_rejection = False
        fact_status = ""

        # This is the sole read-modify-write critical section for a persisted
        # session claim. The locked helpers deliberately avoid reacquiring the
        # cooperative file lock, which would otherwise split the decision and
        # write across processes (or deadlock with non-reentrant file locks).
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            existing_session = self._read_session_locked(normalized)
            if existing_session is not None:
                terminal_session_status = _terminal_task_status_for_session(existing_session.status)
                if terminal_session_status is not None:
                    if self._row_authorizes_retry_over_terminal_session(task, existing_session):
                        existing_session = self._rotate_terminal_session_for_retry_locked(existing_session)
                    else:
                        terminal_session_to_reconcile = existing_session

            if terminal_session_to_reconcile is None:
                if latest_fact_row is not None:
                    fact_status = str(latest_fact_row.get("status") or "").strip().lower()
                    if is_terminal_task_row_status(fact_status):
                        rejection_reason = "task_terminal"
                        fact_terminal_rejection = True
                if not rejection_reason and task.is_terminal:
                    rejection_reason = "task_terminal"
                if not rejection_reason and dependencies_unresolved:
                    rejection_reason = "task_blocked"

                if not rejection_reason:
                    active_session: TaskExecutionSession | None = None
                    if (
                        existing_session is not None
                        and existing_session.status == "active"
                        and not existing_session.is_expired(now=utc_now())
                    ):
                        active_session = existing_session
                    if active_session is not None:
                        same_owner = (
                            active_session.worker_id == str(worker_id or "").strip()
                            and active_session.role_id == str(role_id or "").strip()
                        )
                        if not same_owner:
                            rejection_reason = "lease_conflict"
                        else:
                            active_session.renew(
                                lease_ttl_seconds=lease_ttl_seconds,
                                context_summary=context_summary,
                            )
                            self._write_session_locked(active_session)
                            session = active_session
                            claim_renewed = True
                    else:
                        resume_from_previous = bool(
                            existing_session is not None
                            and existing_session.resumable
                            and (
                                existing_session.status == "suspended"
                                or (existing_session.status == "active" and existing_session.is_expired(now=utc_now()))
                            )
                        )
                        attempt = self._resolve_next_attempt(task, existing_session)
                        resume_count = (
                            int(existing_session.resume_count + 1) if resume_from_previous and existing_session else 0
                        )
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
                            external_task_id=external_task_id
                            or str(task.metadata.get("external_task_id") or "").strip(),
                            context_summary=context_summary,
                            metadata={
                                "previous_session_id": (
                                    existing_session.session_id if existing_session is not None else ""
                                ),
                            },
                        )
                        self._write_session_locked(session)

        if terminal_session_to_reconcile is not None:
            row, reconcile_error, execution_event = self._apply_terminal_session_reconcile(
                normalized,
                session=terminal_session_to_reconcile,
                extra_metadata=metadata,
            )
            if row is None:
                row = self._augment_task_row(task.to_dict())
            result = build_task_execution_claim_result(
                success=False,
                reason="task_terminal",
                task_row=row,
                session=terminal_session_to_reconcile,
                reconciled_from_terminal_session=not reconcile_error,
                reconcile_error=reconcile_error,
                execution_event=execution_event,
            )
            return self._claim_result_with_execution_attempt(result, terminal_session_to_reconcile)

        if rejection_reason:
            task_row = (
                dict(latest_fact_row)
                if fact_terminal_rejection and latest_fact_row is not None
                else self._augment_task_row(task.to_dict())
            )
            result = build_task_execution_claim_result(
                success=False,
                reason=rejection_reason,
                task_row=task_row,
                session=existing_session if rejection_reason == "lease_conflict" else None,
            )
            if fact_terminal_rejection:
                result["execution_fact_authoritative"] = True
                result["source"] = "task_runtime.execution_fact"
                result["fact_status"] = fact_status
            return self._claim_result_with_execution_attempt(
                result,
                existing_session if rejection_reason == "lease_conflict" else None,
            )

        if session is None:
            raise RuntimeError("claim execution completed without a session decision")

        updated_task = self._board.update(
            normalized,
            status=TaskStatus.IN_PROGRESS,
            assignee=str(worker_id or "").strip(),
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state=("resumed" if session.resume_count > 0 else ""),
                extra_metadata=metadata,
            ),
            allow_execution_status=True,
        )
        row = self._augment_task_row(updated_task.to_dict() if updated_task is not None else task.to_dict())
        execution_event = self._append_execution_event(
            "claim_renewed" if claim_renewed else "claimed",
            task_row=row,
            session=session,
            details={
                "selection_source": selection_source,
                "resumed": resume_from_previous,
            },
        )
        result = build_task_execution_claim_result(
            success=True,
            reason="claim_renewed" if claim_renewed else "claimed",
            task_row=row,
            session=session,
            resumed=session.resume_count > 0,
            claim_applied=True,
            execution_event=execution_event,
        )
        return self._claim_result_with_execution_attempt(result, session)

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
            if session.is_expired(now=utc_now()):
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_lease_expired",
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

    def heartbeat_execution_attempt(
        self,
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Atomically renew one identity-fenced execution attempt within a deadline.

        The local per-session lock and its cooperative file lock are held from
        the authoritative session read through durable renewal and fact
        projection. This prevents a validate-then-renew race between a stale
        caller and a concurrent terminal transition.
        """

        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="workspace_mismatch",
                identity=identity,
                evidence_anchor={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        lock_acquired = session_lock.acquire(timeout=command.lock_timeout_seconds)
        if not lock_acquired:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="file_lock_timeout",
                identity=identity,
                evidence_anchor={
                    "lock_scope": "local_session",
                    "lock_timeout_seconds": command.lock_timeout_seconds,
                },
            )
        try:
            remaining_seconds = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining_seconds < 0:
                return self._execution_attempt_heartbeat_verdict(
                    success=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence_anchor={
                        "lock_scope": "local_session",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining_seconds,
                ):
                    return self._heartbeat_execution_attempt_locked(command)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_heartbeat_verdict(
                    success=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence_anchor={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
        finally:
            session_lock.release()

    def _heartbeat_execution_attempt_locked(
        self,
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Renew one attempt while both session locks are already held."""

        identity = command.identity
        session_path = self._session_logical_path(identity.task_id)
        session = self._read_session_locked(identity.task_id)
        if session is None:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_not_found",
                identity=identity,
                evidence_anchor={"session_path": session_path},
            )

        observed_identity = self._execution_attempt_identity_from_session(session)
        evidence_anchor: dict[str, Any] = {"observed_identity": observed_identity.to_record()}
        mismatch_code = self._execution_attempt_mismatch_code(identity, session)
        if mismatch_code is not None:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code=mismatch_code,
                identity=identity,
                evidence_anchor=evidence_anchor,
            )
        if session.status != "active":
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_not_active",
                identity=identity,
                evidence_anchor=evidence_anchor,
            )
        if self._has_pending_terminal_intent(session):
            pending_intent = self._pending_terminal_intent(session)
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="terminal_fence_pending",
                identity=identity,
                evidence_anchor={
                    **evidence_anchor,
                    "pending_terminal_intent": dict(pending_intent or {}),
                    "pending_terminal_intent_valid": pending_intent is not None,
                },
            )
        if session.is_expired(now=utc_now()):
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_lease_expired",
                identity=identity,
                evidence_anchor=evidence_anchor,
            )

        session.renew(
            lease_ttl_seconds=command.lease_ttl_seconds,
            context_summary=command.context_summary,
        )
        if not self._write_session_locked(session):
            row = self._reconcile_terminal_task_row(identity.task_id, session=session)
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_terminal_preserved",
                identity=identity,
                evidence_anchor={
                    **evidence_anchor,
                    "task_row": dict(row or {}),
                    **self._session_write_receipt_details_for_session(session),
                },
            )

        try:
            task = self._board.update(
                identity.task_id,
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status="in_progress",
                    resume_state="resumed" if session.resume_count > 0 else "",
                ),
            )
            row = (
                project_task_row_runtime_state(
                    task.to_dict(),
                    task_status_value=task.status.value,
                    session=session,
                    terminal_session_superseded=False,
                )
                if task is not None
                else None
            )
            if not isinstance(row, dict):
                raise RuntimeError("task_row_missing_after_heartbeat_renewal")
            execution_event = self._append_execution_event(
                "heartbeat_renewed",
                task_row=row,
                session=session,
                details={
                    "lease_ttl_seconds": command.lease_ttl_seconds,
                    "context_summary": sanitize_summary(command.context_summary),
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "TaskRuntime heartbeat projection failed after renewal: task_id=%s session_id=%s error=%s",
                identity.task_id,
                identity.session_id,
                exc,
            )
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="row_projection_failed",
                identity=identity,
                evidence_anchor={
                    **evidence_anchor,
                    **self._session_write_receipt_details_for_session(session),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )

        renewed_identity = self._execution_attempt_identity_from_session(session)
        evidence_anchor.update(self._session_write_receipt_details_for_session(session))
        evidence_anchor.update(self._row_write_receipt_details_for_task(row))
        evidence_anchor["execution_event"] = dict(execution_event)
        return self._execution_attempt_heartbeat_verdict(
            success=True,
            code="heartbeat_renewed",
            identity=identity,
            renewed_identity=renewed_identity,
            evidence_anchor=evidence_anchor,
        )

    def _execution_attempt_heartbeat_verdict(
        self,
        *,
        success: bool,
        code: TaskRuntimeExecutionAttemptHeartbeatCodeV1,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        renewed_identity: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        evidence_anchor: Mapping[str, Any] | None = None,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Build one detached typed heartbeat outcome at the owner boundary."""

        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=success,
            code=code,
            workspace=self.workspace,
            identity=identity,
            renewed_identity=renewed_identity,
            evidence_anchor=dict(evidence_anchor or {}),
        )

    @staticmethod
    def _execution_attempt_mismatch_code(
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        session: TaskExecutionSession,
        *,
        allow_terminal_settlement_lease: bool = False,
    ) -> (
        Literal[
            "session_task_mismatch",
            "session_mismatch",
            "attempt_mismatch",
            "role_mismatch",
            "worker_mismatch",
            "run_mismatch",
            "external_task_id_mismatch",
            "lease_version_mismatch",
        ]
        | None
    ):
        """Return the first stable identity mismatch for a locked session."""

        if session.task_id != identity.task_id:
            return "session_task_mismatch"
        if session.session_id != identity.session_id:
            return "session_mismatch"
        if session.attempt != identity.attempt:
            return "attempt_mismatch"
        if session.role_id != identity.role_id:
            return "role_mismatch"
        if session.worker_id != identity.worker_id:
            return "worker_mismatch"
        if session.run_id != identity.run_id:
            return "run_mismatch"
        if session.external_task_id != identity.external_task_id:
            return "external_task_id_mismatch"
        # R145/R171: lease_expires_at is a renewable same-owner TTL, not a fencing
        # token. Concurrent heartbeats (director loop, DEO pre-claim, batch prepare)
        # advance the stored lease while multi-step DEO prepare still holds the
        # pre-heartbeat identity. Exact equality here left R145 incomplete:
        # validate_execution_attempt was fixed, but heartbeat/mutate still used this
        # helper and collapsed live batches to deo_inventory_ready_failed /
        # deo_execution_attempt_heartbeat_failed (r171b TOOL_RESULT_FAILED drops).
        # Authority steal remains covered by session/attempt/worker/role/run checks.
        #
        # Terminal settlement may still pin a settlement-identity lease snapshot so
        # a post-close renew cannot impersonate the settled attempt.
        if allow_terminal_settlement_lease and session.status != "active":
            expected_lease_expires_at = str(
                session.metadata.get("settlement_identity_lease_expires_at") or session.lease_expires_at or ""
            ).strip()
            if expected_lease_expires_at and expected_lease_expires_at != identity.lease_expires_at:
                return "lease_version_mismatch"
        return None

    def settle_execution_attempt(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> dict[str, Any]:
        """Settle one attempt through a session commit then lock-free projection.

        Lock order is fixed.  The bounded session lock pair (local lock then
        cooperative session-file lock) covers session reread, identity and
        lease validation, the strict DEO registry pre-barrier,
        terminal-transition-id persistence, and winner selection. It is
        released before TaskBoard, execution-fact append, dependency, or
        runtime.v2 projection. A distinct bounded projection lock serializes
        those idempotent effects without ever acquiring a session lock.
        """

        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return self._execution_attempt_settlement_result(
                success=False,
                code="workspace_mismatch",
                command=command,
                evidence={"service_workspace": self.workspace},
            )
        normalized, task = self._task_entity_for_transition(identity.task_id)
        if normalized is None or task is None:
            return self._execution_attempt_settlement_result(
                success=False,
                code="session_not_found",
                command=command,
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(normalized)
        if not session_lock.acquire(timeout=command.lock_timeout_seconds):
            return self._execution_attempt_settlement_result(
                success=False,
                code="file_lock_timeout",
                command=command,
                evidence={"lock_scope": "local_session"},
            )
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_settlement_result(
                    success=False,
                    code="file_lock_timeout",
                    command=command,
                    evidence={"lock_scope": "local_session"},
                )
            try:
                with self._board._file_lock(self._session_file_lock_path(normalized), timeout_seconds=remaining):
                    self._after_directed_effect_linearization_lock(
                        "settlement",
                        identity,
                    )
                    locked_result, session = self._settle_execution_attempt_locked(command)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_settlement_result(
                    success=False,
                    code="file_lock_timeout",
                    command=command,
                    evidence={"lock_scope": "cooperative_session_file"},
                )
        finally:
            session_lock.release()

        if session is None:
            return locked_result
        return self._project_settled_execution_attempt(
            command,
            task=task,
            session=session,
        )

    def _settle_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> tuple[dict[str, Any], TaskExecutionSession | None]:
        """Select and persist the winner without projection or external I/O.

        The caller holds both session locks.  This method may only read or
        write the one session file and may strict-read the DEO registry through
        FactStream. It deliberately returns the persisted winner snapshot for
        the second, idempotent projection phase.
        """

        loaded = self._load_settlement_session_locked(command)
        if not isinstance(loaded, TaskExecutionSession):
            return loaded
        if loaded.status == "active":
            return self._settle_active_execution_attempt_locked(command, loaded)
        return self._settle_replayed_execution_attempt_locked(command, loaded)

    def _load_settlement_session_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskExecutionSession | tuple[dict[str, Any], None]:
        identity = command.identity
        session = self._read_session_locked(identity.task_id)
        if session is None:
            return self._execution_attempt_settlement_result(False, "session_not_found", command), None
        mismatch = self._execution_attempt_mismatch_code(identity, session, allow_terminal_settlement_lease=True)
        if mismatch is not None:
            return self._execution_attempt_settlement_result(False, mismatch, command, session=session), None
        return session

    def _settle_active_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> tuple[dict[str, Any], TaskExecutionSession | None]:
        if session.is_expired(now=utc_now()):
            return self._execution_attempt_settlement_result(
                False, "session_lease_expired", command, session=session
            ), None
        return self._settle_execution_attempt_without_lease_check_locked(command, session)

    def _settle_execution_attempt_without_lease_check_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> tuple[dict[str, Any], TaskExecutionSession | None]:
        """Close DEO parent and persist one terminal outcome under owner locks.

        Normal settlement performs lease validation before entering this
        helper. Factory terminal drain and historical terminal-session repair
        may call it after their stronger run-owner checks when the execution
        lease has already expired. They still MUST pass the exact DEO
        inventory/parent close protocol; this helper never bypasses unresolved
        physical effects.
        """

        prepared = self._prepare_terminal_settlement_intent_locked(command, session)
        if not isinstance(prepared, _PreparedTerminalSettlement):
            return prepared
        parent = prepared.repository.settle_parent_for_terminal_intent(
            command.identity,
            outcome=command.outcome,
            terminal_intent_hash=prepared.terminal_intent_hash,
        )
        if not parent.allowed:
            return self._execution_attempt_settlement_result(
                False,
                cast(TaskRuntimeExecutionAttemptSettlementCodeV1, parent.code),
                command,
                session=session,
                evidence={"directed_effect_settlement": dict(parent.evidence)},
            ), None
        self._apply_terminal_settlement_to_session(
            command, session, parent=parent, terminal_intent_hash=prepared.terminal_intent_hash
        )
        if not self._write_session_locked(session):
            return self._execution_attempt_settlement_result(
                False, "session_terminal_preserved", command, session=session
            ), None
        self._after_terminal_session_write(session)
        return self._execution_attempt_settlement_result(True, "settled", command, session=session), session

    def _prepare_terminal_settlement_intent_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> _PreparedTerminalSettlement | tuple[dict[str, Any], None]:
        transition_id = str(session.terminal_transition_id or "").strip()
        if not transition_id:
            transition_id = f"task-transition-{uuid.uuid4().hex}"
            session.terminal_transition_id = transition_id
        expected = self._build_pending_terminal_intent(command, terminal_transition_id=transition_id)
        had_pending = self._has_pending_terminal_intent(session)
        pending = self._pending_terminal_intent(session) if had_pending else None
        if had_pending and (pending is None or dict(pending) != expected):
            evidence = {"pending_terminal_intent": dict(pending or {}), "expected_terminal_intent": expected}
            return self._execution_attempt_settlement_result(
                False, "settlement_terminal_intent_conflict", command, session=session, evidence=evidence
            ), None
        terminal_intent_hash = str(expected["terminal_intent_hash"])
        repository = DirectedEffectOperationRepository()
        preflight = repository.preflight_parent_for_terminal_intent(
            command.identity, outcome=command.outcome, terminal_intent_hash=terminal_intent_hash
        )
        if not preflight.allowed:
            return self._execution_attempt_settlement_result(
                False,
                cast(TaskRuntimeExecutionAttemptSettlementCodeV1, preflight.code),
                command,
                session=session,
                evidence={"directed_effect_pre_barrier": dict(preflight.evidence)},
            ), None
        if not had_pending:
            session.metadata[_PENDING_TERMINAL_INTENT_METADATA_KEY] = expected
            session.metadata["settlement_identity_lease_expires_at"] = command.identity.lease_expires_at
            if not self._write_session_locked(session):
                return self._execution_attempt_settlement_result(
                    False, "session_terminal_preserved", command, session=session
                ), None
            self._after_terminal_intent_write(session, expected)
        return _PreparedTerminalSettlement(repository, expected, terminal_intent_hash)

    @staticmethod
    def _apply_terminal_settlement_to_session(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
        *,
        parent: DirectedEffectSettlementPreBarrierVerdictV1,
        terminal_intent_hash: str,
    ) -> None:
        proof_raw = parent.evidence.get("close_proof")
        proof = dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
        proof.setdefault("terminal_intent_hash", terminal_intent_hash)
        proof.setdefault("settlement_outcome", command.outcome)
        proof.setdefault("registry_state", str(parent.evidence.get("registry_state") or "strict_empty"))
        session.metadata["terminal_settlement_proof"] = proof
        if command.outcome == "completed":
            session.mark_completed(result_summary=command.summary)
        elif command.outcome == "failed":
            session.mark_failed(error=command.summary)
        else:
            session.mark_suspended(reason=command.summary, resumable=True)

    def _settle_replayed_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> tuple[dict[str, Any], TaskExecutionSession | None]:
        if session.status != command.outcome:
            return self._execution_attempt_settlement_result(
                False,
                "terminal_outcome_conflict",
                command,
                session=session,
                evidence={"persisted_outcome": session.status},
            ), None
        if self._has_pending_terminal_intent(session):
            pending = self._pending_terminal_intent(session)
            expected = self._build_pending_terminal_intent(
                command, terminal_transition_id=str(session.terminal_transition_id or "").strip()
            )
            if pending is None or dict(pending) != expected:
                evidence = {"pending_terminal_intent": dict(pending or {}), "expected_terminal_intent": expected}
                return self._execution_attempt_settlement_result(
                    False, "settlement_terminal_intent_conflict", command, session=session, evidence=evidence
                ), None
        elif not str(session.terminal_transition_id or "").strip():
            session.ensure_terminal_transition_id()
            if not self._write_session_locked(session):
                return self._execution_attempt_settlement_result(
                    False, "session_terminal_preserved", command, session=session
                ), None
        return self._execution_attempt_settlement_result(True, "settled", command, session=session), session

    def _project_settled_execution_attempt(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        task: Task,
        session: TaskExecutionSession,
    ) -> dict[str, Any]:
        """Apply winner-only, replay-safe TaskBoard and fact projections.

        Lock order is session locks, then no lock, then projection locks.  This
        phase must never acquire a session lock or call ``_augment_task_row``;
        it uses the phase-A session snapshot exclusively. The current Task row
        is read only under the projection lock for idempotence and owner-cell
        metadata that may have landed after phase A. A process crash after
        session persistence leaves the canonical winner recoverable: the
        terminal transition id deduplicates the fact and the TaskBoard receipt
        identifies a completed projection.
        """

        task_id = command.identity.task_id
        started_at = time.monotonic()
        projection_lock = self._get_settlement_projection_lock(task_id)
        if not projection_lock.acquire(timeout=command.lock_timeout_seconds):
            return self._execution_attempt_settlement_result(
                False,
                "file_lock_timeout",
                command,
                session=session,
                evidence={"lock_scope": "local_settlement_projection"},
            )
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_settlement_result(
                    False,
                    "file_lock_timeout",
                    command,
                    session=session,
                    evidence={"lock_scope": "local_settlement_projection"},
                )
            with self._board._file_lock(
                self._settlement_projection_file_lock_path(task_id),
                timeout_seconds=remaining,
            ):
                return self._project_settled_execution_attempt_locked(
                    command,
                    task=task,
                    session=session,
                )
        except TaskBoardFileLockTimeoutError:
            return self._execution_attempt_settlement_result(
                False,
                "file_lock_timeout",
                command,
                session=session,
                evidence={"lock_scope": "cooperative_settlement_projection"},
            )
        finally:
            projection_lock.release()

    def _failed_materialization_dependency_satisfaction(
        self,
        *,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        task: Task,
        session: TaskExecutionSession,
    ) -> _DependencySatisfactionDecision | None:
        """Prove that a failed Director task still committed usable capability.

        A failed task remains terminal-failed.  This proof authorizes only the
        narrower dependency effect: later PM tasks may consume the declared
        files that the Director already committed.  Callers cannot assert the
        result with a boolean.  TaskRuntime derives it from the task row, the
        DEO terminal settlement proof, and regular files rooted inside the
        bound workspace.

        Fail closed unless every effect receipt succeeded and at least one
        declared target is a real, non-symlink regular file.  This keeps a
        quality failure repairable without treating an unrelated or ambiguous
        write as task completion.
        """

        if command.outcome != "failed" or session.role_id != "director":
            return None

        task_metadata = dict(task.metadata) if isinstance(task.metadata, Mapping) else {}
        # The Director's live path carries ``adapter_result`` on the settlement
        # command; TaskRuntime commits it atomically with the terminal row.  It
        # is therefore not necessarily present on the pre-projection Task
        # snapshot yet.  Evaluate the same merged metadata that the row update
        # below will persist, while retaining the task-owned contract fields.
        if isinstance(command.metadata, Mapping):
            task_metadata.update(dict(command.metadata))
        adapter_result_raw = task_metadata.get("adapter_result")
        if not isinstance(adapter_result_raw, Mapping):
            return None
        adapter_result = dict(adapter_result_raw)
        if adapter_result.get("write_tool_evidence") is not True:
            return None

        proof_raw = session.metadata.get("terminal_settlement_proof")
        if not isinstance(proof_raw, Mapping):
            return None
        proof = dict(proof_raw)
        if (
            str(proof.get("registry_state") or "").strip().upper() != "CLOSED_WITH_OUTCOME_PROOF"
            or str(proof.get("settlement_outcome") or "").strip().lower() != "failed"
        ):
            return None

        def _proof_count(name: str) -> int | None:
            value = proof.get(name)
            if value is None or isinstance(value, bool):
                return None
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                return None
            return normalized if normalized >= 0 else None

        receipt_count = _proof_count("receipt_count")
        if receipt_count is None or receipt_count <= 0:
            return None
        if any(_proof_count(name) != 0 for name in ("failed_receipt_count", "dead_letter_count", "aborted_count")):
            return None

        stable_identity_raw = proof.get("stable_registry_identity")
        if not isinstance(stable_identity_raw, Mapping):
            return None
        stable_identity = dict(stable_identity_raw)
        identity_record = command.identity.to_record()
        for field_name in (
            "workspace",
            "task_id",
            "external_task_id",
            "role_id",
            "worker_id",
            "run_id",
            "session_id",
            "attempt",
        ):
            if stable_identity.get(field_name) != identity_record.get(field_name):
                return None

        digest_pattern = re.compile(r"^[0-9a-f]{64}$")
        close_evidence_hash = str(proof.get("close_evidence_hash") or "").strip().lower()
        receipt_summary_hash = str(proof.get("receipt_summary_hash") or "").strip().lower()
        if not digest_pattern.fullmatch(close_evidence_hash) or not digest_pattern.fullmatch(receipt_summary_hash):
            return None

        def _relative_path(value: object) -> str | None:
            raw = str(value or "").strip().replace("\\", "/")
            if not raw:
                return None
            path = Path(raw)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                return None
            return path.as_posix()

        def _declared_or_reported_paths(source: Mapping[str, Any], keys: tuple[str, ...]) -> set[str]:
            paths: set[str] = set()
            for key in keys:
                values = source.get(key)
                if not isinstance(values, (list, tuple)):
                    continue
                for value in values:
                    normalized = _relative_path(value)
                    if normalized is not None:
                        paths.add(normalized)
            return paths

        declared_paths = _declared_or_reported_paths(task_metadata, ("target_files", "scope_paths"))
        if not declared_paths:
            return None

        reported_paths = _declared_or_reported_paths(adapter_result, ("new_files", "modified_files"))
        workspace_root = Path(self.workspace).expanduser().resolve()
        materialized_paths: list[str] = []
        for relative_path in sorted(declared_paths.intersection(reported_paths)):
            unresolved_candidate = workspace_root / relative_path
            if unresolved_candidate.is_symlink():
                continue
            try:
                resolved_candidate = unresolved_candidate.resolve(strict=True)
                resolved_candidate.relative_to(workspace_root)
            except (OSError, ValueError):
                continue
            if resolved_candidate.is_file():
                materialized_paths.append(relative_path)
        if not materialized_paths:
            return None

        evidence = {
            "schema_version": _DEPENDENCY_SATISFACTION_SCHEMA_V1,
            "kind": "failed_director_materialization",
            "task_id": command.identity.task_id,
            "external_task_id": command.identity.external_task_id,
            "run_id": command.identity.run_id,
            "session_id": command.identity.session_id,
            "terminal_transition_id": str(session.terminal_transition_id or "").strip(),
            "materialized_paths": materialized_paths,
            "adapter_result_hash": _canonical_sha256(
                {
                    "new_files": list(adapter_result.get("new_files") or []),
                    "modified_files": list(adapter_result.get("modified_files") or []),
                    "write_tool_evidence": True,
                }
            ),
            "close_evidence_ref": str(proof.get("close_evidence_ref") or "").strip(),
            "close_evidence_hash": close_evidence_hash,
            "receipt_summary_hash": receipt_summary_hash,
            "receipt_count": receipt_count,
            "failed_receipt_count": 0,
            "dead_letter_count": 0,
            "aborted_count": 0,
        }
        evidence["evidence_hash"] = _canonical_sha256(evidence)
        return _DependencySatisfactionDecision(evidence=evidence)

    @staticmethod
    def _stored_dependency_satisfaction(
        row: Mapping[str, Any],
        *,
        session: TaskExecutionSession,
    ) -> dict[str, Any]:
        """Return a replay-safe stored satisfaction receipt or an empty mapping."""

        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        evidence_raw = metadata.get(_DEPENDENCY_SATISFACTION_METADATA_KEY)
        if not isinstance(evidence_raw, Mapping):
            return {}
        evidence = dict(evidence_raw)
        if evidence.get("schema_version") != _DEPENDENCY_SATISFACTION_SCHEMA_V1:
            return {}
        if (
            str(evidence.get("terminal_transition_id") or "").strip()
            != str(session.terminal_transition_id or "").strip()
        ):
            return {}
        evidence_hash = str(evidence.pop("evidence_hash", "") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
            return {}
        if _canonical_sha256(evidence) != evidence_hash:
            return {}
        evidence["evidence_hash"] = evidence_hash
        return evidence

    @staticmethod
    def _dependency_satisfaction_receipt_for_status_projection(
        row: Mapping[str, Any],
        *,
        expected_task_id: int,
    ) -> dict[str, Any]:
        """Validate failed-materialization evidence for dependency decisions.

        This does not convert the parent task to completed.  It validates only
        the narrower TaskRuntime-owned capability receipt consumed by
        dependency refresh and claim policy.  Every field used to qualify the
        failed output is hash-bound; malformed, forged, partial, or unrelated
        evidence fails closed.
        """

        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        evidence_raw = metadata.get(_DEPENDENCY_SATISFACTION_METADATA_KEY)
        if not isinstance(evidence_raw, Mapping):
            return {}
        evidence = dict(evidence_raw)
        if (
            evidence.get("schema_version") != _DEPENDENCY_SATISFACTION_SCHEMA_V1
            or evidence.get("kind") != "failed_director_materialization"
        ):
            return {}

        def _evidence_int(field_name: str) -> int | None:
            value = evidence.get(field_name)
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    return None
            return None

        evidence_task_id = _evidence_int("task_id")
        receipt_count = _evidence_int("receipt_count")
        failed_receipt_count = _evidence_int("failed_receipt_count")
        dead_letter_count = _evidence_int("dead_letter_count")
        aborted_count = _evidence_int("aborted_count")
        if None in (
            evidence_task_id,
            receipt_count,
            failed_receipt_count,
            dead_letter_count,
            aborted_count,
        ):
            return {}
        if (
            evidence_task_id != expected_task_id
            or receipt_count is None
            or receipt_count <= 0
            or failed_receipt_count != 0
            or dead_letter_count != 0
            or aborted_count != 0
        ):
            return {}
        materialized_paths = evidence.get("materialized_paths")
        if not isinstance(materialized_paths, list) or not any(str(path or "").strip() for path in materialized_paths):
            return {}
        if not str(evidence.get("terminal_transition_id") or "").strip():
            return {}
        for digest_field in (
            "adapter_result_hash",
            "close_evidence_hash",
            "receipt_summary_hash",
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get(digest_field) or "").strip().lower()):
                return {}
        evidence_hash = str(evidence.pop("evidence_hash", "") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
            return {}
        if _canonical_sha256(evidence) != evidence_hash:
            return {}
        evidence["evidence_hash"] = evidence_hash
        return evidence

    def _project_settled_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        task: Task,
        session: TaskExecutionSession,
    ) -> dict[str, Any]:
        """Project one settled snapshot while the independent projection lock is held."""

        projected_row = self._board.get(command.identity.task_id)
        if projected_row is not None:
            projected_record = projected_row.to_dict()
            receipt = self._settlement_projection_receipt(projected_record)
            if self._settlement_projection_receipt_matches(receipt, command=command, session=session):
                stored_satisfaction = self._stored_dependency_satisfaction(
                    projected_record,
                    session=session,
                )
                result = self._execution_attempt_settlement_result(
                    True,
                    "settlement_idempotent",
                    command,
                    session=session,
                    idempotent=True,
                    evidence={
                        "task": projected_record,
                        "projection_receipt": receipt,
                        "dependency_satisfaction": stored_satisfaction,
                    },
                )
                if command.outcome == "completed" or stored_satisfaction:
                    result["dependency_events"] = self._apply_dependency_completion_side_effects(
                        completed_task_id=command.identity.task_id,
                        dependent_rows_before=self._dependent_rows_blocked_by(command.identity.task_id),
                        dependency_satisfaction=stored_satisfaction,
                    )
                return result

        dependency_decision = self._failed_materialization_dependency_satisfaction(
            command=command,
            task=projected_row or task,
            session=session,
        )
        details: dict[str, Any]
        if command.outcome == "completed":
            status = TaskStatus.COMPLETED
            effective_status, resume_state, assignee = "completed", "", None
            details = {"result_summary": sanitize_summary(command.summary)}
        elif command.outcome == "failed":
            status = TaskStatus.FAILED
            effective_status, resume_state, assignee = "failed", "", None
            details = {"error": sanitize_summary(command.summary)}
        else:
            status = TaskStatus.BLOCKED
            effective_status, resume_state, assignee = "pending", "resumable", ""
            details = {"reason": sanitize_summary(command.summary)}
        extra_metadata = dict(command.metadata)
        if dependency_decision is not None:
            dependency_satisfaction = dict(dependency_decision.evidence)
            extra_metadata[_DEPENDENCY_SATISFACTION_METADATA_KEY] = dependency_satisfaction
            details["dependency_satisfaction"] = dependency_satisfaction
        else:
            dependency_satisfaction = {}
        try:
            updated = self._board.update(
                command.identity.task_id,
                status=status,
                assignee=assignee,
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status=effective_status,
                    resume_state=resume_state,
                    extra_metadata=extra_metadata,
                ),
                allow_terminal_status=command.outcome in {"completed", "failed"},
                allow_execution_status=True,
            )
            if updated is None:
                raise RuntimeError("settled task row was not found during projection")
            row = self._augment_task_row_with_session(updated.to_dict(), session=session)
            event = self._append_execution_event(command.outcome, task_row=row, session=session, details=details)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={"error_type": type(exc).__name__, "error_message": str(exc)},
            )
        if not bool(event.get("ok")):
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={"task": row, "execution_event": event},
            )
        projection_receipt = {
            "terminal_transition_id": session.terminal_transition_id,
            "outcome": command.outcome,
            "fact_event_id": str(event.get("fact_event_id") or "").strip(),
            "fact_event_seq": event.get("fact_event_seq"),
        }
        try:
            receipt_row = self._board.update(
                command.identity.task_id,
                metadata={"task_runtime_settlement": projection_receipt},
                allow_terminal_status=command.outcome in {"completed", "failed"},
                allow_execution_status=True,
            )
            if receipt_row is None:
                raise RuntimeError("settled task row was not found while recording projection receipt")
            row = self._augment_task_row_with_session(receipt_row.to_dict(), session=session)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "execution_event": event,
                    "projection_receipt": projection_receipt,
                },
            )
        result = self._execution_attempt_settlement_result(
            True,
            "settled",
            command,
            session=session,
            idempotent=False,
            evidence={
                "task": row,
                "execution_event": event,
                "projection_receipt": projection_receipt,
                "dependency_satisfaction": dependency_satisfaction,
            },
        )
        if command.outcome == "completed" or dependency_satisfaction:
            dependency_events = self._apply_dependency_completion_side_effects(
                completed_task_id=command.identity.task_id,
                dependent_rows_before=self._dependent_rows_blocked_by(command.identity.task_id),
                dependency_satisfaction=dependency_satisfaction,
            )
            result["dependency_events"] = dependency_events
        return result

    @staticmethod
    def _settlement_projection_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return the detached terminal-projection receipt stored on a task row."""

        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        receipt = metadata_map.get("task_runtime_settlement")
        return dict(receipt) if isinstance(receipt, Mapping) else {}

    @staticmethod
    def _settlement_projection_receipt_matches(
        receipt: Mapping[str, Any],
        *,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> bool:
        """Return whether a durable receipt proves this winner was fully projected."""

        return (
            str(receipt.get("terminal_transition_id") or "").strip()
            == str(session.terminal_transition_id or "").strip()
            and str(receipt.get("outcome") or "").strip() == command.outcome
            and bool(str(receipt.get("fact_event_id") or "").strip())
        )

    def _execution_attempt_settlement_result(
        self,
        success: bool,
        code: TaskRuntimeExecutionAttemptSettlementCodeV1,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        session: TaskExecutionSession | None = None,
        idempotent: bool = False,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        verdict = TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=success,
            code=code,
            workspace=self.workspace,
            identity=command.identity,
            outcome=command.outcome,
            idempotent=idempotent,
            evidence=dict(evidence or {}),
        )
        result = cast(dict[str, Any], verdict.to_record())
        if session is not None:
            result["session"] = session.to_dict()
        result.update(dict(evidence or {}))
        return result

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

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the run-cancellation mutation owner. Cancellation must
            suspend matching execution sessions, update the persisted task rows,
            and append ``task_runtime.execution`` facts for each row mutation.
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
        for task in self._list_file_task_entities():
            task_id = self.normalize_task_id(task.id)
            if task_id is None:
                continue
            reconcile_terminal_session = False
            session_lock = self._get_session_lock(task_id)
            with (
                session_lock,
                self._board._file_lock(self._session_file_lock_path(task_id)),
            ):
                suspend_result = self._suspend_active_session_for_run_locked(
                    task_id,
                    run_id=normalized_run_id,
                    reason=reason,
                )
                if suspend_result.blocker is not None:
                    failed.append(
                        self._directed_effect_inactive_block_record(
                            task_id,
                            suspend_result.blocker,
                        )
                    )
                    continue
                session = suspend_result.session
                if session is None:
                    continue

                if not suspend_result.session_written:
                    reconcile_terminal_session = True

            if reconcile_terminal_session:
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
