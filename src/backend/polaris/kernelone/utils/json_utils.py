"""
KernelOne JSON Utilities - Single source of truth for JSON parsing functions.

This module consolidates all duplicated JSON utility functions across the codebase.
All modules should import from here instead of defining their own versions.

Usage:
    from polaris.kernelone.utils.json_utils import safe_json_loads, parse_json_payload
"""

import json
import logging
import re
from typing import Any, TypeVar

_logger = logging.getLogger(__name__)

T = TypeVar("T", bound=dict[str, Any] | list[Any] | str | int | float | bool | None)


def safe_json_loads(text: str, default: T | None = None) -> T | None:
    """
    Safely parse JSON string, returning default on failure.

    This is the canonical implementation - all modules should use this instead
    of defining local safe_json_loads() functions.

    Args:
        text: JSON string to parse.
        default: Value to return if parsing fails. Defaults to None.

    Returns:
        Parsed JSON value or default on failure.

    Examples:
        >>> safe_json_loads('{"key": "value"}')
        {'key': 'value'}
        >>> safe_json_loads('invalid json')
        None
        >>> safe_json_loads('invalid json', default={})
        {}
    """
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return default


def parse_json_payload(text: str) -> dict[str, Any] | None:
    """
    Parse JSON from text, handling markdown code blocks and substring extraction.

    Handles common LLM output patterns where JSON is wrapped in markdown:
    - ```json\n{...}\n```
    - ```\n{...}\n```
    - Raw JSON
    - JSON embedded in larger text (extracts via find)

    Args:
        text: Text containing JSON (possibly in markdown code block).

    Returns:
        Parsed dictionary or None if parsing fails.

    Examples:
        >>> parse_json_payload('```json\\n{"key": "value"}\\n```')
        {'key': 'value'}
        >>> parse_json_payload('{"key": "value"}')
        {'key': 'value'}
    """
    if not text:
        return None

    candidate = text.strip()

    # Handle markdown code blocks
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
        candidate = candidate.strip()

    if not candidate:
        return None

    # First try direct parse
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError) as exc:
        _logger.debug("Failed to parse JSON payload: %s", exc)

    # Fallback: extract JSON substring from text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            _logger.debug("Failed to parse JSON from substring: %s", exc)

    return None


def format_json(data: Any, indent: int = 2) -> str:
    """
    Format data as JSON string with consistent indentation.

    Args:
        data: Data to serialize.
        indent: Indentation level. Defaults to 2.

    Returns:
        JSON string representation.
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)


# Backward compatibility aliases
_safe_json_loads = safe_json_loads
_parse_json_payload = parse_json_payload


__all__ = [
    "_parse_json_payload",
    # Backward compatibility
    "_safe_json_loads",
    "format_json",
    "parse_json_payload",
    "safe_json_loads",
]
