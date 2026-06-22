"""Decision-stage message synthesis for the transaction kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from
``TurnTransactionController._build_decision_messages`` per the REMAINING_06
decomposition blueprint (step 1). This module owns the control-plane prompt
synthesis that injects single-batch / multi-turn execution guards, task-contract
hints, the implementing-phase HARD GATE, and the bootstrap write-retry user
anchor.

Polaris §8 NOTE: this module embeds large bilingual prompt-engineering literals
(business/prompt content living in a kernel internal). They are moved VERBATIM
to preserve behavior and are flagged for separate review per the blueprint risk
register — do NOT rewrite/delete them in a behavior-preserving pass.

The function is pure: it depends only on its arguments and module-level imports,
so the facade can delegate to it without any per-turn object allocation.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
)


def _is_materialization_quality_repair(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "materialization quality repair mode" in lowered
        or "[director_quality_repair:" in lowered
        or ("artifact quality scan failed" in lowered and "do not read files first" in lowered)
    )


def _line_conflicts_with_quality_repair(line: str) -> bool:
    lowered = str(line or "").lower()
    return any(
        marker in lowered
        for marker in (
            "positive tool sequence templates",
            "tool failure recovery protocol",
            "template [",
            "step 1:",
            "read_file",
            "repo_rg",
            "ripgrep",
            "glob/",
            "search-then",
            "immediately call",
            "use ordered groups",
            "any tool failure",
            "partial completion",
        )
    )


def build_decision_messages(
    context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    ledger: TurnLedger | None = None,
) -> list[dict[str, Any]]:
    """Build decision-stage messages with single-batch execution constraints."""
    messages: list[dict[str, Any]] = [
        dict(message) for message in context if message.get("metadata", {}).get("plane") != "control"
    ]
    if not tool_definitions:
        return messages

    # BUG-01 fix: detect benchmark single-batch mode from user message.
    # When [Benchmark Tool Contract] is present the execution is always
    # single-turn; the multi-turn "first turn read_file" wording must NOT
    # be used because it gives the model explicit permission to defer
    # writes to a non-existent next turn.
    _latest_user_for_guard = ""
    for _m in reversed(context):
        if isinstance(_m, dict) and str(_m.get("role", "")).strip().lower() == "user":
            _latest_user_for_guard = str(_m.get("content", ""))
            break
    _is_benchmark_single_batch = "[Benchmark Tool Contract]" in _latest_user_for_guard
    _is_super_readonly_stage = "[SUPER_MODE_READONLY_STAGE]" in _latest_user_for_guard
    _latest_user_for_guard_lower = _latest_user_for_guard.lower()
    _is_quality_repair = _is_materialization_quality_repair(_latest_user_for_guard)
    _is_toolless_proposal_stage = (
        "[mode:propose]" in _latest_user_for_guard_lower and "do not call tools" in _latest_user_for_guard_lower
    )
    _ledger_delivery_mode = getattr(getattr(ledger, "delivery_contract", None), "mode", None)
    _is_materialize_single_batch = _ledger_delivery_mode in {
        DeliveryMode.MATERIALIZE_CHANGES,
        DeliveryMode.PROPOSE_PATCH,
    }

    if _is_toolless_proposal_stage:
        proposal_guard = (
            "SYSTEM CONSTRAINT (Proposal): Tool calls are disabled for this proposal turn. "
            "Return only the requested parsable patch or fenced file sections. "
            "Do not include progress notes, execution narration, or write-tool instructions."
        )
        messages.append({"role": "system", "content": proposal_guard, "metadata": {"plane": "control"}})
        return messages

    if _is_super_readonly_stage:
        single_batch_guard = (
            "SYSTEM CONSTRAINT (Execution): This is a SUPER readonly planning stage. "
            "Your role is read-only for this stage. Use only read/exploration tools exposed to your current role. "
            "Do NOT attempt to satisfy a write contract in this stage. "
            "Produce planning or analysis output for the next stage, then stop.\\n"
            "系统约束 (只读规划): 当前为 SUPER 的只读规划阶段。"
            "只允许使用当前角色暴露的读取/探索工具，禁止尝试写入，禁止把本阶段当成代码落地阶段。"
        )
    elif _is_benchmark_single_batch or _is_materialize_single_batch:
        if _is_quality_repair:
            single_batch_guard = (
                "SYSTEM CONSTRAINT (Execution): This is a SINGLE-BATCH materialization quality repair. "
                "ALL required repair tool calls MUST be emitted in this single turn. "
                "Do NOT defer write/edit tools to a subsequent turn — there is no subsequent turn in this path.\\n"
                "Complete only the targeted repair requested by the latest quality-gate feedback. "
                "If that feedback forbids read/list/explore steps, do not add them. "
                "Proceed immediately with the requested repair tool calls; do not ask for confirmation.\\n"
                "系统约束 (单批次质量修复): 本轮只执行最新质量门禁反馈要求的定向修复。"
                "如反馈禁止读取、列目录或探索，不得添加这些步骤；直接调用所需写/改工具。"
            )
        else:
            single_batch_guard = (
                "SYSTEM CONSTRAINT (Execution): This is a SINGLE-BATCH execution. "
                "ALL required tool calls MUST be emitted in this single turn. "
                "Do NOT defer any tool call (especially write/edit tools) to a subsequent turn — "
                "there is no subsequent turn in this execution path.\\n"
                "Complete the entire workflow (search → read → write if required) in one batch. "
                "Proceed immediately with tool calls; do not ask for confirmation.\\n"
                "系统约束 (单批次): 本次执行为单轮单批次。所有工具调用必须在本轮一次性完成，"
                "严禁将写入工具推迟到下一轮——当前执行路径不存在下一轮。"
            )
    else:
        single_batch_guard = (
            "SYSTEM CONSTRAINT (Execution): This turn supports multi-turn workflow. "
            "For code modification tasks, follow the 'inspect-then-modify' pattern across turns:\\n"
            "1. First turn: You may call read_file to inspect existing code. "
            "2. Subsequent turns: You MUST call write/edit tools (edit_file, write_file, etc.) to materialize changes.\\n"
            "3. NEVER output large code blocks in text — always use tools to write files.\\n"
            "4. DO NOT ask the user for confirmation, approval, or plan review. "
            "The user has already authorized execution. Proceed immediately with tool calls.\\n"
            "系统约束 (执行层): 当前回合支持多回合工作流. 代码修改任务遵循'先勘察后修改': "
            "第一轮允许调用 read_file 了解现状, 后续回合必须调用写工具落盘修改. "
            "严禁在对话中直接输出大段代码替代工具调用. "
            "严禁请求用户确认或等待批准——用户已授权执行，请立即调用工具实施修改。"
        )
    messages.append({"role": "system", "content": single_batch_guard, "metadata": {"plane": "control"}})
    if _is_quality_repair:
        messages.append(
            {
                "role": "system",
                "content": (
                    "<OVERRIDE_PRIORITY: CRITICAL>\n"
                    "MATERIALIZATION QUALITY REPAIR OVERRIDE:\n"
                    "The latest user quality-repair instruction overrides generic tool sequence templates. "
                    "When it says not to read files first, list directories, explore, or explain, emit only "
                    "the minimal write/edit tool calls needed for the named repair targets, then stop.\n"
                    "</OVERRIDE_PRIORITY>"
                ),
                "metadata": {"plane": "control", "kind": "quality_repair_override"},
            }
        )

    # FIX-20250421: 在 MATERIALIZE_CHANGES 模式下也注入 Task Contract 的正例模板和恢复协议。
    # 根因：CLI 模式下 MATERIALIZE_CHANGES 跳过 Task Contract，导致模型缺乏正例指导。
    # 修复：只注入 POSITIVE 模板（序列模板 + 恢复协议），不注入 NEGATIVE 的 HARD GATE 规则，
    # 避免与多回合先读后写规则冲突。
    # FIX-20250422-SUPER: SUPER_MODE 下保留 HARD GATE，Director 必须立即执行写操作。
    is_materialize = ledger is not None and getattr(ledger.delivery_contract, "mode", None) in {
        DeliveryMode.MATERIALIZE_CHANGES,
        DeliveryMode.PROPOSE_PATCH,
    }
    _is_super_mode = any(
        marker in str(m.get("content", ""))
        for m in context
        for marker in (
            "[SUPER_MODE_HANDOFF]",
            "[/SUPER_MODE_HANDOFF]",
            "[SUPER_MODE_DIRECTOR_CONTINUE]",
            "[/SUPER_MODE_DIRECTOR_CONTINUE]",
        )
    )
    task_contract_hint, _task_contract_metadata = build_single_batch_task_contract_hint(context, tool_definitions)
    if task_contract_hint and not _is_super_readonly_stage:
        if is_materialize and not _is_super_mode:
            # MATERIALIZE 模式（非 SUPER）: 只保留正例模板和恢复协议，过滤掉 NEGATIVE/HARD GATE 规则
            positive_lines = []
            for line in task_contract_hint.split("\n"):
                # 跳过 NEGATIVE 规则行（包含 "INVALID", "HARD GATE", "rejected" 等）
                lowered_line = line.lower()
                if any(
                    marker in lowered_line
                    for marker in (
                        "invalid",
                        "hard gate",
                        "rejected",
                        "do not stop",
                        "read-only",
                        "single-batch",
                        "same batch",
                        "this request requires mutation",
                        "valid pattern",
                    )
                ):
                    continue
                if _is_quality_repair and _line_conflicts_with_quality_repair(line):
                    continue
                positive_lines.append(line)
            if positive_lines:
                messages.append(
                    {
                        "role": "system",
                        "content": "\n".join(positive_lines),
                        "metadata": {"plane": "control", "kind": "task_contract_positive"},
                    }
                )
        else:
            # SUPER_MODE 或非 MATERIALIZE: 保留完整 Task Contract（含 HARD GATE）
            messages.append({"role": "system", "content": task_contract_hint})

    # 【修复根因 C】：implementing 阶段追加 HARD GATE 强制约束。
    # FIX-20250421: 允许 read_file/repo_read_head（针对目标文件的定向读取），
    # 只禁止 broad exploration（glob/repo_tree/repo_rg），避免模型因缺乏上下文而盲写。
    _is_implementing_turn = any(
        "当前阶段: implementing" in str(m.get("content", ""))
        for m in context
        if str(m.get("role", "")).strip().lower() == "user"
    )
    if _is_implementing_turn:
        enforcing_constraint = (
            "HARD GATE (Implementing Phase): You are now in the MODIFY phase. "
            "You MUST call at least one write tool (edit_file, write_file, create_file, etc.) in this turn. "
            "Text-only responses, plan outlines, or 'I will now...' are INVALID and will be rejected. "
            "DO NOT ask for confirmation. DO NOT output code blocks in text. Use tools immediately.\n"
            "CRITICAL: Broad exploration tools (glob, repo_rg, repo_tree) are FORBIDDEN in this phase. "
            "You have already gathered enough context. Proceed directly to write.\n"
            "ALLOWED: You may call read_file or repo_read_head on SPECIFIC target files "
            "if you need to verify exact content before editing. But prioritize write tools.\n"
            "强制约束（修改阶段）：本回合必须调用至少一个写工具。"
            "严禁调用 broad 探索工具（glob/repo_rg/repo_tree）——直接写入。"
            "允许：如需确认目标文件内容，可调用 read_file/repo_read_head 读取特定文件，但优先写工具。"
        )
        messages.append(
            {
                "role": "system",
                "content": enforcing_constraint,
                "metadata": {"plane": "control", "kind": "execution_constraint"},
            }
        )

    _is_bootstrap_write_retry = any(
        "WRITE RETRY MODE" in str(m.get("content", ""))
        for m in context
        if str(m.get("role", "")).strip().lower() == "system"
    )
    if (
        _is_bootstrap_write_retry
        and _latest_user_for_guard.strip()
        and (not messages or str(messages[-1].get("role", "")).strip().lower() != "user")
    ):
        messages.append(
            {
                "role": "user",
                "content": _latest_user_for_guard,
                "metadata": {"plane": "control", "kind": "retry_write_user_anchor"},
            }
        )

    return messages
