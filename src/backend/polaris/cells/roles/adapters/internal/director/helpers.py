"""辅助函数和常量

包含配置解析、模式匹配、常量定义等辅助功能。
"""

from __future__ import annotations

import os
import re
from typing import Any

from polaris.kernelone.constants import DIRECTOR_TIMEOUT_SECONDS

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
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE),
    re.compile(r"\bstub\b", re.IGNORECASE),
)

_PATCH_RESIDUE_PATTERNS = (
    re.compile(r"(?m)^<<<<<<<\s*SEARCH\s*$", re.IGNORECASE),
    re.compile(r"(?m)^=======\s*$"),
    re.compile(r"(?m)^>>>>>>>\s*REPLACE\s*$", re.IGNORECASE),
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
    "module",
    "code",
    "implement",
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
    ".vue",
    ".svelte",
    ".md",
}


def is_project_code_file(file_suffix: str) -> bool:
    """Check if file suffix indicates a project code file."""
    return file_suffix.lower() in _CODE_FILE_EXTENSIONS


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
    """Check if any tool result indicates a successful write operation."""
    write_tools = {"write_file", "edit_file", "patch_apply"}
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("success")):
            continue
        tool_name = str(item.get("tool") or "").strip().lower()
        if tool_name in write_tools:
            return True
    return False


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
        tool_name = str(item.get("tool") or item.get("name") or "").strip().lower()
        if not tool_name:
            tool_name = "unknown"
        normalized.append(
            {
                "tool": tool_name,
                "success": bool(item.get("success", False)),
                "result": item.get("result"),
                "error": str(item.get("error") or "").strip() or None,
            }
        )
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
