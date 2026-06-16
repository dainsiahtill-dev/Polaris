"""Deterministic JSON crusher (T2-B).

Strategy for large JSON tool outputs (e.g. API responses, file listings):
keep the *schema* (the set of key paths) plus a sample of rows, flag numeric
outliers, and collapse the rest into a count. This preserves the information a
reasoning model actually needs (shape + representative values + magnitude)
while dropping the bulk of repetitive rows.

No LLM, deterministic, fail-closed: invalid JSON or a non-shrinking result is
rejected by :func:`~polaris.kernelone.context.crushers.base.finalize`.
"""

from __future__ import annotations

import json
from typing import Any

from polaris.kernelone.context.crushers.base import CrushKind, CrushResult, finalize, no_op

# Sampling parameters for large arrays.
_SAMPLE_HEAD: int = 3
_SAMPLE_TAIL: int = 2
# Arrays at or below this length are kept verbatim (no benefit to sampling).
_MIN_ARRAY_LEN_TO_SAMPLE: int = _SAMPLE_HEAD + _SAMPLE_TAIL + 1


def _collect_key_paths(value: Any, prefix: str, out: set[str]) -> None:
    """Recursively collect dotted key paths describing the JSON schema.

    Args:
        value: A decoded JSON value.
        prefix: The dotted path accumulated so far.
        out: Mutable set collecting the discovered key paths.
    """
    if isinstance(value, dict):
        for key in value:
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            out.add(child_prefix)
            _collect_key_paths(value[key], child_prefix, out)
    elif isinstance(value, list):
        # Describe element shape under "[]" without exploding every index.
        child_prefix = f"{prefix}[]" if prefix else "[]"
        for element in value[:_MIN_ARRAY_LEN_TO_SAMPLE]:
            _collect_key_paths(element, child_prefix, out)


def _numeric_outliers(rows: list[Any]) -> dict[str, Any]:
    """Compute min/max for numeric scalar rows, if any.

    Args:
        rows: The list elements.

    Returns:
        A dict with ``min``/``max`` when numeric scalars are present, else {}.
    """
    numbers = [r for r in rows if isinstance(r, (int, float)) and not isinstance(r, bool)]
    if not numbers:
        return {}
    return {"min": min(numbers), "max": max(numbers)}


def _crush_array(arr: list[Any]) -> Any:
    """Crush a large array into head/tail samples + counts + outliers.

    Args:
        arr: The array to crush.

    Returns:
        A compacted JSON-serializable structure, or the array unchanged when it
        is too short to benefit.
    """
    if len(arr) < _MIN_ARRAY_LEN_TO_SAMPLE:
        return arr

    sample_head = arr[:_SAMPLE_HEAD]
    sample_tail = arr[-_SAMPLE_TAIL:] if _SAMPLE_TAIL > 0 else []
    omitted = len(arr) - _SAMPLE_HEAD - _SAMPLE_TAIL

    crushed: dict[str, Any] = {
        "_crushed": {
            "total": len(arr),
            "omitted": max(0, omitted),
            "sample_head": sample_head,
            "sample_tail": sample_tail,
        }
    }
    outliers = _numeric_outliers(arr)
    if outliers:
        crushed["_crushed"]["outliers"] = outliers
    return crushed


def _crush_value(value: Any) -> Any:
    """Recursively crush a decoded JSON value.

    Args:
        value: A decoded JSON value.

    Returns:
        A compacted JSON-serializable structure.
    """
    if isinstance(value, list):
        return _crush_array(value)
    if isinstance(value, dict):
        return {key: _crush_value(child) for key, child in value.items()}
    return value


def crush_json(text: str) -> CrushResult:
    """Crush a JSON document deterministically.

    Args:
        text: The raw JSON text.

    Returns:
        A :class:`CrushResult`. ``kind`` is NONE when the text is not valid JSON
        or the crushed form is not strictly smaller.
    """
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return no_op(text)

    paths: set[str] = set()
    _collect_key_paths(decoded, "", paths)
    key_paths = sorted(paths)

    crushed_body = _crush_value(decoded)
    envelope: dict[str, Any] = {
        "_schema_keys": key_paths,
        "data": crushed_body,
    }

    try:
        crushed_text = json.dumps(envelope, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return no_op(text)

    return finalize(text, crushed_text, CrushKind.JSON)


__all__ = ["crush_json"]
