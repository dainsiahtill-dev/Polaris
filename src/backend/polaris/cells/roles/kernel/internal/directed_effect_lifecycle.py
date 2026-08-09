"""Exact pre-dispatch TaskRuntime lifecycle orchestration for DEO-2B."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, TypeVar, cast

from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectPreflightResultV1,
    project_director_effect_public_policy_evidence,
    require_directed_effect_immutable_items,
    validate_director_effect_authorization_binding,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    DirectorEffectCurrentPolicyEvidenceV1,
    DirectorEffectPolicyBoundSnapshotV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectPolicySnapshotResultV1,
    validate_director_effect_current_policy_capture_result,
    validate_director_effect_current_policy_evidence,
    validate_director_effect_policy_member_binding_result,
    validate_director_effect_policy_snapshot_result,
)
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DirectedEffectAttemptHeartbeatResultV1,
    DirectedEffectContextClaimResultV1,
    DirectedEffectExecutionContextV1,
    DirectedEffectLifecycleResultV1,
    DirectedEffectOperationClaimStatusV1,
    DirectedEffectPreparedMemberV1,
    PreparedDirectedEffectBatchV1,
)
from polaris.cells.runtime.task_runtime.public import (
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    ClaimDirectedEffectCommandV1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationResultV1,
    DirectedEffectParentRegistryIdentityV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectOperationQueryV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    abort_directed_effect_operation,
    admit_directed_effect_operation,
    admit_directed_effect_parent_batch,
    claim_directed_effect,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    finalize_directed_effect_inventory_admission,
    get_directed_effect_operation,
    mark_directed_effect_recovery_pending,
    seal_directed_effect_inventory,
)


@dataclass(frozen=True, slots=True)
class DirectedEffectLifecycleCandidateV1:
    """One already-authorized mutation candidate; no executable capability."""

    preflight: DirectorEffectPreflightResultV1
    snapshot: DirectorEffectPolicySnapshotResultV1
    authorization_binding: DirectorEffectAuthorizationBindingV1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.preflight, DirectorEffectPreflightResultV1)
            or self.preflight.status != "authorized"
            or self.preflight.intent is None
            or self.preflight.evidence is None
        ):
            raise ValueError("candidate requires an authorized mutation preflight")
        if not isinstance(self.snapshot, DirectorEffectPolicySnapshotResultV1) or not self.snapshot.allowed:
            raise ValueError("candidate requires an allowed policy snapshot")
        if not isinstance(self.authorization_binding, DirectorEffectAuthorizationBindingV1):
            raise TypeError("authorization_binding must be DirectorEffectAuthorizationBindingV1")
        canonical_snapshot = validate_director_effect_policy_snapshot_result(self.snapshot)
        canonical_binding = validate_director_effect_authorization_binding(self.authorization_binding)
        if canonical_snapshot != self.snapshot or canonical_binding != self.authorization_binding:
            raise ValueError("candidate policy evidence must be canonical")
        evidence = canonical_binding.authorization_evidence
        classification = canonical_binding.classification_evidence
        subject = self.snapshot.subject
        intent = self.preflight.intent
        if (
            evidence != self.preflight.evidence
            or evidence.bound_policy_snapshot_hash != self.snapshot.evidence_hash
            or evidence.tool_call_id != intent.tool_call_id
            or subject.tool_call_id != intent.tool_call_id
            or subject.normalized_tool_name != intent.normalized_tool_name
            or subject.inventory_ordinal != intent.ordinal
            or subject.effect_type != intent.effect_type
            or subject.execution_mode != intent.execution_mode
            or classification.canonical_tool_name != subject.normalized_tool_name
            or classification.effect_type != subject.effect_type
            or classification.execution_mode != subject.execution_mode
            or classification.normalized_arguments != subject.normalized_arguments
        ):
            raise ValueError("candidate preflight, snapshot, binding, and intent must match")


@dataclass(frozen=True, slots=True)
class DirectedEffectTaskRuntimePortsV1:
    """Injectable public TaskRuntime callables; production defaults are exact."""

    enroll_parent_stream: Callable[
        [EnrollDirectedEffectParentRegistryStreamCommandV1],
        DirectedEffectStreamEnrollmentResultV1,
    ] = enroll_directed_effect_parent_registry_stream
    admit_parent: Callable[
        [AdmitDirectedEffectParentBatchCommandV1],
        DirectedEffectOperationResultV1,
    ] = admit_directed_effect_parent_batch
    enroll_operation_stream: Callable[
        [EnrollDirectedEffectOperationStreamCommandV1],
        DirectedEffectStreamEnrollmentResultV1,
    ] = enroll_directed_effect_operation_stream
    seal_inventory: Callable[
        [SealDirectedEffectInventoryCommandV1],
        DirectedEffectInventoryResultV1,
    ] = seal_directed_effect_inventory
    admit_operation: Callable[
        [AdmitDirectedEffectOperationCommandV1],
        DirectedEffectOperationResultV1,
    ] = admit_directed_effect_operation
    finalize_inventory: Callable[
        [FinalizeDirectedEffectInventoryAdmissionCommandV1],
        DirectedEffectInventoryResultV1,
    ] = finalize_directed_effect_inventory_admission
    claim_operation: Callable[
        [ClaimDirectedEffectCommandV1],
        DirectedEffectOperationResultV1,
    ] = claim_directed_effect
    get_operation: Callable[
        [GetDirectedEffectOperationQueryV1],
        DirectedEffectOperationResultV1,
    ] = get_directed_effect_operation
    abort_operation: Callable[
        [AbortDirectedEffectOperationCommandV1],
        DirectedEffectOperationResultV1,
    ] = abort_directed_effect_operation
    mark_recovery_pending: Callable[
        [MarkDirectedEffectRecoveryPendingCommandV1],
        DirectedEffectOperationResultV1,
    ] = mark_directed_effect_recovery_pending


_ResultT = TypeVar("_ResultT")


def _rebuild_contract_tree(value: object) -> object:
    """Re-run dataclass invariants recursively for one untrusted port result."""

    if is_dataclass(value) and not isinstance(value, type):
        changes = {item.name: _rebuild_contract_tree(getattr(value, item.name)) for item in fields(value) if item.init}
        rebuilt: object = replace(cast(Any, value), **changes)
        return rebuilt
    if isinstance(value, tuple):
        return tuple(_rebuild_contract_tree(item) for item in value)
    if isinstance(value, list):
        return [_rebuild_contract_tree(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _rebuild_contract_tree(item) for key, item in value.items()}
    return value


def _canonical_port_result(value: object, expected_type: type[_ResultT]) -> _ResultT:
    """Require an exact, recursively reconstructable public result contract."""

    if type(value) is not expected_type:
        raise TypeError(f"port result must be exactly {expected_type.__name__}")
    rebuilt = _rebuild_contract_tree(value)
    if type(rebuilt) is not expected_type or rebuilt != value:
        raise ValueError(f"port result is not canonical {expected_type.__name__}")
    return rebuilt


def _evidence(stage: str, upstream_code: str = "") -> DirectedEffectImmutableItemsV1:
    items: list[tuple[str, str]] = [("stage", stage)]
    if upstream_code:
        items.append(("upstream_code", upstream_code))
    return tuple(items)


def _denied(
    stage: str,
    error_code: DirectedEffectErrorCodeV1,
    upstream_code: str = "",
) -> DirectedEffectLifecycleResultV1:
    return DirectedEffectLifecycleResultV1(
        status="denied",
        prepared_batch=None,
        error_code=error_code,
        upstream_evidence=_evidence(stage, upstream_code),
    )


def _port_exception(
    stage: str,
    error_code: DirectedEffectErrorCodeV1,
    exc: Exception,
) -> DirectedEffectLifecycleResultV1:
    """Project one collaborator failure without swallowing process-control exceptions."""

    return _denied(stage, error_code, f"port_exception:{type(exc).__name__}")


def _parent_admission_key(
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    correlation: ParentCorrelationV1,
) -> str:
    payload = "\0".join(
        (
            "deo-2b-parent-admission-v1",
            execution_attempt.workspace,
            str(execution_attempt.task_id),
            execution_attempt.external_task_id,
            execution_attempt.session_id,
            str(execution_attempt.attempt),
            execution_attempt.role_id,
            execution_attempt.worker_id,
            execution_attempt.run_id,
            correlation.turn_id,
            correlation.batch_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _claim_context_id(*, grant_hash: str, creator_pid: int) -> str:
    payload = f"deo-2b-execution-context-v1\0{grant_hash}\0{creator_pid}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _claimed_operation_hash(grant: DirectedEffectClaimGrantV1) -> str:
    payload = json.dumps(
        grant.operation.to_record(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _current_policy_matches_claim(
    *,
    current: DirectorEffectCurrentPolicyEvidenceV1,
    authorization_binding: DirectorEffectAuthorizationBindingV1,
    bound_snapshot: DirectorEffectPolicyBoundSnapshotV1,
    grant: DirectedEffectClaimGrantV1,
) -> bool:
    """Compare sole-producer evidence with every immutable baseline source."""

    try:
        canonical = validate_director_effect_current_policy_evidence(current)
        public_policy = project_director_effect_public_policy_evidence(authorization_binding)
        snapshot = bound_snapshot.snapshot
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        canonical == current
        and current.baseline_authorization_binding_hash == authorization_binding.authorization_binding_hash
        and current.baseline_public_policy_evidence_hash == public_policy.public_policy_evidence_hash
        and current.bound_member_hash == bound_snapshot.member_binding_hash
        and current.claim_grant_hash == grant.grant_hash
        and current.policy_target_version == snapshot.policy_version
        and current.policy_target_hash == snapshot.policy_hash
        and current.operation_version == str(grant.operation_version)
        and current.operation_hash == _claimed_operation_hash(grant)
        and current.capability_scope_hash == public_policy.capability_scope_hash
        and current.job_token_id == public_policy.job_token_id
        and current.job_token_evidence_hash == public_policy.job_token_evidence_hash
        and current.tool_spec_snapshot_hash == authorization_binding.tool_spec_snapshot_hash
        and current.alias_binding_hash == authorization_binding.alias_binding_hash
        and current.execution_envelope_hash == public_policy.execution_envelope_hash
        and current.allowed_commands_hash == public_policy.allowed_command_hash
    )


def _claim_denied(
    error_code: DirectedEffectErrorCodeV1,
    *,
    operation_claim_status: DirectedEffectOperationClaimStatusV1 = "not_claimed",
) -> DirectedEffectContextClaimResultV1:
    return DirectedEffectContextClaimResultV1(
        status="denied",
        context=None,
        error_code=error_code,
        operation_claim_status=operation_claim_status,
    )


def _same_attempt_binding(
    left: TaskRuntimeExecutionAttemptIdentityV1,
    right: TaskRuntimeExecutionAttemptIdentityV1,
) -> bool:
    """Compare every public attempt field except the renewable lease timestamp."""

    return bool(left == replace(right, lease_expires_at=left.lease_expires_at))


def _refresh_directed_effect_attempt(
    *,
    authority: object,
    expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None,
    context_summary: str,
) -> tuple[DirectedEffectAttemptHeartbeatResultV1, str]:
    """Return typed freshness plus the upstream code needed by the legacy raw guard."""

    upstream_code = "authority_missing"
    try:
        if not isinstance(authority, TaskRuntimeExecutionAttemptAuthorityV1):
            raise TypeError("authority must satisfy TaskRuntimeExecutionAttemptAuthorityV1")
        if expected_execution_attempt is not None and type(expected_execution_attempt) is not (
            TaskRuntimeExecutionAttemptIdentityV1
        ):
            raise TypeError("expected_execution_attempt must be exact")
        snapshot = _canonical_port_result(
            authority.snapshot(lock_timeout_seconds=5.0),
            TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
        )
        upstream_code = snapshot.code
        current = snapshot.identity
        if not snapshot.success or snapshot.closed or current is None:
            if snapshot.closed:
                upstream_code = "authority_closed"
            raise ValueError("attempt authority is unavailable or closed")
        if expected_execution_attempt is not None and not _same_attempt_binding(
            current,
            expected_execution_attempt,
        ):
            upstream_code = "attempt_identity_mismatch"
            raise ValueError("attempt authority stable identity drift")
        heartbeat = _canonical_port_result(
            authority.heartbeat(
                lease_ttl_seconds=120,
                lock_timeout_seconds=5.0,
                context_summary=context_summary,
            ),
            TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1,
        )
        upstream_code = heartbeat.code
        renewed = heartbeat.identity
        if not heartbeat.success or renewed is None or not _same_attempt_binding(current, renewed):
            raise ValueError("attempt heartbeat denied or drifted")
        if expected_execution_attempt is not None and not _same_attempt_binding(
            renewed,
            expected_execution_attempt,
        ):
            upstream_code = "heartbeat_identity_drift"
            raise ValueError("renewed attempt stable identity drift")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return (
            DirectedEffectAttemptHeartbeatResultV1(
                status="denied",
                execution_attempt=None,
                error_code="deo_execution_attempt_heartbeat_failed",
            ),
            upstream_code,
        )
    return (
        DirectedEffectAttemptHeartbeatResultV1(
            status="fresh",
            execution_attempt=renewed,
            error_code=None,
        ),
        upstream_code,
    )


def refresh_directed_effect_attempt(
    *,
    authority: object,
    expected_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None,
    context_summary: str,
) -> DirectedEffectAttemptHeartbeatResultV1:
    """Refresh one public attempt authority without touching its private state."""

    result, _upstream_code = _refresh_directed_effect_attempt(
        authority=authority,
        expected_execution_attempt=expected_execution_attempt,
        context_summary=context_summary,
    )
    return result


class DirectedEffectLifecycleService:
    """Prepare the complete immutable inventory before any batch dispatch."""

    def __init__(
        self,
        *,
        policy_snapshot_port: DirectorEffectPolicySnapshotPortV1,
        task_runtime_ports: DirectedEffectTaskRuntimePortsV1 | None = None,
    ) -> None:
        if not isinstance(policy_snapshot_port, DirectorEffectPolicySnapshotPortV1):
            raise TypeError("policy_snapshot_port must satisfy DirectorEffectPolicySnapshotPortV1")
        self._policy_port = policy_snapshot_port
        self._ports = task_runtime_ports or DirectedEffectTaskRuntimePortsV1()

    def prepare_batch(
        self,
        *,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1,
        turn_id: str,
        batch_id: str,
        candidates: tuple[DirectedEffectLifecycleCandidateV1, ...],
    ) -> DirectedEffectLifecycleResultV1:
        if not isinstance(execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            return _denied("input", "deo_execution_attempt_invalid")
        if not candidates:
            return DirectedEffectLifecycleResultV1(
                status="not_applicable",
                prepared_batch=None,
                error_code=None,
                upstream_evidence=_evidence("read_only"),
            )
        heartbeat = refresh_directed_effect_attempt(
            authority=execution_attempt_authority,
            expected_execution_attempt=execution_attempt,
            context_summary="directed_effect_batch_prepare",
        )
        if heartbeat.status != "fresh" or heartbeat.execution_attempt is None:
            return _denied(
                "execution_attempt_heartbeat",
                "deo_execution_attempt_heartbeat_failed",
            )
        execution_attempt = heartbeat.execution_attempt
        if not isinstance(candidates, tuple) or not 1 <= len(candidates) <= 64:
            return _denied("input", "deo_inventory_invalid")
        try:
            if any(not isinstance(item, DirectedEffectLifecycleCandidateV1) for item in candidates):
                raise TypeError("invalid candidate")
            intents = tuple(item.preflight.intent for item in candidates)
            if any(intent is None for intent in intents):
                raise ValueError("missing intent")
            typed_intents = tuple(intent for intent in intents if intent is not None)
            correlation = ParentCorrelationV1(turn_id=turn_id, batch_id=batch_id)
            expected_attempt_id = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
                execution_attempt
            ).execution_attempt_id
            if any(
                candidate.authorization_binding.authorization_evidence.workspace != execution_attempt.workspace
                or candidate.authorization_binding.authorization_evidence.execution_attempt_id != expected_attempt_id
                or candidate.authorization_binding.authorization_evidence.turn_id != correlation.turn_id
                or candidate.authorization_binding.authorization_evidence.batch_id != correlation.batch_id
                for candidate in candidates
            ):
                raise ValueError("candidate execution identity drift")
        except (TypeError, ValueError):
            return _denied("input", "deo_inventory_invalid")

        try:
            parent_stream = _canonical_port_result(
                self._ports.enroll_parent_stream(
                    EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=execution_attempt)
                ),
                DirectedEffectStreamEnrollmentResultV1,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _port_exception("parent_stream_enrollment", "deo_parent_stream_enrollment_failed", exc)
        if (
            not parent_stream.ok
            or parent_stream.code != "parent_registry_stream_enrolled"
            or parent_stream.execution_attempt != execution_attempt
        ):
            return _denied("parent_stream_enrollment", "deo_parent_stream_enrollment_failed", parent_stream.code)

        parent_admission_key = _parent_admission_key(execution_attempt, correlation)
        try:
            parent_result = _canonical_port_result(
                self._ports.admit_parent(
                    AdmitDirectedEffectParentBatchCommandV1(
                        workspace=execution_attempt.workspace,
                        task_id=execution_attempt.task_id,
                        execution_attempt=execution_attempt,
                        correlation=correlation,
                        admission_idempotency_key=parent_admission_key,
                        actor="roles.kernel",
                    )
                ),
                DirectedEffectOperationResultV1,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _port_exception("parent_admission", "deo_parent_admission_failed", exc)
        if (
            not parent_result.ok
            or parent_result.code not in {"parent_admitted", "parent_idempotent_replay"}
            or parent_result.parent_binding is None
        ):
            return _denied("parent_admission", "deo_parent_admission_failed", parent_result.code)
        parent_binding = parent_result.parent_binding
        if (
            parent_binding.registry_identity
            != DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
            or parent_binding.correlation != correlation
            or parent_binding.admission_idempotency_key != parent_admission_key
            or parent_binding.registry_version < 1
            or parent_binding.parent_sequence < 1
            or parent_binding.source_event_seq != parent_binding.registry_version
            or parent_binding.actor != "roles.kernel"
        ):
            return _denied("parent_admission", "deo_parent_admission_failed", parent_result.code)

        try:
            operation_stream = _canonical_port_result(
                self._ports.enroll_operation_stream(
                    EnrollDirectedEffectOperationStreamCommandV1(
                        execution_attempt=execution_attempt,
                        parent_binding=parent_binding,
                    )
                ),
                DirectedEffectStreamEnrollmentResultV1,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _port_exception(
                "operation_stream_enrollment",
                "deo_operation_stream_enrollment_failed",
                exc,
            )
        if (
            not operation_stream.ok
            or operation_stream.code != "operation_stream_enrolled"
            or operation_stream.execution_attempt != execution_attempt
            or operation_stream.parent_binding != parent_binding
        ):
            return _denied(
                "operation_stream_enrollment",
                "deo_operation_stream_enrollment_failed",
                operation_stream.code,
            )

        seal_command = SealDirectedEffectInventoryCommandV1(
            workspace=execution_attempt.workspace,
            task_id=execution_attempt.task_id,
            execution_attempt=execution_attempt,
            parent_binding=parent_binding,
            intents=typed_intents,
            expected_registry_version=parent_binding.registry_version,
            expected_registry_seq=parent_binding.registry_version + 1,
            expected_operation_head_seq=0,
            actor="roles.kernel",
        )
        try:
            sealed_result = _canonical_port_result(
                self._ports.seal_inventory(seal_command),
                DirectedEffectInventoryResultV1,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _port_exception("inventory_seal", "deo_inventory_seal_failed", exc)
        # R191/M03: L1-01 r11 TASK-3 dropped write_file batch (tests/verify.test.ts,
        # README.md, src/verify.ts) with
        # deo_inventory_seal_failed:guarded_receipt_mismatch:result_not_ok,
        # unexpected_code:guarded_receipt_mismatch,projection_missing.
        # DEO may fail exact-replay receipt confirmation under fact-stream lock
        # pressure even after the seal fact is durable. One immediate re-seal
        # recovers via inventory_seal_idempotent_replay when the seal landed.
        if (
            not sealed_result.ok
            and str(sealed_result.code or "").strip() == "guarded_receipt_mismatch"
            and sealed_result.projection is None
        ):
            try:
                sealed_result = _canonical_port_result(
                    self._ports.seal_inventory(seal_command),
                    DirectedEffectInventoryResultV1,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                return _port_exception("inventory_seal", "deo_inventory_seal_failed", exc)
        sealed = sealed_result.projection
        # R155 live L1-01: TASK-2 write batch dropped with opaque
        # deo_inventory_seal_failed:inventory_seal_idempotent_replay while seal
        # actually returned ok=True. Lifecycle was treating legitimate seal
        # idempotent_replay (prepare_batch retry after seal/ready progressed)
        # as failure via inventory_ready / operation_head guards, and denied
        # using the success code — so ledger projected TOOL_RESULT_FAILED and
        # index.html/engine writes never dispatched.
        is_seal_replay = sealed_result.code == "inventory_seal_idempotent_replay"
        seal_fail_reasons: list[str] = []
        if not sealed_result.ok:
            seal_fail_reasons.append("result_not_ok")
        if sealed_result.code not in {"inventory_sealed", "inventory_seal_idempotent_replay"}:
            seal_fail_reasons.append(f"unexpected_code:{sealed_result.code or 'empty'}")
        if sealed is None:
            seal_fail_reasons.append("projection_missing")
        else:
            # Fresh seal must start pre-ready with empty operation stream.
            # Idempotent seal replay may already be mid-flight or fully ready.
            if sealed.inventory_ready and not is_seal_replay:
                seal_fail_reasons.append("inventory_already_ready")
            # Lease-independent binding: same-owner heartbeats renew lease_expires_at
            # between seal and later prepare retries (R171).
            if not _same_attempt_binding(sealed.execution_attempt, execution_attempt):
                seal_fail_reasons.append("execution_attempt_mismatch")
            if sealed.workspace != execution_attempt.workspace:
                seal_fail_reasons.append("workspace_mismatch")
            if sealed.task_id != execution_attempt.task_id:
                seal_fail_reasons.append("task_id_mismatch")
            if sealed.parent_binding_id != parent_binding.binding_id:
                seal_fail_reasons.append("parent_binding_mismatch")
            expected_seal_seq = parent_binding.registry_version + 1
            if is_seal_replay:
                # Parent binding still carries admit-time registry_version, but
                # seal/ready events may have advanced the parent registry head.
                # Accept any head/seal seq that is still on this binding lineage.
                if sealed.sealed_event_seq < expected_seal_seq:
                    seal_fail_reasons.append("sealed_event_seq_before_parent_admit")
                if sealed.parent_registry_source_head_seq < sealed.sealed_event_seq:
                    seal_fail_reasons.append("parent_registry_head_before_seal")
            else:
                if sealed.parent_registry_source_head_seq != expected_seal_seq:
                    seal_fail_reasons.append("parent_registry_head_mismatch")
                if sealed.sealed_event_seq != expected_seal_seq:
                    seal_fail_reasons.append("sealed_event_seq_mismatch")
            if sealed.operation_source_head_seq != 0 and not is_seal_replay:
                seal_fail_reasons.append("operation_head_nonzero")
            if len(sealed.members) != len(typed_intents):
                seal_fail_reasons.append("member_count_mismatch")
            elif tuple(member.tool_call_id for member in sealed.members) != tuple(
                intent.tool_call_id for intent in typed_intents
            ):
                seal_fail_reasons.append("member_tool_call_id_mismatch")
            elif any(
                (
                    member.ordinal,
                    member.tool_call_id,
                    member.normalized_tool_name,
                    member.effect_type,
                    member.execution_mode,
                    member.intended_effect_fingerprint,
                    member.policy_verdict_hash,
                    member.expected_receipt_binding_hash,
                )
                != (
                    intent.ordinal,
                    intent.tool_call_id,
                    intent.normalized_tool_name,
                    intent.effect_type,
                    intent.execution_mode,
                    intent.intended_effect_fingerprint,
                    intent.policy_verdict_hash,
                    intent.expected_receipt_binding_hash,
                )
                for member, intent in zip(sealed.members, typed_intents, strict=True)
            ):
                seal_fail_reasons.append("member_field_mismatch")
        if seal_fail_reasons:
            detail = f"{sealed_result.code or 'unknown'}:{','.join(seal_fail_reasons)}"
            seal_evidence = sealed_result.evidence if isinstance(sealed_result.evidence, Mapping) else {}
            seal_reason = str(seal_evidence.get("reason") or "").strip()
            if seal_reason:
                detail = f"{detail}:seal_reason:{seal_reason}"
            return _denied("inventory_seal", "deo_inventory_seal_failed", detail)
        if sealed is None:  # Defensive narrowing; projection_missing returned above.
            return _denied("inventory_seal", "deo_inventory_seal_failed", "projection_missing")

        policy_bindings = []
        for candidate, member in zip(candidates, sealed.members, strict=True):
            try:
                binding = self._policy_port.bind_member(
                    DirectorEffectPolicyMemberBindingRequestV1(
                        snapshot=candidate.snapshot,
                        authorization_evidence=candidate.authorization_binding.authorization_evidence,
                        authorization_binding=candidate.authorization_binding,
                        member=member,
                    )
                )
                canonical_binding = validate_director_effect_policy_member_binding_result(binding)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                return _port_exception("member_binding", "deo_authorization_binding_drift", exc)
            bound_snapshot = canonical_binding.bound_snapshot
            if (
                canonical_binding.status != "allowed"
                or canonical_binding.member != member
                or bound_snapshot is None
                or bound_snapshot.snapshot != candidate.snapshot
                or bound_snapshot.authorization_binding != candidate.authorization_binding
                or bound_snapshot.authorization_evidence_hash
                != candidate.authorization_binding.authorization_evidence.authorization_hash
                or bound_snapshot.authorization_binding_hash
                != candidate.authorization_binding.authorization_binding_hash
                or canonical_binding.authorization_binding_hash
                != candidate.authorization_binding.authorization_binding_hash
            ):
                return _denied(
                    "member_binding",
                    canonical_binding.error_code or "deo_authorization_binding_drift",
                )
            policy_bindings.append(canonical_binding)

        prepared_members: list[DirectedEffectPreparedMemberV1] = []
        # On seal idempotent_replay, operation stream may already hold admitted
        # members (head == len). Re-admit from seq 0 so each member hits
        # idempotent_replay rather than inventing seq beyond the stream.
        operation_head = (
            0 if is_seal_replay and sealed.operation_source_head_seq > 0 else sealed.operation_source_head_seq
        )
        for member, binding in zip(sealed.members, policy_bindings, strict=True):
            expected_operation_seq = operation_head + 1
            try:
                result = _canonical_port_result(
                    self._ports.admit_operation(
                        AdmitDirectedEffectOperationCommandV1(
                            workspace=execution_attempt.workspace,
                            task_id=execution_attempt.task_id,
                            execution_attempt=execution_attempt,
                            parent_binding=parent_binding,
                            tool_call_id=member.tool_call_id,
                            effect_id=member.effect_id,
                            expected_version=0,
                            expected_seq=expected_operation_seq,
                            actor="roles.kernel",
                            intended_effect_fingerprint=member.intended_effect_fingerprint,
                            policy_verdict_hash=member.policy_verdict_hash,
                            expected_receipt_binding_hash=member.expected_receipt_binding_hash,
                        )
                    ),
                    DirectedEffectOperationResultV1,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                return _port_exception("member_admission", "deo_member_admission_failed", exc)
            is_admit_replay = result.code == "idempotent_replay"
            if (
                not result.ok
                or result.code not in {"admitted", "idempotent_replay"}
                or result.operation is None
                or (
                    result.operation.workspace,
                    result.operation.task_id,
                    result.operation.execution_attempt_id,
                    result.operation.parent_binding_id,
                    result.operation.parent_sequence,
                    result.operation.tool_call_id,
                    result.operation.effect_id,
                    result.operation.operation_id,
                    result.operation.operation_stream_token,
                )
                != (
                    execution_attempt.workspace,
                    execution_attempt.task_id,
                    parent_binding.registry_identity.execution_attempt_id,
                    parent_binding.binding_id,
                    parent_binding.parent_sequence,
                    member.tool_call_id,
                    member.effect_id,
                    member.operation_id,
                    parent_binding.operation_stream_token,
                )
                or (
                    not is_admit_replay
                    and (
                        result.version != 1
                        or result.state != "INTENT_COMMITTED"
                        or result.snapshot is None
                        or result.snapshot.operation != result.operation
                        or result.snapshot.state != result.state
                        or result.snapshot.version != result.version
                        or result.snapshot.source_head_seq != expected_operation_seq
                    )
                )
                or (is_admit_replay and result.snapshot is None)
            ):
                return _denied("member_admission", "deo_member_admission_failed", result.code)
            if result.snapshot is None:  # Defensive narrowing; missing snapshots return above.
                return _denied("member_admission", "deo_member_admission_failed", "snapshot_missing")
            operation_head = result.snapshot.source_head_seq
            prepared_members.append(
                DirectedEffectPreparedMemberV1(
                    member=member,
                    policy_binding=binding,
                    admitted_operation_version=result.version,
                    latest_operation_stream_head=operation_head,
                )
            )

        # R145: re-bind attempt authority after multi-member admit so finalize
        # and any residual exact-lease writers observe the latest same-owner
        # lease without treating concurrent heartbeats as authority steal.
        finalize_heartbeat = refresh_directed_effect_attempt(
            authority=execution_attempt_authority,
            expected_execution_attempt=execution_attempt,
            context_summary="directed_effect_inventory_finalize",
        )
        if finalize_heartbeat.status != "fresh" or finalize_heartbeat.execution_attempt is None:
            return _denied(
                "execution_attempt_heartbeat",
                "deo_execution_attempt_heartbeat_failed",
            )
        execution_attempt = finalize_heartbeat.execution_attempt

        try:
            ready_result = _canonical_port_result(
                self._ports.finalize_inventory(
                    FinalizeDirectedEffectInventoryAdmissionCommandV1(
                        workspace=execution_attempt.workspace,
                        task_id=execution_attempt.task_id,
                        execution_attempt=execution_attempt,
                        parent_binding=parent_binding,
                        inventory_hash=sealed.inventory_hash,
                        expected_registry_version=sealed.parent_registry_source_head_seq,
                        expected_registry_seq=sealed.parent_registry_source_head_seq + 1,
                        expected_operation_head_seq=operation_head,
                        actor="roles.kernel",
                    )
                ),
                DirectedEffectInventoryResultV1,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return _port_exception("inventory_ready", "deo_inventory_ready_failed", exc)
        ready = ready_result.projection
        is_ready_replay = ready_result.code == "inventory_ready_idempotent_replay"
        # On ready idempotent_replay after seal already projected inventory_ready,
        # parent registry head does not advance again (seq stays put).
        expected_ready_parent_head = (
            sealed.parent_registry_source_head_seq
            if is_ready_replay and sealed.inventory_ready
            else sealed.parent_registry_source_head_seq + 1
        )
        if (
            not ready_result.ok
            or ready_result.code not in {"inventory_ready", "inventory_ready_idempotent_replay"}
            or ready is None
            or not ready.inventory_ready
            # Ignore renewable lease_expires_at (R171 same-owner heartbeat renew).
            or not _same_attempt_binding(ready.execution_attempt, execution_attempt)
            or ready.parent_binding_id != parent_binding.binding_id
            or ready.inventory_hash != sealed.inventory_hash
            or ready.members != sealed.members
            or ready.parent_registry_source_head_seq != expected_ready_parent_head
            or ready.ready_event_seq != ready.parent_registry_source_head_seq
            or ready.admitted_count != len(sealed.members)
            or ready.missing_operation_ids
            or ready.unexpected_operation_ids
            or ready.operation_source_head_seq != operation_head
        ):
            return _denied("inventory_ready", "deo_inventory_ready_failed", ready_result.code)
        if ready is None:  # Defensive narrowing; projection_missing returned above.
            return _denied("inventory_ready", "deo_inventory_ready_failed", "projection_missing")

        authorization_by_call = tuple(
            (
                intent.tool_call_id,
                candidate.authorization_binding.authorization_evidence,
            )
            for candidate, intent in zip(candidates, typed_intents, strict=True)
        )
        prepared_batch = PreparedDirectedEffectBatchV1(
            execution_attempt=execution_attempt,
            parent_binding=parent_binding,
            inventory=ready,
            prepared_members=tuple(prepared_members),
            call_id_index=tuple(
                (prepared.member.tool_call_id, index) for index, prepared in enumerate(prepared_members)
            ),
            latest_parent_registry_head=ready.parent_registry_source_head_seq,
            latest_operation_stream_head=ready.operation_source_head_seq,
            authorization_evidence_by_call_id=authorization_by_call,
        )
        return DirectedEffectLifecycleResultV1(
            status="ready",
            prepared_batch=prepared_batch,
            error_code=None,
            upstream_evidence=_evidence("inventory_ready", ready_result.code),
        )

    def _current_admitted_operation(
        self,
        *,
        prepared_batch: PreparedDirectedEffectBatchV1,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        prepared_member: DirectedEffectPreparedMemberV1,
    ) -> tuple[DirectedEffectOperationResultV1, int]:
        """Strictly read one admitted member and return the shared stream head."""

        member = prepared_member.member
        current = _canonical_port_result(
            self._ports.get_operation(
                GetDirectedEffectOperationQueryV1(
                    workspace=execution_attempt.workspace,
                    task_id=execution_attempt.task_id,
                    execution_attempt=execution_attempt,
                    parent_binding=prepared_batch.parent_binding,
                    tool_call_id=member.tool_call_id,
                    effect_id=member.effect_id,
                )
            ),
            DirectedEffectOperationResultV1,
        )
        if (
            not current.ok
            or current.code != "found"
            or current.operation is None
            or current.state != "INTENT_COMMITTED"
            or current.version != prepared_member.admitted_operation_version
            or current.snapshot is None
            or current.snapshot.operation != current.operation
            or current.snapshot.state != current.state
            or current.snapshot.version != current.version
            or current.snapshot.source_head_seq < prepared_batch.latest_operation_stream_head
            or (
                current.operation.workspace,
                current.operation.task_id,
                current.operation.execution_attempt_id,
                current.operation.parent_binding_id,
                current.operation.parent_sequence,
                current.operation.tool_call_id,
                current.operation.effect_id,
                current.operation.operation_id,
                current.operation.operation_stream_token,
            )
            != (
                execution_attempt.workspace,
                execution_attempt.task_id,
                prepared_batch.parent_binding.registry_identity.execution_attempt_id,
                prepared_batch.parent_binding.binding_id,
                prepared_batch.parent_binding.parent_sequence,
                member.tool_call_id,
                member.effect_id,
                member.operation_id,
                prepared_batch.parent_binding.operation_stream_token,
            )
        ):
            raise RuntimeError("deo_current_admitted_operation_unavailable")
        return current, current.snapshot.source_head_seq

    async def claim_execution_context(
        self,
        *,
        prepared_batch: PreparedDirectedEffectBatchV1,
        execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1,
        tool_call_id: str,
        current_job_token_restriction_evidence: DirectedEffectImmutableItemsV1,
    ) -> DirectedEffectContextClaimResultV1:
        """Claim one exact member and capture current policy before registration."""

        operation_claim_status: DirectedEffectOperationClaimStatusV1 = "not_claimed"
        try:
            canonical_batch = _canonical_port_result(prepared_batch, PreparedDirectedEffectBatchV1)
            normalized_tool_call_id = str(tool_call_id).strip()
            if not normalized_tool_call_id or normalized_tool_call_id != tool_call_id:
                raise ValueError("tool_call_id must be canonical")
            restrictions = require_directed_effect_immutable_items(
                "current_job_token_restriction_evidence",
                current_job_token_restriction_evidence,
            )
            heartbeat = refresh_directed_effect_attempt(
                authority=execution_attempt_authority,
                expected_execution_attempt=canonical_batch.execution_attempt,
                context_summary=f"directed_effect_pre_claim:{normalized_tool_call_id}",
            )
            if heartbeat.status != "fresh" or heartbeat.execution_attempt is None:
                return _claim_denied("deo_execution_attempt_heartbeat_failed")
            claim_execution_attempt = heartbeat.execution_attempt
            index_by_call = dict(canonical_batch.call_id_index)
            member_index = index_by_call[normalized_tool_call_id]
            prepared_member = canonical_batch.prepared_members[member_index]
            member = prepared_member.member
            authorization = dict(canonical_batch.authorization_evidence_by_call_id)[normalized_tool_call_id]
            bound_snapshot = prepared_member.policy_binding.bound_snapshot
            if bound_snapshot is None:
                raise ValueError("prepared member lacks bound snapshot")
            _current, operation_head = self._current_admitted_operation(
                prepared_batch=canonical_batch,
                execution_attempt=claim_execution_attempt,
                prepared_member=prepared_member,
            )
            # The operation stream is shared by every member. Prior members may
            # have appended claim, receipt, abort, or recovery transitions after
            # inventory admission, so the immutable admission head cannot be
            # arithmetically advanced by member index. Read the strict current
            # head, then let the claim command's CAS reject any intervening race.
            expected_seq = operation_head + 1
            operation_claim_status = "unknown"
            claim_command = ClaimDirectedEffectCommandV1(
                workspace=claim_execution_attempt.workspace,
                task_id=claim_execution_attempt.task_id,
                execution_attempt=claim_execution_attempt,
                parent_binding=canonical_batch.parent_binding,
                tool_call_id=member.tool_call_id,
                effect_id=member.effect_id,
                expected_version=prepared_member.admitted_operation_version,
                expected_seq=expected_seq,
                actor="roles.kernel",
                intended_effect_fingerprint=member.intended_effect_fingerprint,
                policy_verdict_hash=member.policy_verdict_hash,
                expected_receipt_binding_hash=member.expected_receipt_binding_hash,
            )
            claim_result = self._claim_operation(claim_command)
            # Durable EFFECT_STARTED without a grant-bearing result (ambiguous
            # append confirmation) can be rehydrated by one exact-replay claim.
            if (
                not claim_result.ok or claim_result.code != "effect_claimed" or claim_result.claim_grant is None
            ) and claim_result.state == "EFFECT_STARTED":
                operation_claim_status = "claimed"
                claim_result = self._claim_operation(claim_command)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return _claim_denied(
                "deo_claim_failed",
                operation_claim_status=operation_claim_status,
            )

        grant = claim_result.claim_grant
        durable_started = claim_result.state == "EFFECT_STARTED"
        if durable_started:
            operation_claim_status = "claimed"
        if (
            not claim_result.ok
            or claim_result.code != "effect_claimed"
            or claim_result.idempotent
            or grant is None
            or claim_result.operation != grant.operation
            or claim_result.state != "EFFECT_STARTED"
            or claim_result.version != prepared_member.admitted_operation_version + 1
            or claim_result.version != grant.operation_version
            or claim_result.snapshot is None
            or claim_result.snapshot.operation != grant.operation
            or claim_result.snapshot.state != "EFFECT_STARTED"
            or claim_result.snapshot.version != grant.operation_version
            or claim_result.snapshot.source_head_seq != expected_seq
            or not _same_attempt_binding(grant.execution_attempt, canonical_batch.execution_attempt)
            or grant.parent_binding != canonical_batch.parent_binding
            or grant.member != member
            or grant.operation.operation_id != member.operation_id
            or grant.inventory_hash != canonical_batch.inventory.inventory_hash
            or grant.claim_event_seq != expected_seq
            or grant.operation_source_head_seq != expected_seq
            or grant.parent_registry_source_head_seq != canonical_batch.latest_parent_registry_head
        ):
            return _claim_denied(
                "deo_claim_failed",
                operation_claim_status=operation_claim_status,
            )
        operation_claim_status = "claimed"

        creator_pid = os.getpid()
        try:
            public_policy = project_director_effect_public_policy_evidence(bound_snapshot.authorization_binding)
            capture = validate_director_effect_current_policy_capture_result(
                await self._policy_port.capture_current_policy_evidence(
                    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1(
                        baseline_authorization_binding=bound_snapshot.authorization_binding,
                        baseline_public_policy_evidence=public_policy,
                        bound_snapshot=bound_snapshot,
                        claimed_member=member,
                        claim_grant=grant,
                        normalized_tool=member.normalized_tool_name,
                        normalized_arguments_hash=authorization.arguments_hash,
                        current_job_token_restriction_evidence=restrictions,
                    )
                )
            )
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            self._seal_claimed_recovery_pending(
                grant=grant,
                member=member,
                reason="current_policy_capture_exception",
            )
            return _claim_denied(
                "deo_current_policy_evidence_unavailable",
                operation_claim_status=operation_claim_status,
            )
        current_policy_evidence = capture.evidence
        if (
            capture.status != "captured"
            or current_policy_evidence is None
            or not _current_policy_matches_claim(
                current=current_policy_evidence,
                authorization_binding=bound_snapshot.authorization_binding,
                bound_snapshot=bound_snapshot,
                grant=grant,
            )
        ):
            self._seal_claimed_recovery_pending(
                grant=grant,
                member=member,
                reason="current_policy_capture_denied_or_mismatch",
            )
            return _claim_denied(
                "deo_current_policy_evidence_unavailable",
                operation_claim_status=operation_claim_status,
            )

        try:
            context = DirectedEffectExecutionContextV1(
                context_id=_claim_context_id(grant_hash=grant.grant_hash, creator_pid=creator_pid),
                batch_id=canonical_batch.parent_binding.correlation.batch_id,
                creator_pid=creator_pid,
                tool_call_id=member.tool_call_id,
                normalized_tool_name=member.normalized_tool_name,
                arguments_hash=authorization.arguments_hash,
                authorization_evidence=authorization,
                claim_grant=grant,
                bound_snapshot=bound_snapshot,
                current_policy_evidence=current_policy_evidence,
                current_job_token_restriction_evidence=restrictions,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return _claim_denied(
                "deo_context_identity_mismatch",
                operation_claim_status=operation_claim_status,
            )
        return DirectedEffectContextClaimResultV1(
            status="claimed",
            context=context,
            error_code=None,
            operation_claim_status="claimed",
        )

    def _claim_operation(
        self,
        command: ClaimDirectedEffectCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Use one canonical TaskRuntime call site for initial claim and exact replay."""

        return _canonical_port_result(
            self._ports.claim_operation(command),
            DirectedEffectOperationResultV1,
        )

    def _seal_claimed_recovery_pending(
        self,
        *,
        grant: DirectedEffectClaimGrantV1,
        member: object,
        reason: str,
    ) -> None:
        """Best-effort RECOVERY_PENDING after durable claim cannot continue.

        R141: post-claim policy capture failure used to leave EFFECT_STARTED
        orphaned (multi-write batches stuck at 3/4 receipts). Recovery is
        append-only evidence; failures here must not mask the original denial.
        """

        try:
            intended = str(getattr(member, "intended_effect_fingerprint", "") or "")
            policy_hash = str(getattr(member, "policy_verdict_hash", "") or "")
            receipt_binding = str(getattr(member, "expected_receipt_binding_hash", "") or "")
            self._ports.mark_recovery_pending(
                MarkDirectedEffectRecoveryPendingCommandV1(
                    workspace=grant.execution_attempt.workspace,
                    task_id=grant.execution_attempt.task_id,
                    execution_attempt=grant.execution_attempt,
                    parent_binding=grant.parent_binding,
                    tool_call_id=grant.operation.tool_call_id,
                    effect_id=grant.operation.effect_id,
                    expected_version=grant.operation_version,
                    expected_seq=grant.claim_event_seq + 1,
                    actor="roles.kernel",
                    intended_effect_fingerprint=intended,
                    policy_verdict_hash=policy_hash,
                    expected_receipt_binding_hash=receipt_binding,
                    reason=str(reason or "post_claim_policy_unavailable")[:200],
                    recovery_evidence_ref="recovery://roles.kernel/post_claim_policy_unavailable",
                    recovery_evidence_hash="0" * 64,
                )
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return

    def abort_unclaimed_members(
        self,
        *,
        prepared_batch: PreparedDirectedEffectBatchV1,
        execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1,
        tool_call_ids: tuple[str, ...],
        reason: str,
    ) -> tuple[DirectedEffectOperationResultV1, ...]:
        """Abort exact unused ready-inventory members without claiming them."""

        canonical_batch = _canonical_port_result(prepared_batch, PreparedDirectedEffectBatchV1)
        if not isinstance(tool_call_ids, tuple) or not tool_call_ids or len(set(tool_call_ids)) != len(tool_call_ids):
            raise ValueError("tool_call_ids must be a non-empty unique tuple")
        heartbeat = refresh_directed_effect_attempt(
            authority=execution_attempt_authority,
            expected_execution_attempt=canonical_batch.execution_attempt,
            context_summary="directed_effect_abort_unclaimed_members",
        )
        if heartbeat.status != "fresh" or heartbeat.execution_attempt is None:
            raise RuntimeError("deo_execution_attempt_heartbeat_failed")
        execution_attempt = heartbeat.execution_attempt
        index_by_call = dict(canonical_batch.call_id_index)
        results: list[DirectedEffectOperationResultV1] = []
        for tool_call_id in tool_call_ids:
            member_index = index_by_call.get(tool_call_id)
            if member_index is None:
                raise RuntimeError("deo_abort_member_not_in_inventory")
            prepared_member = canonical_batch.prepared_members[member_index]
            member = prepared_member.member
            _current, operation_head = self._current_admitted_operation(
                prepared_batch=canonical_batch,
                execution_attempt=execution_attempt,
                prepared_member=prepared_member,
            )
            expected_seq = operation_head + 1
            result = _canonical_port_result(
                self._ports.abort_operation(
                    AbortDirectedEffectOperationCommandV1(
                        workspace=execution_attempt.workspace,
                        task_id=execution_attempt.task_id,
                        execution_attempt=execution_attempt,
                        parent_binding=canonical_batch.parent_binding,
                        tool_call_id=member.tool_call_id,
                        effect_id=member.effect_id,
                        expected_version=prepared_member.admitted_operation_version,
                        expected_seq=expected_seq,
                        actor="roles.kernel",
                        intended_effect_fingerprint=member.intended_effect_fingerprint,
                        policy_verdict_hash=member.policy_verdict_hash,
                        expected_receipt_binding_hash=member.expected_receipt_binding_hash,
                        reason=reason,
                    )
                ),
                DirectedEffectOperationResultV1,
            )
            if (
                not result.ok
                or result.code != "aborted"
                or result.operation is None
                or result.operation.tool_call_id != tool_call_id
                or result.state != "ABORTED"
                or result.version != prepared_member.admitted_operation_version + 1
                or result.snapshot is None
                or result.snapshot.operation != result.operation
                or result.snapshot.state != "ABORTED"
                or result.snapshot.version != result.version
                or result.snapshot.source_head_seq != expected_seq
            ):
                raise RuntimeError("deo_abort_unclaimed_member_failed")
            results.append(result)
        return tuple(results)
