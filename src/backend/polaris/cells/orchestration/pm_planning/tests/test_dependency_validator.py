from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
    validate_dependency_dag,
)


def test_validate_dependency_dag_accepts_linear_chain() -> None:
    validate_dependency_dag(
        [
            {"id": "T01", "depends_on": []},
            {"id": "T02", "depends_on": ["T01"]},
            {"id": "T03", "depends_on": ["T02"]},
        ]
    )


def test_validate_dependency_dag_rejects_cycle() -> None:
    with pytest.raises(DependencyCycleError) as exc_info:
        validate_dependency_dag(
            [
                {"id": "T01", "depends_on": ["T03"]},
                {"id": "T02", "depends_on": ["T01"]},
                {"id": "T03", "depends_on": ["T02"]},
            ]
        )

    assert exc_info.value.cycle[0] == exc_info.value.cycle[-1]
    assert set(exc_info.value.cycle[:-1]) == {"T01", "T02", "T03"}


def test_validate_dependency_dag_skips_external_dependency() -> None:
    validate_dependency_dag(
        [
            {"id": "T01", "depends_on": ["EXT-9"]},
            {"id": "T02", "depends_on": ["T01"]},
        ]
    )
