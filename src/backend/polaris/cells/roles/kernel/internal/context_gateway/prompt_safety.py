"""Prompt-facing sanitizers for ContextOS projection messages."""

from __future__ import annotations

import json
import re
from typing import Any

_TOOL_FAILURE_PROMPT_TOKENS = (
    "director_write_policy_denied",
    "handler_error_type",
    "director_policy",
    "package_diff",
    "**write_file**: error",
    '"ok": false',
    "'ok': false",
)


def _trim_prompt_safe_text(text: str, *, max_chars: int = 220) -> str:
    value = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 3].rstrip()}..."


def _extract_tool_failure_field(text: str, field: str) -> str:
    patterns = (
        rf'"{re.escape(field)}"\s*:\s*"(?P<value>[^"]+)"',
        rf"'{re.escape(field)}'\s*:\s*'(?P<value>[^']+)'",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group("value") or "").strip()
    return ""


def prompt_safe_tool_failure_summary(role: str, content: str) -> str | None:
    """Return a compact failure summary when raw tool receipts would leak."""

    text = str(content or "")
    lowered = text.lower()
    if str(role or "").strip().lower() not in {"assistant", "tool", "tool_result"}:
        return None
    if not any(token in lowered for token in _TOOL_FAILURE_PROMPT_TOKENS):
        return None

    tool = _extract_tool_failure_field(text, "tool") or "unknown"
    error_type = _extract_tool_failure_field(text, "error_type") or _extract_tool_failure_field(
        text, "handler_error_type"
    )
    if not error_type and "director_write_policy_denied" in lowered:
        error_type = "director_write_policy_denied"
    error = _extract_tool_failure_field(text, "error")
    if not error and "write_file" in lowered and "error" in lowered:
        error = "write_file failed"
    summary = {
        "tool": tool,
        "error_type": error_type or "tool_failure",
        "reason": _trim_prompt_safe_text(error or "tool execution failed"),
        "prompt_safe": True,
        "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
    }
    return "[tool_failure_summary]\n" + json.dumps(summary, ensure_ascii=False)


def prompt_safe_message_content(role: str, content: Any) -> str:
    """Return message content safe to include in LLM prompts."""

    text = str(content or "")
    return prompt_safe_tool_failure_summary(role, text) or text
