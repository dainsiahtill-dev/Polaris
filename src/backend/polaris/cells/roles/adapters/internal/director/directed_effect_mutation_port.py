"""Private consume-only Director mutation port for DEO-2B."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from polaris.cells.director.runtime.public import (
    DirectedEffectErrorCodeV1,
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectedEffectImmutableSequenceV1,
    DirectedEffectImmutableValueV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectExecutionValidationRequestV1,
    DirectorEffectExecutionValidationResultV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_arguments,
    validate_directed_effect_execution,
)
from polaris.cells.roles.kernel.public import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectExecutionContextV1,
    DirectedEffectFenceConsumePortV1,
    DirectedEffectMutationPortResultV1,
    DirectedEffectMutationPortV1,
    DirectedEffectToolResultV1,
    validate_directed_effect_execution_context,
)
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DirectedEffectFenceConsumeResultV1,
)
from polaris.cells.runtime.task_runtime.public import (
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectOperationResultV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    commit_directed_effect_receipt,
    dead_letter_directed_effect_operation,
    mark_directed_effect_recovery_pending,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PreparedMutationV1:
    context: DirectedEffectExecutionContextV1
    tool_name: str
    arguments: DirectedEffectImmutableItemsV1
    arguments_hash: str
    authorization: DirectorEffectAuthorizationEvidenceV1
    repair_binding: DeferredDirectorRepairEffectBindingV1 | None


def _denied(error_code: DirectedEffectErrorCodeV1) -> DirectedEffectMutationPortResultV1:
    return DirectedEffectMutationPortResultV1(
        ok=False,
        status="denied",
        tool_result=None,
        error_code=error_code,
    )


def _recovery_tool_result(
    result: DirectedEffectOperationResultV1 | None,
) -> DirectedEffectToolResultV1 | None:
    """Project only a confirmed TaskRuntime recovery/dead-letter fact."""

    if (
        result is None
        or not result.ok
        or result.state not in {"RECOVERY_PENDING", "DEAD_LETTER"}
        or result.operation is None
        or not str(result.evidence.get("event_id") or "").strip()
    ):
        return None
    recovery = {
        "schema_version": "roles.adapters.directed_effect_recovery_fact.v1",
        "authoritative": True,
        "durable": True,
        "code": result.code,
        "event_id": result.evidence.get("event_id"),
        "operation_id": result.operation.operation_id,
        "state": result.state,
        "version": result.version,
        "recovery_evidence_ref": result.evidence.get("recovery_evidence_ref"),
        "recovery_evidence_hash": result.evidence.get("recovery_evidence_hash"),
        "resolution_evidence_ref": result.evidence.get("resolution_evidence_ref"),
        "resolution_evidence_hash": result.evidence.get("resolution_evidence_hash"),
    }
    return _freeze_tool_result({"effect_recovery": recovery})


def _failed(
    recovery: DirectedEffectOperationResultV1 | None = None,
    *,
    failure_kind: str = "",
    physical_error: str = "",
    physical_error_type: str = "",
) -> DirectedEffectMutationPortResultV1:
    """Build a failed mutation result with nested physical-error evidence.

    R182/M03: bare ``deo_physical_execution_failed`` left Run Ledger / residual
    attribution without the executor's concrete error (destructive shrink, path
    shape, CAS deny, etc.). Surface a compact detail payload so lifecycle reason
    and failure_evidence can carry the nested cause without inventing success.
    """

    detail: dict[str, object] = {
        "schema_version": "roles.adapters.directed_effect_physical_failure.v1",
        "error_code": "deo_physical_execution_failed",
        "failure_kind": str(failure_kind or "").strip() or "physical_result_failed",
        "physical_error": str(physical_error or "").strip()[:800],
        "physical_error_type": str(physical_error_type or "").strip()[:120],
    }
    recovery_result = _recovery_tool_result(recovery)
    if recovery_result is not None:
        thawed = {key: _thaw_value(value) for key, value in recovery_result.payload}
        if "effect_recovery" in thawed:
            detail["effect_recovery"] = thawed["effect_recovery"]
    try:
        tool_result = _freeze_tool_result(detail)
    except TypeError:
        tool_result = recovery_result
    return DirectedEffectMutationPortResultV1(
        ok=False,
        status="failed",
        tool_result=tool_result,
        error_code="deo_physical_execution_failed",
    )


def _freeze_value(value: object) -> DirectedEffectImmutableValueV1:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key.strip() for key in value):
            raise TypeError("tool result mapping keys must be non-empty strings")
        return DirectedEffectImmutableMapV1(
            items=tuple(sorted((key, _freeze_value(item)) for key, item in value.items()))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return DirectedEffectImmutableSequenceV1(items=tuple(_freeze_value(item) for item in value))
    raise TypeError("tool result contains a non-immutable value")


def _freeze_tool_result(result: Mapping[str, object]) -> DirectedEffectToolResultV1:
    if any(not isinstance(key, str) or not key.strip() for key in result):
        raise TypeError("tool result mapping keys must be non-empty strings")
    return DirectedEffectToolResultV1(
        payload=tuple(sorted((key, _freeze_value(value)) for key, value in result.items()))
    )


def _thaw_value(value: object) -> object:
    if isinstance(value, DirectedEffectImmutableMapV1):
        return {key: _thaw_value(item) for key, item in value.items}
    if isinstance(value, DirectedEffectImmutableSequenceV1):
        return [_thaw_value(item) for item in value.items]
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _thaw_arguments(arguments: DirectedEffectImmutableItemsV1) -> dict[str, Any]:
    return {key: _thaw_value(value) for key, value in arguments}


def _canonical_validation_result(
    result: object,
) -> DirectorEffectExecutionValidationResultV1 | None:
    if (
        not isinstance(result, DirectorEffectExecutionValidationResultV1)
        or result.__class__ is not DirectorEffectExecutionValidationResultV1
    ):
        return None
    typed = result
    try:
        canonical = DirectorEffectExecutionValidationResultV1(
            allowed=typed.allowed,
            status=typed.status,
            error_code=typed.error_code,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    return canonical if canonical == typed else None


def _canonical_revalidation_result(
    result: object,
) -> DirectorEffectPolicyRevalidationResultV1 | None:
    if (
        not isinstance(result, DirectorEffectPolicyRevalidationResultV1)
        or result.__class__ is not DirectorEffectPolicyRevalidationResultV1
    ):
        return None
    typed = result
    try:
        target = typed.current_target_state_evidence
        canonical_target = DirectorEffectTargetStateEvidenceV1(
            target_path=target.target_path,
            exists=target.exists,
            before_content_hash=target.before_content_hash,
            minimal_content_evidence=target.minimal_content_evidence,
            agents_policy_hash=target.agents_policy_hash,
            target_state_hash=target.target_state_hash,
            is_no_file_state=target.is_no_file_state,
        )
        canonical = DirectorEffectPolicyRevalidationResultV1(
            status=typed.status,
            allowed=typed.allowed,
            error_code=typed.error_code,
            current_policy_version=typed.current_policy_version,
            current_policy_hash=typed.current_policy_hash,
            current_target_state_evidence=canonical_target,
            current_target_state_hash=typed.current_target_state_hash,
            current_normalized_operation_hash=typed.current_normalized_operation_hash,
            target_observation_performed=typed.target_observation_performed,
            current_evidence_hash=typed.current_evidence_hash,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    return canonical if canonical == typed else None


def _canonical_consume_result(result: object) -> DirectedEffectFenceConsumeResultV1 | None:
    if (
        not isinstance(result, DirectedEffectFenceConsumeResultV1)
        or result.__class__ is not DirectedEffectFenceConsumeResultV1
    ):
        return None
    typed = result
    try:
        canonical = DirectedEffectFenceConsumeResultV1(
            ok=typed.ok,
            status=typed.status,
            context_id=typed.context_id,
            error_code=typed.error_code,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    return canonical if canonical == typed else None


def _repair_state_matches(
    evidence: DirectorEffectTargetStateEvidenceV1,
    binding: DeferredDirectorRepairEffectBindingV1,
    *,
    after: bool,
) -> bool:
    effect = binding.effect
    expected_exists = effect.exists_after if after else effect.exists_before
    expected_hash = effect.expected_after_hash if after else effect.expected_before_hash
    return bool(
        not evidence.is_no_file_state
        and evidence.target_path == effect.target_path
        and evidence.exists is expected_exists
        and (not expected_exists or evidence.before_content_hash == expected_hash)
    )


def _physical_effect_receipt(
    *,
    context: DirectedEffectExecutionContextV1,
    normalized_tool_name: str,
    arguments_hash: str,
    physical_result_hash: str,
    policy_evidence_hash: str,
    target_state_hash: str,
    repair_binding: DeferredDirectorRepairEffectBindingV1 | None,
) -> dict[str, object]:
    """Project the hash-bound receipt that TaskRuntime must durably commit."""

    effect = repair_binding.effect if repair_binding is not None else None
    receipt_payload: DirectedEffectImmutableItemsV1 = (
        ("arguments_hash", arguments_hash),
        ("authoritative", True),
        ("batch_id", context.batch_id),
        ("claim_grant_hash", context.claim_grant.grant_hash),
        ("context_id", context.context_id),
        ("durable", True),
        ("effect_call_id", effect.call_id if effect is not None else None),
        ("effect_operation_id", effect.operation_id if effect is not None else None),
        ("normalized_tool_name", normalized_tool_name),
        ("operation_id", context.claim_grant.operation.operation_id),
        ("parent_close_eligible", True),
        ("physical_result_hash", physical_result_hash),
        ("plan_hash", repair_binding.plan_hash if repair_binding is not None else None),
        ("policy_evidence_hash", policy_evidence_hash),
        ("repair_binding_hash", repair_binding.binding_hash if repair_binding is not None else None),
        ("repair_contingency_kind", effect.contingency_kind if effect is not None else None),
        ("repair_request_hash", repair_binding.request_hash if repair_binding is not None else None),
        ("receipt_binding_hash", context.claim_grant.member.expected_receipt_binding_hash),
        ("receipt_outcome", "succeeded"),
        ("schema_version", "roles.adapters.director_physical_effect_receipt.v2"),
        ("target_state_hash", target_state_hash),
        ("tool_call_id", context.tool_call_id),
    )
    receipt_hash = hash_directed_effect_arguments(receipt_payload)
    return {
        **dict(receipt_payload),
        "receipt_hash": receipt_hash,
        "receipt_id": f"director-physical-effect-{receipt_hash[:24]}",
    }


def _commit_physical_effect_receipt(
    context: DirectedEffectExecutionContextV1,
    receipt: Mapping[str, object],
) -> DirectedEffectOperationResultV1:
    """Commit the adapter receipt through TaskRuntime's only public DEO port."""

    grant = context.claim_grant
    return commit_directed_effect_receipt(
        CommitDirectedEffectReceiptCommandV1(
            workspace=grant.execution_attempt.workspace,
            task_id=grant.execution_attempt.task_id,
            execution_attempt=grant.execution_attempt,
            parent_binding=grant.parent_binding,
            tool_call_id=grant.operation.tool_call_id,
            effect_id=grant.operation.effect_id,
            expected_version=grant.operation_version,
            expected_seq=grant.operation_source_head_seq + 1,
            actor="roles.adapters.director",
            intended_effect_fingerprint=grant.member.intended_effect_fingerprint,
            policy_verdict_hash=grant.member.policy_verdict_hash,
            expected_receipt_binding_hash=grant.member.expected_receipt_binding_hash,
            receipt_ref=cast(str, receipt["receipt_id"]),
            receipt_hash=cast(str, receipt["receipt_hash"]),
            receipt_binding_hash=cast(str, receipt["receipt_binding_hash"]),
            receipt_outcome="succeeded",
        )
    )


def _mark_physical_effect_recovery(
    context: DirectedEffectExecutionContextV1,
    *,
    reason: str,
    evidence: DirectedEffectImmutableItemsV1,
) -> DirectedEffectOperationResultV1:
    """Persist finite recovery after a consumed fence; never re-run the effect."""

    grant = context.claim_grant
    evidence_hash = hash_directed_effect_arguments(evidence)
    return mark_directed_effect_recovery_pending(
        MarkDirectedEffectRecoveryPendingCommandV1(
            workspace=grant.execution_attempt.workspace,
            task_id=grant.execution_attempt.task_id,
            execution_attempt=grant.execution_attempt,
            parent_binding=grant.parent_binding,
            tool_call_id=grant.operation.tool_call_id,
            effect_id=grant.operation.effect_id,
            expected_version=grant.operation_version,
            expected_seq=grant.operation_source_head_seq + 1,
            actor="roles.adapters.director",
            intended_effect_fingerprint=grant.member.intended_effect_fingerprint,
            policy_verdict_hash=grant.member.policy_verdict_hash,
            expected_receipt_binding_hash=grant.member.expected_receipt_binding_hash,
            reason=reason,
            recovery_evidence_ref=f"recovery://director/{context.context_id}",
            recovery_evidence_hash=evidence_hash,
        )
    )


def _attempt_physical_effect_recovery(
    context: DirectedEffectExecutionContextV1,
    *,
    reason: str,
    evidence: DirectedEffectImmutableItemsV1,
    terminalize_declared_failure: bool = False,
    terminal_reason: str = "physical executor returned a declared non-success result",
) -> DirectedEffectOperationResultV1 | None:
    """Persist recovery; terminalize only a proven no-effect failure.

    Exceptions and post-state ambiguity remain ``RECOVERY_PENDING`` because the
    physical side effect may have happened.  An executor-declared ``ok=False``
    or a pre-effect policy denial after one-use fence consumption proves no
    physical effect can still occur.  After recording recovery, close that
    member as ``DEAD_LETTER`` so the failed Director attempt can settle and a
    fresh attempt can inspect current disk state before planning another edit.
    """

    try:
        result = _mark_physical_effect_recovery(context, reason=reason, evidence=evidence)
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.exception(
            "TaskRuntime recovery append raised after physical effect consumption: context_id=%s",
            context.context_id,
        )
        return None
    if not result.ok:
        logger.error(
            "TaskRuntime recovery append failed after physical effect consumption: context_id=%s code=%s",
            context.context_id,
            result.code,
        )
        return None
    if terminalize_declared_failure:
        source_head_seq = result.evidence.get("source_head_seq")
        if (
            result.state != "RECOVERY_PENDING"
            or result.operation is None
            or result.version is None
            or isinstance(source_head_seq, bool)
            or not isinstance(source_head_seq, int)
            or source_head_seq < 1
        ):
            logger.error(
                "TaskRuntime proven no-effect recovery lacked terminalization evidence: "
                "context_id=%s state=%s version=%s source_head_seq=%s",
                context.context_id,
                result.state,
                result.version,
                source_head_seq,
            )
            return result
        grant = context.claim_grant
        resolution_evidence = (*evidence, ("recovery_event_id", str(result.evidence.get("event_id") or "")))
        try:
            terminal = dead_letter_directed_effect_operation(
                DeadLetterDirectedEffectOperationCommandV1(
                    workspace=grant.execution_attempt.workspace,
                    task_id=grant.execution_attempt.task_id,
                    execution_attempt=grant.execution_attempt,
                    parent_binding=grant.parent_binding,
                    tool_call_id=grant.operation.tool_call_id,
                    effect_id=grant.operation.effect_id,
                    expected_version=result.version,
                    expected_seq=source_head_seq + 1,
                    actor="roles.adapters.director",
                    intended_effect_fingerprint=grant.member.intended_effect_fingerprint,
                    policy_verdict_hash=grant.member.policy_verdict_hash,
                    expected_receipt_binding_hash=grant.member.expected_receipt_binding_hash,
                    reason=str(terminal_reason or "declared no-effect failure")[:200],
                    resolution_evidence_ref=f"dead-letter://director/{context.context_id}",
                    resolution_evidence_hash=hash_directed_effect_arguments(resolution_evidence),
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.exception(
                "TaskRuntime dead-letter append raised after proven no-effect failure: context_id=%s",
                context.context_id,
            )
            return result
        if not terminal.ok or terminal.state != "DEAD_LETTER":
            logger.error(
                "TaskRuntime dead-letter append failed after proven no-effect failure: context_id=%s code=%s state=%s",
                context.context_id,
                terminal.code,
                terminal.state,
            )
            return result
        return terminal
    return result


def _prepare_mutation(
    *,
    workspace: str,
    context: DirectedEffectExecutionContextV1,
    normalized_tool_name: str,
    normalized_arguments: DirectedEffectImmutableItemsV1,
    repair_binding: DeferredDirectorRepairEffectBindingV1 | None,
) -> tuple[_PreparedMutationV1 | None, DirectedEffectErrorCodeV1 | None]:
    """Canonicalize and authorize immutable mutation inputs without side effects."""

    try:
        if validate_directed_effect_execution_context(context) != context:
            return None, "deo_context_identity_mismatch"
        tool_name = str(normalized_tool_name).strip()
        if not tool_name or tool_name != normalized_tool_name:
            return None, "deo_operation_hash_mismatch"
        arguments = DirectedEffectImmutableMapV1(items=normalized_arguments).items
        arguments_hash = hash_directed_effect_arguments(arguments)
        authorization = context.authorization_evidence
        if (
            authorization.workspace != workspace
            or context.normalized_tool_name != tool_name
            or context.arguments_hash != arguments_hash
        ):
            return None, "deo_operation_hash_mismatch"
        if repair_binding is not None:
            effect = repair_binding.effect
            if type(repair_binding) is not DeferredDirectorRepairEffectBindingV1:
                return None, "deo_context_identity_mismatch"
            if (
                repair_binding.tool_call_id != context.tool_call_id
                or effect.tool_name != tool_name
                or effect.arguments_hash != arguments_hash
            ):
                return None, "deo_operation_hash_mismatch"
        validation = _canonical_validation_result(
            validate_directed_effect_execution(
                DirectorEffectExecutionValidationRequestV1(
                    actual_normalized_tool_name=tool_name,
                    actual_arguments_hash=arguments_hash,
                    current_policy_hash=authorization.policy_hash,
                    current_scope_hash=authorization.capability_scope_hash,
                    current_job_token_evidence_hash=authorization.job_token_evidence_hash,
                    expected_context_id=context.context_id,
                    authorization_evidence=authorization,
                    claim_grant=context.claim_grant,
                ),
                context.bound_snapshot,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return None, "deo_context_identity_mismatch"
    if validation is None:
        return None, "deo_authorization_hash_drift"
    if not validation.allowed:
        return None, validation.error_code or "deo_authorization_hash_drift"
    return (
        _PreparedMutationV1(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
            arguments_hash=arguments_hash,
            authorization=authorization,
            repair_binding=repair_binding,
        ),
        None,
    )


def _policy_request(prepared: _PreparedMutationV1, *, workspace: str) -> DirectorEffectPolicyRevalidationRequestV1:
    snapshot = prepared.context.bound_snapshot
    return DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=snapshot,
        workspace=workspace,
        actual_normalized_tool_name=prepared.tool_name,
        actual_normalized_arguments=prepared.arguments,
        actual_arguments_hash=prepared.arguments_hash,
        authorization_evidence=prepared.authorization,
        member=snapshot.member,
        operation_id=snapshot.member.operation_id,
        claim_grant=prepared.context.claim_grant,
        current_job_token_restriction_evidence=prepared.context.current_job_token_restriction_evidence,
    )


def _receipt_commit_is_valid(
    context: DirectedEffectExecutionContextV1,
    receipt: Mapping[str, Any],
    commit: DirectedEffectOperationResultV1,
) -> bool:
    evidence = commit.evidence
    if (
        not commit.ok
        or commit.code not in {"receipt_committed", "idempotent_replay"}
        or commit.state != "RECEIPT_COMMITTED"
        or commit.operation != context.claim_grant.operation
        or not str(evidence.get("event_id") or "").strip()
    ):
        return False
    expected = {
        "receipt_ref": receipt["receipt_id"],
        "receipt_hash": receipt["receipt_hash"],
        "receipt_binding_hash": receipt["receipt_binding_hash"],
        "receipt_outcome": receipt["receipt_outcome"],
    }
    return all(evidence.get(field) == value for field, value in expected.items())


class _DirectorDirectedEffectMutationPort:
    """Validate, revalidate, spend, then invoke the private physical executor."""

    __slots__ = ("_consume", "_policy", "_workspace")

    def __init__(
        self,
        *,
        workspace: str,
        policy_snapshot_port: DirectorEffectPolicySnapshotPortV1,
        fence_consume_port: DirectedEffectFenceConsumePortV1,
    ) -> None:
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        canonical_workspace = str(Path(workspace).resolve())
        if not isinstance(policy_snapshot_port, DirectorEffectPolicySnapshotPortV1):
            raise TypeError("policy_snapshot_port must satisfy DirectorEffectPolicySnapshotPortV1")
        if not isinstance(fence_consume_port, DirectedEffectFenceConsumePortV1):
            raise TypeError("fence_consume_port must satisfy DirectedEffectFenceConsumePortV1")
        self._workspace = canonical_workspace
        self._policy = policy_snapshot_port
        self._consume = fence_consume_port

    async def _revalidate_policy(
        self,
        prepared: _PreparedMutationV1,
    ) -> tuple[
        DirectorEffectPolicyRevalidationRequestV1 | None,
        DirectorEffectPolicyRevalidationResultV1 | None,
        DirectedEffectErrorCodeV1 | None,
    ]:
        try:
            request = _policy_request(prepared, workspace=self._workspace)
            result = _canonical_revalidation_result(await self._policy.revalidate(request))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None, None, "deo_director_policy_denied"
        if result is None:
            return None, None, "deo_authorization_evidence_drift"
        if not result.allowed:
            return None, None, result.error_code or "deo_director_policy_denied"
        if prepared.repair_binding is not None and not _repair_state_matches(
            result.current_target_state_evidence,
            prepared.repair_binding,
            after=False,
        ):
            return None, None, "deo_target_state_drift"
        return request, result, None

    def _consume_once(self, prepared: _PreparedMutationV1) -> DirectedEffectErrorCodeV1 | None:
        try:
            consumption = _canonical_consume_result(self._consume.consume(prepared.context))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return "deo_context_identity_mismatch"
        if consumption is None:
            return "deo_context_identity_mismatch"
        if (
            consumption.context_id != prepared.context.context_id
            or not consumption.ok
            or consumption.status != "consumed"
        ):
            return consumption.error_code or "deo_context_identity_mismatch"
        return None

    def _settle_pre_effect_policy_denial(
        self,
        prepared: _PreparedMutationV1,
        *,
        error_code: DirectedEffectErrorCodeV1,
    ) -> DirectedEffectMutationPortResultV1:
        """Burn the one-use fence and terminalize a claimed no-effect denial.

        TaskRuntime has already moved the operation to ``EFFECT_STARTED`` before
        this port revalidates policy.  A target/policy drift denial occurs before
        the physical executor, so leaving that claim open makes the parent
        settlement wait forever.  Consume the fence first to exclude a competing
        physical effect, then append recovery + DEAD_LETTER evidence.  If either
        step is ambiguous, preserve a failed/nonterminal result for reconciliation.
        """

        consume_error = self._consume_once(prepared)
        if consume_error is not None:
            return _failed(
                failure_kind="pre_effect_policy_denial_fence_not_consumed",
                physical_error=str(consume_error),
            )
        recovery = _attempt_physical_effect_recovery(
            prepared.context,
            reason="policy revalidation denied before physical execution after fence consumption",
            evidence=(
                ("context_id", prepared.context.context_id),
                ("failure_kind", "pre_effect_policy_denial"),
                ("policy_error_code", error_code),
                ("physical_executor_invoked", False),
                ("fence_consumed", True),
            ),
            terminalize_declared_failure=True,
            terminal_reason="policy revalidation denied before physical execution",
        )
        if recovery is not None and recovery.ok and recovery.state == "DEAD_LETTER":
            return _denied(error_code)
        return _failed(
            recovery,
            failure_kind="pre_effect_policy_denial_terminalization_failed",
            physical_error=str(error_code),
        )

    def _execute_physical(
        self,
        prepared: _PreparedMutationV1,
    ) -> tuple[Mapping[str, Any] | None, DirectedEffectMutationPortResultV1 | None]:
        failure_kind = "physical_executor_construction_exception"
        try:
            from polaris.cells.roles.adapters.internal.director.execution_tools import (
                _create_director_tool_executor,
            )

            executor = _create_director_tool_executor(self._workspace)
            failure_kind = "physical_executor_exception"
            raw_result = executor.execute_tool(
                prepared.tool_name,
                _thaw_arguments(prepared.arguments),
                task_id=prepared.context.claim_grant.execution_attempt.external_task_id,
                repair_effect=(prepared.repair_binding.effect if prepared.repair_binding is not None else None),
            )
        except Exception as exc:  # noqa: BLE001 - every post-consume failure must enter durable recovery
            recovery = _attempt_physical_effect_recovery(
                prepared.context,
                reason=(
                    "physical executor construction raised after fence consumption"
                    if failure_kind == "physical_executor_construction_exception"
                    else "physical executor raised after fence consumption"
                ),
                evidence=(
                    ("context_id", prepared.context.context_id),
                    ("failure_kind", failure_kind),
                    ("exception_type", type(exc).__name__),
                    ("exception_message", str(exc)[:400]),
                ),
            )
            return None, _failed(
                recovery,
                failure_kind=failure_kind,
                physical_error=str(exc),
                physical_error_type=type(exc).__name__,
            )
        if not isinstance(raw_result, Mapping) or raw_result.get("ok") is not True:
            nested_error = ""
            nested_type = ""
            if isinstance(raw_result, Mapping):
                nested_error = str(raw_result.get("error") or raw_result.get("message") or "").strip()
                nested_type = str(raw_result.get("error_type") or "").strip()
            recovery = _attempt_physical_effect_recovery(
                prepared.context,
                reason="physical executor returned a non-success result after fence consumption",
                evidence=(
                    ("context_id", prepared.context.context_id),
                    ("failure_kind", "physical_result_failed"),
                    ("result_type", type(raw_result).__name__),
                    ("physical_error", nested_error[:400]),
                    ("physical_error_type", nested_type[:80]),
                ),
                terminalize_declared_failure=True,
            )
            return None, _failed(
                recovery,
                failure_kind="physical_result_failed",
                physical_error=nested_error,
                physical_error_type=nested_type,
            )
        return raw_result, None

    async def _observe_post_state(
        self,
        prepared: _PreparedMutationV1,
        request: DirectorEffectPolicyRevalidationRequestV1,
        pre_state: DirectorEffectPolicyRevalidationResultV1,
    ) -> tuple[DirectorEffectPolicyRevalidationResultV1 | None, DirectedEffectMutationPortResultV1 | None]:
        if prepared.repair_binding is None:
            return pre_state, None
        try:
            post_state = _canonical_revalidation_result(await self._policy.revalidate(request))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            recovery = _attempt_physical_effect_recovery(
                prepared.context,
                reason="post-effect target observation raised",
                evidence=(
                    ("context_id", prepared.context.context_id),
                    ("failure_kind", "post_effect_observation_exception"),
                ),
            )
            return None, _failed(recovery)
        valid = (
            post_state is not None
            and post_state.target_observation_performed
            and post_state.error_code in {None, "deo_target_state_drift"}
            and _repair_state_matches(post_state.current_target_state_evidence, prepared.repair_binding, after=True)
        )
        if valid:
            return post_state, None
        recovery = _attempt_physical_effect_recovery(
            prepared.context,
            reason="post-effect target observation did not confirm the mutation",
            evidence=(
                ("context_id", prepared.context.context_id),
                ("failure_kind", "post_effect_observation_mismatch"),
            ),
        )
        return None, _failed(recovery)

    @staticmethod
    def _commit_receipt(
        prepared: _PreparedMutationV1,
        raw_result: Mapping[str, Any],
        receipt_state: DirectorEffectPolicyRevalidationResultV1,
    ) -> DirectedEffectMutationPortResultV1:
        try:
            physical_tool_result = _freeze_tool_result(raw_result)
            receipt = _physical_effect_receipt(
                context=prepared.context,
                normalized_tool_name=prepared.tool_name,
                arguments_hash=prepared.arguments_hash,
                physical_result_hash=hash_directed_effect_arguments(physical_tool_result.payload),
                policy_evidence_hash=receipt_state.current_evidence_hash,
                target_state_hash=receipt_state.current_target_state_hash,
                repair_binding=prepared.repair_binding,
            )
            receipt_commit = _commit_physical_effect_receipt(prepared.context, receipt)
            if not _receipt_commit_is_valid(prepared.context, receipt, receipt_commit):
                recovery = _attempt_physical_effect_recovery(
                    prepared.context,
                    reason="physical receipt could not be committed to TaskRuntime",
                    evidence=(
                        ("context_id", prepared.context.context_id),
                        ("failure_kind", "receipt_commit_failed"),
                        ("receipt_hash", cast(str, receipt["receipt_hash"])),
                        ("task_runtime_code", receipt_commit.code),
                    ),
                )
                return _failed(recovery)
            commit_evidence = receipt_commit.evidence
            enriched_result = dict(raw_result)
            enriched_result["effect_receipt"] = receipt
            enriched_result["effect_receipt_commit"] = {
                "code": receipt_commit.code,
                "event_id": commit_evidence.get("event_id"),
                "operation_id": prepared.context.claim_grant.operation.operation_id,
                "receipt_ref": commit_evidence.get("receipt_ref"),
                "receipt_hash": commit_evidence.get("receipt_hash"),
                "receipt_binding_hash": commit_evidence.get("receipt_binding_hash"),
                "receipt_outcome": commit_evidence.get("receipt_outcome"),
                "state": receipt_commit.state,
                "version": receipt_commit.version,
            }
            tool_result = _freeze_tool_result(enriched_result)
        except (OSError, RuntimeError, TypeError, ValueError):
            recovery = _attempt_physical_effect_recovery(
                prepared.context,
                reason="physical receipt projection or commit raised after execution",
                evidence=(("context_id", prepared.context.context_id), ("failure_kind", "receipt_commit_exception")),
            )
            return _failed(recovery)
        return DirectedEffectMutationPortResultV1(ok=True, status="executed", tool_result=tool_result, error_code=None)

    async def execute_mutation(
        self,
        context: DirectedEffectExecutionContextV1,
        normalized_tool_name: str,
        normalized_arguments: DirectedEffectImmutableItemsV1,
        repair_effect_binding: DeferredDirectorRepairEffectBindingV1 | None = None,
    ) -> DirectedEffectMutationPortResultV1:
        prepared, prepare_error = _prepare_mutation(
            workspace=self._workspace,
            context=context,
            normalized_tool_name=normalized_tool_name,
            normalized_arguments=normalized_arguments,
            repair_binding=repair_effect_binding,
        )
        if prepared is None:
            return _denied(prepare_error or "deo_context_identity_mismatch")
        revalidation_request, revalidation, policy_error = await self._revalidate_policy(prepared)
        if revalidation_request is None or revalidation is None:
            return self._settle_pre_effect_policy_denial(
                prepared,
                error_code=policy_error or "deo_director_policy_denied",
            )
        consume_error = self._consume_once(prepared)
        if consume_error is not None:
            return _denied(consume_error)

        raw_result, physical_failure = self._execute_physical(prepared)
        if raw_result is None:
            return physical_failure or _failed()
        receipt_state, observation_failure = await self._observe_post_state(
            prepared,
            revalidation_request,
            revalidation,
        )
        if receipt_state is None:
            return observation_failure or _failed()
        return self._commit_receipt(prepared, raw_result, receipt_state)


def create_director_directed_effect_mutation_port(
    *,
    workspace: str,
    policy_snapshot_port: DirectorEffectPolicySnapshotPortV1,
    fence_consume_port: DirectedEffectFenceConsumePortV1,
) -> DirectedEffectMutationPortV1:
    """Private constructor used only by the adapter public composition boundary."""

    return _DirectorDirectedEffectMutationPort(
        workspace=workspace,
        policy_snapshot_port=policy_snapshot_port,
        fence_consume_port=fence_consume_port,
    )
