"""Shared API payload schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentsApplyPayload(BaseModel):
    draft_path: str | None = None


class AgentsFeedbackPayload(BaseModel):
    text: str = ""


class DocsInitPreviewPayload(BaseModel):
    mode: str = "minimal"
    goal: str = ""
    in_scope: str = ""
    out_of_scope: str = ""
    constraints: str = ""
    definition_of_done: str = ""
    backlog: str = ""


class DocsInitSuggestPayload(BaseModel):
    goal: str = ""
    in_scope: str = ""
    out_of_scope: str = ""
    constraints: str = ""
    definition_of_done: str = ""
    backlog: str = ""


class DocsInitDialogueTurn(BaseModel):
    role: str = ""
    content: str = ""
    questions: list[str] = Field(default_factory=list)


class DocsInitDialoguePayload(BaseModel):
    message: str = ""
    goal: str = ""
    in_scope: str = ""
    out_of_scope: str = ""
    constraints: str = ""
    definition_of_done: str = ""
    backlog: str = ""
    history: list[DocsInitDialogueTurn] = Field(default_factory=list)


class DocsInitFile(BaseModel):
    path: str
    content: str


class DocsInitApplyPayload(BaseModel):
    mode: str = "minimal"
    target_root: str = "docs"
    files: list[DocsInitFile] = Field(default_factory=list)


# ============================================================
# Audit V2 Schemas (Phase 1 - 架构级审计重构)
# ============================================================


class AuditEventResponse(BaseModel):
    """审计事件响应模型"""

    event_id: str
    timestamp: str
    event_type: str
    version: str = "1.0"
    source: dict[str, str] = Field(default_factory=dict)
    task: dict[str, Any] = Field(default_factory=dict)
    resource: dict[str, str] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    signature: str


class AuditLogsResponse(BaseModel):
    """审计日志查询响应"""

    events: list[dict[str, Any]]
    pagination: dict[str, Any]


class AuditExportParams(BaseModel):
    """审计导出参数"""

    format: str = "json"  # json, csv
    start_time: str | None = None
    end_time: str | None = None
    event_types: list[str] | None = None
    include_data: bool = True


class AuditVerifyResponse(BaseModel):
    """审计链验证响应"""

    chain_valid: bool
    first_event_hash: str
    last_event_hash: str
    total_events: int
    gap_count: int
    verified_at: str
    invalid_events: list[dict[str, str]] = Field(default_factory=list)


class AuditStatsResponse(BaseModel):
    """审计统计响应"""

    stats: dict[str, Any]
    time_range: dict[str, str | None]


class AuditCleanupParams(BaseModel):
    """审计清理参数"""

    dry_run: bool = True
    older_than_days: int | None = None


class AuditCleanupResponse(BaseModel):
    """审计清理响应"""

    would_delete: int
    would_free_mb: float
    affected_files: list[str] = Field(default_factory=list)
    dry_run: bool
    cutoff_date: str


# V2 新增 - Triage 相关
class AuditTriageRequest(BaseModel):
    """排障请求"""

    run_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None


class AuditTriageResponse(BaseModel):
    """排障响应 - 完整排障包"""

    status: str  # success, not_found, partial
    run_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None

    # PM 质量历史
    pm_quality_history: list[dict[str, Any]] = Field(default_factory=list)

    # 泄漏发现
    leakage_findings: list[dict[str, Any]] = Field(default_factory=list)

    # Director 工具审计
    director_tool_audit: dict[str, Any] = Field(default_factory=dict)

    # 修复的问题
    issues_fixed: list[dict[str, Any]] = Field(default_factory=list)

    # 验收结果
    acceptance_results: dict[str, Any] = Field(default_factory=dict)

    # 证据路径
    evidence_paths: dict[str, list[str]] = Field(default_factory=dict)

    # 下一个风险
    next_risks: list[str] = Field(default_factory=list)

    # 3-hops 失败定位
    failure_hops: dict[str, Any] | None = None

    # 生成时间
    generated_at: str


class FailureHopsResponse(BaseModel):
    """3-hops 失败定位响应"""

    schema_version: int = 2
    run_id: str
    generated_at: str
    ready: bool
    has_failure: bool
    failure_code: str
    failure_event_seq: int | None = None

    # Hop 1: Phase
    hop1_phase: dict[str, Any] | None = None

    # Hop 2: Evidence
    hop2_evidence: dict[str, Any] | None = None

    # Hop 3: Tool Output
    hop3_tool_output: dict[str, Any] | None = None


class FailureAnalysisRequest(BaseModel):
    """失败链条诊断请求."""

    run_id: str | None = None
    task_id: str | None = None
    error_message: str | None = None
    time_range: str = "1h"
    depth: int = Field(default=3, ge=1, le=3)


class FailureAnalysisResponse(BaseModel):
    """失败链条诊断响应."""

    run_id: str | None = None
    task_id: str | None = None
    depth: int
    failure_hops: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str
    root_cause: dict[str, Any] | None = None
    failure_detected: bool
    event_count: int


class ProjectScanRequest(BaseModel):
    """项目级审计扫描请求."""

    scope: str = "full"  # full, changed, region
    focus: str | None = None
    max_files: int = Field(default=800, ge=1, le=5000)
    max_findings: int = Field(default=300, ge=1, le=2000)


class ProjectScanResponse(BaseModel):
    """项目级审计扫描响应."""

    scope: str
    focus: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CodeRegionRequest(BaseModel):
    """代码区域审计请求."""

    file_path: str | None = None
    function_name: str | None = None
    lines: str | None = None  # e.g. "10-50"


class CodeRegionResponse(BaseModel):
    """代码区域审计响应."""

    file: str
    function_name: str = ""
    line_range: dict[str, int] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)


class AuditTraceResponse(BaseModel):
    """Trace 查询响应."""

    trace_id: str
    event_count: int
    run_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    first_timestamp: str = ""
    last_timestamp: str = ""
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class CorruptionRecord(BaseModel):
    """损坏记录"""

    timestamp: str
    file_path: str
    offset: int
    error_type: str
    error_message: str
    line_preview: str
    recovered: bool = False
