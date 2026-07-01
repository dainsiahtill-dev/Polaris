"""Public execution-guidance surface for Director tasking.

This module exposes the stable profile, strategy, and language-guidance
operations that other Cells need during Director dispatch. The implementation
delegates to ``director.tasking.internal`` because the tasking Cell owns that
logic, but consumers must depend on this public boundary rather than importing
internal modules directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.director.tasking.internal.execution_profile import (
    resolve_director_execution_profile as _resolve_director_execution_profile,
)
from polaris.cells.director.tasking.internal.execution_strategy import (
    apply_execution_strategy_overrides as _apply_execution_strategy_overrides,
    resolve_director_execution_strategy as _resolve_director_execution_strategy,
)
from polaris.cells.director.tasking.internal.language_guidance import (
    build_language_section as _build_language_section,
)
from polaris.cells.director.tasking.public.contracts import (
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)


def coerce_task_execution_profile(payload: Mapping[str, Any]) -> TaskExecutionProfileV1:
    """Return a profile contract from a JSON-safe profile payload.

    Unknown keys are ignored so persisted runtime metadata can carry extra
    audit fields without breaking profile hydration. The dataclass constructor
    still validates required field normalization and value bounds.
    """

    profile_fields = {
        key: payload[key] for key in TaskExecutionProfileV1.__dataclass_fields__ if key in payload
    }
    return TaskExecutionProfileV1(**profile_fields)


def resolve_task_execution_profile(
    *,
    subject: str,
    description: str = "",
    metadata: Mapping[str, Any] | None = None,
    target_files: Sequence[str] | None = None,
    scope_paths: Sequence[str] | None = None,
    workspace: str = "",
) -> TaskExecutionProfileV1:
    """Resolve the canonical Director task execution profile."""

    return _resolve_director_execution_profile(
        subject=subject,
        description=description,
        metadata=dict(metadata or {}),
        target_files=tuple(target_files or ()),
        scope_paths=tuple(scope_paths or ()),
        workspace=workspace,
    )


def resolve_task_execution_strategy(
    profile: TaskExecutionProfileV1,
    *,
    metadata: Mapping[str, Any] | None = None,
    model_window_tokens: int | None = None,
) -> TaskExecutionStrategyV1:
    """Resolve runtime budgets and audit requirements for a task profile."""

    return _resolve_director_execution_strategy(
        profile,
        metadata=dict(metadata or {}),
        model_window_tokens=model_window_tokens,
    )


def apply_task_execution_strategy_overrides(
    *,
    context: dict[str, Any],
    metadata: dict[str, Any],
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
) -> None:
    """Apply strategy controls to trusted runtime context and metadata."""

    _apply_execution_strategy_overrides(
        context=context,
        metadata=metadata,
        profile=profile,
        strategy=strategy,
    )


def build_task_language_section(
    target_files: Sequence[str],
    workspace: str | Path = "",
    *,
    metadata: Mapping[str, Any] | None = None,
    subject: str = "",
    description: str = "",
    scope_paths: Sequence[str] | None = None,
) -> tuple[str, str]:
    """Build the Director language identity and prompt guidance section."""

    return _build_language_section(
        list(target_files),
        workspace,
        metadata=dict(metadata or {}),
        subject=subject,
        description=description,
        scope_paths=tuple(scope_paths or ()),
    )


__all__ = [
    "apply_task_execution_strategy_overrides",
    "build_task_language_section",
    "coerce_task_execution_profile",
    "resolve_task_execution_profile",
    "resolve_task_execution_strategy",
]
