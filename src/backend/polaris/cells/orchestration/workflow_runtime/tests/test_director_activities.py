"""Tests for Director workflow activity timeout policy."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_activity.internal.activities import (
    director_activities as workflow_activity_director_activities,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities import (
    director_activities as workflow_runtime_director_activities,
)


def test_runtime_director_activity_timeout_cap_matches_codegen_budget() -> None:
    assert (
        workflow_runtime_director_activities._coerce_timeout_seconds(
            9999,
            default=300,
            maximum=workflow_runtime_director_activities._DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS,
        )
        == 930
    )
    assert (
        workflow_runtime_director_activities._coerce_timeout_seconds(
            9999,
            default=300,
            maximum=workflow_runtime_director_activities._DIRECTOR_TASK_TIMEOUT_MAX_SECONDS,
        )
        == 900
    )


def test_activity_director_activity_timeout_cap_matches_codegen_budget() -> None:
    assert (
        workflow_activity_director_activities._coerce_timeout_seconds(
            9999,
            default=300,
            maximum=workflow_activity_director_activities._DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS,
        )
        == 930
    )
    assert (
        workflow_activity_director_activities._coerce_timeout_seconds(
            9999,
            default=300,
            maximum=workflow_activity_director_activities._DIRECTOR_TASK_TIMEOUT_MAX_SECONDS,
        )
        == 900
    )


def test_director_activity_timeout_floor_and_invalid_values() -> None:
    assert (
        workflow_runtime_director_activities._coerce_timeout_seconds(
            5,
            default=300,
            maximum=workflow_runtime_director_activities._DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS,
        )
        == 30
    )
    assert (
        workflow_activity_director_activities._coerce_timeout_seconds(
            "bad",
            default=300,
            maximum=workflow_activity_director_activities._DIRECTOR_PROCESS_TIMEOUT_MAX_SECONDS,
        )
        == 300
    )
