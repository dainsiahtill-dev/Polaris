"""Execution Tracker - 尚书令执行追踪器

执行历史归档、执行者性能分析、预测性调度。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from polaris.delivery.cli.pm.state_manager import (
    _generate_id,
    _now_iso,
    get_state_manager,
)
from polaris.delivery.cli.pm.task_orchestrator import (
    AssigneeType,
    TaskOrchestrator,
    TaskStatus,
    get_task_orchestrator,
)

logger = logging.getLogger("polaris.execution_tracker")


@dataclass
class ExecutionRecord:
    """执行记录"""

    execution_id: str
    task_id: str
    executor: str
    executor_type: str
    started_at: str
    completed_at: str | None = None
    status: str = "running"  # running, completed, failed, cancelled
    duration_minutes: float = 0.0
    result_summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutorPerformance:
    """执行者性能分析"""

    executor_id: str
    executor_type: str
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    success_rate: float = 0.0
    avg_duration: float = 0.0
    min_duration: float = 0.0
    max_duration: float = 0.0
    std_duration: float = 0.0
    trend: str = "stable"  # improving, degrading, stable
    reliability_score: float = 0.0  # 0-1
    velocity_score: float = 0.0  # 0-1


@dataclass
class ExecutionTrend:
    """执行趋势分析"""

    period: str  # daily, weekly, monthly
    start_date: str
    end_date: str
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    avg_duration: float = 0.0
    completion_rate: float = 0.0
    velocity_trend: str = "stable"
    quality_trend: str = "stable"


class ExecutionTracker:
    """尚书令执行追踪器

    核心功能：
    1. 执行历史归档 - 完整的执行记录
    2. 执行者性能分析 - ChiefEngineer/Director成功率
    3. 预测性调度 - 基于历史预测最佳执行者
    4. 趋势分析 - 执行效率趋势
    5. 瓶颈识别 - 识别执行瓶颈

    存储结构：
        pm_data/execution/
        ├── stats.json             # 执行者统计
        ├── performance.json       # 性能分析
        └── history/               # 执行历史
            └── YYYY-MM-DD.jsonl
    """

    STATS_FILE = "stats.json"
    PERFORMANCE_FILE = "performance.json"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.state_manager = get_state_manager(workspace)
        self._ensure_initialized()
        self._orchestrator: TaskOrchestrator | None = None

    def _ensure_initialized(self) -> None:
        """Ensure execution subsystem is initialized."""
        if not self.state_manager.is_initialized():
            self.state_manager.initialize()

    def _get_orchestrator(self) -> TaskOrchestrator:
        """Get task orchestrator instance."""
        if self._orchestrator is None:
            self._orchestrator = get_task_orchestrator(self.workspace)
        return self._orchestrator

    def _load_stats(self) -> dict[str, Any]:
        """Load execution stats."""
        data = self.state_manager.read_subsystem_data("execution", self.STATS_FILE)
        if data is None:
            return {
                "version": "1.0",
                "executors": {},
                "history_summary": {
                    "total_runs": 0,
                    "successful_runs": 0,
                    "failed_runs": 0,
                },
            }
        return data

    def _save_stats(self, stats: dict[str, Any]) -> None:
        """Save execution stats."""
        self.state_manager.write_subsystem_data("execution", self.STATS_FILE, stats)

    def _load_performance(self) -> dict[str, Any]:
        """Load performance data."""
        data = self.state_manager.read_subsystem_data("execution", self.PERFORMANCE_FILE)
        if data is None:
            return {
                "version": "1.0",
                "executor_performance": {},
                "trends": {},
            }
        return data

    def _save_performance(self, perf: dict[str, Any]) -> None:
        """Save performance data."""
        self.state_manager.write_subsystem_data("execution", self.PERFORMANCE_FILE, perf)

    def record_execution_start(
        self,
        task_id: str,
        executor: str,
        executor_type: AssigneeType,
        metrics: dict[str, Any] | None = None,
    ) -> ExecutionRecord:
        """Record execution start.

        Args:
            task_id: Task ID
            executor: Executor ID
            executor_type: Type of executor
            metrics: Initial metrics

        Returns:
            Execution record
        """
        execution_id = _generate_id("EXEC")

        record = ExecutionRecord(
            execution_id=execution_id,
            task_id=task_id,
            executor=executor,
            executor_type=executor_type.value,
            started_at=_now_iso(),
            status="running",
            metrics=metrics or {},
        )

        # Append to history
        self.state_manager.append_to_history(
            "execution",
            {
                **asdict(record),
                "event": "start",
            },
        )

        return record

    def record_execution_complete(
        self,
        execution_id: str,
        task_id: str,
        status: str,
        result_summary: str = "",
        artifacts: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> ExecutionRecord:
        """Record execution completion.

        Args:
            execution_id: Execution ID
            task_id: Task ID
            status: Final status
            result_summary: Result summary
            artifacts: Generated artifacts
            metrics: Final metrics

        Returns:
            Updated execution record
        """
        completed_at = _now_iso()

        # Calculate duration
        duration = 0.0
        try:
            # Find start time from history
            history = self.state_manager.read_history("execution", limit=1000)
            for history_record in history:
                if history_record.get("execution_id") == execution_id and history_record.get("event") == "start":
                    start = datetime.fromisoformat(history_record["started_at"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                    duration = (end - start).total_seconds() / 60
                    break
        except (RuntimeError, ValueError) as exc:
            logger.debug("datetime parse failed for duration calc (non-critical): %s", exc)

        record: ExecutionRecord = ExecutionRecord(
            execution_id=execution_id,
            task_id=task_id,
            executor="",  # Will be filled from start record
            executor_type="",
            started_at="",  # Will be filled from start record
            completed_at=completed_at,
            status=status,
            duration_minutes=duration,
            result_summary=result_summary,
            artifacts=artifacts or [],
            metrics=metrics or {},
        )

        # Append to history
        self.state_manager.append_to_history(
            "execution",
            {
                **asdict(record),
                "event": "complete",
            },
        )

        # Update stats
        self._update_execution_stats(record)

        return record

    def _update_execution_stats(self, record: ExecutionRecord) -> None:
        """Update execution statistics."""
        stats = self._load_stats()

        # Update history summary
        stats["history_summary"]["total_runs"] += 1
        if record.status == "completed":
            stats["history_summary"]["successful_runs"] += 1
        elif record.status == "failed":
            stats["history_summary"]["failed_runs"] += 1

        self._save_stats(stats)

    def analyze_executor_performance(
        self,
        executor: str,
        executor_type: AssigneeType,
        days: int = 30,
    ) -> ExecutorPerformance:
        """Analyze executor performance.

        Args:
            executor: Executor ID
            executor_type: Type of executor
            days: Analysis period in days

        Returns:
            Performance analysis
        """
        # Get execution history
        records = self._get_executor_history(executor, executor_type, days)

        if not records:
            return ExecutorPerformance(
                executor_id=executor,
                executor_type=executor_type.value,
            )

        # Calculate metrics
        total = len(records)
        successful = len([r for r in records if r.status == "completed"])
        failed = len([r for r in records if r.status == "failed"])

        durations = [r.duration_minutes for r in records if r.duration_minutes > 0]

        avg_duration = statistics.mean(durations) if durations else 0.0
        min_duration = min(durations) if durations else 0.0
        max_duration = max(durations) if durations else 0.0
        std_duration = statistics.stdev(durations) if len(durations) > 1 else 0.0

        success_rate = successful / total if total > 0 else 0.0

        # Calculate trend
        trend = self._calculate_trend(records)

        # Calculate scores
        reliability = self._calculate_reliability_score(success_rate, std_duration, avg_duration)
        velocity = self._calculate_velocity_score(avg_duration, min_duration)

        return ExecutorPerformance(
            executor_id=executor,
            executor_type=executor_type.value,
            total_executions=total,
            successful_executions=successful,
            failed_executions=failed,
            success_rate=round(success_rate, 2),
            avg_duration=round(avg_duration, 2),
            min_duration=round(min_duration, 2),
            max_duration=round(max_duration, 2),
            std_duration=round(std_duration, 2),
            trend=trend,
            reliability_score=round(reliability, 2),
            velocity_score=round(velocity, 2),
        )

    def _get_executor_history(
        self,
        executor: str,
        executor_type: AssigneeType,
        days: int,
    ) -> list[ExecutionRecord]:
        """Get execution history for an executor."""
        records = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Read recent history
        for i in range(days + 1):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            history = self.state_manager.read_history("execution", date, limit=1000)

            for record in history:
                if (
                    record.get("executor") == executor
                    and record.get("executor_type") == executor_type.value
                    and record.get("event") == "complete"
                ):
                    try:
                        completed_at = datetime.fromisoformat(record.get("completed_at", "").replace("Z", "+00:00"))
                        if completed_at >= cutoff:
                            records.append(ExecutionRecord(**record))
                    except (RuntimeError, ValueError) as exc:
                        logger.debug("datetime parse failed for recent executions (non-critical): %s", exc)

        return records

    def _calculate_trend(self, records: list[ExecutionRecord]) -> str:
        """Calculate performance trend."""
        if len(records) < 10:
            return "stable"

        # Split into two halves
        mid = len(records) // 2
        first_half = records[:mid]
        second_half = records[mid:]

        # Compare success rates
        first_success = len([r for r in first_half if r.status == "completed"]) / len(first_half)
        second_success = len([r for r in second_half if r.status == "completed"]) / len(second_half)

        diff = second_success - first_success
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "degrading"
        return "stable"

    def _calculate_reliability_score(self, success_rate: float, std_duration: float, avg_duration: float) -> float:
        """Calculate reliability score (0-1)."""
        # Success rate contributes 60%
        success_score = success_rate * 0.6

        # Consistency contributes 40%
        if avg_duration > 0:
            cv = std_duration / avg_duration  # Coefficient of variation
            consistency_score = max(0, 1 - cv) * 0.4
        else:
            consistency_score = 0.2

        return min(1.0, success_score + consistency_score)

    def _calculate_velocity_score(self, avg_duration: float, min_duration: float) -> float:
        """Calculate velocity score (0-1)."""
        if avg_duration <= 0:
            return 0.0

        # Assume 60 minutes is average, 10 minutes is excellent
        if avg_duration <= 10:
            return 1.0
        elif avg_duration >= 120:
            return 0.1
        else:
            # Linear scale between 10 and 120 minutes
            return 1.0 - (avg_duration - 10) / 110

    def predict_best_executor(
        self,
        task_type: str = "",
        preferred_type: AssigneeType | None = None,
    ) -> tuple[str, AssigneeType, float] | None:
        """Predict best executor for a task.

        Args:
            task_type: Type of task
            preferred_type: Preferred executor type

        Returns:
            (executor_id, executor_type, confidence_score) or None
        """
        stats = self._load_stats()

        candidates = []
        for key, data in stats.get("executors", {}).items():
            exec_type = AssigneeType(data.get("executor_type", "Director"))

            if preferred_type and exec_type != preferred_type:
                continue

            # Calculate composite score
            reliability = data.get("reliability_score", 0.5)
            velocity = data.get("velocity_score", 0.5)
            load = data.get("current_load", 0)

            # Load penalty
            load_penalty = min(0.3, load * 0.05)

            score = (reliability * 0.5 + velocity * 0.3) - load_penalty
            candidates.append((data.get("executor_id"), exec_type, score))

        if not candidates:
            return None

        # Sort by score
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[0]

    def analyze_trends(self, period: str = "weekly", count: int = 4) -> list[ExecutionTrend]:
        """Analyze execution trends over time.

        Args:
            period: Period type (daily, weekly, monthly)
            count: Number of periods to analyze

        Returns:
            List of trend data
        """
        trends = []

        for i in range(count):
            if period == "daily":
                start = datetime.now(timezone.utc) - timedelta(days=i + 1)
                end = datetime.now(timezone.utc) - timedelta(days=i)
            elif period == "weekly":
                start = datetime.now(timezone.utc) - timedelta(weeks=i + 1)
                end = datetime.now(timezone.utc) - timedelta(weeks=i)
            else:  # monthly
                start = datetime.now(timezone.utc) - timedelta(days=30 * (i + 1))
                end = datetime.now(timezone.utc) - timedelta(days=30 * i)

            trend = self._analyze_period(start, end, period)
            trends.append(trend)

        return trends

    def _analyze_period(self, start: datetime, end: datetime, period: str) -> ExecutionTrend:
        """Analyze a specific time period."""
        records = []

        # Collect records in period
        current = start
        while current < end:
            date_str = current.strftime("%Y-%m-%d")
            history = self.state_manager.read_history("execution", date_str, limit=1000)

            for record in history:
                if record.get("event") == "complete":
                    try:
                        completed_at = datetime.fromisoformat(record.get("completed_at", "").replace("Z", "+00:00"))
                        if start <= completed_at < end:
                            records.append(ExecutionRecord(**record))
                    except (RuntimeError, ValueError) as exc:
                        logger.debug("datetime parse failed for range query (non-critical): %s", exc)

            current += timedelta(days=1)

        # Calculate metrics
        total = len(records)
        completed = len([r for r in records if r.status == "completed"])
        failed = len([r for r in records if r.status == "failed"])

        durations = [r.duration_minutes for r in records if r.duration_minutes > 0]
        avg_duration = statistics.mean(durations) if durations else 0.0

        completion_rate = completed / total if total > 0 else 0.0

        return ExecutionTrend(
            period=period,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            avg_duration=round(avg_duration, 2),
            completion_rate=round(completion_rate, 2),
        )

    def identify_bottlenecks(self, days: int = 7) -> list[dict[str, Any]]:
        """Identify execution bottlenecks.

        Args:
            days: Analysis period

        Returns:
            List of bottlenecks
        """
        bottlenecks: list[dict[str, Any]] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Collect all records
        all_records: list[ExecutionRecord] = []
        for i in range(days):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            history = self.state_manager.read_history("execution", date, limit=1000)
            for record in history:
                if record.get("event") == "complete":
                    try:
                        completed_at = datetime.fromisoformat(record.get("completed_at", "").replace("Z", "+00:00"))
                        if completed_at >= cutoff:
                            all_records.append(ExecutionRecord(**record))
                    except (RuntimeError, ValueError) as exc:
                        logger.debug("datetime parse failed for performance by executor (non-critical): %s", exc)

        # Analyze by executor
        by_executor: dict[str, list[ExecutionRecord]] = {}
        for exec_record in all_records:
            key = f"{exec_record.executor_type}:{exec_record.executor}"
            if key not in by_executor:
                by_executor[key] = []
            by_executor[key].append(exec_record)

        for key, records in by_executor.items():
            failed = [r for r in records if r.status == "failed"]
            if len(failed) > len(records) * 0.3:  # >30% failure rate
                bottlenecks.append(
                    {
                        "type": "high_failure_rate",
                        "executor": key,
                        "failure_rate": round(len(failed) / len(records), 2),
                        "recommendation": "Consider reassigning tasks or investigating issues",
                    }
                )

            long_running = [r for r in records if r.duration_minutes > 120]
            if len(long_running) > len(records) * 0.2:  # >20% long running
                bottlenecks.append(
                    {
                        "type": "slow_execution",
                        "executor": key,
                        "slow_rate": round(len(long_running) / len(records), 2),
                        "recommendation": "Consider breaking down tasks or optimizing workflow",
                    }
                )

        # Check for blocked tasks
        orchestrator = self._get_orchestrator()
        blocked_tasks = orchestrator.get_tasks_by_status(TaskStatus.BLOCKED)
        if len(blocked_tasks) > 5:
            bottlenecks.append(
                {
                    "type": "many_blocked_tasks",
                    "count": len(blocked_tasks),
                    "recommendation": "Review task dependencies and unblock critical path",
                }
            )

        return bottlenecks

    def get_execution_summary(self, days: int = 7) -> dict[str, Any]:
        """Get execution summary for a period.

        Args:
            days: Period in days

        Returns:
            Summary dictionary
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Collect records
        all_records: list[ExecutionRecord] = []
        for i in range(days):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            history = self.state_manager.read_history("execution", date, limit=1000)
            for record in history:
                if record.get("event") == "complete":
                    try:
                        completed_at = datetime.fromisoformat(record.get("completed_at", "").replace("Z", "+00:00"))
                        if completed_at >= cutoff:
                            all_records.append(ExecutionRecord(**record))
                    except (RuntimeError, ValueError) as exc:
                        logger.debug("datetime parse failed for performance trend (non-critical): %s", exc)

        # Calculate summary
        total = len(all_records)
        completed = len([r for r in all_records if r.status == "completed"])
        failed = len([r for r in all_records if r.status == "failed"])

        durations = [r.duration_minutes for r in all_records if r.duration_minutes > 0]

        return {
            "period_days": days,
            "total_executions": total,
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / total, 2) if total > 0 else 0.0,
            "avg_duration": round(statistics.mean(durations), 2) if durations else 0.0,
            "total_duration": round(sum(durations), 2) if durations else 0.0,
            "bottlenecks": self.identify_bottlenecks(days),
        }

    def generate_performance_report(self, output_path: str | None = None) -> str:
        """Generate comprehensive performance report.

        Args:
            output_path: Output file path

        Returns:
            Report file path
        """
        report: dict[str, Any] = {
            "generated_at": _now_iso(),
            "summary": self.get_execution_summary(30),
            "trends": [asdict(t) for t in self.analyze_trends("weekly", 4)],
            "executors": {},
            "recommendations": [],
        }

        # Add executor performance
        stats = self._load_stats()
        for key, data in stats.get("executors", {}).items():
            executor_id = data.get("executor_id")
            executor_type = AssigneeType(data.get("executor_type", "Director"))
            perf = self.analyze_executor_performance(executor_id, executor_type, 30)
            report["executors"][key] = asdict(perf)

        # Generate recommendations
        bottlenecks = self.identify_bottlenecks(7)
        for b in bottlenecks:
            report["recommendations"].append(b["recommendation"])

        # Add general recommendations
        if report["summary"]["success_rate"] < 0.7:
            report["recommendations"].append(
                "Overall success rate is low. Review task complexity and executor capabilities."
            )

        if output_path is None:
            from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

            output_path = os.path.join(
                self.workspace,
                get_workspace_metadata_dir_name(),
                "execution_report.json",
            )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return output_path


# Global instance cache
_tracker_instances: dict[str, ExecutionTracker] = {}


def get_execution_tracker(workspace: str) -> ExecutionTracker:
    """Get or create ExecutionTracker instance."""
    workspace_abs = os.path.abspath(workspace)
    if workspace_abs not in _tracker_instances:
        _tracker_instances[workspace_abs] = ExecutionTracker(workspace_abs)
    return _tracker_instances[workspace_abs]


def reset_execution_tracker(workspace: str) -> None:
    """Reset tracker instance for workspace."""
    workspace_abs = os.path.abspath(workspace)
    _tracker_instances.pop(workspace_abs, None)
