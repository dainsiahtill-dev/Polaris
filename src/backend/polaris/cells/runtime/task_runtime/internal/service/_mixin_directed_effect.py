"""Collaborator mixin for TaskRuntimeService (_mixin_directed_effect)."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence, cast

from polaris.cells.runtime.task_runtime.internal.task_board import (
    Task,
    TaskBoardFileLockTimeoutError,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    AdmitDirectedEffectParentBatchCommandV1,
    AdmitDirectedEffectParentCommandV1,
    DirectedEffectOperationResultV1,
    OwnerReworkExecutionPreparationCodeV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
)

from ..directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from ..execution_session import (
    TaskExecutionSession,
    TaskExecutionSessionWriteReceipt,
    _json_compatible_copy,
    is_terminal_session_status,
    is_terminal_task_row_status,
)
from ._helpers import (
    _EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS,
    _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY,
    _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
    _OWNER_REWORK_HANDOFFS_METADATA_KEY,
    _PENDING_TERMINAL_INTENT_METADATA_KEY,
    _PENDING_TERMINAL_INTENT_SCHEMA_V1,
    _SAME_TASK_LOCAL_REWORK_AUTHORIZATIONS_METADATA_KEY,
    _TASK_SESSION_FILE_PATTERN,
    _canonical_sha256,
    _DirectedEffectRecoverySessionSweep,
    _normalize_owner_rework_handoff_record,
    logger,
)
from ._late_bindings import (
    utc_now,
    utc_now_iso,
)
from ._mixin_base import _ServiceMixinBase

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        DirectedEffectRecoverySweepItemV1,
        DirectedEffectRecoverySweepResultV1,
        OwnerReworkExecutionPreparationResultV1,
        PrepareOwnerReworkExecutionCommandV1,
        PrepareSameTaskLocalReworkCommandV1,
        ReconcileAmbiguousDirectedEffectsCommandV1,
        SameTaskLocalReworkPreparationResultV1,
    )


class _DirectedEffectMixin(_ServiceMixinBase):
    """Method group extracted losslessly from TaskRuntimeService."""

    @staticmethod
    def _after_directed_effect_linearization_lock(
        operation: Literal["parent_admission", "parent_batch_admission", "settlement"],
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> None:
        """Deterministic test seam after the cooperative session lock is held."""

        del operation, identity

    @staticmethod
    def _after_directed_effect_recovery_lease_acquired(*, lease_id: str, owner_epoch: str) -> None:
        """Deterministic test seam after durable startup authority is acquired."""

        del lease_id, owner_epoch

    def _directed_effect_recovery_deadline_result(
        self,
        *,
        stage: str,
        scanned_session_count: int = 0,
        owner_epoch: str = "",
    ) -> DirectedEffectRecoverySweepResultV1:
        """Return one typed hard-deadline failure without hiding prior facts."""

        from polaris.cells.runtime.task_runtime.public.contracts import (
            DirectedEffectRecoverySweepResultV1,
        )

        failure: dict[str, Any] = {
            "code": "recovery_deadline_exceeded",
            "stage": stage,
        }
        if owner_epoch:
            failure["owner_epoch"] = owner_epoch
        return DirectedEffectRecoverySweepResultV1(
            ok=False,
            code="partial_failure",
            workspace=str(Path(self.workspace).expanduser().resolve()),
            scanned_session_count=scanned_session_count,
            failures=(failure,),
        )

    def admit_directed_effect_parent_batch(
        self,
        command: AdmitDirectedEffectParentBatchCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Atomically roll over one completed batch and admit its successor."""

        identity = command.execution_attempt
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="workspace_mismatch",
                evidence={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )
        if command.task_id != identity.task_id:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="task_mismatch",
                evidence={
                    "command_task_id": command.task_id,
                    "identity_task_id": identity.task_id,
                },
            )

        repository = DirectedEffectOperationRepository()
        timeout = _EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS
        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=timeout):
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="file_lock_timeout",
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
            )
        try:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining < 0:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    self._after_directed_effect_linearization_lock(
                        "parent_batch_admission",
                        identity,
                    )
                    return self._admit_directed_effect_parent_batch_locked(
                        command,
                        repository=repository,
                    )
            except TaskBoardFileLockTimeoutError:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": timeout,
                    },
                )
            except OSError as exc:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="session_corrupt",
                    evidence={
                        "stage": "cooperative_session_file_lock",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
        finally:
            session_lock.release()

    def admit_directed_effect_parent(
        self,
        command: AdmitDirectedEffectParentCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Linearize parent admission against every inactive session writer.

        Lock order is fixed: this service's per-task ``RLock``, the
        cooperative session-file lock, then repository-owned FactStream locks.
        No TaskBoard transaction or projection runs in the critical section.
        """

        identity = command.execution_attempt
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="workspace_mismatch",
                evidence={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )
        if command.task_id != identity.task_id:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="task_mismatch",
                evidence={
                    "command_task_id": command.task_id,
                    "identity_task_id": identity.task_id,
                },
            )

        repository = DirectedEffectOperationRepository()
        timeout = _EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS
        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=timeout):
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="file_lock_timeout",
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
            )
        try:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining < 0:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    self._after_directed_effect_linearization_lock(
                        "parent_admission",
                        identity,
                    )
                    return self._admit_directed_effect_parent_locked(
                        command,
                        repository=repository,
                    )
            except TaskBoardFileLockTimeoutError:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": timeout,
                    },
                )
            except OSError as exc:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="session_corrupt",
                    evidence={
                        "stage": "cooperative_session_file_lock",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
        finally:
            session_lock.release()

    def _admit_directed_effect_parent_locked(
        self,
        command: AdmitDirectedEffectParentCommandV1,
        *,
        repository: DirectedEffectOperationRepository,
    ) -> DirectedEffectOperationResultV1:
        """Validate the locked session and invoke the registry-only repository."""

        identity = command.execution_attempt
        failure = self._directed_effect_active_attempt_failure_locked(
            identity,
            repository=repository,
        )
        if failure is not None:
            return failure
        return repository.admit_parent_with_validated_authority(command)

    def _admit_directed_effect_parent_batch_locked(
        self,
        command: AdmitDirectedEffectParentBatchCommandV1,
        *,
        repository: DirectedEffectOperationRepository,
    ) -> DirectedEffectOperationResultV1:
        """Validate the locked session and invoke TaskRuntime-owned rollover."""

        identity = command.execution_attempt
        failure = self._directed_effect_active_attempt_failure_locked(
            identity,
            repository=repository,
        )
        if failure is not None:
            return failure
        return repository.admit_parent_batch_with_validated_authority(command)

    def _directed_effect_active_attempt_failure_locked(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        repository: DirectedEffectOperationRepository,
    ) -> DirectedEffectOperationResultV1 | None:
        """Return a typed refusal unless the caller-held session is the active attempt."""

        try:
            session = self._read_session_locked(
                identity.task_id,
                raise_infrastructure_errors=True,
            )
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_corrupt",
                evidence={
                    "stage": "locked_session_read",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
        if session is None:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_not_found",
                evidence={"session_path": self._session_logical_path(identity.task_id)},
            )
        observed = self._execution_attempt_identity_from_session(session)
        evidence = {"observed": observed.to_record()}
        mismatch_code = self._execution_attempt_mismatch_code(identity, session)
        if mismatch_code is not None:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code=mismatch_code,
                evidence=evidence,
            )
        if session.status != "active":
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_not_active",
                evidence=evidence,
            )
        if session.is_expired(now=utc_now()):
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_lease_expired",
                evidence=evidence,
            )
        return None

    def _directed_effect_attempt_validation_failure(
        self,
        repository: DirectedEffectOperationRepository,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        code: TaskRuntimeExecutionAttemptValidationCodeV1,
        evidence: Mapping[str, Any],
    ) -> DirectedEffectOperationResultV1:
        verdict = self._execution_attempt_validation_verdict(
            valid=False,
            code=code,
            identity=identity,
            evidence=evidence,
        )
        return repository.attempt_validation_failure_result(verdict)

    def _directed_effect_inactive_pre_barrier_locked(
        self,
        session: TaskExecutionSession,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Strictly check one session while both session locks are held."""

        if self._has_pending_terminal_intent(session):
            return self._fulfilled_terminal_intent_pre_barrier_locked(session)
        identity = self._execution_attempt_identity_from_session(session)
        return DirectedEffectOperationRepository().settlement_pre_barrier(identity)

    def _fulfilled_terminal_intent_pre_barrier_locked(
        self,
        session: TaskExecutionSession,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Accept a preserved terminal intent only with its exact durable close proof."""

        pending_intent = self._pending_terminal_intent(session)
        proof_raw = session.metadata.get("terminal_settlement_proof")
        proof = proof_raw if isinstance(proof_raw, Mapping) else None

        def conflict(reason: str) -> DirectedEffectSettlementPreBarrierVerdictV1:
            return DirectedEffectSettlementPreBarrierVerdictV1(
                allowed=False,
                code="settlement_terminal_intent_conflict",
                evidence={
                    "reason": reason,
                    "pending_terminal_intent": dict(pending_intent or {}),
                    "pending_terminal_intent_valid": pending_intent is not None,
                    "terminal_settlement_proof": dict(proof or {}),
                    "terminal_settlement_proof_valid": proof is not None,
                },
            )

        required_keys = {
            "schema_version",
            "identity_hash",
            "outcome",
            "summary_hash",
            "metadata_hash",
            "terminal_transition_id",
            "terminal_intent_hash",
        }
        if pending_intent is None or set(pending_intent) != required_keys:
            return conflict("pending_terminal_intent_invalid")
        body = {key: pending_intent[key] for key in required_keys - {"terminal_intent_hash"}}
        terminal_intent_hash = str(pending_intent.get("terminal_intent_hash") or "").strip()
        outcome = cast(
            Literal["completed", "failed", "suspended"],
            str(pending_intent.get("outcome") or "").strip(),
        )
        current_identity = self._execution_attempt_identity_from_session(session)
        settlement_lease_expires_at = str(session.metadata.get("settlement_identity_lease_expires_at") or "").strip()
        if not settlement_lease_expires_at:
            return conflict("settlement_identity_lease_missing")
        settlement_identity_record = current_identity.to_record()
        settlement_identity_record["lease_expires_at"] = settlement_lease_expires_at
        try:
            settlement_identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(settlement_identity_record)
        except (TypeError, ValueError):
            return conflict("settlement_identity_invalid")
        terminal_transition_id = str(session.terminal_transition_id or "").strip()
        if (
            pending_intent.get("schema_version") != _PENDING_TERMINAL_INTENT_SCHEMA_V1
            or outcome not in {"completed", "failed", "suspended"}
            or terminal_intent_hash != _canonical_sha256(body)
            or pending_intent.get("identity_hash") != _canonical_sha256(settlement_identity.to_record())
            or not terminal_transition_id
            or pending_intent.get("terminal_transition_id") != terminal_transition_id
        ):
            return conflict("pending_terminal_intent_binding_invalid")
        for hash_field in ("identity_hash", "summary_hash", "metadata_hash", "terminal_intent_hash"):
            value = str(pending_intent.get(hash_field) or "")
            if (
                len(value) != 64
                or value != value.lower()
                or any(character not in "0123456789abcdef" for character in value)
            ):
                return conflict(f"pending_terminal_intent_{hash_field}_invalid")
        if proof is None:
            return conflict("terminal_settlement_proof_missing")
        if (
            str(proof.get("terminal_intent_hash") or "").strip() != terminal_intent_hash
            or str(proof.get("settlement_outcome") or "").strip() != outcome
            or not str(proof.get("registry_state") or "").strip()
        ):
            return conflict("terminal_settlement_proof_binding_invalid")
        expected_status = {"completed": "completed", "failed": "failed", "suspended": "suspended"}[outcome]
        if session.status != expected_status:
            return conflict("terminal_session_outcome_mismatch")

        return DirectedEffectOperationRepository().preflight_parent_for_terminal_intent(
            settlement_identity,
            outcome=outcome,
            terminal_intent_hash=terminal_intent_hash,
        )

    @staticmethod
    def _pending_terminal_intent(session: TaskExecutionSession) -> Mapping[str, Any] | None:
        """Return the persisted terminal fence, preserving malformed evidence."""

        raw = session.metadata.get(_PENDING_TERMINAL_INTENT_METADATA_KEY)
        return raw if isinstance(raw, Mapping) else None

    @staticmethod
    def _has_pending_terminal_intent(session: TaskExecutionSession) -> bool:
        """Return whether any terminal intent marker, valid or malformed, exists."""

        return _PENDING_TERMINAL_INTENT_METADATA_KEY in session.metadata

    @staticmethod
    def _build_pending_terminal_intent(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        terminal_transition_id: str,
    ) -> dict[str, Any]:
        """Build the exact durable intent that fences heartbeat and stale reclaim."""

        body: dict[str, Any] = {
            "schema_version": _PENDING_TERMINAL_INTENT_SCHEMA_V1,
            "identity_hash": _canonical_sha256(command.identity.to_record()),
            "outcome": command.outcome,
            "summary_hash": hashlib.sha256(command.summary.encode("utf-8")).hexdigest(),
            "metadata_hash": _canonical_sha256(_json_compatible_copy(dict(command.metadata))),
            "terminal_transition_id": terminal_transition_id,
        }
        return {**body, "terminal_intent_hash": _canonical_sha256(body)}

    def _after_terminal_intent_write(
        self,
        session: TaskExecutionSession,
        terminal_intent: Mapping[str, Any],
    ) -> None:
        """Deterministic crash seam after the durable intent write."""

        del session, terminal_intent

    def _after_terminal_session_write(self, session: TaskExecutionSession) -> None:
        """Deterministic crash seam after the durable terminal session write."""

        del session

    @staticmethod
    def _directed_effect_inactive_block_record(
        task_id: int,
        verdict: DirectedEffectSettlementPreBarrierVerdictV1,
    ) -> dict[str, Any]:
        """Return typed refusal evidence without projecting an inactive row."""

        evidence = dict(verdict.evidence)
        execution_event = {
            "ok": False,
            "reason": verdict.code,
            "code": verdict.code,
            "error": verdict.code,
            "evidence": evidence,
        }
        return {
            "ok": False,
            "success": False,
            "code": verdict.code,
            "reason": verdict.code,
            "task_id": str(task_id),
            "evidence": evidence,
            "execution_event": execution_event,
            "execution_events": (),
        }

    def last_session_write_receipt(self) -> TaskExecutionSessionWriteReceipt | None:
        """Return the last successful execution-session write receipt anchor."""

        with self._session_write_receipt_lock:
            return self._last_session_write_receipt

    @staticmethod
    def _owner_rework_execution_result(
        *,
        ok: bool,
        code: OwnerReworkExecutionPreparationCodeV1,
        reason: str,
        task_id: str,
        handoff_id: str = "",
        task_role: str = "",
        runtime_task_id: str = "",
        reopened: bool = False,
        idempotent: bool = False,
        execution_event: Mapping[str, Any] | None = None,
    ) -> OwnerReworkExecutionPreparationResultV1:
        """Build the public result without coupling module import initialization.

        The import is intentionally local: ``public.service`` owns the public
        facade and imports this implementation, so importing its contracts at
        module import time would create an avoidable package cycle.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            OwnerReworkExecutionPreparationResultV1,
        )

        return OwnerReworkExecutionPreparationResultV1(
            ok=ok,
            code=code,
            reason=reason,
            task_id=task_id,
            handoff_id=handoff_id,
            task_role=task_role,
            runtime_task_id=runtime_task_id,
            reopened=reopened,
            idempotent=idempotent,
            execution_event=dict(execution_event or {}),
        )

    def prepare_owner_rework_execution(
        self,
        command: PrepareOwnerReworkExecutionCommandV1,
    ) -> OwnerReworkExecutionPreparationResultV1:
        """Prepare one claimed owner/requester rework task for adapter execution.

        TaskMarket is the sole authority for owner/requester routing,
        ``resolved_only`` readiness, and claim leasing. This method consumes a
        typed snapshot of that already-granted authority and only reconciles
        TaskRuntime's task row and execution session. It never queries
        TaskMarket, recreates dependencies, or mutates TaskMarket state.

        A terminal TaskRuntime row is reopened only through the sanctioned
        runtime path, which rotates a terminal session and appends execution
        facts. A matching authorization on an already non-terminal row is a
        no-op, while a conflicting or malformed authorization fails closed.
        """

        authorization = getattr(command, "authorization", None)
        task_id = str(getattr(authorization, "task_id", "") or "").strip()
        if not task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization is missing task_id",
                task_id="unknown",
            )

        schema_version = str(getattr(authorization, "schema_version", "") or "").strip()
        if schema_version != _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization schema is invalid",
                task_id=task_id,
            )
        if str(getattr(authorization, "workspace", "") or "").strip() != self.workspace:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_workspace_mismatch",
                reason="owner rework authorization workspace does not match TaskRuntime",
                task_id=task_id,
            )

        handoff = getattr(authorization, "handoff", None)
        try:
            canonical_handoff = _normalize_owner_rework_handoff_record(handoff)
        except ValueError:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization handoff is malformed",
                task_id=task_id,
            )
        handoff_id = str(canonical_handoff["handoff_id"])

        task_role = str(getattr(authorization, "task_role", "") or "").strip().lower()
        counterparty_task_id = str(getattr(authorization, "counterparty_task_id", "") or "").strip()
        if task_role == "owner":
            expected_task_id = str(canonical_handoff["owner_task_id"])
            expected_counterparty_task_id = str(canonical_handoff["requester_task_id"])
            role_allowed = bool(canonical_handoff["owner_reopened"])
        elif task_role == "requester":
            expected_task_id = str(canonical_handoff["requester_task_id"])
            expected_counterparty_task_id = str(canonical_handoff["owner_task_id"])
            role_allowed = True
        else:
            expected_task_id = ""
            expected_counterparty_task_id = ""
            role_allowed = False
        if not role_allowed or task_id != expected_task_id or counterparty_task_id != expected_counterparty_task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_handoff_mismatch",
                reason="owner rework task role or counterparty does not match handoff",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        worker_id = str(getattr(authorization, "worker_id", "") or "").strip()
        worker_role = str(getattr(authorization, "worker_role", "") or "").strip()
        lease_token = str(getattr(authorization, "lease_token", "") or "").strip()
        claimed_item = getattr(authorization, "claimed_item", None)
        counterparty_item = getattr(authorization, "counterparty_item", None)
        if not isinstance(claimed_item, Mapping) or not isinstance(counterparty_item, Mapping):
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization item evidence is malformed",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        if (
            worker_role != "director"
            or not worker_id
            or not lease_token
            or str(claimed_item.get("task_id") or "").strip() != task_id
            or str(claimed_item.get("status") or "").strip().lower() != "in_execution"
            or str(claimed_item.get("lease_token") or "").strip() != lease_token
            or str(claimed_item.get("claimed_by") or "").strip() != worker_id
        ):
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_claim_evidence_invalid",
                reason="owner rework authorization does not prove the active Director lease",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        if str(counterparty_item.get("task_id") or "").strip() != counterparty_task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_counterparty_evidence_invalid",
                reason="owner rework authorization counterparty item does not match handoff",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        for item in (claimed_item, counterparty_item):
            metadata = item.get("metadata")
            handoffs = metadata.get(_OWNER_REWORK_HANDOFFS_METADATA_KEY) if isinstance(metadata, Mapping) else None
            record = handoffs.get(handoff_id) if isinstance(handoffs, Mapping) else None
            try:
                item_handoff = _normalize_owner_rework_handoff_record(record)
            except ValueError:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="owner_rework_handoff_evidence_invalid",
                    reason="owner rework authorization item lacks a valid matching handoff",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                )
            if item_handoff != canonical_handoff:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="owner_rework_handoff_evidence_invalid",
                    reason="owner rework authorization item handoff conflicts with claim evidence",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                )

        observable_row = self.get_task(task_id)
        if not isinstance(observable_row, Mapping):
            return self._owner_rework_execution_result(
                ok=False,
                code="runtime_task_not_found",
                reason="TaskRuntime has no execution row for the claimed owner rework task",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        runtime_task_id = self.normalize_task_id(observable_row.get("id"))
        if runtime_task_id is None:
            return self._owner_rework_execution_result(
                ok=False,
                code="runtime_task_invalid",
                reason="TaskRuntime execution row has no valid task id",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        authorization_record = {
            "schema_version": _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
            "handoff_id": handoff_id,
            "task_id": task_id,
            "task_role": task_role,
            "counterparty_task_id": counterparty_task_id,
            "worker_id": worker_id,
            "worker_role": worker_role,
            "lease_token_sha256": hashlib.sha256(lease_token.encode("utf-8")).hexdigest(),
        }
        with self._get_session_lock(runtime_task_id):
            current_row = self.get_task(task_id)
            if not isinstance(current_row, Mapping):
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_task_not_found",
                    reason="TaskRuntime execution row disappeared before preparation",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )
            current_metadata = current_row.get("metadata")
            metadata = current_metadata if isinstance(current_metadata, Mapping) else {}
            existing_record = metadata.get(_OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY)
            matching_authorization = False
            if existing_record is not None:
                if not isinstance(existing_record, Mapping):
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="owner_rework_authorization_conflict",
                        reason="TaskRuntime has malformed owner rework authorization state",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )
                matching_authorization = dict(existing_record) == authorization_record
                if not matching_authorization:
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="owner_rework_authorization_conflict",
                        reason="TaskRuntime task is already prepared for a different owner rework authorization",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )

            session = self._read_session(runtime_task_id)
            active_session = (
                session is not None and session.status == "active" and not session.is_expired(now=utc_now())
            )
            if active_session and matching_authorization:
                return self._owner_rework_execution_result(
                    ok=True,
                    code="owner_rework_execution_already_prepared",
                    reason="matching owner rework execution is already active",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                    idempotent=True,
                )
            if active_session:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_execution_lease_conflict",
                    reason="TaskRuntime has an active execution lease for this task",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )

            terminal_evidence = is_terminal_task_row_status(current_row.get("status")) or (
                session is not None and is_terminal_session_status(session.status)
            )
            if matching_authorization and not terminal_evidence:
                return self._owner_rework_execution_result(
                    ok=True,
                    code="owner_rework_execution_already_prepared",
                    reason="matching owner rework execution is already prepared",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                    idempotent=True,
                )

            reopened = False
            if terminal_evidence:
                reopened_row = self.reopen_task_row(
                    runtime_task_id,
                    reason="owner_rework_execution_authorized",
                    metadata={
                        _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY: authorization_record,
                    },
                )
                if reopened_row is None:
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="runtime_task_not_found",
                        reason="TaskRuntime could not reopen the owner rework execution row",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )
                reopened = True

            metadata_update: dict[str, Any] = {
                _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY: authorization_record,
            }
            if terminal_evidence:
                metadata_update["terminal_reset_at"] = utc_now().timestamp()
            updated_row = self.update_task_row(runtime_task_id, metadata=metadata_update)
            if updated_row is None:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_task_not_found",
                    reason="TaskRuntime execution row disappeared while recording authorization",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )
            prepared_row = self.get_task(task_id) or updated_row
            execution_event = self._append_execution_event(
                "owner_rework_execution_prepared",
                task_row=dict(prepared_row),
                session=self._read_session(runtime_task_id),
                details={
                    "handoff_id": handoff_id,
                    "task_role": task_role,
                    "counterparty_task_id": counterparty_task_id,
                    "lease_token_sha256": authorization_record["lease_token_sha256"],
                    "reopened": reopened,
                },
            )
        return self._owner_rework_execution_result(
            ok=True,
            code="owner_rework_execution_prepared",
            reason="TaskRuntime owner rework execution is prepared",
            task_id=task_id,
            handoff_id=handoff_id,
            task_role=task_role,
            runtime_task_id=str(runtime_task_id),
            reopened=reopened,
            execution_event=execution_event,
        )

    @staticmethod
    def _same_task_local_rework_result(
        *,
        ok: bool,
        code: Any,
        reason: str,
        external_task_id: str,
        runtime_task_id: str = "",
        reopened: bool = False,
        idempotent: bool = False,
        rework_attempt: int = 0,
        task_row: Mapping[str, Any] | None = None,
        execution_event: Mapping[str, Any] | None = None,
    ) -> SameTaskLocalReworkPreparationResultV1:
        from polaris.cells.runtime.task_runtime.public.contracts import (
            SameTaskLocalReworkPreparationResultV1,
        )

        return SameTaskLocalReworkPreparationResultV1(
            ok=ok,
            code=code,
            reason=reason,
            external_task_id=external_task_id or "unknown",
            runtime_task_id=runtime_task_id,
            reopened=reopened,
            idempotent=idempotent,
            rework_attempt=rework_attempt,
            task_row=dict(task_row or {}),
            execution_event=dict(execution_event or {}),
        )

    @staticmethod
    def _same_task_external_aliases(row: Mapping[str, Any]) -> set[str]:
        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        aliases: set[str] = set()
        for source in (metadata, row):
            for key in ("external_task_id", "source_task_id", "pm_task_id"):
                value = str(source.get(key) or "").strip()
                if value:
                    aliases.add(value)
        return aliases

    @staticmethod
    def _project_completion_action_effect_hash(command: PrepareSameTaskLocalReworkCommandV1) -> str:
        payload = {
            "workspace": str(Path(command.workspace).expanduser().resolve(strict=False)),
            "factory_run_id": command.factory_run_id,
            "external_task_id": command.external_task_id,
            "completion_contract_hash": command.completion_contract_hash,
            "action_id": command.action_id,
            "diagnostic_id": command.diagnostic_id,
            "obligation_id": command.obligation_id,
            "action_kind": command.action_kind,
            "owner_snapshot_hash": command.owner_snapshot_hash,
            "owner_bundle_hash": command.owner_bundle_hash,
            "diagnostic": dict(command.diagnostic),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def prepare_same_task_local_rework(
        self,
        command: PrepareSameTaskLocalReworkCommandV1,
    ) -> SameTaskLocalReworkPreparationResultV1:
        """Apply a cursor-claimed completion action to one exact TaskRuntime row."""

        external_task_id = str(command.external_task_id or "").strip()
        claim = dict(command.dispatch_claim)
        claim_identity_raw = claim.get("identity")
        claim_identity = claim_identity_raw if isinstance(claim_identity_raw, Mapping) else {}
        diagnostic = dict(command.diagnostic)
        canonical_workspace = str(Path(self.workspace).expanduser().resolve(strict=False))
        claim_valid = (
            str(Path(str(claim_identity.get("workspace") or "")).expanduser().resolve(strict=False))
            == canonical_workspace
            and str(claim_identity.get("run_id") or "").strip() == command.factory_run_id
            and str(claim_identity.get("completion_contract_hash") or "").strip() == command.completion_contract_hash
            and str(claim.get("action_id") or "").strip() == command.action_id
            and len(str(claim.get("claim_id") or "").strip()) == 64
            and type(claim.get("attempt_ordinal")) is int
            and str(diagnostic.get("diagnostic_id") or "").strip() == command.diagnostic_id
            and str(diagnostic.get("obligation_id") or "").strip() == command.obligation_id
            and str(diagnostic.get("owner_task_id") or "").strip() == external_task_id
            and str(diagnostic.get("allowed_next_action") or "").strip() == command.action_kind
        )
        if not claim_valid:
            return self._same_task_local_rework_result(
                ok=False,
                code="same_task_local_rework_receipt_mismatch",
                reason="Workflow dispatch claim and owner diagnostic do not bind this workspace, run, and task",
                external_task_id=external_task_id,
            )

        projection = self.query_observable_task_rows_projection().rows_for_factory_run(command.factory_run_id)
        matching_rows: list[Mapping[str, Any]] = []
        for row in projection:
            aliases = self._same_task_external_aliases(row)
            if len(aliases) > 1:
                if external_task_id in aliases:
                    return self._same_task_local_rework_result(
                        ok=False,
                        code="same_task_local_rework_task_identity_conflict",
                        reason="TaskRuntime row exposes conflicting PM task identities",
                        external_task_id=external_task_id,
                    )
                continue
            if aliases == {external_task_id}:
                matching_rows.append(row)
        if len(matching_rows) != 1:
            return self._same_task_local_rework_result(
                ok=False,
                code=(
                    "same_task_local_rework_task_not_found"
                    if not matching_rows
                    else "same_task_local_rework_task_identity_conflict"
                ),
                reason="Factory run must expose exactly one TaskRuntime row for the PM task identity",
                external_task_id=external_task_id,
            )

        row = matching_rows[0]
        runtime_task_id = self.normalize_task_id(row.get("id"))
        if runtime_task_id is None:
            return self._same_task_local_rework_result(
                ok=False,
                code="same_task_local_rework_task_not_found",
                reason="TaskRuntime owner row has no numeric execution identity",
                external_task_id=external_task_id,
            )

        metadata_raw = row.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        records_raw = metadata.get(_SAME_TASK_LOCAL_REWORK_AUTHORIZATIONS_METADATA_KEY)
        records = (
            [dict(item) for item in records_raw if isinstance(item, Mapping)] if isinstance(records_raw, list) else []
        )
        for record in records:
            if str(record.get("action_id") or "").strip() == command.action_id:
                return self._same_task_local_rework_result(
                    ok=True,
                    code="same_task_local_rework_already_prepared",
                    reason="The exact workflow action was already projected into TaskRuntime",
                    external_task_id=external_task_id,
                    runtime_task_id=str(runtime_task_id),
                    idempotent=True,
                    rework_attempt=int(record.get("rework_attempt") or 0),
                    task_row=row,
                )
        if len(records) >= command.max_rework_attempts:
            return self._same_task_local_rework_result(
                ok=False,
                code="same_task_local_rework_budget_exhausted",
                reason="Same-task local rework budget is exhausted",
                external_task_id=external_task_id,
                runtime_task_id=str(runtime_task_id),
                rework_attempt=len(records),
                task_row=row,
            )

        session = self._read_session(runtime_task_id)
        if session is not None and session.status == "active" and not session.is_expired(now=utc_now()):
            return self._same_task_local_rework_result(
                ok=False,
                code="same_task_local_rework_active_lease_conflict",
                reason="The owning Director task still has an active execution lease",
                external_task_id=external_task_id,
                runtime_task_id=str(runtime_task_id),
                task_row=row,
            )

        rework_attempt = len(records) + 1
        record = {
            "schema_version": "task-runtime.same-task-local-rework-record/1",
            "factory_run_id": command.factory_run_id,
            "external_task_id": external_task_id,
            "completion_contract_hash": command.completion_contract_hash,
            "action_id": command.action_id,
            "diagnostic_id": command.diagnostic_id,
            "obligation_id": command.obligation_id,
            "action_kind": command.action_kind,
            "owner_snapshot_hash": command.owner_snapshot_hash,
            "owner_bundle_hash": command.owner_bundle_hash,
            "dispatch_claim_id": str(claim.get("claim_id") or "").strip(),
            "effect_hash": self._project_completion_action_effect_hash(command),
            "rework_attempt": rework_attempt,
            "diagnostic": diagnostic,
            "prepared_at": utc_now_iso(),
        }
        records.append(record)
        metadata_update = {
            _SAME_TASK_LOCAL_REWORK_AUTHORIZATIONS_METADATA_KEY: records[-command.max_rework_attempts :],
            "factory_local_rework": record,
            "last_failure": dict(command.diagnostic),
        }
        latest_fact = self._find_latest_execution_fact_row_for_task(runtime_task_id)
        latest_fact_status = str((latest_fact or {}).get("status") or "").strip().lower()
        terminal_evidence = (
            is_terminal_task_row_status(row.get("status"))
            or is_terminal_task_row_status(latest_fact_status)
            or (session is not None and is_terminal_session_status(session.status))
        )
        reopened = False
        if terminal_evidence:
            reopened_row = self.reopen_task_row(
                runtime_task_id,
                reason="factory_quality_same_task_local_rework_authorized",
                metadata=metadata_update,
            )
            if reopened_row is None:
                return self._same_task_local_rework_result(
                    ok=False,
                    code="same_task_local_rework_active_lease_conflict",
                    reason="TaskRuntime could not safely reopen the owning execution row",
                    external_task_id=external_task_id,
                    runtime_task_id=str(runtime_task_id),
                    task_row=row,
                )
            reopened = True
        updated_row = self.update_task_row(runtime_task_id, metadata=metadata_update)
        if updated_row is None:
            return self._same_task_local_rework_result(
                ok=False,
                code="same_task_local_rework_task_not_found",
                reason="TaskRuntime owner row disappeared while recording local rework",
                external_task_id=external_task_id,
                runtime_task_id=str(runtime_task_id),
            )
        prepared_row = self.get_task(external_task_id) or updated_row
        execution_event = self._append_execution_event(
            "same_task_local_rework_prepared",
            task_row=dict(prepared_row),
            session=self._read_session(runtime_task_id),
            details={
                "factory_run_id": command.factory_run_id,
                "external_task_id": external_task_id,
                "action_id": command.action_id,
                "diagnostic_id": command.diagnostic_id,
                "dispatch_claim_id": str(claim.get("claim_id") or "").strip(),
                "effect_hash": record["effect_hash"],
                "rework_attempt": rework_attempt,
                "reopened": reopened,
            },
        )
        return self._same_task_local_rework_result(
            ok=True,
            code="same_task_local_rework_prepared",
            reason="Exact owning Director task is prepared for local repair",
            external_task_id=external_task_id,
            runtime_task_id=str(runtime_task_id),
            reopened=reopened,
            rework_attempt=rework_attempt,
            task_row=dict(prepared_row),
            execution_event=execution_event,
        )

    def _list_file_task_entities(self) -> list[Task]:
        """Return raw file-backed ``TaskBoard`` entities for owner-cell use.

        Boundary:
            This is the ``runtime.task_runtime`` owner-cell raw ``TaskBoard``
            entity boundary. It is only for mutation paths and file-backed
            projection assembly that must work with persisted ``Task`` entities.
            It is not a public read model; observable readers must use task-row
            projection APIs instead.
        """

        return cast(list[Task], self._board.list_all())

    def _reset_authority_conflicts(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        *,
        factory_run_id: str,
    ) -> list[dict[str, Any]]:
        """Return Factory authority conflicts from observable row projections.

        This is deliberately read-model-only.  Callers that need to decide
        whether a Factory run is settled must not consume raw ``TaskBoard``
        entities, because a late execution fact may supersede a row file.
        Reset keeps its raw entity traversal in the owner mutation method that
        emits its tombstone facts.
        """

        conflicts: list[dict[str, Any]] = []
        observed_task_ids: set[int] = set()
        for task_row_source in task_rows:
            task_row = dict(task_row_source)
            task_id = self.normalize_task_id(task_row.get("id"))
            if task_id is None:
                logger.warning("Skipping TaskRuntime authority row without a valid task id")
                continue
            observed_task_ids.add(task_id)
            metadata = task_row.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            row_factory_run_id = str(metadata_map.get("factory_run_id") or "").strip()
            fact_factory_run_id = self._execution_fact_factory_run_id(task_row)
            for source, owner in (
                ("task_row", row_factory_run_id),
                ("execution_fact", fact_factory_run_id),
            ):
                if owner and owner != factory_run_id:
                    conflicts.append(
                        {
                            "kind": "foreign_factory_run_binding",
                            "task_id": str(task_id),
                            "source": source,
                            "existing_factory_run_id": owner,
                            "requested_factory_run_id": factory_run_id,
                        }
                    )

            session = self._read_session(task_id)
            if session is None or session.status != "active":
                continue
            lease_expired = session.is_expired(now=utc_now())
            session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
            session_factory_run_id = str(
                row_factory_run_id
                or fact_factory_run_id
                or session_metadata.get("factory_run_id")
                or session.run_id
                or ""
            ).strip()
            conflicts.append(
                {
                    "kind": (
                        "active_expired_session"
                        if lease_expired
                        else (
                            "active_foreign_session"
                            if session_factory_run_id != factory_run_id
                            else "active_session_not_settled"
                        )
                    ),
                    "task_id": str(task_id),
                    "session_id": session.session_id,
                    "session_run_id": session.run_id,
                    "existing_factory_run_id": session_factory_run_id,
                    "requested_factory_run_id": factory_run_id,
                    "lease_expires_at": session.lease_expires_at,
                    "lease_expired": lease_expired,
                    "ownership": ("foreign" if session_factory_run_id != factory_run_id else "requested_factory_run"),
                }
            )
        for session_path in self._board.tasks_dir.glob("task_*.session.json"):
            match = _TASK_SESSION_FILE_PATTERN.fullmatch(session_path.name)
            if match is None:
                continue
            task_id = int(match.group(1))
            if task_id in observed_task_ids:
                continue
            session = self._read_session(task_id)
            if session is None or session.status != "active":
                continue
            lease_expired = session.is_expired(now=utc_now())
            session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
            session_factory_run_id = str(session_metadata.get("factory_run_id") or session.run_id or "").strip()
            conflicts.append(
                {
                    "kind": (
                        "active_expired_session"
                        if lease_expired
                        else (
                            "active_orphan_session"
                            if session_factory_run_id == factory_run_id
                            else "active_foreign_session"
                        )
                    ),
                    "task_id": str(task_id),
                    "session_id": session.session_id,
                    "session_run_id": session.run_id,
                    "existing_factory_run_id": session_factory_run_id,
                    "requested_factory_run_id": factory_run_id,
                    "lease_expires_at": session.lease_expires_at,
                    "lease_expired": lease_expired,
                    "ownership": ("foreign" if session_factory_run_id != factory_run_id else "requested_factory_run"),
                }
            )
        return conflicts

    @staticmethod
    def _session_factory_run_id(
        session: TaskExecutionSession,
        task_row: Mapping[str, Any] | None = None,
    ) -> str:
        """Resolve Factory authority from the locked session, then exact row identity."""

        row = task_row or {}
        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        session_metadata_owner = str(session_metadata.get("factory_run_id") or "").strip()
        if session_metadata_owner:
            return session_metadata_owner

        fact = metadata_map.get("task_runtime_execution_fact")
        fact_map = fact if isinstance(fact, Mapping) else {}
        runtime_execution = metadata_map.get("runtime_execution")
        runtime_map = runtime_execution if isinstance(runtime_execution, Mapping) else {}
        row_owner = str(
            row.get("factory_run_id") or metadata_map.get("factory_run_id") or fact_map.get("factory_run_id") or ""
        ).strip()
        row_session_id = str(
            row.get("session_id") or runtime_map.get("session_id") or fact_map.get("session_id") or ""
        ).strip()
        row_attempt = row.get("claim_attempt") or runtime_map.get("attempt") or fact_map.get("attempt")
        row_task_id = 0
        raw_row_id = row.get("id")
        if not isinstance(raw_row_id, bool):
            try:
                row_task_id = int(raw_row_id)
            except (TypeError, ValueError):
                row_task_id = 0
        # ``lease_expires_at`` is intentionally excluded.  Heartbeats rotate
        # that deadline while TaskBoard/fact projections may lag; ownership is
        # bound to the stable execution-attempt identity, not one volatile
        # lease snapshot.  Requiring session_id + attempt still prevents a
        # stale row from lending Factory authority to a replacement attempt.
        if not isinstance(row_attempt, bool) and isinstance(row_attempt, (int, str)):
            try:
                row_attempt_value = int(row_attempt)
            except (TypeError, ValueError):
                row_attempt_value = None
            else:
                if row_session_id == session.session_id and row_attempt_value == session.attempt:
                    return row_owner
        # Live L2-12 SIGSEGV: session persisted, TaskBoard claim_attempt /
        # session_id lagged.  The locked session is still this exact numeric
        # owner task; row metadata factory_run_id is the Factory owner.
        # Without the fallback, startup aborts and the instance cannot restart.
        if row_owner and row_task_id == session.task_id:
            return row_owner
        return ""

    def reconcile_ambiguous_directed_effects(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
    ) -> DirectedEffectRecoverySweepResultV1:
        """Recover ambiguous effects under a TaskRuntime-minted durable lease."""

        from polaris.cells.runtime.task_runtime.public.contracts import (
            DirectedEffectRecoverySweepResultV1,
        )

        canonical_workspace = str(Path(self.workspace).expanduser().resolve())
        if command.workspace != canonical_workspace:
            return DirectedEffectRecoverySweepResultV1(
                ok=False,
                code="partial_failure",
                workspace=canonical_workspace,
                scanned_session_count=0,
                failures=({"code": "workspace_mismatch"},),
            )
        deadline_monotonic = time.monotonic() + command.deadline_seconds
        lease_id = uuid.uuid4().hex
        owner_epoch = uuid.uuid4().hex
        owner_pid = os.getpid()
        remaining_seconds = deadline_monotonic - time.monotonic()
        if remaining_seconds <= 0:
            return DirectedEffectRecoverySweepResultV1(
                ok=False,
                code="partial_failure",
                workspace=canonical_workspace,
                scanned_session_count=0,
                failures=({"code": "recovery_deadline_exceeded"},),
            )

        try:
            with self._board._file_lock(
                self._directed_effect_recovery_lease_file_lock_path(),
                timeout_seconds=min(command.lock_timeout_seconds, remaining_seconds),
            ):
                lease_failure = self._claim_directed_effect_recovery_lease_locked(
                    command=command,
                    lease_id=lease_id,
                    owner_epoch=owner_epoch,
                    owner_pid=owner_pid,
                    deadline_monotonic=deadline_monotonic,
                )
                if lease_failure is not None:
                    return DirectedEffectRecoverySweepResultV1(
                        ok=False,
                        code="partial_failure",
                        workspace=canonical_workspace,
                        scanned_session_count=0,
                        failures=(lease_failure,),
                    )
                result: DirectedEffectRecoverySweepResultV1 | None = None
                release_error: Exception | None = None
                try:
                    self._after_directed_effect_recovery_lease_acquired(
                        lease_id=lease_id,
                        owner_epoch=owner_epoch,
                    )
                    if time.monotonic() >= deadline_monotonic:
                        result = self._directed_effect_recovery_deadline_result(
                            stage="after_recovery_lease_hook",
                            owner_epoch=owner_epoch,
                        )
                    else:
                        result = self._reconcile_ambiguous_directed_effects_under_lease(
                            command,
                            owner_epoch=owner_epoch,
                            deadline_monotonic=deadline_monotonic,
                        )
                finally:
                    try:
                        self._release_directed_effect_recovery_lease_locked(
                            lease_id=lease_id,
                            owner_epoch=owner_epoch,
                            owner_pid=owner_pid,
                        )
                    except (OSError, RuntimeError, TypeError, ValueError) as exc:
                        release_error = exc
                if release_error is not None:
                    prior_failures = result.failures if result is not None else ()
                    return DirectedEffectRecoverySweepResultV1(
                        ok=False,
                        code="partial_failure",
                        workspace=canonical_workspace,
                        scanned_session_count=(result.scanned_session_count if result is not None else 0),
                        items=(result.items if result is not None else ()),
                        failures=(
                            *prior_failures,
                            {"code": "recovery_lease_release_failed", "error": str(release_error)},
                        ),
                    )
                if result is None:
                    raise RuntimeError("directed effect recovery completed without a result")
                return result
        except TaskBoardFileLockTimeoutError:
            if time.monotonic() >= deadline_monotonic:
                return self._directed_effect_recovery_deadline_result(
                    stage="after_recovery_lease_lock_wait",
                    owner_epoch=owner_epoch,
                )
            return DirectedEffectRecoverySweepResultV1(
                ok=False,
                code="partial_failure",
                workspace=canonical_workspace,
                scanned_session_count=0,
                failures=({"code": "recovery_lease_lock_timeout"},),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return DirectedEffectRecoverySweepResultV1(
                ok=False,
                code="partial_failure",
                workspace=canonical_workspace,
                scanned_session_count=0,
                failures=({"code": "recovery_lease_invalid", "error": str(exc)},),
            )

    def _reconcile_ambiguous_directed_effect_session_locked(
        self,
        command: ReconcileAmbiguousDirectedEffectsCommandV1,
        *,
        task_id: int,
        current: TaskExecutionSession | None,
        task_row: Mapping[str, Any],
        owner_epoch: str,
        deadline_monotonic: float,
        remaining_operations: int,
    ) -> _DirectedEffectRecoverySessionSweep:
        """Recover one session while its local and cooperative file locks are held."""

        from polaris.cells.runtime.task_runtime.public.contracts import (
            DirectedEffectRecoverySweepItemV1,
        )

        if self._directed_effect_recovery_deadline_reached(deadline_monotonic):
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_deadline_exceeded",
                        "stage": "after_session_read",
                        "task_id": task_id,
                        "owner_epoch": owner_epoch,
                    },
                ),
                stop_sweep=True,
            )
        if current is None or current.status not in {"active", "suspended"}:
            return _DirectedEffectRecoverySessionSweep()

        current_owner = self._session_factory_run_id(current, task_row)
        if not current_owner:
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_factory_run_id_missing",
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "owner_epoch": owner_epoch,
                    },
                )
            )
        if command.factory_run_id and current_owner != command.factory_run_id:
            return _DirectedEffectRecoverySessionSweep()
        if current.status == "active" and not self._directed_effect_recovery_session_is_expired(current):
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_active_session_unexpired",
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "factory_run_id": current_owner,
                        "lease_expires_at": current.lease_expires_at,
                        "owner_epoch": owner_epoch,
                    },
                ),
                scanned_session_count=1,
            )
        if remaining_operations <= 0:
            return _DirectedEffectRecoverySessionSweep(
                failures=(
                    {
                        "code": "recovery_operation_limit_exceeded",
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "max_operations": command.max_operations,
                    },
                ),
                scanned_session_count=1,
                stop_sweep=True,
            )

        identity = self._execution_attempt_identity_from_session(current)
        repository_sweep = DirectedEffectOperationRepository().reconcile_ambiguous_started_operations(
            identity,
            actor=command.actor,
            reason=command.reason,
            max_operations=remaining_operations,
            deadline_monotonic=deadline_monotonic,
        )
        items: list[DirectedEffectRecoverySweepItemV1] = []
        failures: list[dict[str, Any]] = []
        for result in repository_sweep.results:
            state = result.state
            if not result.ok or result.operation is None:
                failures.append(
                    {
                        "code": result.code,
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "operation_id": (result.operation.operation_id if result.operation is not None else ""),
                        "evidence": dict(result.evidence),
                    }
                )
                continue
            if state == "RECOVERY_PENDING" and result.code == "recovery_pending":
                code: Literal["recovery_pending", "dead_lettered"] = "recovery_pending"
                evidence_prefix = "recovery"
            elif state == "DEAD_LETTER" and result.code == "dead_lettered":
                code = "dead_lettered"
                evidence_prefix = "resolution"
            else:
                failures.append(
                    {
                        "code": "recovery_fact_state_invalid",
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "operation_id": result.operation.operation_id,
                        "state": state,
                        "result_code": result.code,
                    }
                )
                continue
            try:
                items.append(
                    DirectedEffectRecoverySweepItemV1(
                        factory_run_id=current_owner,
                        session_id=current.session_id,
                        task_id=task_id,
                        operation_id=result.operation.operation_id,
                        code=code,
                        state=state,
                        version=result.version,
                        event_id=str(result.evidence.get("event_id") or ""),
                        evidence_ref=str(result.evidence.get(f"{evidence_prefix}_evidence_ref") or ""),
                        evidence_hash=str(result.evidence.get(f"{evidence_prefix}_evidence_hash") or ""),
                    )
                )
            except (TypeError, ValueError) as exc:
                failures.append(
                    {
                        "code": "recovery_fact_projection_invalid",
                        "task_id": task_id,
                        "session_id": current.session_id,
                        "operation_id": result.operation.operation_id,
                        "error": str(exc),
                    }
                )

        deadline_exceeded = time.monotonic() >= deadline_monotonic or any(
            failure.get("code") == "recovery_deadline_exceeded" for failure in failures
        )
        if deadline_exceeded and not any(failure.get("code") == "recovery_deadline_exceeded" for failure in failures):
            failures.append(
                {
                    "code": "recovery_deadline_exceeded",
                    "stage": "after_repository_sweep",
                    "task_id": task_id,
                    "session_id": current.session_id,
                    "owner_epoch": owner_epoch,
                }
            )
        return _DirectedEffectRecoverySessionSweep(
            items=tuple(items),
            failures=tuple(failures),
            scanned_session_count=1,
            scanned_operation_count=repository_sweep.scanned_operation_count,
            stop_sweep=deadline_exceeded,
        )

    @staticmethod
    def _after_directed_effect_recovery_session_read(
        *,
        task_id: int,
        session_id: str,
    ) -> None:
        """Test seam reached while both session authorities remain held."""

        del task_id, session_id

    def _read_directed_effect_recovery_session_locked(
        self,
        task_id: int,
    ) -> TaskExecutionSession | None:
        """Read one recovery candidate while the caller holds both session locks."""

        return self._read_session_locked(task_id, raise_infrastructure_errors=True)

    @staticmethod
    def _after_directed_effect_recovery_session_file_lock_acquired(*, task_id: int) -> None:
        """Test seam reached before recovery starts session I/O."""

        del task_id

    @staticmethod
    def _directed_effect_recovery_deadline_reached(deadline_monotonic: float) -> bool:
        """Return whether the repository-owned recovery deadline has elapsed."""

        return time.monotonic() >= deadline_monotonic
