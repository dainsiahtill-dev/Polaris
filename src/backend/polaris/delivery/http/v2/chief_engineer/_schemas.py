"""Inline Pydantic request/response models for the Chief Engineer v2 router.

Lossless extraction of the ~25 ``BaseModel`` subclasses that lived at module
scope in the former single-file ``chief_engineer`` module. They carry no
behaviour and have no test-patchable external dependency, so a plain module
is safe.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
)
from pydantic import BaseModel, Field


class ChiefEngineerBlueprintSummary(ChiefEngineerBlueprintSummaryV1):
    """Chief Engineer blueprint summary bound to the shared contract."""


class ChiefEngineerBlueprintListResponse(ChiefEngineerBlueprintListV1):
    """Chief Engineer blueprint list response bound to the shared contract."""


class ChiefEngineerBlueprintDetailResponse(ChiefEngineerBlueprintDetailV1):
    """Chief Engineer blueprint detail response bound to the shared contract."""


class ChiefEngineerGenerateBlueprintRequest(BaseModel):
    """Desktop request for generating a task-level Chief Engineer blueprint."""

    task_id: str
    objective: str
    run_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkBlueprintTaskRequest(BaseModel):
    """One task entry in a Chief Engineer bulk blueprint generation request."""

    task_id: str
    objective: str
    run_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkGenerateBlueprintRequest(BaseModel):
    """Bulk request for covering multiple PM/Director tasks with CE blueprints."""

    tasks: list[ChiefEngineerBulkBlueprintTaskRequest] = Field(default_factory=list)
    stop_on_error: bool = False


class ChiefEngineerTaskBlueprintResultResponse(BaseModel):
    """Chief Engineer command/query result with persisted blueprint context."""

    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    source: str = "runtime/blueprints"
    summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    blueprint: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBulkBlueprintError(BaseModel):
    """Per-task failure captured during bulk blueprint generation."""

    task_id: str
    code: str
    message: str


class ChiefEngineerBulkGenerateBlueprintResponse(BaseModel):
    """Bulk blueprint generation evidence for the Chief Engineer desktop."""

    ok: bool
    workspace: str
    total: int
    generated: int
    failed: int
    results: list[ChiefEngineerTaskBlueprintResultResponse] = Field(default_factory=list)
    errors: list[ChiefEngineerBulkBlueprintError] = Field(default_factory=list)


class ChiefEngineerBlueprintDeleteResponse(BaseModel):
    """Deletion evidence for a persisted Chief Engineer blueprint."""

    ok: bool
    blueprint_id: str
    deleted: bool
    source: str = "runtime/blueprints"


class ChiefEngineerDiagnosticsWorkspaceStatus(BaseModel):
    """Workspace readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    workspace: str
    exists: bool
    error: str | None = None


class ChiefEngineerDiagnosticsLLMStatus(BaseModel):
    """Chief Engineer role-specific LLM readiness section."""

    ok: bool
    state: str
    role: str = "chief_engineer"
    blocked_roles: list[str] = Field(default_factory=list)
    unsupported_roles: list[str] = Field(default_factory=list)
    required_ready_roles: list[str] = Field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerDiagnosticsBlueprintStatus(BaseModel):
    """Blueprint store readiness section for Chief Engineer desktop diagnostics."""

    ok: bool
    status: str
    source: str = "runtime/blueprints"
    plan_status: str = "unknown"
    plan_path: str | None = None
    plan_error: str | None = None
    total: int = 0
    loadable: int = 0
    invalid_payloads: int = 0
    planned_tasks: int = 0
    covered_tasks: int = 0
    missing_task_ids: list[str] = Field(default_factory=list)
    director_handoff_ready: bool = False
    latest_updated_at: str | None = None
    error: str | None = None


class ChiefEngineerPMTaskPlanProbe(BaseModel):
    """Read-only PM task plan evidence for Chief Engineer handoff diagnostics."""

    status: str
    path: str | None = None
    task_ids: list[str] | None = None
    error: str | None = None


class ChiefEngineerDiagnosticsResponse(BaseModel):
    """Side-effect-free Chief Engineer desktop readiness snapshot."""

    ok: bool
    can_handoff: bool
    role: str = "chief_engineer"
    generated_at: str
    workspace: ChiefEngineerDiagnosticsWorkspaceStatus
    llm: ChiefEngineerDiagnosticsLLMStatus
    blueprints: ChiefEngineerDiagnosticsBlueprintStatus
    can_generate: bool
    issues: list[str] = Field(default_factory=list)
    generate_blockers: list[str] = Field(default_factory=list)
    handoff_blockers: list[str] = Field(default_factory=list)


class ChiefEngineerRegisterRiskRequest(BaseModel):
    """Desktop request to register a Risk Register entry."""

    task_id: str
    title: str
    severity: str
    owner: str
    mitigation: str = ""
    links: list[str] = Field(default_factory=list)
    supersedes: str | None = None


class ChiefEngineerUpdateRiskStatusRequest(BaseModel):
    """Desktop request to transition a risk to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerRegisterTechDebtRequest(BaseModel):
    """Desktop request to register a Tech-Debt Ledger entry."""

    title: str
    description: str = ""
    severity: str
    surface: str
    owner: str
    evidence: list[str] = Field(default_factory=list)


class ChiefEngineerUpdateTechDebtStatusRequest(BaseModel):
    """Desktop request to transition a tech-debt entry to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerRegisterADRRequest(BaseModel):
    """Desktop request to record an Architecture Decision Record."""

    title: str
    decision: str
    owner: str
    context: str = ""
    consequences: str = ""
    alternatives: list[str] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = None


class ChiefEngineerUpdateADRStatusRequest(BaseModel):
    """Desktop request to transition an ADR to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerRegisterTechRadarRequest(BaseModel):
    """Desktop request to place a library on a Tech-Radar ring."""

    library: str
    ring: str
    owner: str
    rationale: str = ""
    supersedes: str | None = None


class ChiefEngineerUpdateTechRadarRingRequest(BaseModel):
    """Desktop request to move a Tech-Radar entry to a new ring."""

    ring: str
    note: str = ""
    actor: str = "chief_engineer"


class ChiefEngineerStackPolicyCheckRequest(BaseModel):
    """Desktop request to check libraries against the Tech Radar."""

    libraries: list[str] = Field(default_factory=list)


class ChiefEngineerRegisterPostMortemRequest(BaseModel):
    """Desktop request to record a post-mortem / incident review."""

    title: str
    severity: str
    occurred_at: str
    owner: str
    summary: str = ""
    root_cause: str = ""
    impact: str = ""
    timeline: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    related_risk_ids: list[str] = Field(default_factory=list)


class ChiefEngineerUpdatePostMortemStatusRequest(BaseModel):
    """Desktop request to transition a post-mortem to a new status."""

    status: str
    note: str = ""
    actor: str = "chief_engineer"


__all__ = [
    "ChiefEngineerBlueprintDeleteResponse",
    "ChiefEngineerBlueprintDetailResponse",
    "ChiefEngineerBlueprintListResponse",
    "ChiefEngineerBlueprintSummary",
    "ChiefEngineerBulkBlueprintError",
    "ChiefEngineerBulkBlueprintTaskRequest",
    "ChiefEngineerBulkGenerateBlueprintRequest",
    "ChiefEngineerBulkGenerateBlueprintResponse",
    "ChiefEngineerDiagnosticsBlueprintStatus",
    "ChiefEngineerDiagnosticsLLMStatus",
    "ChiefEngineerDiagnosticsResponse",
    "ChiefEngineerDiagnosticsWorkspaceStatus",
    "ChiefEngineerGenerateBlueprintRequest",
    "ChiefEngineerPMTaskPlanProbe",
    "ChiefEngineerRegisterADRRequest",
    "ChiefEngineerRegisterPostMortemRequest",
    "ChiefEngineerRegisterRiskRequest",
    "ChiefEngineerRegisterTechDebtRequest",
    "ChiefEngineerRegisterTechRadarRequest",
    "ChiefEngineerStackPolicyCheckRequest",
    "ChiefEngineerTaskBlueprintResultResponse",
    "ChiefEngineerUpdateADRStatusRequest",
    "ChiefEngineerUpdatePostMortemStatusRequest",
    "ChiefEngineerUpdateRiskStatusRequest",
    "ChiefEngineerUpdateTechDebtStatusRequest",
    "ChiefEngineerUpdateTechRadarRingRequest",
]
