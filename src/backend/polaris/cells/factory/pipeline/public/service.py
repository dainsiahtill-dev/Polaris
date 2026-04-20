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

from ..internal.factory_run_service import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
)
from ..internal.projection_change_analysis import ProjectionChangeAnalysisService
from ..internal.projection_lab import FactoryProjectionLabService
from .contracts import (
    CancelFactoryRunCommandV1,
    FactoryPipelineError,
    FactoryRunCompletedEventV1,
    FactoryRunResultV1,
    FactoryRunStartedEventV1,
    GetFactoryRunStatusQueryV1,
    IFactoryPipeline,
    IFactoryProjectionLab,
    ListFactoryRunsQueryV1,
    ProjectionBackMappingRefreshResultV1,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    RefreshProjectionBackMappingCommandV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
    StartFactoryRunCommandV1,
)

__all__ = [
    "TERMINAL_RUN_STATUSES",
    "CancelFactoryRunCommandV1",
    "FactoryConfig",
    "FactoryPipelineError",
    "FactoryProjectionLabService",
    "FactoryRun",
    "FactoryRunCompletedEventV1",
    "FactoryRunResultV1",
    "FactoryRunService",
    "FactoryRunStartedEventV1",
    "FactoryRunStatus",
    "GetFactoryRunStatusQueryV1",
    "IFactoryPipeline",
    "IFactoryProjectionLab",
    "ListFactoryRunsQueryV1",
    "ProjectionBackMappingRefreshResultV1",
    "ProjectionChangeAnalysisService",
    "ProjectionExperimentResultV1",
    "ProjectionReprojectionResultV1",
    "RefreshProjectionBackMappingCommandV1",
    "ReprojectProjectionExperimentCommandV1",
    "RunProjectionExperimentCommandV1",
    "StartFactoryRunCommandV1",
]
