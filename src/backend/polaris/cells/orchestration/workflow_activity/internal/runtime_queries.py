"""Workflow query mixins backed by workflow history-friendly state.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/runtime_queries.py

ACGA 2.0: This module is Cell-local.  Internal modules must NOT import from
other Cells without going through the public contract.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
    get_workflow_api,
)
from polaris.cells.orchestration.workflow_activity.internal.models import (
    ExecutionEvent,
    TaskExecutionStatus,
)

workflow = get_workflow_api()


class WorkflowQueryState:
    """Reusable query/state helpers shared by workflow_activity workflows."""

    def __init__(self) -> None:
        self._stage = "idle"
        self._history: list[ExecutionEvent] = []
        self._task_statuses: dict[str, TaskExecutionStatus] = {}

    def _record_event(
        self,
        *,
        stage: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._stage = str(stage or "").strip() or self._stage
        self._history.append(ExecutionEvent.create(stage=self._stage, message=message, details=details))

    def _set_task_status(
        self,
        task_id: str,
        state: str,
        *,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return
        self._task_statuses[normalized_task_id] = TaskExecutionStatus(
            task_id=normalized_task_id,
            state=str(state or "").strip(),
            summary=str(summary or "").strip(),
            metadata=dict(metadata or {}),
        )

    @workflow.query
    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        status = self._task_statuses.get(str(task_id or "").strip())
        if status is None:
            return None
        return status.to_dict()

    @workflow.query
    def get_execution_history(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self._history]

    @workflow.query
    def get_runtime_snapshot(self) -> dict[str, Any]:
        return {
            "stage": self._stage,
            "history": [event.to_dict() for event in self._history],
            "tasks": {task_id: status.to_dict() for task_id, status in self._task_statuses.items()},
        }
