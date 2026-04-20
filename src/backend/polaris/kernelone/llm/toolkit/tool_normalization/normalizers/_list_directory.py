"""Normalizer for list_directory tool.

Refactored (2026-04-05): Simplified to only handle complex transformations.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- Path normalization (workspace alias resolution)
- Boolean coercion for recursive/case_sensitive options
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_bool, _coerce_int, _normalize_workspace_alias_path


def normalize_list_directory_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize list_directory arguments.

    Parameter aliases (root/dir/directory -> path, max/limit/n -> max_entries, etc.)
    are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.

    This normalizer ONLY handles complex transformations:
    - Path normalization (workspace alias resolution)
    - Boolean coercion for recursive/case_sensitive options
    """
    normalized = dict(tool_args)

    # 1. Normalize path value (workspace alias resolution)
    if normalized.get("path"):
        candidate_path = normalized.get("path")
        if isinstance(candidate_path, str) and candidate_path.strip():
            normalized["path"] = _normalize_workspace_alias_path(candidate_path.strip())

    # 2. Coerce recursive option to boolean
    if "recursive" in normalized:
        normalized["recursive"] = _coerce_bool(normalized["recursive"])
    else:
        normalized.setdefault("recursive", False)

    # 3. Coerce case_sensitive option to boolean
    if "case_sensitive" in normalized:
        normalized["case_sensitive"] = _coerce_bool(normalized["case_sensitive"])

    # 4. Coerce max_entries (from max/limit/n aliases) to int
    if "max_entries" in normalized:
        int_value = _coerce_int(normalized["max_entries"])
        if int_value is not None:
            normalized["max_entries"] = int_value

    # 5. Set defaults
    normalized.setdefault("max_entries", 200)
    normalized.setdefault("include_hidden", False)

    return normalized
