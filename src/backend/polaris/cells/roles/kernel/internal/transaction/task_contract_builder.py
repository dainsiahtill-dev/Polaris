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
import json
import re
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import (
    FILE_TOKEN_EXTENSION_PATTERN,
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

_SESSION_PATCH_BLOCK_RE = re.compile(r"<SESSION_PATCH>\s*(.*?)\s*</SESSION_PATCH>", flags=re.DOTALL)
_SUPER_READONLY_STAGE_MARKERS: tuple[str, ...] = (
    "[SUPER_MODE_READONLY_STAGE]",
    "[/SUPER_MODE_READONLY_STAGE]",
    "stage_type: readonly_planning",
)
_EXPLICIT_DELIVERY_MODE_MARKERS: tuple[str, ...] = (
    "[mode:materialize]",
    "[mode:materialize_changes]",
    "[mode:propose]",
    "[mode:propose_patch]",
    "[mode:analyze]",
    "[mode:analyze_only]",
)
_SINGLE_TARGET_QUALITY_REPAIR_RE = re.compile(
    r"\[director_quality_repair:(?:write_only_single_target|edit_preferred_single_target)\].*?- Target path:\s*(?P<path>[^\n\r]+)",
    flags=re.DOTALL,
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_tool_token(value: object) -> str:
    normalized = str(value or "").strip().strip("`'\". ").lower()
    return TOOL_ALIASES.get(normalized, normalized) if normalized else ""


def _extract_single_target_quality_repair_path(text: str) -> str:
    match = _SINGLE_TARGET_QUALITY_REPAIR_RE.search(str(text or ""))
    if not match:
        return ""
    values = _normalize_contract_path_values(str(match.group("path") or "").strip())
    return values[0] if values else ""


def _normalize_tool_list(value: object) -> list[str]:
    if isinstance(value, str):
        raw_values: list[object] = [item for item in re.split(r"[,;\s]+", value) if item]
    elif isinstance(value, list | tuple):
        raw_values = list(value)
    else:
        return []
    normalized_values: list[str] = []
    for raw_item in raw_values:
        normalized = _normalize_tool_token(raw_item)
        if normalized and normalized not in normalized_values:
            normalized_values.append(normalized)
    return normalized_values


def _normalize_tool_groups(value: object) -> list[list[str]]:
    if not isinstance(value, list | tuple):
        return []
    groups: list[list[str]] = []
    for raw_group in value:
        group = _normalize_tool_list(raw_group)
        if group:
            groups.append(group)
    return groups


_NO_EXTENSION_FILE_NAMES: frozenset[str] = frozenset(
    {
        "dockerfile",
        "makefile",
        "readme",
        "license",
        "changelog",
        "contributing",
        ".env",
        ".gitignore",
        "go.mod",
        "go.sum",
    }
)


def _normalize_contract_path_values(value: object) -> list[str]:
    if isinstance(value, str):
        raw_values: list[object] = [item for item in re.split(r"[,;\n]+", value) if item.strip()]
    elif isinstance(value, list | tuple):
        raw_values = list(value)
    else:
        return []

    normalized_values: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_values:
        token = str(raw_item or "").strip().strip("`'\"").replace("\\", "/")
        while token.startswith("./"):
            token = token[2:]
        token = token.rstrip("/") if token != "/" else token
        if not token or token.startswith(("/", "~")):
            continue
        parts = [part for part in token.split("/") if part]
        if not parts or any(part == ".." for part in parts):
            continue
        if any(ch in token for ch in ("*", "?", "[", "]", "{", "}", "\t", "\r")):
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized_values.append(token)
    return normalized_values


def _contract_path_looks_like_file_target(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1].lower()
    if basename in _NO_EXTENSION_FILE_NAMES:
        return True
    return "." in basename


def extract_platform_tool_contract(context: list[dict]) -> dict[str, Any]:
    """Extract the platform tool contract from context metadata."""
    contract: dict[str, Any] = {}
    for message in context:
        if not isinstance(message, Mapping):
            continue
        metadata = _mapping(message.get("metadata"))
        for raw_candidate in (
            message.get("tool_contract"),
            message.get("platform_tool_contract"),
            metadata.get("tool_contract"),
            metadata.get("platform_tool_contract"),
        ):
            candidate = _mapping(raw_candidate)
            if candidate:
                contract.update(candidate)
    return contract


def extract_platform_tool_contract_target_files(context: list[dict]) -> tuple[str, ...]:
    """Return explicit file targets carried by platform tool-contract metadata."""
    contract = extract_platform_tool_contract(context)
    targets: list[str] = []
    for key in (
        "target_files",
        "targets",
        "write_targets",
        "required_artifacts",
        "repair_target_files",
        "missing_target_files",
    ):
        targets.extend(
            path
            for path in _normalize_contract_path_values(contract.get(key))
            if _contract_path_looks_like_file_target(path)
        )
    for key in ("allowed_paths", "allowed_scope"):
        targets.extend(
            path
            for path in _normalize_contract_path_values(contract.get(key))
            if _contract_path_looks_like_file_target(path)
        )
    return tuple(dict.fromkeys(targets))


def extract_platform_tool_contract_scope_paths(context: list[dict]) -> tuple[str, ...]:
    """Return directory scopes carried by platform tool-contract metadata."""
    contract = extract_platform_tool_contract(context)
    scopes: list[str] = []
    for key in ("scope_paths", "allowed_scope_paths", "write_scopes", "scope_dirs", "allowed_paths", "allowed_scope"):
        scopes.extend(
            path
            for path in _normalize_contract_path_values(contract.get(key))
            if not _contract_path_looks_like_file_target(path)
        )
    return tuple(dict.fromkeys(scopes))


def platform_tool_contract_is_single_batch(context: list[dict]) -> bool:
    """Return True when the context metadata pins single-batch execution."""
    contract = extract_platform_tool_contract(context)
    mode = str(contract.get("execution_mode") or "").strip().lower()
    return bool(
        contract.get("single_batch")
        or contract.get("tool_contract_single_batch")
        or contract.get("single_batch_execution")
        or mode in {"single_batch", "single-batch"}
    )


def platform_tool_contract_bypasses_read_write_barrier(context: list[dict]) -> bool:
    """Return True when a contract explicitly allows mixed read/write batches."""
    contract = extract_platform_tool_contract(context)
    return bool(
        contract.get("allow_mixed_read_write_batch")
        or contract.get("bypass_read_write_barrier")
        or contract.get("external_batch_rules")
    )


def platform_tool_contract_disables_phase_manager(context: list[dict]) -> bool:
    """Return True when an external contract owns phase progression for this batch."""
    contract = extract_platform_tool_contract(context)
    return bool(contract.get("disable_phase_manager") or contract.get("external_batch_rules"))


def _outer_explicit_delivery_mode_marker(content: str) -> str | None:
    goal_start = content.lower().find("<goal>")
    prefix = content if goal_start < 0 else content[:goal_start]
    lowered_prefix = prefix.lower()
    for marker in _EXPLICIT_DELIVERY_MODE_MARKERS:
        if marker in lowered_prefix:
            return marker
    return None


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
    if not instruction:
        return None
    mode_marker = _outer_explicit_delivery_mode_marker(content)
    if mode_marker is not None and mode_marker not in instruction.lower():
        return f"{mode_marker}\n{instruction}"
    return instruction


def extract_continuation_prompt_metadata(content: str) -> dict[str, Any]:
    """从 continuation prompt 中提取显式元数据。

    当前仅消费 <SESSION_PATCH> 块，以便 kernel 在续跑 turn 时不依赖
    fresh ledger 的历史冻结态即可恢复 delivery_mode 等 continuation contract。
    """
    match = _SESSION_PATCH_BLOCK_RE.search(content)
    if match is None:
        return {}
    raw_patch = match.group(1).strip()
    if not raw_patch:
        return {}
    try:
        patch = json.loads(raw_patch)
    except json.JSONDecodeError:
        return {}
    if not isinstance(patch, dict):
        return {}

    metadata: dict[str, Any] = {}
    delivery_mode = str(patch.get("delivery_mode") or "").strip().lower()
    if delivery_mode:
        metadata["delivery_mode"] = delivery_mode

    task_progress = str(patch.get("task_progress") or "").strip().lower()
    if task_progress:
        metadata["task_progress"] = task_progress

    recent_reads = patch.get("recent_reads")
    if isinstance(recent_reads, list):
        normalized_reads = [str(item).strip() for item in recent_reads if str(item).strip()]
        if normalized_reads:
            metadata["recent_reads"] = normalized_reads

    return metadata


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
    if any(marker in latest_user for marker in _SUPER_READONLY_STAGE_MARKERS):
        return "", {}
    platform_tool_contract = extract_platform_tool_contract(context)
    mixed_read_write_batch_allowed = platform_tool_contract_bypasses_read_write_barrier(context)
    single_quality_repair_target = _extract_single_target_quality_repair_path(latest_user)

    target_file_tokens = [
        token.strip()
        for token in re.findall(
            r"\b[\w./\\-]+\.(?:" + FILE_TOKEN_EXTENSION_PATTERN + r")\b", latest_user, flags=re.IGNORECASE
        )
        if token.strip()
    ]
    dedup_target_files: list[str] = []
    seen_targets: set[str] = set()
    for token in [*target_file_tokens, *extract_platform_tool_contract_target_files(context)]:
        key = token.lower()
        if key in seen_targets:
            continue
        seen_targets.add(key)
        dedup_target_files.append(token)
    if single_quality_repair_target:
        dedup_target_files = [single_quality_repair_target]

    # Classify intent on the real task instruction when the platform contract
    # supplies one; tool-contract metadata must not pollute mutation detection.
    _task_instruction = str(
        platform_tool_contract.get("task_instruction") or platform_tool_contract.get("instruction") or latest_user
    ).strip()
    _requires_write = requires_mutation_intent(_task_instruction)
    _requires_verify = requires_verification_intent(_task_instruction)
    if single_quality_repair_target:
        _requires_write = True
        _requires_verify = False

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
    for normalized in _normalize_tool_list(platform_tool_contract.get("required_tools")):
        if normalized not in required_tools_from_contract:
            required_tools_from_contract.append(normalized)
        mapped = available_tools_map.get(normalized)
        if mapped:
            if mapped not in required_tools_present:
                required_tools_present.append(mapped)
        elif normalized not in required_tools_missing:
            required_tools_missing.append(normalized)
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
    required_any_groups_from_contract.extend(
        _normalize_tool_groups(
            platform_tool_contract.get("required_tool_groups")
            or platform_tool_contract.get("required_any_groups")
            or platform_tool_contract.get("ordered_tool_groups")
        )
    )
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
    with contextlib.suppress(TypeError, ValueError):
        min_calls_required = max(
            min_calls_required,
            int(platform_tool_contract.get("min_tool_calls") or platform_tool_contract.get("minimum_tool_calls") or 0),
        )
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

    if single_quality_repair_target:
        required_tools_from_contract = [
            tool for tool in required_tools_from_contract if tool in write_candidates or tool == "write_file"
        ]
        required_tools_missing = [
            tool for tool in required_tools_missing if tool in write_candidates or tool == "write_file"
        ]
        required_any_groups_from_contract = []
        required_any_groups_resolved = []
        min_calls_required = 1

    # 如果契约中显式要求写/验证工具，提升意图标记
    if required_tools_from_contract:
        _requires_write = _requires_write or any(tool in write_candidates for tool in required_tools_from_contract)
        if not single_quality_repair_target:
            _requires_verify = _requires_verify or any(
                tool in verify_candidates for tool in required_tools_from_contract
            )
    if required_any_groups_from_contract:
        flattened = [token for group in required_any_groups_from_contract for token in group]
        _requires_write = _requires_write or any(tool in write_candidates for tool in flattened)
        if not single_quality_repair_target:
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
            "Contract-required tools are mandatory in this single batch: "
            + ", ".join(required_tools_from_contract)
            + "."
        )
        lines.append("Do not substitute optional read tools for contract-required tools.")
    for missing_tool in required_tools_missing:
        equivalents = [
            candidate for candidate in REQUIRED_TOOL_EQUIVALENTS.get(missing_tool, ()) if candidate in available_tools
        ]
        if equivalents:
            lines.append(
                f"Required contract tool `{missing_tool}` is not exposed in this profile; satisfy it via equivalent tools in this batch: "
                + ", ".join(equivalents)
                + "."
            )
    if required_any_groups_from_contract:
        rendered_contract_groups = " -> ".join(f"[{', '.join(group)}]" for group in required_any_groups_from_contract)
        lines.append(
            "Contract-required tool groups must all be satisfied in this single batch: "
            + rendered_contract_groups
            + "."
        )
        if required_any_groups_resolved:
            rendered_resolved_groups = " -> ".join(f"[{', '.join(group)}]" for group in required_any_groups_resolved)
            lines.append("Use available tools to satisfy each group in order: " + rendered_resolved_groups + ".")
        lines.append("A batch that only satisfies the first group is invalid for this contract.")
    if min_calls_required > 0:
        lines.append(f"Contract minimum tool-call count for this batch: >= {min_calls_required}.")
        if min_calls_required > 1:
            lines.append("A single read-only tool call is invalid; include all required calls before final text.")
    if _requires_write:
        if selected_write:
            if single_quality_repair_target:
                lines.append(
                    "Single-target quality repair is active. Emit exactly one write/edit tool call for "
                    f"`{single_quality_repair_target}`; prefer edit_file when an exact local replacement is enough. "
                    "Do not read, list, explore, verify, or touch sibling files."
                )
            else:
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
            if single_quality_repair_target:
                lines.append("VALID pattern: emit exactly one write/edit tool call for the named target.")
            elif mixed_read_write_batch_allowed:
                lines.append(
                    "VALID pattern: emit [search/read tool] then [write tool] in the same batch "
                    "(all steps must complete in this single tool-call batch)."
                )
            else:
                # BUG-01 compound fix: removed the self-contradicting
                # "MULTI-TURN WORKFLOW: first turn read_file" paragraph.
                # That line told the model it may defer writes to a later turn,
                # directly contradicting the HARD GATE "no write = rejected" rule
                # that immediately precedes it.  In single-batch contract mode
                # there is no later turn, so the paragraph caused silent failures.
                # The transaction kernel still enforces the read/write barrier
                # unless the platform tool contract explicitly bypasses it, so
                # the positive example must not instruct models to mix read and
                # write tools in one parallel batch by default.
                lines.append(
                    "VALID pattern: use the provided context to emit write/edit tool calls now. "
                    "Do not mix read/search tools with write/edit tools in one parallel batch unless "
                    "the platform tool contract explicitly allows mixed read/write batches."
                )
        else:
            lines.append("This request requires mutation. Do not stop after read-only tools.")
        if dedup_target_files and not single_quality_repair_target:
            lines.append(
                "Mutation target files detected from user request: "
                + ", ".join(dedup_target_files[:6])
                + ". The write step must cover every listed target file required by the current task; "
                "for multi-file create tasks, emit one write/edit call per target file instead of stopping "
                "after the first successful write."
            )
    if _requires_verify and not single_quality_repair_target:
        if selected_verify:
            lines.append(
                "Verification is required by the user. Include verification tools in the same batch: "
                + ", ".join(selected_verify)
                + "."
            )
        else:
            lines.append("Verification is required by the user. Include an available verification step.")

    # --- 追加正例序列模板和恢复协议 ---
    sequence_template = (
        ""
        if single_quality_repair_target
        else build_sequence_template(
            required_tools=required_tools_from_contract,
            required_any_groups=required_any_groups_from_contract,
            ordered_tool_groups=required_any_groups_from_contract,
            min_tool_calls=min_calls_required,
            requires_write=_requires_write,
            requires_verify=_requires_verify,
        )
    )
    if sequence_template:
        lines.append(sequence_template)

    recovery_protocol = (
        ""
        if single_quality_repair_target
        else build_recovery_protocol(
            required_tools=required_tools_from_contract,
            required_any_groups=required_any_groups_from_contract,
            available_write_tools=selected_write,
        )
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
    if single_quality_repair_target:
        expected_read_count = 0

    contract_text = "\n".join(lines)
    metadata: dict[str, Any] = {"expected_read_count": expected_read_count}
    return contract_text, metadata
