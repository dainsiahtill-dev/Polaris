"""工具批次执行器 — 负责 TOOL_BATCH 决策的权威执行与最终化路由。

包含:
- ToolBatchExecutor: 主执行器类
- 路径重写与 receipt 记录等辅助函数
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast

from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    receipts_have_stale_edit_failure,
    resolve_mutation_target_guard_violation,
    tool_batch_has_write_invocation,
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
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import record_receipts_to_ledger
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
from polaris.cells.roles.kernel.public.turn_events import TurnEvent, TurnPhaseEvent

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


def _resolve_existing_workspace_file(*, workspace: str, raw_path: str) -> str | None:
    import os

    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return None
    if normalized.startswith("file://"):
        normalized = normalized[len("file://") :].lstrip("/")

    workspace_real = os.path.realpath(workspace or ".")
    full_path = os.path.realpath(os.path.join(workspace_real, normalized))

    # 防御目录遍历：解析后的路径必须在 workspace 内部
    if not (full_path.startswith(workspace_real + os.sep) or full_path == workspace_real):
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

    def _build_tool_batch_runtime(
        self,
        workspace: str = ".",
        *,
        batch_idempotency_key: str = "",
        side_effect_class: str = "readonly",
    ) -> ToolBatchRuntime:
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
                batch_idempotency_key=batch_idempotency_key,
                side_effect_class=side_effect_class,  # type: ignore[arg-type]
            ),
        )

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
                    logger.warning(
                        "Tool %s is marked as both read and write, bypassing strict barrier", overlap
                    )
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

        latest_user_request = extract_latest_user_message(context)
        requires_mutation = enforce_mutation_write_guard and self.requires_mutation_intent(latest_user_request)
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        if requires_mutation and not tool_batch_has_write_invocation(invocations):
            if guard_mode == "strict":
                raise RuntimeError(
                    "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch"
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
            receipts = await self._build_tool_batch_runtime(workspace).execute_batch(
                replay_batch,
                TurnId(turn_id),
            )
            receipts_as_dicts.extend(cast("dict", r) for r in receipts)

        record_receipts_to_ledger(receipts_as_dicts, ledger)

        if (
            requires_mutation
            and tool_batch_has_write_invocation(invocations)
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
                    "batch_receipt": receipts_as_dicts[0] if receipts_as_dicts else None,
                    "workflow_context": workflow_context,
                }
            return await self.handoff_handler.handle_handoff(
                decision,
                state_machine,
                ledger,
                workflow_context=workflow_context,
                handoff_reason="async_operation",
                batch_receipt=receipts_as_dicts[0] if receipts_as_dicts else None,
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
        if tool_batch_has_write_invocation(invocations):
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
        if tool_batch_has_write_invocation(invocations):
            return False
        if ledger.mutation_obligation.mutation_satisfied:
            return False
        contract = ledger.delivery_contract
        if ledger.tool_batch_count <= 2:
            logger.debug(
                "intent-mismatch-allow-exploration: intent detected mutation but "
                "delivery contract mode=%s and tool_batch_count=%d <= 2. "
                "Allowing exploration before enforcing MATERIALIZE_CHANGES. turn_id=%s",
                contract.mode.value if hasattr(contract.mode, "value") else contract.mode,
                ledger.tool_batch_count,
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
        if stream:
            return {
                "kind": "continue_multi_turn",
                "batch_receipt": receipts[0] if receipts else None,
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
            "batch_receipt": receipts[0] if receipts else None,
            "finalization": {
                "turn_id": turn_id,
                "mode": "blocked",
                "blocked_reason": blocked_reason.value if blocked_reason else None,
                "blocked_detail": blocked_detail,
                "needs_followup_workflow": True,
                "workflow_reason": "mutation_bypass_blocked",
            },
        }
