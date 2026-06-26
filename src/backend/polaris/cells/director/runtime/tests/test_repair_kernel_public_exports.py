"""Package-level Director Runtime public repair API export tests."""

from __future__ import annotations

import polaris.cells.director.runtime.public as director_public
from polaris.cells.director.runtime.public import contracts, service

_CONTRACT_EXPORTS = (
    "DirectorRepairCallbackReceiptProjectionV1",
    "DirectorRepairConvergenceResultV1",
    "DirectorRepairConvergenceRoundResultV1",
    "DirectorRepairConvergenceVerifierRequestV1",
    "DirectorRepairMaterializationQualityScheduleRunResultV1",
    "DirectorRepairPostExecutionScheduleRunResultV1",
    "DirectorRepairRevalidationInputV1",
    "DirectorRepairRevalidationRequestV1",
    "DirectorRepairVerifierSnapshotInputV1",
    "RunDirectorRepairConvergenceCommandV1",
)

_SERVICE_EXPORTS = (
    "DirectorRepairConvergenceVerifierFn",
    "DirectorRepairRevalidatorFn",
    "run_director_materialization_quality_repair_schedule_result",
    "run_director_post_execution_repair_schedule_result",
    "run_director_repair_convergence",
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
