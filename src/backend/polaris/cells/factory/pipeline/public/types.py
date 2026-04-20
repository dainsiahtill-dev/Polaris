"""Factory Types - 无人值守开发工厂核心类型定义

本模块定义 V1 统一运行对象 FactoryRun 及其相关类型。
所有事件、产物、状态都挂在 run_id 下。

核心设计原则：
- 统一事件结构：event_id, run_id, phase, ts, level, type, message, payload
- 强一致状态迁移：只允许合法 phase 转移
- 失败分类：transient|deterministic|policy
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunPhase(str, Enum):
    """Run 阶段枚举"""

    PENDING = "pending"  # 初始状态
    INTAKE = "intake"  # 接收需求/文档
    DOCS_CHECK = "docs_check"  # 文档完备性检查
    ARCHITECT = "architect"  # 架构设计/文档生成
    PLANNING = "planning"  # 任务规划
    IMPLEMENTATION = "implementation"  # 代码实现
    VERIFICATION = "verification"  # 验证测试
    QA_GATE = "qa_gate"  # 质量门禁
    HANDOVER = "handover"  # 交付
    COMPLETED = "completed"  # 完成
    FAILED = "failed"  # 失败
    BLOCKED = "blocked"  # 阻塞（需人工介入）
    CANCELLED = "cancelled"  # 取消


class RunLifecycleStatus(str, Enum):
    """Run 生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


class FailureType(str, Enum):
    """失败类型分类"""

    TRANSIENT = "transient"  # 临时性错误，可重试
    DETERMINISTIC = "deterministic"  # 确定性错误，重试无效
    POLICY = "policy"  # 策略违反，需要人工介入


class RunLevel(str, Enum):
    """事件级别"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class GateStatus(str, Enum):
    """质量门禁状态"""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ═══════════════════════════════════════════════════════════════════════════
# 请求/响应类型
# ═══════════════════════════════════════════════════════════════════════════


class FactoryStartRequest(BaseModel):
    """Factory 启动请求"""

    workspace: str = Field(..., description="工作区路径")
    start_from: Literal["auto", "architect", "pm", "director"] = Field(
        default="auto", description="入口策略: auto=自动判定, architect=从架构开始, pm=从规划开始, director=从执行开始"
    )
    directive: str | None = Field(default=None, description="用户指令/需求描述")
    run_director: bool = Field(default=True, description="是否运行 Director")
    director_iterations: int = Field(default=1, ge=1, le=10, description="Director 迭代次数")
    loop: bool = Field(default=False, description="是否循环运行")
    input_source: str | None = Field(default=None, description="输入来源: 'directive' | 'docs' | 'existing_project'")


class FactoryControlRequest(BaseModel):
    """Factory 控制请求"""

    action: Literal["pause", "resume", "cancel", "retry_phase", "retry_from_checkpoint"] = Field(
        ..., description="控制动作"
    )
    target_phase: RunPhase | None = Field(default=None, description="目标阶段（用于 retry_* 操作）")
    reason: str | None = Field(default=None, description="操作原因")


class AgentTurnRequest(BaseModel):
    """Agent 单轮对话请求"""

    session_id: str | None = Field(default=None, description="会话ID，不提供则创建新会话")
    workspace: str = Field(..., description="工作区路径")
    message: str = Field(..., description="用户消息")
    role: Literal["pm", "architect", "chief_engineer", "director", "qa", "assistant"] = Field(
        default="assistant", description="使用的角色"
    )
    mode: Literal["chat", "workflow"] = Field(default="chat", description="模式: chat=对话, workflow=工作流")
    stream: bool = Field(default=True, description="是否流式响应")


class AgentTurnResponse(BaseModel):
    """Agent 单轮对话响应"""

    session_id: str
    reply: str
    reasoning_summary: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    session_state: dict[str, Any]
    phase: RunPhase = RunPhase.PENDING


# ═══════════════════════════════════════════════════════════════════════════
# 核心类型
# ═══════════════════════════════════════════════════════════════════════════


class RunEvent(BaseModel):
    """统一事件结构"""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    phase: RunPhase
    ts: datetime = Field(default_factory=datetime.now)
    level: RunLevel = RunLevel.INFO
    type: str  # event type: phase_enter, phase_exit, tool_call, gate_result, etc.
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None  # 用于关联相关事件


class GateResult(BaseModel):
    """质量门禁结果"""

    gate_name: str
    status: GateStatus
    score: float | None = None
    passed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)  # 产物引用


class ArtifactRef(BaseModel):
    """产物引用"""

    artifact_id: str
    artifact_type: str  # "document", "plan", "code", "test_report", "summary"
    path: str
    size: int | None = None
    checksum: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class RecoveryCheckpoint(BaseModel):
    """恢复检查点"""

    checkpoint_id: str
    run_id: str
    phase: RunPhase
    saved_at: datetime = Field(default_factory=datetime.now)
    state_snapshot: dict[str, Any]
    last_event_id: str


class FailureInfo(BaseModel):
    """失败信息"""

    failure_type: FailureType
    code: str  # 错误码
    detail: str
    phase: RunPhase
    timestamp: datetime = Field(default_factory=datetime.now)
    recoverable: bool  # 是否可恢复
    suggested_action: str | None = None  # 建议操作
    hops: list[dict[str, Any]] = Field(default_factory=list)  # 调试线索


class RoleStatus(BaseModel):
    """角色运行状态"""

    role: str
    status: str  # "idle", "running", "completed", "failed", "blocked"
    detail: str | None = None
    current_task: str | None = None
    progress: float = 0.0  # 0-100


class FactoryRun(BaseModel):
    """Factory Run 核心对象

    所有事件、产物、状态都挂在 run_id 下。
    """

    run_id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex}")
    workspace: str
    phase: RunPhase = RunPhase.PENDING
    progress: float = 0.0  # 0-100

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    # 运行配置
    start_from: str = "auto"
    directive: str | None = None
    input_source: str = "directive"
    run_director: bool = True
    director_iterations: int = 1
    loop: bool = False

    # 角色状态
    roles: dict[str, RoleStatus] = Field(default_factory=dict)

    # 质量门禁
    gates: list[GateResult] = Field(default_factory=list)

    # 产物
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    # 事件流（内存缓存）
    events: list[RunEvent] = Field(default_factory=list)

    # 失败信息
    failure: FailureInfo | None = None

    # 检查点
    last_checkpoint: RecoveryCheckpoint | None = None

    # 产物摘要
    summary_md: str | None = None
    summary_json: dict[str, Any] | None = None


class FactoryRunStatus(BaseModel):
    """Factory Run 状态响应"""

    run_id: str
    phase: RunPhase
    status: RunLifecycleStatus
    current_stage: str | None = None
    last_successful_stage: str | None = None
    progress: float

    roles: dict[str, RoleStatus]
    gates: list[GateResult]

    failure: FailureInfo | None = None

    created_at: datetime
    started_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None

    summary_md: str | None = None


class FactoryRunList(BaseModel):
    """Factory Run 列表"""

    runs: list[FactoryRunStatus]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════════════════════════
# 事件类型常量
# ═══════════════════════════════════════════════════════════════════════════


class EventType:
    """事件类型常量"""

    PHASE_ENTER = "phase_enter"
    PHASE_EXIT = "phase_exit"
    PHASE_PROGRESS = "phase_progress"

    TOOL_CALL = "tool_call"
    TOOL_START = "tool_start"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETE = "tool_complete"
    TOOL_ERROR = "tool_error"

    GATE_START = "gate_start"
    GATE_RESULT = "gate_result"

    ARTIFACT_CREATED = "artifact_created"

    ERROR = "error"
    WARNING = "warning"

    USER_INPUT = "user_input"
    ASSISTANT_OUTPUT = "assistant_output"

    CHECKPOINT_SAVED = "checkpoint_saved"
    CHECKPOINT_RESTORED = "checkpoint_restored"


# ═══════════════════════════════════════════════════════════════════════════
# 阶段转移规则
# ═══════════════════════════════════════════════════════════════════════════

# 合法的阶段转移
VALID_PHASE_TRANSITIONS: dict[RunPhase, list[RunPhase]] = {
    RunPhase.PENDING: [RunPhase.INTAKE, RunPhase.DOCS_CHECK],
    RunPhase.INTAKE: [RunPhase.DOCS_CHECK],
    RunPhase.DOCS_CHECK: [RunPhase.ARCHITECT, RunPhase.PLANNING, RunPhase.FAILED],
    RunPhase.ARCHITECT: [RunPhase.PLANNING, RunPhase.FAILED],
    RunPhase.PLANNING: [RunPhase.IMPLEMENTATION, RunPhase.FAILED],
    RunPhase.IMPLEMENTATION: [RunPhase.VERIFICATION, RunPhase.FAILED],
    RunPhase.VERIFICATION: [RunPhase.QA_GATE, RunPhase.FAILED],
    RunPhase.QA_GATE: [RunPhase.HANDOVER, RunPhase.BLOCKED, RunPhase.FAILED],
    RunPhase.HANDOVER: [RunPhase.COMPLETED],
    RunPhase.COMPLETED: [],
    RunPhase.FAILED: [RunPhase.PENDING],  # 可重试
    RunPhase.BLOCKED: [RunPhase.PENDING],  # 人工介入后可重试
    RunPhase.CANCELLED: [],
}


def is_valid_transition(from_phase: RunPhase, to_phase: RunPhase) -> bool:
    """检查阶段转移是否合法"""
    return to_phase in VALID_PHASE_TRANSITIONS.get(from_phase, [])


def get_next_phases(current_phase: RunPhase) -> list[RunPhase]:
    """获取当前阶段的所有合法下一阶段"""
    return VALID_PHASE_TRANSITIONS.get(current_phase, [])
