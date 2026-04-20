"""突变合约守卫 — 验证 write tool 与目标文件的一致性。

包含：
- 工具调用元数据提取（工具名、执行模式、目标文件）
- 读/写目标路径提取与归一化
- 突变目标漂移检测（用户要求修改 A，LLM 却写 B）
- stale-edit bootstrap 决策合成
- 安全只读引导工具判定
- 工具批次写操作检测
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from polaris.cells.roles.kernel.internal.transaction.constants import (
    READ_TOOLS,
    SAFE_READ_BOOTSTRAP_TOOLS,
    WRITE_TOOLS,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    TurnDecision,
    TurnDecisionKind,
)

# ---------------------------------------------------------------------------
# 元数据提取
# ---------------------------------------------------------------------------


def extract_invocation_tool_name(invocation: Any) -> str:
    """从 invocation 对象或字典中提取工具名。"""
    if isinstance(invocation, Mapping):
        return str(invocation.get("tool_name") or invocation.get("tool") or "").strip()
    return str(getattr(invocation, "tool_name", "") or getattr(invocation, "tool", "") or "").strip()


def extract_invocation_execution_mode(invocation: Any) -> str:
    """从 invocation 对象或字典中提取 execution_mode。"""
    if isinstance(invocation, Mapping):
        raw_mode = invocation.get("execution_mode")
    else:
        raw_mode = getattr(invocation, "execution_mode", None)
    if isinstance(raw_mode, Enum):
        return str(raw_mode.value or "").strip()
    return str(raw_mode or "").strip()


def extract_target_file_from_invocation_args(invocation: Any) -> str:
    """从 invocation 参数中提取目标文件路径。"""
    raw_args = (
        invocation.get("arguments") if isinstance(invocation, Mapping) else getattr(invocation, "arguments", None)
    )
    if not isinstance(raw_args, Mapping):
        return ""
    for key in ("file", "path", "filepath", "target"):
        value = raw_args.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ""


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def extract_target_files_from_message(message: str) -> list[str]:
    """从用户消息中提取疑似目标文件路径。"""
    raw = str(message or "")
    if not raw:
        return []
    # 匹配带扩展名的文件路径
    ext_tokens = re.findall(
        r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|js|ts|tsx|jsx|css|html)\b",
        raw,
        flags=re.IGNORECASE,
    )
    # 匹配常见无扩展名文件（Makefile, Dockerfile, README, LICENSE, .env, .gitignore 等）
    no_ext_tokens = re.findall(
        r"\b(?:Makefile|Dockerfile|README|LICENSE|CHANGELOG|CONTRIBUTING|\.env\.?\w*|\.gitignore)\b",
        raw,
        flags=re.IGNORECASE,
    )
    tokens = ext_tokens + no_ext_tokens
    seen: set[str] = set()
    deduped: list[str] = []
    for token in tokens:
        normalized = str(token or "").strip().replace("\\", "/")
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(normalized)
    return deduped


def normalize_path_token(path: str) -> str:
    """归一化路径 token（去反斜杠、去前导 ./，保留点文件）。

    .. warning::
        旧实现使用 ``lstrip("./")`` 会错误移除点文件的前导点
        （如 ``.gitignore`` → ``gitignore``）。本实现仅移除 ``./`` 前缀组合。
    """
    normalized = str(path or "").strip().replace("\\", "/")
    # 只移除前导的 "./" 组合，不移除单个 "."
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def build_path_match_candidates(paths: list[str]) -> set[str]:
    """构建路径匹配候选集（全路径 + basename）。"""
    candidates: set[str] = set()
    for raw_path in paths:
        normalized = normalize_path_token(raw_path)
        if not normalized:
            continue
        lowered = normalized.lower()
        candidates.add(lowered)
        basename = normalized.rsplit("/", 1)[-1].strip().lower()
        if basename:
            candidates.add(basename)
    return candidates


def expand_bootstrap_read_candidates(target_file: str) -> list[str]:
    """为 bootstrap read 展开候选路径。"""
    normalized = str(target_file or "").strip().replace("\\", "/")
    if not normalized:
        return []
    candidates: list[str] = [normalized]
    basename = normalized.rsplit("/", 1)[-1].strip()
    if basename and basename not in candidates:
        candidates.append(basename)
    return candidates


# ---------------------------------------------------------------------------
# 目标提取
# ---------------------------------------------------------------------------


def extract_read_targets_from_invocations(invocations: list[Any]) -> list[str]:
    """从 invocations 中提取 read 操作的目标文件。"""
    targets: list[str] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        if tool_name not in READ_TOOLS:
            continue
        target_file = extract_target_file_from_invocation_args(invocation)
        if target_file:
            targets.append(target_file)
    return targets


def is_write_invocation(invocation: Any) -> bool:
    """判定 invocation 是否为写操作。"""
    tool_name = extract_invocation_tool_name(invocation)
    if tool_name in WRITE_TOOLS:
        return True
    mode = extract_invocation_execution_mode(invocation)
    return mode == str(ToolExecutionMode.WRITE_SERIAL)


def extract_write_targets_from_invocations(invocations: list[Any]) -> list[str]:
    """从 invocations 中提取 write 操作的目标文件（去重）。"""
    targets: list[str] = []
    seen: set[str] = set()
    for invocation in invocations:
        if not is_write_invocation(invocation):
            continue
        target_file = extract_target_file_from_invocation_args(invocation)
        normalized = normalize_path_token(target_file)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        targets.append(normalized)
    return targets


# ---------------------------------------------------------------------------
# 合约守卫核心
# ---------------------------------------------------------------------------


# 常见代码文件扩展名（用于启发式检测）
_COMMON_CODE_EXTENSIONS: set[str] = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".css",
    ".html",
    ".xml",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".proto",
    ".graphql",
    ".prisma",
    ".dockerfile",
    ".makefile",
    ".dockerignore",
    ".gitignore",
}


def _is_common_code_file(path: str) -> bool:
    """检查文件扩展名是否在常见代码扩展名列表中。"""
    normalized = normalize_path_token(path).lower()
    if not normalized:
        return False
    # 检查是否有扩展名
    if "." not in normalized:
        # 无扩展名文件：检查常见无扩展名文件名
        basename = normalized.rsplit("/", 1)[-1].strip()
        return basename in {
            "makefile",
            "dockerfile",
            "readme",
            "license",
            "changelog",
            "contributing",
            ".env",
            ".gitignore",
        }
    ext = normalized.rsplit(".", 1)[-1].strip()
    return f".{ext}" in _COMMON_CODE_EXTENSIONS


def _file_exists_in_workspace(path: str, workspace: str = ".") -> bool:
    """检查文件是否在 workspace 中已存在（路径遍历安全版）。"""
    import os

    normalized = normalize_path_token(path)
    if not normalized:
        return False

    # 防御路径遍历：使用 realpath 解析并验证边界
    workspace_real = os.path.realpath(workspace or ".")
    full_path = os.path.realpath(os.path.join(workspace_real, normalized))

    # 必须严格位于 workspace 内部
    if not (full_path.startswith(workspace_real + os.sep) or full_path == workspace_real):
        return False

    return os.path.isfile(full_path)


@dataclass(frozen=True)
class MutationTargetGuardViolation:
    """Mutation 目标守卫违规记录。"""

    violation_type: str
    message: str


def resolve_mutation_target_guard_violation(
    latest_user_request: str, invocations: list[Any], *, workspace: str = "."
) -> MutationTargetGuardViolation | str | None:
    """检测 mutation 目标漂移：LLM 写入的文件不在用户明确提到的目标范围内。

    Returns:
        若存在漂移，返回 MutationTargetGuardViolation 或描述字符串；否则返回 None。
    """
    explicit_targets = extract_target_files_from_message(latest_user_request)
    write_targets = extract_write_targets_from_invocations(invocations)
    if not write_targets:
        return None

    # 启发式检测：当 explicit_targets 为空时，进行更严格的检查
    if not explicit_targets:
        written_basenames = {Path(p).name for p in write_targets}
        common_extensions = {".py", ".md", ".txt", ".json", ".yaml", ".yml"}
        for wb in written_basenames:
            if not any(wb.endswith(ext) for ext in common_extensions):
                return MutationTargetGuardViolation(
                    violation_type="UNSUPPORTED_NEW_FILE",
                    message=f"Creating new file with uncommon extension: {wb}",
                )

        mismatched_targets: list[str] = []
        for write_target in write_targets:
            normalized = normalize_path_token(write_target).lower()
            if not normalized:
                continue
            # 如果文件已存在于 workspace 中，可能是合法修改
            if _file_exists_in_workspace(write_target, workspace):
                continue
            # 如果文件扩展名在常见代码扩展名列表中，可能是合法的新文件
            if _is_common_code_file(write_target):
                continue
            mismatched_targets.append(write_target)

        if not mismatched_targets:
            return None

        return (
            "single_batch_contract_violation: mutation write target drift (heuristic); "
            f"write targets out-of-scope={mismatched_targets[:6]} "
            "(new files with uncommon extensions not in workspace)"
        )

    read_targets = extract_read_targets_from_invocations(invocations)
    allowed_candidates = build_path_match_candidates(explicit_targets + read_targets)
    if not allowed_candidates:
        return None

    mismatched_targets = []
    for write_target in write_targets:
        normalized = normalize_path_token(write_target).lower()
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1].strip().lower()
        if normalized in allowed_candidates or basename in allowed_candidates:
            continue
        mismatched_targets.append(write_target)

    if not mismatched_targets:
        return None

    expected_targets = explicit_targets[:6]
    if read_targets:
        expected_targets.extend(read_targets[:6])
    expected_targets = expected_targets[:8]
    return (
        "single_batch_contract_violation: mutation write target drift; "
        f"write targets out-of-scope={mismatched_targets[:6]} expected one of={expected_targets}"
    )


# ---------------------------------------------------------------------------
# Bootstrap / Stale-edit
# ---------------------------------------------------------------------------


def build_stale_edit_bootstrap_decision(
    *,
    turn_id: str,
    retry_invocations: list[Any],
    decision_metadata: Any,
) -> TurnDecision | None:
    """从失败的 write invocations 合成安全 read bootstrap 决策。

    当 strict write retry 被 stale-edit guard 阻止时，合成一个只读批次
    先读取目标文件，再走现有的 write-followup 路径。
    """
    from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation  # local import to avoid cycles

    read_invocations: list[Any] = []
    seen_targets: set[str] = set()
    for index, invocation in enumerate(retry_invocations, start=1):
        target_file = extract_target_file_from_invocation_args(invocation)
        for candidate_file in expand_bootstrap_read_candidates(target_file):
            if not candidate_file or candidate_file in seen_targets:
                continue
            seen_targets.add(candidate_file)
            read_invocations.append(
                cast(
                    "ToolInvocation",
                    {
                        "call_id": ToolCallId(f"{turn_id}_bootstrap_read_{index}_{len(read_invocations) + 1}"),
                        "tool_name": "read_file",
                        "arguments": {"file": candidate_file},
                        "effect_type": ToolEffectType.READ,
                        "execution_mode": ToolExecutionMode.READONLY_SERIAL,
                    },
                )
            )

    if not read_invocations:
        return None

    metadata_payload = dict(decision_metadata) if isinstance(decision_metadata, Mapping) else {}
    if not metadata_payload.get("workspace"):
        metadata_payload["workspace"] = "."

    return cast(
        "TurnDecision",
        {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "turn_id": turn_id,
            "tool_batch": {
                "batch_id": BatchId(f"{turn_id}_stale_bootstrap"),
                "invocations": read_invocations,
            },
            "metadata": metadata_payload,
        },
    )


def receipts_have_stale_edit_failure(receipts: list[dict[str, Any]]) -> bool:
    """检查 receipts 中是否包含 stale_edit 类型的失败。"""
    for receipt in receipts:
        raw_results = receipt.get("raw_results")
        if not isinstance(raw_results, list):
            continue
        for raw_item in raw_results:
            if not isinstance(raw_item, Mapping):
                continue
            error_text = str(raw_item.get("error") or "").strip().lower()
            error_type = str(raw_item.get("error_type") or "").strip().lower()
            if error_type == "stale_edit":
                return True
            if error_text and ("stale_edit" in error_text or "fresh read required" in error_text):
                return True
    return False


def rollback_state_after_retry_batch_failure(state_machine: TurnStateMachine, ledger: TurnLedger) -> None:
    """记录 retry rollback 意图，不违反状态机不变量。

    Retry batches 是试探性的；失败后保持在 TOOL_BATCH_EXECUTING 状态，
    让下一次 _execute_tool_batch 跳过冗余状态转换。
    """
    if state_machine.current_state != TurnState.TOOL_BATCH_EXECUTING:
        return
    ledger.state_history.append(("RETRY_BATCH_ROLLBACK", int(time.time() * 1000)))


# ---------------------------------------------------------------------------
# 通用判定
# ---------------------------------------------------------------------------


def is_safe_read_bootstrap_tool_name(tool_name: str) -> bool:
    """判定工具名是否为安全只读引导工具。"""
    return tool_name in SAFE_READ_BOOTSTRAP_TOOLS


def tool_batch_has_write_invocation(invocations: list[dict[str, Any]] | list[Any]) -> bool:
    """判定工具批次中是否包含写 invocation。"""
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        if tool_name in WRITE_TOOLS:
            return True
        mode = extract_invocation_execution_mode(invocation)
        if mode == str(ToolExecutionMode.WRITE_SERIAL):
            return True
    return False


def has_available_write_tool(tool_definitions: list[dict[str, Any]] | list[Any]) -> bool:
    """判定可用工具定义中是否包含写工具。"""
    for item in tool_definitions:
        if not isinstance(item, Mapping):
            continue
        function_payload = item.get("function")
        if isinstance(function_payload, Mapping):
            tool_name = str(function_payload.get("name") or "").strip()
        else:
            tool_name = str(item.get("name") or "").strip()
        if tool_name in WRITE_TOOLS:
            return True
    return False


def is_mutation_contract_violation(exc: Exception) -> bool:
    """判定异常是否为突变合约违反。"""
    return "single_batch_contract_violation" in str(exc)


def is_stale_edit_contract_violation(exc: Exception) -> bool:
    """判定异常是否为 stale-edit 类型的合约违反。"""
    lowered = str(exc).lower()
    if "single_batch_contract_violation" not in lowered:
        return False
    return "stale_edit" in lowered or "fresh read" in lowered or "requires_bootstrap_read" in lowered


def is_safe_readonly_bootstrap_invocations(invocations: list[Any]) -> bool:
    """判定 invocations 是否为安全的只读 bootstrap 调用。"""
    if not invocations:
        return False
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        if not is_safe_read_bootstrap_tool_name(tool_name):
            return False
    return True
