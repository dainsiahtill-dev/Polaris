"""Unified Benchmark Framework - Execution Engine.

This module provides the unified execution engine for running benchmarks
across all modes (Agentic, Strategy, Context).

Design Patterns
--------------
- Facade Pattern: UnifiedBenchmarkRunner provides single entry point
- Template Method: _run_single_case defines execution skeleton
- Builder Pattern: Report generation is composable

Example
-------
    runner = UnifiedBenchmarkRunner()
    result = await runner.run_suite(
        cases=[case1, case2],
        workspace="/path/to/workspace",
        mode="agentic",
    )
    report = runner.generate_report(result, output_path="report.json")
"""

from __future__ import annotations

import contextlib
import json

# ------------------------------------------------------------------
# Helper: Windows-safe filesystem operations for sandbox fixtures
# ------------------------------------------------------------------
import os
import shutil
import stat
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TypeAlias

from polaris.kernelone.benchmark.adapters.context_adapter import ContextBenchmarkAdapter
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    BenchmarkMode,
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)
from polaris.kernelone.storage import resolve_runtime_path
from polaris.tests.benchmark.adapters.agentic_adapter import AgenticBenchmarkAdapter


def _rmtree_ignore_git(path: Path) -> None:
    """Remove directory tree, handling Windows .git permission issues."""

    def remove_readonly(
        func: Callable[..., Any], path: str, exc_info: tuple[type[BaseException], BaseException, Any]
    ) -> None:
        """Handle permission errors on Windows for .git directories."""
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        shutil.rmtree(path, onerror=remove_readonly)
    except PermissionError:
        # Fallback: manually remove .git folders first
        for root, dirs, _files in os.walk(path, topdown=False):
            for d in dirs:
                full = os.path.join(root, d)
                if full.endswith(".git"):
                    try:
                        os.chmod(full, os.stat(full).st_mode | stat.S_IWRITE)
                        shutil.rmtree(full, ignore_errors=True)
                    except (PermissionError, OSError):
                        pass
        # Retry main removal
        shutil.rmtree(path, ignore_errors=True)


def _copytree_exclude_git(src: Path, dst: Path) -> None:
    """Copy directory tree, excluding .git directories."""

    def copytree_with_exclude(src_dir: Path, dst_dir: Path, exclude_git: bool = True) -> None:
        exclude_git_dirs = {".git"} if exclude_git else set()
        os.makedirs(dst_dir, exist_ok=True)
        for item in os.listdir(src_dir):
            if item in exclude_git_dirs:
                continue
            src_item = os.path.join(src_dir, item)
            dst_item = os.path.join(dst_dir, item)
            if os.path.isdir(src_item):
                copytree_with_exclude(Path(src_item), Path(dst_item), exclude_git)
            else:
                shutil.copy2(src_item, dst_item)

    copytree_with_exclude(src, dst)


# ------------------------------------------------------------------
# Result Models
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkRunResult:
    """Result of running a single benchmark case.

    Attributes:
        case_id: The case that was executed.
        passed: Whether the case passed.
        score: The overall score achieved.
        duration_ms: Execution time in milliseconds.
        verdict: The complete judge verdict.
        error: Error message if execution failed.
    """

    case_id: str
    passed: bool
    score: float
    duration_ms: int
    verdict: UnifiedJudgeVerdict
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": round(self.score, 4),
            "duration_ms": self.duration_ms,
            "verdict": self.verdict.to_dict(),
            "error": self.error,
        }


@dataclass(frozen=True, kw_only=True)
class BenchmarkSuiteResult:
    """Result of running a complete benchmark suite.

    Attributes:
        suite_name: Name of the benchmark suite.
        run_id: Unique identifier for this run.
        mode: The benchmark mode that was used.
        total_cases: Total number of cases run.
        passed_cases: Number of cases that passed.
        failed_cases: Number of cases that failed.
        average_score: Average score across all cases.
        results: Individual case results.
        timestamp: ISO timestamp of when the run started.
        wall_time_ms: Total wall-clock time in milliseconds.
    """

    suite_name: str
    run_id: str
    mode: BenchmarkMode
    total_cases: int
    passed_cases: int
    failed_cases: int
    average_score: float
    results: tuple[BenchmarkRunResult, ...] = field(default_factory=tuple)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    wall_time_ms: int = 0

    @property
    def pass_rate(self) -> float:
        """Calculate pass rate as a fraction."""
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite_name,
            "run_id": self.run_id,
            "mode": self.mode,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "average_score": round(self.average_score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "results": [r.to_dict() for r in self.results],
            "timestamp": self.timestamp,
            "wall_time_ms": self.wall_time_ms,
        }


# ------------------------------------------------------------------
# Executor Protocol
# ------------------------------------------------------------------


class BenchmarkExecutorPort(Protocol):
    """Protocol for benchmark executors.

    This allows different execution backends to be plugged in,
    such as the roles.runtime streaming interface or offline replay.
    """

    def execute(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute a benchmark case and yield events.

        Args:
            case: The benchmark case to execute.
            workspace: The workspace path to use.

        Yields:
            Event dictionaries with type, content, tool calls, etc.
        """
        ...


# ------------------------------------------------------------------
# Progress Callback
# ------------------------------------------------------------------

ProgressCallback: TypeAlias = Callable[[dict[str, Any]], None]


# ------------------------------------------------------------------
# Unified Benchmark Runner
# ------------------------------------------------------------------


class UnifiedBenchmarkRunner:
    """Unified benchmark execution engine.

    This is the canonical entry point for running all types of benchmarks.
    It orchestrates case loading, execution, judgment, and report generation.

    Attributes:
        judge: The judge engine to use for verdicts.

    Example:
        runner = UnifiedBenchmarkRunner()
        result = await runner.run_suite(cases, workspace=".", mode="agentic")
    """

    def __init__(
        self,
        judge: UnifiedJudge | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            judge: Optional judge instance (creates default if not provided).
            progress_callback: Optional callback for progress events.
        """
        self._judge = judge or UnifiedJudge()
        self._progress_callback = progress_callback

    def _emit_progress(self, event: dict[str, Any]) -> None:
        """Emit a progress event if callback is configured."""
        if self._progress_callback:
            # Suppress progress callback errors - they should not crash execution
            with contextlib.suppress(Exception):
                self._progress_callback(event)

    async def run_suite(
        self,
        cases: list[UnifiedBenchmarkCase],
        *,
        workspace: str,
        run_id: str | None = None,
        mode: BenchmarkMode = "agentic",
        sandbox_base: str | None = None,
    ) -> BenchmarkSuiteResult:
        """Run a complete benchmark suite.

        Args:
            cases: List of benchmark cases to execute.
            workspace: Base workspace path.
            run_id: Optional run ID (auto-generated if not provided).
            mode: The benchmark mode to use.
            sandbox_base: Optional custom sandbox base directory.

        Returns:
            BenchmarkSuiteResult with complete results.
        """
        run_id = run_id or f"bench-{uuid.uuid4().hex[:8]}"
        wall_start = time.perf_counter()

        self._emit_progress(
            {
                "type": "suite_started",
                "run_id": run_id,
                "mode": mode,
                "total_cases": len(cases),
            }
        )

        results: list[BenchmarkRunResult] = []

        for index, case in enumerate(cases, start=1):
            self._emit_progress(
                {
                    "type": "case_started",
                    "run_id": run_id,
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.case_id,
                    "mode": mode,
                }
            )

            try:
                result = await self._run_single_case(
                    case=case,
                    workspace=workspace,
                    mode=mode,
                    sandbox_base=sandbox_base,
                )
            except (RuntimeError, ValueError) as exc:
                # Create error result for failed execution
                result = BenchmarkRunResult(
                    case_id=case.case_id,
                    passed=False,
                    score=0.0,
                    duration_ms=0,
                    verdict=UnifiedJudgeVerdict(
                        case_id=case.case_id,
                        passed=False,
                        score=0.0,
                        threshold=case.judge.score_threshold,
                        summary=f"execution error: {exc}",
                    ),
                    error=str(exc),
                )

            results.append(result)

            self._emit_progress(
                {
                    "type": "case_completed",
                    "run_id": run_id,
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.case_id,
                    "passed": result.passed,
                    "score": result.score,
                    "duration_ms": result.duration_ms,
                }
            )

        wall_time_ms = int((time.perf_counter() - wall_start) * 1000)

        suite_result = BenchmarkSuiteResult(
            suite_name="unified_benchmark",
            run_id=run_id,
            mode=mode,
            total_cases=len(results),
            passed_cases=sum(1 for r in results if r.passed),
            failed_cases=sum(1 for r in results if not r.passed),
            average_score=(sum(r.score for r in results) / len(results) if results else 0.0),
            results=tuple(results),
            wall_time_ms=wall_time_ms,
        )

        self._emit_progress(
            {
                "type": "suite_completed",
                "run_id": run_id,
                "mode": mode,
                "total_cases": suite_result.total_cases,
                "passed_cases": suite_result.passed_cases,
                "failed_cases": suite_result.failed_cases,
                "average_score": suite_result.average_score,
                "wall_time_ms": wall_time_ms,
            }
        )

        return suite_result

    async def _run_single_case(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        mode: BenchmarkMode,
        sandbox_base: str | None = None,
    ) -> BenchmarkRunResult:
        """Execute a single benchmark case.

        Args:
            case: The case to execute.
            workspace: Base workspace path.
            mode: The benchmark mode.
            sandbox_base: Optional custom sandbox directory.

        Returns:
            BenchmarkRunResult for this case.
        """
        start = time.perf_counter()

        # Materialize workspace if fixture is specified
        sandbox_workspace = self._materialize_workspace(
            base_workspace=workspace,
            case=case,
            sandbox_base=sandbox_base,
        )

        # Collect observation based on mode
        observed = await self._collect_observation(
            case=case,
            workspace=sandbox_workspace,
            mode=mode,
        )

        # Judge the observation
        verdict = self._judge.judge(case, observed)

        duration_ms = int((time.perf_counter() - start) * 1000)

        return BenchmarkRunResult(
            case_id=case.case_id,
            passed=verdict.passed,
            score=verdict.score,
            duration_ms=duration_ms,
            verdict=verdict,
        )

    def _materialize_workspace(
        self,
        base_workspace: str,
        case: UnifiedBenchmarkCase,
        sandbox_base: str | None = None,
    ) -> str:
        """Materialize a workspace from a fixture.

        If the case specifies a workspace_fixture, copies it to a sandbox
        directory. Otherwise, returns the base workspace unchanged.

        Args:
            base_workspace: The base workspace path.
            case: The benchmark case with optional fixture.
            sandbox_base: Optional custom sandbox base directory.

        Returns:
            Path to the materialized workspace.
        """
        if not case.workspace_fixture:
            return base_workspace

        fixture_root = Path(__file__).parent / "fixtures"
        fixture_dir = fixture_root / case.workspace_fixture

        if not fixture_dir.is_dir():
            # Try relative to polaris root
            polaris_root = Path(__file__).parent.parent.parent
            fixture_dir = (
                polaris_root
                / "cells"
                / "llm"
                / "evaluation"
                / "fixtures"
                / "agentic_benchmark"
                / "workspaces"
                / case.workspace_fixture
            )

        if not fixture_dir.is_dir():
            return base_workspace

        # Determine sandbox location
        sandbox_root = Path(resolve_runtime_path(sandbox_base or base_workspace, "runtime/benchmarks"))
        target_dir = sandbox_root / case.case_id

        # Clean existing sandbox (handle Windows .git permission issues)
        if target_dir.exists():
            _rmtree_ignore_git(target_dir)

        # Copy fixture, excluding .git directories (Windows can't rmdir .git)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        _copytree_exclude_git(fixture_dir, target_dir)

        return str(target_dir)

    async def _collect_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        mode: BenchmarkMode,
    ) -> ObservedBenchmarkRun:
        """Collect observation from execution.

        This dispatches to the appropriate mode-specific collector.

        Args:
            case: The case being executed.
            workspace: The workspace path.
            mode: The benchmark mode.

        Returns:
            ObservedBenchmarkRun with execution trace.
        """
        if mode == "agentic":
            return await self._collect_agentic_observation(case, workspace)
        elif mode == "strategy":
            return await self._collect_strategy_observation(case, workspace)
        else:
            return await self._collect_context_observation(case, workspace)

    async def _collect_agentic_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """Collect observation from Agentic benchmark execution.

        This uses the roles.runtime streaming interface.

        Args:
            case: The case to execute.
            workspace: The workspace path.

        Returns:
            ObservedBenchmarkRun with execution trace.
        """
        adapter = AgenticBenchmarkAdapter()
        events: list[dict[str, Any]] = []

        async for event in adapter.stream_session(case, workspace):
            events.append(event)

        return adapter.parse_observation(case, workspace, events)

    async def _collect_strategy_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """Collect observation from Strategy benchmark replay.

        This loads from pre-recorded strategy receipts.

        Args:
            case: The case to replay.
            workspace: The workspace path.

        Returns:
            ObservedBenchmarkRun with execution trace.
        """
        from polaris.kernelone.benchmark.adapters.strategy_adapter import (
            StrategyBenchmarkAdapter,
        )

        adapter = StrategyBenchmarkAdapter()
        result = adapter.load_observation(case, workspace)
        if result is None:
            return self._create_error_observation(
                case.case_id,
                case.role,
                workspace,
                "no strategy observation found for case",
            )
        return result

    async def _collect_context_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """Collect observation from Context benchmark evaluation.

        This evaluates context selection quality.

        Args:
            case: The case to evaluate.
            workspace: The workspace path.

        Returns:
            ObservedBenchmarkRun with context selection results.
        """
        adapter = ContextBenchmarkAdapter()
        return adapter.evaluate(case, workspace)

    def _create_error_observation(
        self,
        case_id: str,
        role: str,
        workspace: str,
        error_message: str,
    ) -> ObservedBenchmarkRun:
        """Create an error observation result."""
        return ObservedBenchmarkRun(
            case_id=case_id,
            role=role,
            workspace=workspace,
            output="",
            error=error_message,
        )

    def generate_report(
        self,
        result: BenchmarkSuiteResult,
        *,
        output_path: str | None = None,
        include_raw_events: bool = False,
    ) -> dict[str, Any]:
        """Generate a structured report from suite results.

        Args:
            result: The benchmark suite result.
            output_path: Optional path to write the report.
            include_raw_events: Whether to include full raw events.

        Returns:
            Report dictionary.
        """
        report: dict[str, Any] = {
            "schema_version": 1,
            "suite": result.suite_name,
            "run_id": result.run_id,
            "timestamp": result.timestamp,
            "mode": result.mode,
            "summary": {
                "total_cases": result.total_cases,
                "passed_cases": result.passed_cases,
                "failed_cases": result.failed_cases,
                "average_score": round(result.average_score, 4),
                "pass_rate": round(result.pass_rate, 4),
                "wall_time_ms": result.wall_time_ms,
            },
            "final": {
                "ready": result.passed_cases == result.total_cases,
                "grade": "PASS" if result.passed_cases == result.total_cases else "FAIL",
                "next_action": ("proceed" if result.passed_cases == result.total_cases else "fix_failures"),
            },
            "cases": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": round(r.score, 4),
                    "duration_ms": r.duration_ms,
                    "verdict": r.verdict.to_dict(),
                    "error": r.error,
                }
                for r in result.results
            ],
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        return report

    def list_workspace_files(self, workspace: str) -> list[str]:
        """List all files in a workspace recursively.

        Args:
            workspace: The workspace path.

        Returns:
            List of relative file paths.
        """
        root = Path(workspace)
        if not root.is_dir():
            return []

        results: list[str] = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    rel = path.relative_to(root).as_posix()
                    results.append(rel)
                except ValueError:
                    results.append(str(path))
        return results
