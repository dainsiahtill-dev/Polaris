"""Normalizer for file path tools (write_file, edit_file, append_to_file, etc.).

Refactored (2026-04-05): Path aliases are now handled by SchemaDrivenNormalizer
via contracts.py arg_aliases. This normalizer is kept minimal.

This normalizer handles:
- Workspace path normalization for existing 'file' key
- (Path alias extraction is handled by SchemaDrivenNormalizer)
"""

from __future__ import annotations

from typing import Any

from ._shared import _normalize_workspace_alias_path, recover_write_body_string


def normalize_file_path_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize common file path arguments for file operations.

    Path aliases (path/filepath/file_path/target -> file) are handled by
    SchemaDrivenNormalizer via contracts.py arg_aliases.

    This function normalizes the existing ``file`` path value and recovers
    structured ``content`` bodies (R138 ``$text`` / continuation maps) into
    plain strings so DEO write authorization can proceed.
    """
    normalized = dict(tool_args)

    # Normalize existing 'file' path (workspace alias resolution)
    file_candidate = normalized.get("file")
    if isinstance(file_candidate, str) and file_candidate.strip():
        normalized["file"] = _normalize_workspace_alias_path(file_candidate.strip())

    # R138: recover structured content maps before DEO freezes arguments.
    if "content" in normalized and not isinstance(normalized.get("content"), str):
        recovered = recover_write_body_string(normalized.get("content"))
        if recovered is not None:
            normalized["content"] = recovered

    return normalized
