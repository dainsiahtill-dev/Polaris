"""Tool Categories SSOT - Single Source of Truth for tool classification.

This module provides canonical tool category definitions derived from _TOOL_SPECS.
It replaces duplicate TOOL_CATEGORIES definitions in:
- polaris/cells/roles/kernel/internal/tool_gateway.py
- polaris/cells/roles/kernel/internal/policy/layer/tool.py

Usage:
    >>> from polaris.kernelone.tool_execution.tool_categories import TOOL_CATEGORIES
    >>> "write_file" in TOOL_CATEGORIES["code_write"]
    True
"""

from __future__ import annotations

from polaris.kernelone.tool_execution.contracts import _TOOL_SPECS


def _build_tool_categories() -> dict[str, frozenset[str]]:
    """Build tool categories from _TOOL_SPECS dynamically.

    Maps spec.category values to canonical category keys:
      - "write" → "code_write"
      - "exec"  → "command_execution"
      - "read"  → "read_only"
      - "delete"→ "file_delete"

    Returns:
        Dict mapping category name to frozenset of canonical tool names.
    """
    cats: dict[str, set[str]] = {
        "code_write": set(),
        "command_execution": set(),
        "file_delete": set(),
        "read_only": set(),
    }

    specs = _TOOL_SPECS
    if hasattr(specs, "_data"):
        specs = specs._data

    for name, spec in specs.items():
        cat = spec.get("category", "read")
        cat_key: str
        if cat == "write":
            cat_key = "code_write"
        elif cat == "exec":
            cat_key = "command_execution"
        elif cat == "delete":
            cat_key = "file_delete"
        else:
            cat_key = "read_only"
        cats[cat_key].add(name)

    return {k: frozenset(v) for k, v in cats.items()}


TOOL_CATEGORIES: dict[str, frozenset[str]] = _build_tool_categories()

# Convenience lookups
CODE_WRITE_TOOLS = TOOL_CATEGORIES["code_write"]
COMMAND_EXECUTION_TOOLS = TOOL_CATEGORIES["command_execution"]
FILE_DELETE_TOOLS = TOOL_CATEGORIES["file_delete"]
READ_ONLY_TOOLS = TOOL_CATEGORIES["read_only"]


def is_code_write_tool(name: str) -> bool:
    """Check if tool is a code write tool."""
    return name in CODE_WRITE_TOOLS


def is_command_execution_tool(name: str) -> bool:
    """Check if tool is a command execution tool."""
    return name in COMMAND_EXECUTION_TOOLS


def is_file_delete_tool(name: str) -> bool:
    """Check if tool is a file delete tool."""
    return name in FILE_DELETE_TOOLS


def is_read_only_tool(name: str) -> bool:
    """Check if tool is a read-only tool."""
    return name in READ_ONLY_TOOLS
