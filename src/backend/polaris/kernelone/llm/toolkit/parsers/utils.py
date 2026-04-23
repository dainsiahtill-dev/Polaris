"""Shared parsing utilities for the parsers module.

Contains helper functions and common utilities used by all parsers.

NOTE: ParsedToolCall is now a type alias to the canonical ToolCall from
polaris.kernelone.llm.contracts.tool. All new code should import ToolCall directly.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# Import canonical ToolCall and create backward-compatible alias
from polaris.kernelone.llm.contracts.tool import ToolCall

# Backward compatibility: ParsedToolCall is now an alias to canonical ToolCall
# The fields are identical: id, name, arguments, raw, source, parse_error
ParsedToolCall = ToolCall

logger = logging.getLogger(__name__)


def _normalize_allowed_tool_names(
    allowed_tool_names: Iterable[str] | None,
) -> set[str]:
    """Normalize allowed tool names to a set.

    Args:
        allowed_tool_names: List of allowed tool names

    Returns:
        Set of normalized tool names (lowercase, stripped)
    """
    return {str(item).strip().lower() for item in (allowed_tool_names or []) if str(item).strip()}


def parse_value(value: str) -> Any:
    """Parse a string value to its appropriate type.

    Args:
        value: String value to parse

    Returns:
        Parsed value (str, int, float, bool, or list/dict if JSON)
    """
    value = value.strip()

    # Try JSON parsing
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    # Boolean values
    lower = value.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False

    # Numeric values
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    # String values (remove quotes)
    if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
        return value[1:-1]

    return value


def resolve_signature_requirement(require_signature: bool | None) -> bool:
    """Resolve whether signature verification is required.

    Args:
        require_signature: Explicit setting, or None to check environment

    Returns:
        True if signature verification is required
    """
    if require_signature is not None:
        return bool(require_signature)

    raw = (
        str(
            os.environ.get("KERNELONE_REQUIRE_SIGNED_TOOL_TAGS", "")
        )
        .strip()
        .lower()
    )

    if not raw:
        return False
    return raw in {"1", "true", "yes", "on"}


def is_quoted_line(text: str, start: int) -> bool:
    """Check if a position in text is within a quoted line.

    Args:
        text: Full text
        start: Position to check

    Returns:
        True if the line starting at 'start' is a quoted line
    """
    line_start = text.rfind("\n", 0, start)
    if line_start < 0:
        line_start = 0
    else:
        line_start += 1
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].lstrip()
    return line.startswith(">")


def stable_json(value: Any) -> Any:
    """Convert an object to a stably serializable structure.

    Args:
        value: Object to convert

    Returns:
        JSON-serializable structure
    """
    if isinstance(value, dict):
        return {str(k): stable_json(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [stable_json(item) for item in value]
    if isinstance(value, tuple):
        return [stable_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def tool_signature(call: ParsedToolCall) -> tuple[str, str]:
    """Generate a stable signature for a tool call.

    Args:
        call: Parsed tool call

    Returns:
        Tuple of (name, args_json) for deduplication
    """
    name = str(call.name or "").strip().lower()
    args = stable_json(call.arguments if isinstance(call.arguments, dict) else {})
    args_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return name, args_json


def deduplicate_tool_calls(calls: list[ParsedToolCall]) -> list[ParsedToolCall]:
    """Deduplicate tool calls based on name and arguments.

    Args:
        calls: List of parsed tool calls

    Returns:
        Deduplicated list of tool calls
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[ParsedToolCall] = []
    for call in calls:
        signature = tool_signature(call)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(call)
    return deduped
