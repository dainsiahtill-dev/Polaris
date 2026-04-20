"""Normalizer for background_run and background_check tools.

Refactored (2026-04-05): Added to handle timeout and task_id normalization.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- background_run: max_seconds -> timeout conversion, timeout clamping [1, 3600]
- background_check: id -> task_id conversion
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_int


def normalize_background_run_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize background_run arguments.

    This normalizer handles:
    - max_seconds -> timeout conversion
    - timeout clamping [1, 3600]

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # Handle max_seconds -> timeout conversion
    if "timeout" not in normalized or normalized.get("timeout") is None:
        max_seconds = _coerce_int(normalized.get("max_seconds"))
        if max_seconds is not None and max_seconds > 0:
            normalized["timeout"] = max_seconds

    # Remove max_seconds alias
    normalized.pop("max_seconds", None)

    # Clamp timeout to valid range [1, 3600]
    if "timeout" in normalized:
        timeout_val = _coerce_int(normalized.get("timeout"))
        if timeout_val is not None:
            normalized["timeout"] = max(1, min(3600, timeout_val))
        else:
            normalized["timeout"] = 300  # Default

    # Set default if not present
    normalized.setdefault("timeout", 300)

    return normalized


def normalize_background_check_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize background_check arguments.

    This normalizer handles:
    - id -> task_id conversion

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # Handle id -> task_id conversion
    if "task_id" not in normalized or normalized.get("task_id") is None:
        task_id = normalized.get("id")
        if task_id is not None and str(task_id).strip():
            normalized["task_id"] = str(task_id).strip()

    # Remove id alias
    normalized.pop("id", None)

    return normalized
