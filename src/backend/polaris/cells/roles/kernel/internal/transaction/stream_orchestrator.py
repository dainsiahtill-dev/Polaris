"""流式决策与 Turn 执行编排器。

职责：
- 流式 LLM 调用（call_llm_for_decision_stream）
- 流式 Turn 执行编排（execute_turn_stream）
- speculative 任务管理（drain_speculative_tasks）
- 拒绝响应检测（is_refusal_response）

设计原则：
- 所有外部依赖通过 __init__ 注入，零隐式耦合
- 公共流式入口只做参数装配，执行路径收敛到本 orchestrator
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal, cast

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_count_from_facts,
    native_tool_call_facts_from_sources,
    project_completion_dispatch_evidence_to_metadata,
    project_native_tool_call_facts_to_metadata,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    native_tool_calls_from_response,
    provider_response_hash as derive_provider_response_hash,
    restrict_tool_definitions_to_write,
    stream_tool_call_signature,
    supersede_partial_tool_calls,
    upsert_stream_native_tool_call,
)
from polaris.cells.roles.kernel.internal.speculation.cancel import CancellationCoordinator
from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import (
    build_tool_dispatch_dropped_anomaly,
)
from polaris.cells.roles.kernel.internal.transaction.decode_corrective import (
    build_corrective_context,
    evaluate_decode_corrective,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryContract, DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.handoff_handlers import HandoffHandler
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase
from polaris.cells.roles.kernel.internal.transaction.read_strategy import (
    ReadStrategy,
    determine_optimal_strategy,
    is_content_truncated,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import normalize_batch_receipt
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import RetryOrchestrator
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
    extract_continuation_prompt_metadata,
    extract_latest_user_message,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    ToolBatchEvent,
    TurnEvent,
    TurnPhaseEvent,
)
from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delivery Resolver 关键词配置
# ---------------------------------------------------------------------------

# 中文关键词列表：当 prompt 中包含这些词，且已读取目标文件时，
# 自动推断为 MATERIALIZE_CHANGES 交付模式。
# 支持变体：完善/完善化/修改/改动/优化/改进/补充
_MATERIALIZE_KEYWORDS: list[str] = [
    "完善",
    "完善化",
    "修改",
    "改动",
    "优化",
    "改进",
    "补充",
]

# 预编译正则表达式，支持关键词变体匹配
_MATERIALIZE_KEYWORDS_RE: re.Pattern[str] = re.compile("|".join(re.escape(kw) for kw in _MATERIALIZE_KEYWORDS))


# ---------------------------------------------------------------------------
# 拒绝响应检测
# ---------------------------------------------------------------------------


def is_refusal_response(response: RawLLMResponse) -> bool:
    """检测 LLM 响应是否为拒绝执行（refusal）."""
    native_calls = response.get("native_tool_calls") or response.get("tool_calls") or []
    if native_calls:
        return False
    content = str(response.get("content") or "").strip()
    if not content:
        return False
    refusal_markers = (
        "i cannot",
        "i can't",
        "i'm sorry",
        "i am sorry",
        "i cannot assist",
        "i can't assist",
        "不能",
        "禁止",
        "不允许",
        "无法",
        "对不起",
        "抱歉",
    )
    lowered = content.lower()
    return any(marker in lowered for marker in refusal_markers)


def _extract_read_tools_from_receipt(batch_receipt: dict[str, Any] | None) -> list[str]:
    """从 batch_receipt 中提取真正的文件读取工具名称列表（去重）。

    FIX-20250421: 区分 exploration tools（glob/repo_rg/grep）和 actual read tools（read_file/repo_read_*）。
    只有真正读取了文件内容的工具才算 read tools。
    """
    if not batch_receipt:
        return []
    results = batch_receipt.get("results") or batch_receipt.get("raw_results") or []
    seen: set[str] = set()
    reads: list[str] = []
    for item in results:
        name = str(item.get("tool_name") or "").strip()
        if not name:
            continue
        if name in seen:
            continue
        # FIX-20250421: 只识别真正读取文件内容的工具
        # Exploration tools (glob, repo_rg, grep, search_code) are NOT read tools
        if name.startswith(
            ("read_file", "repo_read_head", "repo_read_slice", "repo_read_tail", "repo_read_around", "repo_read_range")
        ):
            seen.add(name)
            reads.append(name)
    return reads


def _has_write_tools_in_receipt(batch_receipt: dict[str, Any] | None) -> bool:
    """检测 batch_receipt 中是否包含成功的写工具调用。"""
    if not batch_receipt:
        return False
    results = batch_receipt.get("results") or batch_receipt.get("raw_results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name") or "").strip()
        status = str(item.get("status") or "").strip()
        if name in WRITE_TOOLS and status == "success":
            return True
    return False


# ---------------------------------------------------------------------------
# Read Strategy 自动切换
# ---------------------------------------------------------------------------


def _should_use_slice_mode(file_path: str, content_length: int) -> bool:
    """判断是否应该使用分段读取模式。

    当文件大小超过阈值（默认100KB）时，建议使用 repo_read_slice 分段读取。

    Args:
        file_path: 文件路径
        content_length: 内容长度（字节）

    Returns:
        True 如果应该使用分段读取模式
    """
    from polaris.cells.roles.kernel.internal.transaction.read_strategy import _should_use_slice_mode as _check_slice

    should_slice, _ = _check_slice(file_path, content_length=content_length)
    return should_slice


def _detect_truncation_heuristics(content: str | None, result_metadata: dict[str, Any] | None = None) -> bool:
    """检测内容是否被截断的启发式函数。

    检查返回内容是否以 "..." 或 "[truncated]" 结尾，
    或检查 result_metadata 中的 truncated 标记。

    Args:
        content: 读取到的内容字符串
        result_metadata: 工具返回的元数据字典，可选

    Returns:
        True 如果内容被截断
    """
    is_truncated, _ = is_content_truncated(content, result_metadata)
    return is_truncated


class ReadStrategyAdapter:
    """Read Strategy 适配器 —— 在工具结果返回后自动决策读取策略。"""

    def __init__(self, threshold_bytes: int = 100 * 1024) -> None:
        self.threshold_bytes = threshold_bytes

    def analyze_tool_result(self, tool_name: str, result: dict[str, Any]) -> ReadStrategy | None:
        """分析工具执行结果，决定是否需要切换读取策略。

        Args:
            tool_name: 工具名称
            result: 工具执行结果

        Returns:
            ReadStrategy 如果需要切换策略，None 如果保持当前策略
        """
        # 只处理 read_file 工具的截断情况
        if tool_name != "read_file":
            return None

        content = result.get("content")
        file_path = result.get("file", "")
        content_length = len(content.encode("utf-8")) if content else 0

        # 使用 read_strategy 模块的决策逻辑
        strategy = determine_optimal_strategy(
            file_path=file_path,
            content=content,
            result_metadata=result,
            file_size_bytes=content_length,
        )

        if strategy.use_slice_mode:
            logger.info(
                "read_strategy_switch: file=%s reason=%s",
                file_path,
                strategy.reason,
            )

        return strategy

    def build_slice_replacements(
        self,
        file_path: str,
        total_lines: int,
        slice_size: int = 200,
    ) -> list[dict[str, Any]]:
        """构��分段读取的替换工具调用列表。

        Args:
            file_path: 文件路径
            total_lines: 文件总行数
            slice_size: 每段读取的行数

        Returns:
            工具调用参数列表
        """
        from polaris.cells.roles.kernel.internal.transaction.read_strategy import calculate_slice_ranges

        ranges = calculate_slice_ranges(total_lines, slice_size)
        return [
            {
                "tool_name": "repo_read_slice",
                "arguments": {
                    "file": file_path,
                    "start": start,
                    "end": end,
                },
            }
            for start, end in ranges
        ]


def _build_continue_visible_content(
    read_tools: list[str],
    current_progress: str = "content_gathered",
    force_read_required: bool = False,
    delivery_mode: str | None = None,
    turns_in_phase: int = 0,
    max_turns_per_phase: int = 3,
    modification_contract_status: str = "empty",
    conversation_context: list[dict[str, Any]] | None = None,
    batch_receipt: dict[str, Any] | None = None,
) -> str:
    """构建 continue_multi_turn 的 visible_content，内嵌 SESSION_PATCH。

    FIX-20250421: 基于 PhaseManager 的真实阶段生成提示语，不再依赖字符串匹配。
    这是系统提示 LLM 当前约束的唯一合法入口。

    Args:
        read_tools: 已调用的读工具列表（真正的 read_file，不是 glob）
        current_progress: PhaseManager 的当前阶段值
        delivery_mode: continuation prompt contract 的显式交付模式
        conversation_context: 对话上下文，用于检测 SUPER_MODE 标记
        batch_receipt: 当前 turn 的 batch receipt，用于检测写工具执行
    """
    # FIX-20250422-SUPER: 检测 SUPER_MODE 标记 — CLI SUPER 模式已提供完整计划，
    # 不应要求 LLM 再次声明 modification_plan。
    _is_super_mode = False
    if conversation_context:
        _super_markers = (
            "[SUPER_MODE_HANDOFF]",
            "[/SUPER_MODE_HANDOFF]",
            "[SUPER_MODE_DIRECTOR_CONTINUE]",
            "[/SUPER_MODE_DIRECTOR_CONTINUE]",
        )
        for message in conversation_context:
            if not isinstance(message, dict):
                continue
            content = str(message.get("content", ""))
            if any(m in content for m in _super_markers):
                _is_super_mode = True
                break

    # FIX-20250421: 使用 PhaseManager 的阶段生成约束提示
    try:
        phase = Phase(current_progress)
    except ValueError:
        phase = Phase.EXPLORING

    # FIX-20250422-v4: SUPER_MODE + MATERIALIZE_CHANGES 强制写工具检测
    # 如果当前 turn 没有写工具且处于 VERIFYING/IMPLEMENTING 阶段，强制回到 IMPLEMENTING
    _is_materialize = str(delivery_mode or "").lower() == "materialize_changes"
    _has_write_this_turn = _has_write_tools_in_receipt(batch_receipt)
    _force_implementing = (
        _is_super_mode
        and _is_materialize
        and not _has_write_this_turn
        and phase
        in (
            Phase.VERIFYING,
            Phase.IMPLEMENTING,
        )
    )

    # 基于 Phase 的约束提示
    if phase == Phase.VERIFYING and not _force_implementing:
        instruction = (
            "当前阶段：验证（VERIFYING）。请运行测试或手动验证修复效果。严禁调用探索工具（glob/repo_rg/repo_tree 等）。"
        )
        visible_prefix = "验证阶段继续"
    elif phase == Phase.IMPLEMENTING or _force_implementing:
        if _force_implementing:
            # FIX-20250422-v4: 强制写模式 — LLM 尚未执行任何写操作，必须立即写入
            instruction = (
                "当前阶段：强制执行（IMPLEMENTING）。你尚未执行任何文件修改。"
                "MANDATORY: 立即调用 write_file/edit_file/edit_blocks/append_to_file 实施修改。"
                "严禁继续读取文件。严禁输出文字分析。严禁调用 execute_command 逃避写操作。"
                "HARD GATE: 无写工具调用 = 拒绝。文本-only 完成 = 拒绝。"
                "\n\n【工具调用示例】"
                "\n示例1 - 创建新文件: write_file(file='path/to/file.py', content='...')"
                "\n示例2 - 编辑文件: edit_file(file='path/to/file.py', old_string='...', new_string='...')"
                "\n示例3 - 追加内容: append_to_file(file='path/to/file.py', content='...')"
                "\n\n立即执行上述工具之一，不要输出任何文字分析！"
            )
            visible_prefix = "强制写阶段 — 立即执行修改"
        else:
            instruction = (
                "当前阶段：实现（IMPLEMENTING）。你正在执行代码修改。"
                "MANDATORY: 调用 write_file/edit_file/apply_diff 完成修改。"
                "严禁调用 glob/repo_rg/repo_tree 等探索工具。"
            )
            visible_prefix = "写阶段继续"
    elif phase == Phase.CONTENT_GATHERED:
        # FIX-20250422-v3: 基于 ModificationContract 状态生成认知指令
        # FIX-20250422-SUPER: SUPER_MODE 下跳过 plan 要求，直接执行
        if modification_contract_status == "ready" or _is_super_mode:
            if _is_super_mode:
                instruction = (
                    "当前阶段：SUPER_MODE 执行阶段（CONTENT_GATHERED）。"
                    "PM 已生成完整计划，你的唯一职责是执行。"
                    "MANDATORY: 立即调用 write_file/edit_file/edit_blocks 实施修改。"
                    "禁止继续读取文件。禁止输出文字计划。禁止询问确认。"
                    "HARD GATE: 无写工具调用 = 拒绝。文本-only 完成 = 拒绝。"
                )
            else:
                instruction = (
                    "当前阶段：执行准备就绪（CONTENT_GATHERED + PLAN READY）。"
                    "你的修改计划已确认。MANDATORY: 立即调用 write_file/edit_file 按计划执行修改。"
                    "禁止继续读取文件。"
                )
            visible_prefix = "计划就绪，开始执行"
        else:
            instruction = (
                "当前阶段：内容已收集（CONTENT_GATHERED）。你已读取文件内容。"
                "在执行修改之前，你必须先在 SESSION_PATCH 中声明 modification_plan。"
                '格式: "modification_plan": [{"target_file": "path", "action": "具体修改描述"}]'
                "\n一旦计划确认，你将被允许执行写工具。"
            )
            if turns_in_phase > 1:
                remaining = max(0, max_turns_per_phase - turns_in_phase)
                instruction += (
                    f"\n\n⚠️ 警告：你已在此阶段停留 {turns_in_phase} 回合。"
                    f"还剩 {remaining} 回合制定计划，超时后将强制要求写操作。"
                )
            visible_prefix = "等待修改计划"
    elif phase == Phase.DONE:
        instruction = "当前阶段：已完成（DONE）。请汇总结果并以 END_SESSION 结束。"
        visible_prefix = "完成阶段"
    else:  # EXPLORING
        instruction = (
            "当前阶段：探索（EXPLORING）。允许使用 glob/repo_rg 定位文件。"
            "找到目标文件后，必须调用 read_file 读取内容后才能修改。"
        )
        # FIX-20250421-v2: 在 MATERIALIZE_CHANGES 模式下，强制要求继续执行，不能返回文本
        if force_read_required:
            instruction += (
                "\n\n🚨 CRITICAL: 当前任务要求代码修改（MATERIALIZE_CHANGES）。"
                "你只执行了探索工具（glob/repo_rg），尚未读取任何文件内容。"
                "MANDATORY: 必须立即调用 read_file 读取目标文件，然后执行 write_file/edit_file 完成修改。"
                "严禁返回文本分析或建议——必须通过工具执行实际修改！"
            )
        visible_prefix = "探索阶段继续"

    patch: dict[str, Any] = {
        # FIX-20250421: 不再强制覆盖 task_progress，保持当前阶段
        # 只在有读工具时记录 recent_reads
    }
    if delivery_mode:
        patch["delivery_mode"] = delivery_mode
    if read_tools:
        patch["recent_reads"] = read_tools

    return (
        f"[系统提示] 多回合工作流继续：{visible_prefix}。\n"
        f"{instruction}\n"
        "严禁输出文字计划或代码块。必须调用工具！\n"
        f"<SESSION_PATCH>\n{json.dumps(patch, ensure_ascii=False)}\n</SESSION_PATCH>"
    )


def _build_delivery_contract_from_mode(mode: DeliveryMode) -> DeliveryContract:
    """根据显式 delivery_mode 构建 delivery contract。"""
    if mode == DeliveryMode.MATERIALIZE_CHANGES:
        return DeliveryContract(
            mode=DeliveryMode.MATERIALIZE_CHANGES,
            requires_mutation=True,
            requires_verification=False,
            allow_inline_code=False,
            allow_patch_proposal=False,
        )
    if mode == DeliveryMode.PROPOSE_PATCH:
        return DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            requires_mutation=False,
            requires_verification=False,
            allow_inline_code=True,
            allow_patch_proposal=True,
        )
    return DeliveryContract(
        mode=DeliveryMode.ANALYZE_ONLY,
        requires_mutation=False,
        requires_verification=False,
        allow_inline_code=True,
        allow_patch_proposal=False,
    )


def _resolve_continuation_delivery_contract(
    *,
    raw_user: str,
    original_delivery_mode: str | None,
    parsed_progress: str,
    recent_reads: list[str] | None = None,
) -> DeliveryContract:
    """Resolve continuation turn delivery contract from prompt metadata first.

    The continuation prompt is the canonical contract surface for follow-up turns.
    When it carries explicit delivery_mode metadata, use it directly instead of
    relying on a fresh ledger carrying prior frozen state.

    Args:
        raw_user: 原始用户 prompt 文本
        original_delivery_mode: 原始的交付模式（用于回退）
        parsed_progress: 解析出的当前进度阶段
        recent_reads: 已读取的文件列表（用于关键词检测），可选

    Returns:
        DeliveryContract: 解析出的交付契约

    新增逻辑（关键词检测）：
        当满足以下条件时，自动推断为 MATERIALIZE_CHANGES 模式：
        1. prompt 中包含中文关键词（完善/修改/优化/补充等变体）
        2. recent_reads 非空（表示已读取目标文件）
    """
    continuation_metadata = extract_continuation_prompt_metadata(raw_user)
    explicit_delivery_mode = str(continuation_metadata.get("delivery_mode") or "").strip().lower()
    if explicit_delivery_mode:
        try:
            mode = DeliveryMode(explicit_delivery_mode)
        except ValueError:
            logger.warning(
                "continuation_prompt_delivery_mode_invalid: progress=%s raw_mode=%s",
                parsed_progress,
                explicit_delivery_mode,
            )
        else:
            logger.debug(
                "continuation_prompt_delivery_mode: progress=%s mode=%s (prompt_metadata)",
                parsed_progress,
                mode.name,
            )
            return _build_delivery_contract_from_mode(mode)

    # FIX-20250421: Also check for <DeliveryMode> XML tag injected by session_orchestrator.py
    # in the Goal block. This ensures delivery_mode survives across turns even when
    # the SESSION_PATCH block is not present or doesn't contain delivery_mode.
    delivery_mode_match = re.search(r"<DeliveryMode>(.*?)</DeliveryMode>", raw_user, re.IGNORECASE)
    if delivery_mode_match:
        xml_delivery_mode = delivery_mode_match.group(1).strip().lower()
        if xml_delivery_mode:
            try:
                mode = DeliveryMode(xml_delivery_mode)
            except ValueError:
                logger.warning(
                    "continuation_prompt_delivery_mode_invalid_xml: progress=%s raw_mode=%s",
                    parsed_progress,
                    xml_delivery_mode,
                )
            else:
                logger.debug(
                    "continuation_prompt_delivery_mode: progress=%s mode=%s (xml_tag)",
                    parsed_progress,
                    mode.name,
                )
                return _build_delivery_contract_from_mode(mode)

    if original_delivery_mode == DeliveryMode.MATERIALIZE_CHANGES.value:
        logger.debug(
            "continuation_prompt_delivery_mode: progress=%s mode=MATERIALIZE_CHANGES (original_preserved)",
            parsed_progress,
        )
        return _build_delivery_contract_from_mode(DeliveryMode.MATERIALIZE_CHANGES)
    if original_delivery_mode == DeliveryMode.ANALYZE_ONLY.value:
        logger.debug(
            "continuation_prompt_delivery_mode: progress=%s mode=ANALYZE_ONLY (original_preserved)",
            parsed_progress,
        )
        return _build_delivery_contract_from_mode(DeliveryMode.ANALYZE_ONLY)
    if original_delivery_mode == DeliveryMode.PROPOSE_PATCH.value:
        logger.debug(
            "continuation_prompt_delivery_mode: progress=%s mode=PROPOSE_PATCH (original_preserved)",
            parsed_progress,
        )
        return _build_delivery_contract_from_mode(DeliveryMode.PROPOSE_PATCH)

    # FIX-20250421-v3: 关键词检测分支
    # 当 prompt 中包含中文关键词（完善/修改/优化/补充等），且已读取目标文件时，
    # 自动推断为 MATERIALIZE_CHANGES 模式
    if recent_reads and _MATERIALIZE_KEYWORDS_RE.search(raw_user):
        logger.debug(
            "continuation_prompt_delivery_mode: progress=%s mode=MATERIALIZE_CHANGES (keyword_detected: recent_reads=%d)",
            parsed_progress,
            len(recent_reads),
        )
        return _build_delivery_contract_from_mode(DeliveryMode.MATERIALIZE_CHANGES)

    if parsed_progress == "implementing":
        logger.debug(
            "continuation_prompt_delivery_mode: progress=%s mode=MATERIALIZE_CHANGES (fallback)",
            parsed_progress,
        )
        return _build_delivery_contract_from_mode(DeliveryMode.MATERIALIZE_CHANGES)

    logger.debug(
        "continuation_prompt_delivery_mode: progress=%s mode=ANALYZE_ONLY (fallback)",
        parsed_progress,
    )
    return _build_delivery_contract_from_mode(DeliveryMode.ANALYZE_ONLY)


# ---------------------------------------------------------------------------
# Speculative 任务清理
# ---------------------------------------------------------------------------


async def drain_speculative_tasks(
    tasks: list[tuple[str, asyncio.Task[dict[str, Any]]]],
    *,
    ledger: TurnLedger | None = None,
    timeout_s: float = 0.2,
    shadow_engine: StreamShadowEngine | None = None,
) -> None:
    """Drain speculative tasks and cancel leftovers to avoid task leaks."""
    if tasks:
        task_list = [task for _, task in tasks]
        call_ids_by_task = {task: call_id for call_id, task in tasks}
        done, pending = await asyncio.wait(task_list, timeout=timeout_s)
        for task in done:
            call_id = str(call_ids_by_task.get(task, "") or "")
            if ledger is None or not call_id or task.cancelled():
                continue
            try:
                outcome = task.result()
            except Exception as exc:  # noqa: BLE001
                outcome = {"enabled": True, "result": None, "error": str(exc)}
            if isinstance(outcome, Mapping):
                ledger.record_speculative_outcome(call_id, outcome)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.debug(
            "speculative tasks drained: done=%s pending_cancelled=%s",
            len(done),
            len(pending),
        )

    if (
        shadow_engine is not None
        and hasattr(shadow_engine, "_registry")
        and shadow_engine._registry is not None
        and ledger is not None
    ):
        records = shadow_engine._registry.get_turn_records(ledger.turn_id)
        running_futures = [
            record.future for record in records if record.future is not None and not record.future.done()
        ]
        if running_futures:
            await asyncio.wait(running_futures, timeout=0.1)
        await shadow_engine._registry.drain_turn(
            ledger.turn_id,
            timeout_s=timeout_s,
            salvage_governor=getattr(shadow_engine, "_salvage_governor", None),
            task_group=getattr(shadow_engine, "_task_group", None),
        )
    if shadow_engine is not None and hasattr(shadow_engine, "_task_group") and shadow_engine._task_group is not None:
        shadow_engine._task_group.close()

    # NOTE: per-turn 推测执行汇总不在此发射。drain 发生在流式解码结束、authoritative
    # 工具批裁决（adopt/join/replay）之前；此处发射会漏掉全部裁决指标。汇总改由
    # ``ToolBatchExecutor.execute_tool_batch`` 在裁决完成后发射（emit_turn_summary
    # 对每个 metrics 实例幂等，故不会重复）。


# ---------------------------------------------------------------------------
# StreamOrchestrator
# ---------------------------------------------------------------------------


class StreamOrchestrator:
    """流式决策与 Turn 执行编排器 — 集中管理流式专用逻辑。"""

    def __init__(
        self,
        *,
        llm_provider: Callable,
        llm_provider_stream: Callable | None,
        decoder: Any,
        emit_event: Callable[[TurnEvent], None],
        build_decision_messages: Callable[[list[dict], list[dict]], list[dict]],
        build_stream_shadow_engine: Callable[..., StreamShadowEngine | None],
        call_llm_for_decision: Callable[..., Any],
        handoff_handler: HandoffHandler,
        tool_batch_executor: ToolBatchExecutor,
        retry_orchestrator: RetryOrchestrator,
        handle_final_answer: Callable[..., Any],
        requires_mutation_intent_hybrid: Callable[..., Any],
        extract_monitoring_metrics: Callable[..., dict[str, float]],
        resolve_shadow_workspace: Callable[[list[dict]], str] | None = None,
        config: Any | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.llm_provider_stream = llm_provider_stream
        self.decoder = decoder
        self.emit_event = emit_event
        self.build_decision_messages = build_decision_messages
        self.build_stream_shadow_engine = build_stream_shadow_engine
        self.resolve_shadow_workspace = resolve_shadow_workspace
        self.call_llm_for_decision = call_llm_for_decision
        self.handoff_handler = handoff_handler
        self.tool_batch_executor = tool_batch_executor
        self.retry_orchestrator = retry_orchestrator
        self.handle_final_answer = handle_final_answer
        self.requires_mutation_intent_hybrid = requires_mutation_intent_hybrid
        self.extract_monitoring_metrics = extract_monitoring_metrics
        self.config = config
        self._session_turn_count = 0
        # FIX-20250421-P2: 逃生舱机制 - 连续 exploring 回合计数
        self._consecutive_exploring_count: int = 0
        self._escape_hatch_triggered: bool = False

    def _resolve_shadow_workspace(self, context: list[dict]) -> str:
        if self.resolve_shadow_workspace is None:
            return "."
        workspace = str(self.resolve_shadow_workspace(context) or "").strip()
        return workspace or "."

    # -----------------------------------------------------------------------
    # 流式 LLM 决策调用
    # -----------------------------------------------------------------------

    async def _call_llm_for_decision_stream_impl(
        self,
        context: list[dict],
        tool_definitions: list[dict],
        ledger: TurnLedger,
        shadow_engine: StreamShadowEngine | None = None,
        *,
        tool_choice_override: Any | None = None,
        model_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_floor: int | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """流式调用LLM获取决策，yield 事件并返回最终 RawLLMResponse（通过内部 materialize 事件）。"""
        from polaris.cells.roles.kernel.internal.turn_engine.stream_handler import StreamEventHandler

        decision_messages = self.build_decision_messages(context, tool_definitions)
        normalized_model_override = str(model_override or "").strip() or None
        request_payload = {
            "messages": decision_messages,
            "tools": tool_definitions if tool_definitions else None,
            "tool_choice": (
                tool_choice_override if tool_choice_override is not None else ("auto" if tool_definitions else None)
            ),
            "model_override": normalized_model_override,
            # ADR-0090 W2.6: phase-aware low temperature for escalated retries.
            "temperature_override": temperature_override,
            # I3-r22 (F10): reasoning-sized reserved output floor for retry calls.
            "max_tokens_floor": max_tokens_floor,
        }

        if self.llm_provider_stream is None:
            non_stream_kwargs: dict[str, Any] = {}
            if temperature_override is not None:
                non_stream_kwargs["temperature_override"] = temperature_override
            if max_tokens_floor is not None:
                non_stream_kwargs["max_tokens_floor"] = max_tokens_floor
            response = await self.call_llm_for_decision(
                context,
                tool_definitions,
                ledger,
                tool_choice_override=tool_choice_override,
                **non_stream_kwargs,
            )
            yield cast(
                TurnEvent,
                {
                    "type": "_internal_materialize",
                    "response": response,
                },
            )
            return

        shadow_workspace = self._resolve_shadow_workspace(context)
        handler = StreamEventHandler(workspace=shadow_workspace)
        if shadow_engine is None:
            shadow_engine = self.build_stream_shadow_engine(workspace=shadow_workspace, turn_id=ledger.turn_id)
        speculative_tasks: list[tuple[str, asyncio.Task[dict[str, Any]]]] = []
        emitted_content_parts: list[str] = []
        emitted_thinking_parts: list[str] = []
        stream_native_tool_calls: list[dict[str, Any]] = []
        stream_tool_call_signatures: set[str] = set()
        stream_call_index_by_id: dict[str, int] = {}
        materialized_response_seen = False

        async def _try_speculate_tool_call(
            tool_name: str,
            tool_args: dict[str, Any],
            call_id: str,
        ) -> None:
            if shadow_engine is None:
                return
            speculative_tasks.append(
                (
                    call_id,
                    asyncio.create_task(
                        shadow_engine.speculate_tool_call(
                            tool_name=tool_name,
                            arguments=tool_args,
                            call_id=call_id,
                            turn_id=ledger.turn_id,
                        )
                    ),
                )
            )

        try:
            async for event in handler.process_stream(
                self.llm_provider_stream(request_payload),
                round_index=0,
                start_time=time.time(),
                profile=None,
            ):
                event_type = str(event.get("type") or "").strip()
                if event_type in {"thinking_chunk", "reasoning_chunk"}:
                    content = str(event.get("content") or "")
                    if content:
                        emitted_thinking_parts.append(content)
                    if shadow_engine is not None:
                        shadow_engine.consume_delta(content)
                    yield ContentChunkEvent(turn_id=ledger.turn_id, chunk=content, is_thinking=True)
                elif event_type in {"content_chunk", "chunk"}:
                    content = str(event.get("content") or "")
                    if content:
                        emitted_content_parts.append(content)
                    if shadow_engine is not None:
                        shadow_engine.consume_delta(content)
                    yield ContentChunkEvent(turn_id=ledger.turn_id, chunk=content, is_thinking=False)
                elif event_type == "tool_call":
                    tool_name = str(event.get("tool") or "").strip()
                    call_id = str(event.get("call_id") or "").strip()
                    raw_args = event.get("args")
                    tool_args = dict(raw_args) if isinstance(raw_args, dict) else {}
                    if tool_name:
                        signature = stream_tool_call_signature(tool_name, tool_args, call_id)
                        if signature not in stream_tool_call_signatures:
                            stream_tool_call_signatures.add(signature)
                            upsert_stream_native_tool_call(
                                stream_native_tool_calls,
                                stream_call_index_by_id,
                                tool_name=tool_name,
                                tool_args=tool_args,
                                call_id=call_id,
                            )
                        if shadow_engine is not None:
                            shadow_engine.consume_delta(f"<tool_call:{tool_name}>")
                            # ADR-0090 I2.3: empty-args placeholders are never speculated —
                            # they either refine into a complete call or are no-arg calls
                            # whose speculation has no value worth a shadow slot.
                            if tool_args:
                                await _try_speculate_tool_call(tool_name, tool_args, call_id)
                        yield ToolBatchEvent(
                            turn_id=ledger.turn_id,
                            batch_id="",
                            tool_name=tool_name,
                            call_id=call_id,
                            status="started",
                            progress=0.0,
                            arguments=tool_args,
                        )
                elif event_type == "error":
                    error_message = str(event.get("error") or event.get("message") or "unknown_error")
                    yield ErrorEvent(
                        turn_id=ledger.turn_id,
                        error_type="stream_error",
                        message=error_message,
                        state_at_error="DECISION_REQUESTED",
                    )
                    return
                elif event_type == "_internal_materialize":
                    materialized_response_seen = True
                    thinking_parts = event.get("thinking_content")
                    if thinking_parts:
                        thinking = "".join(thinking_parts)
                    elif emitted_thinking_parts:
                        thinking = "".join(emitted_thinking_parts)
                    else:
                        thinking = None
                    visible_content = str(
                        event.get("emitted_round_content") or event.get("raw_output") or "".join(emitted_content_parts)
                    )
                    native_tool_calls = list(event.get("native_tool_calls") or [])
                    if not native_tool_calls and stream_native_tool_calls:
                        native_tool_calls = list(stream_native_tool_calls)
                    native_tool_calls = supersede_partial_tool_calls(native_tool_calls)
                    raw_usage = event.get("usage")
                    response_usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
                    response = RawLLMResponse(
                        content=visible_content,
                        thinking=thinking,
                        native_tool_calls=native_tool_calls,
                        model=str(event.get("model") or "unknown"),
                        usage=response_usage,
                    )
                    yield cast(
                        TurnEvent,
                        {
                            "type": "_internal_materialize",
                            "response": response,
                        },
                    )
            if not materialized_response_seen:
                response = RawLLMResponse(
                    content="".join(emitted_content_parts),
                    thinking="".join(emitted_thinking_parts) if emitted_thinking_parts else None,
                    native_tool_calls=supersede_partial_tool_calls(list(stream_native_tool_calls)),
                    model="unknown",
                    usage={},
                )
                yield cast(
                    TurnEvent,
                    {
                        "type": "_internal_materialize",
                        "response": response,
                    },
                )
        finally:
            await drain_speculative_tasks(speculative_tasks, ledger=ledger, shadow_engine=shadow_engine)

    # -----------------------------------------------------------------------
    # 流式 Turn 执行编排
    # -----------------------------------------------------------------------

    async def execute_turn_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        *,
        call_llm_for_decision_stream: Callable[..., AsyncIterator[TurnEvent]] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """流式执行turn的内部实现。"""
        from polaris.cells.roles.kernel.internal.kernel_guard import KernelGuardError
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            apply_delivery_mode_filter,
            has_available_write_tool,
            is_mutation_contract_violation,
        )

        # FIX-20250421-P2: 在函数开始处统一导入，避免局部导入导致的 F823 错误
        from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
            DeliveryMode as DeliveryModeEnum,
        )

        # H2 物理熔断器：session 级 turn 硬限制，防止无限循环
        self._session_turn_count += 1
        max_session_turns = getattr(self.config, "max_session_turns", 20)
        if self._session_turn_count > max_session_turns:
            logger.warning(
                "session_turn_limit_exceeded: turn_id=%s count=%d max=%d",
                turn_id,
                self._session_turn_count,
                max_session_turns,
            )
            state_machine.transition_to(TurnState.FAILED)
            ledger.state_history.append(("FAILED", int(time.time() * 1000)))
            ledger.finalize()
            ledger.anomaly_flags.append(
                {
                    "type": "SESSION_TURN_LIMIT_EXCEEDED",
                    "turn_id": turn_id,
                    "session_turn_count": self._session_turn_count,
                    "max_session_turns": max_session_turns,
                }
            )
            yield ErrorEvent(
                turn_id=turn_id,
                error_type="MAX_SESSION_TURNS_EXCEEDED",
                message=(
                    f"Session exceeded {max_session_turns} turns without completing contract obligations. "
                    "Raising to human for review."
                ),
                state_at_error=state_machine.current_state.name,
            )
            return

        state_machine.transition_to(TurnState.CONTEXT_BUILT)
        ledger.state_history.append(("CONTEXT_BUILT", int(time.time() * 1000)))

        # Phase 1b: 解析交付契约（与 run 模式保持一致）
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            resolve_delivery_mode,
        )

        latest_user_request = extract_latest_user_message(context)
        # Guard: 如果 context 包含 orchestrator 续写 prompt（<Goal>/<Progress> XML 块），
        # 说明这是 continuation turn，delivery_mode 必须从 prompt contract 中恢复。
        _raw_user = str(
            next(
                (m.get("content", "") for m in reversed(context) if str(m.get("role", "")).strip().lower() == "user"),
                "",
            )
        )
        _is_continuation_prompt = "<Goal>" in _raw_user and "<Progress>" in _raw_user
        if _is_continuation_prompt:
            _progress_match = re.search(r"当前阶段:\s*(\w+)", _raw_user)
            _parsed_progress = _progress_match.group(1) if _progress_match else "exploring"
            # 提取 recent_reads 用于关键词检测
            _continuation_metadata = extract_continuation_prompt_metadata(_raw_user)
            _recent_reads = _continuation_metadata.get("recent_reads")
            delivery_contract = _resolve_continuation_delivery_contract(
                raw_user=_raw_user,
                original_delivery_mode=ledger._original_delivery_mode,
                parsed_progress=_parsed_progress,
                recent_reads=_recent_reads if isinstance(_recent_reads, list) else None,
            )
        else:
            # SLM 优先、regex 兜底
            try:
                from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                    CognitiveGateway,
                )

                gateway = CognitiveGateway.get_default_instance_sync()
                if gateway is not None:
                    delivery_contract = await gateway.resolve_delivery_mode(latest_user_request)
                else:
                    delivery_contract = resolve_delivery_mode(latest_user_request)
            except (ImportError, AttributeError, RuntimeError, asyncio.TimeoutError, OSError):
                delivery_contract = resolve_delivery_mode(latest_user_request)
        ledger.set_delivery_contract(delivery_contract)

        # FIX-20250421-P2: 逃生舱机制 - 连续 exploring 回合检测
        # 当 LLM 连续 N 回合处于 exploring 且未执行 write 时，强制降级为单 batch write 模式
        _escape_hatch_threshold: int = 3  # 连续 3 回合 exploring 触发
        if _is_continuation_prompt:
            if delivery_contract.mode in (DeliveryModeEnum.ANALYZE_ONLY, DeliveryModeEnum.PROPOSE_PATCH):
                self._consecutive_exploring_count += 1
                logger.debug(
                    "escape_hatch_monitor: consecutive_exploring=%d/%d turn_id=%s",
                    self._consecutive_exploring_count,
                    _escape_hatch_threshold,
                    turn_id,
                )
            elif delivery_contract.mode == DeliveryModeEnum.MATERIALIZE_CHANGES:
                # 进入 mutation 模式，重置计数器
                if self._consecutive_exploring_count > 0:
                    logger.debug(
                        "escape_hatch_monitor: reset_counter (materialize) turn_id=%s",
                        turn_id,
                    )
                self._consecutive_exploring_count = 0

            # 触发逃生舱：强制降级为 MATERIALIZE_CHANGES 并限制工具
            if self._consecutive_exploring_count >= _escape_hatch_threshold and not self._escape_hatch_triggered:
                logger.warning(
                    "escape_hatch_triggered: consecutive_exploring=%d turn_id=%s "
                    "forcing MATERIALIZE_CHANGES with minimal execution tools",
                    self._consecutive_exploring_count,
                    turn_id,
                )
                self._escape_hatch_triggered = True
                delivery_contract = _build_delivery_contract_from_mode(DeliveryMode.MATERIALIZE_CHANGES)
                ledger.set_delivery_contract(delivery_contract)
                # Keep the same minimal execution schema as from-scratch weak
                # Director turns. The mutation gates still require a write in
                # the batch; the provider schema must retain read/locate tools
                # that prompts reference for recovery and verification.
                tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
                if not tool_definitions:
                    logger.error(
                        "escape_hatch_no_write_tools: turn_id=%s no write tools available",
                        turn_id,
                    )

        state_machine.transition_to(TurnState.DECISION_REQUESTED)
        ledger.state_history.append(("DECISION_REQUESTED", int(time.time() * 1000)))
        self.emit_event(TurnPhaseEvent.create(turn_id, "decision_requested"))

        shadow_workspace = self._resolve_shadow_workspace(context)
        shadow_engine = self.build_stream_shadow_engine(workspace=shadow_workspace, turn_id=turn_id)
        llm_response: RawLLMResponse | None = None
        # FIX-20260427: In SUPER mode, force tool_choice="required" ONLY for Director
        # to prevent the LLM from generating text-only analysis instead of calling write tools.
        # Architect/PM/CE use "auto" since they may legitimately produce text analysis.
        _super_tool_choice_override: Any | None = None
        for _msg in context:
            if not isinstance(_msg, dict):
                continue
            _content = str(_msg.get("content", ""))
            if "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in _content or "[SUPER_MODE_DIRECTOR_CONTINUE]" in _content:
                _super_tool_choice_override = "required"
                break
        _call_llm_stream = (
            call_llm_for_decision_stream
            if call_llm_for_decision_stream is not None
            else self._call_llm_for_decision_stream_impl
        )
        async for event in _call_llm_stream(
            context,
            tool_definitions,
            ledger,
            shadow_engine=shadow_engine,
            tool_choice_override=_super_tool_choice_override,
        ):
            if isinstance(event, dict) and event.get("type") == "_internal_materialize":
                llm_response = event.get("response")
                continue
            yield event

        if llm_response is None:
            yield ErrorEvent(
                turn_id=turn_id,
                error_type="stream_error",
                message="No LLM response materialized from stream",
                state_at_error="DECISION_REQUESTED",
            )
            return

        # ADR-0090 I3: one corrective re-ask before a degraded decode kills the
        # turn — either every native tool call failed to parse, or the response
        # was completely empty. The weak model gets the exact error quoted back.
        probe_decision = self.decoder.decode(llm_response, TurnId(turn_id))
        corrective_ask = evaluate_decode_corrective(
            probe_decision,
            llm_response,
            tool_definitions=tool_definitions,
        )
        if corrective_ask is not None:
            logger.warning(
                "decode_corrective_retry(stream): reason=%s turn_id=%s",
                corrective_ask.reason,
                turn_id,
            )
            self.emit_event(
                TurnPhaseEvent.create(
                    turn_id,
                    "decode_corrective_retry",
                    {"reason": corrective_ask.reason},
                )
            )
            if self.llm_provider_stream is not None:
                superseded_usage_raw = llm_response.get("usage", {})
                superseded_usage = superseded_usage_raw if isinstance(superseded_usage_raw, dict) else {}
                ledger.record_llm_call(
                    phase="decision",
                    model=llm_response.get("model", "unknown"),
                    tokens_in=superseded_usage.get("prompt_tokens", 0),
                    tokens_out=superseded_usage.get("completion_tokens", 0),
                    metadata={"superseded_by_corrective_retry": corrective_ask.reason},
                )
            corrective_response: RawLLMResponse | None = None
            async for event in _call_llm_stream(
                build_corrective_context(context, corrective_ask),
                tool_definitions,
                ledger,
                shadow_engine=shadow_engine,
                tool_choice_override=_super_tool_choice_override,
            ):
                if isinstance(event, dict) and event.get("type") == "_internal_materialize":
                    corrective_response = event.get("response")
                    continue
                yield event
            if corrective_response is not None:
                llm_response = corrective_response

        if self.llm_provider_stream is not None:
            raw_response_usage = llm_response.get("usage", {})
            response_usage = raw_response_usage if isinstance(raw_response_usage, dict) else {}
            response_context_os_audit = response_usage.get("context_os_audit")
            ledger.record_llm_call(
                phase="decision",
                model=llm_response.get("model", "unknown"),
                tokens_in=response_usage.get("prompt_tokens", 0),
                tokens_out=response_usage.get("completion_tokens", 0),
                metadata=(
                    {"context_os_audit": dict(response_context_os_audit)}
                    if isinstance(response_context_os_audit, dict)
                    else None
                ),
            )

        if (
            shadow_engine is not None
            and is_refusal_response(llm_response)
            and hasattr(shadow_engine, "_registry")
            and shadow_engine._registry is not None
        ):
            coordinator = CancellationCoordinator()
            task_group = getattr(shadow_engine, "_task_group", None)
            await coordinator.refuse_turn(
                turn_id=turn_id,
                registry=shadow_engine._registry,
                task_group=task_group if isinstance(task_group, TurnScopedTaskGroup) else None,
            )

        state_machine.transition_to(TurnState.DECISION_RECEIVED)
        ledger.state_history.append(("DECISION_RECEIVED", int(time.time() * 1000)))

        # Reuse the probe decision when no corrective retry replaced the response
        # (decode is pure — identical input yields an identical decision).
        decision = probe_decision if corrective_ask is None else self.decoder.decode(llm_response, TurnId(turn_id))
        decision_metadata = dict(decision.get("metadata") or {})
        native_tool_call_facts = native_tool_call_facts_from_sources(
            decision_metadata,
            native_tool_calls_from_response(llm_response),
        )
        native_tool_call_count = native_tool_call_count_from_facts(native_tool_call_facts)
        provider_response_hash = derive_provider_response_hash(llm_response, decision_metadata)
        decision_metadata.setdefault("provider_response_hash", provider_response_hash)
        project_native_tool_call_facts_to_metadata(decision_metadata, native_tool_call_facts)
        decision = dict(decision)
        decision["metadata"] = decision_metadata
        if native_tool_call_count > 0 and tool_definitions and not decision.get("tool_batch"):
            ledger.anomaly_flags.append(
                build_tool_dispatch_dropped_anomaly(
                    response=llm_response,
                    metadata=decision_metadata,
                    turn_id=turn_id,
                    streaming=True,
                )
            )
            raise RuntimeError(
                "tool_dispatch_dropped: provider emitted "
                f"{native_tool_call_count} tool call(s), but no executable tool batch was decoded"
            )

        # PROPOSE_PATCH / ANALYZE_ONLY 边界保护：过滤 write tools（与 run 模式一致）。
        # 必须在 record_decision / TOOL_BATCH 执行之前应用，否则只读/提案契约下
        # 流式 Director 仍会真实写入 workspace（fail-open）。
        decision = apply_delivery_mode_filter(decision, ledger)

        ledger.record_decision(decision)
        self.emit_event(
            TurnPhaseEvent.create(
                turn_id,
                "decision_completed",
                {
                    "kind": decision.get("kind").value
                    if hasattr(decision.get("kind"), "value")
                    else str(decision.get("kind")),
                    "finalize_mode": decision.get("finalize_mode").value
                    if hasattr(decision.get("finalize_mode"), "value")
                    else str(decision.get("finalize_mode")),
                },
            )
        )

        state_machine.transition_to(TurnState.DECISION_DECODED)
        ledger.state_history.append(("DECISION_DECODED", int(time.time() * 1000)))

        decision_kind = decision.get("kind")
        result: dict
        batch_receipt: dict[str, Any] = {}
        forced_retry_result: dict | None = None
        latest_user_request = extract_latest_user_message(context)
        guard_mode = str(getattr(self.config, "mutation_guard_mode", "warn"))
        # 当 delivery contract 明确要求 MATERIALIZE_CHANGES 时，无视 guard_mode 强制 strict
        # 防止 LLM 以"请求确认"的 FINAL_ANSWER 逃避工具调用（用户已确认多次但仍不执行）
        _contract_requires_tools = ledger.delivery_contract.mode == DeliveryModeEnum.MATERIALIZE_CHANGES
        if (
            decision_kind != TurnDecisionKind.TOOL_BATCH
            and await self.requires_mutation_intent_hybrid(latest_user_request)
            and has_available_write_tool(tool_definitions)
        ):
            if guard_mode == "strict" or _contract_requires_tools:
                logger.warning(
                    "mutation-contract guard(stream): non-tool decision (%s) for mutation request, "
                    "forcing retry path (guard=%s contract_requires_tools=%s)",
                    decision_kind,
                    guard_mode,
                    _contract_requires_tools,
                )
                forced_retry_result = await self.retry_orchestrator.retry_tool_batch_after_contract_violation(
                    turn_id=turn_id,
                    context=context,
                    tool_definitions=tool_definitions,
                    state_machine=state_machine,
                    ledger=ledger,
                    stream=True,
                    shadow_engine=shadow_engine,
                )
            elif guard_mode == "warn":
                logger.warning(
                    "mutation-contract guard(stream, soft): non-tool decision (%s) for mutation request, "
                    "but mutation_guard_mode=warn allows passthrough. turn_id=%s",
                    decision_kind,
                    turn_id,
                )
                ledger.record_mutation_guard_warning(
                    reason=f"non_tool_decision_for_mutation_request:{decision_kind.value if hasattr(decision_kind, 'value') else decision_kind}",
                    user_request=latest_user_request,
                )

        if forced_retry_result is not None:
            result = forced_retry_result
            batch_receipt = normalize_batch_receipt(result.get("batch_receipt"))
            if batch_receipt:
                for item in batch_receipt.get("results", []):
                    yield ToolBatchEvent(
                        turn_id=turn_id,
                        batch_id=str(batch_receipt.get("batch_id", "")),
                        tool_name=str(item.get("tool_name", "")),
                        call_id=str(item.get("call_id", "")),
                        status="success" if item.get("status") == "success" else "error",
                        progress=1.0,
                        result=item.get("result"),
                        error=item.get("error"),
                    )
            if result.get("kind") == "handoff_workflow":
                handoff_decision = result.get("decision") or decision
                async for event in self.handoff_handler.handle_handoff_stream(
                    handoff_decision,
                    state_machine,
                    ledger,
                    workflow_context=result.get("workflow_context"),
                    handoff_reason=result.get("handoff_reason"),
                    batch_receipt=batch_receipt,
                ):
                    yield event
                return
        else:
            if decision_kind == TurnDecisionKind.FINAL_ANSWER:
                result = await self.handle_final_answer(decision, state_machine, ledger)
            elif decision_kind == TurnDecisionKind.HANDOFF_WORKFLOW:
                async for event in self.handoff_handler.handle_handoff_stream(decision, state_machine, ledger):
                    yield event
                return
            elif decision_kind == TurnDecisionKind.HANDOFF_DEVELOPMENT:
                async for event in self.handoff_handler.handle_development_handoff_stream(
                    decision, state_machine, ledger
                ):
                    yield event
                return
            elif decision_kind == TurnDecisionKind.ASK_USER:
                result = await self.handoff_handler.handle_ask_user(decision, state_machine, ledger)
            elif decision_kind == TurnDecisionKind.TOOL_BATCH:
                allowed_tool_names = extract_allowed_tool_names_from_definitions(tool_definitions)
                try:
                    result = await self.tool_batch_executor.execute_tool_batch(
                        decision,
                        state_machine,
                        ledger,
                        context,
                        stream=True,
                        shadow_engine=shadow_engine,
                        allowed_tool_names=allowed_tool_names,
                    )
                except (RuntimeError, KernelGuardError) as exc:
                    if not isinstance(exc, RuntimeError) or not is_mutation_contract_violation(exc):
                        raise
                    result = await self.retry_orchestrator.retry_tool_batch_after_contract_violation(
                        turn_id=turn_id,
                        context=context,
                        tool_definitions=tool_definitions,
                        state_machine=state_machine,
                        ledger=ledger,
                        stream=True,
                        shadow_engine=shadow_engine,
                        # Wave-5: a READ-ONLY violating batch is bootstrapped directly —
                        # discarding the model's (often correct) reads and re-asking
                        # makes weak models emit worse, hallucinated calls.
                        original_decision=decision,
                    )
                batch_receipt = normalize_batch_receipt(result.get("batch_receipt"))
                if batch_receipt:
                    for item in batch_receipt.get("results", []):
                        yield ToolBatchEvent(
                            turn_id=turn_id,
                            batch_id=str(batch_receipt.get("batch_id", "")),
                            tool_name=str(item.get("tool_name", "")),
                            call_id=str(item.get("call_id", "")),
                            status="success" if item.get("status") == "success" else "error",
                            progress=1.0,
                            result=item.get("result"),
                            error=item.get("error"),
                        )
                if result.get("kind") == "handoff_workflow":
                    handoff_decision = result.get("decision") or decision
                    async for event in self.handoff_handler.handle_handoff_stream(
                        handoff_decision,
                        state_machine,
                        ledger,
                        workflow_context=result.get("workflow_context"),
                        handoff_reason=result.get("handoff_reason"),
                        batch_receipt=batch_receipt,
                    ):
                        yield event
                    return
            else:
                raise ValueError(f"Unknown decision kind: {decision_kind}")

        _kind = result.get("kind", "")

        # continue_multi_turn: 构建包含 SESSION_PATCH 的 visible_content，
        # 让 Orchestrator 通过 ADR-0080 机制自动注入 structured_findings
        if _kind == "continue_multi_turn":
            _receipt = normalize_batch_receipt(result.get("batch_receipt"))
            if _receipt:
                _summary_results = _receipt.get("results") or []
                _summary_tool_names = []
                if isinstance(_summary_results, list):
                    _summary_tool_names = [
                        str(item.get("tool_name", ""))
                        for item in _summary_results
                        if isinstance(item, dict) and str(item.get("tool_name", ""))
                    ]
                logger.debug(
                    "continue_multi_turn merged_receipt_summary: turn_id=%s results=%d success=%s failure=%s tools=%s",
                    turn_id,
                    len(_summary_results) if isinstance(_summary_results, list) else 0,
                    _receipt.get("success_count"),
                    _receipt.get("failure_count"),
                    ",".join(_summary_tool_names[:8]),
                )
            read_tools = _extract_read_tools_from_receipt(result.get("batch_receipt"))
            # FIX-20250421: 使用 PhaseManager 的真实阶段，不再依赖字符串匹配
            # PhaseManager 基于工具执行结果（不是 LLM 宣称）驱动阶段
            _current_progress = ledger.phase_manager.current_phase.value
            logger.debug(
                "continue_multi_turn using PhaseManager phase: %s turn_id=%s",
                _current_progress,
                turn_id,
            )

            # FIX-20250421-v2: 在 MATERIALIZE_CHANGES 模式下，如果仍在 EXPLORING 阶段，
            # 强化提示语，强制要求模型继续执行 read_file，严禁返回文本
            _is_materialize = getattr(ledger.delivery_contract, "mode", None) == DeliveryMode.MATERIALIZE_CHANGES
            _force_read_required = (
                _is_materialize
                and _current_progress == Phase.EXPLORING.value
                and not read_tools  # 还没有真正读取过文件
            )

            result["visible_content"] = _build_continue_visible_content(
                read_tools,
                current_progress=_current_progress,
                force_read_required=_force_read_required,
                delivery_mode=ledger.delivery_contract.mode.value,
                turns_in_phase=ledger.phase_manager._turns_in_current_phase,
                max_turns_per_phase=ledger.phase_manager._max_turns_per_phase,
                modification_contract_status=ledger.modification_contract.status.value,
                conversation_context=context,
                batch_receipt=result.get("batch_receipt"),
            )

        visible_content = result.get("visible_content", "")
        if visible_content and result.get("kind") != "final_answer":
            from polaris.kernelone.llm.reasoning import strip_reasoning_tags

            clean_visible = strip_reasoning_tags(str(visible_content))
            yield ContentChunkEvent(
                turn_id=turn_id,
                chunk=clean_visible,
                is_thinking=False,
                is_finalization=True,
            )

        _kind = result.get("kind", "")
        completion_status: Literal["success", "failed", "handoff", "suspended"] = (
            "handoff" if _kind == "handoff_workflow" else "suspended" if _kind == "ask_user" else "success"
        )
        _finalization = result.get("finalization") or {}
        monitoring: dict[str, Any] = dict(self.extract_monitoring_metrics(result.get("metrics", {})))
        context_os_audit_summary = summarize_context_os_audit_from_ledger(ledger)
        if context_os_audit_summary:
            monitoring["context_os_audit"] = context_os_audit_summary
        # Completion-side dispatch evidence: the stream completion owner rebuilds
        # the missing-dispatch tool_call_lifecycle receipt from these keys, using
        # the same metadata contract as the non-stream completion path.
        project_completion_dispatch_evidence_to_metadata(
            monitoring,
            decision_metadata,
            result.get("llm_response_metadata"),
            llm_response.get("usage"),
        )
        project_native_tool_call_facts_to_metadata(
            monitoring,
            native_tool_call_facts_from_sources(
                monitoring,
                native_tool_calls_from_response(llm_response),
            ),
        )
        yield CompletionEvent(
            turn_id=turn_id,
            status=completion_status,
            duration_ms=result.get("metrics", {}).get("duration_ms", 0),
            llm_calls=result.get("metrics", {}).get("llm_calls", 0),
            tool_calls=result.get("metrics", {}).get("tool_calls", 0),
            monitoring=monitoring,
            visible_content=visible_content,
            turn_kind=_kind,
            batch_receipt=batch_receipt or {},
            error=_finalization.get("suspended_reason") or _finalization.get("error") if _kind == "ask_user" else None,
        )
