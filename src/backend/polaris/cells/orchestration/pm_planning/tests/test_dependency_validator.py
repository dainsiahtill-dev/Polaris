from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
    Schedule,
    compute_schedule,
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


# ---------------------------------------------------------------------------
# compute_schedule — Critical Path Method over the task DAG
# ---------------------------------------------------------------------------


def test_compute_schedule_empty_is_zero() -> None:
    schedule = compute_schedule([])
    assert isinstance(schedule, Schedule)
    assert schedule.order == ()
    assert schedule.critical_path == ()
    assert schedule.makespan == 0.0
    assert schedule.to_dict()["order"] == []


def test_compute_schedule_single_task() -> None:
    schedule = compute_schedule([{"id": "a", "estimated_effort": 3}])
    assert schedule.order == ("a",)
    assert schedule.levels == {"a": 0}
    assert schedule.weights == {"a": 3.0}
    assert schedule.earliest_start == {"a": 0.0}
    assert schedule.earliest_finish == {"a": 3.0}
    assert schedule.slack == {"a": 0.0}
    assert schedule.critical_path == ("a",)
    assert schedule.makespan == 3.0


def test_compute_schedule_linear_chain_all_critical() -> None:
    tasks = [
        {"id": "a"},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    schedule = compute_schedule(tasks)
    assert schedule.order == ("a", "b", "c")
    assert schedule.levels == {"a": 0, "b": 1, "c": 2}
    # default unit weights -> makespan 3, the whole chain is critical
    assert schedule.makespan == 3.0
    assert schedule.critical_path == ("a", "b", "c")
    assert all(abs(s) < 1e-9 for s in schedule.slack.values())


def test_compute_schedule_diamond_critical_path_and_slack() -> None:
    # a -> {b(5), c(2)} -> d ; the heavy branch (b) is critical, c has slack.
    tasks = [
        {"id": "a", "estimated_effort": 1},
        {"id": "b", "depends_on": ["a"], "estimated_effort": 5},
        {"id": "c", "depends_on": ["a"], "estimated_effort": 2},
        {"id": "d", "depends_on": ["b", "c"], "estimated_effort": 1},
    ]
    schedule = compute_schedule(tasks)
    assert schedule.makespan == 7.0  # a(1)+b(5)+d(1)
    assert schedule.earliest_finish["c"] == 3.0
    assert schedule.slack["a"] == 0.0
    assert schedule.slack["b"] == 0.0
    assert schedule.slack["d"] == 0.0
    assert schedule.slack["c"] == 3.0  # c can slip 3 units without delaying d
    assert schedule.critical_path == ("a", "b", "d")
    assert schedule.levels == {"a": 0, "b": 1, "c": 1, "d": 2}


def test_compute_schedule_independent_tasks_all_critical() -> None:
    tasks = [{"id": "a", "estimated_effort": 2}, {"id": "b", "estimated_effort": 5}]
    schedule = compute_schedule(tasks)
    assert schedule.levels == {"a": 0, "b": 0}
    assert schedule.makespan == 5.0
    # both start at 0; only the longest (b) is on the critical path
    assert schedule.slack["b"] == 0.0
    assert schedule.slack["a"] == 3.0
    assert schedule.critical_path == ("b",)


def test_compute_schedule_weight_sources_and_fallback() -> None:
    tasks = [
        {"id": "num", "estimated_hours": 4},
        {"id": "size", "estimated_effort": "L"},  # 4.0
        {"id": "numstr", "effort": "2.5"},
        {"id": "bad", "estimate": "not-a-number"},  # -> default 1.0
        {"id": "missing"},  # -> default 1.0
        {"id": "zero", "estimated_effort": 0},  # non-positive ignored -> default 1.0
    ]
    schedule = compute_schedule(tasks)
    assert schedule.weights == {
        "num": 4.0,
        "size": 4.0,
        "numstr": 2.5,
        "bad": 1.0,
        "missing": 1.0,
        "zero": 1.0,
    }


def test_compute_schedule_skips_external_dependency_refs() -> None:
    # 'ext' is not in the task set: it must be skipped, not treated as a real edge.
    tasks = [{"id": "a", "depends_on": ["ext"]}, {"id": "b", "depends_on": ["a"]}]
    schedule = compute_schedule(tasks)
    assert schedule.order == ("a", "b")
    assert schedule.levels == {"a": 0, "b": 1}
    assert schedule.makespan == 2.0


def test_compute_schedule_raises_on_cycle() -> None:
    tasks = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(DependencyCycleError):
        compute_schedule(tasks)


def test_compute_schedule_ignores_malformed_entries() -> None:
    tasks = [{"id": "a"}, "not-a-dict", {"id": ""}, {"id": "a"}]  # dup + empty + non-dict
    schedule = compute_schedule(tasks)  # type: ignore[arg-type]
    assert schedule.order == ("a",)
    assert schedule.makespan == 1.0


def test_compute_schedule_slack_has_no_float_noise() -> None:
    # Non-binary-exact weights must not leak float noise into the slack dict;
    # a critical task must still test == 0.0 exactly.
    tasks = [
        {"id": "a", "estimated_effort": 1.3},
        {"id": "b", "depends_on": ["a"], "estimated_effort": 1.3},
        {"id": "c", "depends_on": ["a"], "estimated_effort": 0.7},
        {"id": "d", "depends_on": ["b", "c"], "estimated_effort": 1.3},
    ]
    schedule = compute_schedule(tasks)
    assert schedule.slack["a"] == 0.0
    assert schedule.slack["b"] == 0.0
    assert schedule.slack["d"] == 0.0
    assert schedule.critical_path == ("a", "b", "d")
    # c has real positive slack (b is heavier than c), reported cleanly rounded
    assert schedule.slack["c"] == 0.6
