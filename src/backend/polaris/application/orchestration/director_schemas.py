"""Pydantic v2 DTOs for Director orchestration layer.

Replaces raw ``dict[str, Any]`` returns with validated, typed models.
All external data crossing the Director boundary MUST use these schemas.

Schema hierarchy:
    DirectorTaskSchema      — Single task I/O
    DirectorIterationSchema — Full iteration result
    DirectorSubmitSchema    — Task submission response
    DirectorWorkflowSchema  — Workflow submission/wait result
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Task DTOs
# ---------------------------------------------------------------------------


class DirectorTaskSchema(BaseModel):
    """Validated task representation used across Director boundary.

    Replaces ``dict[str, Any]`` task dicts with typed, validated fields.
    Input data (e.g. from TaskBoard) passes through ``model_validate()``
    which coerces and strips unknown fields.
    """

    model_config = {"strict": False, "populate_by_name": True}

    id: str = Field(..., description="Task identifier")
    subject: str = Field(..., description="Task title / subject line")
    description: str = Field(default="", description="Task description or goal")
    status: str = Field(default="pending", description="Task lifecycle status")
    priority: str = Field(default="medium", description="Task priority level")
    owner: str = Field(default="", description="Task owner")
    assignee: str = Field(default="", description="Task assignee")
    role: str = Field(default="", description="PM role assignment")
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Arbitrary task metadata (no nested Any)",
    )


class DirectorTaskResultSchema(BaseModel):
    """Immutable result snapshot for a single Director task execution."""

    model_config = {"frozen": True}

    task_id: str = Field(..., description="Executed task identifier")
    subject: str = Field(..., description="Task subject line")
    success: bool = Field(..., description="Whether execution succeeded")
    status: Literal["completed", "failed", "skipped"] = Field(..., description="Terminal status")
    response_length: int = Field(default=0, description="Response content length")
    error: str = Field(default="", description="Error message if failed")
    metadata: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict,
        description="Execution metadata (changed_files, adapter info, etc.)",
    )


# ---------------------------------------------------------------------------
# Iteration DTOs
# ---------------------------------------------------------------------------


class DirectorIterationSchema(BaseModel):
    """Result snapshot for a full Director iteration."""

    model_config = {"frozen": True}

    success: bool = Field(..., description="Whether iteration completed")
    iteration: int = Field(..., description="Iteration number")
    tasks_processed: int = Field(default=0, description="Tasks in this batch")
    tasks_succeeded: int = Field(default=0, description="Succeeded tasks")
    tasks_failed: int = Field(default=0, description="Failed tasks")
    results: list[DirectorTaskResultSchema] = Field(
        default_factory=list,
        description="Per-task results",
    )
    notes: str = Field(default="", description="Iteration-level notes")


# ---------------------------------------------------------------------------
# Submission DTOs
# ---------------------------------------------------------------------------


class DirectorSubmitResponse(BaseModel):
    """Response for task submission."""

    model_config = {"frozen": True}

    id: str = Field(..., description="Submitted task ID")
    subject: str = Field(..., description="Task subject")
    status: Literal["submitted"] = Field(default="submitted", description="Submission status")


class DirectorWorkflowSubmission(BaseModel):
    """Response for workflow submission."""

    model_config = {"frozen": True}

    submitted: bool = Field(..., description="Whether submission succeeded")
    status: str = Field(default="", description="Workflow status")
    workflow_id: str = Field(default="", description="Workflow identifier")
    workflow_run_id: str = Field(default="", description="Workflow run identifier")
    error: str = Field(default="", description="Error message if failed")


class DirectorWorkflowWaitResult(BaseModel):
    """Response for workflow wait."""

    model_config = {"frozen": True}

    status: str = Field(default="", description="Terminal workflow status")
    error: str = Field(default="", description="Error message if failed")


# ---------------------------------------------------------------------------
# Adapter input DTO
# ---------------------------------------------------------------------------


class DirectorAdapterInput(BaseModel):
    """Structured input for the Director role adapter.

    Replaces the raw ``dict`` built by ``_build_adapter_input``.
    """

    model_config = {"populate_by_name": True}

    task_id: str = Field(..., description="Task identifier")
    pm_task_id: str = Field(..., description="PM task identifier")
    id: str = Field(..., description="Alias for task_id")
    subject: str = Field(..., alias="title", description="Task subject")
    title: str = Field(default="", description="Task title (alias)")
    goal: str = Field(default="", description="Task goal / description")
    description: str = Field(default="", description="Task description")
    input: str = Field(default="", description="Raw input text")
    task: DirectorTaskSchema = Field(..., description="Full task schema")
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Adapter metadata",
    )


__all__ = [
    "DirectorAdapterInput",
    "DirectorIterationSchema",
    "DirectorSubmitResponse",
    "DirectorTaskResultSchema",
    "DirectorTaskSchema",
    "DirectorWorkflowSubmission",
    "DirectorWorkflowWaitResult",
]
