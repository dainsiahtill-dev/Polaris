"""Runtime V2 Protocol Types - 实时状态协议 V2

本模块定义 V2 实时协议类型，用于前后端实时状态同步。
所有消息类型统一为 runtime_snapshot_v2 和 runtime_event_v2。

核心设计原则：
- 强类型定义：所有字段都有明确的类型和约束
- 统一状态语义：所有角色/worker/task 使用统一的状态枚举
- 完整上下文：每条消息包含足够的上下文用于 UI 渲染
- 性能优化：消息体积最小化，避免冗余数据
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ═══════════════════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════════════════


class RoleType(str, Enum):
    """角色类型枚举"""

    PM = "PM"
    ARCHITECT = "Architect"
    CHIEF_ENGINEER = "ChiefEngineer"
    DIRECTOR = "Director"
    QA = "QA"
    CFO = "CFO"
    HR = "HR"


class RoleState(str, Enum):
    """角色状态枚举"""

    IDLE = "idle"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFICATION = "verification"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class WorkerState(str, Enum):
    """Worker 状态枚举"""

    IDLE = "idle"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskState(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class PMTaskState(str, Enum):
    """PM 任务状态枚举"""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class QATaskState(str, Enum):
    """QA 审计状态枚举"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class EventSeverity(str, Enum):
    """事件严重程度"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CodeChangeStatus(str, Enum):
    """代码变更状态"""

    PENDING = "pending"
    GENERATED = "generated"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewVerdict(str, Enum):
    """评审结论"""

    APPROVED = "approved"
    REJECTED = "rejected"
    COMMENTS = "comments"


# ═══════════════════════════════════════════════════════════════════════════
# V2 类型定义
# ═══════════════════════════════════════════════════════════════════════════


class RuntimeRoleState(BaseModel):
    """角色运行时状态"""

    role: RoleType
    state: RoleState
    task_id: str | None = None
    task_title: str | None = None
    detail: str | None = None
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(use_enum_values=True)


class RuntimeWorkerState(BaseModel):
    """Worker 运行时状态"""

    id: str
    state: WorkerState
    task_id: str | None = None
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(use_enum_values=True)


class RuntimeTaskNode(BaseModel):
    """任务节点"""

    id: str
    title: str
    level: int = Field(ge=1, le=10, description="任务层级，1 为最顶层")
    parent_id: str | None = None
    state: TaskState
    blocked_by: list[str] = Field(default_factory=list, description="阻塞此任务的任务ID列表")
    progress: float = Field(ge=0, le=100, default=0, description="完成进度百分比")

    model_config = ConfigDict(use_enum_values=True)


class PMTaskNode(BaseModel):
    """PM 任务节点"""

    id: str
    title: str
    description: str = ""
    state: PMTaskState
    priority: int = Field(default=1, ge=0, le=3, description="优先级: 0=low, 1=medium, 2=high, 3=critical")
    assignee: str | None = None
    acceptance: list[str] = Field(default_factory=list, description="验收标准")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(use_enum_values=True)


class QATaskNode(BaseModel):
    """QA 审计任务节点"""

    id: str
    target_task_id: str
    target_type: Literal["task", "code_change", "file"] = "task"
    state: QATaskState
    verdict: ReviewVerdict | None = None
    issues: list[str] = Field(default_factory=list)
    reviewed_by: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None

    model_config = ConfigDict(use_enum_values=True)


class RuntimeSummary(BaseModel):
    """运行摘要"""

    total: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0


class DiffHunk(BaseModel):
    """Diff 内容块"""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    content: str


class CodeChangeState(BaseModel):
    """代码变更状态"""

    change_id: str
    task_id: str
    worker_id: str
    file_path: str
    base_sha: str
    head_sha: str
    hunks: list[DiffHunk] = Field(default_factory=list)
    status: CodeChangeStatus = CodeChangeStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(use_enum_values=True)


class ReviewComment(BaseModel):
    """评审评论"""

    id: str
    reviewer: str
    body: str
    file_path: str | None = None
    line: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class ReviewState(BaseModel):
    """评审状态"""

    review_id: str
    change_id: str
    task_id: str
    worker_id: str
    verdict: ReviewVerdict | None = None
    reviewer: str | None = None
    comments: list[ReviewComment] = Field(default_factory=list)
    status: Literal["pending", "reviewing", "completed"] = "pending"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(use_enum_values=True)


class RuntimeSnapshotV2(BaseModel):
    """运行时快照 V2 - 主数据源

    完整的状态快照，包含所有角色、任务、Worker 的当前状态。
    客户端应以此为主要数据源进行状态渲染。
    """

    type: Literal["runtime_snapshot_v2"] = "runtime_snapshot_v2"
    schema_version: Literal[2] = 2
    run_id: str
    ts: datetime = Field(default_factory=datetime.now)

    # 阶段信息 - 使用 factory.RunPhase
    phase: str

    # 角色状态映射
    roles: dict[RoleType, RuntimeRoleState] = Field(default_factory=dict)

    # Worker 列表
    workers: list[RuntimeWorkerState] = Field(default_factory=list)

    # 任务树
    tasks: list[RuntimeTaskNode] = Field(default_factory=list)

    # 运行摘要
    summary: RuntimeSummary = Field(default_factory=RuntimeSummary)

    # 代码变更列表
    code_changes: list[CodeChangeState] = Field(default_factory=list)

    # 评审列表
    reviews: list[ReviewState] = Field(default_factory=list)

    # 错误信息（如果有）
    error: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class RuntimeEventV2(BaseModel):
    """运行时事件 V2 - 增量事件流

    实时推送的增量事件，用于更新 UI 时间线和实时反馈。
    客户端应根据 seq 字段进行顺序处理。
    """

    type: Literal["runtime_event_v2"] = "runtime_event_v2"
    schema_version: Literal[2] = 2
    event_id: str
    seq: int = Field(ge=0, description="事件序列号，用于排序")
    run_id: str
    ts: datetime = Field(default_factory=datetime.now)

    # 事件类型：general, diff_generated, review_requested, review_result, pm_task_generated, qa_audit
    event_type: (
        Literal[
            "general",
            "diff_generated",
            "review_requested",
            "review_result",
            "pm_task_generated",
            "pm_iteration_complete",
            "pm_phase_change",
            "qa_audit_started",
            "qa_audit_completed",
            "qa_issue_found",
        ]
        | None
    ) = "general"

    # 阶段信息
    phase: str

    # 事件来源
    role: RoleType | None = None
    node_level: int | None = Field(None, ge=1, le=10, description="任务层级")

    # 状态变更
    state: str | None = None

    # 关联任务/Worker
    task_id: str | None = None
    worker_id: str | None = None

    # 代码变更相关字段
    change_id: str | None = None  # 代码变更ID
    file_path: str | None = None  # 文件路径
    base_sha: str | None = None  # 变更前 SHA
    head_sha: str | None = None  # 变更后 SHA
    verdict: ReviewVerdict | None = None  # 评审结论
    review_status: CodeChangeStatus | None = None  # 变更评审状态

    # 事件内容
    severity: EventSeverity = EventSeverity.INFO
    message: str
    detail: str | None = None

    # 性能指标
    metrics: dict[str, Any] = Field(default_factory=dict, description="如 latency_ms, queue_depth, tokens_used")

    model_config = ConfigDict(use_enum_values=True)


# ═══════════════════════════════════════════════════════════════════════════
# JSON Schema 输出（供前端对齐）
# ═══════════════════════════════════════════════════════════════════════════


def get_runtime_snapshot_v2_schema() -> dict[str, Any]:
    """获取 RuntimeSnapshotV2 的 JSON Schema"""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "RuntimeSnapshotV2",
        "type": "object",
        "required": ["type", "schema_version", "run_id", "ts", "phase", "roles", "workers", "tasks", "summary"],
        "properties": {
            "type": {"const": "runtime_snapshot_v2"},
            "schema_version": {"const": 2},
            "run_id": {"type": "string"},
            "ts": {"type": "string", "format": "date-time"},
            "phase": {
                "type": "string",
                "enum": [
                    "pending",
                    "intake",
                    "docs_check",
                    "architect",
                    "planning",
                    "implementation",
                    "verification",
                    "qa_gate",
                    "handover",
                    "completed",
                    "failed",
                    "blocked",
                    "cancelled",
                ],
            },
            "roles": {
                "type": "object",
                "properties": {
                    "PM": {"$ref": "#/definitions/RuntimeRoleState"},
                    "ChiefEngineer": {"$ref": "#/definitions/RuntimeRoleState"},
                    "Director": {"$ref": "#/definitions/RuntimeRoleState"},
                    "QA": {"$ref": "#/definitions/RuntimeRoleState"},
                },
            },
            "workers": {"type": "array", "items": {"$ref": "#/definitions/RuntimeWorkerState"}},
            "tasks": {"type": "array", "items": {"$ref": "#/definitions/RuntimeTaskNode"}},
            "summary": {"$ref": "#/definitions/RuntimeSummary"},
            "error": {"type": ["string", "null"]},
        },
        "definitions": {
            "RuntimeRoleState": {
                "type": "object",
                "required": ["role", "state", "updated_at"],
                "properties": {
                    "role": {"type": "string", "enum": ["PM", "ChiefEngineer", "Director", "QA"]},
                    "state": {
                        "type": "string",
                        "enum": [
                            "idle",
                            "analyzing",
                            "planning",
                            "executing",
                            "verification",
                            "completed",
                            "failed",
                            "blocked",
                        ],
                    },
                    "task_id": {"type": ["string", "null"]},
                    "task_title": {"type": ["string", "null"]},
                    "detail": {"type": ["string", "null"]},
                    "updated_at": {"type": "string", "format": "date-time"},
                },
            },
            "RuntimeWorkerState": {
                "type": "object",
                "required": ["id", "state", "updated_at"],
                "properties": {
                    "id": {"type": "string"},
                    "state": {"type": "string", "enum": ["idle", "claimed", "in_progress", "completed", "failed"]},
                    "task_id": {"type": ["string", "null"]},
                    "updated_at": {"type": "string", "format": "date-time"},
                },
            },
            "RuntimeTaskNode": {
                "type": "object",
                "required": ["id", "title", "level", "state", "blocked_by", "progress"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": 10},
                    "parent_id": {"type": ["string", "null"]},
                    "state": {
                        "type": "string",
                        "enum": [
                            "pending",
                            "ready",
                            "claimed",
                            "in_progress",
                            "completed",
                            "failed",
                            "blocked",
                            "cancelled",
                        ],
                    },
                    "blocked_by": {"type": "array", "items": {"type": "string"}},
                    "progress": {"type": "number", "minimum": 0, "maximum": 100},
                },
            },
            "RuntimeSummary": {
                "type": "object",
                "required": ["total", "completed", "failed", "blocked"],
                "properties": {
                    "total": {"type": "integer"},
                    "completed": {"type": "integer"},
                    "failed": {"type": "integer"},
                    "blocked": {"type": "integer"},
                },
            },
        },
    }


def get_runtime_event_v2_schema() -> dict[str, Any]:
    """获取 RuntimeEventV2 的 JSON Schema"""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "RuntimeEventV2",
        "type": "object",
        "required": ["type", "schema_version", "event_id", "seq", "run_id", "ts", "phase", "severity", "message"],
        "properties": {
            "type": {"const": "runtime_event_v2"},
            "schema_version": {"const": 2},
            "event_id": {"type": "string"},
            "seq": {"type": "integer", "minimum": 0},
            "run_id": {"type": "string"},
            "ts": {"type": "string", "format": "date-time"},
            "phase": {"type": "string"},
            "role": {"type": ["string", "null"], "enum": ["PM", "ChiefEngineer", "Director", "QA", "Worker", None]},
            "node_level": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
            "state": {"type": ["string", "null"]},
            "task_id": {"type": ["string", "null"]},
            "worker_id": {"type": ["string", "null"]},
            "severity": {"type": "string", "enum": ["debug", "info", "warning", "error"]},
            "message": {"type": "string"},
            "detail": {"type": ["string", "null"]},
            "metrics": {"type": "object"},
        },
    }
