"""目标执行投影 - Goal Execution Projection

派生视图：从 GoalProposal + TaskProgress 实时计算执行状态。
不污染持久化的 GoalProposal。

关联文档:
- docs/resident/design/goal-execution-projection.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from polaris.kernelone.utils.time_utils import utc_now_str


class ExecutionStage(str, Enum):
    """执行阶段"""

    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEW = "review"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


@dataclass
class TaskProgressItem:
    """单个任务的进度"""

    task_id: str
    subject: str
    status: Literal["pending", "in_progress", "completed", "failed", "blocked"] = "pending"
    progress_percent: float = 0.0
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "subject": self.subject,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskProgressItem:
        return cls(
            task_id=data.get("task_id", ""),
            subject=data.get("subject", ""),
            status=data.get("status", "pending"),
            progress_percent=data.get("progress_percent", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class GoalExecutionView:
    """目标执行投影 - 派生视图，不持久化

    Attributes:
        goal_id: 目标ID
        stage: 当前执行阶段
        percent: 进度百分比 (0.0 - 1.0)
        current_task: 当前正在执行的任务
        eta_minutes: 预计剩余时间（分钟）
        total_tasks: 任务总数
        completed_tasks: 已完成任务数
        failed_tasks: 失败任务数
        started_at: 开始时间
        updated_at: 更新时间
        task_progress: 关联的任务列表
    """

    goal_id: str
    stage: str = ExecutionStage.UNKNOWN.value
    percent: float = 0.0
    current_task: str | None = None
    eta_minutes: int | None = None
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    started_at: str | None = None
    updated_at: str = field(default_factory=utc_now_str)
    task_progress: list[TaskProgressItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "stage": self.stage,
            "percent": round(self.percent, 2),
            "current_task": self.current_task,
            "eta_minutes": self.eta_minutes,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "task_progress": [t.to_dict() for t in self.task_progress],
        }


# 阶段关键词映射
STAGE_KEYWORDS: dict[str, list[str]] = {
    ExecutionStage.PLANNING.value: [
        "plan",
        "设计",
        "分析",
        "调研",
        "方案",
        "架构",
        "proposal",
        "design",
        "analyze",
        "research",
        "调研",
        "评估",
        "可行性",
    ],
    ExecutionStage.CODING.value: [
        "实现",
        "编写",
        "重构",
        "修改",
        "添加",
        "更新",
        "implement",
        "code",
        "refactor",
        "write",
        "modify",
        "add",
        "update",
        "create",
        "开发",
    ],
    ExecutionStage.TESTING.value: [
        "测试",
        "验证",
        "fix",
        "修复",
        "test",
        "verify",
        "check",
        "debug",
        "validate",
        "bugfix",
        "解决",
        "修复",
    ],
    ExecutionStage.REVIEW.value: [
        "审查",
        "review",
        "检查",
        "audit",
        "inspect",
        "评估",
        "验收",
        "检视",
        "code review",
        "走查",
    ],
}


def infer_stage(tasks: list[TaskProgressItem]) -> str:
    """从任务列表推断当前阶段

    算法:
    1. 如果全部完成 -> completed
    2. 找到第一个未完成的任务
    3. 根据任务主题关键词匹配阶段
    4. 无匹配时根据完成比例推断
    """
    if not tasks:
        return ExecutionStage.UNKNOWN.value

    # 检查是否全部完成
    if all(t.status == "completed" for t in tasks):
        return ExecutionStage.COMPLETED.value

    # 找到第一个未完成的任务
    pending = [t for t in tasks if t.status != "completed"]
    if not pending:
        return ExecutionStage.COMPLETED.value

    # 优先使用进行中的任务，否则使用第一个待处理
    active_tasks = [t for t in pending if t.status == "in_progress"]
    target_task = active_tasks[0] if active_tasks else pending[0]

    subject_lower = target_task.subject.lower()

    # 关键词匹配
    for stage, keywords in STAGE_KEYWORDS.items():
        if any(kw.lower() in subject_lower for kw in keywords):
            return stage

    # 默认: 根据完成比例推断
    completed_count = len([t for t in tasks if t.status == "completed"])
    ratio = completed_count / len(tasks) if tasks else 0

    if ratio <= 0.2:
        return ExecutionStage.PLANNING.value
    elif ratio <= 0.7:
        return ExecutionStage.CODING.value
    else:
        return ExecutionStage.TESTING.value


def estimate_eta(tasks: list[TaskProgressItem]) -> int | None:
    """估算剩余时间（分钟）

    算法:
    1. 如果有已完成任务，计算平均耗时
    2. 否则使用默认估算（每个任务5分钟）
    3. 应用边界限制（1-120分钟）
    """
    if not tasks:
        return None

    # 已完成的任务作为基准
    completed = [t for t in tasks if t.status == "completed" and t.started_at and t.completed_at]

    # 计算剩余任务数
    remaining = [t for t in tasks if t.status in ("pending", "in_progress", "blocked")]
    remaining_count = len(remaining)

    if remaining_count == 0:
        return 0

    if not completed:
        # 无历史数据，使用默认估算
        return max(1, min(remaining_count * 5, 120))

    # 计算平均任务耗时
    durations: list[float] = []
    for task in completed:
        try:
            started = task.started_at or ""
            completed_at = task.completed_at or ""
            start = datetime.fromisoformat(started.replace("Z", "+00:00"))
            end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration_minutes = (end - start).total_seconds() / 60
            if duration_minutes > 0:
                durations.append(duration_minutes)
        except (ValueError, AttributeError):
            continue

    if not durations:
        return max(1, min(remaining_count * 5, 120))

    # 使用中位数避免极端值影响
    durations.sort()
    avg_duration: float = durations[len(durations) // 2]

    # 特别处理 in_progress 任务
    in_progress_eta: float = 0
    for task in remaining:
        if task.status == "in_progress":
            # 已进行50%的任务，剩余50%时间
            in_progress_eta += avg_duration * (1 - task.progress_percent)
        else:
            in_progress_eta += avg_duration

    # 边界限制
    return max(1, min(int(in_progress_eta), 120))


def calculate_percent(tasks: list[TaskProgressItem]) -> float:
    """计算整体进度百分比"""
    if not tasks:
        return 0.0

    total_progress = 0.0
    for task in tasks:
        status_weights = {
            "pending": 0.0,
            "in_progress": 0.5,
            "completed": 1.0,
            "failed": 0.0,
            "blocked": 0.0,
        }
        base_weight = status_weights.get(task.status, 0.0)

        # 对于进行中的任务，使用细粒度进度
        if task.status == "in_progress":
            total_progress += task.progress_percent
        else:
            total_progress += base_weight

    return round(total_progress / len(tasks), 2)


def get_current_task(tasks: list[TaskProgressItem]) -> str | None:
    """获取当前正在执行的任务主题"""
    # 优先找进行中的
    in_progress = [t for t in tasks if t.status == "in_progress"]
    if in_progress:
        return in_progress[0].subject

    # 其次找失败的
    failed = [t for t in tasks if t.status == "failed"]
    if failed:
        return f"[失败] {failed[0].subject}"

    # 然后找阻塞的
    blocked = [t for t in tasks if t.status == "blocked"]
    if blocked:
        return f"[阻塞] {blocked[0].subject}"

    return None


def build_goal_execution_projection(
    goal_id: str,
    task_progress: list[dict[str, Any]],
    started_at: str | None = None,
) -> GoalExecutionView:
    """构建目标执行投影

    Args:
        goal_id: 目标ID
        task_progress: 任务进度列表（来自 Director 或 PM）
        started_at: 开始时间（可选）

    Returns:
        GoalExecutionView 派生视图
    """
    # 转换任务进度
    tasks = [TaskProgressItem.from_dict(t) for t in task_progress]

    # 计算各项指标
    stage = infer_stage(tasks)
    percent = calculate_percent(tasks)
    eta = estimate_eta(tasks)
    current = get_current_task(tasks)

    # 统计
    total = len(tasks)
    completed = len([t for t in tasks if t.status == "completed"])
    failed = len([t for t in tasks if t.status == "failed"])

    return GoalExecutionView(
        goal_id=goal_id,
        stage=stage,
        percent=percent,
        current_task=current,
        eta_minutes=eta,
        total_tasks=total,
        completed_tasks=completed,
        failed_tasks=failed,
        started_at=started_at,
        task_progress=tasks,
    )


class ExecutionProjectionService:
    """执行投影服务

    负责构建和管理 GoalExecutionView 派生视图。
    """

    def __init__(self, storage=None) -> None:
        self._storage = storage
        # 内存缓存（短期）
        self._cache: dict[str, GoalExecutionView] = {}
        self._cache_ttl_seconds = 30

    def build_projection(
        self,
        goal_id: str,
        task_progress: list[dict[str, Any]],
        started_at: str | None = None,
    ) -> GoalExecutionView:
        """构建执行投影"""
        view = build_goal_execution_projection(goal_id, task_progress, started_at)
        self._cache[goal_id] = view
        return view

    def get_cached_projection(self, goal_id: str) -> GoalExecutionView | None:
        """获取缓存的投影（如果有）"""
        return self._cache.get(goal_id)

    def invalidate_cache(self, goal_id: str) -> None:
        """使缓存失效"""
        self._cache.pop(goal_id, None)

    def build_bulk_projections(
        self,
        goals_with_tasks: list[dict[str, Any]],
    ) -> list[GoalExecutionView]:
        """批量构建执行投影"""
        results = []
        for item in goals_with_tasks:
            goal_id = item.get("goal_id", "")
            tasks = item.get("task_progress", [])
            started = item.get("started_at")
            if goal_id:
                view = self.build_projection(goal_id, tasks, started)
                results.append(view)
        return results


# 全局服务实例
_execution_projection_service: ExecutionProjectionService | None = None


def get_execution_projection_service(storage=None) -> ExecutionProjectionService:
    """获取全局 ExecutionProjectionService 实例"""
    global _execution_projection_service
    if _execution_projection_service is None:
        _execution_projection_service = ExecutionProjectionService(storage)
    return _execution_projection_service
