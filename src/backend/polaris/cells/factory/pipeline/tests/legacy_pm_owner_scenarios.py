"""Named historical input scenarios, never current run admission or authority.

The serialized PM document is an explicit synthetic fixture. Existing Factory
persistence and CE capture/portfolio owners execute their actual implementations.
Only public CE DTOs and copied input observations cross this test boundary.
Production code must not import this test module.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict

import pytest
from polaris.cells.chief_engineer.blueprint.public import ChiefEngineerBlueprintPortfolioV1
from polaris.cells.factory.pipeline.tests.test_formal_project_identity import (
    PROJECT as LEGACY_PROJECT_ID,
    TASK as LEGACY_TASK_ID,
    _persisted_legacy_owner,
)

__all__ = ["LEGACY_PROJECT_ID", "LEGACY_TASK_ID", "produce_synthetic_legacy_ce_portfolio"]


class SyntheticLegacyCEPortfolioCase(TypedDict):
    factory_run_id: str
    pm_tasks: list[dict[str, Any]]
    portfolio: ChiefEngineerBlueprintPortfolioV1


async def produce_synthetic_legacy_ce_portfolio(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    structured_output: dict[str, Any],
    project_id: str = LEGACY_PROJECT_ID,
) -> SyntheticLegacyCEPortfolioCase:
    """Seed historical inputs, then return actual public CE output and data."""
    run, executor, tasks = await _persisted_legacy_owner(workspace, monkeypatch, metadata={"project_id": project_id})
    portfolio_tasks = executor._chief_engineer_portfolio_tasks(tasks)
    _authority, carrier = await executor._capture_chief_engineer_portfolio_authority(
        run=run, pm_tasks=tasks, portfolio_tasks=portfolio_tasks
    )
    portfolio = executor._build_chief_engineer_portfolio_from_candidate(
        run_id=run.id,
        tasks=portfolio_tasks,
        authority_carrier=carrier,
        structured_output=deepcopy(structured_output),
    )
    assert isinstance(portfolio, ChiefEngineerBlueprintPortfolioV1)
    return {
        "factory_run_id": run.id,
        "pm_tasks": deepcopy(tasks),
        "portfolio": portfolio,
    }
