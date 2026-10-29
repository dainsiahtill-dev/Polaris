"""ToolBatchExecutor core methods (everything except execute_tool_batch).

Private implementation module of the tool_batch_executor package.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any, NoReturn

from polaris.cells.director.runtime.public import DirectedEffectImmutableItemsV1, hash_directed_effect_arguments
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    DeferredRequestReplayFence,
)
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    DirectedEffectLifecycleCandidateV1,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import (
    DirectedEffectAuthoritativePolicyGuardRequestV1,
    DirectedEffectPolicyGuardResultV1,
    _immutable_arguments,
)
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.deferred_repair_followup import (
    DeferredCommandEffectSynthesizer,
    build_deferred_repair_followup,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.effect_policy import (
    CompiledEffectPolicy,
    EffectPolicyViolationError,
    get_effect_policy_mode,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent as _default_requires_mutation_intent,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    normalize_batch_receipts,
)
from polaris.cells.roles.kernel.internal.transaction.tool_failure_circuit_breaker import (
    ToolFailureCircuitBreaker,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectRuntimeDependenciesV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent, TurnEvent, TurnPhaseEvent
from polaris.cells.runtime.task_runtime.public import (
    GetDirectedEffectOperationQueryV1,
    TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
    get_directed_effect_operation,
    validate_task_runtime_execution_attempt,
)
from polaris.kernelone.tool_execution.contracts import tool_batch_inventory_error

from ._helpers import (
    _DEO_PREPARE_LOCK_RETRY_ATTEMPTS,
    _DEO_PREPARE_LOCK_RETRY_BASE_SECONDS,
    _RECOVERABLE_DEO_ADMISSION_CODES,
    _collapse_last_write_wins_mutations,
    _deo_prepare_upstream_code,
    _is_deo_abort_error,
    _is_no_write_structured_turn,
    _is_transient_deo_prepare_lock_failure,
    _mapping_value,
    _merge_batch_receipts,
    _normalize_capability_token,
    _PreparedDirectedEffectDispatchV1,
    _RecoverableDirectedEffectDenialError,
    _seal_deo_abort_tool_lifecycle,
    _serial_mutation_cohorts,
    logger,
)


class _ToolBatchExecutorCore:
    """Core ToolBatchExecutor methods excluding execute_tool_batch."""

    def __init__(
        self,
        *,
        tool_runtime: Any,
        config: TransactionConfig,
        emit_event: Callable[[TurnEvent], None],
        guard_assert_single_tool_batch: Callable[..., None],
        finalization_handler: Any,
        handoff_handler: Any,
        requires_mutation_intent: Callable[[str], bool] | None = None,
        tool_failure_circuit_breaker: ToolFailureCircuitBreaker | None = None,
        effect_policy: CompiledEffectPolicy | None = None,
        directed_effect_runtime: DirectedEffectRuntimeDependenciesV1 | None = None,
        directed_effect_required: bool = False,
        directed_effect_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        directed_effect_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
        capability_token: Mapping[str, Any] | None = None,
        execution_envelope_hash: str = "",
    ) -> None:
        self.tool_runtime = tool_runtime
        self.config = config
        self.emit_event = emit_event
        self.guard_assert_single_tool_batch = guard_assert_single_tool_batch
        self.finalization_handler = finalization_handler
        self.handoff_handler = handoff_handler
        self.requires_mutation_intent = requires_mutation_intent or _default_requires_mutation_intent
        self._tool_failure_circuit_breaker = tool_failure_circuit_breaker or ToolFailureCircuitBreaker()
        self._effect_policy = effect_policy
        if (
            directed_effect_runtime is not None
            and type(directed_effect_runtime) is not DirectedEffectRuntimeDependenciesV1
        ):
            raise TypeError("directed_effect_runtime must be exactly DirectedEffectRuntimeDependenciesV1")
        if directed_effect_execution_attempt is not None:
            if type(directed_effect_execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
                raise TypeError(
                    "directed_effect_execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1"
                )
            canonical_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
                directed_effect_execution_attempt.to_record()
            )
            if canonical_attempt != directed_effect_execution_attempt:
                raise ValueError("directed_effect_execution_attempt must be canonical")
        if directed_effect_execution_attempt_authority is not None and not isinstance(
            directed_effect_execution_attempt_authority,
            TaskRuntimeExecutionAttemptAuthorityV1,
        ):
            raise TypeError("directed_effect_execution_attempt_authority must be exact")
        if directed_effect_required and (
            directed_effect_runtime is None
            or directed_effect_execution_attempt is None
            or directed_effect_execution_attempt_authority is None
        ):
            raise ValueError("required directed-effect execution needs runtime dependencies and attempt identity")
        self.directed_effect_runtime = directed_effect_runtime
        self.directed_effect_required = bool(directed_effect_required)
        self.directed_effect_execution_attempt = directed_effect_execution_attempt
        self.directed_effect_execution_attempt_authority = directed_effect_execution_attempt_authority
        self._capability_token = _normalize_capability_token(_mapping_value(capability_token))
        self._execution_envelope_hash = str(
            execution_envelope_hash or self._capability_token.get("execution_envelope_hash") or ""
        ).strip()
        deferred_request_fence = DeferredRequestReplayFence()
        self._deferred_repair_synthesizer = DeferredRepairEffectSynthesizer(_replay_fence=deferred_request_fence)
        self._deferred_command_synthesizer = DeferredCommandEffectSynthesizer(_replay_fence=deferred_request_fence)
        # FIX-20250422: Track files already read in this session to block redundant reads
        self._session_read_files: set[str] = set()

    def _raise_contract_violation(
        self,
        *,
        turn_id: str,
        error_type: str,
        message: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> NoReturn:
        """Emit structured error telemetry then raise contract violation."""
        self.emit_event(
            ErrorEvent(
                turn_id=turn_id,
                error_type=error_type,
                message=message,
                state_at_error="TOOL_BATCH_VALIDATION",
            )
        )
        if metadata:
            logger.warning(
                "contract_violation_event: turn_id=%s error_type=%s metadata=%s",
                turn_id,
                error_type,
                dict(metadata),
            )
        raise RuntimeError(message)

    def _check_effect_policy(
        self,
        invocations: list[Any],
        turn_id: str,
    ) -> None:
        """Check tool invocations against the cell's effect policy.

        In 'warn' mode: log violations but allow execution.
        In 'strict' mode: raise EffectPolicyViolationError on first violation.
        In 'off' mode: skip entirely.
        """
        mode = get_effect_policy_mode()
        if mode == "off" or self._effect_policy is None:
            return

        for invocation in invocations:
            tool_name = str(
                invocation.get("tool_name", "")
                if isinstance(invocation, dict)
                else getattr(invocation, "tool_name", "")
            )
            effect_type = str(
                invocation.get("effect_type", "")
                if isinstance(invocation, dict)
                else getattr(invocation, "effect_type", "")
            )
            arguments = (
                dict(invocation.get("arguments", {}))
                if isinstance(invocation, dict) and isinstance(invocation.get("arguments"), dict)
                else {}
            )

            if not effect_type:
                continue

            verdict = self._effect_policy.check_tool_invocation(
                tool_name=tool_name,
                effect_type=effect_type,
                arguments=arguments,
            )

            if not verdict.allowed:
                if mode == "strict":
                    raise EffectPolicyViolationError(verdict)
                # warn mode: log and continue
                logger.warning(
                    "effect_policy_violation: turn_id=%s tool=%s effect=%s reason=%s",
                    turn_id,
                    tool_name,
                    effect_type,
                    verdict.reason,
                )

    @staticmethod
    def _canonicalize_directed_effect_invocations(
        invocations: list[Any],
    ) -> list[ToolInvocation]:
        """Classify every final invocation once from its captured raw tool name."""

        canonical: list[ToolInvocation] = []
        for invocation in invocations:
            if isinstance(invocation, ToolInvocation):
                classification = invocation.classification
                if (
                    classification is None
                    or invocation.effect_type is not classification.effect_type
                    or invocation.execution_mode is not classification.execution_mode
                    or invocation.tool_name != classification.canonical_tool_name
                ):
                    raise RuntimeError("directed_effect_batch_classification_failed")
                try:
                    captured = ToolInvocation._from_captured_classification(
                        call_id=invocation.call_id,
                        raw_tool_name=str(invocation.raw_tool_name),
                        arguments=dict(invocation.arguments),
                        classification=classification,
                        native_arguments=(
                            dict(invocation.native_arguments) if invocation.native_arguments is not None else None
                        ),
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("directed_effect_batch_classification_failed") from exc
                canonical.append(captured.model_copy(deep=True))
                continue
            raw_tool_name = str(invocation.get("raw_tool_name") or invocation.get("tool_name") or "")
            arguments = invocation.get("arguments", {})
            if not isinstance(arguments, dict):
                raise RuntimeError("directed_effect_invalid_tool_arguments")
            canonical.append(
                ToolInvocation.model_validate(
                    {
                        "call_id": str(invocation.get("call_id") or ""),
                        "raw_tool_name": raw_tool_name,
                        "tool_name": raw_tool_name,
                        "arguments": dict(arguments),
                        "native_arguments": invocation.get("native_arguments"),
                    }
                )
            )
        return canonical

    async def _prepare_directed_effect_dispatch(
        self,
        *,
        invocations: list[Any],
        workspace: str,
        turn_id: str,
        batch_id: str,
        preserve_same_path_inventory: bool = False,
        expected_prior_target_hashes: Mapping[str, str] | None = None,
    ) -> tuple[list[ToolInvocation], _PreparedDirectedEffectDispatchV1 | None]:
        """Authorize and seal every mutation before any member of the batch executes."""

        try:
            canonical = self._canonicalize_directed_effect_invocations(invocations)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("directed_effect_batch_classification_failed") from exc
        mutations = [invocation for invocation in canonical if invocation.effect_type is not ToolEffectType.READ]
        if not mutations:
            return canonical, None
        # Ordinary model batches use last-write-wins so repeated writes to one
        # target do not invalidate later DEO claim baselines. Deferred repair
        # followups are different: their sealed inventory intentionally contains
        # a forward mutation plus a same-path rollback contingency. Collapsing
        # that pair drops the forward member, then the explicit forward/rollback
        # partition cannot cover the prepared inventory and settlement fails.
        # Preserve the exact paired inventory only for that typed followup path.
        path_superseded_drops: list[tuple[str, str, str]] = []
        if not preserve_same_path_inventory:
            mutations, path_superseded_drops = _collapse_last_write_wins_mutations(mutations)
        pending_cohorts: tuple[tuple[ToolInvocation, ...], ...] = ()
        if self.directed_effect_required and not preserve_same_path_inventory:
            cohorts = _serial_mutation_cohorts(mutations)
            if len(cohorts) > 1:
                mutations = list(cohorts[0])
                pending_cohorts = cohorts[1:]
        if not mutations:
            # All mutations were pathless-empty or collapsed away; fail closed.
            first_error = path_superseded_drops[0][2] if path_superseded_drops else "directed_effect_policy_denied"
            raise RuntimeError(first_error)
        if not self.directed_effect_required:
            return canonical, None
        runtime = self.directed_effect_runtime
        execution_attempt = self.directed_effect_execution_attempt
        execution_authority = self.directed_effect_execution_attempt_authority
        if runtime is None or execution_attempt is None or execution_authority is None:
            raise RuntimeError("directed_effect_runtime_authority_unavailable")
        if str(getattr(self.config, "mutation_guard_mode", "")) != "strict":
            raise RuntimeError("directed_effect_mutation_guard_not_strict")
        normalized_turn_id = str(turn_id).strip()
        normalized_batch_id = str(batch_id).strip()
        if not normalized_turn_id or not normalized_batch_id:
            raise RuntimeError("directed_effect_batch_identity_unavailable")
        guard_factory = getattr(self.tool_runtime, "directed_effect_policy_guard", None)
        if not callable(guard_factory):
            raise RuntimeError("directed_effect_gateway_authority_unavailable")
        try:
            guard = guard_factory(runtime.policy_snapshot_port)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError("directed_effect_gateway_authority_unavailable") from exc

        candidates: list[DirectedEffectLifecycleCandidateV1] = []
        restrictions: list[tuple[str, DirectedEffectImmutableItemsV1]] = []
        dropped_members: list[tuple[str, str, str]] = list(path_superseded_drops)
        # R140: one malformed/out-of-scope mutation must not abort authorized
        # siblings (e.g. edit_file with path + valid blocks next to search/replace
        # missing file). Soft-deny members and renumber inventory ordinals.
        for invocation in mutations:
            try:
                verdict = await guard.evaluate_authoritative(
                    DirectedEffectAuthoritativePolicyGuardRequestV1(
                        invocation=invocation,
                        workspace=workspace,
                        inventory_ordinal=len(candidates),
                        execution_attempt=execution_attempt,
                        turn_id=normalized_turn_id,
                        batch_id=normalized_batch_id,
                    )
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                raise RuntimeError("directed_effect_policy_guard_failed") from exc
            if (
                type(verdict) is not DirectedEffectPolicyGuardResultV1
                or verdict.status != "authorized"
                or verdict.preflight is None
                or verdict.snapshot is None
                or verdict.authorization_binding is None
                or verdict.current_job_token_restriction_evidence is None
            ):
                error_code = str(getattr(verdict, "error_code", None) or "directed_effect_policy_denied")
                if error_code in {
                    "deo_job_token_invalid",
                    "deo_authorization_hash_drift",
                    "deo_context_replayed",
                }:
                    raise RuntimeError(error_code)
                dropped_members.append(
                    (
                        str(invocation.call_id),
                        str(invocation.tool_name or invocation.raw_tool_name or "unknown_tool"),
                        error_code,
                    )
                )
                continue
            target = verdict.snapshot.baseline_target_state_evidence
            expected_hash = (expected_prior_target_hashes or {}).get(target.target_path)
            if expected_hash is not None and target.before_content_hash != expected_hash:
                # The prior result is backed by a committed TaskRuntime receipt.
                # Fresh policy evidence must still observe its exact postimage.
                self._validate_directed_effect_feedback_attempt(workspace)
                raise _RecoverableDirectedEffectDenialError(
                    ((invocation.call_id, invocation.tool_name, "deo_dependent_preimage_mismatch"),),
                    tuple(canonical),
                )
            candidates.append(
                DirectedEffectLifecycleCandidateV1(
                    preflight=verdict.preflight,
                    snapshot=verdict.snapshot,
                    authorization_binding=verdict.authorization_binding,
                )
            )
            restrictions.append(
                (
                    str(invocation.call_id),
                    verdict.current_job_token_restriction_evidence,
                )
            )

        if not candidates:
            first_error = dropped_members[0][2] if dropped_members else "directed_effect_policy_denied"
            denied_members = [row for row in dropped_members if row[2] != "deo_same_path_superseded_by_later_write"]
            fatal_members = [row for row in denied_members if row[2] not in _RECOVERABLE_DEO_ADMISSION_CODES]
            if fatal_members:
                raise RuntimeError(fatal_members[0][2])
            if denied_members and all(row[2] in _RECOVERABLE_DEO_ADMISSION_CODES for row in denied_members):
                self._validate_directed_effect_feedback_attempt(workspace)
                raise _RecoverableDirectedEffectDenialError(tuple(dropped_members), tuple(canonical))
            raise RuntimeError(first_error)

        # Resolve via package module so tests that monkeypatch
        # tool_batch_executor.DirectedEffectLifecycleService keep working.
        import polaris.cells.roles.kernel.internal.transaction.tool_batch_executor as _tbe_pkg

        lifecycle = _tbe_pkg.DirectedEffectLifecycleService(
            policy_snapshot_port=runtime.policy_snapshot_port,
        )
        prepared = None
        for prepare_attempt in range(_DEO_PREPARE_LOCK_RETRY_ATTEMPTS):
            prepared = lifecycle.prepare_batch(
                execution_attempt=execution_attempt,
                execution_attempt_authority=execution_authority,
                turn_id=normalized_turn_id,
                batch_id=normalized_batch_id,
                candidates=tuple(candidates),
            )
            if prepared.status == "ready" and prepared.prepared_batch is not None:
                break
            if prepare_attempt + 1 >= _DEO_PREPARE_LOCK_RETRY_ATTEMPTS:
                break
            if not _is_transient_deo_prepare_lock_failure(prepared):
                break
            # Yield the event loop so concurrent FactStream holders (settlement,
            # heartbeats, factory cutoff) can release advisory locks.
            delay = _DEO_PREPARE_LOCK_RETRY_BASE_SECONDS * (2**prepare_attempt)
            logger.warning(
                "DEO prepare_batch transient fact-stream lock failure; retrying "
                "attempt=%s/%s delay=%.3fs error=%s upstream=%s turn_id=%s batch_id=%s",
                prepare_attempt + 1,
                _DEO_PREPARE_LOCK_RETRY_ATTEMPTS,
                delay,
                prepared.error_code,
                _deo_prepare_upstream_code(prepared),
                normalized_turn_id,
                normalized_batch_id,
            )
            await asyncio.sleep(delay)
        if prepared is None or prepared.status != "ready" or prepared.prepared_batch is None:
            # Preserve upstream TaskRuntime code (e.g. lease_version_mismatch)
            # so control-plane receipts are not opaque deo_* shells.
            code = str(
                (prepared.error_code if prepared is not None else None) or "directed_effect_batch_prepare_denied"
            )
            upstream = _deo_prepare_upstream_code(prepared) if prepared is not None else ""
            raise RuntimeError(f"{code}:{upstream}" if upstream else code)
        return canonical, _PreparedDirectedEffectDispatchV1(
            batch=prepared.prepared_batch,
            restrictions_by_call_id=tuple(restrictions),
            dropped_members=tuple(dropped_members),
            pending_cohorts=pending_cohorts,
        )

    @staticmethod
    def _committed_cohort_targets(
        prepared: _PreparedDirectedEffectDispatchV1,
        receipts: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        """Require owner-persisted commits before admitting dependent mutations."""
        if any(code != "deo_same_path_superseded_by_later_write" for _, _, code in prepared.dropped_members):
            return None
        rows = {
            str(row.get("call_id") or ""): row
            for receipt in normalize_batch_receipts(receipts)
            for row in (receipt.get("raw_results") or receipt.get("results") or [])
            if isinstance(row, dict)
        }
        targets: dict[str, str] = {}
        batch = prepared.batch
        for prepared_member in batch.prepared_members:
            member = prepared_member.member
            row = rows.get(member.tool_call_id, {})
            value = row.get("result")
            if row.get("status") != "success" or not isinstance(value, dict):
                return None
            commit = row.get("effect_receipt_commit") or value.get("effect_receipt_commit")
            receipt = row.get("effect_receipt") or value.get("effect_receipt")
            if (
                not isinstance(commit, dict)
                or commit.get("state") != "RECEIPT_COMMITTED"
                or not isinstance(receipt, dict)
                or receipt.get("tool_call_id") != member.tool_call_id
            ):
                return None
            physical_value = {
                key: item for key, item in value.items() if key not in {"effect_receipt", "effect_receipt_commit"}
            }
            receipt_value = {key: item for key, item in receipt.items() if key not in {"receipt_hash", "receipt_id"}}
            if hash_directed_effect_arguments(_immutable_arguments(physical_value)) != receipt.get(
                "physical_result_hash"
            ) or hash_directed_effect_arguments(_immutable_arguments(receipt_value)) != receipt.get("receipt_hash"):
                return None
            observed = get_directed_effect_operation(
                GetDirectedEffectOperationQueryV1(
                    workspace=batch.execution_attempt.workspace,
                    task_id=batch.execution_attempt.task_id,
                    execution_attempt=batch.execution_attempt,
                    parent_binding=batch.parent_binding,
                    tool_call_id=member.tool_call_id,
                    effect_id=member.effect_id,
                )
            )
            snapshot = observed.snapshot
            if (
                not observed.ok
                or snapshot is None
                or str(getattr(snapshot.state, "value", snapshot.state))
                not in {"RECEIPT_COMMITTED", "CLOSED_BY_PARENT"}
                or commit.get("receipt_hash") != receipt.get("receipt_hash")
                or commit.get("receipt_ref") != receipt.get("receipt_id")
                or commit.get("receipt_binding_hash") != member.expected_receipt_binding_hash
                or receipt.get("receipt_binding_hash") != member.expected_receipt_binding_hash
                or receipt.get("operation_id") != snapshot.operation.operation_id
                or receipt.get("receipt_outcome") != "succeeded"
                or commit.get("receipt_outcome") != "succeeded"
            ):
                return None
            bound = prepared_member.policy_binding.bound_snapshot
            if bound is None:
                return None
            target = bound.snapshot.baseline_target_state_evidence
            if not target.is_no_file_state:
                after = value.get("after_sha256")
                if not isinstance(after, str) or len(after) != 64 or any(c not in "0123456789abcdef" for c in after):
                    return None
                targets[target.target_path] = after
        return targets

    async def _execute_dependent_effect_cohorts(
        self,
        *,
        prepared: _PreparedDirectedEffectDispatchV1,
        first_receipts: list[dict[str, Any]],
        workspace: str,
        turn_id: str,
        batch_id: str,
        cancel_token: CancelToken | None,
        allowed_tool_names: set[str] | None,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]], str | None]:
        """Use existing admission/dispatch owners for dependent serial cohorts.

        No new state is stored here. TaskRuntime parent admission closes only a
        receipt-complete predecessor; repeated/cold admission remains fail-closed.
        """
        receipts: list[dict[str, Any]] = []
        dropped: list[tuple[str, str, str]] = []
        previous = prepared
        previous_receipts = first_receipts
        expected_targets: dict[str, str] = {}
        error: str | None = None
        for ordinal, cohort in enumerate(prepared.pending_cohorts, 1):
            phase = "predecessor"
            current = None
            try:
                committed = self._committed_cohort_targets(previous, previous_receipts)
                if committed is None:
                    raise RuntimeError("deo_dependent_predecessor_not_committed")
                expected_targets.update(committed)
                if cancel_token is not None and cancel_token.cancelled:
                    raise RuntimeError("deo_dependent_batch_cancelled")
                cohort_id = f"{batch_id}:jit:{ordinal}"
                phase = "admission"
                canonical, current = await self._prepare_directed_effect_dispatch(
                    invocations=list(cohort),
                    workspace=workspace,
                    turn_id=turn_id,
                    batch_id=cohort_id,
                    expected_prior_target_hashes=expected_targets,
                )
                if current is None or current.pending_cohorts:
                    raise RuntimeError("deo_dependent_cohort_not_prepared")
                denied = {row[0] for row in current.dropped_members}
                dispatch = [item for item in canonical if item.call_id not in denied]
                batch = ToolBatch(
                    batch_id=cohort_id,
                    invocations=dispatch,
                    serial_writes=[item for item in dispatch if item.execution_mode is ToolExecutionMode.WRITE_SERIAL],
                    async_receipts=[
                        item for item in dispatch if item.execution_mode is ToolExecutionMode.ASYNC_RECEIPT
                    ],
                )
                phase = "dispatch"
                result = await self._build_tool_batch_runtime(
                    workspace, turn_id=turn_id, cancel_token=cancel_token, prepared_directed_effect=current
                ).execute_batch(batch, TurnId(turn_id), allowed_tool_names=allowed_tool_names)
                previous_receipts = normalize_batch_receipts(result)
                receipts.extend(previous_receipts)
                dropped.extend(current.dropped_members)
                previous = current
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                error = str(exc) or "deo_dependent_dispatch_failed"
                if isinstance(exc, _RecoverableDirectedEffectDenialError):
                    phase = "predecessor"
                # A failure after admission may have claimed or changed bytes.
                # Preserve UNKNOWN for this cohort; never invent failedNoEffect.
                if phase != "predecessor":
                    uncertain = [
                        {
                            "call_id": item.call_id,
                            "tool_name": item.tool_name,
                            "status": "error",
                            "result": {"error": error},
                            "error": error,
                            "directed_effect_claim_status": "unknown",
                            "directed_effect_mutation_status": "unknown",
                        }
                        for item in cohort
                    ]
                    receipts.append(
                        {
                            "batch_id": f"{batch_id}:jit:{ordinal}",
                            "turn_id": turn_id,
                            "results": uncertain,
                            "raw_results": uncertain,
                            "success_count": 0,
                            "failure_count": len(uncertain),
                        }
                    )
                remaining_start = ordinal - 1 if phase == "predecessor" else ordinal
                for remaining in prepared.pending_cohorts[remaining_start:]:
                    dropped.extend((item.call_id, item.tool_name, error) for item in remaining)
                break
        return receipts, dropped, error

    def _validate_directed_effect_feedback_attempt(self, workspace: str) -> None:
        """Require the same live owner before continuing a no-effect denial.

        A process-local snapshot is only a capability view. Persisted
        TaskRuntime facts still decide whether this attempt may continue.
        Lease renewal can update its expiry without changing its identity.
        """
        authority = self.directed_effect_execution_attempt_authority
        expected = self.directed_effect_execution_attempt
        if authority is None or expected is None:
            raise RuntimeError("deo_execution_attempt_invalid")
        try:
            snapshot = authority.snapshot(lock_timeout_seconds=5.0)
            if (
                type(snapshot) is not TaskRuntimeExecutionAttemptAuthoritySnapshotV1
                or not snapshot.success
                or snapshot.closed
                or type(snapshot.identity) is not TaskRuntimeExecutionAttemptIdentityV1
            ):
                raise ValueError("execution attempt authority is unavailable")
            identity = snapshot.identity
            if identity is None:
                raise ValueError("execution attempt identity is unavailable")
            fields = (
                "workspace",
                "task_id",
                "external_task_id",
                "session_id",
                "attempt",
                "role_id",
                "worker_id",
                "run_id",
            )
            if any(getattr(identity, key) != getattr(expected, key) for key in fields):
                raise ValueError("execution attempt identity drift")
            if (
                workspace != identity.workspace
                or str(self.config.role_id) != identity.role_id
                or (self.config.task_id and str(self.config.task_id) != identity.external_task_id)
                or (self.config.run_id and str(self.config.run_id) != identity.run_id)
            ):
                raise ValueError("execution frame identity drift")
            verdict = validate_task_runtime_execution_attempt(
                ValidateTaskRuntimeExecutionAttemptQueryV1(workspace=workspace, identity=identity)
            )
            if (
                type(verdict) is not TaskRuntimeExecutionAttemptValidationVerdictV1
                or not verdict.valid
                or verdict.identity != identity
                or verdict.workspace != workspace
            ):
                raise ValueError("persisted execution attempt validation failed")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError("deo_execution_attempt_invalid") from exc

    def _build_tool_batch_runtime(
        self,
        workspace: str = ".",
        *,
        batch_idempotency_key: str = "",
        side_effect_class: str = "readonly",
        turn_id: str = "",
        cancel_token: CancelToken | None = None,
        prepared_directed_effect: _PreparedDirectedEffectDispatchV1 | None = None,
        directed_effect_dispatch_call_ids: tuple[str, ...] | None = None,
        directed_effect_abort_call_ids: tuple[str, ...] = (),
        directed_effect_repair_bindings_by_call_id: tuple[tuple[str, DeferredDirectorRepairEffectBindingV1], ...] = (),
        directed_effect_rollback_activation_by_call_id: tuple[tuple[str, str], ...] = (),
        directed_effect_invocation_observer: Callable[[ToolInvocation], None] | None = None,
    ) -> ToolBatchRuntime:
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
                turn_id=turn_id,
                cancel_token=cancel_token,
                batch_idempotency_key=batch_idempotency_key,
                side_effect_class=side_effect_class,
            ),
            directed_effect_runtime=self.directed_effect_runtime,
            directed_effect_required=self.directed_effect_required,
            directed_effect_execution_attempt=self.directed_effect_execution_attempt,
            directed_effect_execution_attempt_authority=self.directed_effect_execution_attempt_authority,
            prepared_directed_effect_batch=(
                prepared_directed_effect.batch if prepared_directed_effect is not None else None
            ),
            directed_effect_restrictions_by_call_id=(
                prepared_directed_effect.restrictions_by_call_id if prepared_directed_effect is not None else ()
            ),
            directed_effect_dispatch_call_ids=directed_effect_dispatch_call_ids,
            directed_effect_abort_call_ids=directed_effect_abort_call_ids,
            directed_effect_repair_bindings_by_call_id=directed_effect_repair_bindings_by_call_id,
            directed_effect_rollback_activation_by_call_id=(directed_effect_rollback_activation_by_call_id),
            directed_effect_invocation_observer=directed_effect_invocation_observer,
        )

    async def _reset_tool_runtime_turn_boundary(self, turn_id: str) -> None:
        """Explicitly notify the tool runtime of turn boundaries."""
        reset_hook = getattr(self.tool_runtime, "reset_turn_boundary", None)
        if not callable(reset_hook):
            return
        try:
            result = reset_hook(turn_id)
            if asyncio.iscoroutine(result):
                await result
        except (RuntimeError, TypeError, ValueError):
            # TODO: narrow exception type — reset_hook is an external callback
            logger.warning("tool-runtime turn boundary reset failed: turn_id=%s", turn_id, exc_info=True)

    def _check_idempotency(self, batch_idempotency_key: str) -> dict | None:
        """Check if a tool batch has already been executed.

        Returns the cached receipt if found, None otherwise.
        Queries ReceiptStore via the tool_runtime's receipt_store if available.
        """
        if not batch_idempotency_key:
            return None
        # Phase 1.5: Query ReceiptStore for actual idempotency
        try:
            receipt_store = getattr(self.tool_runtime, "receipt_store", None)
            if receipt_store is not None and hasattr(receipt_store, "get_by_batch_idempotency_key"):
                # Defensive: avoid creating un-awaited coroutines from mocks
                import inspect

                getter = receipt_store.get_by_batch_idempotency_key
                if inspect.iscoroutinefunction(getter):
                    return None
                cached = getter(batch_idempotency_key)
                # Defensive: ensure we only return a concrete dict, never a coroutine/mock
                if isinstance(cached, dict):
                    return cached
        except (AttributeError, RuntimeError, TypeError):
            pass
        return None

    @staticmethod
    def _canonical_result_payload(payload: Any) -> str:
        """把工具结果投影成可稳定比较的 canonical 串.

        剥离已知的易变字段（耗时、时间戳、receipt id 等），只比较语义内容，
        避免审计把"同内容不同计时"误判为 wrong_adoption.
        """
        volatile = {
            "execution_time_ms",
            "duration_ms",
            "elapsed_ms",
            "timestamp",
            "ts",
            "receipt_id",
            "effect_receipt",
            "started_at",
            "finished_at",
        }

        def _strip(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {k: _strip(v) for k, v in sorted(value.items()) if k not in volatile}
            if isinstance(value, (list, tuple)):
                return [_strip(item) for item in value]
            return value

        import json

        return json.dumps(_strip(payload), ensure_ascii=False, sort_keys=True, default=str)

    async def _audit_adopted_result(
        self,
        *,
        invocation: Any,
        speculative_payload: Any,
        workspace: str,
        turn_id: str,
        tool_name: str,
        shadow_engine: Any,
    ) -> Any:
        """重算权威结果并与投机结果比对（ADR-0077 不变量 A 的实运行验证）.

        一致则沿用投机结果；不一致则记 ``wrong_adoption`` 并改用权威结果。
        审计本身的任何失败都安全降级为"沿用投机结果"，绝不影响主流程正确性
        （审计是只读叠加，不得改变不开启审计时的行为）。
        """
        try:
            mode = invocation.get("execution_mode")
            audit_batch = ToolBatch(
                batch_id=BatchId(f"{turn_id}_audit"),
                parallel_readonly=[invocation] if mode == ToolExecutionMode.READONLY_PARALLEL else [],
                readonly_serial=[invocation] if mode == ToolExecutionMode.READONLY_SERIAL else [],
                serial_writes=[],
                async_receipts=[],
            )
            if not (audit_batch.parallel_readonly or audit_batch.readonly_serial):
                # 仅审计只读领养；其余安全跳过。
                return speculative_payload
            receipts = await self._build_tool_batch_runtime(
                workspace,
                turn_id=turn_id,
                cancel_token=CancelToken(),
            ).execute_batch(audit_batch, TurnId(turn_id))
            normalized = normalize_batch_receipts(receipts)
            auth_payload: Any = None
            for entry in normalized:
                results = entry.get("results") if isinstance(entry, Mapping) else None
                if isinstance(results, list) and results:
                    first = results[0]
                    auth_payload = first.get("result") if isinstance(first, Mapping) else None
                    break
            if self._canonical_result_payload(speculative_payload) != self._canonical_result_payload(auth_payload):
                metrics = getattr(shadow_engine, "metrics", None)
                if metrics is not None and hasattr(metrics, "record_wrong_adoption"):
                    metrics.record_wrong_adoption(reason=f"{tool_name}:adopt_audit_mismatch")
                logger.warning(
                    "[tool_batch][audit] wrong adoption detected for %r (turn=%s); using authoritative result",
                    tool_name,
                    turn_id,
                )
                return auth_payload
            return speculative_payload
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TypeError, ValueError, KeyError):
            logger.debug(
                "[tool_batch][audit] audit failed for %r; keeping speculative result", tool_name, exc_info=True
            )
            return speculative_payload

    async def _execute_deferred_repair_followup(
        self,
        *,
        receipts_as_dicts: list[dict[str, Any]],
        primary_batch_id: str,
        workspace: str,
        turn_id: str,
        ledger: TurnLedger,
        cancel_token: CancelToken,
    ) -> list[dict[str, Any]]:
        """Execute at most one visible, non-recursive deferred repair batch."""

        execution_attempt = self.directed_effect_execution_attempt
        followup = deepcopy(
            build_deferred_repair_followup(
                receipts_as_dicts,
                primary_batch_id=primary_batch_id,
                turn_id=turn_id,
                expected_workspace=workspace,
                expected_task_id=(execution_attempt.external_task_id if execution_attempt is not None else ""),
                expected_execution_attempt=execution_attempt,
                synthesizer=self._deferred_repair_synthesizer,
                command_synthesizer=getattr(self, "_deferred_command_synthesizer", None),
            )
        )
        if followup is None:
            return []
        dispatch_wire = followup.dispatch_batch.model_dump(mode="json")
        error = tool_batch_inventory_error(dispatch_wire, require_execution_groups=True)
        if error is not None:
            raise RuntimeError(error)
        # The sealed repair inventory also contains inactive rollback members.
        # Compare only the existing explicit forward selection, not a caller-
        # supplied exception to complete ToolBatch inventory binding.
        forward_wire = dict(dispatch_wire)
        forward_wire["invocations"] = [
            invocation.model_dump(mode="json")
            for invocation in followup.inventory_invocations
            if str(invocation.call_id) in followup.forward_call_ids
        ]
        error = tool_batch_inventory_error(forward_wire, require_execution_groups=True)
        if error is not None:
            raise RuntimeError(error)
        try:
            inventory_invocations, followup_prepared = await self._prepare_directed_effect_dispatch(
                invocations=list(followup.inventory_invocations),
                workspace=workspace,
                turn_id=turn_id,
                batch_id=followup.batch_id,
                preserve_same_path_inventory=True,
            )
        except RuntimeError as deo_exc:
            error_token = str(deo_exc)
            if _is_deo_abort_error(error_token):
                _seal_deo_abort_tool_lifecycle(
                    workspace=workspace,
                    run_id=str(execution_attempt.run_id if execution_attempt is not None else ""),
                    task_id=str(
                        getattr(self.directed_effect_execution_attempt, "external_task_id", "")
                        if self.directed_effect_execution_attempt is not None
                        else ""
                    ),
                    turn_id=turn_id,
                    role_id=str(getattr(self.config, "role_id", "") or ""),
                    invocations=list(followup.inventory_invocations),
                    metadata={},
                    ledger=ledger,
                    error_code=error_token,
                )
            raise
        if followup_prepared is None:
            raise RuntimeError("deo_deferred_repair_followup_not_prepared")
        prepared_call_ids = tuple(member.member.tool_call_id for member in followup_prepared.batch.prepared_members)
        prepared_call_id_set = set(prepared_call_ids)
        followup_call_id_set = {
            *followup.forward_call_ids,
            *followup.rollback_call_ids,
        }
        if not prepared_call_id_set.issubset(followup_call_id_set):
            raise RuntimeError("deo_deferred_repair_prepared_inventory_unknown_member")

        # R27: the authoritative policy guard may soft-deny one repair while
        # retaining an unrelated sibling.  The DEO parent then contains only
        # the authorized subset, so the original follow-up partition cannot be
        # handed to ToolBatchRuntime unchanged.  Project every execution
        # contract onto the exact prepared inventory.  A forward/rollback pair
        # remains atomic: admitting only one side is unsafe and fails closed.
        prepared_activation: list[tuple[str, str]] = []
        incomplete_pairs: list[tuple[str, str]] = []
        for rollback_call_id, forward_call_id in followup.rollback_activation_by_call_id:
            rollback_prepared = rollback_call_id in prepared_call_id_set
            forward_prepared = forward_call_id in prepared_call_id_set
            if rollback_prepared != forward_prepared:
                incomplete_pairs.append((rollback_call_id, forward_call_id))
            elif rollback_prepared:
                prepared_activation.append((rollback_call_id, forward_call_id))
        if incomplete_pairs:
            # Keep the exact policy/preparation split observable.  The public
            # lifecycle receipt intentionally carries only normalized dropped
            # invocation refs, so without this evidence a live failure looks
            # identical whether the forward member, rollback contingency, or
            # an unrelated sibling was denied.  Never log file content here.
            logger.warning(
                "DEO deferred-repair pair incomplete turn_id=%s batch_id=%s "
                "pairs=%s policy_dropped=%s prepared_call_ids=%s",
                turn_id,
                followup.batch_id,
                incomplete_pairs,
                followup_prepared.dropped_members,
                prepared_call_ids,
            )
            _seal_deo_abort_tool_lifecycle(
                workspace=workspace,
                run_id=str(execution_attempt.run_id if execution_attempt is not None else ""),
                task_id=str(
                    getattr(self.directed_effect_execution_attempt, "external_task_id", "")
                    if self.directed_effect_execution_attempt is not None
                    else ""
                ),
                turn_id=turn_id,
                role_id=str(getattr(self.config, "role_id", "") or ""),
                invocations=[
                    invocation
                    for invocation in inventory_invocations
                    if str(invocation.call_id) in prepared_call_id_set
                ],
                metadata={
                    "incomplete_pairs": [list(pair) for pair in incomplete_pairs],
                    "dropped_members": [list(row) for row in followup_prepared.dropped_members],
                },
                ledger=ledger,
                error_code="deo_deferred_repair_prepared_pair_incomplete",
            )
            raise RuntimeError("deo_deferred_repair_prepared_pair_incomplete")

        prepared_forward_call_ids = tuple(
            call_id for call_id in followup.forward_call_ids if call_id in prepared_call_id_set
        )
        prepared_rollback_call_ids = tuple(
            call_id for call_id in followup.rollback_call_ids if call_id in prepared_call_id_set
        )
        prepared_effect_bindings = tuple(
            (call_id, binding)
            for call_id, binding in followup.effect_bindings_by_call_id
            if call_id in prepared_call_id_set
        )
        prepared_invocations = [
            invocation for invocation in inventory_invocations if str(invocation.call_id) in prepared_call_id_set
        ]
        prepared_dispatch_invocations = [
            invocation
            for invocation in followup.dispatch_batch.serial_writes
            if str(invocation.call_id) in prepared_call_id_set
        ]
        prepared_dispatch_batch = ToolBatch(
            batch_id=followup.dispatch_batch.batch_id,
            invocations=list(prepared_dispatch_invocations),
            parallel_readonly=[],
            readonly_serial=[],
            serial_writes=list(prepared_dispatch_invocations),
            async_receipts=[],
        )

        # Prepared rollback contingencies are not selected tool invocations.
        # Keep the selected forwards before dispatch and capture a rollback
        # only when the runtime selects it from the sealed effect binding.
        ledger.record_invocation_batch(followup.batch_id, prepared_dispatch_invocations)
        captured_forward_ids = {str(item.call_id) for item in prepared_dispatch_invocations}
        inventory_by_call = {str(item.call_id): item for item in inventory_invocations}

        def record_selected_invocation(invocation: ToolInvocation) -> None:
            call_id = str(invocation.call_id)
            expected = inventory_by_call.get(call_id)
            if (
                expected is None
                or expected.tool_name != invocation.tool_name
                or expected.arguments != invocation.arguments
            ):
                raise RuntimeError("committed_tool_receipt_invocation_mismatch")
            if call_id not in captured_forward_ids:
                ledger.record_invocation_batch(followup.batch_id, [expected])
                captured_forward_ids.add(call_id)

        self._check_effect_policy(prepared_invocations, turn_id)
        ledger.tool_batch_count += 1
        ledger.state_history.append(("DEFERRED_REPAIR_FOLLOWUP_SCHEDULED", int(time.time() * 1000)))
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "tool_batch_started",
                {
                    "event_kind": "deferred_repair_followup_scheduled",
                    "batch_id": followup.batch_id,
                    "forward_count": len(prepared_forward_call_ids),
                    "rollback_contingency_count": len(prepared_rollback_call_ids),
                    "policy_dropped_count": len(followup_prepared.dropped_members),
                    "request_ids": list(followup.request_ids),
                    "visible_tool_batch_count": ledger.tool_batch_count,
                },
            )
        )
        followup_receipts = await self._build_tool_batch_runtime(
            workspace,
            turn_id=turn_id,
            cancel_token=cancel_token,
            prepared_directed_effect=followup_prepared,
            directed_effect_dispatch_call_ids=prepared_forward_call_ids,
            directed_effect_abort_call_ids=prepared_rollback_call_ids,
            directed_effect_repair_bindings_by_call_id=prepared_effect_bindings,
            directed_effect_rollback_activation_by_call_id=tuple(prepared_activation),
            directed_effect_invocation_observer=record_selected_invocation,
        ).execute_batch(
            prepared_dispatch_batch,
            TurnId(turn_id),
        )
        normalized_followup_receipts = normalize_batch_receipts(followup_receipts)
        for receipt in normalized_followup_receipts:
            receipt["deferred_repair_followup_batch_id"] = followup.batch_id
            receipt["deferred_repair_request_ids"] = list(followup.request_ids)
        return normalized_followup_receipts

    def _check_materialize_contract(self, ledger: TurnLedger, invocations: list[Any]) -> bool:
        """Primary check: delivery contract 已明确要求 materialize。"""
        if ledger.delivery_contract.mode != DeliveryMode.MATERIALIZE_CHANGES:
            return False
        if ledger.mutation_obligation.mutation_satisfied:
            return False
        if tool_batch_has_authoritative_write_invocation(invocations):
            return False

        # FIX-20250422: Phase timeout 熔断 —— 如果已经超时，允许 LLM_ONCE 收口
        # 不再返回 continue_multi_turn，让 LLM 输出 final_answer 或错误
        if ledger.mutation_obligation.blocked_reason == BlockedReason.PHASE_TIMEOUT:
            logger.warning(
                "phase-timeout-allow-finalization: MATERIALIZE_CHANGES mode phase timeout detected. "
                "Allowing LLM_ONCE to prevent infinite loop. turn_id=%s",
                ledger.turn_id,
            )
            return False

        logger.debug(
            "mutation-bypass-skip-finalization: MATERIALIZE_CHANGES mode, no write tools yet. "
            "Blocking LLM_ONCE (would close tool channel) and returning continue_multi_turn. turn_id=%s",
            ledger.turn_id,
        )
        ledger.mutation_obligation.mark_blocked(
            BlockedReason.NO_WRITE_TOOL_AVAILABLE,
            detail="MATERIALIZE_CHANGES requires write tool invocation, but none were present. "
            "Skipping LLM_ONCE finalization to keep tool channel open for next turn.",
        )
        return True

    def _check_intent_mismatch(self, ledger: TurnLedger, invocations: list[Any], latest_user_request: str) -> bool:
        """Secondary guard: intent 检测到 mutation 但 delivery contract 不是 MATERIALIZE_CHANGES。"""
        if not latest_user_request:
            return False
        if not self.requires_mutation_intent(latest_user_request):
            return False
        if _is_no_write_structured_turn(self.config, ledger):
            logger.debug(
                "intent-mismatch-suppressed-no-write-structured: role=%s turn_id=%s",
                str(getattr(self.config, "role_id", "") or ""),
                ledger.turn_id,
            )
            return False
        if tool_batch_has_authoritative_write_invocation(invocations):
            return False
        if ledger.mutation_obligation.mutation_satisfied:
            return False
        # FIX-20250422-v2: Phase timeout 后必须允许 LLM_ONCE 收口，不能再 continue_multi_turn。
        # 根因：return True = "block finalization" = force continue_multi_turn，
        # 旧代码 return True 导致 phase timeout 后反而无限循环。
        # 修复：return False = "allow finalization" = LLM_ONCE proceeds → turn completes。
        if ledger.mutation_obligation.blocked_reason == BlockedReason.PHASE_TIMEOUT:
            logger.warning(
                "intent-mismatch-allow-finalization: phase timeout detected. "
                "Allowing LLM_ONCE finalization to break infinite loop. turn_id=%s",
                ledger.turn_id,
            )
            return False
        contract = ledger.delivery_contract
        # FIX-20250422-v2: 使用 PhaseManager 的 session 级阶段停留计数器，
        # 而非 per-turn 的 tool_batch_count。tool_batch_count 每 turn 重置为 0，
        # 导致 <= 2 的宽限期永远不会过期，LLM 可以无限探索。
        # PhaseManager._turns_in_current_phase 跨 turn 持久化（通过 _session_phase_manager），
        # 正确反映 session 级的阶段停留轮数。
        session_turns_in_phase = ledger.phase_manager._turns_in_current_phase
        if session_turns_in_phase <= 2:
            logger.debug(
                "intent-mismatch-allow-exploration: intent detected mutation but "
                "delivery contract mode=%s and session_turns_in_phase=%d <= 2. "
                "Allowing exploration before enforcing MATERIALIZE_CHANGES. turn_id=%s",
                contract.mode.value if hasattr(contract.mode, "value") else contract.mode,
                session_turns_in_phase,
                ledger.turn_id,
            )
            ledger.delivery_contract = replace(
                ledger.delivery_contract,
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
            return False
        logger.warning(
            "intent-mismatch-block: intent detected mutation but delivery contract "
            "mode=%s is not MATERIALIZE_CHANGES. turn_id=%s blocking LLM_ONCE.",
            contract.mode.value if hasattr(contract.mode, "value") else contract.mode,
            ledger.turn_id,
        )
        ledger.delivery_contract = replace(
            ledger.delivery_contract,
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )
        ledger.mutation_obligation.mark_blocked(
            BlockedReason.NO_WRITE_TOOL_AVAILABLE,
            detail="Intent classifier detected mutation requirement, but delivery contract was not "
            "MATERIALIZE_CHANGES and no write tools were invoked after multiple batches. "
            "Blocking LLM_ONCE to prevent inline patch escape.",
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_CONTRACT_INTENT_MISMATCH_BLOCK",
                "turn_id": ledger.turn_id,
                "reason": "intent_requires_mutation_but_contract_not_materialize",
                "user_request": latest_user_request,
            }
        )
        return True

    def _should_block_llm_once_finalization(
        self,
        ledger: TurnLedger,
        invocations: list[Any],
        latest_user_request: str = "",
    ) -> bool:
        """判定是否应阻止 LLM_ONCE 收口以防止贴代码逃逸。

        双层检查：
        1. Primary: delivery contract == MATERIALIZE_CHANGES
        2. Secondary: intent classifier 检测到 mutation 但 delivery contract 未升级
           （多轮对话中最新消息丢失原始 mutation 意图的场景）
        """
        return self._check_materialize_contract(ledger, invocations) or self._check_intent_mismatch(
            ledger, invocations, latest_user_request
        )

    def _build_mutation_bypass_result(
        self,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        receipts: list[dict],
        *,
        stream: bool = False,
    ) -> dict:
        """构建 Mutation Bypass 结果 —— 跳过 LLM_ONCE finalization，返回 continue_multi_turn。

        当 MATERIALIZE_CHANGES 模式下尚无写工具调用时，LLM_ONCE 会关闭工具通道
        并迫使 LLM 输出纯文本计划。返回 continue_multi_turn 让 Orchestrator 自动
        进入下一回合，保持工具通道开启，并在 continuation prompt 中提示 LLM 调用写工具。
        """
        turn_id = str(decision.get("turn_id", ""))
        # 避免重复状态转换（调用方可能已 transition 到 TOOL_BATCH_EXECUTED）
        if state_machine.current_state != TurnState.TOOL_BATCH_EXECUTED:
            state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTED)
        ledger.state_history.append(("TOOL_BATCH_EXECUTED", int(time.time() * 1000)))

        blocked_reason = ledger.mutation_obligation.blocked_reason
        blocked_detail = ledger.mutation_obligation.blocked_detail
        visible_msg = f"[MUTATION_CONTINUE] {blocked_reason.value if blocked_reason else 'unknown'}: {blocked_detail}"

        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "mutation_bypass_blocked",
                {
                    "reason": blocked_reason.value if blocked_reason else None,
                    "detail": blocked_detail,
                    "next_action": "continue_multi_turn",
                },
            )
        )

        # 对于流式模式，返回 continue_multi_turn 让 orchestrator 自动进入下一回合
        merged_batch_receipt = _merge_batch_receipts(receipts)
        if stream:
            return {
                "kind": "continue_multi_turn",
                "batch_receipt": merged_batch_receipt,
                "workflow_context": {
                    "turn_id": turn_id,
                    "reason": "mutation_skip_finalization",
                    "blocked_reason": blocked_reason.value if blocked_reason else None,
                    "blocked_detail": blocked_detail,
                    "delivery_mode": ledger.delivery_contract.mode.value,
                    "requires_mutation": True,
                    "next_step_hint": "Use write tools in the next turn to materialize changes.",
                },
            }

        # 非流式：标记 continue 并返回结果
        state_machine.transition_to(TurnState.COMPLETED)
        ledger.state_history.append(("COMPLETED", int(time.time() * 1000)))
        ledger.finalize()

        from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

        self.emit_event(
            CompletionEvent(
                turn_id=turn_id,
                status="success",
                duration_ms=ledger.get_duration_ms(),
                llm_calls=len(ledger.llm_calls),
                tool_calls=len(ledger.tool_executions),
            )
        )
        return {
            "turn_id": turn_id,
            "kind": "mutation_bypass_blocked",
            "visible_content": visible_msg,
            "decision": {
                "kind": decision.get("kind").value
                if hasattr(decision.get("kind"), "value")
                else str(decision.get("kind", "")),
                "finalize_mode": decision.get("finalize_mode").value
                if hasattr(decision.get("finalize_mode"), "value")
                else str(decision.get("finalize_mode", "")),
            },
            "metrics": {
                "duration_ms": ledger.get_duration_ms(),
                "llm_calls": len(ledger.llm_calls),
                "tool_calls": len(ledger.tool_executions),
            },
            "batch_receipt": merged_batch_receipt,
            "finalization": {
                "turn_id": turn_id,
                "mode": "blocked",
                "blocked_reason": blocked_reason.value if blocked_reason else None,
                "blocked_detail": blocked_detail,
                "needs_followup_workflow": True,
                "workflow_reason": "mutation_bypass_blocked",
            },
        }
