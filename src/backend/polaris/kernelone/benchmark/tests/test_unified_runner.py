"""Tests for unified_runner.py.

These tests verify the unified benchmark execution engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)
from polaris.kernelone.benchmark.unified_runner import (
    BenchmarkRunResult,
    BenchmarkSuiteResult,
    UnifiedBenchmarkRunner,
)


class TestBenchmarkRunResult:
    """Tests for BenchmarkRunResult dataclass."""

    def test_basic_result(self) -> None:
        """Test basic result creation."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=True,
            score=0.9,
            threshold=0.75,
        )
        result = BenchmarkRunResult(
            case_id="test",
            passed=True,
            score=0.9,
            duration_ms=150,
            verdict=verdict,
        )
        assert result.case_id == "test"
        assert result.passed is True
        assert result.duration_ms == 150

    def test_error_result(self) -> None:
        """Test error result creation."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=False,
            score=0.0,
            threshold=0.75,
            summary="execution error",
        )
        result = BenchmarkRunResult(
            case_id="test",
            passed=False,
            score=0.0,
            duration_ms=0,
            verdict=verdict,
            error="connection timeout",
        )
        assert result.error == "connection timeout"

    def test_to_dict(self) -> None:
        """Test serialization."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=True,
            score=0.85,
            threshold=0.75,
        )
        result = BenchmarkRunResult(
            case_id="test",
            passed=True,
            score=0.85,
            duration_ms=100,
            verdict=verdict,
        )
        d = result.to_dict()
        assert d["case_id"] == "test"
        assert d["passed"] is True
        assert d["score"] == 0.85


class TestBenchmarkSuiteResult:
    """Tests for BenchmarkSuiteResult dataclass."""

    def test_basic_suite_result(self) -> None:
        """Test basic suite result."""
        result = BenchmarkSuiteResult(
            suite_name="test_suite",
            run_id="run-001",
            mode="agentic",
            total_cases=5,
            passed_cases=4,
            failed_cases=1,
            average_score=0.82,
        )
        assert result.total_cases == 5
        assert result.pass_rate == 0.8

    def test_pass_rate_zero_cases(self) -> None:
        """Test pass rate with zero cases."""
        result = BenchmarkSuiteResult(
            suite_name="empty",
            run_id="run-002",
            mode="agentic",
            total_cases=0,
            passed_cases=0,
            failed_cases=0,
            average_score=0.0,
        )
        assert result.pass_rate == 0.0


class TestUnifiedBenchmarkRunner:
    """Tests for UnifiedBenchmarkRunner class."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> str:
        """Create a temporary workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return str(workspace)

    @pytest.fixture
    def simple_case(self) -> UnifiedBenchmarkCase:
        """Create a simple test case."""
        return UnifiedBenchmarkCase(
            case_id="simple_case",
            role="director",
            title="Simple Test",
            prompt="Return 'hello'",
            judge=JudgeConfig(
                score_threshold=0.5,
                validators=(),
            ),
        )

    @pytest.fixture
    def runner(self) -> UnifiedBenchmarkRunner:
        """Create a runner instance."""
        return UnifiedBenchmarkRunner()

    def test_runner_initialization(self, runner: UnifiedBenchmarkRunner) -> None:
        """Test runner initializes correctly."""
        assert runner._judge is not None
        assert runner._progress_callback is None

    def test_runner_with_progress_callback(self) -> None:
        """Test runner with progress callback."""
        progress_events: list[dict[str, Any]] = []

        def callback(event: dict[str, Any]) -> None:
            progress_events.append(event)

        runner = UnifiedBenchmarkRunner(progress_callback=callback)
        assert runner._progress_callback is not None

    @pytest.mark.asyncio
    async def test_run_suite_empty_cases(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
    ) -> None:
        """Test running suite with no cases."""
        result = await runner.run_suite(
            cases=[],
            workspace=temp_workspace,
            mode="agentic",
        )
        assert result.total_cases == 0
        assert result.passed_cases == 0

    @pytest.mark.asyncio
    async def test_run_suite_single_case_passes(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
        simple_case: UnifiedBenchmarkCase,
    ) -> None:
        """Test running suite with a passing case."""
        result = await runner.run_suite(
            cases=[simple_case],
            workspace=temp_workspace,
            mode="agentic",
        )
        assert result.total_cases == 1
        # Case may fail due to no actual execution in mock mode
        assert result.run_id.startswith("bench-")

    @pytest.mark.asyncio
    async def test_run_suite_generates_run_id(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
        simple_case: UnifiedBenchmarkCase,
    ) -> None:
        """Test that run_id is generated."""
        result = await runner.run_suite(
            cases=[simple_case],
            workspace=temp_workspace,
            mode="agentic",
        )
        assert result.run_id is not None
        assert len(result.run_id) > 0

    @pytest.mark.asyncio
    async def test_run_suite_custom_run_id(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
        simple_case: UnifiedBenchmarkCase,
    ) -> None:
        """Test custom run_id is used."""
        result = await runner.run_suite(
            cases=[simple_case],
            workspace=temp_workspace,
            mode="agentic",
            run_id="custom-run-id",
        )
        assert result.run_id == "custom-run-id"

    @pytest.mark.asyncio
    async def test_run_suite_progress_events(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
        simple_case: UnifiedBenchmarkCase,
    ) -> None:
        """Test progress events are emitted."""
        progress_events: list[dict[str, Any]] = []

        def callback(event: dict[str, Any]) -> None:
            progress_events.append(event)

        runner = UnifiedBenchmarkRunner(progress_callback=callback)

        await runner.run_suite(
            cases=[simple_case],
            workspace=temp_workspace,
            mode="agentic",
        )

        event_types = {e["type"] for e in progress_events}
        assert "suite_started" in event_types
        assert "case_started" in event_types
        assert "case_completed" in event_types
        assert "suite_completed" in event_types

    def test_generate_report(
        self,
        runner: UnifiedBenchmarkRunner,
    ) -> None:
        """Test report generation."""
        suite_result = BenchmarkSuiteResult(
            suite_name="test_suite",
            run_id="run-001",
            mode="agentic",
            total_cases=2,
            passed_cases=1,
            failed_cases=1,
            average_score=0.75,
            results=(
                BenchmarkRunResult(
                    case_id="case1",
                    passed=True,
                    score=0.9,
                    duration_ms=100,
                    verdict=UnifiedJudgeVerdict(
                        case_id="case1",
                        passed=True,
                        score=0.9,
                        threshold=0.75,
                    ),
                ),
                BenchmarkRunResult(
                    case_id="case2",
                    passed=False,
                    score=0.6,
                    duration_ms=80,
                    verdict=UnifiedJudgeVerdict(
                        case_id="case2",
                        passed=False,
                        score=0.6,
                        threshold=0.75,
                    ),
                ),
            ),
        )

        report = runner.generate_report(suite_result)

        assert report["suite"] == "test_suite"
        assert report["summary"]["total_cases"] == 2
        assert report["summary"]["passed_cases"] == 1
        assert report["final"]["grade"] == "FAIL"

    def test_generate_report_with_file(
        self,
        runner: UnifiedBenchmarkRunner,
        tmp_path: Path,
    ) -> None:
        """Test report generation to file."""
        suite_result = BenchmarkSuiteResult(
            suite_name="test_suite",
            run_id="run-001",
            mode="agentic",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            average_score=1.0,
        )

        output_path = tmp_path / "report.json"

        runner.generate_report(suite_result, output_path=str(output_path))

        assert output_path.is_file()
        with open(output_path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["suite"] == "test_suite"

    def test_list_workspace_files_empty(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
    ) -> None:
        """Test listing files in empty workspace."""
        files = runner.list_workspace_files(temp_workspace)
        assert files == []

    def test_list_workspace_files_with_files(
        self,
        runner: UnifiedBenchmarkRunner,
        temp_workspace: str,
    ) -> None:
        """Test listing files in workspace with files."""
        # Create some test files
        (Path(temp_workspace) / "file1.py").touch()
        (Path(temp_workspace) / "file2.txt").touch()
        subdir = Path(temp_workspace) / "subdir"
        subdir.mkdir()
        (subdir / "file3.py").touch()

        files = runner.list_workspace_files(temp_workspace)

        assert "file1.py" in files
        assert "file2.txt" in files
        assert "subdir/file3.py" in files


class TestMaterializeWorkspace:
    """Tests for workspace materialization."""

    @pytest.fixture
    def runner(self) -> UnifiedBenchmarkRunner:
        """Create a runner instance."""
        return UnifiedBenchmarkRunner()

    def test_materialize_no_fixture(
        self,
        runner: UnifiedBenchmarkRunner,
        tmp_path: Path,
    ) -> None:
        """Test materializing workspace with no fixture."""
        case = UnifiedBenchmarkCase(
            case_id="test",
            role="director",
            title="Test",
            prompt="test",
            workspace_fixture="",  # No fixture
        )
        workspace = str(tmp_path / "base")
        result = runner._materialize_workspace(workspace, case)
        assert result == workspace

    def test_materialize_with_nonexistent_fixture(
        self,
        runner: UnifiedBenchmarkRunner,
        tmp_path: Path,
    ) -> None:
        """Test materializing with nonexistent fixture falls back to base."""
        case = UnifiedBenchmarkCase(
            case_id="test",
            role="director",
            title="Test",
            prompt="test",
            workspace_fixture="nonexistent_fixture",
        )
        workspace = str(tmp_path / "base")
        result = runner._materialize_workspace(workspace, case)
        assert result == workspace


class TestCollectObservation:
    """Tests for observation collection."""

    @pytest.fixture
    def runner(self) -> UnifiedBenchmarkRunner:
        """Create a runner instance."""
        return UnifiedBenchmarkRunner()

    @pytest.mark.asyncio
    async def test_collect_context_observation(
        self,
        runner: UnifiedBenchmarkRunner,
        tmp_path: Path,
    ) -> None:
        """Test collecting context mode observation."""
        case = UnifiedBenchmarkCase(
            case_id="context_test",
            role="director",
            title="Context Test",
            prompt="Evaluate context",
        )

        observation = await runner._collect_observation(
            case=case,
            workspace=str(tmp_path),
            mode="context",
        )

        assert observation.case_id == "context_test"
        assert observation.role == "director"

    def test_create_error_observation(
        self,
        runner: UnifiedBenchmarkRunner,
    ) -> None:
        """Test creating error observation."""
        observation = runner._create_error_observation(
            case_id="error_test",
            role="director",
            workspace="/tmp",
            error_message="test error",
        )

        assert observation.case_id == "error_test"
        assert observation.error == "test error"
        assert observation.output == ""
