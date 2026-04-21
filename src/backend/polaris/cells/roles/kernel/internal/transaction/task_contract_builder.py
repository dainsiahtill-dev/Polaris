"""单批次任务契约提示构建。

从用户消息和工具定义中解析：
- 目标文件
- 必需工具 / 工具组
- 最小调用次数
- 突变 / 验证意图

然后生成注入到 system prompt 的 TASK CONTRACT 文本。
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import (
    REQUIRED_TOOL_EQUIVALENTS,
    TOOL_ALIASES,
    VERIFICATION_TOOLS,
    WRITE_TOOLS,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent,
    requires_verification_intent,
)
from polaris.cells.roles.kernel.internal.transaction.tool_sequence_templates import (
    build_recovery_protocol,
    build_sequence_template,
    extract_expected_read_count,
)

# Sentinel prefix that marks the start of injected Benchmark boilerplate.
# Everything from this marker onwards is framework metadata, not user intent.
_BENCHMARK_CONTRACT_MARKER: str = "[Benchmark Tool Contract]"


def _strip_benchmark_boilerplate(text: str) -> str:
    """Return only the real task instruction, stripping Benchmark Tool Contract boilerplate.

    The evaluator appends a ``[Benchmark Tool Contract]`` section to every
    benchmark user message.  That section contains words like ``replace``,
    ``edit``, ``emit``, ``build`` and Chinese config/devops terms that
    mislead ``requires_mutation_intent`` into a false-positive classification
    for purely read-only tasks.

    Stripping the boilerplate before intent classification ensures the
    classifier sees only the genuine task description.
    """
    idx = text.find(_BENCHMARK_CONTRACT_MARKER)
    if idx == -1:
        return text
    return text[:idx].strip()


def _extract_instruction_from_continuation_prompt(content: str) -> str | None:
    """从 orchestrator continuation prompt 中提取 <Instruction> 块内容。

    continuation prompt 格式：
      <Goal>...</Goal>
      <Progress>...</Progress>
      <WorkingMemory>...</WorkingMemory>
      <Instruction>...</Instruction>

    若检测到该格式，仅返回 <Instruction> 块内的文本，避免历史 <Goal>
    中的突变关键词污染当前 turn 的意图分类。
    """
    has_goal = "<Goal>" in content and "</Goal>" in content
    has_instruction = "<Instruction>" in content and "</Instruction>" in content
    if not (has_goal and has_instruction):
        return None

    # 提取 <Instruction>...</Instruction> 之间的内容
    start = content.find("<Instruction>") + len("<Instruction>")
    end = content.find("</Instruction>")
    if start < 0 or end < 0 or end <= start:
        return None
    instruction = content[start:end].strip()
    return instruction if instruction else None


def extract_latest_user_message(context: list[dict]) -> str:
    """从 conversation context 中提取最新的用户消息。"""
    for message in reversed(context):
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role != "user":
            continue
        latest_user = str(message.get("content") or "").strip()
        if not latest_user:
            continue
        instruction = _extract_instruction_from_continuation_prompt(latest_user)
        if instruction is not None:
            return instruction
        return latest_user
    return ""


def extract_tool_name_from_definition(item: Mapping[str, Any]) -> str:
    """从 tool definition 字典中提取工具名。"""
    function_payload = item.get("function")
    if isinstance(function_payload, Mapping):
        return str(function_payload.get("name") or "").strip()
    return str(item.get("name") or "").strip()


def extract_allowed_tool_names_from_definitions(tool_definitions: list[dict]) -> set[str]:
    """从 tool definitions 中提取所有允许的工具名。"""
    allowed: set[str] = set()
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        tool_name = extract_tool_name_from_definition(raw_item)
        if tool_name:
            allowed.add(tool_name)
    return allowed


def build_single_batch_task_contract_hint(
    context: list[dict],
    tool_definitions: list[dict],
) -> tuple[str, dict[str, Any]]:
    """构建单次批次的任务契约提示文本。

    解析用户消息中的隐含约束（目标文件、必需工具、最小调用次数），
    并生成 LLM 必须遵守的 HARD GATE 规则。

    Returns:
        Tuple of (contract_text, metadata_dict).
        metadata_dict contains keys like "expected_read_count" for circuit breaker tuning.
    """
    latest_user = extract_latest_user_message(context)
    if not latest_user:
        return "", {}

    target_file_tokens = [
        token.strip()
        for token in re.findall(
            r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|js|ts|tsx|jsx|css|html)\b",
            latest_user,
            flags=re.IGNORECASE,
        )
        if token.strip()
    ]
    dedup_target_files: list[str] = []
    seen_targets: set[str] = set()
    for token in target_file_tokens:
        key = token.lower()
        if key in seen_targets:
            continue
        seen_targets.add(key)
        dedup_target_files.append(token)

    # BUG-04 fix: classify intent on the *real* task instruction only.
    # Benchmark boilerplate contains mutation-adjacent English tokens that
    # trigger false-positive DEVOPS/STRONG_MUTATION classification.
    _task_instruction = _strip_benchmark_boilerplate(latest_user)
    _requires_write = requires_mutation_intent(_task_instruction)
    _requires_verify = requires_verification_intent(_task_instruction)

    # --- 构建可用工具映射（必须在 required_tools 解析之前）---
    available_tools: list[str] = []
    for item in tool_definitions:
        if not isinstance(item, Mapping):
            continue
        function_payload = item.get("function")
        if isinstance(function_payload, Mapping):
            name = str(function_payload.get("name") or "").strip()
        else:
            name = str(item.get("name") or "").strip()
        if name:
            available_tools.append(name)
    if not available_tools:
        return "", {}

    write_candidates = tuple(WRITE_TOOLS)
    verify_candidates = tuple(VERIFICATION_TOOLS)
    selected_write = [tool for tool in available_tools if tool in write_candidates]
    selected_verify = [tool for tool in available_tools if tool in verify_candidates]
    available_tools_map = {tool.lower(): tool for tool in available_tools}

    # --- 解析必需工具（无论是否 mutation，都要提取 contract 约束）---
    # C4 修复：必须在 early return 之前解析，否则 required_tools_from_contract 永不执行
    required_tools_from_contract: list[str] = []
    required_tools_present: list[str] = []
    required_tools_missing: list[str] = []
    required_tools_match = re.search(
        r"required\s+tools\s*\(at\s+least\s+once\)\s*:\s*([^\n\r]+)",
        latest_user,
        flags=re.IGNORECASE,
    )
    if required_tools_match:
        raw_segment = str(required_tools_match.group(1) or "").strip()
        for raw_tool in raw_segment.split(","):
            normalized = raw_tool.strip().strip("`'\". ").lower()
            if not normalized:
                continue
            normalized = TOOL_ALIASES.get(normalized, normalized)
            if normalized not in required_tools_from_contract:
                required_tools_from_contract.append(normalized)
            mapped = available_tools_map.get(normalized)
            if mapped:
                if mapped not in required_tools_present:
                    required_tools_present.append(mapped)
            elif normalized not in required_tools_missing:
                required_tools_missing.append(normalized)

    # --- 解析必需工具组 ---
    required_any_groups_from_contract: list[list[str]] = []
    required_any_groups_match = re.search(
        r"required\s+tool\s+groups\s*:\s*([^\n\r]+)",
        latest_user,
        flags=re.IGNORECASE,
    )
    if required_any_groups_match:
        groups_segment = str(required_any_groups_match.group(1) or "").strip()
        for raw_group in re.findall(r"\[([^\]]+)\]", groups_segment):
            normalized_group: list[str] = []
            for raw_tool in raw_group.split(","):
                normalized = raw_tool.strip().strip("`'\". ").lower()
                if not normalized:
                    continue
                normalized = TOOL_ALIASES.get(normalized, normalized)
                if normalized not in normalized_group:
                    normalized_group.append(normalized)
            if normalized_group:
                required_any_groups_from_contract.append(normalized_group)

    required_any_groups_resolved: list[list[str]] = []
    # BUG-06 fix: Tool priority order within write-groups.
    # Models (especially MiniMax) frequently confuse edit_file's complex
    # signature (requires start_line/end_line or search/replace) with
    # write_file's simple signature (just file + content).  By placing
    # simpler/safer tools first in the resolved group, the model is more
    # likely to pick a tool whose signature it can correctly fill.
    _write_group_priority: dict[str, int] = {
        "append_to_file": 0,  # simplest: file + content
        "precision_edit": 1,  # structured but well-defined
        "repo_apply_diff": 2,  # diff-based, models handle reasonably
        "search_replace": 3,  # search + replace pair
        "edit_file": 4,  # complex: needs start_line/end_line OR search/replace
        "write_file": 5,  # destructive full-overwrite, last resort
    }
    for group in required_any_groups_from_contract:
        resolved_group: list[str] = []
        for group_item in group:
            mapped = available_tools_map.get(group_item)
            if mapped and mapped not in resolved_group:
                resolved_group.append(mapped)
                continue
            equivalents = [
                candidate
                for candidate in REQUIRED_TOOL_EQUIVALENTS.get(group_item, ())
                if candidate in available_tools and candidate not in resolved_group
            ]
            resolved_group.extend(equivalents)
        if resolved_group:
            # Sort write-tools by safety priority; leave non-write tools in original order
            resolved_group.sort(key=lambda t: _write_group_priority.get(t, 99))
            required_any_groups_resolved.append(resolved_group)

    # --- 解析最小调用次数 ---
    min_calls_required = 0
    min_calls_match = re.search(
        r"tool\s+call\s+count\s*must\s*be\s*>=\s*(\d+)",
        latest_user,
        flags=re.IGNORECASE,
    )
    between_match = re.search(
        r"tool\s+call\s+count\s+must\s+be\s+between\s+(\d+)\s+and\s+(\d+)",
        latest_user,
        flags=re.IGNORECASE,
    )
    if between_match:
        with contextlib.suppress(TypeError, ValueError):
            min_calls_required = max(min_calls_required, int(between_match.group(1)))
    if min_calls_match:
        with contextlib.suppress(TypeError, ValueError):
            min_calls_required = max(min_calls_required, int(min_calls_match.group(1)))

    # 如果契约中显式要求写/验证工具，提升意图标记
    if required_tools_from_contract:
        _requires_write = _requires_write or any(tool in write_candidates for tool in required_tools_from_contract)
        _requires_verify = _requires_verify or any(tool in verify_candidates for tool in required_tools_from_contract)
    if required_any_groups_from_contract:
        flattened = [token for group in required_any_groups_from_contract for token in group]
        _requires_write = _requires_write or any(tool in write_candidates for tool in flattened)
        _requires_verify = _requires_verify or any(tool in verify_candidates for tool in flattened)

    # C4 修复：只有「既无 mutation 意图又无显式 contract 约束」才早期返回
    if not _requires_write and not _requires_verify and not required_tools_from_contract:
        return "", {}

    lines = [
        "TASK CONTRACT (single-batch planning):",
        "Read-only exploration tools alone are invalid if the user explicitly requests modify/create/verify.",
    ]
    if required_tools_from_contract:
        lines.append(
            "Benchmark-required tools are mandatory in this single batch: "
            + ", ".join(required_tools_from_contract)
            + "."
        )
        lines.append("Do not substitute optional read tools for benchmark-required tools.")
    for missing_tool in required_tools_missing:
        equivalents = [
            candidate for candidate in REQUIRED_TOOL_EQUIVALENTS.get(missing_tool, ()) if candidate in available_tools
        ]
        if equivalents:
            lines.append(
                f"Required benchmark tool `{missing_tool}` is not exposed in this profile; satisfy it via equivalent tools in this batch: "
                + ", ".join(equivalents)
                + "."
            )
    if required_any_groups_from_contract:
        rendered_contract_groups = " -> ".join(f"[{', '.join(group)}]" for group in required_any_groups_from_contract)
        lines.append(
            "Benchmark-required tool groups must all be satisfied in this single batch: "
            + rendered_contract_groups
            + "."
        )
        if required_any_groups_resolved:
            rendered_resolved_groups = " -> ".join(f"[{', '.join(group)}]" for group in required_any_groups_resolved)
            lines.append("Use available tools to satisfy each group in order: " + rendered_resolved_groups + ".")
        lines.append("A batch that only satisfies the first group is invalid for this benchmark case.")
    if min_calls_required > 0:
        lines.append(f"Benchmark minimum tool-call count for this batch: >= {min_calls_required}.")
        if min_calls_required > 1:
            lines.append("A single read-only tool call is invalid; include all required calls before final text.")
    if _requires_write:
        if selected_write:
            lines.append(
                "This request requires mutation. Include at least one write tool in the same batch: "
                + ", ".join(selected_write)
                + "."
            )
            lines.append(
                "HARD GATE: if your tool batch contains no write tool call, your plan is invalid and will be rejected."
            )
            lines.append(
                "HARD GATE: plain-text-only completion without any tool call is invalid for this mutation request."
            )
            # BUG-01 compound fix: removed the self-contradicting
            # "MULTI-TURN WORKFLOW: first turn read_file" paragraph.
            # That line told the model it may defer writes to a later turn,
            # directly contradicting the HARD GATE "no write = rejected" rule
            # that immediately precedes it.  In single-batch benchmark mode
            # there is no later turn, so the paragraph caused silent failures.
            lines.append(
                "INVALID completion: plain-text code dump without any tool call (rejected as inline patch escape)."
            )
            lines.append(
                "HARD GATE: Do NOT ask the user for confirmation, plan approval, or 'next step' instructions. "
                "The user has already authorized execution. Proceed directly with tool calls."
            )
            lines.append(
                "INVALID completion: text-only responses such as 'I will now...', 'Please confirm...', "
                "'Here is the plan...' — these are rejected. Only tool calls are accepted."
            )
            lines.append(
                "VALID pattern: emit [search/read tool] then [write tool] in the same batch "
                "(all steps must complete in this single tool-call batch)."
            )
        else:
            lines.append("This request requires mutation. Do not stop after read-only tools.")
        if dedup_target_files:
            lines.append(
                "Mutation target files detected from user request: "
                + ", ".join(dedup_target_files[:6])
                + ". Ensure the write step touches at least one target file."
            )
    if _requires_verify:
        if selected_verify:
            lines.append(
                "Verification is required by the user. Include verification tools in the same batch: "
                + ", ".join(selected_verify)
                + "."
            )
        else:
            lines.append("Verification is required by the user. Include an available verification step.")

    # --- 追加正例序列模板和恢复协议 ---
    sequence_template = build_sequence_template(
        required_tools=required_tools_from_contract,
        required_any_groups=required_any_groups_from_contract,
        ordered_tool_groups=required_any_groups_from_contract,
        min_tool_calls=min_calls_required,
        requires_write=_requires_write,
        requires_verify=_requires_verify,
    )
    if sequence_template:
        lines.append(sequence_template)

    recovery_protocol = build_recovery_protocol(
        required_tools=required_tools_from_contract,
        required_any_groups=required_any_groups_from_contract,
        available_write_tools=selected_write,
    )
    if recovery_protocol:
        lines.append(recovery_protocol)

    # 计算 expected_read_count 供 Circuit Breaker 使用
    expected_read_count = extract_expected_read_count(
        required_tools=required_tools_from_contract,
        ordered_tool_groups=required_any_groups_from_contract,
        min_tool_calls=min_calls_required,
        requires_write=_requires_write,
    )

    contract_text = "\n".join(lines)
    metadata: dict[str, Any] = {"expected_read_count": expected_read_count}
    return contract_text, metadata
