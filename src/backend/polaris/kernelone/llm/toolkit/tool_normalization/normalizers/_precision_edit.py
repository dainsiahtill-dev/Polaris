"""Normalizer for precision_edit tool.

Refactored (2026-04-05): Simplified to only handle special transformations.
Path and parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

This normalizer handles:
- Workspace path normalization for existing 'file' key
- (All parameter aliases are handled by SchemaDrivenNormalizer)
"""

from __future__ import annotations

from typing import Any

from ._shared import _normalize_workspace_alias_path


def normalize_precision_edit_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize precision_edit arguments.

    Path aliases (path/filepath/file_path/target -> file) are handled by
    SchemaDrivenNormalizer via contracts.py arg_aliases.

    This function only normalizes the existing 'file' path value.
    """
    normalized = dict(tool_args)

    # Normalize existing 'file' path (workspace alias resolution)
    file_candidate = normalized.get("file")
    if isinstance(file_candidate, str) and file_candidate.strip():
        normalized["file"] = _normalize_workspace_alias_path(file_candidate.strip())

    return normalized
