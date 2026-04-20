"""Normalizer for read_file tool.

Refactored (2026-04-05): Simplified to only handle range parameter conversion.
Path aliases are now handled by SchemaDrivenNormalizer via contracts.py arg_aliases.

Complex transformation (not expressible as arg_aliases):
- Range parameter conversion: offset/limit/start/end -> start_line/end_line
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_int, _remove_aliases

# Range aliases that need conversion (cannot be expressed as simple arg_aliases)
_RANGE_ALIASES = ("offset", "limit", "start", "end")


def normalize_read_file_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize read_file arguments.

    This normalizer ONLY handles range parameter conversion:
    - GPT style: offset -> start_line, limit -> end_line (or start_line + limit - 1)
    - Grep style: start -> start_line, end -> end_line

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # 1. Normalize range parameters (GPT style and Grep style)
    # This is a complex transformation that cannot be expressed as arg_aliases
    _normalize_range_params(normalized)

    # 2. Clean up range aliases
    _remove_aliases(normalized, _RANGE_ALIASES)

    # 3. Set defaults
    normalized.setdefault("max_bytes", 200000)
    normalized.setdefault("range_required", False)

    return normalized


def _normalize_range_params(normalized: dict[str, Any]) -> None:
    """Normalize range parameters (offset/limit -> start_line/end_line).

    This handles the complex conversion logic:
    - GPT style: offset -> start_line
    - GPT style: limit -> end_line (or start_line + limit - 1)
    - Grep style: start -> start_line, end -> end_line
    """
    # GPT style: offset -> start_line
    if "offset" in normalized and "start_line" not in normalized:
        offset_val = _coerce_int(normalized.get("offset"))
        if offset_val is not None and offset_val > 0:
            normalized["start_line"] = offset_val

    # GPT style: limit -> end_line (or start_line + limit - 1)
    if "limit" in normalized and "end_line" not in normalized:
        limit_val = _coerce_int(normalized.get("limit"))
        start_val = _coerce_int(normalized.get("start_line"))
        if limit_val is not None and limit_val > 0:
            if start_val is not None and start_val > 0:
                normalized["end_line"] = start_val + limit_val - 1
            else:
                normalized["end_line"] = limit_val

    # Grep style: start -> start_line
    if "start" in normalized and "start_line" not in normalized:
        start_val = _coerce_int(normalized.get("start"))
        if start_val is not None:
            normalized["start_line"] = start_val

    # Grep style: end -> end_line
    if "end" in normalized and "end_line" not in normalized:
        end_val = _coerce_int(normalized.get("end"))
        if end_val is not None:
            normalized["end_line"] = end_val
