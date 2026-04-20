"""Polaris AI Platform - Tool Call Accumulator

Accumulates tool call deltas from streaming responses.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _ToolCallAccumulator:
    """Accumulator for tool call deltas during streaming.

    Tracks tool name, call ID, arguments buffer, and provider metadata
    for assembling complete tool calls from streaming fragments.
    """

    tool_name: str = ""
    call_id: str = ""
    arguments_buffer: str = ""
    explicit_arguments: dict[str, Any] | None = None
    explicit_arguments_provisional: bool = False
    emitted_signature: str = ""
    provider_meta: dict[str, Any] = field(default_factory=dict)


def _provider_supports_structured_stream(provider_instance: Any) -> bool:
    """Check if provider supports structured streaming events.

    Args:
        provider_instance: The provider instance to check.

    Returns:
        True if provider has invoke_stream_events method.
    """
    return "invoke_stream_events" in getattr(provider_instance.__class__, "__dict__", {})


def _normalize_arguments(value: Any) -> tuple[dict[str, Any], bool]:
    """Normalize arguments to a dict if possible.

    Args:
        value: Raw arguments value (dict, str, or None).

    Returns:
        Tuple of (normalized dict, is_complete).
    """
    if isinstance(value, dict):
        return dict(value), True
    if value is None:
        return {}, False
    text = str(value or "").strip()
    if not text:
        return {}, False
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, False
    if isinstance(parsed, dict):
        return parsed, True
    return {"value": parsed}, True


def _tool_accumulator_key(tool_call: dict[str, Any], ordinal: int) -> str:
    """Generate stable key for tool call identity.

    Uses stable identifiers (call_id, tool_name) to ensure all deltas of the
    same tool call map to the same key, regardless of ordinal number.

    Args:
        tool_call: The tool call delta dict.
        ordinal: Fallback ordinal number.

    Returns:
        Stable key string for this tool call.
    """
    tool_name = str(tool_call.get("tool") or "").strip()
    call_id = str(tool_call.get("call_id") or "").strip()
    content_block_index = tool_call.get("content_block_index")
    stream_index = tool_call.get("index")

    # Priority 1: Provider-emitted indices remain stable across delta fragments
    # even when later chunks start adding call_id/tool_name fields.
    if isinstance(content_block_index, int):
        return f"content_block_index:{content_block_index}"
    if isinstance(stream_index, int):
        return f"index:{stream_index}"
    # Priority 2: Use call_id when the provider does not expose a stream index.
    if call_id:
        return f"call_id:{call_id}"
    # Priority 3: Use tool_name (without ordinal to ensure stable key)
    if tool_name:
        return f"tool:{tool_name}"
    # Priority 4: Fallback to ordinal only when no other identifier exists
    return f"ordinal:{ordinal}"


def _safe_text_length(value: Any) -> int:
    """Return text length for stream audit payload without raising TypeError.

    Args:
        value: The value to measure.

    Returns:
        Length if string/bytes, 0 otherwise.
    """
    if isinstance(value, str):
        return len(value)
    if isinstance(value, bytes):
        return len(value)
    return 0


def _debug_compact_payload(value: Any, *, max_chars: int = 2000) -> Any:
    """Render payload for debug logs while keeping size bounded.

    Args:
        value: The value to compact.
        max_chars: Maximum characters before truncation.

    Returns:
        Compact representation suitable for logging.
    """
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    if len(serialized) <= max_chars:
        try:
            return json.loads(serialized)
        except (TypeError, ValueError, json.JSONDecodeError):
            return serialized
    return {
        "_truncated": True,
        "preview": serialized[:max_chars],
        "total_length": len(serialized),
    }


def _debug_tool_arguments(arguments: Any, arguments_text: str | None = None) -> Any:
    """Best-effort arguments snapshot for tool debug events.

    Args:
        arguments: Raw arguments value.
        arguments_text: Optional text representation.

    Returns:
        Compact representation of arguments.
    """
    normalized, complete = _normalize_arguments(arguments)
    if complete:
        return _debug_compact_payload(normalized)
    text = str(arguments_text or "").strip()
    if not text:
        return {}
    normalized_text, text_complete = _normalize_arguments(text)
    if text_complete:
        return _debug_compact_payload(normalized_text)
    return _debug_compact_payload({"_raw_arguments_text": text})
