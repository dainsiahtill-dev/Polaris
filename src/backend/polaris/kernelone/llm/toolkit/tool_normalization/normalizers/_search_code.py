"""Normalizer for search_code/ripgrep tools.

Refactored (2026-04-05): Simplified to only handle complex transformations.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- Scope pattern normalization: adds **/* to directory paths
- File patterns list normalization
- Case sensitivity normalization for fixed search
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._shared import _coerce_int, _normalize_workspace_alias_path


def normalize_search_code_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize search_code/ripgrep arguments.

    Parameter aliases (query/text/search/keyword/q -> query, file/file_path -> path,
    max/limit/n -> max_results, etc.) are handled by SchemaDrivenNormalizer via
    contracts.py arg_aliases.

    This normalizer ONLY handles complex transformations:
    - Scope pattern normalization (directory -> directory/**/*)
    - File patterns list normalization
    - Case sensitivity normalization for fixed search
    """
    normalized = dict(tool_args)

    # 1. Normalize scope pattern (complex transformation)
    _normalize_scope_pattern(normalized)

    # 2. Normalize file_patterns list
    _normalize_file_patterns(normalized)

    # 3. Normalize case_sensitivity for fixed search (complex transformation)
    _normalize_case_sensitivity(normalized)

    # 4. Coerce max_results (from max/limit/n aliases) to int
    if "max_results" in normalized:
        int_value = _coerce_int(normalized["max_results"])
        if int_value is not None:
            normalized["max_results"] = int_value

    # 5. Set defaults
    normalized.setdefault("file_patterns", [])
    normalized.setdefault("max_results", 50)
    normalized.setdefault("context_lines", 0)

    return normalized


def _normalize_scope_pattern(normalized: dict[str, Any]) -> None:
    """Normalize scope pattern for search.

    Complex transformation: if path doesn't contain glob markers,
    automatically append /**/* to treat it as a directory search.
    """
    path_value = normalized.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return

    token = _normalize_workspace_alias_path(path_value).replace("\\", "/")
    if not token:
        return

    token = token.lstrip("./")
    if not token:
        return

    # If path contains glob markers, keep as-is
    if any(marker in token for marker in ("*", "?", "[")):
        normalized["path"] = token
        return

    # Otherwise, append /**/* for directory search
    normalized["path"] = token if Path(token).suffix else f"{token}/**/*"


def _normalize_file_patterns(normalized: dict[str, Any]) -> None:
    """Normalize file_patterns list from various sources.

    Handles consolidation of file_patterns from different input formats.
    """
    # Normalize to list
    raw_patterns = normalized.get("file_patterns")
    if isinstance(raw_patterns, str):
        if raw_patterns.strip():
            normalized["file_patterns"] = [raw_patterns.strip()]
        else:
            normalized["file_patterns"] = []
    elif isinstance(raw_patterns, list):
        normalized["file_patterns"] = [str(item) for item in raw_patterns if str(item or "").strip()]
    else:
        normalized["file_patterns"] = []


def _normalize_case_sensitivity(normalized: dict[str, Any]) -> None:
    """Normalize case_sensitivity for fixed search type.

    Complex transformation: ensures case_sensitive is a proper boolean
    for fixed/literal search types.
    """
    search_type = str(normalized.get("type") or "").strip().lower()
    if search_type in {"fixed", "literal"}:
        normalized["case_sensitive"] = bool(normalized.get("case_sensitive") or False)
