"""Stable public service surface for factory.pipeline.

Error Handling Contract:
    All factory.pipeline operations raise ``FactoryPipelineError`` on
    expected domain failures (e.g., invalid workspace, missing run_id).
    ``FactoryPipelineError.code`` contains a machine-readable error code;
    ``FactoryPipelineError.details`` contains structured context.

    Unexpected infrastructure failures propagate as standard exceptions and
    should NOT be caught here — let them surface to the application layer.

    Callers should only catch ``FactoryPipelineError``, not the base
    ``RuntimeError``, to avoid masking infrastructure failures.

Example::

    from polaris.cells.factory.pipeline.public.service import (
        IFactoryPipeline,
        FactoryPipelineError,
    )

    try:
        result = await pipeline.run_pipeline(project_path, config)
    except FactoryPipelineError as exc:
        logger.error("Pipeline failed [%s]: %s", exc.code, exc)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..internal.bench_gates import build_real_run_gate
from ..internal.factory_run_admission import FactoryWorkspaceRunAdmission
from ..internal.factory_run_service import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
)
from ..internal.factory_settlement_runtime import (
    FactorySettlementRuntime,
    FactorySettlementRuntimeError,
    create_factory_settlement_runtime,
    start_factory_settlement_runtime,
    stop_all_factory_settlement_runtimes,
    stop_factory_settlement_runtime,
    wake_factory_settlement_runtime,
)
from ..internal.projection_change_analysis import ProjectionChangeAnalysisService
from ..internal.projection_lab import FactoryProjectionLabService
from ..internal.run_ledger import (
    JobToken,
    RunLedger,
    build_gate_ledger_event,
    build_job_token_from_record,
    build_run_ledger_projection,
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_meta,
    summarize_run_ledger_projection,
)
from .contracts import (
    CancelFactoryRunCommandV1,
    FactoryLifecycleOperationClaimV1,
    FactoryPipelineError,
    FactoryRunCompletedEventV1,
    FactoryRunResultV1,
    FactoryRunStartedEventV1,
    FactoryStageExecutionClaimV1,
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
    FactoryWorkspaceRunLeaseStateV1,
    FactoryWorkspaceRunLeaseStorageError,
    FactoryWorkspaceRunLeaseV1,
    GetFactoryRunStatusQueryV1,
    IFactoryPipeline,
    IFactoryProjectionLab,
    ListFactoryRunsQueryV1,
    ProjectionBackMappingRefreshResultV1,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
    RefreshProjectionBackMappingCommandV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
    StartFactoryRunCommandV1,
)

FactoryRunServiceFactory = Callable[[str], FactoryRunService]


def _create_factory_run_service(workspace: str) -> FactoryRunService:
    return FactoryRunService(workspace=Path(workspace))


def get_factory_workspace_run_lease(workspace: str) -> FactoryWorkspaceRunLeaseV1 | None:
    """Return the durable Factory admission projection for one workspace."""

    return FactoryWorkspaceRunAdmission(workspace).current()


async def recover_stale_factory_workspace_owner(
    command: RecoverStaleFactoryWorkspaceOwnerCommandV1,
    *,
    service_factory: FactoryRunServiceFactory | None = None,
) -> RecoverStaleFactoryWorkspaceOwnerResultV1:
    """Fence child sessions and release one explicitly identified stale owner.

    The command workspace is canonicalized before service construction. The
    supplied service factory is checked against that binding so a delivery
    adapter cannot accidentally recover authority in another workspace.
    Domain errors from the admission owner pass through unchanged.
    """

    workspace = str(Path(command.workspace).expanduser().resolve())
    factory = service_factory or _create_factory_run_service
    service = factory(workspace)
    service_workspace = str(Path(service.workspace).expanduser().resolve())
    if service_workspace != workspace:
        raise FactoryPipelineError(
            "Factory stale-owner recovery service is bound to another workspace",
            code="factory_workspace_binding_mismatch",
            details={
                "requested_workspace": workspace,
                "service_workspace": service_workspace,
                "run_id": command.run_id,
            },
        )

    released = await service.recover_stale_workspace_owner(
        command.run_id,
        expected_fencing_token=command.expected_fencing_token,
        reason=command.reason,
    )
    return RecoverStaleFactoryWorkspaceOwnerResultV1(
        workspace=released.workspace,
        run_id=command.run_id,
        expected_fencing_token=command.expected_fencing_token,
        reason=command.reason,
        lease=released,
    )


__all__ = [
    "TERMINAL_RUN_STATUSES",
    "CancelFactoryRunCommandV1",
    "FactoryConfig",
    "FactoryLifecycleOperationClaimV1",
    "FactoryPipelineError",
    "FactoryProjectionLabService",
    "FactoryRun",
    "FactoryRunCompletedEventV1",
    "FactoryRunResultV1",
    "FactoryRunService",
    "FactoryRunStartedEventV1",
    "FactoryRunStatus",
    "FactorySettlementRuntime",
    "FactorySettlementRuntimeError",
    "FactoryStageExecutionClaimV1",
    "FactoryWorkspaceReleaseEvidenceV1",
    "FactoryWorkspaceRunLeaseConflictError",
    "FactoryWorkspaceRunLeaseStateV1",
    "FactoryWorkspaceRunLeaseStorageError",
    "FactoryWorkspaceRunLeaseV1",
    "GetFactoryRunStatusQueryV1",
    "IFactoryPipeline",
    "IFactoryProjectionLab",
    "JobToken",
    "ListFactoryRunsQueryV1",
    "ProjectionBackMappingRefreshResultV1",
    "ProjectionChangeAnalysisService",
    "ProjectionExperimentResultV1",
    "ProjectionReprojectionResultV1",
    "RecoverStaleFactoryWorkspaceOwnerCommandV1",
    "RecoverStaleFactoryWorkspaceOwnerResultV1",
    "RefreshProjectionBackMappingCommandV1",
    "ReprojectProjectionExperimentCommandV1",
    "RunLedger",
    "RunProjectionExperimentCommandV1",
    "StartFactoryRunCommandV1",
    "build_gate_ledger_event",
    "build_job_token_from_record",
    "build_real_run_gate",
    "build_run_ledger_projection",
    "create_factory_settlement_runtime",
    "get_factory_workspace_run_lease",
    "load_run_ledger_projection",
    "persist_real_run_gate_ledger",
    "recover_stale_factory_workspace_owner",
    "start_factory_settlement_runtime",
    "stop_all_factory_settlement_runtimes",
    "stop_factory_settlement_runtime",
    "summarize_run_ledger_meta",
    "summarize_run_ledger_projection",
    "wake_factory_settlement_runtime",
]
