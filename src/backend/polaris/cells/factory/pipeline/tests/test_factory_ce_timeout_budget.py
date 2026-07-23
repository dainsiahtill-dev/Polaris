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
    deadline_epoch = datetime.now(timezone.utc).timestamp() + 800.0
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

    assert requested_timeout == 600
    assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
    assert decision.budget_plan.conserved is True
    assert decision.timeout_seconds > 240
    assert decision.timeout_seconds <= requested_timeout
