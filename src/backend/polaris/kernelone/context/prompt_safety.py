"""Prompt-facing sanitizers for ContextOS projection messages."""

from __future__ import annotations

import ast
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
_TOOL_FAILURE_SUMMARY_PREFIX = "[tool_failure_summary]\n"


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


def _extract_braced_payload(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    candidate = text[start : end + 1]
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(candidate)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _deep_get_text(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, dict):
        for key in keys:
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if isinstance(raw, (int, float, bool)):
                return str(raw)
            if isinstance(raw, list):
                joined = ", ".join(str(item).strip() for item in raw if str(item).strip())
                if joined:
                    return joined
        for nested in value.values():
            result = _deep_get_text(nested, keys, depth=depth + 1)
            if result:
                return result
    if isinstance(value, list):
        for item in value:
            result = _deep_get_text(item, keys, depth=depth + 1)
            if result:
                return result
    return ""


def _infer_tool_failure_tool(text: str) -> str:
    explicit = _extract_tool_failure_field(text, "tool") or _extract_tool_failure_field(text, "tool_name")
    if explicit:
        return explicit
    markdown_tool = re.search(r"\*\*(?P<tool>[A-Za-z_][A-Za-z0-9_.-]*)\*\*\s*:\s*error", text, re.IGNORECASE)
    if markdown_tool:
        return str(markdown_tool.group("tool") or "").strip()
    lowered = text.lower()
    for candidate in ("write_file", "edit_file", "read_file", "execute_command", "delete_file"):
        if candidate in lowered:
            return candidate
    return "unknown"


def tool_failure_summary_payload(role: str, content: str) -> dict[str, Any] | None:
    """Return a prompt-safe failure payload when raw tool receipts would leak."""

    text = str(content or "")
    lowered = text.lower()
    if str(role or "").strip().lower() not in {"assistant", "tool", "tool_result"}:
        return None
    if not any(token in lowered for token in _TOOL_FAILURE_PROMPT_TOKENS):
        return None

    tool = _infer_tool_failure_tool(text)
    payload = _extract_braced_payload(text)
    error_type = (
        _extract_tool_failure_field(text, "error_type")
        or _extract_tool_failure_field(text, "handler_error_type")
        or _deep_get_text(payload, ("error_type", "handler_error_type", "code"))
    )
    if not error_type and "director_write_policy_denied" in lowered:
        error_type = "director_write_policy_denied"
    error = _extract_tool_failure_field(text, "error") or _deep_get_text(
        payload,
        (
            "error",
            "message",
            "reason",
            "reasons",
            "write_gate_reason",
            "suggestion",
            "blocked_reason",
            "detail",
        ),
    )
    if not error and "write_file" in lowered and "error" in lowered:
        error = "write_file failed"
    scope = _deep_get_text(payload, ("allowed_scope", "allowed_write_paths", "changed_files", "file", "path"))
    return {
        "tool": tool,
        "error_type": error_type or "tool_failure",
        "reason": _trim_prompt_safe_text(error or "tool execution failed"),
        **({"scope": _trim_prompt_safe_text(scope)} if scope else {}),
        "prompt_safe": True,
        "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
    }


def parse_tool_failure_summary(content: Any) -> dict[str, Any] | None:
    """Parse an already-sanitized tool failure summary."""

    text = str(content or "")
    if not text.startswith(_TOOL_FAILURE_SUMMARY_PREFIX):
        return None
    try:
        payload = json.loads(text[len(_TOOL_FAILURE_SUMMARY_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return dict(payload)


def format_tool_failure_summary(payload: dict[str, Any]) -> str:
    return _TOOL_FAILURE_SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False)


def prompt_safe_tool_failure_summary(role: str, content: str) -> str | None:
    """Return a compact failure summary when raw tool receipts would leak."""

    summary = tool_failure_summary_payload(role, content)
    if summary is None:
        return None
    return format_tool_failure_summary(summary)


def prompt_safe_message_content(role: str, content: Any) -> str:
    """Return message content safe to include in LLM prompts."""

    text = str(content or "")
    return prompt_safe_tool_failure_summary(role, text) or text
