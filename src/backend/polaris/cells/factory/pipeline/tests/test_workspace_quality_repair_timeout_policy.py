"""Factory and Director must share one bounded quality-repair timeout policy."""

from __future__ import annotations

import time

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import OrchestrationStageExecutor
from polaris.kernelone.llm.budget_policy import DIRECTOR_QUALITY_REPAIR_TIMEOUT_SECONDS


def test_workspace_quality_repair_uses_shared_director_budget_when_deadline_is_ample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: live L3-22 had 27 minutes left but Factory forced 90 seconds."""

    monkeypatch.delenv("KERNELONE_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS", raising=False)
    timeout_seconds = OrchestrationStageExecutor._workspace_quality_llm_repair_timeout_seconds(
        {"factory_run_deadline_epoch_seconds": time.time() + 1_800.0}
    )

    assert timeout_seconds == DIRECTOR_QUALITY_REPAIR_TIMEOUT_SECONDS


def test_workspace_quality_repair_still_caps_shared_budget_at_factory_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KERNELONE_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS", raising=False)
    timeout_seconds = OrchestrationStageExecutor._workspace_quality_llm_repair_timeout_seconds(
        {"factory_run_deadline_epoch_seconds": time.time() + 65.0}
    )

    assert 59.0 <= timeout_seconds <= 60.0


def test_workspace_quality_repair_keeps_explicit_lower_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_WORKSPACE_QUALITY_REPAIR_LLM_TIMEOUT_SECONDS", "45")

    assert OrchestrationStageExecutor._workspace_quality_llm_repair_timeout_seconds({}) == 45.0
