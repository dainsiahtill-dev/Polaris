"""Main runner logic for holographic benchmarks.

Thin shim that preserves the historical ``runner`` module surface after the
case executors were extracted into the sibling ``cases/`` package and the
dispatch table moved to ``registry``. The case-execution helper classes and
``_exec_tc_*`` executors now live in ``cases/*``; the ``EXECUTORS`` dispatch
dict and ``_select_cases`` live in ``registry``. The top-level
``run_case`` / ``run_holographic_suite`` entry points remain here.

Importing this module re-fires the import-time assembly of ``EXECUTORS`` via
``registry`` so the dispatch table is fully populated and identical to before.
"""

from __future__ import annotations

import time

from polaris.kernelone.benchmark.holographic.cases.knowledge_pipeline import (
    TempfileWorkspace,
)
from polaris.kernelone.benchmark.holographic.config import (
    HolographicRunResult,
    HolographicSuiteResult,
    RunStatus,
)
from polaris.kernelone.benchmark.holographic.registry import EXECUTORS, _select_cases
from polaris.kernelone.benchmark.holographic.stats import (
    _evaluate_thresholds,
    _now_iso,
    _perf_ms,
)
from polaris.kernelone.benchmark.holographic_models import HolographicCase

__all__ = [
    "EXECUTORS",
    "TempfileWorkspace",
    "run_case",
    "run_holographic_suite",
]


async def run_case(case: HolographicCase) -> HolographicRunResult:
    """Run one holographic benchmark case."""
    start_ns = time.perf_counter_ns()
    if not case.is_ready:
        return HolographicRunResult(
            case_id=case.case_id,
            status=RunStatus.SKIPPED,
            message=case.blocker or "case marked pending",
            duration_ms=_perf_ms(start_ns),
        )

    executor = EXECUTORS.get(case.case_id)
    if executor is None:
        return HolographicRunResult(
            case_id=case.case_id,
            status=RunStatus.SKIPPED,
            message="executor not implemented for ready case",
            duration_ms=_perf_ms(start_ns),
        )

    try:
        metrics = await executor(case)
    except (RuntimeError, ValueError) as exc:
        return HolographicRunResult(
            case_id=case.case_id,
            status=RunStatus.ERROR,
            message=f"execution error: {exc}",
            duration_ms=_perf_ms(start_ns),
        )

    failures = tuple(_evaluate_thresholds(metrics, case.thresholds))
    status = RunStatus.PASSED if not failures else RunStatus.FAILED
    return HolographicRunResult(
        case_id=case.case_id,
        status=status,
        metrics=metrics,
        failures=failures,
        duration_ms=_perf_ms(start_ns),
    )


async def run_holographic_suite(selected_case_ids: list[str] | None = None) -> HolographicSuiteResult:
    """Run selected holographic benchmark cases."""
    case_ids = set(selected_case_ids) if selected_case_ids else None
    selected_cases = _select_cases(case_ids)
    results: list[HolographicRunResult] = []
    for case in selected_cases:
        result = await run_case(case)
        results.append(result)
    passed = sum(1 for result in results if result.status == RunStatus.PASSED)
    failed = sum(1 for result in results if result.status == RunStatus.FAILED)
    skipped = sum(1 for result in results if result.status == RunStatus.SKIPPED)
    errored = sum(1 for result in results if result.status == RunStatus.ERROR)
    return HolographicSuiteResult(
        run_id=f"holo-{int(time.time())}",
        timestamp_utc=_now_iso(),
        total_cases=len(results),
        passed=passed,
        failed=failed,
        skipped=skipped,
        errored=errored,
        results=tuple(results),
    )
