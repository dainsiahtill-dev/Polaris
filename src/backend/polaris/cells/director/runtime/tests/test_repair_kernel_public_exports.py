"""Package-level Director Runtime public repair API export tests."""

from __future__ import annotations

import polaris.cells.director.runtime.public as director_public
from polaris.cells.director.runtime.public import contracts, service

_CONTRACT_EXPORTS = (
    "DirectorRepairCallbackReceiptProjectionV1",
    "DirectorRepairConvergenceResultV1",
    "DirectorRepairConvergenceRoundResultV1",
    "DirectorRepairConvergenceVerifierRequestV1",
    "DirectorRepairEnvironmentPrepCatalogResultV1",
    "DirectorRepairEnvironmentPrepPlanV1",
    "DirectorRepairEnvironmentPrepReceiptV1",
    "DirectorRepairEnvironmentRefreshRequirementV1",
    "DirectorRepairEnvironmentRefreshRequirementsResultV1",
    "DirectorRepairMaterializationAllowedPathsResultV1",
    "DirectorRepairPlanProbeItemV1",
    "DirectorRepairPlanProbeResultV1",
    "DirectorTaskBoundaryQualityResultV1",
    "DirectorRepairMaterializationQualityScheduleRunResultV1",
    "DirectorRepairPostExecutionScheduleRunResultV1",
    "DirectorRepairRevalidationInputV1",
    "DirectorRepairRevalidationRequestV1",
    "DirectorRepairVerifierSnapshotInputV1",
    "QueryDirectorRepairEnvironmentPrepCatalogV1",
    "QueryDirectorRepairEnvironmentRefreshRequirementsV1",
    "QueryDirectorRepairMaterializationAllowedPathsV1",
    "QueryDirectorRepairPlanProbeV1",
    "RunDirectorRepairConvergenceCommandV1",
    "RunDirectorTaskBoundaryQualityLoopCommandV1",
)

_SERVICE_EXPORTS = (
    "DirectorRepairConvergenceVerifierFn",
    "DirectorRepairRevalidatorFn",
    "query_director_repair_environment_prep_catalog",
    "query_director_repair_environment_refresh_requirements",
    "query_director_repair_materialization_allowed_paths",
    "query_director_repair_plan_probe",
    "run_director_materialization_quality_repair_schedule_result",
    "run_director_post_execution_repair_schedule_result",
    "run_director_repair_convergence",
    "run_director_task_boundary_quality_loop",
)


def test_director_runtime_public_package_exports_repair_convergence_contracts() -> None:
    missing_or_miswired = [
        name for name in _CONTRACT_EXPORTS if getattr(director_public, name) is not getattr(contracts, name)
    ]

    assert missing_or_miswired == []
    assert set(_CONTRACT_EXPORTS).issubset(director_public.__all__)


def test_director_runtime_public_package_exports_repair_convergence_service_api() -> None:
    missing_or_miswired = [
        name for name in _SERVICE_EXPORTS if getattr(director_public, name) is not getattr(service, name)
    ]

    assert missing_or_miswired == []
    assert set(_SERVICE_EXPORTS).issubset(director_public.__all__)
