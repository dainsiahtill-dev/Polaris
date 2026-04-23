"""工具批次执行器 — 负责 TOOL_BATCH 决策的权威执行与最终化路由。

包含:
- ToolBatchExecutor: 主执行器类
- 路径重写与 receipt 记录等辅助函数
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, NoReturn, cast

from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    extract_target_file_from_invocation_args,
    extract_target_files_from_message,
    receipts_have_stale_edit_failure,
    resolve_mutation_target_guard_violation,
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import build_workflow_handoff_context
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent as _default_requires_mutation_intent,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    extract_tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import extract_latest_user_message
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolExecutionMode,
    TurnDecision,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent, TurnEvent, TurnPhaseEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------


def _tool_requires_existing_file(tool_name: str) -> bool:
    return tool_name in {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "file_exists",
        "edit_file",
        "precision_edit",
    }


_DIRECT_READ_TOOLS = {
    "read_file",
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_read_around",
    "repo_read_range",
}


def _normalize_file_reference_path(raw_path: str) -> str:
    """规范化工具调用中的文件路径字符串。

    处理 Windows 常见的混合格式：
    - file:// URI
    - 反斜杠分隔符
    - 误带前导斜杠的绝对盘符路径，例如 /C:/workspace/file.txt
    """

    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("file://"):
        normalized = normalized[len("file://") :].lstrip("/")
    if len(normalized) >= 4 and normalized[0] == "/" and normalized[2:4] == ":/" and normalized[1].isalpha():
        normalized = normalized[1:]
    return normalized


def _is_path_within_workspace(*, workspace_real: str, candidate_real: str) -> bool:
    try:
        return os.path.commonpath([workspace_real, candidate_real]) == workspace_real
    except ValueError:
        return False


def _resolve_existing_workspace_file(*, workspace: str, raw_path: str) -> str | None:
    normalized = _normalize_file_reference_path(raw_path)
    if not normalized:
        return None

    workspace_real = os.path.realpath(workspace or ".")
    if os.path.isabs(normalized):
        full_path = os.path.realpath(normalized)
    else:
        full_path = os.path.realpath(os.path.join(workspace_real, normalized))

    # 防御目录遍历：解析后的路径必须在 workspace 内部
    if not _is_path_within_workspace(workspace_real=workspace_real, candidate_real=full_path):
        logger.warning("Path traversal attempt blocked: %s", raw_path)
        return None

    if not os.path.isfile(full_path):
        return None

    # 返回相对于 workspace 的标准化路径，保持与原接口一致
    rel = os.path.relpath(full_path, workspace_real).replace("\\", "/")
    return rel


def rewrite_existing_file_paths_in_invocations(
    *,
    turn_id: str,
    workspace: str,
    invocations: list[Any],
) -> list[Any]:
    """将 invocation 中的文件路径重写为 workspace 内实际存在的路径。"""
    rewritten: list[Any] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        if not _tool_requires_existing_file(tool_name):
            rewritten.append(invocation)
            continue
        if isinstance(invocation, Mapping):
            raw_arguments = invocation.get("arguments")
        else:
            raw_arguments = getattr(invocation, "arguments", None)
        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        if not arguments:
            rewritten.append(invocation)
            continue
        rewritten_invocation = invocation
        for path_key in ("file", "path", "filepath", "target"):
            raw_path = arguments.get(path_key)
            if not isinstance(raw_path, str):
                continue
            normalized_raw_path = raw_path.strip().replace("\\", "/")
            if not normalized_raw_path:
                continue
            resolved_path = _resolve_existing_workspace_file(workspace=workspace, raw_path=normalized_raw_path)
            if not resolved_path or resolved_path == normalized_raw_path:
                continue
            new_arguments = dict(arguments)
            new_arguments[path_key] = resolved_path
            if isinstance(invocation, Mapping):
                updated = dict(invocation)
                updated["arguments"] = new_arguments
                rewritten_invocation = cast(Any, updated)
            else:
                rewritten_invocation = {
                    "call_id": str(getattr(invocation, "call_id", "") or ""),
                    "tool_name": tool_name,
                    "arguments": new_arguments,
                    "effect_type": getattr(invocation, "effect_type", None),
                    "execution_mode": getattr(invocation, "execution_mode", None),
                }
            logger.warning(
                "mutation-path-correction: turn_id=%s tool=%s rewrite %s -> %s",
                turn_id,
                tool_name,
                normalized_raw_path,
                resolved_path,
            )
            break
        rewritten.append(rewritten_invocation)
    return rewritten


# ---------------------------------------------------------------------------
# Receipt 辅助
# ---------------------------------------------------------------------------


def _merge_batch_receipts(receipts: list[Any]) -> dict[str, Any] | None:
    """Merge multiple per-tool receipts into a single canonical batch receipt."""
    return merge_batch_receipts(receipts)


# ---------------------------------------------------------------------------
# ToolBatchExecutor
# ---------------------------------------------------------------------------


class ToolBatchExecutor:
    """工具批次执行器 — 负责权威工具批次执行与最终化路由。"""

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
    ) -> None:
        self.tool_runtime = tool_runtime
        self.config = config
        self.emit_event = emit_event
        self.guard_assert_single_tool_batch = guard_assert_single_tool_batch
        self.finalization_handler = finalization_handler
        self.handoff_handler = handoff_handler
        self.requires_mutation_intent = requires_mutation_intent or _default_requires_mutation_intent
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

    def _build_tool_batch_runtime(
        self,
        workspace: str = ".",
        *,
        batch_idempotency_key: str = "",
        side_effect_class: str = "readonly",
        turn_id: str = "",
    ) -> ToolBatchRuntime:
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
                turn_id=turn_id,
                batch_idempotency_key=batch_idempotency_key,
                side_effect_class=side_effect_class,  # type: ignore[arg-type]
            ),
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
        except Exception:  # noqa: BLE001
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

    async def execute_tool_batch(
        self,
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
        workspace = str(metadata.get("workspace", ".")).strip() or "."
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
                if tname and tname not in allowed_tool_names:
                    disallowed_tools.append(tname)
            if disallowed_tools:
                raise RuntimeError(
                    "single_batch_contract_violation: retry batch used tools outside narrowed set: "
                    + ", ".join(sorted(set(disallowed_tools)))
                )

        # --- READ-WRITE BARRIER LOGIC ---
        # BUG-NEW-1 fix: the Barrier must be bypassed in Benchmark single-batch mode.
        # Benchmark contracts explicitly require read + write in the SAME batch
        # (e.g., Ordered groups: [edit_file] -> [read_file]).  Applying the Barrier
        # here causes an unresolvable retry loop that ultimately forces the model into
        # a destructive write_file overwrite (see BUG-NEW-2 below).
        _latest_user_for_barrier = extract_latest_user_message(context)
        _is_benchmark_batch = "[Benchmark Tool Contract]" in _latest_user_for_barrier

        if not _is_benchmark_batch:
            # Normal (non-benchmark) execution: enforce the Read-Write Barrier.
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

        if _is_content_gathered_phase and _is_materialize and not _has_write:
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

        _exploration_streak_hard_block = "EXPLORATION_STREAK_HARD_BLOCK" in _latest_user_for_barrier
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
            # Partial block: replace blocked tools with error receipts, keep valid tools
            modified_invocations = []
            for inv in invocations:
                tname = extract_invocation_tool_name(inv)
                if tname in _broad_exploration_tools:
                    modified_invocations.append(
                        {
                            **inv,
                            "_implementing_phase_blocked": True,
                            "_blocked_reason": f"Tool '{tname}' blocked in implementing phase. Use write tools.",
                        }
                    )
                else:
                    modified_invocations.append(inv)
            invocations = modified_invocations
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

        latest_user_request = extract_latest_user_message(context)
        requires_mutation = enforce_mutation_write_guard and self.requires_mutation_intent(latest_user_request)
        known_target_files = extract_target_files_from_message(latest_user_request)
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
            violation = resolve_mutation_target_guard_violation(latest_user_request, invocations)
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

        # === Phase 4a: 开始执行 ===
        if state_machine.current_state != TurnState.TOOL_BATCH_EXECUTING:
            state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTING)
        ledger.state_history.append(("TOOL_BATCH_EXECUTING", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "tool_batch_started", {"tool_count": len(invocations)}))

        # Speculative Execution Kernel v2 integration
        receipts_as_dicts: list[dict] = []
        replay_invocations: list[Any] = []

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
                except Exception:  # noqa: BLE001
                    resolution = {"action": "replay", "result": None, "error": None}
                action = str(resolution.get("action", "replay"))
                is_write_tool = WriteToolPhases.is_write_tool(tool_name)
                if action in ("adopt", "join") and not is_write_tool:
                    adopted_result = {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": resolution.get("result"),
                        "error": None,
                        "execution_time_ms": 0,
                        "effect_receipt": None,
                    }
                    raw_result = {
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": resolution.get("result"),
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

        # 对未命中的 invocation 走 authoritative batch 执行
        if replay_invocations:
            replay_batch = ToolBatch(
                batch_id=tool_batch.get("batch_id", BatchId(f"{turn_id}_replay")),
                parallel_readonly=[
                    inv
                    for inv in replay_invocations
                    if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL
                ],
                readonly_serial=[
                    inv for inv in replay_invocations if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL
                ],
                serial_writes=[
                    inv for inv in replay_invocations if inv.get("execution_mode") == ToolExecutionMode.WRITE_SERIAL
                ],
                async_receipts=[
                    inv for inv in replay_invocations if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT
                ],
            )
            receipts = await self._build_tool_batch_runtime(workspace, turn_id=turn_id).execute_batch(
                replay_batch,
                TurnId(turn_id),
            )
            receipts_as_dicts.extend(normalize_batch_receipts(receipts))

        record_receipts_to_ledger(receipts_as_dicts, ledger)

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

        # FIX-20250421: PhaseManager — 基于工具执行结果驱动阶段流转
        # 只有在非 benchmark 模式下才使用 PhaseManager（benchmark 有独立规则）
        _latest_user = extract_latest_user_message(context)
        _is_benchmark = "[Benchmark Tool Contract]" in str(_latest_user)
        if not _is_benchmark:
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
