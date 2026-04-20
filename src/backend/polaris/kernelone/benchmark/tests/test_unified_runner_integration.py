"""Unified Benchmark Runner Integration Tests.

These tests verify the unified benchmark runner works across all modes
(agentic, strategy, context) and includes regression tests for tool
equivalence groups.

.. deprecated::
    This module tests the legacy integration points. New code should use
    ``polaris.kernelone.benchmark.unified_runner.UnifiedBenchmarkRunner``
    directly with proper mocking of the roles.runtime streaming interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ObservedBenchmarkRun,
    ToolCallObservation,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.benchmark.unified_runner import (
    BenchmarkSuiteResult,
    UnifiedBenchmarkRunner,
)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> str:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


@pytest.fixture
def sample_agentic_case() -> UnifiedBenchmarkCase:
    """Create a sample agentic benchmark case."""
    return UnifiedBenchmarkCase(
        case_id="test_agentic_001",
        role="director",
        title="Test Agentic Case",
        prompt="Find and fix the bug in src/app.py",
        judge=JudgeConfig(
            required_tools=("search_code", "read_file"),
            mode="agentic",
        ),
    )


@pytest.fixture
def sample_strategy_case() -> UnifiedBenchmarkCase:
    """Create a sample strategy benchmark case."""
    return UnifiedBenchmarkCase(
        case_id="test_strategy_001",
        role="director",
        title="Test Strategy Case",
        prompt="Replay the recorded session",
        judge=JudgeConfig(
            required_tools=("read_file",),
            mode="strategy",
        ),
    )


@pytest.fixture
def sample_context_case() -> UnifiedBenchmarkCase:
    """Create a sample context benchmark case."""
    return UnifiedBenchmarkCase(
        case_id="test_context_001",
        role="director",
        title="Test Context Case",
        prompt="Evaluate context selection",
        judge=JudgeConfig(
            mode="context",
        ),
    )


class TestUnifiedRunnerIntegration:
    """Integration tests for all three benchmark modes."""

    @pytest.mark.asyncio
    async def test_agentic_mode_runs(
        self,
        temp_workspace: str,
        sample_agentic_case: UnifiedBenchmarkCase,
    ) -> None:
        """agentic mode can run without errors.

        Note: This tests that the runner can execute without actual
        roles.runtime connectivity. The case may fail validation but
        should not raise exceptions.
        """
        runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
        result = await runner.run_suite(
            cases=[sample_agentic_case],
            workspace=temp_workspace,
            mode="agentic",
        )
        assert result.mode == "agentic"
        assert result.total_cases == 1

    @pytest.mark.asyncio
    async def test_strategy_mode_handles_missing_replay(
        self,
        temp_workspace: str,
        sample_strategy_case: UnifiedBenchmarkCase,
    ) -> None:
        """strategy mode returns error observation for missing replay data.

        When no pre-recorded replay exists, the strategy adapter should
        return an error observation rather than raising an exception.
        """
        runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
        result = await runner.run_suite(
            cases=[sample_strategy_case],
            workspace=temp_workspace,
            mode="strategy",
        )
        assert result.mode == "strategy"
        assert result.total_cases == 1

    @pytest.mark.asyncio
    async def test_context_mode_runs(
        self,
        temp_workspace: str,
        sample_context_case: UnifiedBenchmarkCase,
    ) -> None:
        """context mode can run without errors."""
        runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
        result = await runner.run_suite(
            cases=[sample_context_case],
            workspace=temp_workspace,
            mode="context",
        )
        assert result.mode == "context"
        assert result.total_cases == 1

    @pytest.mark.asyncio
    async def test_run_suite_returns_valid_result_structure(
        self,
        temp_workspace: str,
        sample_agentic_case: UnifiedBenchmarkCase,
    ) -> None:
        """Verify BenchmarkSuiteResult has all required fields."""
        runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
        result = await runner.run_suite(
            cases=[sample_agentic_case],
            workspace=temp_workspace,
            mode="agentic",
        )

        # Check required attributes exist
        assert hasattr(result, "suite_name")
        assert hasattr(result, "run_id")
        assert hasattr(result, "mode")
        assert hasattr(result, "total_cases")
        assert hasattr(result, "passed_cases")
        assert hasattr(result, "failed_cases")
        assert hasattr(result, "average_score")
        assert hasattr(result, "results")
        assert hasattr(result, "timestamp")

        # Check run_id format
        assert result.run_id.startswith("bench-")

        # Check numeric fields are valid
        assert result.total_cases >= 0
        assert result.passed_cases >= 0
        assert result.failed_cases >= 0
        assert 0.0 <= result.average_score <= 1.0


class TestUnifiedJudgeRegression:
    """Regression tests to ensure unified judge works correctly."""

    def test_required_tools_equivalence_group_search_replace(
        self,
    ) -> None:
        """Equivalent tools satisfy required_tools check.

        When a case requires 'precision_edit' and the observed run uses
        'search_replace' (same equivalence group), the check should pass.
        """
        judge = UnifiedJudge()

        # Case requires precision_edit
        case = UnifiedBenchmarkCase(
            case_id="equiv_edit_test",
            role="director",
            title="Edit Test",
            prompt="Edit the file",
            judge=JudgeConfig(
                required_tools=("precision_edit",),
                mode="agentic",
            ),
        )

        # Observed uses equivalent tool search_replace
        observed = ObservedBenchmarkRun(
            case_id="equiv_edit_test",
            role="director",
            workspace="/tmp",
            output="File edited",
            tool_calls=(ToolCallObservation(tool="search_replace", args={}),),
        )

        verdict = judge.judge(case, observed)

        # Should pass because search_replace is in the same equivalence group
        required_tool_check = next(
            (c for c in verdict.checks if c.code == "required_tool:precision_edit"),
            None,
        )
        assert required_tool_check is not None
        assert required_tool_check.passed, (
            f"Equivalent tool search_replace should satisfy required_tools precision_edit. "
            f"Evidence: {required_tool_check.evidence}"
        )

    def test_required_tools_equivalence_group_read_file(
        self,
    ) -> None:
        """Equivalent read tools satisfy required_tools check.

        When a case requires 'read_file' and the observed run uses
        'repo_read_head' (same equivalence group), the check should pass.
        """
        judge = UnifiedJudge()

        case = UnifiedBenchmarkCase(
            case_id="equiv_read_test",
            role="director",
            title="Read Test",
            prompt="Read the file",
            judge=JudgeConfig(
                required_tools=("read_file",),
                mode="agentic",
            ),
        )

        # Observed uses equivalent tool repo_read_head
        observed = ObservedBenchmarkRun(
            case_id="equiv_read_test",
            role="director",
            workspace="/tmp",
            output="File content here",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )

        verdict = judge.judge(case, observed)

        required_tool_check = next(
            (c for c in verdict.checks if c.code == "required_tool:read_file"),
            None,
        )
        assert required_tool_check is not None
        assert required_tool_check.passed, (
            f"Equivalent tool repo_read_head should satisfy required_tools read_file. "
            f"Evidence: {required_tool_check.evidence}"
        )

    def test_required_tools_equivalence_group_repo_rg(
        self,
    ) -> None:
        """Equivalent search tools satisfy required_tools check.

        When a case requires 'repo_rg' and the observed run uses
        'grep' (same equivalence group), the check should pass.
        """
        judge = UnifiedJudge()

        case = UnifiedBenchmarkCase(
            case_id="equiv_search_test",
            role="director",
            title="Search Test",
            prompt="Search for pattern",
            judge=JudgeConfig(
                required_tools=("repo_rg",),
                mode="agentic",
            ),
        )

        # Observed uses equivalent tool grep
        observed = ObservedBenchmarkRun(
            case_id="equiv_search_test",
            role="director",
            workspace="/tmp",
            output="Found matches",
            tool_calls=(ToolCallObservation(tool="grep", args={"query": "test"}),),
        )

        verdict = judge.judge(case, observed)

        required_tool_check = next(
            (c for c in verdict.checks if c.code == "required_tool:repo_rg"),
            None,
        )
        assert required_tool_check is not None
        assert required_tool_check.passed, (
            f"Equivalent tool grep should satisfy required_tools repo_rg. Evidence: {required_tool_check.evidence}"
        )

    def test_forbidden_tools_still_enforced_with_equivalence(
        self,
    ) -> None:
        """Forbidden tools are still blocked even if they're in equivalence groups.

        When a tool is marked forbidden, using an equivalent tool should
        still fail the check.
        """
        judge = UnifiedJudge()

        case = UnifiedBenchmarkCase(
            case_id="forbidden_test",
            role="director",
            title="Forbidden Test",
            prompt="Edit the file",
            judge=JudgeConfig(
                forbidden_tools=("edit_file",),
                mode="agentic",
            ),
        )

        # Observed uses edit_file (forbidden)
        observed = ObservedBenchmarkRun(
            case_id="forbidden_test",
            role="director",
            workspace="/tmp",
            output="File edited",
            tool_calls=(ToolCallObservation(tool="edit_file", args={}),),
        )

        verdict = judge.judge(case, observed)

        forbidden_check = next(
            (c for c in verdict.checks if c.code == "forbidden_tool:edit_file"),
            None,
        )
        assert forbidden_check is not None
        assert not forbidden_check.passed, "forbidden_tool should fail even for equivalence group tools"

    def test_tool_equivalence_groups_completeness(
        self,
    ) -> None:
        """Verify all documented equivalence groups are in TOOL_EQUIVALENCE_GROUPS."""
        judge = UnifiedJudge()

        # Documented groups in unified_judge.py:
        expected_groups = {
            "search_replace": {"search_replace", "precision_edit", "repo_apply_diff", "edit_file"},
            "read_file": {"read_file", "repo_read_head", "repo_read_slice", "repo_read_tail", "repo_read_around"},
            "repo_rg": {"repo_rg", "grep", "ripgrep", "search_code", "precision_edit"},
            "repo_tree": {"repo_tree", "list_directory", "ls"},
        }

        for group_key, expected_members in expected_groups.items():
            actual_group = judge.TOOL_EQUIVALENCE_GROUPS.get(group_key, set())
            assert actual_group == expected_members, (
                f"Group {group_key} mismatch. Expected {expected_members}, got {actual_group}"
            )

    def test_non_equivalent_tools_do_not_satisfy_requirement(
        self,
    ) -> None:
        """Non-equivalent tools should not satisfy required_tools.

        Using a read tool when an edit tool is required should fail.
        """
        judge = UnifiedJudge()

        case = UnifiedBenchmarkCase(
            case_id="non_equiv_test",
            role="director",
            title="Non-Equivalent Test",
            prompt="Edit the file",
            judge=JudgeConfig(
                required_tools=("search_replace",),
                mode="agentic",
            ),
        )

        # Observed uses a non-equivalent read tool
        observed = ObservedBenchmarkRun(
            case_id="non_equiv_test",
            role="director",
            workspace="/tmp",
            output="File content",
            tool_calls=(ToolCallObservation(tool="read_file", args={}),),
        )

        verdict = judge.judge(case, observed)

        required_tool_check = next(
            (c for c in verdict.checks if c.code == "required_tool:search_replace"),
            None,
        )
        assert required_tool_check is not None
        assert not required_tool_check.passed, (
            "Non-equivalent tool read_file should NOT satisfy required_tools search_replace"
        )


class TestUnifiedRunnerProgressEvents:
    """Tests for progress event emission."""

    @pytest.mark.asyncio
    async def test_progress_events_emitted_for_all_phases(
        self,
        temp_workspace: str,
        sample_agentic_case: UnifiedBenchmarkCase,
    ) -> None:
        """Progress events should be emitted for suite start, case, and completion."""
        progress_events: list[dict[str, object]] = []

        def callback(event: dict[str, object]) -> None:
            progress_events.append(event)

        runner = UnifiedBenchmarkRunner(
            judge=UnifiedJudge(),
            progress_callback=callback,
        )

        await runner.run_suite(
            cases=[sample_agentic_case],
            workspace=temp_workspace,
            mode="agentic",
        )

        event_types = {e["type"] for e in progress_events}
        assert "suite_started" in event_types
        assert "case_started" in event_types
        assert "case_completed" in event_types
        assert "suite_completed" in event_types

    @pytest.mark.asyncio
    async def test_progress_event_contains_required_fields(
        self,
        temp_workspace: str,
        sample_agentic_case: UnifiedBenchmarkCase,
    ) -> None:
        """Each progress event should contain type and run_id."""
        progress_events: list[dict[str, object]] = []

        def callback(event: dict[str, object]) -> None:
            progress_events.append(event)

        runner = UnifiedBenchmarkRunner(
            judge=UnifiedJudge(),
            progress_callback=callback,
        )

        await runner.run_suite(
            cases=[sample_agentic_case],
            workspace=temp_workspace,
            mode="agentic",
        )

        for event in progress_events:
            assert "type" in event, f"Event missing 'type': {event}"
            assert "run_id" in event, f"Event missing 'run_id': {event}"


class TestUnifiedRunnerReportGeneration:
    """Tests for report generation."""

    def test_generate_report_creates_valid_structure(
        self,
        temp_workspace: str,
    ) -> None:
        """Report should have all required top-level fields."""
        runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())

        result = BenchmarkSuiteResult(
            suite_name="test_suite",
            run_id="bench-test-001",
            mode="agentic",
            total_cases=2,
            passed_cases=1,
            failed_cases=1,
            average_score=0.75,
        )

        report = runner.generate_report(result)

        # Check top-level fields
        assert "schema_version" in report
        assert "suite" in report
        assert "run_id" in report
        assert "timestamp" in report
        assert "mode" in report
        assert "summary" in report
        assert "final" in report
        assert "cases" in report

        # Check summary fields
        summary = report["summary"]
        assert "total_cases" in summary
        assert "passed_cases" in summary
        assert "failed_cases" in summary
        assert "average_score" in summary
        assert "pass_rate" in summary

        # Check final fields
        final = report["final"]
        assert "ready" in final
        assert "grade" in final
        assert "next_action" in final
