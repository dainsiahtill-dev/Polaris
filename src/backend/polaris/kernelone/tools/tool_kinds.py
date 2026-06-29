"""Canonical tool-kind facts shared across execution and control-plane code."""

from __future__ import annotations

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "precision_edit",
        "edit_blocks",
        "search_replace",
        "edit_file",
        "repo_apply_diff",
        "append_to_file",
        "write_file",
        "create_file",
        "delete_file",
        "rename_file",
        "apply_patch",
        "patch_apply",
        "apply_diff",
    }
)


def normalize_tool_name(value: object) -> str:
    """Normalize a provider/runtime tool name for kind checks."""

    return str(value or "").strip().lower().replace("-", "_")


def is_write_tool_name(value: object) -> bool:
    """Return whether a normalized tool name is a materializing write tool."""

    return normalize_tool_name(value) in WRITE_TOOLS


__all__ = ["WRITE_TOOLS", "is_write_tool_name", "normalize_tool_name"]
