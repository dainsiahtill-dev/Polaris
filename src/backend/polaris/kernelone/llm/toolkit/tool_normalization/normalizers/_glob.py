"""Normalizer for glob tool.

Refactored (2026-04-05): Simplified to only handle complex transformations.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- File patterns list to single pattern extraction
- Boolean coercion for recursive option
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_bool, _coerce_int

_FILE_PATTERNS_KEY = "file_patterns"


def normalize_glob_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize glob arguments.

    Parameter aliases (glob/query/q -> pattern, max/limit/n -> max_results, etc.)
    are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.

    This normalizer ONLY handles complex transformations:
    - File patterns list to single pattern extraction
    - Boolean coercion for recursive option
    """
    normalized = dict(tool_args)

    # 1. Extract pattern from file_patterns (array) if no pattern set
    # This is a complex transformation (list -> single value)
    if not normalized.get("pattern") and _FILE_PATTERNS_KEY in normalized:
        file_patterns = normalized.get(_FILE_PATTERNS_KEY)
        if isinstance(file_patterns, list) and file_patterns:
            # Use first non-empty pattern from list
            for fp in file_patterns:
                if isinstance(fp, str) and fp.strip():
                    normalized["pattern"] = fp.strip()
                    break
        # Remove file_patterns key after extraction
        normalized.pop(_FILE_PATTERNS_KEY, None)

    # 2. Coerce recursive option to boolean
    # This is a complex transformation that cannot be expressed as arg_aliases
    if "recursive" in normalized:
        normalized["recursive"] = _coerce_bool(normalized["recursive"])
    else:
        normalized.setdefault("recursive", False)

    # 3. Coerce max_results (from max/limit/n aliases) to int
    if "max_results" in normalized:
        int_value = _coerce_int(normalized["max_results"])
        if int_value is not None:
            normalized["max_results"] = int_value

    return normalized
