"""Regression coverage for Factory Chief Engineer timeout admission.

All text in this module is UTF-8.
"""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from pytest import MonkeyPatch


def test_default_ce_portfolio_timeout_can_consume_conserved_factory_budget(
    monkeypatch: MonkeyPatch,
) -> None:
    """A slow structured CE call must not retain the obsolete 240-second cap."""

    for env_key in stage_executor_module._CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
        monkeypatch.delenv(env_key, raising=False)

    requested_timeout = OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds({})
    deadline_epoch = datetime.now(timezone.utc).timestamp() + 1200.0
    decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
        {"factory_run_deadline_epoch_seconds": deadline_epoch},
        requested_timeout_seconds=requested_timeout,
        dependency_schedule=build_task_dependency_schedule(
            [
                {"id": "TASK-1", "depends_on": []},
                {"id": "TASK-2", "depends_on": ["TASK-1"]},
                {"id": "TASK-3", "depends_on": ["TASK-2"]},
            ]
        ),
    )

    assert requested_timeout == 750
    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.budget_plan.conserved is True
    assert decision.timeout_seconds > 240
    assert decision.timeout_seconds <= requested_timeout


def test_explicit_short_ce_timeout_blocks_even_with_ample_or_unbounded_horizon():
    from polaris.cells.factory.pipeline.internal.factory_deadline_calculations import (
        chief_engineer_deadline_projection_decision,
        chief_engineer_llm_timeout_seconds,
    )

    schedule = build_task_dependency_schedule([{"id": "TASK-1"}])
    for context in (
        {"timeout_seconds": 600},
        {"timeout_seconds": 600, "factory_run_deadline_epoch_seconds": datetime.now(timezone.utc).timestamp() + 2000},
    ):
        assert chief_engineer_llm_timeout_seconds(context) == 600
        decision = chief_engineer_deadline_projection_decision(
            context, requested_timeout_seconds=600, dependency_schedule=schedule, output_tokens=60000
        )
        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.timeout_seconds == 0
        assert "chief_engineer_deadline_below_generation_floor" in decision.budget_plan.blockers


def test_ce_default_timeout_and_lease_follow_trusted_budget_override(monkeypatch):
    from polaris.cells.factory.pipeline.internal.factory_deadline_calculations import (
        chief_engineer_execution_attempt_lease_budget,
        chief_engineer_llm_timeout_seconds,
    )

    for key in stage_executor_module._CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert chief_engineer_llm_timeout_seconds({"llm_max_tokens": 64000}) == 800
    assert (
        chief_engineer_llm_timeout_seconds({"task_execution_contract": {"context_budget": {"max_tokens": 48000}}})
        == 600
    )
    lease = chief_engineer_execution_attempt_lease_budget(800)
    assert lease.lease_ttl_seconds == 830
    assert lease.heartbeat_interval_seconds == 30
