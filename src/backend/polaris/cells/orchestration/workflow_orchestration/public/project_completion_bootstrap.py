"""Bootstrap-only binding surface for durable project-completion orchestration."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_convergence import (
    bind_project_completion_convergence_runtime as _bind_project_completion_convergence_runtime,
    clear_project_completion_convergence_runtime as _clear_project_completion_convergence_runtime,
)
from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_supervisor import (
    EventDrivenProjectCompletionSupervisorV1,
    bind_project_completion_supervisor as _bind_project_completion_supervisor,
    clear_project_completion_supervisor as _clear_project_completion_supervisor,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    ProjectCompletionActionPortV1,
    ProjectCompletionDiagnosticsPortV1,
    ProjectCompletionModelCeilingPortV1,
    ProjectCompletionOutcomePortV1,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorPortV1,
)


def bind_project_completion_convergence_runtime(
    *,
    cursor: ProjectCompletionCursorPortV1,
    outcome_port: ProjectCompletionOutcomePortV1,
    diagnostics_port: ProjectCompletionDiagnosticsPortV1,
    action_port: ProjectCompletionActionPortV1,
    model_ceiling_port: ProjectCompletionModelCeilingPortV1,
) -> None:
    """Bind typed cursor and owner ports; production wiring stays in bootstrap."""

    _bind_project_completion_convergence_runtime(
        cursor=cursor,
        outcome_port=outcome_port,
        diagnostics_port=diagnostics_port,
        action_port=action_port,
        model_ceiling_port=model_ceiling_port,
    )


def clear_project_completion_convergence_runtime() -> None:
    """Release process-local composition state during lifespan shutdown/tests."""

    _clear_project_completion_convergence_runtime()


def bind_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    _bind_project_completion_supervisor(supervisor)


def clear_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    _clear_project_completion_supervisor(supervisor)


__all__ = [
    "bind_project_completion_convergence_runtime",
    "bind_project_completion_supervisor",
    "clear_project_completion_convergence_runtime",
    "clear_project_completion_supervisor",
]
