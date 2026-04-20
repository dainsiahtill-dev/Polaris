"""Shared data contracts for workflow_activity Cell.

This module holds all workflow-domain types that are used across
activities, workflows, and the embedded API.  It must NOT import from
workflow_engine (preventing a Cell cycle).

Imported freely by:
- polaris.cells.orchestration.workflow_activity.internal.activities.*
- polaris.cells.orchestration.workflow_activity.internal.workflows.*
- polaris.cells.orchestration.workflow_activity.internal.embedded_api
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return an explicit UTC timestamp for workflow history records."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _workflow_token(run_id: str) -> str:
    return str(run_id or "").strip() or "adhoc"


def pm_workflow_id(run_id: str) -> str:
    """Return the canonical Workflow workflow id for the PM parent workflow."""
    return f"polaris-pm-{_workflow_token(run_id)}"


def director_workflow_id(run_id: str) -> str:
    """Return the canonical Workflow workflow id for the Director child workflow."""
    return f"polaris-director-{_workflow_token(run_id)}"


def qa_workflow_id(run_id: str) -> str:
    """Return the canonical Workflow workflow id for the QA child workflow."""
    return f"polaris-qa-{_workflow_token(run_id)}"


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class ExecutionEvent:
    """Structured execution event stored in workflow query history."""

    timestamp: str
    stage: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        stage: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        return cls(
            timestamp=utc_now_iso(),
            stage=str(stage or "").strip(),
            message=str(message or "").strip(),
            details=_normalize_mapping(details),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "stage": self.stage,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class TaskExecutionStatus:
    """Serializable task status used by Workflow workflow queries."""

    task_id: str
    state: str
    summary: str = ""
    updated_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state,
            "summary": self.summary,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TaskContract:
    """Task contract passed into Workflow child workflows."""

    task_id: str
    title: str
    goal: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Any) -> TaskContract:
        payload = _normalize_mapping(raw)
        return cls(
            task_id=str(payload.get("id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            goal=str(payload.get("goal") or payload.get("description") or "").strip(),
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.payload)
        data.setdefault("id", self.task_id)
        data.setdefault("title", self.title)
        if self.goal:
            data.setdefault("goal", self.goal)
        return data


@dataclass(frozen=True)
class PMWorkflowInput:
    """Input payload for the top-level PM Workflow workflow."""

    workspace: str
    run_id: str
    precomputed_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def workflow_id(self) -> str:
        return pm_workflow_id(self.run_id)

    @classmethod
    def from_mapping(cls, raw: Any) -> PMWorkflowInput:
        payload = _normalize_mapping(raw)
        raw_precomputed = payload.get("precomputed_payload")
        precomputed = cast("dict[str, Any]", raw_precomputed if isinstance(raw_precomputed, dict) else {})
        raw_metadata = payload.get("metadata")
        metadata = cast("dict[str, Any]", raw_metadata if isinstance(raw_metadata, dict) else {})
        return cls(
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            precomputed_payload=precomputed,
            metadata=metadata,
        )

    def payload_tasks(self) -> list[TaskContract]:
        payload = _normalize_mapping(self.precomputed_payload)
        raw_tasks_raw = payload.get("tasks")
        raw_tasks: list[Any] = raw_tasks_raw if isinstance(raw_tasks_raw, list) else []
        tasks: list[TaskContract] = []
        for item in raw_tasks:
            contract = TaskContract.from_mapping(item)
            if contract.task_id:
                tasks.append(contract)
        return tasks


@dataclass(frozen=True)
class PMWorkflowResult:
    """Result produced by the top-level PM workflow."""

    run_id: str
    tasks: list[TaskContract]
    director_status: str
    qa_status: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (RuntimeError, ValueError):
        return max(1, int(default))


def _coerce_execution_mode(value: Any, default: str = "parallel") -> str:
    token = str(value or "").strip().lower()
    if token in {"serial", "parallel"}:
        return token
    return default


@dataclass(frozen=True)
class DirectorWorkflowInput:
    """Input payload for the Director workflow."""

    workspace: str
    run_id: str
    tasks: list[TaskContract]
    execution_mode: str = "parallel"
    max_parallel_tasks: int = 3
    ready_timeout_seconds: int = 30
    task_timeout_seconds: int = MAX_WORKFLOW_TIMEOUT_SECONDS
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Any) -> DirectorWorkflowInput:
        payload = _normalize_mapping(raw)
        raw_tasks_raw = payload.get("tasks")
        raw_tasks: list[Any] = raw_tasks_raw if isinstance(raw_tasks_raw, list) else []
        tasks: list[TaskContract] = []
        for item in raw_tasks:
            contract = item if isinstance(item, TaskContract) else TaskContract.from_mapping(item)
            if contract.task_id:
                tasks.append(contract)
        raw_metadata = payload.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_config = metadata.get("director_config")
        director_config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        raw_execution_mode = payload.get("execution_mode")
        if raw_execution_mode is None:
            raw_execution_mode = director_config.get("execution_mode")
        raw_max_parallel_tasks = payload.get("max_parallel_tasks")
        if raw_max_parallel_tasks is None:
            raw_max_parallel_tasks = director_config.get("max_parallel_tasks")
        raw_ready_timeout = payload.get("ready_timeout_seconds")
        if raw_ready_timeout is None:
            raw_ready_timeout = director_config.get("ready_timeout_seconds")
        raw_task_timeout = payload.get("task_timeout_seconds")
        if raw_task_timeout is None:
            raw_task_timeout = director_config.get("task_timeout_seconds")
        return cls(
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            tasks=tasks,
            execution_mode=_coerce_execution_mode(raw_execution_mode),
            max_parallel_tasks=_coerce_positive_int(raw_max_parallel_tasks, 3),
            ready_timeout_seconds=_coerce_positive_int(raw_ready_timeout, 30),
            task_timeout_seconds=_coerce_positive_int(raw_task_timeout, MAX_WORKFLOW_TIMEOUT_SECONDS),
            metadata=metadata,
        )


@dataclass(frozen=True)
class DirectorWorkflowResult:
    """Aggregated Director workflow result."""

    run_id: str
    status: str
    completed_tasks: int
    failed_tasks: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectorTaskInput:
    """Input payload for one Director child workflow."""

    workspace: str
    run_id: str
    task: TaskContract
    phases: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Any) -> DirectorTaskInput:
        payload = _normalize_mapping(raw)
        task_raw = payload.get("task")
        task = task_raw if isinstance(task_raw, TaskContract) else TaskContract.from_mapping(task_raw)
        phases = (
            [str(item).strip() for item in payload.get("phases") or [] if str(item).strip()]
            if isinstance(payload.get("phases"), list)
            else []
        )
        raw_metadata = payload.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        return cls(
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            task=task,
            phases=phases,
            metadata=metadata,
        )


@dataclass(frozen=True)
class DirectorTaskResult:
    """Result from one Director child workflow."""

    task_id: str
    status: str
    completed_phases: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Any) -> DirectorTaskResult:
        payload = _normalize_mapping(raw)
        completed_phases = (
            [str(item).strip() for item in payload.get("completed_phases") or [] if str(item).strip()]
            if isinstance(payload.get("completed_phases"), list)
            else []
        )
        errors = (
            [str(item).strip() for item in payload.get("errors") or [] if str(item).strip()]
            if isinstance(payload.get("errors"), list)
            else []
        )
        raw_metadata = payload.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            completed_phases=completed_phases,
            errors=errors,
            metadata=metadata,
        )


@dataclass(frozen=True)
class QAWorkflowInput:
    """Input payload for the QA workflow."""

    workspace: str
    run_id: str
    director_status: str
    task_results: list[DirectorTaskResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Any) -> QAWorkflowInput:
        payload = _normalize_mapping(raw)
        raw_task_results_raw = payload.get("task_results")
        raw_task_results: list[Any] = raw_task_results_raw if isinstance(raw_task_results_raw, list) else []
        task_results: list[DirectorTaskResult] = []
        for item in raw_task_results:
            if isinstance(item, DirectorTaskResult):
                if item.task_id:
                    task_results.append(item)
                continue
            result = DirectorTaskResult.from_mapping(item)
            if result.task_id:
                task_results.append(result)
        raw_metadata = payload.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        return cls(
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            director_status=str(payload.get("director_status") or "").strip(),
            task_results=task_results,
            metadata=metadata,
        )


@dataclass(frozen=True)
class QAWorkflowResult:
    """QA verdict emitted by the QA workflow."""

    run_id: str
    passed: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskFailureRecord:
    """Record of a task failure with classification for recovery decisions."""

    task_id: str
    error_message: str
    error_category: str
    retryable: bool
    max_retries: int
    recovery_strategy: str
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "error_message": self.error_message,
            "error_category": self.error_category,
            "retryable": self.retryable,
            "max_retries": self.max_retries,
            "recovery_strategy": self.recovery_strategy,
            "timestamp": self.timestamp,
        }
