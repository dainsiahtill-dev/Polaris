"""Bootstrap-only binding surface for durable project-completion orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionActionPortV1,
    ProjectCompletionAdvanceResultV1,
    ProjectCompletionDiagnosticsPortV1,
    ProjectCompletionModelCeilingPortV1,
    ProjectCompletionOutcomePortV1,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorPortV1,
)

_AdvanceCallable = Callable[
    [AdvanceProjectCompletionCommandV1],
    Awaitable[ProjectCompletionAdvanceResultV1],
]
_RecoverCallable = Callable[[], Awaitable[tuple[AdvanceProjectCompletionCommandV1, ...]]]


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


def create_event_driven_project_completion_supervisor(
    *,
    advance: _AdvanceCallable,
    recover: _RecoverCallable | None = None,
) -> EventDrivenProjectCompletionSupervisorV1:
    """Compose the private event-driven supervisor through the Cell boundary."""

    return EventDrivenProjectCompletionSupervisorV1(advance=advance, recover=recover)


def bind_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    _bind_project_completion_supervisor(supervisor)


def clear_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    _clear_project_completion_supervisor(supervisor)


__all__ = [
    "EventDrivenProjectCompletionSupervisorV1",
    "bind_project_completion_convergence_runtime",
    "bind_project_completion_supervisor",
    "clear_project_completion_convergence_runtime",
    "clear_project_completion_supervisor",
    "create_event_driven_project_completion_supervisor",
]
