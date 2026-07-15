"""Contract tests for Factory dependency-aware deadline admission."""

from __future__ import annotations

from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
    resolve_chief_engineer_portfolio_admission,
    resolve_director_dispatch_admission,
)


def _policy() -> FactoryDeadlineBudgetPolicyV1:
    return FactoryDeadlineBudgetPolicyV1(
        chief_engineer_min_start_seconds=40.0,
        director_first_task_min_seconds=150.0,
        director_followup_task_min_seconds=40.0,
        quality_gate_reserved_seconds=120.0,
        safety_seconds=5.0,
        director_settlement_barrier_seconds=5.0,
    )


def _serial_tasks(count: int = 5) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    for index in range(1, count + 1):
        task_id = f"TASK-{index}"
        tasks.append(
            {
                "id": task_id,
                "depends_on": [] if index == 1 else [f"TASK-{index - 1}"],
            }
        )
    return tasks


def test_dependency_schedule_uses_critical_path_not_raw_task_count() -> None:
    schedule = build_task_dependency_schedule(
        [
            {"id": "foundation", "depends_on": []},
            {"id": "api", "depends_on": ["foundation"]},
            {"id": "web", "depends_on": ["foundation"]},
            {"id": "qa", "depends_on": ["api", "web"]},
        ]
    )

    assert schedule.valid is True
    assert schedule.dependency_edge_count == 4
    assert schedule.critical_path_task_count == 3


def test_dependency_schedule_recomputes_only_unresolved_path() -> None:
    schedule = build_task_dependency_schedule(
        _serial_tasks(),
        active_task_ids=["TASK-3", "TASK-4", "TASK-5"],
    )

    assert schedule.valid is True
    assert schedule.active_task_ids == ("TASK-3", "TASK-4", "TASK-5")
    assert schedule.critical_path_task_count == 3


def test_dependency_schedule_fails_closed_for_unknown_dependency() -> None:
    schedule = build_task_dependency_schedule([{"id": "TASK-1", "depends_on": ["MISSING"]}])

    assert schedule.valid is False
    assert schedule.critical_path_task_count == 1
    assert schedule.blockers == ("unknown_dependencies:TASK-1:MISSING",)


def test_dependency_schedule_fails_closed_for_cycle() -> None:
    schedule = build_task_dependency_schedule(
        [
            {"id": "TASK-1", "depends_on": ["TASK-2"]},
            {"id": "TASK-2", "depends_on": ["TASK-1"]},
        ]
    )

    assert schedule.valid is False
    assert schedule.critical_path_task_count == 2
    assert any(blocker.startswith("dependency_cycle:") for blocker in schedule.blockers)


def test_ce_portfolio_admission_reserves_entire_director_critical_path() -> None:
    decision = resolve_chief_engineer_portfolio_admission(
        remaining_seconds=540.0,
        requested_timeout_seconds=240,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.reserved_downstream_seconds == 460.0
    assert decision.available_for_stage_seconds == 80.0
    assert decision.timeout_seconds == 80
    assert decision.execution_timeout_seconds == 80
    assert decision.settlement_timeout_seconds == 0
    assert decision.reservation_breakdown == {
        "director_first_wave": 155,
        "director_followup_waves": 180,
        "qa_finalization": 120,
        "safety": 5,
    }


def test_ce_portfolio_admission_projects_when_full_chain_cannot_fit() -> None:
    decision = resolve_chief_engineer_portfolio_admission(
        remaining_seconds=460.0,
        requested_timeout_seconds=240,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
    assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
    assert decision.timeout_seconds == 0
    assert decision.execution_timeout_seconds == 0
    assert decision.settlement_timeout_seconds == 0


def test_director_admission_reserves_future_tasks_and_quality_gate() -> None:
    decision = resolve_director_dispatch_admission(
        remaining_seconds=470.0,
        requested_timeout_seconds=600,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        first_materialization_pending=True,
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.reserved_downstream_seconds == 305.0
    assert decision.available_for_stage_seconds == 165.0
    assert decision.minimum_start_budget_seconds == 150.0
    assert decision.timeout_seconds == 165
    assert decision.execution_timeout_seconds == 160
    assert decision.settlement_timeout_seconds == 5
    assert decision.execution_timeout_seconds + decision.settlement_timeout_seconds == decision.timeout_seconds
    assert decision.reservation_breakdown["current_wave_execution"] == 160
    assert decision.reservation_breakdown["current_wave_settlement"] == 5


def test_director_admission_blocks_instead_of_starving_future_chain() -> None:
    decision = resolve_director_dispatch_admission(
        remaining_seconds=420.0,
        requested_timeout_seconds=600,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        first_materialization_pending=True,
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
    assert decision.reason == "insufficient_factory_deadline_for_director_dispatch"
    assert decision.timeout_seconds == 0
    assert decision.execution_timeout_seconds == 0
    assert decision.settlement_timeout_seconds == 0


def test_admission_without_factory_deadline_preserves_requested_timeout() -> None:
    decision = resolve_chief_engineer_portfolio_admission(
        remaining_seconds=None,
        requested_timeout_seconds=240,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.timeout_seconds == 240
    assert decision.execution_timeout_seconds == 240
    assert decision.settlement_timeout_seconds == 0
    assert decision.remaining_seconds is None


def test_director_admission_without_deadline_keeps_settlement_inside_requested_lease() -> None:
    decision = resolve_director_dispatch_admission(
        remaining_seconds=None,
        requested_timeout_seconds=240,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks()),
        first_materialization_pending=True,
        policy=_policy(),
    )

    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.timeout_seconds == 240
    assert decision.execution_timeout_seconds == 235
    assert decision.settlement_timeout_seconds == 5
    assert decision.to_dict()["execution_timeout_seconds"] == 235
    assert decision.to_dict()["settlement_timeout_seconds"] == 5


def test_director_minimum_applies_to_execution_not_total_stage_lease() -> None:
    blocked = resolve_director_dispatch_admission(
        remaining_seconds=None,
        requested_timeout_seconds=150,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks(1)),
        first_materialization_pending=True,
        policy=_policy(),
    )
    admitted = resolve_director_dispatch_admission(
        remaining_seconds=None,
        requested_timeout_seconds=155,
        dependency_schedule=build_task_dependency_schedule(_serial_tasks(1)),
        first_materialization_pending=True,
        policy=_policy(),
    )

    assert blocked.disposition is FactoryDeadlineDispositionV1.BLOCK
    assert blocked.budget_plan.blockers == ("requested_timeout_below_director_stage_lease_minimum",)
    assert admitted.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert admitted.execution_timeout_seconds == 150
    assert admitted.settlement_timeout_seconds == 5
