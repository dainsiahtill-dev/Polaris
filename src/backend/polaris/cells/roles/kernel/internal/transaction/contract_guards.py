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

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from polaris.cells.roles.kernel.internal.transaction.constants import (
    READ_TOOLS,
    RECON_TOOLS,
    SAFE_READ_BOOTSTRAP_TOOLS,
    TOOL_ALIASES,
    WRITE_TOOLS,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.write_authority import (
    is_authoritative_write_invocation as _is_authoritative_write_invocation,
    is_authoritative_write_path,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
)

logger = logging.getLogger(__name__)

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


_DECLARED_TARGET_LINE_RE = re.compile(
    r"^\s*(?:allowed\s+target\s+files|target\s+files|target_files|targets|目标文件|范围|scope)\s*[:：]\s*(?P<value>.+)$",
    flags=re.IGNORECASE,
)


def _dedupe_normalized_paths(tokens: list[str]) -> list[str]:
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


def _extract_file_tokens_from_text(text: str) -> list[str]:
    """Extract file-looking path tokens from one trusted text fragment."""
    raw = str(text or "")
    if not raw:
        return []
    # 匹配带扩展名的文件路径
    ext_tokens = re.findall(
        r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|toml|js|ts|tsx|jsx|css|html)\b",
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
    return _dedupe_normalized_paths(tokens)


def extract_target_files_from_message(message: str) -> list[str]:
    """从用户消息中提取疑似目标文件路径。"""
    raw = str(message or "")
    if not raw:
        return []
    declared_tokens: list[str] = []
    for line in raw.splitlines():
        match = _DECLARED_TARGET_LINE_RE.match(line)
        if not match:
            continue
        declared_tokens.extend(_extract_file_tokens_from_text(match.group("value")))
    if declared_tokens:
        return _dedupe_normalized_paths(declared_tokens)
    return _extract_file_tokens_from_text(raw)


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
    """构建路径匹配候选集。

    A scoped path such as ``src/main.py`` must not authorize writing a
    different root-level ``main.py``. Bare filenames remain flexible because
    the user did not declare a directory.
    """
    candidates: set[str] = set()
    for raw_path in paths:
        normalized = normalize_path_token(raw_path)
        if not normalized:
            continue
        lowered = normalized.lower()
        candidates.add(lowered)
        basename = normalized.rsplit("/", 1)[-1].strip().lower()
        if basename and "/" not in normalized:
            candidates.add(basename)
    return candidates


def expand_bootstrap_read_candidates(target_file: str) -> list[str]:
    """为 bootstrap read 展开候选路径。"""
    normalized = str(target_file or "").strip().replace("\\", "/")
    if not normalized:
        return []
    if "/" in normalized:
        return [normalized]
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


def is_authoritative_write_invocation(invocation: Any) -> bool:
    """判定 invocation 是否为 authoritative write。"""
    if not is_write_invocation(invocation):
        return False
    target_file = extract_target_file_from_invocation_args(invocation)
    if target_file:
        return is_authoritative_write_path(target_file)
    return _is_authoritative_write_invocation(invocation)


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
    from pathlib import Path

    normalized = normalize_path_token(path)
    if not normalized:
        return False

    # 防御路径遍历：使用 realpath 解析并验证边界
    workspace_real = os.path.realpath(workspace or ".")
    full_path = os.path.realpath(os.path.join(workspace_real, normalized))

    # 必须严格位于 workspace 内部（Path.is_relative_to 替代脆弱的字符串前缀匹配）
    if not Path(full_path).is_relative_to(Path(workspace_real)):
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


def filter_out_of_scope_write_invocations(
    latest_user_request: str,
    invocations: list[Any],
) -> tuple[list[Any], tuple[str, ...]]:
    """Drop out-of-scope writes when a batch also contains valid target writes.

    This preserves the target guard: a batch that only writes outside the
    declared contract is left intact so the strict guard can fail it. The
    filter is only a defensive normalizer for noisy model batches that include
    useful in-scope writes plus extra files.
    """

    explicit_targets = extract_target_files_from_message(latest_user_request)
    if not explicit_targets:
        return invocations, ()

    read_targets = extract_read_targets_from_invocations(invocations)
    allowed_candidates = build_path_match_candidates(explicit_targets + read_targets)
    if not allowed_candidates:
        return invocations, ()

    kept: list[Any] = []
    dropped: list[str] = []
    kept_write_count = 0
    dropped_write_count = 0
    for invocation in invocations:
        if not is_write_invocation(invocation):
            kept.append(invocation)
            continue
        target_file = extract_target_file_from_invocation_args(invocation)
        normalized = normalize_path_token(target_file).lower()
        basename = normalized.rsplit("/", 1)[-1].strip().lower() if normalized else ""
        if normalized and normalized not in allowed_candidates and basename not in allowed_candidates:
            dropped_write_count += 1
            dropped.append(target_file)
            continue
        kept_write_count += 1
        kept.append(invocation)

    if not dropped_write_count or not kept_write_count:
        return invocations, ()
    return kept, tuple(dropped)


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
    target_files: list[str] = []
    for invocation in retry_invocations:
        target_file = extract_target_file_from_invocation_args(invocation)
        if target_file:
            target_files.append(target_file)

    return _build_read_bootstrap_decision(
        turn_id=turn_id,
        target_files=target_files,
        decision_metadata=decision_metadata,
        batch_suffix="stale_bootstrap",
    )


def build_context_target_bootstrap_decision(
    *,
    turn_id: str,
    latest_user_request: str,
    decision_metadata: Any,
) -> TurnDecision | None:
    """Synthesize a safe read bootstrap batch from target paths already present in context."""
    target_files = extract_target_files_from_message(latest_user_request)
    return _build_read_bootstrap_decision(
        turn_id=turn_id,
        target_files=target_files,
        decision_metadata=decision_metadata,
        batch_suffix="context_bootstrap",
    )


def _build_read_bootstrap_decision(
    *,
    turn_id: str,
    target_files: list[str],
    decision_metadata: Any,
    batch_suffix: str,
) -> TurnDecision | None:

    read_invocations: list[Any] = []
    seen_targets: set[str] = set()
    for index, target_file in enumerate(target_files, start=1):
        for candidate_file in expand_bootstrap_read_candidates(target_file):
            normalized_candidate = normalize_path_token(candidate_file)
            if not normalized_candidate or normalized_candidate in seen_targets:
                continue
            seen_targets.add(normalized_candidate)
            read_invocations.append(
                cast(
                    "ToolInvocation",
                    {
                        "call_id": ToolCallId(f"{turn_id}_bootstrap_read_{index}_{len(read_invocations) + 1}"),
                        "tool_name": "read_file",
                        "arguments": {"file": normalized_candidate},
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
                "batch_id": BatchId(f"{turn_id}_{batch_suffix}"),
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


def tool_batch_has_authoritative_write_invocation(invocations: list[dict[str, Any]] | list[Any]) -> bool:
    """判定工具批次中是否包含 authoritative write invocation。"""
    return any(is_authoritative_write_invocation(invocation) for invocation in invocations)


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


# Polaris 自有教学错误锚点（平台字符串,非目标项目内容）:写工具参数"形状"失败
# —— 散文塞进 blocks / SEARCH==REPLACE 空操作 / 缺必填参数 / 无有效编辑块。
_WRITE_ARGUMENT_SHAPE_FAILURE_ANCHORS: tuple[str, ...] = (
    "Parameter validation failed",
    "Parameter failed",
    "Missing edit payload",
    "line-range edit requires",
    "missing argument",
    "whole-file write, not edit",
    "Validation failed",
    "prose/narration",
    "identical search and replace",
    "No valid edit blocks",
    # PreWriteGuard syntax block: the write carried correctable content and a
    # teaching suggestion — without a retry re-ask the turn ends on the single
    # blocked write and dies as no_materialized_changes (live factory-bench
    # L2-11 r3: main.py IndentationError blocked once, never retried).
    "Code syntax validation failed",
    # Wall 2 (2026-06-15): write_file emitted with a blank `content` argument on
    # a content-bearing target (the body got narrated in prose/reasoning instead).
    # Recognising it as an argument-shape failure routes it into the same
    # escalation/re-ask ladder so a real-content write is forced — otherwise the
    # single empty write dies as director_no_materialized_changes with no recovery.
    "Empty write content",
)


def _collect_write_error_text(item: Mapping[str, Any]) -> str:
    """Collect auditable error text from one normalized write receipt item."""
    fragments: list[str] = []
    payload = item.get("result")
    if isinstance(payload, Mapping):
        for key in ("error", "message", "error_message", "stderr"):
            value = payload.get(key)
            if value:
                fragments.append(str(value))
    for key in ("error", "message", "error_message", "stderr"):
        value = item.get(key)
        if value:
            fragments.append(str(value))
    return "\n".join(fragments)


def _matching_raw_write_error_text(raw_results: list[Any], item: Mapping[str, Any]) -> str:
    """Return error text from the raw receipt matching a canonical result item."""
    call_id = str(item.get("call_id") or "").strip()
    tool_name = str(item.get("tool_name") or "").strip()
    candidates: list[Mapping[str, Any]] = []
    for raw_item in raw_results:
        if not isinstance(raw_item, Mapping):
            continue
        raw_call_id = str(raw_item.get("call_id") or "").strip()
        raw_tool_name = str(raw_item.get("tool_name") or "").strip()
        if call_id:
            if raw_call_id != call_id:
                continue
            if tool_name and raw_tool_name and raw_tool_name != tool_name:
                continue
        elif tool_name and raw_tool_name != tool_name:
            continue
        candidates.append(raw_item)

    if not call_id and len(candidates) != 1:
        return ""

    fragments: list[str] = []
    for raw_item in candidates:
        error_text = _collect_write_error_text(raw_item)
        if error_text:
            fragments.append(error_text)
    return "\n".join(fragments)


def batch_write_results_all_failed_on_argument_shape(batch_receipt: Mapping[str, Any]) -> bool:
    """判定:批内出现过写调用,且全部因参数形状失败(无一成功)。

    Phase-1 A8a(2026-06-11, phase1smoke4 实证):弱模型自愿发 edit_blocks 却把
    散文塞进参数/发空操作,单 session 6 连败;而 W1.10 强制收窄阶梯只挂在
    "无写调用"违约上,从未触发。本谓词让这种批次走同一升级阶梯——其后段
    按名强制 + line-range schema 收窄已被实证可被 guided decoding 满足
    (fix5:散文逃逸 数十→0)。

    严格条件防误伤:任一写调用成功 → False;任一写调用因非形状原因失败
    (如 stale-edit、目标漂移——各有专属守卫)→ False;批内无写调用 → False。
    """
    results = batch_receipt.get("results") or []
    raw_results = batch_receipt.get("raw_results") or []
    if not isinstance(raw_results, list):
        raw_results = []
    write_seen = False
    for item in results:
        if not isinstance(item, Mapping):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        if tool_name not in WRITE_TOOLS:
            continue
        write_seen = True
        status = str(item.get("status") or "").strip().lower()
        payload = item.get("result")
        payload_ok = payload.get("ok") if isinstance(payload, Mapping) else None
        if status == "success" and payload_ok is not False:
            return False
        error_text = _collect_write_error_text(item)
        if not error_text:
            error_text = _matching_raw_write_error_text(raw_results, item)
        if not any(anchor in error_text for anchor in _WRITE_ARGUMENT_SHAPE_FAILURE_ANCHORS):
            return False
    return write_seen


def has_successful_recon_execution(tool_executions: list[dict[str, Any]]) -> bool:
    """判定 ledger 工具执行记录中是否存在至少一次成功的侦察工具执行。

    recon-required finalize gate（ADR-0091 R1）的核心谓词：
    - 工具名归一化（小写、``-``→``_``、TOOL_ALIASES 别名映射）后必须命中
      RECON_TOOLS（与评测侧 unified_judge 共享的 SSOT 集合）；
    - status 必须为 ``"success"``（失败的侦察调用不构成落地证据）。
    """
    for entry in tool_executions:
        if not isinstance(entry, Mapping):
            continue
        raw_name = str(entry.get("tool_name") or "").strip().lower().replace("-", "_")
        tool_name = TOOL_ALIASES.get(raw_name, raw_name)
        if tool_name in RECON_TOOLS and str(entry.get("status") or "") == "success":
            return True
    return False


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


def apply_delivery_mode_filter(decision: TurnDecision, ledger: TurnLedger) -> TurnDecision:
    """根据 delivery_contract 过滤决策中的 write tools。

    PROPOSE_PATCH / ANALYZE_ONLY 模式下禁止 write tools。
    若检测到 write tools，过滤后降级为 FINAL_ANSWER。

    run 模式（``TurnTransactionController._execute_turn``）与 stream 模式
    （``StreamOrchestrator.execute_turn_stream``）共用此实现，确保只读/提案
    边界在两条路径上语义一致。
    """
    contract = ledger.delivery_contract
    if contract.mode == DeliveryMode.MATERIALIZE_CHANGES:
        return decision

    tool_batch = decision.get("tool_batch")
    if not tool_batch:
        return decision

    invocations = list(tool_batch.get("invocations", []) or [])
    filtered = [inv for inv in invocations if not is_write_invocation(inv)]
    dropped = len(invocations) - len(filtered)

    if dropped == 0:
        return decision

    logger.warning(
        "delivery-mode-filter: dropped %d write tool(s) in %s mode. turn_id=%s",
        dropped,
        contract.mode.value,
        ledger.turn_id,
    )
    ledger.anomaly_flags.append(
        {
            "type": "DELIVERY_MODE_WRITE_TOOL_FILTERED",
            "turn_id": ledger.turn_id,
            "dropped_count": dropped,
            "delivery_mode": contract.mode.value,
            "original_tool_count": len(invocations),
        }
    )

    if not filtered:
        # 全部过滤完，降级为 FINAL_ANSWER
        return TurnDecision(
            turn_id=decision.get("turn_id"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message=decision.get("visible_message", ""),
            reasoning_summary=decision.get("reasoning_summary"),
            tool_batch=None,
            finalize_mode=FinalizeMode.NONE,
            domain=decision.get("domain", "code"),
            metadata={
                **(decision.get("metadata") or {}),
                "delivery_mode_filter_applied": True,
                "dropped_write_tools": dropped,
            },
        )

    # 部分过滤，重建 tool_batch
    turn_id_val = decision.get("turn_id")
    new_batch = ToolBatch(
        batch_id=tool_batch.get("batch_id", BatchId(f"{turn_id_val}_filtered")),
        invocations=filtered,
        parallel_readonly=[inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL],
        readonly_serial=[inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL],
        serial_writes=[],
        async_receipts=[inv for inv in filtered if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT],
    )
    return TurnDecision(
        turn_id=turn_id_val,
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message=decision.get("visible_message", ""),
        reasoning_summary=decision.get("reasoning_summary"),
        tool_batch=new_batch,
        finalize_mode=decision.get("finalize_mode"),
        domain=decision.get("domain", "code"),
        metadata={
            **(decision.get("metadata") or {}),
            "delivery_mode_filter_applied": True,
            "dropped_write_tools": dropped,
        },
    )
