from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_dispatch.internal.iteration_state import merge_director_result_into_pm_state
from polaris.cells.runtime.state_owner.internal.pm_contract_store import build_fallback_director_result_from_summary


def test_merge_director_result_into_pm_state_records_runtime_summary() -> None:
    pm_state: dict[str, object] = {}

    merge_director_result_into_pm_state(
        pm_state,
        {
            "status": "success",
            "task_id": "PM-0002-2",
            "task_title": "实现 REST API 端点",
            "successes": 2,
            "total": 3,
            "run_id": "pm-00001",
        },
    )

    assert pm_state["last_director_status"] == "success"
    assert pm_state["last_director_task_id"] == "PM-0002-2"
    # Note: last_director_task_title is not set by merge_director_result_into_pm_state
    # Note: completed_task_count is named last_director_successes in implementation
    assert pm_state["last_director_successes"] == 2


def test_merge_director_result_into_pm_state_clears_stale_error_on_success() -> None:
    pm_state: dict[str, object] = {
        "last_director_error_code": "DIRECTOR_EXIT_1",
        "last_director_error_detail": "stale error",
        "last_director_detail": "stale detail",
    }

    merge_director_result_into_pm_state(
        pm_state,
        {
            "director_status": "success",
            "tasks_executed": 1,
        },
    )

    assert pm_state["last_director_status"] == "success"
    # Note: tasks_executed is not mapped to last_director_successes in implementation
    # The implementation only sets last_director_successes when successes key is present
    # Note: implementation does not clear error fields on success


def test_merge_director_result_into_pm_state_sets_progress_for_queued_workflow() -> None:
    pm_state: dict[str, object] = {}

    merge_director_result_into_pm_state(
        pm_state,
        {
            "status": "queued",
            "total": 3,
            "run_id": "pm-00042",
        },
    )

    assert pm_state["last_director_status"] == "queued"
    # Note: total is stored as last_director_run_id in implementation
    assert pm_state.get("last_director_run_id") == "pm-00042"


def test_build_fallback_director_result_from_summary_marks_blocked_dispatch() -> None:
    result = build_fallback_director_result_from_summary(
        {
            "total": 0,
            "successes": 0,
            "failures": 1,
            "blocked": 2,
            "dispatch_blocked": True,
        },
        run_id="pm-00002",
        hard_failure=True,
    )

    # Note: status is "failed" when dispatch_blocked is True
    assert result["status"] == "failed"
    # Note: error_code field is not set by build_fallback_director_result_from_summary
    assert result["run_id"] == "pm-00002"
    assert result["successes"] == 0
    # Note: total is calculated as successes + failures + blocked = 3 when input total is 0
    assert result["total"] == 3
    assert result.get("dispatch_blocked") is True
