"""Pydantic v2 DTOs for PM orchestration layer.

Replaces raw ``dict[str, Any]`` returns with validated, typed models.
All external data crossing the PM boundary MUST use these schemas.

Schema hierarchy:
    PmIterationResult      — Single iteration outcome
    PmIterationContext     — Iteration context
    PmDispatchResult       — Dispatch pipeline result
    PmBlockedPolicyResult  — Blocked policy evaluation result
    PmPlanningResult       — Planning result
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Planning DTOs
# ---------------------------------------------------------------------------


class PmPlanningResult(BaseModel):
    """Result of PM planning iteration."""

    model_config = {"frozen": True}

    exit_code: int = Field(..., description="Planning exit code")
    normalized: dict[str, Any] = Field(
        default_factory=dict, description="Normalized planning payload"
    )
    task_count: int = Field(default=0, description="Number of tasks generated")
    notes: str = Field(default="", description="Planning notes")


# ---------------------------------------------------------------------------
# Dispatch DTOs
# ---------------------------------------------------------------------------


class PmDispatchResult(BaseModel):
    """Result of PM dispatch pipeline."""

    model_config = {"frozen": True}

    used: bool = Field(default=False, description="Whether dispatch was used")
    exit_code: int = Field(default=0, description="Dispatch exit code")
    chief_engineer_result: dict[str, Any] | None = Field(
        default=None, description="Chief Engineer result"
    )
    engine_dispatch: dict[str, Any] | None = Field(
        default=None, description="Engine dispatch result"
    )
    integration_qa_result: dict[str, Any] | None = Field(
        default=None, description="Integration QA result"
    )
    director_result: dict[str, Any] | None = Field(
        default=None, description="Director result"
    )
    error: str = Field(default="", description="Error message if failed")


# ---------------------------------------------------------------------------
# Blocked Policy DTOs
# ---------------------------------------------------------------------------


class PmBlockedPolicyResult(BaseModel):
    """Result of blocked policy evaluation."""

    model_config = {"frozen": True}

    decision: Literal["continue", "manual_stop", "degrade_and_continue", "skip_and_continue"] = Field(
        ..., description="Policy decision"
    )
    exit_code: int = Field(..., description="Exit code")
    pm_state_patch: dict[str, Any] = Field(
        default_factory=dict, description="PM state patches"
    )
    audit_payload: dict[str, Any] = Field(
        default_factory=dict, description="Audit payload"
    )
    strategy: str = Field(default="", description="Strategy used")
    reason: str = Field(default="", description="Decision reason")
    task_status_update: dict[str, Any] | None = Field(
        default=None, description="Task status update"
    )


# ---------------------------------------------------------------------------
# Iteration Context DTOs
# ---------------------------------------------------------------------------


class PmIterationContext(BaseModel):
    """Lightweight context required to drive a PM iteration."""

    model_config = {"populate_by_name": True}

    workspace: str = Field(..., description="Workspace path")
    iteration: int = Field(default=1, description="Iteration number")
    run_id: str = Field(default="", description="Run identifier")
    planning_context: dict[str, Any] = Field(
        default_factory=dict, description="Planning context"
    )
    dispatch_enabled: bool = Field(
        default=True, description="Whether dispatch is enabled"
    )


# ---------------------------------------------------------------------------
# Iteration Result DTOs
# ---------------------------------------------------------------------------


class PmIterationResult(BaseModel):
    """Immutable snapshot of a single PM iteration outcome."""

    model_config = {"frozen": True}

    exit_code: int = Field(..., description="Iteration exit code")
    run_id: str = Field(..., description="Run identifier")
    iteration: int = Field(..., description="Iteration number")
    task_count: int = Field(default=0, description="Tasks in this iteration")
    status: Literal["completed", "failed", "blocked"] = Field(
        ..., description="Iteration status"
    )
    chief_engineer_result: dict[str, Any] | None = Field(
        default=None, description="Chief Engineer result"
    )
    engine_dispatch: dict[str, Any] | None = Field(
        default=None, description="Engine dispatch result"
    )
    integration_qa_result: dict[str, Any] | None = Field(
        default=None, description="Integration QA result"
    )
    director_result: dict[str, Any] | None = Field(
        default=None, description="Director result"
    )
    blocked_policy_result: PmBlockedPolicyResult | None = Field(
        default=None, description="Blocked policy result"
    )
    schema_warnings: list[str] = Field(
        default_factory=list, description="Schema warnings"
    )
    notes: str = Field(default="", description="Iteration notes")


__all__ = [
    "PmBlockedPolicyResult",
    "PmDispatchResult",
    "PmIterationContext",
    "PmIterationResult",
    "PmPlanningResult",
]
