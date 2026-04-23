"""LLM Caller contracts - public boundary for LLM caller tool helpers.

This module exposes tool schema building functions from roles.kernel.internal.llm_caller
for use by other Cells, following the Public/Internal Fence principle.

Public exports:
- resolve_tool_call_provider: resolve tool call format provider hint
- build_native_tool_schemas: build OpenAI-format tool schemas from role profile
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_schemas,
    resolve_tool_call_provider,
)

__all__ = [
    "build_native_tool_schemas",
    "resolve_tool_call_provider",
]
