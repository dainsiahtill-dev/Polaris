"""ToolBatchExecutor.execute_tool_batch implementation mixin.

Private implementation module of the tool_batch_executor package.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    build_tool_batch_lifecycle_receipt_from_sources,
    build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt,
)
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.speculative_flags import is_adoption_audit_enabled
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    batch_write_failure_error_types,
    batch_write_failures_require_llm_replan,
    batch_write_results_all_failed_on_argument_shape,
    extract_allowed_scope_paths_from_message,
    extract_invocation_tool_name,
    extract_target_file_from_invocation_args,
    extract_target_files_from_message,
    filter_out_of_scope_write_invocations,
    filter_scope_paths_for_explicit_targets,
    receipts_have_stale_edit_failure,
    resolve_mutation_target_guard_violation,
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import build_workflow_handoff_context
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    extract_tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_latest_user_message,
    extract_platform_tool_contract_missing_target_files,
    extract_platform_tool_contract_scope_paths,
    extract_platform_tool_contract_target_files,
    platform_tool_contract_bypasses_read_write_barrier,
    platform_tool_contract_disables_phase_manager,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolExecutionMode,
    TurnDecision,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import TurnPhaseEvent
from polaris.kernelone.tools.tool_kinds import is_write_tool_name

from ._helpers import (
    _DIRECT_READ_TOOLS,
    WRITE_FILE_DUPLICATE_REJECTION_KEY,
    _append_tool_batch_receipts_to_run_ledger,
    _batch_has_authoritative_success,
    _batch_result_count,
    _capability_token_from_metadata,
    _effect_receipts_from_batch_receipts,
    _execution_envelope_hash_from_metadata,
    _is_deo_abort_error,
    _is_mutation_for_speculative_routing,
    _is_recoverable_deo_normalization_abort,
    _merge_batch_receipts,
    _normalize_file_reference_path,
    _recent_edit_failure_in_context,
    _resolve_existing_workspace_file,
    _resolve_tool_batch_execution_identity,
    _seal_deo_abort_tool_lifecycle,
    _tool_name_allowed_by_alias,
    annotate_autofilled_write_receipts,
    diff_write_file_autofill_evidence,
    fill_content_only_write_file_from_remaining_targets,
    fill_single_target_line_range_edit_blocks,
    logger,
    normalize_replay_execution_modes,
    rewrite_existing_file_paths_in_invocations,
    split_write_file_duplicate_content_rejections,
)


def _failed_batch_diagnostic_excerpt(receipts: list[dict[str, Any]]) -> str:
    """Preserve bounded actionable tool diagnostics across the turn boundary."""

    details: list[str] = []
    for receipt in normalize_batch_receipts(receipts):
        rows = receipt.get("raw_results")
        if not isinstance(rows, list):
            rows = receipt.get("results")
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            payloads = [row]
            result = row.get("result")
            if isinstance(result, Mapping):
                payloads.append(result)
            for payload in payloads:
                for key in ("error", "physical_error", "suggestion", "stderr", "stderr_tail"):
                    value = " ".join(str(payload.get(key) or "").split())
                    if value and value not in details:
                        details.append(value[:800])
            if sum(len(item) for item in details) >= 1200:
                break
        if sum(len(item) for item in details) >= 1200:
            break
    return " | ".join(details)[:1200]


def _project_directed_effect_dropped_member_receipt(
    *,
    dropped_member_rows: list[tuple[str, str, str]],
    batch_id: str,
    turn_id: str,
) -> dict[str, Any]:
    """Project DEO non-dispatch outcomes without conflating supersession and denial.

    Last-write-wins deliberately removes an earlier same-path mutation before
    physical dispatch.  That normalization outcome is fully accounted for, but
    is not a tool failure and must not poison the Run Ledger lifecycle.  Policy
    or guard denials remain ordinary fail-closed tool errors.
    """

    results: list[dict[str, Any]] = []
    success_count = 0
    failure_count = 0
    for call_id, tool_name, error_code in dropped_member_rows:
        reason = str(error_code or "directed_effect_policy_denied")
        if reason == "deo_same_path_superseded_by_later_write":
            results.append(
                {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "status": "success",
                    "result": {
                        "ok": True,
                        "no_op": True,
                        "superseded": True,
                        "reason": reason,
                    },
                    "error": None,
                    "effect_receipt": None,
                    "directed_effect_claim_status": "not_claimed",
                }
            )
            success_count += 1
            continue
        results.append(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "status": "error",
                "result": {
                    "ok": False,
                    "error": reason,
                    "error_type": "deo_member_soft_denied",
                },
                "error": reason,
                "effect_receipt": None,
                "directed_effect_claim_status": "not_claimed",
            }
        )
        failure_count += 1
    return {
        "batch_id": batch_id,
        "turn_id": turn_id,
        "results": results,
        "raw_results": [dict(item) for item in results],
        "success_count": success_count,
        "failure_count": failure_count,
        "pending_async_count": 0,
        "has_pending_async": False,
    }


class _ToolBatchExecuteMixin:
    """Mixin providing ToolBatchExecutor.execute_tool_batch."""

    async def execute_tool_batch(
        self: Any,
        decision: TurnDecision,
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        context: list[dict],
        *,
        stream: bool = False,
        shadow_engine: Any | None = None,
        allowed_tool_names: set[str] | None = None,
        enforce_mutation_write_guard: bool = True,
        count_towards_batch_limit: bool = True,
    ) -> dict:
        """执行工具批次。"""

        tool_batch = decision.get("tool_batch")
        if not tool_batch:
            raise ValueError("TOOL_BATCH decision missing tool_batch")

        turn_id = str(decision.get("turn_id", ""))
        batch_seq = ledger.tool_batch_count
        batch_idempotency_key = f"{turn_id}:{batch_seq}"

        # Phase 1: Idempotency check
        cached_receipt = self._check_idempotency(batch_idempotency_key)
        if cached_receipt is not None:
            logger.info("Idempotency hit for batch %s, returning cached receipt", batch_idempotency_key)
            return cached_receipt

        metadata = decision.get("metadata", {})
        workspace, execution_run_id, execution_task_id = _resolve_tool_batch_execution_identity(
            metadata,
            self.config,
        )
        raw_invocations = list(tool_batch.get("invocations", []) or [])
        invocations = rewrite_existing_file_paths_in_invocations(
            turn_id=turn_id,
            workspace=workspace,
            invocations=raw_invocations,
        )
        await self._reset_tool_runtime_turn_boundary(turn_id)
        if allowed_tool_names is not None:
            disallowed_tools = []
            for invocation in invocations:
                tname = extract_invocation_tool_name(invocation)
                if tname and not _tool_name_allowed_by_alias(tname, allowed_tool_names):
                    disallowed_tools.append(tname)
            if disallowed_tools:
                raise RuntimeError(
                    "single_batch_contract_violation: retry batch used tools outside narrowed set: "
                    + ", ".join(sorted(set(disallowed_tools)))
                )

        latest_user_request = extract_latest_user_message(context)
        single_target_candidates = extract_target_files_from_message(latest_user_request)
        single_scope_candidates = extract_allowed_scope_paths_from_message(latest_user_request)
        structured_target_candidates: list[str] = list(extract_platform_tool_contract_target_files(context))
        single_target_candidates.extend(structured_target_candidates)
        single_scope_candidates.extend(extract_platform_tool_contract_scope_paths(context))
        modification_contract = getattr(ledger, "modification_contract", None)
        if modification_contract is not None:
            contract_targets = getattr(modification_contract, "target_files", None)
            if isinstance(contract_targets, (list, tuple)):
                structured_target_candidates.extend(str(item) for item in contract_targets)
                single_target_candidates.extend(str(item) for item in contract_targets)
        single_target_candidates = list(dict.fromkeys(str(item) for item in single_target_candidates if str(item)))
        structured_target_candidates = list(
            dict.fromkeys(str(item) for item in structured_target_candidates if str(item))
        )
        single_scope_candidates = list(dict.fromkeys(str(item) for item in single_scope_candidates if str(item)))
        single_scope_candidates = filter_scope_paths_for_explicit_targets(
            single_scope_candidates,
            single_target_candidates,
        )
        invocations = fill_single_target_line_range_edit_blocks(
            invocations,
            target_files=tuple(single_target_candidates),
        )
        invocations_before_write_fill = invocations
        invocations = fill_content_only_write_file_from_remaining_targets(
            invocations,
            target_files=tuple(structured_target_candidates or single_target_candidates),
        )
        invocations, duplicate_write_rejections = split_write_file_duplicate_content_rejections(invocations)
        if duplicate_write_rejections:
            rejected_call_ids = [str(item.get("call_id") or "") for item in duplicate_write_rejections]
            logger.warning(
                "duplicate_content_write_rejected: file-less write_file duplicated an already-claimed "
                "same-batch write; rejected without dispatch. turn_id=%s call_ids=%s",
                turn_id,
                rejected_call_ids,
            )
            ledger.anomaly_flags.append(
                {
                    "type": "WRITE_FILE_DUPLICATE_CONTENT_REJECTED",
                    "turn_id": turn_id,
                    "rejected_call_ids": rejected_call_ids,
                    "rejections": [
                        dict(item.get(WRITE_FILE_DUPLICATE_REJECTION_KEY) or {}) for item in duplicate_write_rejections
                    ],
                }
            )
        write_file_autofill_evidence = diff_write_file_autofill_evidence(invocations_before_write_fill, invocations)
        invocations, dropped_out_of_scope_writes = filter_out_of_scope_write_invocations(
            latest_user_request,
            invocations,
            additional_allowed_targets=tuple(single_target_candidates),
            additional_allowed_scopes=tuple(single_scope_candidates),
        )
        if dropped_out_of_scope_writes:
            reason = (
                f"mutation write target drift sanitized; dropped_out_of_scope={list(dropped_out_of_scope_writes)[:6]}"
            )
            logger.warning("%s turn_id=%s", reason, turn_id)
            ledger.record_mutation_guard_warning(reason=reason, user_request=latest_user_request)

        # --- READ-WRITE BARRIER LOGIC ---
        # Platform tool contracts can explicitly allow read + write in the same
        # batch. In that case the normal read/write barrier must not split the
        # batch and create an impossible retry loop.
        _bypass_read_write_barrier = platform_tool_contract_bypasses_read_write_barrier(context)

        if not _bypass_read_write_barrier:
            # Normal execution: enforce the Read-Write Barrier.
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            has_read = False
            has_write = False
            read_tools_invoked: list[str] = []
            write_tools_invoked: list[str] = []

            for invocation in invocations:
                tname = extract_invocation_tool_name(invocation)
                if not tname:
                    continue
                spec = ToolSpecRegistry.get(tname)
                if spec:
                    if spec.is_read_tool():
                        has_read = True
                        read_tools_invoked.append(tname)
                    if spec.is_write_tool():
                        has_write = True
                        write_tools_invoked.append(tname)

            if has_read and has_write:
                overlap = set(read_tools_invoked) & set(write_tools_invoked)
                if overlap:
                    logger.warning("Tool %s is marked as both read and write, bypassing strict barrier", overlap)
                else:
                    raise RuntimeError(
                        "single_batch_contract_violation: Cannot mix Read tools "
                        f"({','.join(set(read_tools_invoked))}) and Write tools "
                        f"({','.join(set(write_tools_invoked))}) in the same parallel batch. "
                        "Please wait for read results before writing."
                    )
        else:
            logger.debug(
                "read-write-barrier: bypassed for benchmark single-batch mode. turn_id=%s",
                turn_id,
            )

        # FIX-20250421-v3: Phase detection using PhaseManager (real phase) instead of string matching.
        # PhaseManager is the single source of truth for phase state.
        from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase

        _current_phase = ledger.phase_manager.current_phase
        _is_implementing_phase = _current_phase == Phase.IMPLEMENTING
        _is_verifying_phase = _current_phase == Phase.VERIFYING
        _is_exploring_phase = _current_phase == Phase.EXPLORING

        # FIX-20250421-v3: Text output interception for MATERIALIZE_CHANGES + EXPLORING.
        # If no tools are invoked in EXPLORING phase with MATERIALIZE_CHANGES mode,
        # block the text output and force continue_multi_turn.
        _is_materialize = (
            ledger._original_delivery_mode == DeliveryMode.MATERIALIZE_CHANGES.value
            or getattr(ledger.delivery_contract, "mode", None) == DeliveryMode.MATERIALIZE_CHANGES
        )
        if _is_exploring_phase and _is_materialize and not invocations:
            logger.warning(
                "text_output_intercepted: MATERIALIZE_CHANGES + EXPLORING with no tool calls. "
                "Forcing continue_multi_turn. turn_id=%s",
                turn_id,
            )
            raise RuntimeError(
                "single_batch_contract_violation: "
                "MATERIALIZE_CHANGES mode requires tool execution in EXPLORING phase. "
                "You must call read_file/glob/repo_rg to explore, then write_file/edit_file to modify. "
                "Text-only responses are not allowed."
            )

        _broad_exploration_tools = {"glob", "repo_tree", "repo_rg", "grep", "search_code", "ripgrep", "find"}
        _broad_exploration_tools.add("list_directory")
        _has_broad_exploration = any(
            extract_invocation_tool_name(inv) in _broad_exploration_tools for inv in invocations
        )
        _has_write = tool_batch_has_authoritative_write_invocation(invocations)
        tool_names = [extract_invocation_tool_name(inv) for inv in invocations]
        non_empty_tool_names = [name for name in tool_names if name]
        only_broad_exploration = bool(non_empty_tool_names) and all(
            name in _broad_exploration_tools for name in non_empty_tool_names
        )
        has_direct_read = any(name in _DIRECT_READ_TOOLS for name in non_empty_tool_names)

        # FIX-20250422-v3: CONTENT_GATHERED + MATERIALIZE_CHANGES 的就绪门禁。
        # 取代 FIX-20250422-v2 的机械式 turns_in_phase >= 2 硬阻断。
        # 新逻辑：通过 ModificationContract 评估 LLM 是否已准备好写操作：
        # - READY_TO_WRITE（有修改计划）→ 强制写（阻断读）
        # - NEEDS_PLAN（无计划）+ turns < max → 允许读（由 continuation prompt 注入规划指令）
        # - NEEDS_PLAN + turns >= max → 降级到 phase timeout（现有行为）
        from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
            ReadinessVerdict,
            evaluate_modification_readiness,
        )

        _is_content_gathered_phase = _current_phase == Phase.CONTENT_GATHERED
        _enable_modification_contract = getattr(self.config, "enable_modification_contract", True)

        # Phase-2 weak-model exemption (2026-06-10): after a FAILED edit attempt the
        # mandated recovery (and the failure budget's own instruction) is to re-read
        # the exact file content before retrying the edit. Blanket "reading is
        # blocked" in CONTENT_GATHERED traps that recovery and forces blind edits
        # from memory (hallucinated SEARCH text). Allow a direct-read-only batch
        # through when the conversation shows a recent edit failure.
        _verification_read_exemption = (
            _is_content_gathered_phase
            and _is_materialize
            and not _has_write
            and has_direct_read
            and not _has_broad_exploration
            and _recent_edit_failure_in_context(context)
        )
        if _verification_read_exemption:
            logger.info(
                "content_gathered verification-read exemption: allowing direct read after edit failure. "
                "turn_id=%s tools=%s",
                turn_id,
                non_empty_tool_names,
            )

        if _is_content_gathered_phase and _is_materialize and not _has_write and not _verification_read_exemption:
            if _enable_modification_contract:
                # FIX-20250422-SUPER: 传递对话上下文以检测 SUPER_MODE 标记
                _verdict = evaluate_modification_readiness(
                    contract=ledger.modification_contract,
                    phase_value=_current_phase.value,
                    delivery_mode_value=ledger.delivery_contract.mode.value
                    if hasattr(ledger.delivery_contract.mode, "value")
                    else str(ledger.delivery_contract.mode),
                    turns_in_phase=ledger.phase_manager._turns_in_current_phase,
                    max_turns_per_phase=ledger.phase_manager._max_turns_per_phase,
                    conversation_context=context,
                )
                if _verdict == ReadinessVerdict.READY_TO_WRITE:
                    # 契约已就绪，强制 LLM 使用写工具
                    self._raise_contract_violation(
                        turn_id=turn_id,
                        error_type="content_gathered_write_required",
                        message=(
                            "single_batch_contract_violation: CONTENT_GATHERED phase requires write tools. "
                            "Your modification plan is confirmed. "
                            "You MUST call write_file/edit_file to execute your plan now. "
                            "Reading more files is blocked."
                        ),
                        metadata={
                            "phase": "content_gathered",
                            "verdict": _verdict.value,
                            "contract_status": ledger.modification_contract.status.value,
                            "turns_in_phase": ledger.phase_manager._turns_in_current_phase,
                            "tool_names": non_empty_tool_names,
                            "has_write": _has_write,
                        },
                    )
                else:
                    # NEEDS_PLAN: 检查是否已超过 max_turns_per_phase → 降级到 timeout
                    if ledger.phase_manager._turns_in_current_phase >= ledger.phase_manager._max_turns_per_phase:
                        logger.warning(
                            "modification_contract_timeout_degradation: contract still %s after %d turns. "
                            "Falling back to phase timeout hard block. turn_id=%s",
                            ledger.modification_contract.status.value,
                            ledger.phase_manager._turns_in_current_phase,
                            turn_id,
                        )
                        self._raise_contract_violation(
                            turn_id=turn_id,
                            error_type="content_gathered_write_required",
                            message=(
                                "single_batch_contract_violation: CONTENT_GATHERED phase timeout. "
                                "You have spent too many turns reading without declaring a modification plan. "
                                "You MUST call write_file/edit_file to materialize changes NOW. "
                                "Reading more files is blocked."
                            ),
                            metadata={
                                "phase": "content_gathered",
                                "verdict": _verdict.value,
                                "contract_status": ledger.modification_contract.status.value,
                                "turns_in_phase": ledger.phase_manager._turns_in_current_phase,
                                "tool_names": non_empty_tool_names,
                                "has_write": _has_write,
                                "degraded": True,
                            },
                        )
                    else:
                        # 允许读操作，由 continuation prompt 注入规划指令
                        logger.info(
                            "modification_contract_needs_plan: allowing read tools in CONTENT_GATHERED. "
                            "contract_status=%s turns_in_phase=%d max=%d turn_id=%s",
                            ledger.modification_contract.status.value,
                            ledger.phase_manager._turns_in_current_phase,
                            ledger.phase_manager._max_turns_per_phase,
                            turn_id,
                        )
            else:
                # 功能禁用：回退到 FIX-20250422-v2 的 turns_in_phase >= 2 硬阻断
                if ledger.phase_manager._turns_in_current_phase >= 2:
                    self._raise_contract_violation(
                        turn_id=turn_id,
                        error_type="content_gathered_write_required",
                        message=(
                            "single_batch_contract_violation: CONTENT_GATHERED phase requires write tools. "
                            "You have already read file contents for multiple turns. "
                            "You MUST call write_file/edit_file to materialize changes. "
                            "Reading more files is blocked. Emit write tools now."
                        ),
                        metadata={
                            "phase": "content_gathered",
                            "turns_in_phase": ledger.phase_manager._turns_in_current_phase,
                            "tool_names": non_empty_tool_names,
                            "has_write": _has_write,
                        },
                    )

        _exploration_streak_hard_block = "EXPLORATION_STREAK_HARD_BLOCK" in latest_user_request
        if (
            _exploration_streak_hard_block
            and _is_exploring_phase
            and only_broad_exploration
            and not has_direct_read
            and not _has_write
        ):
            self._raise_contract_violation(
                turn_id=turn_id,
                error_type="exploration_streak_hard_block",
                message=(
                    "single_batch_contract_violation: exploration_streak_hard_block active. "
                    "Do not emit only glob/repo_rg/list_directory/repo_tree again. "
                    "You must call read_file (or a write tool) this turn."
                ),
                metadata={
                    "phase": "exploring",
                    "tool_names": non_empty_tool_names,
                    "has_direct_read": has_direct_read,
                    "has_write": _has_write,
                },
            )

        phase_blocked_invocations: list[Any] = []
        if _is_implementing_phase and _has_broad_exploration and not _has_write:
            # FIX-20250421: Hard block when ALL tools are broad exploration — raise exception
            # This triggers retry orchestrator (is_mutation_contract_violation check with
            # "single_batch_contract_violation" prefix) and forces LLM to use write tools.
            filtered_invocations = [
                inv for inv in invocations if extract_invocation_tool_name(inv) not in _broad_exploration_tools
            ]
            if not filtered_invocations:
                raise RuntimeError(
                    "single_batch_contract_violation: "
                    "in implementing phase, broad exploration tools (glob/repo_tree/repo_rg) "
                    "are not allowed. Use write_file/edit_file to materialize changes."
                )
            # Partial block: blocked exploration calls must never reach the
            # physical runtime. Keep them as explicit failed receipts while
            # executing the remaining allowed calls. Annotating a strict
            # ToolInvocation with ad-hoc fields is insufficient: model
            # canonicalization may discard those fields and dispatch the tool.
            phase_blocked_invocations = [
                inv for inv in invocations if extract_invocation_tool_name(inv) in _broad_exploration_tools
            ]
            invocations = filtered_invocations
            ledger._implementing_phase_block_triggered = True

        # FIX-20250421: Verifying Phase Hard Constraint — verification REQUIRED, write not enough
        # FIX-20250422-SUPER: SUPER_MODE bypass — CLI SUPER mode already has PM-generated plan
        # and QA will verify separately. Director should not be blocked here.
        from polaris.cells.roles.kernel.internal.transaction.constants import VERIFICATION_TOOLS
        from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
            _conversation_has_super_mode_markers,
        )

        if _is_verifying_phase and not _conversation_has_super_mode_markers(context):
            tool_names = [extract_invocation_tool_name(inv) for inv in invocations]
            has_verification = any(t in VERIFICATION_TOOLS for t in tool_names)
            if not has_verification:
                # Verification tool (execute_command) is mandatory in verifying phase
                raise RuntimeError(
                    "single_batch_contract_violation: "
                    "verifying-phase-requires-verification: In verifying phase, "
                    "you must call execute_command to run tests (pytest, etc.) "
                    "or verify the fix manually. No verification tool detected — ending session."
                )

        requires_mutation = enforce_mutation_write_guard and self.requires_mutation_intent(latest_user_request)
        known_target_files = extract_target_files_from_message(latest_user_request)
        known_target_files.extend(extract_platform_tool_contract_target_files(context))
        known_target_files = list(dict.fromkeys(str(item) for item in known_target_files if str(item)))
        authoritative_absent_targets = {
            _normalize_file_reference_path(target).casefold()
            for target in extract_platform_tool_contract_missing_target_files(context)
            if _normalize_file_reference_path(target)
        }
        known_target_keys = {
            _normalize_file_reference_path(target).casefold()
            for target in known_target_files
            if _normalize_file_reference_path(target)
        }
        target_files_known = bool(known_target_files) or bool(ledger.mutation_obligation.target_files_known)
        missing_read_evidence = int(ledger.mutation_obligation.read_evidence_count or 0) <= 0
        if (
            requires_mutation
            and _is_materialize
            and _is_exploring_phase
            and target_files_known
            and missing_read_evidence
            and only_broad_exploration
            and not has_direct_read
            and not _has_write
        ):
            # A platform materialization contract is authoritative workspace evidence.
            # It can describe the target workspace even when this executor runs from
            # a different local workspace, so do not replace that evidence with a
            # local filesystem probe and demand an impossible read.
            targets_authoritatively_absent = bool(known_target_keys) and known_target_keys.issubset(
                authoritative_absent_targets
            )

            # From-scratch create trap: when every known target file is absent,
            # demanding read_file sends the Director to read a non-existent file
            # (which yields no read evidence), so it loops on broad exploration and
            # never materializes the entry file. The filesystem fallback preserves
            # this behavior when no platform evidence is available.
            existing_targets = [
                target
                for target in known_target_files
                if _resolve_existing_workspace_file(workspace=workspace, raw_path=target) is not None
            ]
            if targets_authoritatively_absent or (known_target_files and not existing_targets):
                self._raise_contract_violation(
                    turn_id=turn_id,
                    error_type="known_target_requires_write",
                    message=(
                        "single_batch_contract_violation: target_files_known_and_absent; "
                        "requires_direct_write. The known target files do not exist yet — do not read a file "
                        "that does not exist. Call write_file to create them this turn."
                    ),
                    metadata={
                        "phase": "exploring",
                        "tool_names": non_empty_tool_names,
                        "known_target_files": known_target_files[:6],
                        "absent_targets": True,
                        "authoritative_absent_targets": sorted(authoritative_absent_targets)[:6],
                    },
                )
            self._raise_contract_violation(
                turn_id=turn_id,
                error_type="known_target_requires_read",
                message=(
                    "single_batch_contract_violation: target_files_known_without_read_evidence; "
                    "requires_bootstrap_read. Broad exploration is no longer allowed once a candidate file path "
                    "is already known. Call read_file on the known target (or write after a fresh read)."
                ),
                metadata={
                    "phase": "exploring",
                    "tool_names": non_empty_tool_names,
                    "known_target_files": known_target_files[:6],
                    "read_evidence_count": ledger.mutation_obligation.read_evidence_count,
                },
            )
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        # FIX-20250421: Upgrade to strict in implementing phase if broad exploration was attempted
        if _is_implementing_phase and _has_broad_exploration and not _has_write:
            guard_mode = "strict"
        if requires_mutation and not tool_batch_has_authoritative_write_invocation(invocations):
            if guard_mode == "strict":
                raise RuntimeError(
                    "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch. "
                    "In implementing phase, you must emit at least one write tool (edit_file, write_file, etc.). "
                    "Use read_file only for specific target file verification."
                )
            elif guard_mode == "warn":
                logger.warning(
                    "mutation-guard-soft: user request triggered mutation markers but no write tool invoked. "
                    "turn_id=%s user_request=%r",
                    turn_id,
                    latest_user_request,
                )
                ledger.record_mutation_guard_warning(
                    reason="mutation_markers_detected_but_no_write_tool",
                    user_request=latest_user_request,
                )
        if requires_mutation:
            violation = resolve_mutation_target_guard_violation(
                latest_user_request,
                invocations,
                additional_allowed_targets=tuple(single_target_candidates),
                additional_allowed_scopes=tuple(single_scope_candidates),
            )
            if violation:
                if guard_mode == "strict":
                    raise RuntimeError(violation)
                elif guard_mode == "warn":
                    logger.warning(
                        "mutation-target-guard-soft: %s. turn_id=%s",
                        violation,
                        turn_id,
                    )
                    ledger.record_mutation_guard_warning(
                        reason=str(violation),
                        user_request=latest_user_request,
                    )
        if count_towards_batch_limit:
            ledger.tool_batch_count += 1
            self.guard_assert_single_tool_batch(
                turn_id=turn_id,
                tool_batch_count=ledger.tool_batch_count,
                ledger=ledger,
            )

        # FIX-20250422: Log redundant reads for debugging but do NOT block them.
        # The prompt truncation happens in the context assembler (not the tool),
        # so a file may appear "fully read" to the tool but truncated to the model.
        # Blocking re-reads causes dead loops. Phase timeout (max 3 turns in
        # CONTENT_GATHERED) prevents infinite loops instead.
        _redundant_reads: list[str] = []
        for invocation in invocations:
            tname = extract_invocation_tool_name(invocation)
            if tname in _DIRECT_READ_TOOLS:
                target_file = extract_target_file_from_invocation_args(invocation)
                if target_file:
                    normalized_target = target_file.replace("\\", "/").lower()
                    if normalized_target in self._session_read_files:
                        _redundant_reads.append(target_file)
        if _redundant_reads:
            logger.debug(
                "[DEBUG][FIX-20250422] redundant_read_detected (not blocked): files=%s phase=%s turn_id=%s",
                _redundant_reads[:3],
                _current_phase.value if hasattr(_current_phase, "value") else str(_current_phase),
                turn_id,
            )
        logger.debug(
            "[DEBUG][FIX-20250422] session_read_files count=%s turn_id=%s",
            len(self._session_read_files),
            turn_id,
        )

        # Classify every final invocation and prepare the complete mutation
        # inventory before any read, speculative adoption, or physical effect.
        try:
            invocations, prepared_directed_effect = await self._prepare_directed_effect_dispatch(
                invocations=invocations,
                workspace=workspace,
                turn_id=turn_id,
                batch_id=str(tool_batch.get("batch_id") or f"{turn_id}_batch"),
            )
        except RuntimeError as deo_exc:
            # R135: seal blocked lifecycle before re-raising so claimed materialization
            # never ends as bare TOOL_LIFECYCLE_MISSING after DEO abort.
            error_token = str(deo_exc)
            if _is_deo_abort_error(error_token):
                _seal_deo_abort_tool_lifecycle(
                    workspace=workspace,
                    run_id=execution_run_id,
                    task_id=execution_task_id,
                    turn_id=turn_id,
                    role_id=str(getattr(self.config, "role_id", "") or ""),
                    invocations=invocations,
                    metadata=metadata if isinstance(metadata, Mapping) else {},
                    ledger=ledger,
                    error_code=error_token,
                    provider_response_hash=str(
                        (metadata or {}).get("provider_response_hash") if isinstance(metadata, Mapping) else ""
                    ),
                )
            if _is_recoverable_deo_normalization_abort(error_token):
                # Live L2-12 TASK-2 / M03: native edit_file denied as
                # deo_tool_normalization_failed must surface as a tool error
                # receipt so the model can retry. Re-raising aborted the
                # Director turn and Factory projected
                # director_no_materialized_changes with no write receipt.
                normalization_failed_results: list[dict[str, Any]] = []
                for invocation in invocations:
                    tool_name = extract_invocation_tool_name(invocation) or "unknown_tool"
                    normalization_failed_results.append(
                        {
                            "call_id": str(getattr(invocation, "call_id", "") or ""),
                            "tool_name": tool_name,
                            "status": "error",
                            "result": {
                                "ok": False,
                                "error": error_token,
                                "error_type": "deo_tool_normalization_failed",
                            },
                            "error": error_token,
                            "effect_receipt": None,
                            "directed_effect_claim_status": "not_claimed",
                        }
                    )
                normalization_failed_receipts = [
                    {
                        "batch_id": str(tool_batch.get("batch_id") or f"{turn_id}_batch"),
                        "turn_id": turn_id,
                        "results": normalization_failed_results,
                        "raw_results": [dict(item) for item in normalization_failed_results],
                        "success_count": 0,
                        "failure_count": len(normalization_failed_results),
                        "pending_async_count": 0,
                        "has_pending_async": False,
                    }
                ]
                record_receipts_to_ledger(normalization_failed_receipts, ledger)
                if state_machine.current_state != TurnState.TOOL_BATCH_EXECUTING:
                    state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTING)
                state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTED)
                from polaris.cells.roles.kernel.internal.transaction.finalization import (
                    FinalizationHandler,
                )

                return FinalizationHandler.complete_with_tool_results(
                    decision,
                    normalization_failed_receipts,
                    state_machine,
                    ledger,
                    self.emit_event,
                )
            raise

        # Effect policy enforcement gate
        self._check_effect_policy(invocations, turn_id)

        # === Phase 4a: 开始执行 ===
        if state_machine.current_state != TurnState.TOOL_BATCH_EXECUTING:
            state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        ledger.state_history.append(("TOOL_BATCH_EXECUTING", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "tool_batch_started", {"tool_count": len(invocations)}))

        # Speculative Execution Kernel v2 integration
        receipts_as_dicts: list[dict] = []
        if phase_blocked_invocations:
            blocked_results: list[dict[str, Any]] = []
            for invocation in phase_blocked_invocations:
                tool_name = extract_invocation_tool_name(invocation)
                reason = f"Tool '{tool_name}' blocked in implementing phase. Use write tools."
                blocked_results.append(
                    {
                        "call_id": str(invocation.get("call_id") or ""),
                        "tool_name": tool_name,
                        "status": "error",
                        "result": {
                            "ok": False,
                            "error": reason,
                            "error_type": "implementing_phase_tool_blocked",
                        },
                        "error": reason,
                        "effect_receipt": None,
                    }
                )
            receipts_as_dicts.append(
                {
                    "batch_id": str(tool_batch.get("batch_id") or f"{turn_id}_batch"),
                    "turn_id": turn_id,
                    "results": blocked_results,
                    "raw_results": [dict(item) for item in blocked_results],
                    "success_count": 0,
                    "failure_count": len(blocked_results),
                    "pending_async_count": 0,
                    "has_pending_async": False,
                }
            )
        replay_invocations: list[Any] = []
        batch_cancel_token = CancelToken()

        audit_adoptions = is_adoption_audit_enabled()
        if shadow_engine is not None and hasattr(shadow_engine, "resolve_or_execute"):
            for invocation in invocations:
                tool_name = str(invocation.get("tool_name", ""))
                call_id = str(invocation.get("call_id", ""))
                args = dict(invocation.get("arguments", {})) if isinstance(invocation.get("arguments"), dict) else {}
                try:
                    resolution = await shadow_engine.resolve_or_execute(
                        turn_id=turn_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        args=args,
                    )
                except asyncio.CancelledError:
                    raise
                except (RuntimeError, TypeError, ValueError) as exc:
                    if stream and WriteToolPhases.is_write_tool(tool_name):
                        # Speculative resolution errored for a write tool. Fall back to
                        # authoritative batch execution (below) rather than aborting the
                        # whole turn — the authoritative path executes the write safely,
                        # identical to running with speculation disabled.
                        logger.debug(
                            "[tool_batch] speculative resolution error for write tool %r "
                            "(call_id=%s): %s; replaying via authoritative batch",
                            tool_name,
                            call_id,
                            exc,
                        )
                        replay_invocations.append(invocation)
                        continue
                    resolution = {"action": "replay", "result": None, "error": str(exc)}
                action = str(resolution.get("action", "replay"))
                # Task9 classification is authoritative for the production
                # branch.  Unknown/default-write and ASYNC calls may not be
                # adopted as speculative READs merely because the legacy
                # write-name table does not recognize their spelling.
                is_write_tool = _is_mutation_for_speculative_routing(
                    invocation,
                    directed_effect_required=self.directed_effect_required,
                )
                if stream and is_write_tool and action == "block":
                    # A missing/blocked speculative prepare-shadow is a benign optimization
                    # miss: recovered/post-hoc write tool calls (e.g. from non-function-calling
                    # models, surfaced after streaming) never get a speculative prepare, so the
                    # shadow is legitimately absent. Fall back to authoritative batch execution
                    # (identical to speculation-disabled, which executes writes through the same
                    # path) instead of aborting the entire turn and discarding the model's work.
                    error = str(resolution.get("error") or "write_tool_prepare_shadow_blocked")
                    logger.debug(
                        "[tool_batch] speculative prepare miss for write tool %r "
                        "(call_id=%s, reason=%s); replaying via authoritative batch",
                        tool_name,
                        call_id,
                        error,
                    )
                    replay_invocations.append(invocation)
                    continue
                if action in ("adopt", "join") and not is_write_tool:
                    final_payload = resolution.get("result")
                    if audit_adoptions:
                        # 领养审计模式：把投机结果与权威重算结果对比，证明 ADR-0077
                        # 不变量 A（correctness 不变）。不一致即记 wrong_adoption，并
                        # 改用权威结果（detector + 安全网）。默认关闭，仅评测/验证开启。
                        final_payload = await self._audit_adopted_result(
                            invocation=invocation,
                            speculative_payload=final_payload,
                            workspace=workspace,
                            turn_id=turn_id,
                            tool_name=tool_name,
                            shadow_engine=shadow_engine,
                        )
                    adopted_result = {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": final_payload,
                        "error": None,
                        "execution_time_ms": 0,
                        "effect_receipt": None,
                    }
                    raw_result = {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": final_payload,
                    }
                    receipts_as_dicts.append(
                        {
                            "batch_id": str(tool_batch.get("batch_id", "")),
                            "turn_id": turn_id,
                            "results": [adopted_result],
                            "raw_results": [raw_result],
                            "success_count": 1,
                            "failure_count": 0,
                            "pending_async_count": 0,
                            "has_pending_async": False,
                        }
                    )
                else:
                    replay_invocations.append(invocation)
        else:
            replay_invocations = list(invocations)

        # WS1 (2026-07-07): ToolCallEnvelope 归一化缺口修复。
        # ToolBatchRuntime 按 execution_mode 分桶执行 replay_invocations；
        # 但 decoded/native tool call 在 allow-list/guard/mutation 检查通过后
        # 仍可能缺失 execution_mode 标注（弱模型 textual 工具调用、native
        # FC 调用但 provider 未填 execution_mode 等场景）。未归一化的调用会
        # 同时落入空 ToolBatch，被下游 hard gate 误判为
        # ``tool_dispatch_dropped`` 拒绝整个 batch，破坏 ToolCallEnvelope
        # 的统一归一化契约。已显式标注 execution_mode 的 invocation 行为
        # 完全不变；只有缺失/非法标注的 invocation 才走 ToolBatchRuntime
        # 共享分类真相源补齐。hard gate 仍保留：如果补齐后仍无 receipt，
        # executor 必须 fail-closed。
        replay_invocations = normalize_replay_execution_modes(replay_invocations)

        # 对未命中的 invocation 走 authoritative batch 执行
        dropped_member_ids: set[str] = set()
        dropped_member_rows: list[tuple[str, str, str]] = []
        if prepared_directed_effect is not None:
            dropped_member_rows = list(prepared_directed_effect.dropped_members)
            dropped_member_ids = {call_id for call_id, _tool, _code in dropped_member_rows}
        if replay_invocations:

            def _keep_for_dispatch(inv: Any) -> bool:
                call_id = str(inv.get("call_id") or "")
                return call_id not in dropped_member_ids

            replay_batch = ToolBatch(
                batch_id=tool_batch.get("batch_id", BatchId(f"{turn_id}_replay")),
                parallel_readonly=[
                    inv
                    for inv in replay_invocations
                    if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL and _keep_for_dispatch(inv)
                ],
                readonly_serial=[
                    inv
                    for inv in replay_invocations
                    if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL and _keep_for_dispatch(inv)
                ],
                serial_writes=[
                    inv
                    for inv in replay_invocations
                    if inv.get("execution_mode") == ToolExecutionMode.WRITE_SERIAL and _keep_for_dispatch(inv)
                ],
                async_receipts=[
                    inv
                    for inv in replay_invocations
                    if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT and _keep_for_dispatch(inv)
                ],
            )
            receipts = await self._build_tool_batch_runtime(
                workspace,
                turn_id=turn_id,
                cancel_token=batch_cancel_token,
                prepared_directed_effect=prepared_directed_effect,
            ).execute_batch(
                replay_batch,
                TurnId(turn_id),
            )
            receipts_as_dicts.extend(normalize_batch_receipts(receipts))

        # R140/R86: every non-dispatched DEO member remains visible. Genuine
        # policy denials are tool errors; benign last-write-wins supersession is
        # a successful no-effect accounting row and must not poison lifecycle.
        if dropped_member_rows:
            receipts_as_dicts.append(
                _project_directed_effect_dropped_member_receipt(
                    dropped_member_rows=dropped_member_rows,
                    batch_id=str(tool_batch.get("batch_id") or f"{turn_id}_batch"),
                    turn_id=turn_id,
                )
            )

        # DEO-2C: adapter repair planning may return one typed deferred request
        # only after the active ToolBatch has completed and released its JIT
        # fence. Strip those process-local request objects before any result can
        # reach finalization/provider context, then schedule one visible,
        # non-recursive follow-up batch through the normal DEO preparation and
        # ToolBatchRuntime boundaries. Forward and rollback contingencies enter
        # the same sealed inventory; only forwards dispatch, and unused rollback
        # members are durably aborted by ToolBatchRuntime.
        receipts_as_dicts.extend(
            await self._execute_deferred_repair_followup(
                receipts_as_dicts=receipts_as_dicts,
                primary_batch_id=str(tool_batch.get("batch_id") or f"{turn_id}_batch"),
                workspace=workspace,
                turn_id=turn_id,
                ledger=ledger,
                cancel_token=batch_cancel_token,
            )
        )

        if invocations and _batch_result_count(receipts_as_dicts) <= 0:
            decoded_tool_calls = [
                tool_invocation_audit_ref(
                    invocation,
                    reason="decoded_tool_batch_without_authoritative_receipt",
                    tool_name=extract_invocation_tool_name(invocation),
                    target_file=extract_target_file_from_invocation_args(invocation),
                )
                for invocation in invocations
            ]
            lifecycle = build_tool_batch_lifecycle_receipt_from_sources(
                run_id=execution_run_id,
                task_id=execution_task_id,
                turn_id=turn_id,
                role=str(getattr(self.config, "role_id", "") or ""),
                provider_response_hash=str(metadata.get("provider_response_hash") or ""),
                metadata=metadata,
                decoded_tool_calls_count=len(invocations),
                dropped_tool_calls=decoded_tool_calls,
                missing_receipt_reason="decoded_tool_batch_produced_no_authoritative_batch_receipt",
            ).to_dict()
            ledger.anomaly_flags.append(build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt(lifecycle))
            _append_tool_batch_receipts_to_run_ledger(
                workspace=workspace,
                run_id=execution_run_id,
                role_id=str(getattr(self.config, "role_id", "") or ""),
                task_id=execution_task_id,
                turn_id=turn_id,
                invocations=invocations,
                receipts=[],
                capability_token=self._capability_token or _capability_token_from_metadata(metadata),
                execution_envelope_hash=(
                    self._execution_envelope_hash or _execution_envelope_hash_from_metadata(metadata)
                ),
                provider_response_hash=str(metadata.get("provider_response_hash") or ""),
                metadata=metadata,
            )
            raise RuntimeError("tool_dispatch_dropped: decoded tool batch produced no authoritative batch receipt")

        if write_file_autofill_evidence:
            annotate_autofilled_write_receipts(receipts_as_dicts, write_file_autofill_evidence)
        if duplicate_write_rejections:
            # Teaching-error receipt for the rejected duplicate(s): surfaces to the
            # model as a failed tool result without any write having happened, and
            # keeps the original same-batch write's receipt authoritative.
            receipts_as_dicts.append(
                {
                    "batch_id": str(tool_batch.get("batch_id", "") or ""),
                    "turn_id": turn_id,
                    "results": [dict(item) for item in duplicate_write_rejections],
                    "raw_results": [dict(item) for item in duplicate_write_rejections],
                    "success_count": 0,
                    "failure_count": len(duplicate_write_rejections),
                    "pending_async_count": 0,
                    "has_pending_async": False,
                }
            )
        record_receipts_to_ledger(receipts_as_dicts, ledger)
        _append_tool_batch_receipts_to_run_ledger(
            workspace=workspace,
            run_id=execution_run_id,
            role_id=str(getattr(self.config, "role_id", "") or ""),
            task_id=execution_task_id,
            turn_id=turn_id,
            invocations=invocations,
            receipts=receipts_as_dicts,
            capability_token=self._capability_token or _capability_token_from_metadata(metadata),
            execution_envelope_hash=self._execution_envelope_hash or _execution_envelope_hash_from_metadata(metadata),
            provider_response_hash=str(metadata.get("provider_response_hash") or ""),
            metadata=metadata,
        )
        if invocations and receipts_as_dicts and not _batch_has_authoritative_success(receipts_as_dicts):
            merged_failed_receipt = _merge_batch_receipts(receipts_as_dicts) or {}
            failure_error_types = batch_write_failure_error_types(merged_failed_receipt)
            failed_tool_names = [
                str(result.get("tool_name") or "").strip()
                for receipt in normalize_batch_receipts(receipts_as_dicts)
                for result in receipt.get("raw_results", []) or receipt.get("results", []) or []
                if isinstance(result, dict) and str(result.get("status") or "").strip().lower() != "success"
            ]
            ledger.anomaly_flags.append(
                {
                    "type": "TOOL_BATCH_ALL_RESULTS_FAILED",
                    "turn_id": turn_id,
                    "reason": "decoded_tool_batch_produced_only_failed_results",
                    "decoded_tool_calls_count": len(invocations),
                    "failed_tool_names": [name for name in failed_tool_names if name],
                    "error_types": list(failure_error_types),
                }
            )
            if batch_write_failures_require_llm_replan(merged_failed_receipt):
                ledger.tool_batch_count = max(0, int(ledger.tool_batch_count or 0) - 1)
                rendered_error_types = ",".join(failure_error_types) or "correctable_write_rejection"
                raise RuntimeError(
                    "single_batch_contract_violation: write tool batch produced no effects and requires "
                    f"a new invocation within the authorized target scope; error_types={rendered_error_types}"
                )
            # Live L2-12 TASK-3-source-modules: MiniMax issued
            # ``execute_command("ls && cat ...")``. Security returned a
            # recoverable compound-command no-op, DEO then dead-lettered it as
            # a mutation, and this raise aborted the turn with
            # error_types=unknown because execute_command is not a WRITE_TOOLS
            # name. Observational/command failures must stay as receipts so the
            # model can re-issue read_file / single commands.
            if any(is_write_tool_name(name) for name in failed_tool_names):
                failure_details = _failed_batch_diagnostic_excerpt(receipts_as_dicts)
                detail_suffix = f"; failure_details={failure_details}" if failure_details else ""
                raise RuntimeError(
                    "tool_dispatch_failed: decoded tool batch produced only failed tool results; "
                    f"decoded_tool_calls={len(invocations)}; "
                    f"error_types={','.join(failure_error_types) or 'unknown'}{detail_suffix}"
                )

        # 本 turn 的工具批裁决已完成（adopt/join/replay 全部计入 metrics）；在此
        # 发射 per-turn 推测执行汇总，确保它包含全部裁决指标（drain 阶段过早，
        # 早于裁决）。emit_turn_summary 对每个 metrics 实例幂等，重试路径不会重复。
        if shadow_engine is not None:
            _spec_metrics = getattr(shadow_engine, "metrics", None)
            if _spec_metrics is not None and hasattr(_spec_metrics, "emit_turn_summary"):
                _spec_metrics.emit_turn_summary(turn_id)

        # FIX-20250422: Track successfully read files in session state
        # Only track files that were NOT truncated — truncated reads are NOT
        # "successful" reads from the model's perspective, and the model needs
        # to re-read (often with range params) to get the full content before
        # it can materialize changes. Blocking re-reads of truncated files
        # causes the infinite loop in MATERIALIZE_CHANGES mode.
        for receipt in receipts_as_dicts:
            if not isinstance(receipt, dict):
                continue
            for result_item in receipt.get("results", []) or []:
                if not isinstance(result_item, dict):
                    continue
                if result_item.get("status") != "success":
                    continue
                tname = str(result_item.get("tool_name", ""))
                if tname in _DIRECT_READ_TOOLS:
                    result_data = result_item.get("result") or {}
                    if isinstance(result_data, dict):
                        file_path = str(result_data.get("file", ""))
                        is_truncated = bool(result_data.get("truncated", False))
                        if file_path and not is_truncated:
                            normalized_fp = file_path.replace("\\", "/").lower()
                            if normalized_fp not in self._session_read_files:
                                self._session_read_files.add(normalized_fp)
                                logger.debug(
                                    "[DEBUG][FIX-20250422] session_read_files added: %s turn_id=%s",
                                    normalized_fp,
                                    turn_id,
                                )
                        elif file_path and is_truncated:
                            logger.debug(
                                "[DEBUG][FIX-20250422] session_read_files SKIP (truncated): %s turn_id=%s",
                                file_path.replace("\\", "/").lower(),
                                turn_id,
                            )

        # FIX-20250421: PhaseManager — 基于工具执行结果驱动阶段流转.
        # External tool contracts may own batch-level phase rules, so only those
        # contracts can disable the default phase manager.
        if not platform_tool_contract_disables_phase_manager(context):
            # FIX-20250421: receipts_as_dicts 是 receipt 列表，每个 receipt 包含嵌套的 results
            # 需要展开所有 receipt 的 results 才能正确提取 ToolResult
            all_result_items: list[dict[str, Any]] = []
            for receipt in receipts_as_dicts:
                if isinstance(receipt, dict):
                    receipt_results = receipt.get("results") or []
                    if isinstance(receipt_results, list):
                        all_result_items.extend(r for r in receipt_results if isinstance(r, dict))
            tool_results = extract_tool_results_from_batch_receipt({"results": all_result_items})
            if tool_results:
                old_phase = ledger.phase_manager.current_phase
                new_phase = ledger.phase_manager.transition(tool_results)
                if new_phase != old_phase:
                    logger.info(
                        "Phase transition: %s -> %s (tools: %s) turn_id=%s",
                        old_phase.value,
                        new_phase.value,
                        [r.tool_name for r in tool_results],
                        turn_id,
                    )

                # 验证工具组合是否符合阶段约束
                is_valid, error_msg = ledger.phase_manager.validate_tools_for_phase(tool_results)
                if not is_valid:
                    # 阶段违规：生成错误 receipt 而不是抛异常
                    logger.warning("Phase violation: %s turn_id=%s", error_msg, turn_id)
                    # 将错误信息注入到 receipts 中，让 LLM 在下一轮看到
                    receipts_as_dicts.append(
                        {
                            "tool_name": "phase_guard",
                            "status": "error",
                            "result": error_msg,
                            "call_id": f"phase_guard_{turn_id}",
                        }
                    )

                # FIX-20250422: Phase timeout 熔断机制
                # 防止 MATERIALIZE_CHANGES 模式下 LLM 在 CONTENT_GATHERED 阶段无限重读
                is_timeout, timeout_msg = ledger.phase_manager.is_phase_timeout()
                if is_timeout:
                    logger.warning("Phase timeout: %s turn_id=%s", timeout_msg, turn_id)
                    # 将超时信息注入到 receipts 中
                    receipts_as_dicts.append(
                        {
                            "tool_name": "phase_timeout_guard",
                            "status": "error",
                            "result": timeout_msg,
                            "call_id": f"phase_timeout_{turn_id}",
                        }
                    )
                    # 标记 mutation obligation 为 forced_finalization
                    # 这样 _should_block_llm_once_finalization 会允许 LLM_ONCE 收口
                    ledger.mutation_obligation.mark_blocked(
                        BlockedReason.PHASE_TIMEOUT,
                        detail=timeout_msg,
                    )

        if (
            requires_mutation
            and tool_batch_has_authoritative_write_invocation(invocations)
            and receipts_have_stale_edit_failure(receipts_as_dicts)
        ):
            raise RuntimeError(
                "single_batch_contract_violation: stale_edit blocked write invocation; requires_bootstrap_read"
            )

        # Phase-1 A8a (2026-06-11, phase1smoke4): when a mutation-required batch
        # contains write invocations that ALL failed on argument shape (prose in
        # blocks / SEARCH==REPLACE no-op / missing args), escalate through the
        # SAME mutation-contract retry ladder — its later attempts force the
        # write tool by name and narrow edit_blocks to the line-range schema,
        # which guided decoding satisfies (fix5: prose escapes dozens -> 0).
        # Without this trigger the ladder only fires on no-write batches, so a
        # model that volunteers malformed writes burns every turn unescalated.
        if requires_mutation:
            _shape_guard_receipt = _merge_batch_receipts(receipts_as_dicts)
            if _shape_guard_receipt and batch_write_results_all_failed_on_argument_shape(_shape_guard_receipt):
                # This batch produced ZERO effects (every write failed on
                # argument shape), so it must not consume the single-batch
                # budget: the escalation retry executes a REPLACEMENT batch,
                # and with the void batch still counted the guard sees two
                # ToolBatches and kills the turn mid-escalation (live
                # factory-bench L2-11 r5: KernelGuardError right after a
                # missing-required-argument write_file).
                ledger.tool_batch_count = max(0, int(ledger.tool_batch_count or 0) - 1)
                raise RuntimeError(
                    "single_batch_contract_violation: mutation write batch failed on argument shape "
                    "(prose/no-op/missing-args in write tool arguments) — escalating to forced-write retry"
                )

        breaker_snapshot = self._tool_failure_circuit_breaker.evaluate_batch(
            turn_id=turn_id,
            receipts=receipts_as_dicts,
            invocations=[invocation.to_dict() for invocation in invocations],
        )
        if breaker_snapshot.triggered:
            if _effect_receipts_from_batch_receipts(receipts_as_dicts):
                # A partially successful mutation batch is already consumed:
                # its effect receipts are authoritative and cannot be replaced
                # by a second physical ToolBatch in the same logical turn.
                # Preserve the committed effects and surface the residual
                # failures to same-task continuation instead of requesting a
                # replacement batch that would violate the kernel's own
                # single-batch invariant (live L3-24 r16).
                ledger.anomaly_flags.append(
                    {
                        "type": "TOOL_FAILURE_CIRCUIT_BREAKER_PARTIAL_PROGRESS_PRESERVED",
                        "turn_id": breaker_snapshot.turn_id,
                        "batch_failures": breaker_snapshot.batch_failures,
                        "consecutive_failures": breaker_snapshot.consecutive_failures,
                        "total_failures": breaker_snapshot.total_failures,
                        "trigger_reason": breaker_snapshot.trigger_reason,
                        "triggered_dimension": breaker_snapshot.triggered_dimension or "none",
                    }
                )
                logger.warning(
                    "Tool failure circuit breaker observed partial authoritative progress; "
                    "preserving receipts without replacement batch. turn_id=%s failures=%s dimension=%s",
                    breaker_snapshot.turn_id,
                    breaker_snapshot.batch_failures,
                    breaker_snapshot.triggered_dimension or "none",
                )
            else:
                batch_cancel_token.cancel("tool_failure_circuit_breaker_triggered")
                raise RuntimeError(
                    "single_batch_contract_violation: tool_failure_circuit_breaker_triggered "
                    f"turn_id={breaker_snapshot.turn_id} "
                    f"batch_failures={breaker_snapshot.batch_failures} "
                    f"consecutive_failures={breaker_snapshot.consecutive_failures} "
                    f"total_failures={breaker_snapshot.total_failures} "
                    f"consecutive_threshold={breaker_snapshot.consecutive_threshold} "
                    f"total_threshold={breaker_snapshot.total_threshold} "
                    f"trigger_reason={breaker_snapshot.trigger_reason} "
                    f"triggered_dimension={breaker_snapshot.triggered_dimension or 'none'}"
                )

        # === Phase 4b: 执行完成 ===
        state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTED)
        ledger.state_history.append(("TOOL_BATCH_EXECUTED", int(time.time() * 1000)))
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "tool_batch_completed",
                {
                    "receipt_count": len(receipts_as_dicts),
                    "pending_async_count": sum(int(r.get("pending_async_count", 0)) for r in receipts_as_dicts),
                },
            )
        )
        pending_async_count = sum(int(r.get("pending_async_count", 0)) for r in receipts_as_dicts)
        if pending_async_count > 0:
            merged_batch_receipt = _merge_batch_receipts(receipts_as_dicts)
            workflow_context = build_workflow_handoff_context(
                decision=decision,
                receipts=receipts_as_dicts,
                ledger=ledger,
                handoff_reason="async_operation",
                handoff_source="async_pending_receipt",
            )
            if stream:
                return {
                    "kind": "handoff_workflow",
                    "batch_receipt": merged_batch_receipt,
                    "workflow_context": workflow_context,
                }
            return await self.handoff_handler.handle_handoff(
                decision,
                state_machine,
                ledger,
                workflow_context=workflow_context,
                handoff_reason="async_operation",
                batch_receipt=merged_batch_receipt,
            )

        # === Phase 5: 确定下一步 ===
        finalize_mode = decision.get("finalize_mode")

        if finalize_mode == FinalizeMode.NONE:
            from polaris.cells.roles.kernel.internal.transaction.finalization import (
                FinalizationHandler,
            )

            return FinalizationHandler.complete_with_tool_results(
                decision, receipts_as_dicts, state_machine, ledger, self.emit_event
            )

        elif finalize_mode == FinalizeMode.LOCAL:
            from polaris.cells.roles.kernel.internal.transaction.finalization import (
                FinalizationHandler,
            )

            return FinalizationHandler.finalize_local(
                decision, receipts_as_dicts, state_machine, ledger, self.emit_event
            )

        elif finalize_mode == FinalizeMode.LLM_ONCE:
            # === Mutation Bypass: 阻止贴代码逃逸 ===
            # 如果 delivery mode 为 MATERIALIZE_CHANGES 但本批次没有写工具调用，
            # 则阻止进入 LLM_ONCE（tool_choice=none 会剥夺写工具能力），
            # 返回 BLOCKED 状态让上层决定后续动作。
            latest_user_request = extract_latest_user_message(context)
            if self._should_block_llm_once_finalization(ledger, invocations, latest_user_request):
                return self._build_mutation_bypass_result(
                    decision, state_machine, ledger, receipts_as_dicts, stream=stream
                )
            return await self.finalization_handler.execute_llm_once(
                decision, receipts_as_dicts, state_machine, ledger, context, stream=stream
            )

        else:
            raise ValueError(f"Unknown finalize_mode: {finalize_mode}")
