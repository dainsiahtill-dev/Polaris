"""Execution metrics for the historical Director role."""

from __future__ import annotations

from typing import Any

from polaris.cells.director.planning.public import (
    DirectorAgent as PlanningDirectorAgent,
    ExecutionRecord,
    QualityTracker,
    RiskRegistry,
)
from polaris.kernelone.telemetry.metrics import MetricsRecorder, Timer

__all__ = ["DirectorAgent", "ExecutionRecord", "QualityTracker", "RiskRegistry"]


class DirectorAgent(PlanningDirectorAgent):
    """Use the shared Director lifecycle with the execution Cell's metrics."""

    def __init__(self, workspace: str, message_bus: Any = None) -> None:
        super().__init__(workspace, message_bus)
        self.metrics = MetricsRecorder()
        self.metrics.define_counter(
            "director_execution_total", description="Total number of task executions", labels=["status"]
        )
        self.metrics.define_histogram(
            "director_execution_duration_seconds",
            description="Task execution duration in seconds",
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
        )
        self.metrics.define_histogram(
            "director_risk_score",
            description="Risk score distribution",
            buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
        )

    def _tool_execute_task(self, task_id: str) -> dict[str, Any]:
        """Execute a task."""

        with Timer("director_execution_timer") as timer:
            result = self._execute_task_impl(task_id)
            # Record execution metrics
            duration_seconds = timer.elapsed_seconds
            self.metrics.get_histogram("director_execution_duration_seconds").observe(duration_seconds)
            status = "started"
            if result and result.get("ok"):
                self.metrics.get_counter("director_execution_total", {"status": status}).inc()
            return result

    def _execute_task_impl(self, task_id: str) -> dict[str, Any]:
        return super()._tool_execute_task(task_id)

    def _record_execution_risks(self, execution: ExecutionRecord) -> None:
        self.metrics.get_histogram("director_risk_score").observe(execution.risk_score)
        self.metrics.get_counter("director_execution_total", {"status": "assessed"}).inc()
        super()._record_execution_risks(execution)

    def _record_execution_qa(self, execution: ExecutionRecord, qa_result: dict[str, Any]) -> None:
        super()._record_execution_qa(execution, qa_result)
        status = "completed" if qa_result.get("passed") else "failed"
        self.metrics.get_counter("director_execution_total", {"status": status}).inc()
