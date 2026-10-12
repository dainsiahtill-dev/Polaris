"""LLM Caller contracts - public boundary for LLM caller tool helpers.

This module exposes tool schema building functions from roles.kernel.internal.llm_caller
for use by other Cells, following the Public/Internal Fence principle.

Public exports:
- resolve_tool_call_provider: resolve tool call format provider hint
- build_native_tool_schemas: build OpenAI-format tool schemas from role profile
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.llm_caller.helpers import _clamp_factory_director_timeout_to_deadline
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_schemas,
    resolve_tool_call_provider,
)
from polaris.kernelone.llm.budget_policy import DEFAULT_ROLE_OUTPUT_TOKENS, resolve_output_budget_tokens

from .generation_outcome import RoleGenerationOutcomeV1, bind_role_generation_outcome

__all__ = [
    "DEFAULT_ROLE_OUTPUT_TOKENS",
    "RoleGenerationOutcomeV1",
    "bind_role_generation_outcome",
    "build_native_tool_schemas",
    "clamp_role_execution_timeout_to_deadline",
    "resolve_role_output_budget",
    "resolve_tool_call_provider",
]


def resolve_role_output_budget(
    *, context: Mapping[str, Any] | None = None, requested: int = DEFAULT_ROLE_OUTPUT_TOKENS
) -> int:
    """Compatibility facade for KernelOne's canonical pure output resolver."""
    return resolve_output_budget_tokens(requested, dict(context) if context is not None else None)


def clamp_role_execution_timeout_to_deadline(*, timeout_seconds: int, context: Mapping[str, Any] | None = None) -> int:
    """Reuse the canonical execution ceiling without exposing kernel internals."""
    return _clamp_factory_director_timeout_to_deadline(
        timeout_seconds,
        dict(context) if context is not None else None,
    )
