"""Stable service exports for orchestration.pm_dispatch."""

from __future__ import annotations

from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    resolve_director_dispatch_tasks,
    run_dispatch_pipeline,
    run_post_dispatch_integration_qa,
)
from polaris.cells.orchestration.pm_dispatch.internal.iteration_state import (
    clear_manual_intervention,
    finalize_iteration,
    handle_spin_guard,
    record_stop,
)
from polaris.cells.orchestration.shared_types import ErrorCategory, ErrorClassifier

from ..internal.orchestration_command_service import CommandResult, OrchestrationCommandService
from .contracts import (
    DispatchPmTasksCommandV1,
    GetPmDispatchStatusQueryV1,
    PmDispatchError,
    PmDispatchResultV1,
    PmIterationAdvancedEventV1,
    PmTaskDispatchedEventV1,
    ResumePmIterationCommandV1,
)

__all__ = [
    "CommandResult",
    "DispatchPmTasksCommandV1",
    "ErrorCategory",
    "ErrorClassifier",
    "GetPmDispatchStatusQueryV1",
    "OrchestrationCommandService",
    "PmDispatchError",
    "PmDispatchResultV1",
    "PmIterationAdvancedEventV1",
    "PmTaskDispatchedEventV1",
    "ResumePmIterationCommandV1",
    "clear_manual_intervention",
    "finalize_iteration",
    "handle_spin_guard",
    "record_stop",
    "resolve_director_dispatch_tasks",
    "run_dispatch_pipeline",
    "run_post_dispatch_integration_qa",
]
