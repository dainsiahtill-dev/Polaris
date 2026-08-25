"""Public service export for the ``runtime.task_runtime`` cell.

Primary implementation lives in
``polaris.cells.runtime.task_runtime.internal.service``.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping
from typing import NoReturn, SupportsIndex, TypeAlias, cast

from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import DirectedEffectOperationRepository
from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService, reset_runtime_task_records

from .contracts import (
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    AdmitDirectedEffectParentCommandV1,
    BindRuntimeTaskToFactoryRunCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationResultV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentRegistryResultV1,
    DirectedEffectRecoverySweepResultV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    ExpiredFactoryRunSessionFenceResultV1,
    FenceExpiredFactoryRunSessionsCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ObservableTaskRowsProjectionV1,
    OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    OwnerReworkExecutionPreparationResultV1,
    PrepareOwnerReworkExecutionCommandV1,
    PrepareSameTaskLocalReworkCommandV1,
    QuerySameTaskLocalReworkAuthorizationV1,
    ReconcileAmbiguousDirectedEffectsCommandV1,
    RuntimeTaskFactoryRunBindingResultV1,
    SameTaskLocalReworkAuthorizationQueryResultV1,
    SameTaskLocalReworkPreparationResultV1,
    SealDirectedEffectInventoryCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementCodeV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)

TaskRuntimeExecutionAttemptHeartbeatCallableV1: TypeAlias = Callable[
    [HeartbeatTaskRuntimeExecutionAttemptCommandV1],
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
]
TaskRuntimeExecutionAttemptSettlementCallableV1: TypeAlias = Callable[
    [SettleTaskRuntimeExecutionAttemptCommandV1],
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
]


def _directed_effect_authority_failure(
    command: AdmitDirectedEffectOperationCommandV1
    | ClaimDirectedEffectCommandV1
    | AbortDirectedEffectOperationCommandV1
    | CommitDirectedEffectReceiptCommandV1
    | MarkDirectedEffectRecoveryPendingCommandV1
    | DeadLetterDirectedEffectOperationCommandV1
    | GetDirectedEffectOperationQueryV1
    | GetDirectedEffectParentReadinessQueryV1
    | GetDirectedEffectParentRegistryQueryV1
    | SealDirectedEffectInventoryCommandV1
    | FinalizeDirectedEffectInventoryAdmissionCommandV1
    | GetDirectedEffectInventoryQueryV1,
    repository: DirectedEffectOperationRepository,
) -> DirectedEffectOperationResultV1 | None:
    """Validate the persisted attempt before any aggregate read or mutation."""

    return repository.validate_attempt(command.workspace, command.execution_attempt)


def _inventory_failure(
    failure: DirectedEffectOperationResultV1,
) -> DirectedEffectInventoryResultV1:
    """Copy one typed authority refusal without manufacturing a projection."""

    return DirectedEffectInventoryResultV1(
        ok=False,
        code=cast(DirectedEffectInventoryCodeV1, failure.code),
        evidence=failure.evidence,
    )


def admit_directed_effect_parent(
    command: AdmitDirectedEffectParentCommandV1,
) -> DirectedEffectOperationResultV1:
    """Persist one immutable TaskRuntime registry-owned parent binding."""

    if not isinstance(command, AdmitDirectedEffectParentCommandV1):
        raise TypeError("command must be AdmitDirectedEffectParentCommandV1")
    return TaskRuntimeService(command.workspace).admit_directed_effect_parent(command)


def admit_directed_effect_parent_batch(
    command: AdmitDirectedEffectParentBatchCommandV1,
) -> DirectedEffectOperationResultV1:
    """Admit one parent batch after TaskRuntime safely closes its predecessor."""

    if not isinstance(command, AdmitDirectedEffectParentBatchCommandV1):
        raise TypeError("command must be AdmitDirectedEffectParentBatchCommandV1")
    return TaskRuntimeService(command.workspace).admit_directed_effect_parent_batch(command)


def get_directed_effect_parent_registry(
    query: GetDirectedEffectParentRegistryQueryV1,
) -> DirectedEffectParentRegistryResultV1:
    """Strictly rebuild one attempt-scoped parent registry projection."""

    if not isinstance(query, GetDirectedEffectParentRegistryQueryV1):
        raise TypeError("query must be GetDirectedEffectParentRegistryQueryV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(query, repository)
    if failure is not None:
        return DirectedEffectParentRegistryResultV1(
            ok=False,
            code=failure.code,
            evidence=failure.evidence,
        )
    return repository.get_parent_registry(query)


def enroll_directed_effect_parent_registry_stream(
    command: EnrollDirectedEffectParentRegistryStreamCommandV1,
) -> DirectedEffectStreamEnrollmentResultV1:
    """Explicitly maintenance-enroll one attempt-derived parent registry stream."""

    if not isinstance(command, EnrollDirectedEffectParentRegistryStreamCommandV1):
        raise TypeError("command must be EnrollDirectedEffectParentRegistryStreamCommandV1")
    return DirectedEffectOperationRepository().enroll_parent_registry_stream(command)


def enroll_directed_effect_operation_stream(
    command: EnrollDirectedEffectOperationStreamCommandV1,
) -> DirectedEffectStreamEnrollmentResultV1:
    """Explicitly maintenance-enroll one strict-revalidated operation stream."""

    if not isinstance(command, EnrollDirectedEffectOperationStreamCommandV1):
        raise TypeError("command must be EnrollDirectedEffectOperationStreamCommandV1")
    return DirectedEffectOperationRepository().enroll_operation_stream(command)


def seal_directed_effect_inventory(
    command: SealDirectedEffectInventoryCommandV1,
) -> DirectedEffectInventoryResultV1:
    """Seal one complete immutable parent inventory before child admission."""

    if not isinstance(command, SealDirectedEffectInventoryCommandV1):
        raise TypeError("command must be SealDirectedEffectInventoryCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return _inventory_failure(failure) if failure is not None else repository.seal_inventory(command)


def finalize_directed_effect_inventory_admission(
    command: FinalizeDirectedEffectInventoryAdmissionCommandV1,
) -> DirectedEffectInventoryResultV1:
    """Finalize exact sealed inventory admission under a guarded snapshot."""

    if not isinstance(command, FinalizeDirectedEffectInventoryAdmissionCommandV1):
        raise TypeError("command must be FinalizeDirectedEffectInventoryAdmissionCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return _inventory_failure(failure) if failure is not None else repository.finalize_inventory(command)


def get_directed_effect_inventory(
    query: GetDirectedEffectInventoryQueryV1,
) -> DirectedEffectInventoryResultV1:
    """Read one strict current sealed inventory projection."""

    if not isinstance(query, GetDirectedEffectInventoryQueryV1):
        raise TypeError("query must be GetDirectedEffectInventoryQueryV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(query, repository)
    return _inventory_failure(failure) if failure is not None else repository.get_inventory(query)


def admit_directed_effect_operation(
    command: AdmitDirectedEffectOperationCommandV1,
) -> DirectedEffectOperationResultV1:
    """Authorize and persist ``ABSENT -> INTENT_COMMITTED`` for one effect."""

    if not isinstance(command, AdmitDirectedEffectOperationCommandV1):
        raise TypeError("command must be AdmitDirectedEffectOperationCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.admit(command)


def claim_directed_effect(
    command: ClaimDirectedEffectCommandV1,
) -> DirectedEffectOperationResultV1:
    """Authorize and persist ``INTENT_COMMITTED -> EFFECT_STARTED``."""

    if not isinstance(command, ClaimDirectedEffectCommandV1):
        raise TypeError("command must be ClaimDirectedEffectCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.claim(command)


def abort_directed_effect_operation(
    command: AbortDirectedEffectOperationCommandV1,
) -> DirectedEffectOperationResultV1:
    """Authorize and persist ``INTENT_COMMITTED -> ABORTED``."""

    if not isinstance(command, AbortDirectedEffectOperationCommandV1):
        raise TypeError("command must be AbortDirectedEffectOperationCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.abort(command)


def commit_directed_effect_receipt(
    command: CommitDirectedEffectReceiptCommandV1,
) -> DirectedEffectOperationResultV1:
    """Durably bind one physical-effect receipt to its started operation."""

    if not isinstance(command, CommitDirectedEffectReceiptCommandV1):
        raise TypeError("command must be CommitDirectedEffectReceiptCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.commit_receipt(command)


def mark_directed_effect_recovery_pending(
    command: MarkDirectedEffectRecoveryPendingCommandV1,
) -> DirectedEffectOperationResultV1:
    """Persist finite recovery evidence after an ambiguous started effect."""

    if not isinstance(command, MarkDirectedEffectRecoveryPendingCommandV1):
        raise TypeError("command must be MarkDirectedEffectRecoveryPendingCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.mark_recovery_pending(command)


def dead_letter_directed_effect_operation(
    command: DeadLetterDirectedEffectOperationCommandV1,
) -> DirectedEffectOperationResultV1:
    """Durably end an unrecoverable operation without re-running its effect."""

    if not isinstance(command, DeadLetterDirectedEffectOperationCommandV1):
        raise TypeError("command must be DeadLetterDirectedEffectOperationCommandV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(command, repository)
    return failure if failure is not None else repository.dead_letter(command)


def get_directed_effect_operation(
    query: GetDirectedEffectOperationQueryV1,
) -> DirectedEffectOperationResultV1:
    """Return one strict-stream rebuilt, non-authoritative DEO projection."""

    if not isinstance(query, GetDirectedEffectOperationQueryV1):
        raise TypeError("query must be GetDirectedEffectOperationQueryV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(query, repository)
    return failure if failure is not None else repository.get(query)


def reconcile_ambiguous_directed_effects(
    command: ReconcileAmbiguousDirectedEffectsCommandV1,
) -> DirectedEffectRecoverySweepResultV1:
    """Run one bounded TaskRuntime-owned recovery sweep without effect replay."""

    if not isinstance(command, ReconcileAmbiguousDirectedEffectsCommandV1):
        raise TypeError("command must be ReconcileAmbiguousDirectedEffectsCommandV1")
    return TaskRuntimeService(command.workspace).reconcile_ambiguous_directed_effects(command)


def get_directed_effect_parent_readiness(
    query: GetDirectedEffectParentReadinessQueryV1,
) -> DirectedEffectParentReadinessResultV1:
    """Read a strict, non-authoritative parent operation-stream diagnostic."""

    if not isinstance(query, GetDirectedEffectParentReadinessQueryV1):
        raise TypeError("query must be GetDirectedEffectParentReadinessQueryV1")
    repository = DirectedEffectOperationRepository()
    failure = _directed_effect_authority_failure(query, repository)
    if failure is not None:
        return DirectedEffectParentReadinessResultV1(
            ok=False,
            code=failure.code,
            evidence=failure.evidence,
        )
    return repository.get_parent_readiness(query)


def heartbeat_task_runtime_execution_attempt(
    command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
    """Renew one canonical execution attempt through TaskRuntime's bounded owner path."""

    return TaskRuntimeService(command.workspace).heartbeat_execution_attempt(command)


def settle_task_runtime_execution_attempt(
    command: SettleTaskRuntimeExecutionAttemptCommandV1,
) -> dict[str, object]:
    """Settle one claim through TaskRuntime's canonical identity fence."""

    return TaskRuntimeService(command.workspace).settle_execution_attempt(command)


def settle_task_runtime_execution_attempt_typed(
    command: SettleTaskRuntimeExecutionAttemptCommandV1,
) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
    """Settle one attempt and normalize TaskRuntime's legacy mapping to a verdict."""

    result = settle_task_runtime_execution_attempt(command)
    if not isinstance(result, Mapping):
        raise RuntimeError("task runtime settlement returned a non-mapping result")
    success = result.get("success")
    code = result.get("code")
    idempotent = result.get("idempotent", False)
    evidence = result.get("evidence", {})
    if not isinstance(success, bool) or not isinstance(code, str) or not isinstance(idempotent, bool):
        raise RuntimeError("task runtime settlement returned an invalid typed result")
    if not isinstance(evidence, Mapping):
        raise RuntimeError("task runtime settlement returned invalid evidence")
    allowed_codes = {
        "settled",
        "settlement_idempotent",
        "workspace_mismatch",
        "session_not_found",
        "session_task_mismatch",
        "session_mismatch",
        "attempt_mismatch",
        "role_mismatch",
        "worker_mismatch",
        "run_mismatch",
        "external_task_id_mismatch",
        "lease_version_mismatch",
        "session_not_active",
        "session_lease_expired",
        "file_lock_timeout",
        "session_terminal_preserved",
        "terminal_outcome_conflict",
        "settlement_parent_close_required",
        "settlement_parent_close_proof_required",
        "settlement_parent_registry_invalid",
        "settlement_parent_registry_unavailable",
        "settlement_directed_effect_unresolved",
        "settlement_effect_outcome_conflict",
        "settlement_terminal_intent_conflict",
        "settlement_parent_close_failed",
        "row_projection_failed",
    }
    if code not in allowed_codes:
        raise RuntimeError(f"task runtime settlement returned unsupported code: {code}")
    return TaskRuntimeExecutionAttemptSettlementVerdictV1(
        success=success,
        code=cast(TaskRuntimeExecutionAttemptSettlementCodeV1, code),
        workspace=command.workspace,
        identity=command.identity,
        outcome=command.outcome,
        idempotent=idempotent,
        evidence=dict(evidence),
    )


class TaskRuntimeExecutionAttemptAuthorityV1:
    """Process-local, non-durable authority handle for one TaskRuntime attempt.

    The handle is a derived capability only. TaskRuntime's persisted execution
    facts remain authoritative for lease validity, terminal winner selection,
    and recovery. Its bounded lock linearizes public heartbeat renewal and
    terminal settlement for this process-local caller.
    """

    __slots__ = (
        "_closed",
        "_heartbeat",
        "_identity",
        "_lock",
        "_operation_owner_thread_id",
        "_settle",
        "_terminal_outcome",
    )

    def __init__(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        heartbeat: TaskRuntimeExecutionAttemptHeartbeatCallableV1 = heartbeat_task_runtime_execution_attempt,
        settle: TaskRuntimeExecutionAttemptSettlementCallableV1 = settle_task_runtime_execution_attempt_typed,
    ) -> None:
        if not isinstance(identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if not callable(heartbeat):
            raise TypeError("heartbeat must be callable")
        if not callable(settle):
            raise TypeError("settle must be callable")
        self._identity = identity
        self._heartbeat = heartbeat
        self._settle = settle
        self._lock = threading.RLock()
        self._closed = False
        self._operation_owner_thread_id: int | None = None
        self._terminal_outcome: str | None = None

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        """Reject persistence: this capability is intentionally process-local."""

        del protocol
        raise TypeError("TaskRuntimeExecutionAttemptAuthorityV1 is not serializable")

    @staticmethod
    def _timeout(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("lock_timeout_seconds must be a finite number")
        timeout = float(value)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("lock_timeout_seconds must be a finite number >= 0")
        return timeout

    @staticmethod
    def _same_attempt_binding(
        current: TaskRuntimeExecutionAttemptIdentityV1,
        renewed: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> bool:
        return (
            current.workspace,
            current.task_id,
            current.external_task_id,
            current.session_id,
            current.attempt,
            current.role_id,
            current.worker_id,
            current.run_id,
            current.schema_version,
        ) == (
            renewed.workspace,
            renewed.task_id,
            renewed.external_task_id,
            renewed.session_id,
            renewed.attempt,
            renewed.role_id,
            renewed.worker_id,
            renewed.run_id,
            renewed.schema_version,
        )

    def snapshot(
        self,
        *,
        lock_timeout_seconds: float = 5.0,
    ) -> TaskRuntimeExecutionAttemptAuthoritySnapshotV1:
        """Return the current identity under the handle's bounded lock."""

        timeout = self._timeout(lock_timeout_seconds)
        if not self._lock.acquire(timeout=timeout):
            return TaskRuntimeExecutionAttemptAuthoritySnapshotV1(
                success=False,
                code="authority_lock_timeout",
                identity=None,
                closed=False,
            )
        try:
            return TaskRuntimeExecutionAttemptAuthoritySnapshotV1(
                success=True,
                code="available",
                identity=self._identity,
                closed=self._closed,
            )
        finally:
            self._lock.release()

    def heartbeat(
        self,
        *,
        lease_ttl_seconds: int,
        lock_timeout_seconds: float,
        context_summary: str = "",
    ) -> TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1:
        """Renew and atomically replace the current identity when TaskRuntime accepts it."""

        timeout = self._timeout(lock_timeout_seconds)
        if not self._lock.acquire(timeout=timeout):
            return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                success=False,
                code="authority_lock_timeout",
                identity=None,
            )
        operation_owner_thread_id = threading.get_ident()
        operation_claimed = False
        try:
            current = self._identity
            if self._operation_owner_thread_id is not None:
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="authority_operation_in_progress",
                    identity=current,
                )
            self._operation_owner_thread_id = operation_owner_thread_id
            operation_claimed = True
            if self._closed:
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="authority_closed",
                    identity=current,
                )
            command = HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=current.workspace,
                identity=current,
                lease_ttl_seconds=lease_ttl_seconds,
                lock_timeout_seconds=timeout,
                context_summary=context_summary,
            )
            try:
                verdict = self._heartbeat(command)
            except Exception as exc:  # noqa: BLE001 - injected boundary failures must fail closed.
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="heartbeat_callback_exception",
                    identity=current,
                    callback_error_type=type(exc).__name__,
                )
            if not isinstance(verdict, TaskRuntimeExecutionAttemptHeartbeatVerdictV1):
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="heartbeat_invalid_verdict",
                    identity=current,
                )
            if not verdict.success:
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="heartbeat_rejected",
                    identity=current,
                    task_runtime_verdict=verdict,
                )
            renewed = verdict.renewed_identity
            if not isinstance(renewed, TaskRuntimeExecutionAttemptIdentityV1):
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="heartbeat_missing_renewed_identity",
                    identity=current,
                    task_runtime_verdict=verdict,
                )
            if verdict.identity != current or not self._same_attempt_binding(current, renewed):
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="heartbeat_identity_drift",
                    identity=current,
                    task_runtime_verdict=verdict,
                )
            if self._closed or self._terminal_outcome is not None:
                return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                    success=False,
                    code="authority_closed",
                    identity=current,
                    task_runtime_verdict=verdict,
                )
            self._identity = renewed
            return TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1(
                success=True,
                code="heartbeat_renewed",
                identity=renewed,
                task_runtime_verdict=verdict,
            )
        finally:
            if operation_claimed and self._operation_owner_thread_id == operation_owner_thread_id:
                self._operation_owner_thread_id = None
            self._lock.release()

    def settle(
        self,
        *,
        outcome: str,
        summary: str,
        metadata: Mapping[str, object] | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1:
        """Settle the current identity, then close this handle after terminal success.

        A same-outcome replay returns a typed success without re-opening the
        handle. The handle is process-local, so this never blocks durable Phase-B
        replay, which remains TaskRuntime/Factory-owned recovery work.
        """

        timeout = self._timeout(lock_timeout_seconds)
        if outcome not in {"completed", "failed", "suspended"}:
            raise ValueError("outcome must be completed, failed, or suspended")
        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if not self._lock.acquire(timeout=timeout):
            return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                success=False,
                code="authority_lock_timeout",
                identity=None,
                outcome=cast(TaskRuntimeExecutionAttemptSettlementOutcomeV1, outcome),
            )
        operation_owner_thread_id = threading.get_ident()
        operation_claimed = False
        try:
            current = self._identity
            typed_outcome = cast(TaskRuntimeExecutionAttemptSettlementOutcomeV1, outcome)
            if self._operation_owner_thread_id is not None:
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="authority_operation_in_progress",
                    identity=current,
                    outcome=typed_outcome,
                )
            self._operation_owner_thread_id = operation_owner_thread_id
            operation_claimed = True
            if self._closed:
                if self._terminal_outcome == outcome:
                    return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                        success=True,
                        code="terminal_replay",
                        identity=current,
                        outcome=typed_outcome,
                    )
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="terminal_outcome_conflict",
                    identity=current,
                    outcome=typed_outcome,
                )
            command = SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=current.workspace,
                identity=current,
                outcome=typed_outcome,
                summary=summary,
                lock_timeout_seconds=timeout,
                metadata=dict(metadata or {}),
            )
            try:
                verdict = self._settle(command)
            except Exception as exc:  # noqa: BLE001 - injected boundary failures must fail closed.
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="settlement_callback_exception",
                    identity=current,
                    outcome=typed_outcome,
                    callback_error_type=type(exc).__name__,
                )
            if not isinstance(verdict, TaskRuntimeExecutionAttemptSettlementVerdictV1):
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="settlement_invalid_verdict",
                    identity=current,
                    outcome=typed_outcome,
                )
            if not verdict.success:
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="settlement_rejected",
                    identity=current,
                    outcome=typed_outcome,
                    task_runtime_verdict=verdict,
                )
            if (
                verdict.workspace != command.workspace
                or verdict.identity != command.identity
                or verdict.outcome != command.outcome
            ):
                return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                    success=False,
                    code="settlement_verdict_drift",
                    identity=current,
                    outcome=typed_outcome,
                    task_runtime_verdict=verdict,
                )
            self._closed = True
            self._terminal_outcome = outcome
            return TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1(
                success=True,
                code="settled",
                identity=current,
                outcome=typed_outcome,
                task_runtime_verdict=verdict,
            )
        finally:
            if operation_claimed and self._operation_owner_thread_id == operation_owner_thread_id:
                self._operation_owner_thread_id = None
            self._lock.release()


def create_task_runtime_execution_attempt_authority(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    heartbeat: TaskRuntimeExecutionAttemptHeartbeatCallableV1 = heartbeat_task_runtime_execution_attempt,
    settle: TaskRuntimeExecutionAttemptSettlementCallableV1 = settle_task_runtime_execution_attempt_typed,
) -> TaskRuntimeExecutionAttemptAuthorityV1:
    """Create the non-durable public authority handle for a validated attempt."""

    return TaskRuntimeExecutionAttemptAuthorityV1(
        identity,
        heartbeat=heartbeat,
        settle=settle,
    )


def open_task_runtime_execution_attempt_authority(
    command: OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
    """Open a read-only validated process-local authority through TaskRuntime."""

    if not isinstance(command, OpenTaskRuntimeExecutionAttemptAuthorityCommandV1):
        raise TypeError("command must be OpenTaskRuntimeExecutionAttemptAuthorityCommandV1")
    return TaskRuntimeService(command.workspace).open_execution_attempt_authority(command)


def fence_expired_factory_run_sessions(
    command: FenceExpiredFactoryRunSessionsCommandV1,
) -> ExpiredFactoryRunSessionFenceResultV1:
    """Irrevocably fence expired sessions owned by one Factory run."""

    return TaskRuntimeService(command.workspace).fence_expired_factory_run_sessions(command)


def query_observable_task_rows(workspace: str) -> ObservableTaskRowsProjectionV1:
    """Return TaskRuntime rows with explicit fact-authority provenance."""

    return TaskRuntimeService(workspace).query_observable_task_rows_projection()


def query_factory_run_settlement(workspace: str, *, factory_run_id: str) -> dict[str, object]:
    """Return TaskRuntime-owned settlement evidence for one Factory run."""

    return TaskRuntimeService(workspace).query_factory_run_settlement(
        factory_run_id=factory_run_id,
    )


def validate_task_runtime_execution_attempt(
    query: ValidateTaskRuntimeExecutionAttemptQueryV1,
) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
    """Validate persisted TaskRuntime execution authority without mutation."""

    return TaskRuntimeService(query.workspace).validate_execution_attempt(query)


def bind_runtime_task_to_factory_run(
    command: BindRuntimeTaskToFactoryRunCommandV1,
) -> RuntimeTaskFactoryRunBindingResultV1:
    """Bind one existing TaskRuntime row to a Factory portfolio run."""

    runtime = TaskRuntimeService(command.workspace)
    return runtime.bind_task_to_factory_run(command)


def prepare_owner_rework_execution(
    command: PrepareOwnerReworkExecutionCommandV1,
) -> OwnerReworkExecutionPreparationResultV1:
    """Prepare one already-claimed TaskMarket owner-rework task for execution.

    TaskMarket decides orchestration, dependency readiness, and the claim
    lease. This public boundary delegates only TaskRuntime's execution-row and
    session transition to the runtime owner cell.
    """

    runtime = TaskRuntimeService(command.authorization.workspace)
    return runtime.prepare_owner_rework_execution(command)


def prepare_same_task_local_rework(
    command: PrepareSameTaskLocalReworkCommandV1,
) -> SameTaskLocalReworkPreparationResultV1:
    """Project a canonical QA requeue receipt into the exact TaskRuntime row."""

    runtime = TaskRuntimeService(command.workspace)
    return runtime.prepare_same_task_local_rework(command)


def query_same_task_local_rework_authorization(
    query: QuerySameTaskLocalReworkAuthorizationV1,
) -> SameTaskLocalReworkAuthorizationQueryResultV1:
    """Read one committed local-rework authorization from durable execution facts."""

    runtime = TaskRuntimeService(query.workspace)
    return runtime.query_same_task_local_rework_authorization(query)


__all__ = [
    "TaskRuntimeExecutionAttemptAuthorityV1",
    "TaskRuntimeService",
    "abort_directed_effect_operation",
    "admit_directed_effect_operation",
    "admit_directed_effect_parent",
    "bind_runtime_task_to_factory_run",
    "claim_directed_effect",
    "create_task_runtime_execution_attempt_authority",
    "enroll_directed_effect_operation_stream",
    "enroll_directed_effect_parent_registry_stream",
    "fence_expired_factory_run_sessions",
    "get_directed_effect_operation",
    "get_directed_effect_parent_registry",
    "heartbeat_task_runtime_execution_attempt",
    "open_task_runtime_execution_attempt_authority",
    "prepare_owner_rework_execution",
    "prepare_same_task_local_rework",
    "query_factory_run_settlement",
    "query_observable_task_rows",
    "query_same_task_local_rework_authorization",
    "reset_runtime_task_records",
    "settle_task_runtime_execution_attempt",
    "settle_task_runtime_execution_attempt_typed",
    "validate_task_runtime_execution_attempt",
]
