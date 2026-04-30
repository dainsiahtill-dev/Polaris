"""Task Orchestrator - PM Task Orchestrator

PM-maintained source of truth for task states，智能任务分配，DAG依赖管理，多维度完成验证。
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from polaris.delivery.cli.pm.state_manager import (
    _now_iso,
    get_state_manager,
)

logger = logging.getLogger("polaris.task_orchestrator")


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"  # 待分配
    ASSIGNED = "assigned"  # 已分配
    IN_PROGRESS = "in_progress"  # 执行中
    REVIEW = "review"  # 审核中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    BLOCKED = "blocked"  # 阻塞
    CANCELLED = "cancelled"  # 已取消


class TaskPriority(str, Enum):
    """任务优先级"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AssigneeType(str, Enum):
    """执行者类型"""

    CHIEF_ENGINEER = "ChiefEngineer"
    DIRECTOR = "Director"
    PM = "PM"


@dataclass
class TaskVerification:
    """任务完成验证"""

    method: str  # 验证方法: test_passed, manual_review, auto_check
    evidence: str  # 验证证据
    verified_by: str  # 验证者
    verified_at: str = field(default_factory=_now_iso)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskAssignment:
    """任务分配记录"""

    task_id: str
    assignee: str  # 执行者ID
    assignee_type: AssigneeType
    assigned_at: str = field(default_factory=_now_iso)
    assigned_by: str = "pm"
    expected_completion: str | None = None
    notes: str = ""


@dataclass
class Task:
    """任务实体 - PM-maintained source of truth"""

    id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    assignee: str | None = None
    assignee_type: AssigneeType | None = None
    requirements: list[str] = field(default_factory=list)  # 关联需求IDs
    dependencies: list[str] = field(default_factory=list)  # 依赖任务IDs
    estimated_effort: int = 0  # 预估工作量 (分钟)
    actual_effort: int = 0  # 实际工作量
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    assigned_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    verification: TaskVerification | None = None
    result_summary: str = ""  # 执行结果摘要
    artifacts: list[str] = field(default_factory=list)  # 产出物
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutorStats:
    """执行者统计"""

    executor_id: str
    executor_type: AssigneeType
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    avg_completion_time: float = 0.0  # 平均完成时间(分钟)
    success_rate: float = 0.0  # 成功率
    current_load: int = 0  # 当前负载
    last_assigned_at: str | None = None


class TaskOrchestrator:
    """PM Task Orchestrator

    核心功能：
    1. 任务注册表 - PM-maintained sole source of truth
    2. 智能任务分配 - 基于执行者负载和能力
    3. DAG依赖解析 - 任务依赖图管理
    4. 任务完成验证 - 多维度验证
    5. 执行者性能追踪 - 历史成功率分析

    存储结构：
        pm_data/tasks/
        ├── registry.json          # 任务注册表（真相源）
        ├── assignments.json       # 任务分配记录
        ├── completions.json       # 任务完成验证
        └── stats.json             # 任务统计
    """

    REGISTRY_FILE = "registry.json"
    ASSIGNMENTS_FILE = "assignments.json"
    COMPLETIONS_FILE = "completions.json"
    STATS_FILE = "stats.json"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.state_manager = get_state_manager(workspace)
        self._task_lock = threading.RLock()
        self._ensure_initialized()
        self._executor_stats_cache: dict[str, ExecutorStats] = {}

    def _ensure_initialized(self) -> None:
        """Ensure tasks subsystem is initialized."""
        if not self.state_manager.is_initialized():
            self.state_manager.initialize()

    def _load_registry(self) -> dict[str, Any]:
        """Load task registry (source of truth)."""
        data = self.state_manager.read_subsystem_data("tasks", self.REGISTRY_FILE)
        if data is None:
            return {
                "version": "1.0",
                "tasks": {},
                "stats": {
                    "total": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "pending": 0,
                    "failed": 0,
                    "blocked": 0,
                },
                "next_id": 1,
            }
        return data

    def _save_registry(self, registry: dict[str, Any]) -> None:
        """Save task registry atomically."""
        self.state_manager.write_subsystem_data("tasks", self.REGISTRY_FILE, registry)

    def _load_assignments(self) -> dict[str, Any]:
        """Load task assignments."""
        data = self.state_manager.read_subsystem_data("tasks", self.ASSIGNMENTS_FILE)
        if data is None:
            return {"version": "1.0", "assignments": []}
        return data

    def _save_assignments(self, assignments: dict[str, Any]) -> None:
        """Save task assignments."""
        self.state_manager.write_subsystem_data("tasks", self.ASSIGNMENTS_FILE, assignments)

    def _load_stats(self) -> dict[str, Any]:
        """Load executor stats."""
        data = self.state_manager.read_subsystem_data("tasks", self.STATS_FILE)
        if data is None:
            return {"version": "1.0", "executors": {}}
        return data

    def _save_stats(self, stats: dict[str, Any]) -> None:
        """Save executor stats."""
        self.state_manager.write_subsystem_data("tasks", self.STATS_FILE, stats)

    def _generate_task_id(self, registry: dict[str, Any]) -> str:
        """Generate new task ID."""
        next_num = registry.get("next_id", 1)
        task_id = f"TASK-{next_num:04d}"
        registry["next_id"] = next_num + 1
        return task_id

    def register_task(
        self,
        title: str,
        description: str,
        priority: TaskPriority = TaskPriority.MEDIUM,
        requirements: list[str] | None = None,
        dependencies: list[str] | None = None,
        estimated_effort: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Register a new task.

        Args:
            title: Task title
            description: Task description
            priority: Priority level
            requirements: Related requirement IDs
            dependencies: Task dependencies
            estimated_effort: Estimated effort in minutes
            metadata: Additional metadata

        Returns:
            Registered task
        """
        with self._task_lock:
            registry = self._load_registry()

            task_id = self._generate_task_id(registry)

            task = Task(
                id=task_id,
                title=title,
                description=description,
                status=TaskStatus.PENDING,
                priority=priority,
                requirements=requirements or [],
                dependencies=dependencies or [],
                estimated_effort=estimated_effort,
                metadata=metadata or {},
            )

            # Save to registry
            registry["tasks"][task_id] = asdict(task)
            registry["stats"]["total"] += 1
            registry["stats"]["pending"] += 1

            self._save_registry(registry)

            # Record to history
            self.state_manager.append_to_history(
                "tasks",
                {
                    "action": "register",
                    "task_id": task_id,
                    "title": title,
                    "priority": priority.value,
                },
            )

            # Update PM stats
            self.state_manager.increment_stat("total_tasks_created")

            return task

    def get_task(self, task_id: str) -> Task | None:
        """Get task by ID.

        Args:
            task_id: Task ID

        Returns:
            Task or None
        """
        with self._task_lock:
            registry = self._load_registry()
            data = registry["tasks"].get(task_id)
            if data is None:
                return None
            return Task(**data)

    def update_task(
        self,
        task_id: str,
        changed_by: str = "pm",
        **updates: Any,
    ) -> Task | None:
        """Update task fields.

        Args:
            task_id: Task ID
            changed_by: Who made the change
            **updates: Fields to update

        Returns:
            Updated task or None
        """
        with self._task_lock:
            registry = self._load_registry()
            data = registry["tasks"].get(task_id)
            if data is None:
                return None

            # Track status change
            old_status = data.get("status")

            for field, value in updates.items():
                if field in data:
                    data[field] = value

            data["updated_at"] = _now_iso()

            # Update stats if status changed
            new_status = data.get("status")
            if old_status != new_status and old_status and new_status:
                old_status_key = old_status.lower() if isinstance(old_status, str) else old_status.value
                new_status_key = new_status.lower() if isinstance(new_status, str) else new_status.value

                if old_status_key in registry["stats"]:
                    registry["stats"][old_status_key] = max(0, registry["stats"][old_status_key] - 1)
                if new_status_key in registry["stats"]:
                    registry["stats"][new_status_key] += 1

            self._save_registry(registry)

            # Record change
            self.state_manager.append_to_history(
                "tasks",
                {
                    "action": "update",
                    "task_id": task_id,
                    "changes": list(updates.keys()),
                    "changed_by": changed_by,
                },
            )

            return Task(**data)

    def assign_task(
        self,
        task_id: str,
        assignee: str,
        assignee_type: AssigneeType,
        assigned_by: str = "pm",
        expected_completion: str | None = None,
        notes: str = "",
    ) -> Task | None:
        """Assign task to an executor.

        Args:
            task_id: Task ID
            assignee: Executor ID
            assignee_type: Type of executor
            assigned_by: Who assigned
            expected_completion: Expected completion time
            notes: Assignment notes

        Returns:
            Updated task or None
        """
        with self._task_lock:
            task = self.get_task(task_id)
            if task is None:
                return None

            # Check if dependencies are satisfied
            deps_satisfied, blocked_deps = self._check_dependencies_satisfied(task_id)
            if not deps_satisfied:
                # Update status to blocked
                self.update_task(
                    task_id,
                    assigned_by,
                    status=TaskStatus.BLOCKED,
                    metadata={"blocked_reason": f"Dependencies not satisfied: {blocked_deps}"},
                )
                return self.get_task(task_id)

            # Update task
            updates = {
                "status": TaskStatus.ASSIGNED,
                "assignee": assignee,
                "assignee_type": assignee_type,
                "assigned_at": _now_iso(),
            }

            task = self.update_task(task_id, assigned_by, **updates)

            if task:
                # Record assignment
                assignment = TaskAssignment(
                    task_id=task_id,
                    assignee=assignee,
                    assignee_type=assignee_type,
                    assigned_by=assigned_by,
                    expected_completion=expected_completion,
                    notes=notes,
                )

                assignments = self._load_assignments()
                assignments["assignments"].append(asdict(assignment))
                self._save_assignments(assignments)

                # Update executor stats
                self._update_executor_load(assignee, assignee_type, 1)

                # Record to history
                self.state_manager.append_to_history(
                    "tasks",
                    {
                        "action": "assign",
                        "task_id": task_id,
                        "assignee": assignee,
                        "assignee_type": assignee_type.value,
                    },
                )

            return task

    def start_task(self, task_id: str, executor: str) -> Task | None:
        """Mark task as started.

        Args:
            task_id: Task ID
            executor: Executor ID

        Returns:
            Updated task or None
        """
        task = self.get_task(task_id)
        if task is None:
            return None

        if task.assignee != executor:
            return None

        return self.update_task(
            task_id,
            executor,
            status=TaskStatus.IN_PROGRESS,
            started_at=_now_iso(),
        )

    def complete_task(
        self,
        task_id: str,
        executor: str,
        verification: TaskVerification,
        result_summary: str = "",
        artifacts: list[str] | None = None,
    ) -> Task | None:
        """Complete a task with verification.

        Key method for PM to maintain truth. Only verified tasks can be marked as completed.

        Args:
            task_id: Task ID
            executor: Executor ID
            verification: Completion verification
            result_summary: Result summary
            artifacts: Generated artifacts

        Returns:
            Updated task or None
        """
        task = self.get_task(task_id)
        if task is None:
            return None

        if task.assignee != executor:
            return None

        # Validate verification
        if not self._validate_verification(verification):
            return None

        # Update task
        updates = {
            "status": TaskStatus.COMPLETED,
            "completed_at": _now_iso(),
            "verification": asdict(verification),
            "result_summary": result_summary,
            "artifacts": artifacts or [],
        }

        task = self.update_task(task_id, executor, **updates)

        if task:
            # Update executor stats
            if task.assignee_type:
                self._update_executor_stats(task, True)

            # Update PM stats
            self.state_manager.increment_stat("total_tasks_completed")
            self.state_manager.update_stats(last_task_completed_at=_now_iso())

            # Record completion
            self.state_manager.append_to_history(
                "tasks",
                {
                    "action": "complete",
                    "task_id": task_id,
                    "executor": executor,
                    "verification_method": verification.method,
                },
            )

            # Update related requirements
            self._update_requirements_on_task_complete(task)

        return task

    def fail_task(
        self,
        task_id: str,
        executor: str,
        reason: str,
        retry_allowed: bool = True,
    ) -> Task | None:
        """Mark task as failed.

        Args:
            task_id: Task ID
            executor: Executor ID
            reason: Failure reason
            retry_allowed: Whether retry is allowed

        Returns:
            Updated task or None
        """
        task = self.get_task(task_id)
        if task is None:
            return None

        updates = {
            "status": TaskStatus.FAILED,
            "result_summary": reason,
            "metadata": {**task.metadata, "retry_allowed": retry_allowed, "failure_reason": reason},
        }

        task = self.update_task(task_id, executor, **updates)

        if task and task.assignee_type:
            self._update_executor_stats(task, False)

        # Update PM stats
        self.state_manager.increment_stat("total_tasks_failed")

        return task

    def block_task(self, task_id: str, reason: str) -> Task | None:
        """Block a task.

        Args:
            task_id: Task ID
            reason: Block reason

        Returns:
            Updated task or None
        """
        return self.update_task(
            task_id,
            "pm",
            status=TaskStatus.BLOCKED,
            metadata={"blocked_reason": reason},
        )

    def unblock_task(self, task_id: str) -> Task | None:
        """Unblock a task.

        Args:
            task_id: Task ID

        Returns:
            Updated task or None
        """
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.BLOCKED:
            return None

        # Check dependencies
        deps_satisfied, _ = self._check_dependencies_satisfied(task_id)
        if not deps_satisfied:
            return task

        new_status = TaskStatus.PENDING if not task.assignee else TaskStatus.ASSIGNED
        return self.update_task(
            task_id,
            "pm",
            status=new_status,
            metadata={"blocked_reason": None},
        )

    def _check_dependencies_satisfied(self, task_id: str) -> tuple[bool, list[str]]:
        """Check if task dependencies are satisfied.

        Returns:
            (is_satisfied, list_of_unsatisfied_deps)
        """
        task = self.get_task(task_id)
        if not task:
            return False, []

        unsatisfied = []
        for dep_id in task.dependencies:
            dep_task = self.get_task(dep_id)
            if dep_task is None or dep_task.status != TaskStatus.COMPLETED:
                unsatisfied.append(dep_id)

        return len(unsatisfied) == 0, unsatisfied

    def _validate_verification(self, verification: TaskVerification) -> bool:
        """Validate task completion verification."""
        if not verification.method:
            return False

        valid_methods = ["test_passed", "manual_review", "auto_check", "code_review"]
        if verification.method not in valid_methods:
            return False

        return bool(verification.evidence)

    def _update_executor_stats(self, task: Task, success: bool) -> None:
        """Update executor statistics."""
        if not task.assignee or not task.assignee_type:
            return

        stats = self._load_stats()
        assignee_type_str = (
            task.assignee_type.value if hasattr(task.assignee_type, "value") else str(task.assignee_type)
        )
        executor_key = f"{assignee_type_str}:{task.assignee}"

        if executor_key not in stats["executors"]:
            stats["executors"][executor_key] = {
                "executor_id": task.assignee,
                "executor_type": assignee_type_str,
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "avg_completion_time": 0.0,
                "success_rate": 0.0,
            }

        exec_stats = stats["executors"][executor_key]
        exec_stats["total_tasks"] += 1

        if success:
            exec_stats["completed_tasks"] += 1
        else:
            exec_stats["failed_tasks"] += 1

        # Calculate success rate
        total = exec_stats["total_tasks"]
        completed = exec_stats["completed_tasks"]
        exec_stats["success_rate"] = round(completed / total, 2) if total > 0 else 0.0

        # Calculate average completion time
        if success and task.started_at and task.completed_at:
            try:
                start = datetime.fromisoformat(task.started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(task.completed_at.replace("Z", "+00:00"))
                duration = (end - start).total_seconds() / 60  # minutes

                old_avg = exec_stats["avg_completion_time"]
                count = exec_stats["completed_tasks"]
                exec_stats["avg_completion_time"] = round((old_avg * (count - 1) + duration) / count, 2)
            except (RuntimeError, ValueError) as exc:
                logger.debug("datetime parse failed for avg completion time (non-critical): %s", exc)

        self._save_stats(stats)
        self._executor_stats_cache[executor_key] = ExecutorStats(**exec_stats)

    def _update_executor_load(self, executor: str, executor_type: AssigneeType, delta: int) -> None:
        """Update executor current load."""
        executor_key = f"{executor_type.value}:{executor}"

        if executor_key in self._executor_stats_cache:
            self._executor_stats_cache[executor_key].current_load += delta

    def _update_requirements_on_task_complete(self, task: Task) -> None:
        """Update requirement status when task completes."""
        from polaris.delivery.cli.pm.requirements_tracker import RequirementStatus, get_requirements_tracker

        try:
            tracker = get_requirements_tracker(self.workspace)

            for req_id in task.requirements:
                req = tracker.get_requirement(req_id)
                if req and req.status == RequirementStatus.IN_PROGRESS:
                    # Check if all tasks for this requirement are complete
                    all_complete = all(
                        (t := self.get_task(t_id)) is not None and t.status == TaskStatus.COMPLETED
                        for t_id in req.tasks
                    )
                    if all_complete:
                        tracker.update_status(req_id, RequirementStatus.IMPLEMENTED, "pm")
        except (RuntimeError, ValueError) as exc:
            logger.debug("requirement auto-mark implemented failed (non-critical): %s", exc)

    def auto_assign_task(
        self,
        task_id: str,
        preferred_type: AssigneeType | None = None,
    ) -> Task | None:
        """Auto-assign task based on executor performance and load.

        Args:
            task_id: Task ID
            preferred_type: Preferred executor type

        Returns:
            Assigned task or None
        """
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return None

        # Get available executors
        executors = self._get_available_executors(preferred_type)

        if not executors:
            return None

        # Score executors
        best_executor = None
        best_score = -1.0

        for executor_key, stats in executors:
            score = self._score_executor_for_task(stats, task)
            if score > best_score:
                best_score = score
                best_executor = (executor_key, stats)

        if best_executor:
            executor_key, stats = best_executor
            return self.assign_task(
                task_id,
                stats.executor_id,
                stats.executor_type,
                "orchestrator",
                notes=f"Auto-assigned based on score: {best_score:.2f}",
            )

        return None

    def _get_available_executors(self, preferred_type: AssigneeType | None = None) -> list[tuple[str, ExecutorStats]]:
        """Get list of available executors."""
        stats = self._load_stats()
        executors = []

        for key, data in stats.get("executors", {}).items():
            executor_type = AssigneeType(data["executor_type"])

            if preferred_type and executor_type != preferred_type:
                continue

            stats_obj = ExecutorStats(**data)
            executors.append((key, stats_obj))

        # If no executors found, create default ones
        if not executors:
            default_executors = [
                ("ChiefEngineer:default", ExecutorStats("default", AssigneeType.CHIEF_ENGINEER)),
                ("Director:default", ExecutorStats("default", AssigneeType.DIRECTOR)),
            ]
            executors = default_executors

        return executors

    def _score_executor_for_task(self, stats: ExecutorStats, task: Task) -> float:
        """Score an executor for a task."""
        # Factors:
        # 1. Success rate (40%)
        # 2. Low current load (30%)
        # 3. Average completion time (20%)
        # 4. Total experience (10%)

        success_score = stats.success_rate * 0.4

        # Load score: lower is better, max 1.0 at 0 load
        load_score = max(0, 1.0 - stats.current_load * 0.1) * 0.3

        # Time score: faster is better, normalize assuming 60min is average
        time_score = (
            min(1.0, 60.0 / stats.avg_completion_time) * 0.2
            if stats.avg_completion_time > 0
            else 0.1  # Unknown, give small score
        )

        # Experience score
        exp_score = min(1.0, stats.total_tasks / 10.0) * 0.1

        return success_score + load_score + time_score + exp_score

    def get_ready_tasks(self) -> list[Task]:
        """Get tasks that are ready to be assigned (dependencies satisfied)."""
        registry = self._load_registry()
        ready = []

        for task_data in registry["tasks"].values():
            if task_data["status"] == TaskStatus.PENDING.value:
                task_id = task_data["id"]
                deps_satisfied, _ = self._check_dependencies_satisfied(task_id)
                if deps_satisfied:
                    ready.append(Task(**task_data))

        return ready

    def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        """Get tasks by status."""
        registry = self._load_registry()
        return [Task(**data) for data in registry["tasks"].values() if data["status"] == status.value]

    def get_tasks_by_assignee(self, assignee: str) -> list[Task]:
        """Get tasks assigned to an executor."""
        registry = self._load_registry()
        return [Task(**data) for data in registry["tasks"].values() if data.get("assignee") == assignee]

    def get_dependency_graph(self) -> dict[str, list[str]]:
        """Get task dependency graph."""
        registry = self._load_registry()
        graph = {}

        for task_id, task_data in registry["tasks"].items():
            graph[task_id] = task_data.get("dependencies", [])

        return graph

    def topological_sort(self) -> list[str]:
        """Get tasks in dependency order (topological sort)."""
        graph = self.get_dependency_graph()

        # Kahn's algorithm
        in_degree = defaultdict(int)
        for task_id in graph:
            in_degree[task_id] = 0

        for deps in graph.values():
            for dep in deps:
                in_degree[dep] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            # Sort by priority
            queue.sort(key=self._get_task_priority_value, reverse=True)
            task_id = queue.pop(0)
            result.append(task_id)

            # Find tasks that depend on this one
            for tid, deps in graph.items():
                if task_id in deps:
                    in_degree[tid] -= 1
                    if in_degree[tid] == 0:
                        queue.append(tid)

        return result

    def _get_task_priority_value(self, task_id: str) -> int:
        """Get numeric priority value for sorting."""
        task = self.get_task(task_id)
        if task is None:
            return 0

        priority_map = {
            TaskPriority.CRITICAL: 4,
            TaskPriority.HIGH: 3,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 1,
        }
        return priority_map.get(task.priority, 0)

    def get_executor_stats(self, executor: str, executor_type: AssigneeType) -> ExecutorStats | None:
        """Get statistics for an executor."""
        executor_key = f"{executor_type.value}:{executor}"

        if executor_key in self._executor_stats_cache:
            return self._executor_stats_cache[executor_key]

        stats = self._load_stats()
        data = stats["executors"].get(executor_key)
        if data:
            return ExecutorStats(**data)

        return None

    def get_all_executor_stats(self) -> dict[str, ExecutorStats]:
        """Get statistics for all executors."""
        stats = self._load_stats()
        return {key: ExecutorStats(**data) for key, data in stats.get("executors", {}).items()}

    def get_stats_summary(self) -> dict[str, Any]:
        """Get task statistics summary."""
        registry = self._load_registry()
        return {
            **registry["stats"],
            "completion_rate": self._calculate_completion_rate(registry["stats"]),
            "avg_tasks_per_executor": self._calculate_avg_load(),
        }

    def _calculate_completion_rate(self, stats: dict[str, int]) -> float:
        """Calculate task completion rate."""
        total = stats.get("total", 0)
        completed = stats.get("completed", 0)
        return round(completed / total, 2) if total > 0 else 0.0

    def _calculate_avg_load(self) -> float:
        """Calculate average tasks per executor."""
        executors = self.get_all_executor_stats()
        if not executors:
            return 0.0

        total_load = sum(e.current_load for e in executors.values())
        return round(total_load / len(executors), 2)

    def retry_task(self, task_id: str, reason: str = "") -> Task | None:
        """Retry a failed task.

        Args:
            task_id: Task ID
            reason: Retry reason

        Returns:
            Updated task or None
        """
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.FAILED:
            return None

        # Check if retry is allowed
        if not task.metadata.get("retry_allowed", True):
            return None

        # Reset task status
        updates = {
            "status": TaskStatus.PENDING,
            "assignee": None,
            "assignee_type": None,
            "started_at": None,
            "completed_at": None,
            "verification": None,
            "result_summary": f"Retry: {reason}" if reason else "Retry",
        }

        return self.update_task(task_id, "pm", **updates)

    def bulk_register_tasks(self, tasks_data: list[dict[str, Any]]) -> list[Task]:
        """Bulk register tasks.

        Args:
            tasks_data: List of task data dictionaries

        Returns:
            List of registered tasks
        """
        tasks = []
        for data in tasks_data:
            task = self.register_task(
                title=data["title"],
                description=data.get("description", ""),
                priority=TaskPriority(data.get("priority", "medium")),
                requirements=data.get("requirements", []),
                dependencies=data.get("dependencies", []),
                estimated_effort=data.get("estimated_effort", 0),
                metadata=data.get("metadata", {}),
            )
            tasks.append(task)
        return tasks

    def get_task_history(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        assignee_type: AssigneeType | None = None,
        status: TaskStatus | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get task history with filtering and pagination.

        Args:
            task_id: Filter by specific task ID
            assignee: Filter by assignee
            assignee_type: Filter by assignee type
            status: Filter by status
            start_date: Start date filter (ISO format)
            end_date: End date filter (ISO format)
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with tasks and pagination info
        """
        registry = self._load_registry()
        tasks = []

        for task_data in registry["tasks"].values():
            # Apply filters
            if task_id and task_data["id"] != task_id:
                continue
            if assignee and task_data.get("assignee") != assignee:
                continue
            if assignee_type and task_data.get("assignee_type") != assignee_type.value:
                continue
            if status and task_data["status"] != status.value:
                continue

            # Date filtering
            created_at = task_data.get("created_at", "")
            if start_date and created_at < start_date:
                continue
            if end_date and created_at > end_date:
                continue

            tasks.append(dict(task_data))

        # Sort by created_at descending
        tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        total = len(tasks)
        paginated = tasks[offset : offset + limit]

        return {
            "tasks": paginated,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    def get_director_task_history(
        self,
        iteration: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get tasks that were dispatched to Director.

        This is specifically for retrieving the task list sent to Director
        in each orchestration iteration.

        Args:
            iteration: Filter by specific PM iteration number
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dictionary with director tasks and pagination info
        """
        registry = self._load_registry()
        director_tasks = []

        for task_data in registry["tasks"].values():
            # Check if task was assigned to Director
            assignee_type = task_data.get("assignee_type")
            if assignee_type != AssigneeType.DIRECTOR.value:
                continue

            # Filter by iteration if provided
            if iteration is not None:
                metadata = task_data.get("metadata", {})
                task_iteration = metadata.get("pm_iteration")
                if task_iteration != iteration:
                    continue

            director_tasks.append(dict(task_data))

        # Sort by assigned_at descending
        director_tasks.sort(key=lambda x: x.get("assigned_at") or x.get("created_at", ""), reverse=True)

        total = len(director_tasks)
        paginated = director_tasks[offset : offset + limit]

        return {
            "tasks": paginated,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    def get_task_assignments(
        self,
        task_id: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get task assignment history.

        Args:
            task_id: Filter by task ID
            assignee: Filter by assignee
            limit: Maximum results

        Returns:
            List of assignment records
        """
        assignments = self._load_assignments()
        records = assignments.get("assignments", [])

        filtered = records
        if task_id:
            filtered = [r for r in filtered if r.get("task_id") == task_id]
        if assignee:
            filtered = [r for r in filtered if r.get("assignee") == assignee]

        # Sort by assigned_at descending
        filtered.sort(key=lambda x: x.get("assigned_at", ""), reverse=True)

        return filtered[:limit]

    def search_tasks(
        self,
        query: str,
        search_description: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search tasks by title or description.

        Args:
            query: Search query
            search_description: Whether to search in description
            limit: Maximum results

        Returns:
            List of matching tasks with scores
        """
        registry = self._load_registry()
        results = []
        query_lower = query.lower()

        for task_data in registry["tasks"].values():
            score = 0.0
            matches = []

            # Title match (higher weight)
            title = task_data.get("title", "").lower()
            if query_lower in title:
                score += 2.0
                matches.append("title")
                # Boost for exact match
                if query_lower == title:
                    score += 1.0

            # Description match
            if search_description:
                description = task_data.get("description", "").lower()
                if query_lower in description:
                    score += 1.0
                    matches.append("description")

            if score > 0:
                result = dict(task_data)
                result["_search_score"] = round(score, 2)
                result["_search_matches"] = matches
                results.append(result)

        # Sort by score
        results.sort(key=lambda x: x["_search_score"], reverse=True)
        return results[:limit]


# Global instance cache
_orchestrator_instances: dict[str, TaskOrchestrator] = {}
_orchestrator_lock = threading.RLock()


def get_task_orchestrator(workspace: str) -> TaskOrchestrator:
    """Get or create TaskOrchestrator instance."""
    workspace_abs = os.path.abspath(workspace)

    # 快速路径 - 无锁检查
    instance = _orchestrator_instances.get(workspace_abs)
    if instance is not None:
        return instance

    # 慢速路径 - 加锁创建
    with _orchestrator_lock:
        if workspace_abs not in _orchestrator_instances:
            _orchestrator_instances[workspace_abs] = TaskOrchestrator(workspace_abs)
        return _orchestrator_instances[workspace_abs]


def reset_task_orchestrator(workspace: str) -> None:
    """Reset orchestrator instance for workspace."""
    workspace_abs = os.path.abspath(workspace)
    with _orchestrator_lock:
        _orchestrator_instances.pop(workspace_abs, None)
