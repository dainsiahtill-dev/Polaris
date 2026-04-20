"""workflow_activity internal activities package.

ACGA 2.0: This module is Cell-local.  It must NOT be imported by other Cells
without going through the public contract.
"""

from __future__ import annotations

# Re-export all activities for convenience
from polaris.cells.orchestration.workflow_activity.internal.activities.base import (
    ActivityExecutionContext,
    ActivityExecutionResult,
    get_registered_activity,
    list_registered_activities,
    register_activity,
)
from polaris.cells.orchestration.workflow_activity.internal.activities.director_activities import (
    claim_task,
    complete_task,
    execute_task_phase,
    get_ready_tasks,
)
from polaris.cells.orchestration.workflow_activity.internal.activities.pm_activities import (
    generate_pm_tasks,
    validate_task_contract,
)
from polaris.cells.orchestration.workflow_activity.internal.activities.qa_activities import (
    collect_evidence,
    run_integration_qa,
    run_unit_qa,
)

__all__ = [
    "ActivityExecutionContext",
    "ActivityExecutionResult",
    "claim_task",
    "collect_evidence",
    "complete_task",
    "execute_task_phase",
    "generate_pm_tasks",
    "get_ready_tasks",
    "get_registered_activity",
    "list_registered_activities",
    "register_activity",
    "run_integration_qa",
    "run_unit_qa",
    "validate_task_contract",
]
