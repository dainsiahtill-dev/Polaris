"""Unified result compaction utilities for tool execution.

This module provides functions for compacting tool result payloads to fit
within context window constraints. It consolidates compaction logic from
the cells layer into kernelone for canonical use.

Design Decisions:
- Pure functions for easy testing and composability
- Configurable limits via constants for operational flexibility
- Special handling for read_file content to preserve meaningful data
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.tool_state.safety import (
    _DEFAULT_CONTEXT_WINDOW_TOKENS,
    _MAX_READ_FILE_CONTENT_CHARS,
    _MAX_RESULT_DEPTH,
    _MAX_RESULT_ERROR_CHARS,
    _MAX_RESULT_LIST_ITEMS,
    _MAX_RESULT_OBJECT_KEYS,
    _MAX_RESULT_STRING_CHARS,
    _READ_FILE_PROMOTION_HEADROOM_RATIO,
)

# -----------------------------------------------------------------------------
# Core Compaction Functions
# -----------------------------------------------------------------------------


def compact_result_payload(
    tool_name: str,
    payload: dict[str, Any],
    context_window_tokens: int | None = None,
) -> dict[str, Any]:
    """Compact a tool result payload for transcript context.

    Args:
        tool_name: Name of the tool that produced the result
        payload: Raw result payload from tool execution
        context_window_tokens: Effective context window in tokens (optional)

    Returns:
        Compacted payload dictionary
    """
    compact: dict[str, Any] = {
        "tool": str(payload.get("tool") or tool_name).strip() or tool_name,
    }
    if "success" in payload:
        compact["success"] = bool(payload.get("success"))
    if "authorized" in payload:
        compact["authorized"] = bool(payload.get("authorized"))

    error_text = str(payload.get("error") or "").strip()
    if error_text:
        compact["error"] = trim_text(error_text, max_chars=_MAX_RESULT_ERROR_CHARS)

    result_value: Any = payload.get("result")
    if result_value is None and "raw_result" in payload:
        result_value = payload.get("raw_result")
    if result_value is not None:
        compact_result = compact_value(result_value, depth=0)
        compact["result"] = promote_read_file_content(
            tool_name=tool_name,
            raw_result=result_value,
            compact_result=compact_result,
            context_window_tokens=context_window_tokens,
        )

    for key in ("operation", "file_path", "args"):
        if key in payload:
            compact[key] = compact_value(payload.get(key), depth=0)
    return compact


def compact_value(value: Any, *, depth: int) -> Any:
    """Recursively compact a value to fit size constraints.

    Args:
        value: Value to compact
        depth: Current recursion depth

    Returns:
        Compacted value
    """
    if depth >= _MAX_RESULT_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return trim_text(value, max_chars=_MAX_RESULT_STRING_CHARS)
    if isinstance(value, list):
        items = [compact_value(item, depth=depth + 1) for item in value[:_MAX_RESULT_LIST_ITEMS]]
        if len(value) > _MAX_RESULT_LIST_ITEMS:
            items.append(f"[TRUNCATED_ITEMS:{len(value) - _MAX_RESULT_LIST_ITEMS}]")
        return items
    if isinstance(value, dict):
        compact_obj: dict[str, Any] = {}
        priority_keys = (
            "ok",
            "file",
            "path",
            "content",
            "truncated",
            "bytes",
            "line_count",
            "message",
            "error",
            "stdout",
            "stderr",
        )
        ordered_keys: list[str] = []
        for key in priority_keys:
            if key in value and key not in ordered_keys:
                ordered_keys.append(key)
        for key in value:
            key_name = str(key)
            if key_name not in ordered_keys:
                ordered_keys.append(key_name)

        selected_keys = ordered_keys[:_MAX_RESULT_OBJECT_KEYS]
        for key in selected_keys:
            compact_obj[key] = compact_value(value.get(key), depth=depth + 1)
        omitted = len(ordered_keys) - len(selected_keys)
        if omitted > 0:
            compact_obj["_omitted_keys"] = omitted
        return compact_obj

    return trim_text(str(value), max_chars=_MAX_RESULT_STRING_CHARS)


def trim_text(text: str, *, max_chars: int) -> str:
    """Trim text to maximum character length with truncation marker.

    Args:
        text: Text to trim
        max_chars: Maximum characters allowed

    Returns:
        Trimmed text with marker if truncated
    """
    token = str(text or "")
    if len(token) <= max_chars:
        return token
    # Reserve space for the marker so total output <= max_chars
    omitted_chars = len(token) - max_chars
    marker = f"...[TRUNCATED:{omitted_chars} chars]..."
    available = max_chars - len(marker)
    head = max(1, int(available * 0.75))
    tail = max(1, available - head)
    return token[:head] + marker + token[-tail:]


def promote_read_file_content(
    tool_name: str,
    raw_result: Any,
    compact_result: Any,
    context_window_tokens: int | None = None,
) -> Any:
    """Preserve meaningful read_file content in transcript history.

    Root-cause fix:
    The generic 1600-char compaction made medium file reads look incomplete
    to the model, which could trigger repeated identical read_file cycles.

    Args:
        tool_name: Name of the tool
        raw_result: Original uncompacted result
        compact_result: Already compacted result
        context_window_tokens: Effective context window in tokens

    Returns:
        Result with read_file content promoted if context allows
    """
    if str(tool_name or "").strip().lower() != "read_file":
        return compact_result
    if not isinstance(raw_result, dict) or not isinstance(compact_result, dict):
        return compact_result

    raw_content = raw_result.get("content")
    if not isinstance(raw_content, str):
        return compact_result
    if not _can_promote_read_file_content(raw_content, context_window_tokens):
        if (
            isinstance(compact_result, dict)
            and isinstance(compact_result.get("content"), str)
            and compact_result.get("content") != raw_content
        ):
            compact_result["content_compacted_by_tool_loop"] = True
            compact_result["content_original_chars"] = len(raw_content)
        return compact_result

    max_chars = max(2048, min(_MAX_READ_FILE_CONTENT_CHARS, 200000))
    if len(raw_content) <= max_chars:
        compact_result["content"] = raw_content
        compact_result["content_compacted_by_tool_loop"] = False
        return compact_result

    compact_result["content"] = trim_text(raw_content, max_chars=max_chars)
    compact_result["content_compacted_by_tool_loop"] = True
    compact_result["content_original_chars"] = len(raw_content)
    return compact_result


def _can_promote_read_file_content(
    content: str,
    context_window_tokens: int | None = None,
) -> bool:
    """Check if read_file content can be promoted based on context budget.

    Args:
        content: File content to potentially promote
        context_window_tokens: Effective context window in tokens

    Returns:
        True if content can be promoted, False otherwise
    """
    effective_tokens = context_window_tokens or _DEFAULT_CONTEXT_WINDOW_TOKENS
    allowed_prompt_tokens = max(
        256,
        int(effective_tokens * (1.0 - _READ_FILE_PROMOTION_HEADROOM_RATIO)),
    )
    used_tokens = _estimate_text_tokens(content)
    projected_tokens = used_tokens + _estimate_text_tokens(content)
    return projected_tokens <= allowed_prompt_tokens


def _estimate_text_tokens(text: str) -> int:
    """Fast token estimate for budget gating (not billing-accurate).

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    token = str(text or "")
    if not token:
        return 1
    # ASCII-heavy text ~= 4 chars/token. Keep a floor for CJK/mixed payloads.
    return max(1, int(len(token) / 4))


__all__ = [
    "compact_result_payload",
    "compact_value",
    "promote_read_file_content",
    "trim_text",
]
