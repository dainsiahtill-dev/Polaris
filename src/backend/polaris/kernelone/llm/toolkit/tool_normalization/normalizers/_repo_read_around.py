"""Normalizer for repo_read_around tool.

Refactored (2026-04-05): Added to handle start/end to line/radius conversion.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- start/end -> line/radius conversion (Grep style to GPT style)
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_int


def normalize_repo_read_around_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize repo_read_around arguments.

    This normalizer handles start/end to line/radius conversion:
    - Grep style: start + end -> GPT style line + radius

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # Handle start/end -> line/radius conversion
    # If line is not set, compute from start/end
    if "line" not in normalized or normalized.get("line") is None:
        start_value = normalized.get("start")
        end_value = normalized.get("end")

        start_line = _coerce_int(start_value)
        end_line = _coerce_int(end_value)

        if start_line is not None and end_line is not None and end_line >= start_line:
            # Compute center line
            normalized["line"] = start_line + ((end_line - start_line) // 2)
            # Compute radius from the range
            if "radius" not in normalized or normalized.get("radius") is None:
                normalized["radius"] = max(1, (end_line - start_line) // 2)

    # Remove start/end aliases
    normalized.pop("start", None)
    normalized.pop("end", None)

    # Set defaults
    normalized.setdefault("radius", 5)

    return normalized
