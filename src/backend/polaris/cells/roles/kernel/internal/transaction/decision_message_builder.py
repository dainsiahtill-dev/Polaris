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
so the controller can delegate to it without any per-turn object allocation.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
    platform_tool_contract_is_single_batch,
)
from polaris.kernelone.context.prompt_safety import format_tool_failure_summary, parse_tool_failure_summary


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


def _sanitize_materialize_positive_task_contract_line(
    line: str,
    *,
    verification_deferred_to_governed_phase: bool = False,
) -> str | None:
    """Keep positive tool templates while stripping negative benchmark wording."""
    stripped = line.strip()
    lowered = stripped.lower()
    if not stripped:
        return stripped
    if stripped.startswith("TEMPLATE [General-Mutation]:"):
        return (
            "TEMPLATE [General-Mutation]: "
            "For create-file or full replacement tasks, call write_file immediately with the complete file body. "
            "For existing targeted edits, inspect only the specific target file when exact current content is needed, "
            "then use edit_blocks/edit_file/search_replace/repo_apply_diff. "
            "use append_to_file only for explicit append-at-end tasks. "
            "Verify after writing when a verification/read tool is available."
        )
    if stripped.startswith("TEMPLATE [Edit-Then-Verify]:"):
        return (
            "TEMPLATE [Edit-Then-Verify]: "
            "Step 1: read_file the target file only when exact existing content is needed. "
            "Step 2: use edit_blocks/edit_file/search_replace/repo_apply_diff for existing content changes; "
            "use write_file for create-file or full replacement tasks. "
            "Step 3: read_file again when verification is required."
        )
    if stripped.startswith("TEMPLATE [Search-Replace]:"):
        return (
            "TEMPLATE [Search-Replace]: "
            "Step 1: use repo_rg/ripgrep to locate occurrences when the target is unknown. "
            "Step 2: read_file the exact target file. "
            "Step 3: use edit_blocks, edit_file, search_replace, or repo_apply_diff to perform the replacement."
        )
    if stripped.startswith("COMPLETION CHECK:"):
        if verification_deferred_to_governed_phase:
            return (
                "COMPLETION CHECK: Finish this turn only after the required write/edit tools exposed in the "
                "current physical schema have been emitted. Verification remains mandatory in a later governed "
                "continuation or quality phase."
            )
        return (
            "COMPLETION CHECK: Finish only after the required write/edit and verification tools "
            "for this turn have been emitted."
        )
    if any(
        marker in lowered
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
        return None
    return line


def _infer_role_id_from_context(context: list[dict[str, Any]]) -> str:
    for message in context:
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            for key in ("role", "role_id", "actor", "expected_role_id"):
                value = str(metadata.get(key) or "").strip().lower()
                if value:
                    return value
        content = str(message.get("content") or "")
        lowered = content.lower()
        if "polaris 体系中的 **qa**" in lowered or "你是 polaris 体系中的 **qa**" in lowered:
            return "qa"
        if "polaris 体系中的 **director**" in lowered or "你是 polaris 体系中的 **director**" in lowered:
            return "director"
        if "polaris 体系中的 **chief engineer**" in lowered:
            return "chief_engineer"
        if "polaris 体系中的 **pm**" in lowered:
            return "pm"
    return ""


def _is_readonly_qa_judgement_turn(context: list[dict[str, Any]], latest_user: str) -> bool:
    role_id = _infer_role_id_from_context(context)
    if role_id != "qa":
        return False
    joined = "\n".join(str(message.get("content") or "") for message in context if isinstance(message, dict)).lower()
    latest = str(latest_user or "").lower()
    readonly_markers = (
        "do not inspect the workspace. do not call tools",
        "review the qa target using only the deterministic evidence already collected",
        "using only the deterministic evidence already collected by polaris",
        "code writing: forbidden",
        "代码写入: 禁止",
    )
    return any(marker in joined or marker in latest for marker in readonly_markers)


def _compact_tool_failure_messages(
    messages: list[dict[str, Any]],
    *,
    max_failure_kinds: int = 4,
) -> list[dict[str, Any]]:
    """Collapse repeated prompt-safe tool failures before final provider request."""

    aggregate: dict[tuple[str, str, str], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    first_failure_index: int | None = None
    failure_count = 0
    for message in messages:
        payload = parse_tool_failure_summary(message.get("content", ""))
        if payload is None:
            result.append(message)
            continue
        failure_count += 1
        if first_failure_index is None:
            first_failure_index = len(result)
            result.append({})
        key = (
            str(payload.get("tool") or "unknown"),
            str(payload.get("error_type") or "tool_failure"),
            str(payload.get("reason") or "tool execution failed"),
        )
        entry = aggregate.setdefault(
            key,
            {
                "tool": key[0],
                "error_type": key[1],
                "reason": key[2],
                "count": 0,
            },
        )
        entry["count"] = int(entry["count"]) + 1

    if first_failure_index is None or failure_count <= 1:
        return messages

    failures = sorted(aggregate.values(), key=lambda item: (-int(item["count"]), str(item["tool"])))
    included = failures[: max(1, int(max_failure_kinds))]
    digest = {
        "schema_version": "tool_failure_summary_digest.v1",
        "failure_count": sum(int(item["count"]) for item in failures),
        "unique_failure_count": len(failures),
        "failures": included,
        "omitted_failure_kinds": max(0, len(failures) - len(included)),
        "prompt_safe": True,
        "observation_only": True,
        "non_deliverable": True,
        "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
    }
    result[first_failure_index] = {
        "role": "system",
        "content": format_tool_failure_summary(digest),
        "name": "tool_failure_summary_digest",
        "metadata": {"plane": "control", "kind": "tool_failure_summary_digest"},
    }
    return result


def _physical_tool_names(tool_definitions: list[dict[str, Any]]) -> list[str]:
    """Project the exact tool names present in the final physical request."""

    names: list[str] = []
    for definition in tool_definitions:
        function_payload = definition.get("function")
        if isinstance(function_payload, dict):
            name = str(function_payload.get("name") or "").strip()
        else:
            name = str(definition.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def build_decision_messages(
    context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    ledger: TurnLedger | None = None,
) -> list[dict[str, Any]]:
    """Build decision-stage messages with single-batch execution constraints."""
    messages: list[dict[str, Any]] = _compact_tool_failure_messages(
        [dict(message) for message in context if message.get("metadata", {}).get("plane") != "control"]
    )
    if not tool_definitions:
        return messages

    # Single-batch execution is driven by platform contract metadata or delivery
    # mode. The multi-turn wording must not be used for a single-turn contract
    # because it gives the model permission to defer writes to a non-existent
    # next turn.
    _latest_user_for_guard = ""
    for _m in reversed(context):
        if isinstance(_m, dict) and str(_m.get("role", "")).strip().lower() == "user":
            _latest_user_for_guard = str(_m.get("content", ""))
            break
    _is_platform_contract_single_batch = platform_tool_contract_is_single_batch(context)
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

    if _is_readonly_qa_judgement_turn(context, _latest_user_for_guard):
        qa_guard = (
            "SYSTEM CONSTRAINT (QA Readonly): This is a QA evidence-judgement turn, not a mutation turn. "
            "Use only the deterministic evidence already supplied in the prompt. "
            "Do NOT inject or follow write/edit task-contract guidance, do NOT create mutation target files, "
            "and do NOT call tools when the QA output contract forbids tool use."
        )
        messages.append({"role": "system", "content": qa_guard, "metadata": {"plane": "control"}})
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
    elif _is_platform_contract_single_batch or _is_materialize_single_batch:
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
                "For create-file, scaffold, or full-replacement tasks, emit write_file/edit_file in this batch "
                "instead of starting with read-only exploration. "
                "Targeted reads are allowed only when exact existing content is required, and must be paired "
                "with the required write/edit call in the same batch. "
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
    physical_tool_names = _physical_tool_names(tool_definitions)
    if physical_tool_names:
        messages.append(
            {
                "role": "system",
                "content": (
                    "CURRENT TURN PHYSICAL TOOL SCHEMA: "
                    + ", ".join(physical_tool_names)
                    + ". Only these tools are callable in this Provider request; this exact schema "
                    "supersedes broader role-capability lists. Tools not listed here are unavailable "
                    "in this Provider request. Never claim or promise a tool action that this schema "
                    "does not expose."
                ),
                "metadata": {"plane": "control", "kind": "physical_tool_schema_truth"},
            }
        )
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
                if _is_quality_repair and _line_conflicts_with_quality_repair(line):
                    continue
                positive_line = _sanitize_materialize_positive_task_contract_line(
                    line,
                    verification_deferred_to_governed_phase=bool(
                        _task_contract_metadata.get("verification_deferred_to_governed_phase")
                    ),
                )
                if positive_line is not None:
                    positive_lines.append(positive_line)
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
