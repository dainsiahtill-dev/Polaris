"""Normalizer for repo_rg tool.

Polaris primary code search tool using ripgrep.

Refactored (2026-04-05): Expanded to handle complex transformations.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- Value range clamping: max_results [1, 10000], context_lines [0, 100]
- Path normalization (workspace alias resolution)
- path->paths conversion (string path to array)
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_int, _normalize_workspace_alias_path


def normalize_repo_rg_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize repo_rg arguments.

    Parameter aliases (query/text/search/keyword/q -> pattern, file/file_path -> path,
    max/limit/n -> max_results, g -> glob, case/sensitive -> case_sensitive, etc.)
    are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.

    This normalizer ONLY handles complex transformations:
    - Value range clamping (max_results [1, 10000], context_lines [0, 100])
    - Path normalization (workspace alias resolution)
    - path->paths conversion (string path to array)
    """
    normalized = dict(tool_args)

    # 1. Normalize path value (workspace alias resolution)
    if normalized.get("path"):
        candidate_path = normalized.get("path")
        if isinstance(candidate_path, str) and candidate_path.strip():
            normalized["path"] = _normalize_workspace_alias_path(candidate_path.strip())

    # 2. path -> paths conversion (string path to array)
    # ripgrep expects paths as array
    if normalized.get("path") and not normalized.get("paths"):
        path_val = normalized.get("path")
        if isinstance(path_val, str) and path_val.strip():
            normalized["paths"] = [path_val.strip()]

    # 3. Clamp max_results to valid range [1, 10000]
    # This is a complex transformation that cannot be expressed as arg_aliases
    if "max_results" in normalized:
        int_value = _coerce_int(normalized["max_results"])
        if int_value is not None:
            normalized["max_results"] = max(1, min(10000, int_value))

    # 4. Clamp context_lines to valid range [0, 100]
    # This is a complex transformation that cannot be expressed as arg_aliases
    if "context_lines" in normalized:
        int_value = _coerce_int(normalized["context_lines"])
        if int_value is not None:
            normalized["context_lines"] = max(0, min(100, int_value))

    # 5. Set defaults
    normalized.setdefault("max_results", 50)
    normalized.setdefault("context_lines", 0)

    return normalized
