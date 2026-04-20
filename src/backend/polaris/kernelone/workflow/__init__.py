"""KernelOne workflow runtime primitives.

This package contains pure technical workflow runtime modules:
- runtime protocol and configs
- workflow contract parsing/validation
- in-memory queueing
- timer scheduling
- activity execution
"""

from __future__ import annotations

from .activity_runner import ActivityConfig, ActivityExecution, ActivityRunner
from .base import (
    EmbeddedConfig,
    RuntimeBackendPort,
    RuntimeSubmissionResult,
    WorkflowConfig,
    WorkflowSnapshot,
)
from .contracts import RetryPolicy, TaskSpec, WorkflowContract, WorkflowContractError
from .engine import (
    HandlerRegistryPort,
    TaskExecutionOutcome,
    TaskRuntimeState,
    WorkflowEngine,
    WorkflowRuntimeState,
)
from .saga_events import (
    _EVENT_COMPENSATION_COMPLETED,
    _EVENT_COMPENSATION_FAILED,
    _EVENT_COMPENSATION_STARTED,
    _EVENT_COMPENSATION_TASK_COMPLETED,
    _EVENT_COMPENSATION_TASK_FAILED,
    _EVENT_COMPENSATION_TASK_STARTED,
    _EVENT_HUMAN_APPROVED,
    _EVENT_HUMAN_REJECTED,
    _EVENT_TASK_SUSPENDED_HUMAN_REVIEW,
    _EVENT_WORKFLOW_CHECKPOINT,
    _EVENT_WORKFLOW_PAUSED,
    _EVENT_WORKFLOW_RESUMED,
    _EVENT_WORKFLOW_SIGNAL_RECEIVED,
)
from .task_queue import Task, TaskQueue, TaskQueueManager, TaskResult
from .task_status import ACTIVE_STATUSES, TERMINAL_STATUSES, WorkflowTaskStatus
from .timer_wheel import TimerJob, TimerWheel

__all__ = [
    "ACTIVE_STATUSES",
    "ActivityConfig",
    "ActivityExecution",
    "ActivityRunner",
    "EmbeddedConfig",
    "HandlerRegistryPort",
    "RetryPolicy",
    "RuntimeBackendPort",
    "RuntimeSubmissionResult",
    "Task",
    "TaskExecutionOutcome",
    "TaskQueue",
    "TaskQueueManager",
    "TaskResult",
    "TaskRuntimeState",
    "TaskSpec",
    "TERMINAL_STATUSES",
    "TimerJob",
    "TimerWheel",
    "WorkflowConfig",
    "WorkflowContract",
    "WorkflowContractError",
    "WorkflowEngine",
    "WorkflowRuntimeState",
    "WorkflowTaskStatus",
    "WorkflowSnapshot",
    # Saga event constants
    "_EVENT_COMPENSATION_COMPLETED",
    "_EVENT_COMPENSATION_FAILED",
    "_EVENT_COMPENSATION_STARTED",
    "_EVENT_COMPENSATION_TASK_COMPLETED",
    "_EVENT_COMPENSATION_TASK_FAILED",
    "_EVENT_COMPENSATION_TASK_STARTED",
    "_EVENT_HUMAN_APPROVED",
    "_EVENT_HUMAN_REJECTED",
    "_EVENT_TASK_SUSPENDED_HUMAN_REVIEW",
    "_EVENT_WORKFLOW_CHECKPOINT",
    "_EVENT_WORKFLOW_PAUSED",
    "_EVENT_WORKFLOW_RESUMED",
    "_EVENT_WORKFLOW_SIGNAL_RECEIVED",
]
