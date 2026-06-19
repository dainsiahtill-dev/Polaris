"""Hardened JSON parsing and serialization helpers for the deterministic judge.

This module is a leaf foundation for the ``judge`` package: it depends only on
the standard library (``json``, ``re``) so it can be safely imported by every
other judge module without risk of circular imports.

The functions here protect against deeply-nested malicious JSON payloads that
could otherwise cause stack overflow during parsing.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Default maximum nesting depth to prevent stack overflow from malicious JSON.
_DEFAULT_JSON_MAX_DEPTH: int = 100


class _ExcessiveNestingError(ValueError):
    """Raised when JSON nesting depth exceeds the configured limit.

    This is a subclass of ValueError for compatibility with json.JSONDecodeError,
    allowing callers to catch this specific error type.
    """

    def __init__(self, max_depth: int, message: str | None = None) -> None:
        self.max_depth = max_depth
        default_msg = f"JSON nesting depth exceeds maximum allowed depth of {max_depth}"
        super().__init__(message or default_msg)


def _count_json_depth(s: str) -> int:
    """Count the maximum nesting depth of a JSON string without parsing it.

    This function performs a quick scan of the JSON string to estimate
    the nesting depth by counting unmatched opening braces/brackets.

    Args:
        s: JSON string to analyze.

    Returns:
        Maximum nesting depth found in the string.
    """
    max_depth = 0
    current_depth = 0
    in_string = False
    escape_next = False

    for char in s:
        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char in {"{", "["}:
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        elif char in {"}", "]"}:
            current_depth = max(0, current_depth - 1)

    return max_depth


def _safe_json_loads(
    s: str,
    max_depth: int = _DEFAULT_JSON_MAX_DEPTH,
) -> dict[str, Any] | list[Any]:
    """Parse JSON string with depth limit to prevent stack overflow.

    This function provides safe JSON parsing that protects against
    deeply-nested malicious JSON payloads that could cause stack overflow.

    Args:
        s: JSON string to parse.
        max_depth: Maximum allowed nesting depth. Defaults to 100.
            Values less than 1 are treated as 1 (only root object allowed).

    Returns:
        Parsed JSON object (dict or list).

    Raises:
        _ExcessiveNestingError: If nesting depth exceeds max_depth.
        json.JSONDecodeError: If the input is not valid JSON.
    """
    effective_max_depth = max(1, max_depth)

    # Fast pre-check: estimate depth without full parsing
    estimated_depth = _count_json_depth(s)
    if estimated_depth > effective_max_depth:
        raise _ExcessiveNestingError(
            effective_max_depth,
            f"JSON nesting depth {estimated_depth} exceeds maximum allowed depth of {effective_max_depth}",
        )

    # Now parse with depth-limited decoder
    return _json_loads_with_depth_limit(s, effective_max_depth)


def _json_loads_with_depth_limit(s: str, max_depth: int) -> dict[str, Any] | list[Any]:
    """Parse JSON with depth-limited object and array hooks.

    Uses object_hook to track depth during parsing, as Python's json module
    calls this hook for each nested dictionary.

    Args:
        s: JSON string to parse.
        max_depth: Maximum allowed nesting depth.

    Returns:
        Parsed JSON object.

    Raises:
        _ExcessiveNestingError: If nesting depth exceeds max_depth.
    """
    current_depth = [0]

    def depth_limited_object_hook(obj: dict[str, Any]) -> dict[str, Any]:
        current_depth[0] += 1
        if current_depth[0] > max_depth:
            raise _ExcessiveNestingError(
                max_depth,
                f"JSON object nesting depth {current_depth[0]} exceeds maximum allowed depth of {max_depth}",
            )
        return obj

    return json.loads(s, object_hook=depth_limited_object_hook)


def _serialize_args(args: dict[str, object]) -> str:
    """Serialize a dictionary of arguments to a JSON string.

    Args:
        args: Dictionary of arguments to serialize.

    Returns:
        JSON string representation, or str(args) if serialization fails.
    """
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(args)


def _extract_json_dict(text: str) -> dict[str, object] | None:
    """Extract a JSON object from text that may contain markdown code blocks.

    This function searches for JSON objects within markdown code fences
    or as standalone JSON. It uses depth-limited parsing to prevent
    stack overflow from malicious inputs.

    Args:
        text: Input text that may contain JSON in code fences or standalone.

    Returns:
        Parsed dictionary if valid JSON object found, None otherwise.

    Raises:
        _ExcessiveNestingError: If nesting depth exceeds the configured limit.
    """
    candidate = str(text or "").strip()
    if not candidate:
        return None

    raw_candidates = re.findall(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        candidate,
        re.DOTALL | re.IGNORECASE,
    )
    if candidate.startswith("{") and candidate.endswith("}"):
        raw_candidates.append(candidate)

    for item in raw_candidates:
        try:
            payload = _safe_json_loads(item)
        except _ExcessiveNestingError:
            # Re-raise excessive nesting errors - this is a security issue
            raise
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None
