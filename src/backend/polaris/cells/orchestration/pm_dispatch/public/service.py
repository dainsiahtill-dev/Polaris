"""Stable service exports for orchestration.pm_dispatch."""

from __future__ import annotations

from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline as _dispatch_pipeline
from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    DispatchCallbacks,
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
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
    CommandResult,
    OrchestrationCommandService,
)
from polaris.cells.orchestration.pm_dispatch.public.contracts import (
    DispatchPmTasksCommandV1,
    GetPmDispatchStatusQueryV1,
    PmDispatchError,
    PmDispatchResultV1,
    PmIterationAdvancedEventV1,
    PmTaskDispatchedEventV1,
    ResumePmIterationCommandV1,
)
from polaris.cells.orchestration.shared_types import ErrorClassifier


def reachable_provider_pool(pool: tuple[str, ...], *, probe_timeout: float = 3.0) -> list[str]:
    """Public wrapper for the reachable-provider probe.

    Delegates to the internal implementation at call time (rather than binding
    it at import) so that callers and tests can substitute the probe via the
    canonical internal symbol.
    """
    return _dispatch_pipeline._reachable_provider_pool(pool, probe_timeout=probe_timeout)


__all__ = [
    "CommandResult",
    "DispatchCallbacks",
    "DispatchPmTasksCommandV1",
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
    "reachable_provider_pool",
    "record_stop",
    "resolve_director_dispatch_tasks",
    "run_dispatch_pipeline",
    "run_post_dispatch_integration_qa",
]
