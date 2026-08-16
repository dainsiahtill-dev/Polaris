"""辅助函数和常量

包含配置解析、模式匹配、常量定义等辅助功能。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.public.service import is_authoritative_write_result
from polaris.kernelone.constants import DIRECTOR_TIMEOUT_SECONDS
from polaris.kernelone.tools.tool_kinds import is_write_tool_name

# -----------------------------------------------------------------------------
# 配置解析辅助函数
# -----------------------------------------------------------------------------


def _seq_parse_bool(value: Any, *, default: bool) -> bool:
    """Parse boolean value from various types."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return default


def _seq_resolve_bool(
    settings: Any,
    sentinel: Any,
    name: str,
    env_key: str,
    default: bool,
) -> bool:
    """Resolve boolean setting from settings object or environment."""
    configured = getattr(settings, name, sentinel)
    if configured is not sentinel:
        return _seq_parse_bool(configured, default=default)
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    return _seq_parse_bool(raw, default=default)


def _seq_resolve_int(
    settings: Any,
    sentinel: Any,
    name: str,
    env_key: str,
    default: int,
    *,
    minimum: int = 1,
) -> int:
    """Resolve integer setting from settings object or environment."""
    configured = getattr(settings, name, sentinel)
    if configured is not sentinel:
        try:
            return max(minimum, int(configured))
        except (TypeError, ValueError):
            pass
    raw = os.environ.get(env_key)
    if raw is not None:
        try:
            return max(minimum, int(raw))
        except ValueError:
            return max(minimum, int(default))
    return max(minimum, int(default))


def _seq_resolve_str(
    settings: Any,
    sentinel: Any,
    name: str,
    env_key: str,
    default: str,
) -> str:
    """Resolve string setting from settings object or environment."""
    configured = getattr(settings, name, sentinel)
    if configured is not sentinel:
        token = str(configured).strip()
        if token:
            return token
    raw = os.environ.get(env_key)
    if raw is not None:
        token = str(raw).strip()
        if token:
            return token
    return str(default)


# -----------------------------------------------------------------------------
# 质量检测模式
# -----------------------------------------------------------------------------

_LOW_QUALITY_PATTERNS = (
    re.compile(r"(?m)^\s*(?://|#|/\*|\*|<!--)\s*(?:TODO|FIXME|TBD)\b"),
    re.compile(r"(?m)^\s*(?:TODO|FIXME|TBD)\b(?::|\s*$)"),
    # "placeholder" as a CODE TOKEN is legitimate input-UI code: the HTML
    # attribute (placeholder="..."), object key (placeholder: ...), CSS
    # pseudo-element (::placeholder) / pseudo-class (:placeholder-shown),
    # property access (el.placeholder), quoted id ('placeholder'), and
    # data-placeholder. Only PROSE-style scaffold talk is low quality (live
    # factory-bench L2-10 r4 killed a real <textarea placeholder="...">, r5
    # killed a real .editor::placeholder rule).
    re.compile(r"(?<![.:'\"-])\bplaceholder\b(?!\s*[=:])(?![-'\"])", re.IGNORECASE),
    re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE),
    re.compile(r"\bstub\b", re.IGNORECASE),
)

# Markers that legitimately appear as *string literals* when product code NAMES
# the token rather than embodying it — e.g. an anti-placeholder test or lint that
# checks ``npm test`` output / source for forbidden tokens:
#   FORBIDDEN_TOKENS = ("todo", "fixme", "notimplemented", "no test specified")
#   assert "stub" not in source
# A genuine unfinished-code marker (``raise NotImplementedError``, ``# stub``) is
# NOT inside a string literal, so suppressing string-literal hits keeps real
# placeholders flagged while no longer punishing a correct quality-enforcing test.
# (live factory-bench L1-02 r10: a model-authored tests/test_product.py defined a
# FORBIDDEN_TOKENS list containing "notimplemented"; the bare \bNotImplemented\b
# scan matched that literal, failed materialization quality, and trapped the
# Director in an unfixable rewrite loop — the file was already correct.)
_STRING_LITERAL_GUARDED_PATTERNS = frozenset(
    {
        r"\bNotImplemented(?:Error|Exception)?\b",
        r"\bstub\b",
    }
)

# "placeholder" in documentation/comments is descriptive prose (e.g. JSDoc
# "Browser-only entry point placeholder"), not unfinished code. Real unfinished
# markers live in executable spans (``const x = placeholder``, ``return placeholder``).
# (live factory-bench L1-01 r153b: src/web.ts failed materialization semantic quality
# solely because of a JSDoc word, while the file had real isNode guards + export.)
_COMMENT_GUARDED_PATTERNS = frozenset(
    {
        r"(?<![.:'\"-])\bplaceholder\b(?!\s*[=:])(?![-'\"])",
    }
)


def _match_is_inside_string_literal(line: str, rel_start: int, rel_end: int) -> bool:
    """Best-effort: is the [rel_start, rel_end) span on ``line`` inside a quote?

    Counts unescaped single/double quotes before the match on the same line: an
    odd count means the span opened inside a string literal. Language-agnostic and
    deliberately conservative — it only suppresses tokens that are clearly quoted
    string content (the shape of a forbidden-token list or a "must not contain X"
    assertion), never bare code identifiers.
    """
    prefix = line[:rel_start]
    for quote in ('"', "'"):
        count = 0
        idx = 0
        while idx < len(prefix):
            ch = prefix[idx]
            if ch == "\\":
                idx += 2
                continue
            if ch == quote:
                count += 1
            idx += 1
        if count % 2 == 1:
            # Opened a string with this quote before the match; confirm it also
            # closes after the match on the same line (a complete literal).
            rest = line[rel_end:]
            if quote in rest:
                return True
    return False


def _match_is_inside_line_comment(line: str, rel_start: int) -> bool:
    """Return True when the match sits on a comment-only span of ``line``.

    Covers full-line comments (``//``, ``#``, ``/*``, block continuation ``*``,
    ``<!--``) and end-of-line comments introduced by ``//`` or ``#`` before the
    match. Conservative: does not try to parse multi-line block-comment ranges
    that lack a leading ``*`` on continuation lines.
    """

    stripped = line.lstrip()
    if stripped.startswith(("//", "#", "/*", "*", "<!--")):
        return True
    for marker in ("//", "#"):
        idx = line.find(marker)
        if 0 <= idx < rel_start and not _match_is_inside_string_literal(line, idx, idx + len(marker)):
            return True
    return False


def low_quality_pattern_match(pattern: re.Pattern[str], content: str) -> bool:
    """Return whether ``pattern`` flags genuine low-quality content in ``content``.

    For the string-literal-guarded markers (NotImplemented / stub) a hit that
    sits inside a quoted string literal is treated as the token being *named*
    (anti-placeholder test/lint), not a real unfinished-code marker, and is
    skipped. For comment-guarded markers (placeholder prose), a hit that sits
    only in a comment is descriptive documentation, not unfinished code, and is
    skipped. All other patterns keep their original bare-search semantics.
    """
    needs_string_guard = pattern.pattern in _STRING_LITERAL_GUARDED_PATTERNS
    needs_comment_guard = pattern.pattern in _COMMENT_GUARDED_PATTERNS
    if not needs_string_guard and not needs_comment_guard:
        return bool(pattern.search(content))
    for match in pattern.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_end = content.find("\n", match.end())
        if line_end < 0:
            line_end = len(content)
        line = content[line_start:line_end]
        rel_start = match.start() - line_start
        rel_end = match.end() - line_start
        if needs_string_guard and _match_is_inside_string_literal(line, rel_start, rel_end):
            continue
        if needs_comment_guard and _match_is_inside_line_comment(line, rel_start):
            continue
        return True
    return False


_PATCH_RESIDUE_PATTERNS = (
    re.compile(r"(?m)^<{4,7}\s*SEARCH\b", re.IGNORECASE),
    re.compile(r"(?m)^=======\s*$"),
    re.compile(r"(?m)^>{4,7}\s*REPLACE\b", re.IGNORECASE),
    re.compile(r"(?m)^END\s+PATCH_FILE\s*$", re.IGNORECASE),
    re.compile(r"(?m)^PATCH_FILE(?::|\s+)", re.IGNORECASE),
)

_GENERIC_SCAFFOLD_MARKERS = (
    "Generated Project Scaffold",
    "Auto-generated starter entrypoint for Polaris stress workflow",
    "def safe_divide(",
    "def parse_arguments(",
    "helpers 模块的单元测试",
    "应用程序主入口点",
)

_DOMAIN_STOPWORDS = {
    "task",
    "tasks",
    "project",
    "src",
    "module",
    "code",
    "implement",
    "extend",
    "extends",
    "according",
    "execute",
    "execution",
    "feature",
    "service",
    "system",
    "update",
    "add",
    "fix",
}

_MIN_FILES_PATTERN = re.compile(r"至少\s*(\d+)\s*个(?:代码)?文件", re.IGNORECASE)
_MIN_LINES_PATTERN = re.compile(r"(?:不少于|至少)\s*(\d+)\s*行", re.IGNORECASE)


# -----------------------------------------------------------------------------
# 超时和租约常量
# -----------------------------------------------------------------------------

# Keep adapter timeout aligned with kernel LLM timeout budget to avoid
# aborting valid long-running Director generations prematurely.
_DEFAULT_LLM_CALL_TIMEOUT_SECONDS: float = DIRECTOR_TIMEOUT_SECONDS
_DEFAULT_TASK_LEASE_TTL_SECONDS = 120
_TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS = 15.0


# -----------------------------------------------------------------------------
# 文件类型检测
# -----------------------------------------------------------------------------

_CODE_FILE_EXTENSIONS: set[str] = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".cs",
    ".php",
    ".rb",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".cjs",
    ".mjs",
    ".yaml",
    ".yml",
    ".toml",
    ".vue",
    ".svelte",
    ".md",
    ".mod",
    ".sum",
}

_CODE_FILE_NAMES: set[str] = {
    "go.mod",
    "go.sum",
    "dockerfile",
    "makefile",
    "gnumakefile",
    "requirements.txt",
    "procfile",
    "gemfile",
    "cargo.lock",
    "cargo.toml",
}

_CANONICAL_MANIFEST_BASENAMES: dict[str, str] = {
    "cargo.toml": "Cargo.toml",
    "cargo.lock": "Cargo.lock",
}


def canonicalize_project_manifest_path(path: str) -> str:
    """Normalize well-known manifest basenames to the tool-required case.

    Live L2-14: Director wrote ``cargo.toml``. Cargo and the official
    rust quality sandbox only accept ``Cargo.toml``, so the gate skipped
    rust compile/test and quality_gate passed with no cargo receipt.
    """

    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return normalized
    parts = normalized.split("/")
    canonical = _CANONICAL_MANIFEST_BASENAMES.get(parts[-1].lower())
    if canonical is not None:
        parts[-1] = canonical
    return "/".join(parts)


def is_project_code_file(file_suffix: str, filename: str = "") -> bool:
    """Check if suffix or well-known delivery filename is a project file.

    Live L2-13: Director wrote ``go.mod`` (suffix ``.mod``). The scanner
    ignored it, so an authoritative create receipt projected as
    ``director_no_materialized_changes`` and blocked remaining Go tasks.
    Manifest names must count even when the suffix is not a language source.
    """
    name = Path(filename).name.lower() if filename else ""
    if name in _CODE_FILE_NAMES:
        return True
    suffix = str(file_suffix or "").lower()
    if not suffix and name:
        suffix = Path(name).suffix.lower()
    return suffix in _CODE_FILE_EXTENSIONS


# -----------------------------------------------------------------------------
# 内容预览和摘要辅助函数
# -----------------------------------------------------------------------------


def preview_content_for_error(content: str, limit: int = 240) -> str:
    """Preview content for error messages, truncating if too long."""
    token = " ".join(str(content or "").split())
    if len(token) <= limit:
        return token
    return token[:limit] + "...(truncated)"


def summarize_tools_for_debug(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize tool results for debug logging."""
    summary: list[dict[str, Any]] = []
    for item in tool_results[:12]:
        if not isinstance(item, dict):
            continue
        result_value = item.get("result")
        result: dict[str, Any] = result_value if isinstance(result_value, dict) else {}
        summary.append(
            {
                "tool": str(item.get("tool") or ""),
                "success": bool(item.get("success", False)),
                "error": str(item.get("error") or "").strip() or None,
                "file": str(result.get("file") or result.get("path") or "").strip() or None,
                "source_tool": str(result.get("source_tool") or "").strip() or None,
            }
        )
    return summary


# -----------------------------------------------------------------------------
# 错误检测辅助函数
# -----------------------------------------------------------------------------


def is_format_validation_failure(error_text: str) -> bool:
    """Check if error indicates format validation failure."""
    token = str(error_text or "").strip().lower()
    if not token:
        return False
    hints = (
        "未找到有效的json或补丁",
        "no valid json found",
        "validation failed",
        "验证失败",
    )
    return any(hint in token for hint in hints)


def is_timeout_failure(error_text: str) -> bool:
    """Check if error indicates timeout."""
    token = str(error_text or "").strip().lower()
    if not token:
        return False
    hints = (
        "timeout",
        "timed out",
        "llm_timeout",
    )
    return any(hint in token for hint in hints)


def has_successful_write_tool(tool_results: list[dict[str, Any]]) -> bool:
    """Check whether tool results contain an authoritative successful write."""
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        if not _is_successful_tool_result(item):
            continue
        tool_name = item.get("tool_name") or item.get("tool") or ""
        if is_write_tool_name(tool_name) and _has_tool_execution_receipt(item) and is_authoritative_write_result(item):
            return True
    return False


def _is_successful_tool_result(item: Mapping[str, Any]) -> bool:
    direct_signal = _tool_result_success_signal(item)
    if direct_signal is not None:
        return direct_signal
    result = item.get("result")
    if isinstance(result, Mapping):
        nested_signal = _tool_result_success_signal(result)
        if nested_signal is not None:
            return nested_signal
    return False


def _tool_result_success_signal(item: Mapping[str, Any]) -> bool | None:
    status = str(item.get("status") or "").strip().lower()
    if status:
        return status == "success"
    if "success" in item:
        return _coerce_tool_success_value(item.get("success"))
    if "ok" in item:
        return _coerce_tool_success_value(item.get("ok"))
    return None


def _coerce_tool_success_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "ok", "success"}:
            return True
        if token in {"0", "false", "no", "error", "failed"}:
            return False
    return bool(value)


def _has_tool_execution_receipt(item: Mapping[str, Any]) -> bool:
    """Return True for executed tool results, not plain LLM tool-call requests."""
    if str(item.get("status") or "").strip():
        return True
    if item.get("effect_receipt") is not None:
        return True
    if item.get("result") is not None:
        return True
    raw_result = item.get("raw_result")
    return isinstance(raw_result, Mapping) and bool(raw_result)


def is_empty_role_response(role_response: dict[str, Any]) -> bool:
    """Check if role response is empty (no content, error, or tools)."""
    if not isinstance(role_response, dict):
        return True
    content = str(role_response.get("content") or "").strip()
    error = str(role_response.get("error") or "").strip()
    if content or error:
        return False
    raw = role_response.get("raw_response")
    if isinstance(raw, dict):
        tool_calls = raw.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return False
    tool_calls = role_response.get("tool_calls")
    return not (isinstance(tool_calls, list) and tool_calls)


def looks_like_protocol_patch_response(text: str) -> bool:
    """Check if text looks like a protocol patch response."""
    body = str(text or "")
    lowered = body.lower()
    if not lowered.strip():
        return False
    if "patch_file" in lowered or "delete_file" in lowered:
        return True
    if "<<<<<<< search" in lowered or ">>>>>>> replace" in lowered:
        return True
    if re.search(r"(?:^|\n)\s*search:?\s*\n", body, flags=re.IGNORECASE) and re.search(
        r"\n\s*replace:?\s*\n",
        body,
        flags=re.IGNORECASE,
    ):
        return True
    return bool(re.search(r"(?:^|\n)\s*(?:file|create|delete(?:_file)?)\s*[:\s]+\S+", body, flags=re.IGNORECASE))


# -----------------------------------------------------------------------------
# 响应提取辅助函数
# -----------------------------------------------------------------------------


def extract_kernel_tool_results(role_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize tool results from role response."""
    receipt_results = _extract_kernel_batch_receipt_results(role_response)
    if receipt_results:
        return receipt_results

    raw_tool_results = role_response.get("tool_results")
    if not isinstance(raw_tool_results, list):
        raw_tool_results = role_response.get("tool_calls")
    if not isinstance(raw_tool_results, list):
        raw = role_response.get("raw_response")
        if isinstance(raw, dict):
            raw_tool_results = raw.get("tool_results")
            if not isinstance(raw_tool_results, list):
                raw_tool_results = raw.get("tool_calls")
        else:
            raw_tool_results = []
    if not isinstance(raw_tool_results, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_tool_results:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("tool") or item.get("name") or "").strip().lower()
        if not tool_name:
            tool_name = "unknown"
        status = str(item.get("status") or "").strip().lower()
        success = _is_successful_tool_result(item)
        normalized.append(
            {
                "tool": tool_name,
                "tool_name": tool_name,
                "success": success,
                "status": status or ("success" if success else "error" if item.get("error") else ""),
                "result": item.get("result"),
                "error": str(item.get("error") or "").strip() or None,
                "call_id": str(item.get("call_id") or "").strip(),
                "arguments": item.get("arguments"),
                "effect_receipt": item.get("effect_receipt"),
                "raw_result": dict(item),
            }
        )
    return normalized


def _extract_kernel_batch_receipt_results(role_response: dict[str, Any]) -> list[dict[str, Any]]:
    for receipt in _iter_candidate_batch_receipts(role_response):
        results = receipt.get("results")
        if not isinstance(results, list):
            results = receipt.get("raw_results")
        if not isinstance(results, list):
            continue
        normalized = _normalize_batch_receipt_results(results)
        if normalized:
            return normalized
    return []


def _iter_candidate_batch_receipts(role_response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [role_response.get("batch_receipt")]
    raw = role_response.get("raw_response")
    if isinstance(raw, Mapping):
        candidates.append(raw.get("batch_receipt"))
        metadata = raw.get("metadata")
        if isinstance(metadata, Mapping):
            candidates.append(metadata.get("batch_receipt"))
        execution_stats = raw.get("execution_stats")
        if isinstance(execution_stats, Mapping):
            candidates.append(execution_stats.get("batch_receipt"))
    metadata = role_response.get("metadata")
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("batch_receipt"))
    execution_stats = role_response.get("execution_stats")
    if isinstance(execution_stats, Mapping):
        candidates.append(execution_stats.get("batch_receipt"))

    receipts: list[dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            receipts.append(dict(candidate))
    return receipts


def _normalize_batch_receipt_results(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("tool") or item.get("name") or "").strip().lower()
        if not tool_name:
            tool_name = "unknown"
        status = str(item.get("status") or "").strip().lower()
        success = _is_successful_tool_result(item)
        normalized_item = dict(item)
        normalized_item.update(
            {
                "tool": tool_name,
                "tool_name": tool_name,
                "success": success,
                "status": status or ("success" if success else "error" if item.get("error") else ""),
                "result": item.get("result"),
                "error": str(item.get("error") or "").strip() or None,
                "call_id": str(item.get("call_id") or "").strip(),
                "arguments": item.get("arguments"),
                "effect_receipt": item.get("effect_receipt"),
                "raw_result": dict(item),
            }
        )
        normalized.append(normalized_item)
    return normalized


def coerce_task_record(entry: Any) -> dict[str, Any]:
    """Coerce task entry to dictionary."""
    if isinstance(entry, dict):
        return dict(entry)
    to_dict = getattr(entry, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
            if isinstance(payload, dict):
                return dict(payload)
        except (AttributeError, TypeError, ValueError):
            return {}
    record: dict[str, Any] = {}
    for key in ("id", "status", "subject", "title", "blocked_by", "blocks", "assignee"):
        if hasattr(entry, key):
            record[key] = getattr(entry, key)
    return record


# -----------------------------------------------------------------------------
# TaskBoard 快照辅助
# -----------------------------------------------------------------------------


def taskboard_snapshot_brief(snapshot: dict[str, Any]) -> str:
    """Build brief string from taskboard snapshot."""
    if not isinstance(snapshot, dict):
        return "taskboard unavailable"
    _raw_counts = snapshot.get("counts")
    counts: dict[str, Any] = _raw_counts if isinstance(_raw_counts, dict) else {}
    total = int(counts.get("total") or 0)
    ready = int(counts.get("ready") or 0)
    pending = int(counts.get("pending") or 0)
    in_progress = int(counts.get("in_progress") or 0)
    completed = int(counts.get("completed") or 0)
    failed = int(counts.get("failed") or 0)
    blocked = int(counts.get("blocked") or 0)
    return (
        "TaskBoard "
        f"total={total} ready={ready} pending={pending} "
        f"in_progress={in_progress} completed={completed} failed={failed} blocked={blocked}"
    )
