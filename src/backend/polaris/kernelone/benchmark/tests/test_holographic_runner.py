"""Tests for holographic runner."""

from __future__ import annotations

import pytest
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_registry import (
    HOLOGRAPHIC_CASES,
    ready_case_ids,
)
from polaris.kernelone.benchmark.holographic_runner import (
    EXECUTORS,
    RunStatus,
    run_case,
    run_holographic_suite,
)


def _find_case(case_id: str) -> HolographicCase:
    for case in HOLOGRAPHIC_CASES:
        if case.case_id == case_id:
            return case
    raise AssertionError(f"missing case: {case_id}")


@pytest.mark.asyncio
async def test_run_case_executes_previously_pending_definition() -> None:
    case = _find_case("TC-PHX-002")
    result = await run_case(case)
    assert result.status in (RunStatus.PASSED, RunStatus.FAILED)
    assert "fallback_p99_ms" in result.metrics


@pytest.mark.asyncio
async def test_run_case_executes_available_executor() -> None:
    case = _find_case("TC-PHX-004")
    result = await run_case(case)
    assert result.status in (RunStatus.PASSED, RunStatus.FAILED)
    assert "diff_rate_percent" in result.metrics
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_suite_run_selected_cases() -> None:
    suite = await run_holographic_suite(["TC-PHX-002", "TC-PHX-004", "TC-QM-003"])
    assert suite.total_cases == 3
    assert suite.skipped == 0
    assert suite.passed + suite.failed + suite.skipped + suite.errored == suite.total_cases


def test_all_ready_cases_have_executors() -> None:
    ready_ids = set(ready_case_ids())
    executor_ids = set(EXECUTORS.keys())
    missing = ready_ids - executor_ids
    assert not missing, f"missing executors for ready cases: {sorted(missing)}"


@pytest.mark.asyncio
async def test_new_ready_case_executor_runs_without_skip() -> None:
    case = _find_case("TC-ER-004")
    result = await run_case(case)
    assert result.status in (RunStatus.PASSED, RunStatus.FAILED)


@pytest.mark.asyncio
async def test_cognitive_case_executor_runs_without_skip() -> None:
    case = _find_case("TC-COG-001")
    result = await run_case(case)
    assert result.status in (RunStatus.PASSED, RunStatus.FAILED)
    assert "decision_p99_ms" in result.metrics
