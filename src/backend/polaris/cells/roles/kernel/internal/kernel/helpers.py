"""Kernel Helper Functions - 内核辅助函数

提供工具参数处理、数据序列化、文本处理等辅助功能。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

# 默认角色写调用限制
_DEFAULT_ROLE_WRITE_CALL_LIMITS = {
    "director": 3,
}


def normalize_tool_args(raw_args: Any) -> dict[str, Any]:
    """Normalize tool arguments from heterogeneous LLM outputs.

    Args:
        raw_args: 原始工具参数（可能是 dict, str, 或其他类型）

    Returns:
        规范化后的参数字典
    """
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        candidate = raw_args.strip()
        if not candidate:
            return {}
        try:
            decoded = json.loads(candidate)
        except (RuntimeError, ValueError):
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def extract_structured_tool_calls(data: Any) -> list[dict[str, Any]]:
    """Convert structured schema tool calls to OpenAI-style native tool_calls.

    Args:
        data: 结构化工具调用数据

    Returns:
        OpenAI 风格的工具调用列表
    """
    if not isinstance(data, dict):
        return []

    raw_calls = data.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            continue

        function_payload = item.get("function")
        if not isinstance(function_payload, dict):
            function_payload = {}

        tool_name = str(item.get("tool") or item.get("name") or function_payload.get("name") or "").strip()
        if not tool_name:
            continue

        args = normalize_tool_args(item.get("arguments"))
        if not args:
            args = normalize_tool_args(item.get("args"))
        if not args:
            args = normalize_tool_args(item.get("input"))
        if not args:
            args = normalize_tool_args(function_payload.get("arguments"))
        if not args:
            args = normalize_tool_args(function_payload.get("input"))

        normalized.append(
            {
                "id": str(item.get("id") or f"structured_{index + 1}"),
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )

    return normalized


def resolve_role_write_call_limit(role_id: str) -> int:
    """Resolve per-turn write tool call limit for a role (0 = unlimited).

    Args:
        role_id: 角色标识

    Returns:
        写调用限制数量
    """
    import os

    role = str(role_id or "").strip().lower()
    default_limit = int(_DEFAULT_ROLE_WRITE_CALL_LIMITS.get(role, 0))
    role_env_key = f"POLARIS_ROLE_WRITE_CALLS_PER_TURN_{role.upper()}" if role else ""
    raw_value = ""
    if role_env_key:
        raw_value = str(os.environ.get(role_env_key, "")).strip()
    if not raw_value:
        raw_value = str(os.environ.get("POLARIS_ROLE_WRITE_CALLS_PER_TURN", "")).strip()
    if not raw_value:
        return max(0, default_limit)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return max(0, default_limit)
    return max(0, min(parsed, 30))


def make_json_safe(value: Any, *, _depth: int = 0) -> Any:
    """Recursively strip non-JSON-serializable values for canonical journal.

    Args:
        value: 要序列化的值
        _depth: 递归深度（防止无限递归）

    Returns:
        JSON 安全的值
    """
    if _depth > 8:
        return str(value)
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {k: make_json_safe(v, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(item, _depth=_depth + 1) for item in value]
    # Strip non-serializable: datetime, Pydantic models, dataclasses, etc.
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def build_stream_event_message(event_type: str, payload: dict[str, Any]) -> str:
    """Build a compact human-readable message for stream events.

    Args:
        event_type: 事件类型
        payload: 事件数据

    Returns:
        人类可读的事件消息
    """
    event_name = str(event_type or "").strip() or "stream_event"
    if event_name in {"thinking_chunk", "content_chunk"}:
        return str(payload.get("content") or "")
    if event_name == "tool_call":
        return f"tool_call:{payload.get('tool') or 'unknown'!s}"
    if event_name == "tool_result":
        tool_name = str(payload.get("tool") or "unknown")
        status = "ok" if bool(payload.get("success", False)) else "failed"
        return f"tool_result:{tool_name}:{status}"
    if event_name == "error":
        return str(payload.get("error") or "stream_error")
    return event_name


def build_text_preview(text: str, max_length: int = 240) -> str:
    """Build a compact preview of text content.

    Args:
        text: 文本内容
        max_length: 最大长度

    Returns:
        预览文本
    """
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    if len(compact) <= max_length:
        return compact
    return f"{compact[:max_length]}..."


def summarize_args(args: Any, max_length: int = 200) -> str:
    """Convert tool arguments to readable summary string.

    Args:
        args: 工具参数
        max_length: 最大长度

    Returns:
        参数摘要字符串
    """
    if not args:
        return ""

    if isinstance(args, dict):
        # Only keep key parameters
        summary_parts = []
        for key, value in args.items():
            if key in ("file_path", "path", "file", "content", "command", "code", "search", "replace"):
                # Special handling for long parameters
                value_str = str(value)
                if len(value_str) > max_length:
                    value_str = value_str[:max_length] + "..."
                summary_parts.append(f"{key}={value_str}")
            else:
                # Other parameters show only first 50 chars
                value_str = str(value)
                if len(value_str) > 50:
                    value_str = value_str[:50] + "..."
                summary_parts.append(f"{key}={value_str}")
        return ", ".join(summary_parts)
    elif isinstance(args, str):
        return args[:max_length] + "..." if len(args) > max_length else args
    else:
        return str(args)[:max_length]


def quality_result_to_dict(value: Any | None) -> dict[str, Any] | None:
    """Convert quality result to dictionary.

    Compatible with dataclass / namespace / dict types.

    Args:
        value: 质量结果

    Returns:
        字典形式的质量结果
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    payload = getattr(value, "__dict__", None)
    if isinstance(payload, dict):
        return dict(payload)
    return {"value": str(value)}


__all__ = [
    "_DEFAULT_ROLE_WRITE_CALL_LIMITS",
    "build_stream_event_message",
    "build_text_preview",
    "extract_structured_tool_calls",
    "make_json_safe",
    "normalize_tool_args",
    "quality_result_to_dict",
    "resolve_role_write_call_limit",
    "summarize_args",
]
