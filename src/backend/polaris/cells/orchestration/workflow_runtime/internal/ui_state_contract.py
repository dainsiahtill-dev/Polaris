"""UI 状态合同 (UI State Contract)

定义 UI 与后端之间的状态交换格式，确保 PM/Director/LLM Runtime
实时状态同源。

架构位置：核心编排层共享合同 (Core Orchestration Shared Contract)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class UIPhase(Enum):
    """UI 展示阶段"""

    IDLE = "idle"
    PLANNING = "planning"
    ANALYZING = "analyzing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UITaskStatus(Enum):
    """UI 任务状态"""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class UIFileChangeMetrics:
    """UI 文件变更指标

    统一展示格式: C/M/D 文件数, +/-/~ 行数
    """

    # 文件计数
    created: int = 0  # C - Created files
    modified: int = 0  # M - Modified files
    deleted: int = 0  # D - Deleted files

    # 行数计数
    lines_added: int = 0  # + lines (green)
    lines_removed: int = 0  # - lines (red)
    lines_changed: int = 0  # ~ modified lines (yellow)

    def to_display_string(self) -> str:
        """转换为展示字符串"""
        return (
            f"C{self.created}/M{self.modified}/D{self.deleted} "
            f"+{self.lines_added}/-{self.lines_removed}/~{self.lines_changed}"
        )

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
class UITaskItem:
    """UI 任务项

    每个任务统一输出:
    - 当前 phase
    - 当前文件
    - C/M/D 文件数
    - +/-/~ 行数
    - 重试次数
    - 最后更新时间
    """

    task_id: str
    role_id: str  # pm, director, qa, chief_engineer
    status: UITaskStatus
    phase: UIPhase

    # 进度信息
    current_file: str | None = None
    progress_percent: float = 0.0

    # 文件变更统计
    file_changes: UIFileChangeMetrics = field(default_factory=UIFileChangeMetrics)

    # 执行统计
    retry_count: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # 错误信息
    error_category: str | None = None
    error_message: str | None = None

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role_id": self.role_id,
            "status": self.status.value,
            "phase": self.phase.value,
            "current_file": self.current_file,
            "progress_percent": self.progress_percent,
            "file_changes": self.file_changes.to_dict(),
            "retry_count": self.retry_count,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "last_updated": self.last_updated.isoformat(),
            "error_category": self.error_category,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


@dataclass
class UIOrchestrationState:
    """UI 编排状态

    统一 runtime snapshot 字段，保证:
    - PM/Director/LLM Runtime 实时状态同源
    - 状态延迟可观测
    """

    schema_version: str = "1.0"
    run_id: str = ""
    workspace: str = ""

    # 整体状态
    overall_status: UITaskStatus = UITaskStatus.PENDING
    overall_phase: UIPhase = UIPhase.IDLE
    overall_progress: float = 0.0

    # 角色状态映射
    role_status: dict[str, UITaskStatus] = field(default_factory=dict)

    # 任务列表
    tasks: dict[str, UITaskItem] = field(default_factory=dict)

    # 全局文件变更统计
    total_file_changes: UIFileChangeMetrics = field(default_factory=UIFileChangeMetrics)

    # 时间戳
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    # 状态延迟追踪（用于监控）
    server_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float = 0.0  # 状态生成延迟

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "workspace": self.workspace,
            "overall_status": self.overall_status.value,
            "overall_phase": self.overall_phase.value,
            "overall_progress": self.overall_progress,
            "role_status": {k: v.value for k, v in self.role_status.items()},
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "total_file_changes": self.total_file_changes.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "server_timestamp": self.server_timestamp.isoformat(),
            "latency_ms": self.latency_ms,
        }


@dataclass
class UIEvent:
    """UI 事件

    用于 WebSocket 实时推送
    """

    event_type: str  # task_started, task_progress, task_completed, task_failed, etc.
    run_id: str
    task_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }


class UIStateConverter:
    """UI 状态转换器

    将内部 OrchestrationSnapshot 转换为 UI 合同格式
    """

    @staticmethod
    def from_orchestration_snapshot(snapshot) -> UIOrchestrationState:
        """从编排快照转换"""
        from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import RunStatus, TaskPhase

        # 状态映射
        status_map = {
            RunStatus.PENDING: UITaskStatus.PENDING,
            RunStatus.RUNNING: UITaskStatus.RUNNING,
            RunStatus.RETRYING: UITaskStatus.RETRYING,
            RunStatus.BLOCKED: UITaskStatus.BLOCKED,
            RunStatus.COMPLETED: UITaskStatus.COMPLETED,
            RunStatus.FAILED: UITaskStatus.FAILED,
            RunStatus.CANCELLED: UITaskStatus.CANCELLED,
            RunStatus.TIMEOUT: UITaskStatus.FAILED,
        }

        phase_map = {
            TaskPhase.INIT: UIPhase.IDLE,
            TaskPhase.PLANNING: UIPhase.PLANNING,
            TaskPhase.ANALYZING: UIPhase.ANALYZING,
            TaskPhase.EXECUTING: UIPhase.EXECUTING,
            TaskPhase.VERIFYING: UIPhase.VERIFYING,
            TaskPhase.COMPLETED: UIPhase.COMPLETED,
        }

        # 转换任务
        ui_tasks = {}
        total_changes = UIFileChangeMetrics()

        for task_id, task in snapshot.tasks.items():
            # 转换文件变更
            if hasattr(task, "file_changes"):
                fc = UIFileChangeMetrics(
                    created=task.file_changes.created,
                    modified=task.file_changes.modified,
                    deleted=task.file_changes.deleted,
                    lines_added=task.file_changes.lines_added,
                    lines_removed=task.file_changes.lines_removed,
                    lines_changed=task.file_changes.lines_changed,
                )
            else:
                fc = UIFileChangeMetrics()

            ui_task = UITaskItem(
                task_id=task_id,
                role_id=task.role_id,
                status=status_map.get(task.status, UITaskStatus.PENDING),
                phase=phase_map.get(task.phase, UIPhase.IDLE),
                current_file=task.current_file,
                progress_percent=task.progress_percent,
                file_changes=fc,
                retry_count=task.retry_count,
                start_time=task.started_at,
                end_time=task.completed_at,
                last_updated=task.updated_at or datetime.now(timezone.utc),
                error_category=task.error_category,
                error_message=task.error_message,
            )

            ui_tasks[task_id] = ui_task

            # 累加总计
            total_changes.created += fc.created
            total_changes.modified += fc.modified
            total_changes.deleted += fc.deleted
            total_changes.lines_added += fc.lines_added
            total_changes.lines_removed += fc.lines_removed
            total_changes.lines_changed += fc.lines_changed

        # 构建角色状态映射
        role_status = {}
        for task in ui_tasks.values():
            if task.role_id not in role_status:
                role_status[task.role_id] = task.status

        return UIOrchestrationState(
            schema_version=snapshot.schema_version,
            run_id=snapshot.run_id,
            workspace=snapshot.workspace,
            overall_status=status_map.get(snapshot.status, UITaskStatus.PENDING),
            overall_phase=phase_map.get(snapshot.current_phase, UIPhase.IDLE),
            overall_progress=snapshot.overall_progress,
            role_status=role_status,
            tasks=ui_tasks,
            total_file_changes=total_changes,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
            completed_at=snapshot.completed_at,
            server_timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def calculate_latency(ui_state: UIOrchestrationState) -> float:
        """计算状态延迟（毫秒）"""
        now = datetime.now(timezone.utc)
        delta = now - ui_state.server_timestamp
        return delta.total_seconds() * 1000


__all__ = [
    "UIEvent",
    "UIFileChangeMetrics",
    "UIOrchestrationState",
    "UIPhase",
    "UIStateConverter",
    "UITaskItem",
    "UITaskStatus",
]
