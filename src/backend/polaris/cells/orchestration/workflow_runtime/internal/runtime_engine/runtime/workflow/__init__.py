"""Workflow runtime facade.

This module is the canonical import surface for the self-hosted workflow core.
Legacy `runtime.embedded` imports remain temporarily for compatibility.
"""

from ..embedded import (
    ActivityConfig,
    ActivityRunner,
    RetryPolicy,
    SqliteRuntimeStore,
    TaskQueue,
    TaskQueueManager,
    TaskSpec,
    TimerWheel,
    WorkflowContract,
    WorkflowContractError,
    WorkflowEngine,
)

__all__ = [
    "ActivityConfig",
    "ActivityRunner",
    "RetryPolicy",
    "SqliteRuntimeStore",
    "TaskQueue",
    "TaskQueueManager",
    "TaskSpec",
    "TimerWheel",
    "WorkflowContract",
    "WorkflowContractError",
    "WorkflowEngine",
]
