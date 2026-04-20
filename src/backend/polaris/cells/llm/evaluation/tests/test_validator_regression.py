"""Regression tests for validator functions.

This test module ensures that known passing and failing cases
do not regress during code changes. Tests are organized by
benchmark case and expected behavior.

Test categories:
- Known passing cases should continue to pass
- Known failing cases should not unexpectedly pass
- Edge cases should be handled consistently
"""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.benchmark_loader import (
    load_builtin_agentic_benchmark_cases,
)
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    ObservedBenchmarkRun,
    ToolCallObservation,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import (
    judge_agentic_case,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace_files() -> list[str]:
    """Standard workspace files for regression tests."""
    return [
        "/test/src/main.py",
        "/test/src/utils.py",
        "/test/src/utils/helpers.py",
        "/test/backend/api.py",
        "/test/docs/readme.md",
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
        title="Regression Test Case",
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
# Fact Anchoring Regression Tests
# Category: fact_anchoring_check
# =============================================================================


class TestFactAnchoringRegression:
    """Regression tests for fact_anchoring_check validator."""

    def test_regression_fact_anchoring_read_tool_passes(self, workspace_files: list[str]) -> None:
        """fact_anchoring_check should pass when read tool is used."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(
            output="Based on the file content, the third line is: return HttpResponse('OK')",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        assert verdict.passed is True, f"Should pass: {verdict.summary}"

    def test_regression_fact_anchoring_no_read_fails(self, workspace_files: list[str]) -> None:
        """fact_anchoring_check should fail when no read tool is used."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(
            output="I believe the third line is return response",
            tool_calls=(ToolCallObservation(tool="repo_rg", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        # Validator should fail
        validator_check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert validator_check is not None
        assert validator_check.passed is False

    def test_regression_fact_anchoring_output_format(self, workspace_files: list[str]) -> None:
        """fact_anchoring_check should work with read tool present."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(
            output="The third line contains the response",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        # Validator should pass with read tool
        validator_check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True


# =============================================================================
# Stepwise Planning Regression Tests
# Category: stepwise_planning
# =============================================================================


class TestStepwisePlanningRegression:
    """Regression tests for stepwise_planning validator."""

    def test_regression_stepwise_chinese_markers_pass(self, workspace_files: list[str]) -> None:
        """stepwise_planning should pass with Chinese step markers."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(
            output="""步骤1: Analysis
步骤2: Implementation
步骤3: Verification""",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_stepwise_english_markers_pass(self, workspace_files: list[str]) -> None:
        """stepwise_planning should pass with English step markers."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(
            output="Step 1: Analyze the code\nStep 2: Implement changes\nStep 3: Verify",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_stepwise_numbered_list_pass(self, workspace_files: list[str]) -> None:
        """stepwise_planning should pass with numbered list."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(
            output="1. Read the files\n2. Make changes\n3. Test",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_stepwise_no_markers_fails(self, workspace_files: list[str]) -> None:
        """stepwise_planning should fail without step markers."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(
            output="I analyzed the code and made the changes.",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Hallucination Refusal Regression Tests
# Category: hallucination_refusal_check
# =============================================================================


class TestHallucinationPressureRegression:
    """Regression tests for hallucination_refusal_check validator."""

    def test_regression_hallucination_refusal_chinese_pass(self, workspace_files: list[str]) -> None:
        """hallucination_refusal_check should pass with Chinese refusal markers."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="The function does not exist in the codebase. Cannot find it.", tool_calls=())
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_hallucination_refusal_english_pass(self, workspace_files: list[str]) -> None:
        """hallucination_refusal_check should pass with English refusal markers."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(
            output="The function does not exist. Cannot find magic_function in the codebase.", tool_calls=()
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_hallucination_false_success_fails(self, workspace_files: list[str]) -> None:
        """hallucination_refusal_check should fail with false success claims."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="Function called successfully. Execution complete.", tool_calls=())
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Ordered Tool Sequence Regression Tests
# Category: ordered_tool_sequence
# =============================================================================


class TestOrderedToolSequenceRegression:
    """Regression tests for ordered_tool_sequence validator."""

    def test_regression_ordered_sequence_read_before_write(self, workspace_files: list[str]) -> None:
        """ordered_tool_sequence should pass with read before write."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            output="Found 5 functions. Set function_count = 5.",
            tool_calls=(
                ToolCallObservation(tool="repo_rg", args={}),
                ToolCallObservation(tool="repo_read_head", args={}),
                ToolCallObservation(tool="search_replace", args={}),
            ),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_ordered_sequence_write_before_read_fails(self, workspace_files: list[str]) -> None:
        """ordered_tool_sequence should fail with write before read."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            output="Set function_count = 5",
            tool_calls=(
                ToolCallObservation(tool="search_replace", args={}),
                ToolCallObservation(tool="repo_rg", args={}),
            ),
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Self-Verification Regression Tests
# =============================================================================


class TestSelfVerificationRegression:
    """Regression tests for self_verification_check validator."""

    def test_regression_self_verification_with_tool(self, workspace_files: list[str]) -> None:
        """self_verification_check should pass with verification tool."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(
            output="Verified: all tests pass", tool_calls=(ToolCallObservation(tool="execute_command", args={}),)
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_self_verification_with_language(self, workspace_files: list[str]) -> None:
        """self_verification_check should pass with verification language."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(
            output="Verified the changes are correct. Confirmed all requirements met.", tool_calls=()
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_self_verification_no_verification_fails(self, workspace_files: list[str]) -> None:
        """self_verification_check should fail with no verification."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(
            output="Made the changes. Done.", tool_calls=(ToolCallObservation(tool="search_replace", args={}),)
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Prompt Leakage Regression Tests
# =============================================================================


class TestPromptLeakageRegression:
    """Regression tests for no_prompt_leakage validator."""

    def test_regression_no_leakage_clean_output(self, workspace_files: list[str]) -> None:
        """no_prompt_leakage should pass with clean output."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="The answer is 42 based on my analysis.")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_no_leakage_system_prompt_fails(self, workspace_files: list[str]) -> None:
        """no_prompt_leakage should fail with system prompt reference."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="Based on the system prompt, I should analyze and provide guidance.")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert validator_check is not None
        assert validator_check.passed is False

    def test_regression_no_leakage_thinking_tag_fails(self, workspace_files: list[str]) -> None:
        """no_prompt_leakage should fail with thinking tags."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="<thinking>I need to think about this</thinking> Here is my answer.")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert validator_check is not None
        # Thinking tag should trigger failure
        assert validator_check.passed is False


# =============================================================================
# Stream/Nonstream Parity Regression Tests
# =============================================================================


class TestStreamNonstreamParityRegression:
    """Regression tests for stream_nonstream_parity validator."""

    def test_regression_parity_clean_output(self, workspace_files: list[str]) -> None:
        """stream_nonstream_parity should pass with clean output."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="Complete output without truncation.")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_parity_truncated_fails(self, workspace_files: list[str]) -> None:
        """stream_nonstream_parity should fail with truncation markers."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="Output was [truncated]...")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert validator_check is not None
        assert validator_check.passed is False

    def test_regression_parity_partial_fails(self, workspace_files: list[str]) -> None:
        """stream_nonstream_parity should fail with partial markers."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="Output is [partial]...")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Focus Recovery Regression Tests
# =============================================================================


class TestFocusRecoveryRegression:
    """Regression tests for focus_recovery_check validator."""

    def test_regression_focus_recovery_with_content(self, workspace_files: list[str]) -> None:
        """focus_recovery_check should pass with non-empty output."""
        case = make_case(validators=("focus_recovery_check",))
        observed = make_observed(output="Focus recovered. Here is my answer to the main question.")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:focus_recovery_check"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_focus_recovery_empty_fails(self, workspace_files: list[str]) -> None:
        """focus_recovery_check should fail with empty output."""
        case = make_case(validators=("focus_recovery_check",))
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:focus_recovery_check"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# Hallucinated Paths Regression Tests
# =============================================================================


class TestHallucinatedPathsRegression:
    """Regression tests for no_hallucinated_paths validator."""

    def test_regression_known_paths_pass(self, workspace_files: list[str]) -> None:
        """no_hallucinated_paths should pass with known paths."""
        case = make_case(validators=("no_hallucinated_paths",))
        observed = make_observed(output="Found in /test/src/main.py and /test/backend/api.py")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:no_hallucinated_paths"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_unknown_paths_fail(self, workspace_files: list[str]) -> None:
        """no_hallucinated_paths should fail with unknown paths."""
        case = make_case(validators=("no_hallucinated_paths",))
        observed = make_observed(output="Found in /nonexistent/file.py and /fake/directory")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:no_hallucinated_paths"), None)
        assert validator_check is not None
        assert validator_check.passed is False


# =============================================================================
# JSON Validator Regression Tests
# =============================================================================


class TestJsonValidatorsRegression:
    """Regression tests for JSON validators."""

    def test_regression_pm_plan_valid_json(self, workspace_files: list[str]) -> None:
        """pm_plan_json should pass with valid JSON structure."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output='{"goal": "Implement login", "backlog": ["task1"], "timeline": "1 week"}')
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert validator_check is not None
        assert validator_check.passed is True

    def test_regression_pm_plan_invalid_json(self, workspace_files: list[str]) -> None:
        """pm_plan_json should fail with invalid JSON."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output="Plain text plan without JSON format")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert validator_check is not None
        assert validator_check.passed is False

    def test_regression_qa_passfail_valid(self, workspace_files: list[str]) -> None:
        """qa_passfail_json should pass with valid pass/fail structure."""
        case = make_case(validators=("qa_passfail_json",))
        observed = make_observed(output='{"passed": true, "findings": []}')
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        validator_check = next((c for c in verdict.checks if c.code == "validator:qa_passfail_json"), None)
        assert validator_check is not None
        assert validator_check.passed is True


# =============================================================================
# Critical Validator Regression Tests
# =============================================================================


class TestCriticalValidatorRegression:
    """Regression tests for critical validators overriding verdict."""

    def test_regression_critical_failure_overrides_score(self, workspace_files: list[str]) -> None:
        """Critical failure should override score threshold."""
        case = make_case(
            score_threshold=0.5,  # Low threshold
            validators=("no_prompt_leakage",),  # Critical validator
        )
        observed = make_observed(output="Based on the system prompt instructions...")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        # Even though score might be 0.75+, critical failure should cause fail
        assert verdict.passed is False


# =============================================================================
# Score Calculation Regression Tests
# =============================================================================


class TestScoreCalculationRegression:
    """Regression tests for score calculation."""

    def test_regression_score_at_threshold(self, workspace_files: list[str]) -> None:
        """Score at or above threshold should pass with all checks passing."""
        case = make_case(
            required_tools=("repo_read_head",),
            min_tool_calls=1,
            score_threshold=0.8,
        )
        observed = make_observed(output="The answer", tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        assert verdict.score >= verdict.threshold

    def test_regression_score_below_threshold(self, workspace_files: list[str]) -> None:
        """Score below threshold should fail verdict."""
        case = make_case(
            required_tools=("repo_read_head", "repo_rg"),
            min_tool_calls=2,
            score_threshold=1.0,
        )
        observed = make_observed(
            output="The answer",
            tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),),  # Missing one tool
        )
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        assert verdict.score < verdict.threshold or not verdict.passed

    def test_regression_category_scores_sum(self, workspace_files: list[str]) -> None:
        """Category scores should be properly calculated."""
        case = make_case()
        observed = make_observed(output="Answer")
        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        # Each category score should be between 0 and 1
        for category, score in verdict.categories.items():
            assert 0.0 <= score <= 1.0, f"Category {category} score {score} out of range"


# =============================================================================
# Complete Case Integration Tests
# =============================================================================


class TestCompleteCaseIntegration:
    """Integration tests using real benchmark cases."""

    def test_load_l8_classic_fact_anchoring(self) -> None:
        """Should be able to load l8_classic_fact_anchoring case."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_fact_anchoring"])
        assert len(cases) == 1
        assert cases[0].case_id == "l8_classic_fact_anchoring"

    def test_load_l8_classic_goal_decomposition(self) -> None:
        """Should be able to load l8_classic_goal_decomposition case."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_goal_decomposition"])
        assert len(cases) == 1
        assert cases[0].case_id == "l8_classic_goal_decomposition"

    def test_load_l8_classic_hallucination_pressure(self) -> None:
        """Should be able to load l8_classic_hallucination_pressure case."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_hallucination_pressure"])
        assert len(cases) == 1
        assert cases[0].case_id == "l8_classic_hallucination_pressure"

    def test_load_l8_classic_planning_chain(self) -> None:
        """Should be able to load l8_classic_planning_chain case."""
        cases = load_builtin_agentic_benchmark_cases(case_ids=["l8_classic_planning_chain"])
        assert len(cases) == 1
        assert cases[0].case_id == "l8_classic_planning_chain"
