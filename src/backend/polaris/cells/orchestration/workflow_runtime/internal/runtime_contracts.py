"""统一编排契约类型 (Unified Orchestration Contracts)

定义单一编排内核使用的标准类型和契约，消除 PM/Director 的字段漂移。
遵循零信任原则：所有输入必须校验。

架构位置：应用层契约 (Application Layer Contracts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

# ============================================================================
# 执行模式枚举 (Execution Mode)
# ============================================================================


class OrchestrationMode(Enum):
    """编排执行模式"""

    CHAT = "chat"  # 交互式对话（类似 Claude/Codex）
    WORKFLOW = "workflow"  # 合同驱动执行（自动化）


class RunStatus(Enum):
    """统一运行状态枚举 - 消除 PM/Director 状态漂移"""

    PENDING = "pending"  # 等待执行
    RUNNING = "running"  # 执行中
    RETRYING = "retrying"  # 重试中
    BLOCKED = "blocked"  # 依赖阻塞
    COMPLETED = "completed"  # 成功完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 被取消
    TIMEOUT = "timeout"  # 超时

    def is_terminal(self) -> bool:
        """是否为终态"""
        return self in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMEOUT,
        )

    def can_retry(self) -> bool:
        """是否允许重试"""
        return self in (RunStatus.FAILED, RunStatus.TIMEOUT)


class TaskPhase(Enum):
    """任务执行阶段（UI 进度追踪用）"""

    INIT = "init"  # 初始化
    PLANNING = "planning"  # 规划
    ANALYZING = "analyzing"  # 分析
    EXECUTING = "executing"  # 执行
    VERIFYING = "verifying"  # 验证
    COMPLETED = "completed"  # 完成


# ============================================================================
# 角色与任务定义
# ============================================================================


@dataclass(frozen=True)
class RoleEntrySpec:
    """角色条目规格

    Attributes:
        role_id: 角色标识 (pm, architect, chief_engineer, director, qa)
        input: 角色输入（指令/上下文）
        scope_paths: 作用域路径列表（文件/目录）
        tool_policy: 工具授权策略
        retry_policy: 重试策略配置
    """

    role_id: str
    input: str = ""
    scope_paths: list[str] = field(default_factory=list)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(
        default_factory=lambda: {
            "max_attempts": 3,
            "backoff_seconds": 1.0,
        }
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """零信任校验"""
        errors = []

        valid_roles = {"pm", "architect", "chief_engineer", "director", "qa"}
        if self.role_id not in valid_roles:
            errors.append(f"Invalid role_id: {self.role_id}, must be one of {valid_roles}")

        if len(self.input) > 10_000_000:  # 10MB 限制
            errors.append("Input exceeds 10MB limit")

        for path in self.scope_paths:
            if ".." in path:
                errors.append(f"Path traversal detected: {path}")

        for key, value in self.metadata.items():
            try:
                str(key).encode("utf-8")
                str(value).encode("utf-8")
            except UnicodeEncodeError as e:
                errors.append(f"Non-UTF-8 in role entry metadata: {e}")

        return errors


@dataclass(frozen=True)
class PipelineTask:
    """流水线任务定义

    Attributes:
        task_id: 任务唯一标识
        role_entry: 角色执行规格
        depends_on: 依赖任务ID列表
        max_concurrency: 最大并发度（当前任务）
        timeout_seconds: 任务超时（秒）
        continue_on_error: 错误时是否继续
    """

    task_id: str
    role_entry: RoleEntrySpec
    depends_on: list[str] = field(default_factory=list)
    max_concurrency: int = 1
    timeout_seconds: int = MAX_WORKFLOW_TIMEOUT_SECONDS
    continue_on_error: bool = False

    def validate(self) -> list[str]:
        """零信任校验"""
        errors = []

        if not self.task_id:
            errors.append("task_id cannot be empty")
        elif not self.task_id.replace("-", "").replace("_", "").isalnum():
            errors.append(f"Invalid task_id format: {self.task_id}")

        if self.max_concurrency < 1:
            errors.append(f"max_concurrency must be >= 1, got {self.max_concurrency}")

        if self.timeout_seconds < 1:
            errors.append(f"timeout_seconds must be >= 1, got {self.timeout_seconds}")

        errors.extend(self.role_entry.validate())
        return errors


@dataclass(frozen=True)
class PipelineSpec:
    """流水线规格

    Attributes:
        tasks: 任务列表
        max_concurrency: 全局最大并发度
        global_timeout_seconds: 全局超时（秒）
        continue_on_error: 全局错误继续策略
    """

    tasks: list[PipelineTask] = field(default_factory=list)
    max_concurrency: int = 3
    global_timeout_seconds: int = 7200  # 2 hours
    continue_on_error: bool = False

    def validate(self) -> list[str]:
        """零信任校验 + 依赖图验证"""
        errors = []

        if not self.tasks:
            errors.append("Pipeline must have at least one task")
            return errors

        # 校验每个任务
        task_ids: set[str] = set()
        for task in self.tasks:
            if task.task_id in task_ids:
                errors.append(f"Duplicate task_id: {task.task_id}")
            task_ids.add(task.task_id)
            errors.extend(task.validate())

        # 校验依赖存在性
        for task in self.tasks:
            for dep_id in task.depends_on:
                if dep_id not in task_ids:
                    errors.append(f"Task {task.task_id} depends on unknown task: {dep_id}")

        # 检测循环依赖
        if self._has_cycle(task_ids):
            errors.append("Circular dependency detected in pipeline")

        return errors

    def _has_cycle(self, task_ids: set[str]) -> bool:
        """检测依赖图中是否存在环"""
        graph = {t.task_id: set(t.depends_on) for t in self.tasks}
        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.remove(node)
            return False

        return any(node not in visited and dfs(node) for node in task_ids)


# ============================================================================
# 编排请求与快照
# ============================================================================


@dataclass(frozen=True)
class OrchestrationRunRequest:
    """统一编排运行请求

    这是提交编排运行的唯一入口契约。

    Attributes:
        run_id: 运行唯一标识（由调用方生成或系统分配）
        workspace: 工作区路径
        mode: 执行模式 (chat/workflow)
        pipeline_spec: 流水线规格
        role_entries: 角色条目列表（简化场景使用）
        constraints: 约束条件
        metadata: 元数据（追踪/审计）
    """

    run_id: str
    workspace: Path
    mode: OrchestrationMode
    pipeline_spec: PipelineSpec | None = None
    role_entries: list[RoleEntrySpec] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """零信任校验"""
        errors = []

        if not self.run_id:
            errors.append("run_id cannot be empty")

        if not isinstance(self.workspace, Path):
            errors.append(f"workspace must be Path, got {type(self.workspace)}")
        elif not self.workspace.exists():
            errors.append(f"Workspace does not exist: {self.workspace}")

        if self.pipeline_spec is not None:
            errors.extend(self.pipeline_spec.validate())
        elif not self.role_entries:
            errors.append("Either pipeline_spec or role_entries must be provided")

        if self.role_entries:
            for entry in self.role_entries:
                errors.extend(entry.validate())

        # 校验元数据 UTF-8
        for key, value in self.metadata.items():
            try:
                str(key).encode("utf-8")
                str(value).encode("utf-8")
            except UnicodeEncodeError as e:
                errors.append(f"Non-UTF-8 in metadata: {e}")

        return errors


@dataclass
class FileChangeStats:
    """文件变更统计（UI 展示用）"""

    created: int = 0  # C - Created
    modified: int = 0  # M - Modified
    deleted: int = 0  # D - Deleted
    lines_added: int = 0  # + lines
    lines_removed: int = 0  # - lines
    lines_changed: int = 0  # ~ modified lines

    def to_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "modified": self.modified,
            "deleted": self.deleted,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "lines_changed": self.lines_changed,
        }


@dataclass
class TaskSnapshot:
    """任务状态快照（统一字段，消除漂移）"""

    task_id: str
    status: RunStatus
    phase: TaskPhase
    role_id: str

    # 进度信息
    current_file: str | None = None
    progress_percent: float = 0.0

    # 统计信息
    file_changes: FileChangeStats = field(default_factory=FileChangeStats)
    retry_count: int = 0

    # 时间戳
    created_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    # 错误信息
    error_category: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "phase": self.phase.value,
            "role_id": self.role_id,
            "current_file": self.current_file,
            "progress_percent": self.progress_percent,
            "file_changes": self.file_changes.to_dict(),
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_category": self.error_category,
            "error_message": self.error_message,
        }


@dataclass
class OrchestrationSnapshot:
    """统一编排运行快照

    这是唯一真实状态来源（Single Source of Truth）。
    UI、API、CLI 都从这个结构获取状态。

    Attributes:
        schema_version: 快照 schema 版本（兼容演进）
        run_id: 运行标识
        workspace: 工作区路径
        mode: 执行模式
        status: 整体状态
        tasks: 任务状态字典
        current_phase: 当前阶段
        overall_progress: 整体进度 0-100
        created_at: 创建时间
        updated_at: 更新时间
        completed_at: 完成时间
    """

    schema_version: str = "1.0"
    run_id: str = ""
    workspace: str = ""
    mode: str = "workflow"
    status: RunStatus = RunStatus.PENDING
    tasks: dict[str, TaskSnapshot] = field(default_factory=dict)
    current_phase: TaskPhase = TaskPhase.INIT
    overall_progress: float = 0.0

    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "workspace": self.workspace,
            "mode": self.mode,
            "status": self.status.value,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "current_phase": self.current_phase.value,
            "overall_progress": self.overall_progress,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ============================================================================
# 信号与控制
# ============================================================================


class OrchestrationSignal(Enum):
    """编排控制信号"""

    CANCEL = "cancel"  # 取消运行
    PAUSE = "pause"  # 暂停
    RESUME = "resume"  # 恢复
    RETRY = "retry"  # 重试失败任务
    SKIP = "skip"  # 跳过当前任务


@dataclass(frozen=True)
class SignalRequest:
    """信号请求"""

    signal: OrchestrationSignal
    task_id: str | None = None  # 特定任务，None 表示整体
    payload: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 兼容层映射（PM/Director 模式映射）
# ============================================================================


class CompatibilityMapper:
    """兼容层映射器

    将旧版 PM/Director 语义映射到统一编排契约。
    """

    @staticmethod
    def pm_mode_to_orchestration(mode: str) -> OrchestrationMode:
        """PM 模式映射"""
        mode_map = {
            "run_once": OrchestrationMode.WORKFLOW,
            "loop": OrchestrationMode.WORKFLOW,
            "chat": OrchestrationMode.CHAT,
        }
        return mode_map.get(mode, OrchestrationMode.WORKFLOW)

    @staticmethod
    def director_mode_to_orchestration(mode: str) -> OrchestrationMode:
        """Director 模式映射"""
        mode_map = {
            "one_shot": OrchestrationMode.WORKFLOW,
            "continuous": OrchestrationMode.WORKFLOW,
            "chat": OrchestrationMode.CHAT,
        }
        return mode_map.get(mode, OrchestrationMode.WORKFLOW)

    @staticmethod
    def legacy_status_to_unified(status: str) -> RunStatus:
        """旧状态映射到统一状态"""
        status_map = {
            # PM 状态
            "idle": RunStatus.PENDING,
            "running": RunStatus.RUNNING,
            "completed": RunStatus.COMPLETED,
            "error": RunStatus.FAILED,
            # Director 状态
            "pending": RunStatus.PENDING,
            "in_progress": RunStatus.RUNNING,
            "success": RunStatus.COMPLETED,
            "failure": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }
        return status_map.get(status.lower(), RunStatus.PENDING)


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    # Utils
    "CompatibilityMapper",
    # Snapshots
    "FileChangeStats",
    # Enums
    "OrchestrationMode",
    # Requests
    "OrchestrationRunRequest",
    "OrchestrationSignal",
    "OrchestrationSnapshot",
    "PipelineSpec",
    "PipelineTask",
    # Specs
    "RoleEntrySpec",
    "RunStatus",
    "SignalRequest",
    "TaskPhase",
    "TaskSnapshot",
]
