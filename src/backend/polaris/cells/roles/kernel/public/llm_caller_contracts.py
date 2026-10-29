"""LLM Caller contracts - public boundary for LLM caller tool helpers.

This module exposes tool schema building functions from roles.kernel.internal.llm_caller
for use by other Cells, following the Public/Internal Fence principle.

Public exports:
- resolve_tool_call_provider: resolve tool call format provider hint
- build_native_tool_schemas: build OpenAI-format tool schemas from role profile
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, cast

from polaris.cells.roles.kernel.internal.llm_caller.helpers import (
    _clamp_factory_director_timeout_to_deadline,
    _factory_execution_deadline_epochs,
    _select_viable_factory_deadline_epoch,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_schemas,
    director_uses_legacy_tool_slices,
    prepare_role_session_continuation_policy,
    resolve_tool_call_provider,
    stamp_director_quality_repair_one_call_policy,
)
from polaris.kernelone.llm.budget_policy import DEFAULT_ROLE_OUTPUT_TOKENS, resolve_output_budget_tokens

from .generation_outcome import RoleGenerationOutcomeV1, bind_role_generation_outcome

__all__ = [
    "DEFAULT_ROLE_OUTPUT_TOKENS",
    "RoleGenerationOutcomeV1",
    "bind_role_generation_outcome",
    "build_native_tool_schemas",
    "clamp_role_execution_timeout_seconds_to_deadline",
    "clamp_role_execution_timeout_to_deadline",
    "director_uses_legacy_tool_slices",
    "prepare_role_session_continuation_policy",
    "resolve_role_output_budget",
    "resolve_tool_call_provider",
    "stamp_director_quality_repair_one_call_policy",
]


def resolve_role_output_budget(
    *, context: Mapping[str, Any] | None = None, requested: int = DEFAULT_ROLE_OUTPUT_TOKENS
) -> int:
    """Compatibility facade for KernelOne's canonical pure output resolver."""
    result: int = resolve_output_budget_tokens(requested, dict(context) if context is not None else None)
    return result


def clamp_role_execution_timeout_to_deadline(*, timeout_seconds: int, context: Mapping[str, Any] | None = None) -> int:
    """Reuse the canonical execution ceiling without exposing kernel internals."""
    result: int = _clamp_factory_director_timeout_to_deadline(
        timeout_seconds,
        dict(context) if context is not None else None,
    )
    return result


def clamp_role_execution_timeout_seconds_to_deadline(
    *, timeout_seconds: float, context: Mapping[str, Any] | None = None
) -> float:
    """Apply the live execution deadline without rounding or extending its lease."""
    resolved_timeout = max(0.1, float(timeout_seconds))
    context_override = context if isinstance(context, dict) else dict(context) if isinstance(context, Mapping) else None
    if context_override is None:
        return resolved_timeout
    candidates = _factory_execution_deadline_epochs(context_override)
    if not candidates:
        return resolved_timeout
    now = time.time()
    selected = _select_viable_factory_deadline_epoch(
        context_override,
        now=now,
        minimum_remaining_seconds=0.0,
    )
    if selected is None:
        raise RuntimeError("factory_director_execution_deadline_exhausted")
    _source, deadline_epoch = selected
    remaining_seconds = deadline_epoch - now
    if remaining_seconds <= 0:
        raise RuntimeError("factory_director_execution_deadline_exhausted")
    return cast("float", (min(resolved_timeout, remaining_seconds)))
