"""Cell-boundary-preserving lazy cross-Cell import loaders.

Every loader performs its ``polaris.cells.*`` / ``polaris.kernelone.*``
import INSIDE the function body (never at module level), so importing this
module triggers no cross-Cell coupling and breaks import cycles. Bodies are
moved verbatim from the original ``dispatch_pipeline.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _get_chief_engineer_service() -> Callable:
    """Lazy import for chief_engineer.blueprint to avoid module-level cross-Cell coupling."""
    from polaris.cells.chief_engineer.blueprint.public.service import (
        run_pre_dispatch_chief_engineer,
    )

    return run_pre_dispatch_chief_engineer


def _get_chief_engineer_blueprint_services() -> tuple[type, Callable]:
    """Lazy import for chief_engineer.blueprint public blueprint generation."""
    from polaris.cells.chief_engineer.blueprint.public.contracts import (
        GenerateTaskBlueprintCommandV1,
    )
    from polaris.cells.chief_engineer.blueprint.public.service import (
        generate_task_blueprint,
    )

    return GenerateTaskBlueprintCommandV1, generate_task_blueprint


def _get_workflow_runtime() -> tuple[type, type, Callable]:
    """Lazy import for workflow_runtime to avoid module-level cross-Cell coupling."""
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        PMWorkflowInput,
        WorkflowSubmissionResult,
        submit_pm_workflow_sync,
    )

    return PMWorkflowInput, WorkflowSubmissionResult, submit_pm_workflow_sync


def _get_task_market_services() -> tuple[type, Callable]:
    """Lazy import for runtime.task_market to avoid module-level cross-Cell coupling."""
    from polaris.cells.runtime.task_market.public.contracts import (
        PublishTaskWorkItemCommandV1,
    )
    from polaris.cells.runtime.task_market.public.service import (
        get_task_market_service,
    )

    return PublishTaskWorkItemCommandV1, get_task_market_service


def _get_task_market_requeue_services() -> tuple[type, Callable]:
    """Lazy import for supervisor requeue without widening module-level coupling."""
    from polaris.cells.runtime.task_market.public.contracts import (
        RequeueTaskCommandV1,
    )
    from polaris.cells.runtime.task_market.public.service import (
        get_task_market_service,
    )

    return RequeueTaskCommandV1, get_task_market_service


def _get_task_market_revision_services() -> tuple[type, type, type]:
    """Lazy import for task_market revision/change-order contracts."""
    from polaris.cells.runtime.task_market.public.contracts import (
        QueryPlanRevisionsV1,
        RegisterPlanRevisionCommandV1,
        SubmitChangeOrderCommandV1,
    )

    return RegisterPlanRevisionCommandV1, SubmitChangeOrderCommandV1, QueryPlanRevisionsV1


def _get_task_market_consumers() -> tuple[type, type, type]:
    """Lazy import for CE/Director/QA task-market consumers."""
    from polaris.cells.chief_engineer.blueprint.public.service import CEConsumer
    from polaris.cells.director.task_consumer import DirectorExecutionConsumer
    from polaris.cells.qa.audit_verdict.public.service import QAConsumer

    return CEConsumer, DirectorExecutionConsumer, QAConsumer


def _get_shared_quality() -> tuple[Callable, Callable]:
    """Lazy import for shared_quality to avoid circular imports."""
    from polaris.cells.orchestration.pm_planning.public.service import (
        detect_integration_verify_command,
        run_integration_verify_runner,
    )

    return detect_integration_verify_command, run_integration_verify_runner


def _get_cognitive_runtime_services() -> tuple[type, type, Callable[[], Any]]:
    """Lazy import for factory.cognitive_runtime public contracts."""
    from polaris.cells.factory.cognitive_runtime.public import (
        RecordRuntimeReceiptCommandV1,
        ResolveContextCommandV1,
        get_cognitive_runtime_public_service,
    )

    return RecordRuntimeReceiptCommandV1, ResolveContextCommandV1, get_cognitive_runtime_public_service


def _get_io_utils() -> tuple[Callable, Callable]:
    """Lazy import for events to avoid circular imports."""
    from polaris.kernelone.events import emit_dialogue, emit_event

    return emit_event, emit_dialogue


def _get_tasks_utils() -> tuple[Callable, Callable]:
    """Return task utility functions from the Cell's own port module.

    Delivery layer is intentionally never imported here; all pure logic
    lives in ``pm_task_utils``.
    """
    from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
        get_director_task_status_summary,
        to_bool,
    )

    return get_director_task_status_summary, to_bool


def _get_shangshuling_port() -> Any:
    """Return the cell-local Shangshuling port."""
    from polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry import (
        get_shangshuling_port,
    )

    return get_shangshuling_port()


def _get_traceability_safety() -> tuple[Any, Any, Any]:
    """Lazy import for traceability safety helpers."""
    from polaris.kernelone.traceability.internal.safety import (
        safe_find_node,
        safe_link,
        safe_register_node,
    )

    return safe_find_node, safe_link, safe_register_node
