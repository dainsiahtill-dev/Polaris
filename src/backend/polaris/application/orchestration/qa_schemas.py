"""Pydantic v2 DTOs for QA orchestration layer.

Replaces raw ``dict[str, Any]`` returns with validated, typed models.
All external data crossing the QA boundary MUST use these schemas.

Schema hierarchy:
    QaReviewResult         — Single review outcome
    QaVerdictResult        — Compiled verdict
    QaAuditLifecycleResult — Full lifecycle result
    QaAuditConfig          — Configuration
    QaAuditPlan            — Audit plan
    QaVerdictQuery         — Verdict query result
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Review DTOs
# ---------------------------------------------------------------------------


class QaReviewResult(BaseModel):
    """Immutable snapshot of a single QA review outcome."""

    model_config = {"frozen": True}

    review_id: str = Field(..., description="Review identifier")
    target: str = Field(..., description="Review target")
    status: Literal["completed", "failed", "skipped"] = Field(..., description="Review status")
    issue_count: int = Field(default=0, description="Number of issues found")
    findings: list[str] = Field(default_factory=list, description="Review findings")
    error: str = Field(default="", description="Error message if failed")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Review metadata")


# ---------------------------------------------------------------------------
# Verdict DTOs
# ---------------------------------------------------------------------------


class QaVerdictResult(BaseModel):
    """Immutable snapshot of a compiled QA verdict."""

    model_config = {"frozen": True}

    verdict: Literal["PASS", "FAIL", "NEEDS_REVIEW"] = Field(..., description="Final verdict")
    verdict_id: str = Field(..., description="Verdict identifier")
    summary: str = Field(..., description="Verdict summary")
    findings: list[str] = Field(default_factory=list, description="Verdict findings")
    suggestions: list[str] = Field(default_factory=list, description="Improvement suggestions")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Quality score")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Verdict metadata")


# ---------------------------------------------------------------------------
# Lifecycle DTOs
# ---------------------------------------------------------------------------


class QaAuditLifecycleResult(BaseModel):
    """Immutable snapshot of a full QA audit lifecycle outcome."""

    model_config = {"frozen": True}

    success: bool = Field(..., description="Whether audit passed")
    task_id: str = Field(..., description="Audited task identifier")
    workspace: str = Field(..., description="Workspace path")
    review: QaReviewResult | None = Field(default=None, description="Review result")
    verdict: QaVerdictResult | None = Field(default=None, description="Verdict result")
    notes: str = Field(default="", description="Lifecycle notes")


# ---------------------------------------------------------------------------
# Config DTOs
# ---------------------------------------------------------------------------


class QaAuditConfig(BaseModel):
    """Configuration for QA audit execution."""

    model_config = {"populate_by_name": True}

    workspace: str = Field(..., description="Workspace path")
    run_id: str = Field(default="", description="Run identifier")
    criteria: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Audit criteria")
    evidence_paths: list[str] = Field(default_factory=list, description="Evidence file paths")
    auto_audit: bool = Field(default=True, description="Enable auto audit")
    min_coverage: float = Field(default=0.7, ge=0.0, le=1.0, description="Minimum test coverage")


# ---------------------------------------------------------------------------
# Audit Plan DTO
# ---------------------------------------------------------------------------


class QaAuditPlan(BaseModel):
    """Audit plan for a specific task."""

    model_config = {"frozen": True}

    task_id: str = Field(..., description="Task identifier")
    workspace: str = Field(..., description="Workspace path")
    run_id: str = Field(default="", description="Run identifier")
    criteria: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Merged criteria")
    evidence_paths: list[str] = Field(default_factory=list, description="Evidence file paths")


# ---------------------------------------------------------------------------
# Verdict Query DTO
# ---------------------------------------------------------------------------


class QaVerdictQuery(BaseModel):
    """Result of querying verdict state."""

    model_config = {"frozen": True}

    ok: bool = Field(..., description="Whether query succeeded")
    status: str = Field(..., description="Verdict status")
    verdict: str | None = Field(default=None, description="Current verdict")
    details: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Verdict details")
    error_code: str | None = Field(default=None, description="Error code")
    error_message: str | None = Field(default=None, description="Error message")


__all__ = [
    "QaAuditConfig",
    "QaAuditLifecycleResult",
    "QaAuditPlan",
    "QaReviewResult",
    "QaVerdictQuery",
    "QaVerdictResult",
]
