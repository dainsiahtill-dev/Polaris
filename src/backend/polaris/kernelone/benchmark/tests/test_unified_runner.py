"""Regression tests for UnifiedBenchmarkRunner suite execution and judging.

Covers two confirmed defects:

* run_suite must not abort the whole suite when a single case raises a
  non-RuntimeError/ValueError exception (e.g. OSError during workspace
  materialization). It should record the failed case and keep going.
* _run_single_case must pass the materialized workspace's real file listing
  into the judge so the no_hallucinated_paths validator has ground-truth
  known_paths instead of short-circuiting vacuously.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.benchmark.unified_runner import (
    BenchmarkRunResult,
    UnifiedBenchmarkRunner,
)


def _make_case(case_id: str, *, validators: tuple[str, ...] = ()) -> UnifiedBenchmarkCase:
    """Build a minimal benchmark case targeting the QA role."""
    return UnifiedBenchmarkCase(
        case_id=case_id,
        role="qa",
        title=case_id,
        prompt="inspect the workspace",
        judge=JudgeConfig(score_threshold=0.5, validators=validators),
    )


def test_run_suite_continues_after_oserror_in_one_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-case OSError must be recorded as a failed case, not abort the suite."""
    runner = UnifiedBenchmarkRunner()

    failing_case = _make_case("case-oserror")
    healthy_case = _make_case("case-ok")

    async def _fake_run_single_case(
        *,
        case: UnifiedBenchmarkCase,
        workspace: str,
        mode: str,
        sandbox_base: str | None = None,
    ) -> Any:
        if case.case_id == "case-oserror":
            # Mimics _materialize_workspace failing on disk-full / permission /
            # broken symlink — an OSError that is NOT a RuntimeError/ValueError.
            raise OSError("disk full while materializing fixture")
        observed = ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output="all good",
        )
        verdict = runner._judge.judge(case, observed)
        return BenchmarkRunResult(
            case_id=case.case_id,
            passed=verdict.passed,
            score=verdict.score,
            duration_ms=0,
            verdict=verdict,
        )

    # Patch _run_single_case to exercise run_suite's per-case guard directly.
    monkeypatch.setattr(runner, "_run_single_case", _fake_run_single_case)

    suite = asyncio.run(
        runner.run_suite(
            cases=[failing_case, healthy_case],
            workspace=".",
            mode="agentic",
        )
    )

    assert suite.total_cases == 2
    results_by_id = {r.case_id: r for r in suite.results}
    # The failing case is recorded, not propagated.
    assert results_by_id["case-oserror"].passed is False
    assert "disk full" in results_by_id["case-oserror"].error
    # The remaining case still ran.
    assert "case-ok" in results_by_id


def test_run_suite_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BaseException-level interruptions must still abort the suite."""
    runner = UnifiedBenchmarkRunner()
    case = _make_case("case-interrupt")

    async def _interrupt(**_kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_run_single_case", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(runner.run_suite(cases=[case], workspace=".", mode="agentic"))


def test_hallucinated_path_validator_receives_known_paths(tmp_path: Path) -> None:
    """The no_hallucinated_paths validator must run against real workspace files.

    Before the fix, _run_single_case judged without workspace_files, so the
    validator short-circuited (`if not known_paths: return True`) and a
    fabricated path passed vacuously.
    """
    # Materialize a workspace with a known file.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    runner = UnifiedBenchmarkRunner()
    judge = UnifiedJudge()

    fabricated_case = _make_case("case-fabricated", validators=("no_hallucinated_paths",))
    grounded_case = _make_case("case-grounded", validators=("no_hallucinated_paths",))

    workspace_files = runner.list_workspace_files(str(tmp_path))
    assert "src/main.py" in workspace_files

    # An answer referencing a path that does not exist in the workspace.
    fabricated_observed = ObservedBenchmarkRun(
        case_id=fabricated_case.case_id,
        role=fabricated_case.role,
        workspace=str(tmp_path),
        output="The fix lives in lib/totally_fake_module.py and config/ghost.yaml",
    )
    # An answer referencing only real files.
    grounded_observed = ObservedBenchmarkRun(
        case_id=grounded_case.case_id,
        role=grounded_case.role,
        workspace=str(tmp_path),
        output="The entry point is src/main.py",
    )

    fabricated_verdict = judge.judge(fabricated_case, fabricated_observed, workspace_files=workspace_files)
    grounded_verdict = judge.judge(grounded_case, grounded_observed, workspace_files=workspace_files)

    fabricated_check = next(c for c in fabricated_verdict.checks if c.code == "validator:no_hallucinated_paths")
    grounded_check = next(c for c in grounded_verdict.checks if c.code == "validator:no_hallucinated_paths")

    assert fabricated_check.passed is False
    assert grounded_check.passed is True

    # Sanity: with NO workspace_files the validator short-circuits to pass —
    # this is exactly the vacuous behavior the runner fix avoids.
    vacuous_verdict = judge.judge(fabricated_case, fabricated_observed)
    vacuous_check = next(c for c in vacuous_verdict.checks if c.code == "validator:no_hallucinated_paths")
    assert vacuous_check.passed is True
