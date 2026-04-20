"""Public contracts for orchestration.workflow_activity Cell.

These types form the stable public interface for cross-Cell access to
activity and workflow registration.  All registry implementations stay in
internal/ and are not re-exported here.

ACGA 2.0 rule: only these types may cross the Cell boundary.
"""

from __future__ import annotations

from polaris.cells.orchestration.workflow_activity.internal.activities.base import (
    ActivityExecutionContext,
    ActivityExecutionResult,
)
from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
    EmbeddedActivityAPI,
    EmbeddedWorkflowAPI,
    WorkflowContext,
    get_activity_api,
    get_workflow_api,
)
from polaris.cells.orchestration.workflow_activity.internal.models import (
    DirectorTaskInput,
    DirectorTaskResult,
    DirectorWorkflowInput,
    DirectorWorkflowResult,
    ExecutionEvent,
    PMWorkflowInput,
    PMWorkflowResult,
    QAWorkflowInput,
    QAWorkflowResult,
    TaskContract,
    TaskExecutionStatus,
    TaskFailureRecord,
)

__all__ = [
    "ActivityExecutionContext",
    "ActivityExecutionResult",
    "DirectorTaskInput",
    "DirectorTaskResult",
    "DirectorWorkflowInput",
    "DirectorWorkflowResult",
    "EmbeddedActivityAPI",
    "EmbeddedWorkflowAPI",
    "ExecutionEvent",
    "PMWorkflowInput",
    "PMWorkflowResult",
    "QAWorkflowInput",
    "QAWorkflowResult",
    "TaskContract",
    "TaskExecutionStatus",
    "TaskFailureRecord",
    "WorkflowContext",
    "get_activity_api",
    "get_workflow_api",
]
