"""Declarative API contract models for the Director v2 router.

This module holds ONLY the pure Pydantic request/response and diagnostics
models for ``polaris.delivery.http.v2.director``. It carries no route
handlers, dependencies, or helper functions and triggers no import-time
side effects beyond Pydantic model class construction.

The canonical router module (``director``) re-exports every symbol defined
here so that ``director.<ModelName>`` continues to resolve for callers and
tests.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.projection.public.role_contracts import RoleTaskContractV1
from polaris.kernelone.constants import (
    DEFAULT_DIRECTOR_MAX_PARALLELISM,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)
from pydantic import BaseModel, Field

# ============================================================================
# Phase 6: 统一编排请求模型
# ============================================================================


class DirectorRunOrchestrationRequest(BaseModel):
    """Director 运行编排请求"""

    workspace: str = Field(default=".", description="工作区路径")
    task_filter: str | None = Field(default=None, description="任务过滤条件")
    task_id: str | None = Field(default=None, description="指定单个任务 ID")
    max_workers: int = Field(default=DEFAULT_DIRECTOR_MAX_PARALLELISM, description="最大并行工作者数")
    execution_mode: str = Field(default="parallel", description="执行模式: serial, parallel")


class DirectorOrchestrationResponse(BaseModel):
    """Director 编排响应"""

    run_id: str
    status: str
    workspace: str
    tasks_queued: int
    message: str


class DirectorIntegrationQaRequest(BaseModel):
    """Run post-Director integration QA for a workspace."""

    workspace: str = Field(default="", description="工作区路径；为空时使用当前活动工作区")
    run_id: str | None = Field(default=None, description="可选运行 ID；为空时生成 QA 运行 ID")
    iteration: int = Field(default=0, ge=0, description="PM/QA 迭代号")


class DirectorIntegrationQaResponse(BaseModel):
    ok: bool
    workspace: str
    run_id: str
    director_result: dict[str, Any] | None = None
    result: dict[str, Any]


# ============================================================================
# 原有请求/响应模型
# ============================================================================


class TaskCreateRequest(BaseModel):
    subject: str
    description: str = ""
    command: str | None = None
    priority: str = "MEDIUM"
    blocked_by: list[str] = Field(default_factory=list)
    timeout_seconds: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(RoleTaskContractV1):
    """Director task response bound to the shared role task contract."""


class DirectorStatusResponse(BaseModel):
    state: str
    workspace: str
    metrics: dict[str, Any]
    tasks: dict[str, Any]
    workers: dict[str, Any]
    token_budget: dict[str, Any]


class DirectorDiagnosticsStatusSection(BaseModel):
    """Runtime status evidence for Director desktop readiness."""

    ok: bool
    state: str
    running: bool
    source: str = "none"
    projection_source: str = "none"
    error: str | None = None


class DirectorDiagnosticsTaskSection(BaseModel):
    """Task queue evidence for Director handoff readiness."""

    ok: bool
    source: str
    total: int = 0
    pending: int = 0
    claimed: int = 0
    running: int = 0
    blocked: int = 0
    failed: int = 0
    completed: int = 0
    cancelled: int = 0
    ready_to_execute: int = 0
    ready_task_ids: list[str] = Field(default_factory=list)
    blueprint_ready_task_ids: list[str] = Field(default_factory=list)
    missing_blueprint_task_ids: list[str] = Field(default_factory=list)
    invalid_blueprint_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    running_task_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class DirectorDiagnosticsWorkerSection(BaseModel):
    """Worker pool evidence for Director handoff readiness."""

    ok: bool
    total: int = 0
    idle: int = 0
    busy: int = 0
    healthy: int = 0
    unhealthy: int = 0
    active_task_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class DirectorDiagnosticsLLMSection(BaseModel):
    """Director role-specific LLM readiness evidence."""

    ok: bool
    state: str
    role: str = "director"
    blocked_roles: list[str] = Field(default_factory=list)
    unsupported_roles: list[str] = Field(default_factory=list)
    required_ready_roles: list[str] = Field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DirectorDiagnosticsResponse(BaseModel):
    """Side-effect-free Director desktop readiness snapshot."""

    ok: bool
    can_execute: bool
    role: str = "director"
    generated_at: str
    workspace: str
    status: DirectorDiagnosticsStatusSection
    tasks: DirectorDiagnosticsTaskSection
    workers: DirectorDiagnosticsWorkerSection
    llm: DirectorDiagnosticsLLMSection
    issues: list[str] = Field(default_factory=list)
    execution_blockers: list[str] = Field(default_factory=list)


__all__ = [
    "DirectorDiagnosticsLLMSection",
    "DirectorDiagnosticsResponse",
    "DirectorDiagnosticsStatusSection",
    "DirectorDiagnosticsTaskSection",
    "DirectorDiagnosticsWorkerSection",
    "DirectorIntegrationQaRequest",
    "DirectorIntegrationQaResponse",
    "DirectorOrchestrationResponse",
    "DirectorRunOrchestrationRequest",
    "DirectorStatusResponse",
    "TaskCreateRequest",
    "TaskResponse",
]
