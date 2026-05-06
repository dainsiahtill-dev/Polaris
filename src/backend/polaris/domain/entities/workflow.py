"""Shared workflow entity models.

This module provides canonical workflow-domain types that are consumed by:
- workflow_activity Cell
- workflow_runtime Cell

Extracted from the duplicate model definitions in:
- polaris.cells.orchestration.workflow_activity.internal.models
- polaris.cells.orchestration.workflow_runtime.internal.models

Migration notes (2026-04-21):
- PMWorkflowResult, DirectorWorkflowResult unified here
- _coerce_positive_int, _coerce_execution_mode unified here
- TaskContract remains in each Cell's models to avoid circular deps
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionMode(str, Enum):
    """Workflow execution modes."""

    SEQUENTIAL = "sequential"
    SERIAL = "serial"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class PMWorkflowResult:
    """Result produced by the top-level PM workflow.

    Attributes:
        run_id: Unique identifier for this workflow run.
        tasks: List of task contracts managed by the PM.
        director_status: Current status of the Director sub-workflow.
        qa_status: Current status of the QA sub-workflow.
        metadata: Additional workflow metadata.
    """

    run_id: str
    tasks: list[Any]
    director_status: str
    qa_status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectorWorkflowResult:
    """Aggregated Director workflow result.

    Attributes:
        run_id: Unique identifier for this workflow run.
        status: Overall execution status.
        completed_tasks: Number of successfully completed tasks.
        failed_tasks: Number of failed tasks.
        metadata: Additional workflow metadata.
    """

    run_id: str
    status: str
    completed_tasks: int
    failed_tasks: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _coerce_positive_int(value: Any, default: int) -> int:
    """Coerce a value to a positive integer.

    Args:
        value: The value to coerce.
        default: Default value if coercion fails.

    Returns:
        The coerced positive integer, at minimum 1.
    """
    if value is None:
        return max(1, int(default))
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def _coerce_execution_mode(value: Any, default: str = "parallel") -> str:
    """Coerce a value to a valid execution mode.

    Args:
        value: The value to coerce.
        default: Default mode if coercion fails.

    Returns:
        Either "serial", "sequential", or "parallel".
    """
    token = str(value or "").strip().lower()
    if token in {"serial", "sequential", "parallel"}:
        return token
    return default
