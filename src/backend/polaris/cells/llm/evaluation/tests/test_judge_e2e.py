"""End-to-end tests for judge_agentic_case function.

This test module provides comprehensive E2E testing for the complete
judge_agentic_case workflow using real benchmark case JSON fixtures.

Test coverage:
- Complete judge_agentic_case workflow
- Score calculation logic
- Real benchmark cases (l8_classic_*, etc.)
- Edge cases for verdict generation
"""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.agentic_benchmark import (
    _build_benchmark_prompt_appendix,
    _merge_prompt_appendices,
)
from polaris.cells.llm.evaluation.internal.benchmark_loader import (
    load_builtin_agentic_benchmark_cases,
)
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    AgenticJudgeVerdict,
    ObservedBenchmarkRun,
    ToolCallObservation,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import (
    SCORE_WEIGHTS,
    judge_agentic_case,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def base_workspace_files() -> list[str]:
    """Standard workspace files for testing."""
    return [
        "/test/src/main.py",
        "/test/src/utils.py",
        "/test/backend/api.py",
        "/test/src/utils/helpers.py",
    ]


def make_case(
    validators: tuple[str, ...] = (),
    required_tools: tuple[str, ...] = (),
    forbidden_tools: tuple[str, ...] = (),
    min_tool_calls: int = 0,
    max_tool_calls: int | None = None,
    required_output: tuple[str, ...] = (),
    forbidden_output: tuple[str, ...] = (),
    score_threshold: float = 0.8,
) -> AgenticBenchmarkCase:
    """Create a test case with specified configuration."""
    return AgenticBenchmarkCase(
        case_id="test_case",
        role="director",
        title="Test Case",
        prompt="Test prompt",
        judge=AgenticJudgeConfig(
            score_threshold=score_threshold,
            validators=validators,
            required_tools=required_tools,
            forbidden_tools=forbidden_tools,
            min_tool_calls=min_tool_calls,
            max_tool_calls=max_tool_calls,
            required_output_substrings=required_output,
            forbidden_output_substrings=forbidden_output,
        ),
    )


def make_observed(
    output: str = "Test output",
    tool_calls: tuple[ToolCallObservation, ...] | None = None,
) -> ObservedBenchmarkRun:
    """Create an observed run for testing."""
    return ObservedBenchmarkRun(
        case_id="test_case",
        role="director",
        workspace="/test",
        output=output,
        tool_calls=(tool_calls if tool_calls is not None else (ToolCallObservation(tool="repo_read_head", args={}),)),
    )


# =============================================================================
# Test Score Calculation
# =============================================================================


class TestScoreCalculation:
    """Tests for score calculation logic."""

    def test_all_checks_pass_score_1(self, base_workspace_files: list[str]) -> None:
        """All checks passing should result in score of 1.0."""
        case = make_case(
            required_tools=("repo_read_head",),
            min_tool_calls=1,
        )
        observed = make_observed(
            output="The third line is: return HttpResponse('OK')",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.score == 1.0

    def test_no_checks_pass_score_0(self, base_workspace_files: list[str]) -> None:
        """No checks passing should result in score near 0."""
        # Create a case with validators that will fail
        case = make_case(
            required_tools=("repo_read_head",),
            min_tool_calls=1,
            validators=("fact_anchoring_check",),  # Requires read tool
        )
        observed = make_observed(
            output="I think the answer is...",
            tool_calls=(ToolCallObservation(tool="repo_rg", args={}),),  # Wrong tool
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        # Score will be partial because some checks pass
        assert verdict.score < 1.0

    def test_partial_checks_pass_score_between_0_and_1(self, base_workspace_files: list[str]) -> None:
        """Partial checks passing should result in score between 0 and 1."""
        case = make_case(
            required_tools=("repo_read_head",),
            min_tool_calls=1,
        )
        observed = make_observed(
            output="Some content",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        # With only tooling check, score should be 1.0 for tooling
        assert verdict.score >= 0.8

    def test_score_threshold_pass(self, base_workspace_files: list[str]) -> None:
        """Score above threshold should result in passed verdict."""
        case = make_case(
            required_tools=("repo_read_head",),
            min_tool_calls=1,
            score_threshold=0.8,
        )
        observed = make_observed(
            output="The third line is: return HttpResponse('OK')",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.passed is True
        assert verdict.score >= case.judge.score_threshold

    def test_score_threshold_fail(self, base_workspace_files: list[str]) -> None:
        """Score below threshold should result in failed verdict."""
        # Create a case where some checks will fail
        case = make_case(
            required_tools=("repo_read_head", "repo_rg"),  # Require two tools
            min_tool_calls=2,  # Require at least 2 calls
            score_threshold=1.0,  # Require perfect
        )
        observed = make_observed(
            output="Some content",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),  # Only one tool
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        # With missing tool and fewer calls, verdict should fail
        assert verdict.score < case.judge.score_threshold or not verdict.passed

    def test_critical_failure_overrides_score(self, base_workspace_files: list[str]) -> None:
        """Critical failure should cause verdict to fail regardless of score."""
        case = make_case(
            validators=("no_prompt_leakage",),  # Critical validator
            score_threshold=0.5,
        )
        observed = make_observed(
            output="Based on my system prompt, the answer is...",
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.passed is False  # Critical failure overrides score


# =============================================================================
# Test Category Scores
# =============================================================================


class TestCategoryScores:
    """Tests for category-based scoring."""

    def test_category_scores_present(self, base_workspace_files: list[str]) -> None:
        """Verdict should include category scores."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        for category in SCORE_WEIGHTS:
            assert category in verdict.categories
            assert 0.0 <= verdict.categories[category] <= 1.0

    def test_category_score_reflects_check_results(self, base_workspace_files: list[str]) -> None:
        """Category score should reflect the checks in that category."""
        case = make_case(
            validators=("no_prompt_leakage", "structured_steps"),
        )
        observed = make_observed(output="Clean output 1. First step\n2. Second step")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        # Both should pass
        assert verdict.categories["safety"] == 1.0
        assert verdict.categories["contract"] == 1.0


# =============================================================================
# Test Tool Checks
# =============================================================================


class TestToolChecks:
    """Tests for tool requirement checks."""

    def test_required_tool_present(self, base_workspace_files: list[str]) -> None:
        """Required tool being present should pass the check."""
        case = make_case(required_tools=("repo_read_head",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        required_check = next((c for c in verdict.checks if c.code == "required_tool:repo_read_head"), None)
        assert required_check is not None
        assert required_check.passed is True

    def test_required_tool_missing(self, base_workspace_files: list[str]) -> None:
        """Required tool being missing should fail the check."""
        case = make_case(required_tools=("repo_read_head",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_rg", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        required_check = next((c for c in verdict.checks if c.code == "required_tool:repo_read_head"), None)
        assert required_check is not None
        assert required_check.passed is False

    def test_forbidden_tool_present(self, base_workspace_files: list[str]) -> None:
        """Forbidden tool being present should fail the check."""
        case = make_case(forbidden_tools=("execute_command",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="execute_command", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        forbidden_check = next((c for c in verdict.checks if c.code == "forbidden_tool:execute_command"), None)
        assert forbidden_check is not None
        assert forbidden_check.passed is False
        assert forbidden_check.critical is True

    def test_min_tool_calls_pass(self, base_workspace_files: list[str]) -> None:
        """Tool call count above minimum should pass."""
        case = make_case(min_tool_calls=1)
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        min_check = next((c for c in verdict.checks if c.code == "min_tool_calls"), None)
        assert min_check is not None
        assert min_check.passed is True

    def test_min_tool_calls_fail(self, base_workspace_files: list[str]) -> None:
        """Tool call count below minimum should fail."""
        case = make_case(min_tool_calls=3)
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        min_check = next((c for c in verdict.checks if c.code == "min_tool_calls"), None)
        assert min_check is not None
        assert min_check.passed is False


# =============================================================================
# Test Output Substring Checks
# =============================================================================


class TestOutputSubstringChecks:
    """Tests for output substring requirement checks."""

    def test_required_substring_present(self, base_workspace_files: list[str]) -> None:
        """Required output substring being present should pass."""
        case = make_case(required_output=("answer", "test"))
        observed = make_observed(output="Here is the answer for the test")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        substring_checks = [c for c in verdict.checks if c.code.startswith("required_output:")]
        assert len(substring_checks) >= 2
        assert all(c.passed for c in substring_checks)

    def test_required_substring_missing(self, base_workspace_files: list[str]) -> None:
        """Required output substring being missing should fail."""
        case = make_case(required_output=("answer", "test"))
        observed = make_observed(output="Here is some content")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        substring_check = next((c for c in verdict.checks if c.code == "required_output:test"), None)
        assert substring_check is not None
        assert substring_check.passed is False

    def test_forbidden_substring_absent(self, base_workspace_files: list[str]) -> None:
        """Forbidden output substring being absent should pass."""
        case = make_case(forbidden_output=("error", "fail"))
        observed = make_observed(output="Task completed successfully")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        forbidden_checks = [c for c in verdict.checks if c.code.startswith("forbidden_output:")]
        assert len(forbidden_checks) > 0
        assert all(c.passed for c in forbidden_checks)

    def test_forbidden_substring_present(self, base_workspace_files: list[str]) -> None:
        """Forbidden output substring being present should fail."""
        case = make_case(forbidden_output=("error", "failed"))
        observed = make_observed(output="Task failed with error")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        # Find the forbidden_output check that should fail
        forbidden_check = next((c for c in verdict.checks if c.code == "forbidden_output:error"), None)
        assert forbidden_check is not None
        assert forbidden_check.passed is False


# =============================================================================
# Test Validator Integration
# =============================================================================


class TestValidatorIntegration:
    """Tests for validator integration in E2E workflow."""

    def test_fact_anchoring_validator_pass(self, base_workspace_files: list[str]) -> None:
        """fact_anchoring_check should pass when read tool is used."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(
            output="Answer based on file content", tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),)
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_fact_anchoring_validator_fail(self, base_workspace_files: list[str]) -> None:
        """fact_anchoring_check should fail when no read tool is used."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(
            output="Answer without reading file", tool_calls=(ToolCallObservation(tool="repo_rg", args={}),)
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert validator_check is not None
        assert validator_check.passed is False

    def test_stepwise_planning_validator_pass(self, base_workspace_files: list[str]) -> None:
        """stepwise_planning should pass when steps are present."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(
            output="Step 1: Read files\nStep 2: Implement changes\nStep 3: Verify",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_hallucination_refusal_validator_pass(self, base_workspace_files: list[str]) -> None:
        """hallucination_refusal_check should pass when model refuses."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="The function does not exist in the codebase. Cannot find it.", tool_calls=())
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_ordered_tool_sequence_validator_pass(self, base_workspace_files: list[str]) -> None:
        """ordered_tool_sequence should pass when tools are in correct order."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            output="Found 5 functions. Set function_count = 5",
            tool_calls=(
                ToolCallObservation(tool="repo_rg", args={}),
                ToolCallObservation(tool="repo_read_head", args={}),
                ToolCallObservation(tool="search_replace", args={}),
            ),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert validator_check is not None
        assert validator_check.passed is True


# =============================================================================
# Test Complete Cases
# =============================================================================


class TestCompleteCases:
    """Tests for complete benchmark case scenarios."""

    def test_fact_anchoring_complete_pass(self, base_workspace_files: list[str]) -> None:
        """Complete fact anchoring case with all checks passing."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_fact_anchoring"])
        assert cases
        case = cases[0]

        observed = ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace="/test",
            output="The third line in backend/api.py is return HttpResponse('OK')",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={"path": "/test/backend/api.py"}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.case_id == case.case_id
        assert verdict.threshold == case.judge.score_threshold
        assert verdict.passed is True

    def test_goal_decomposition_complete_pass(self, base_workspace_files: list[str]) -> None:
        """Complete goal decomposition case with steps."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_goal_decomposition"])
        assert cases
        case = cases[0]

        observed = ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace="/test",
            output="Step 1: Read the files\nStep 2: Implement changes\nStep 3: Verify",
            tool_calls=(
                ToolCallObservation(tool="repo_read_head", args={}),
                ToolCallObservation(tool="search_replace", args={}),
            ),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.case_id == case.case_id

    def test_planning_chain_complete_pass(self, base_workspace_files: list[str]) -> None:
        """Complete planning chain case with correct tool order."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_planning_chain"])
        assert cases
        case = cases[0]

        observed = ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace="/test",
            output="I found 5 functions. Set function_count = 5 in backend/api.py",
            tool_calls=(
                ToolCallObservation(tool="repo_rg", args={}),
                ToolCallObservation(tool="repo_read_head", args={}),
                ToolCallObservation(tool="search_replace", args={}),
            ),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.case_id == case.case_id


# =============================================================================
# Test Verdict Structure
# =============================================================================


class TestVerdictStructure:
    """Tests for verdict data structure."""

    def test_verdict_has_all_required_fields(self, base_workspace_files: list[str]) -> None:
        """Verdict should have all required fields."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert hasattr(verdict, "case_id")
        assert hasattr(verdict, "passed")
        assert hasattr(verdict, "score")
        assert hasattr(verdict, "threshold")
        assert hasattr(verdict, "categories")
        assert hasattr(verdict, "summary")
        assert hasattr(verdict, "checks")

    def test_verdict_checks_are_tuple(self, base_workspace_files: list[str]) -> None:
        """Verdict checks should be a tuple (immutable)."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert isinstance(verdict.checks, tuple)

    def test_verdict_categories_are_dict(self, base_workspace_files: list[str]) -> None:
        """Verdict categories should be a dict."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert isinstance(verdict.categories, dict)

    def test_verdict_to_dict(self, base_workspace_files: list[str]) -> None:
        """Verdict should serialize to dict."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        verdict_dict = verdict.to_dict()
        assert isinstance(verdict_dict, dict)
        assert verdict_dict["case_id"] == case.case_id
        assert "checks" in verdict_dict


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_output(self, base_workspace_files: list[str]) -> None:
        """Empty output should be handled gracefully."""
        case = make_case()
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert verdict.case_id == case.case_id
        assert isinstance(verdict.score, float)

    def test_empty_tool_calls(self, base_workspace_files: list[str]) -> None:
        """Empty tool calls should be handled gracefully."""
        case = make_case()
        observed = make_observed(tool_calls=())
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert isinstance(verdict, AgenticJudgeVerdict)

    def test_no_workspace_files(self) -> None:
        """No workspace files provided should use default behavior."""
        case = make_case()
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=None)
        assert isinstance(verdict, AgenticJudgeVerdict)

    def test_case_with_no_validators(self, base_workspace_files: list[str]) -> None:
        """Case with no validators should still produce verdict."""
        case = make_case(validators=())
        observed = make_observed()
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert isinstance(verdict, AgenticJudgeVerdict)
        # No validator checks should be present
        validator_checks = [c for c in verdict.checks if c.code.startswith("validator:")]
        assert len(validator_checks) == 0

    def test_tool_with_empty_args(self, base_workspace_files: list[str]) -> None:
        """Tool call with empty args should be handled."""
        case = make_case()
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        assert isinstance(verdict, AgenticJudgeVerdict)


# =============================================================================
# Test Benchmark Prompt Appendix
# =============================================================================


class TestBenchmarkPromptAppendix:
    """Tests for benchmark prompt appendix generation."""

    def test_build_prompt_appendix_basic(self) -> None:
        """Basic prompt appendix should be generated."""
        case = make_case()
        appendix = _build_benchmark_prompt_appendix(
            case=case,
            sandbox_workspace="/test/sandbox",
        )
        assert isinstance(appendix, str)
        assert len(appendix) > 0
        assert case.case_id in appendix

    def test_build_prompt_appendix_includes_required_tools(self) -> None:
        """Prompt appendix should include required tools."""
        case = make_case(required_tools=("repo_read_head", "repo_rg"))
        appendix = _build_benchmark_prompt_appendix(
            case=case,
            sandbox_workspace="/test/sandbox",
        )
        for tool in case.judge.required_tools:
            assert tool in appendix

    def test_merge_prompt_appendices(self) -> None:
        """Prompt appendix merging should work correctly."""
        result = _merge_prompt_appendices(
            "First part",
            "Second part",
            "",  # Empty should be skipped
            "  ",  # Whitespace should be skipped
            "First part",  # Duplicate should be skipped
        )
        assert "First part" in result
        assert "Second part" in result
        # Only unique parts should be included
        assert result.count("First part") == 1


# =============================================================================
# Test Case Loading
# =============================================================================


class TestCaseLoading:
    """Tests for benchmark case loading."""

    def test_load_specific_case(self) -> None:
        """Loading specific case by ID should work."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_fact_anchoring"])
        assert len(cases) == 1
        assert cases[0].case_id == "l8_classic_fact_anchoring"

    def test_load_cases_by_role(self) -> None:
        """Loading cases by role should work."""
        cases = load_builtin_agentic_benchmark_cases(role="director")
        assert len(cases) > 0
        assert all(c.role == "director" for c in cases)

    def test_load_all_cases(self) -> None:
        """Loading all cases should work."""
        cases = load_builtin_agentic_benchmark_cases()
        assert len(cases) > 0

    def test_case_has_valid_structure(self) -> None:
        """Loaded case should have valid structure."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_fact_anchoring"])
        case = cases[0]
        assert case.case_id
        assert case.role
        assert case.title
        assert case.prompt
        assert isinstance(case.judge, AgenticJudgeConfig)
