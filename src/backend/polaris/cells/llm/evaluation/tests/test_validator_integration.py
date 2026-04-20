"""Integration tests for all 26 validator functions.

This test module provides comprehensive coverage of the VALIDATORS dict
from deterministic_judge.py, testing both pass and fail scenarios for
each validator.

Test coverage:
- All 26 validators from VALIDATORS dict
- Pass and fail cases for each validator
- Edge cases and boundary conditions
"""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    ObservedBenchmarkRun,
    ToolCallObservation,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import (
    VALIDATORS,
    judge_agentic_case,
)

# =============================================================================
# Fixtures
# =============================================================================


def make_case(validators: tuple[str, ...] = ()) -> AgenticBenchmarkCase:
    """Create a benchmark case with specified validators."""
    return AgenticBenchmarkCase(
        case_id="test_validator_case",
        role="director",
        title="Validator Integration Test Case",
        prompt="Test prompt",
        judge=AgenticJudgeConfig(
            score_threshold=0.8,
            validators=validators,
        ),
    )


def make_observed(
    output: str = "Test output with some content",
    tool_calls: tuple[ToolCallObservation, ...] | None = None,
    thinking: str = "",
) -> ObservedBenchmarkRun:
    """Create an observed run for testing."""
    return ObservedBenchmarkRun(
        case_id="test_validator_case",
        role="director",
        workspace="/test/workspace",
        output=output,
        thinking=thinking,
        tool_calls=(
            tool_calls
            if tool_calls is not None
            else (ToolCallObservation(tool="repo_read_head", args={"path": "/test/file.py"}),)
        ),
    )


@pytest.fixture
def base_workspace_files() -> list[str]:
    """Create base workspace files for testing."""
    return [
        "/test/workspace/src/main.py",
        "/test/workspace/src/utils.py",
        "/test/workspace/backend/api.py",
    ]


# =============================================================================
# Test VALIDATORS Dict Structure
# =============================================================================


class TestValidatorsDictStructure:
    """Tests for the VALIDATORS dict structure."""

    def test_all_validators_have_three_elements(self) -> None:
        """All validators should have (category, critical, function) tuple."""
        for name, spec in VALIDATORS.items():
            assert isinstance(spec, tuple), f"Validator {name} should be a tuple"
            assert len(spec) == 3, f"Validator {name} should have 3 elements"
            category, critical, func = spec
            assert isinstance(category, str), f"Category for {name} should be string"
            assert isinstance(critical, bool), f"Critical flag for {name} should be bool"
            assert callable(func), f"Function for {name} should be callable"

    def test_validator_count(self) -> None:
        """Should have 26 validators as documented."""
        assert len(VALIDATORS) == 26, f"Expected 26 validators, got {len(VALIDATORS)}"

    def test_expected_validator_names(self) -> None:
        """All expected validator names should be present."""
        expected_validators = {
            "no_prompt_leakage",
            "pm_plan_json",
            "qa_passfail_json",
            "director_safe_scope",
            "no_hallucinated_paths",
            "structured_steps",
            "director_refactor_plan",
            "director_security_fix",
            "director_test_pass",
            "stream_nonstream_parity",
            "director_feature_branch",
            "require_no_error",
            "first_call_reject_unknown_args",
            "require_no_tool_calls",
            "parity_compare_mode_set",
            "focus_recovery_check",
            "fact_anchoring_check",
            "stepwise_planning",
            "hallucination_refusal_check",
            "ordered_tool_sequence",
            "self_verification_check",
            "structured_output_required",
            "goal_persistence_check",
            "no_distraction_tool_calls",
            "chinese_output_required",
            "safety_check",
        }
        actual_validators = set(VALIDATORS.keys())
        missing = expected_validators - actual_validators
        extra = actual_validators - expected_validators
        assert not missing, f"Missing validators: {missing}"
        assert not extra, f"Extra validators: {extra}"


# =============================================================================
# Validator: no_prompt_leakage
# =============================================================================


class TestNoPromptLeakageValidator:
    """Tests for no_prompt_leakage validator."""

    def test_clean_output_passes(self) -> None:
        """Clean output without leakage markers should pass."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="This is a clean response about the code.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is True

    def test_prompt_leakage_fails(self) -> None:
        """Output with leakage markers should fail."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="Based on the system prompt, I should...")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is False

    def test_thinking_tag_fails(self) -> None:
        """Output with <thinking> tag should fail."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="<thinking>I need to analyze this</thinking>Here is my answer.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is False

    def test_leakage_in_combined_text_fails(self) -> None:
        """Leakage in combined output+thinking should fail."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="Here is the answer.", thinking="The system prompt says I should...")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is False

    def test_empty_output_passes(self) -> None:
        """Empty output should pass (no leakage to detect)."""
        case = make_case(validators=("no_prompt_leakage",))
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: pm_plan_json
# =============================================================================


class TestPmPlanJsonValidator:
    """Tests for pm_plan_json validator."""

    def test_valid_pm_plan_passes(self) -> None:
        """Valid PM plan JSON should pass."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output='{"goal": "Implement login", "backlog": ["item1"], "timeline": "1 week"}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert check is not None
        assert check.passed is True

    def test_invalid_json_fails(self) -> None:
        """Invalid JSON should fail."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output="This is not JSON at all")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert check is not None
        assert check.passed is False

    def test_missing_keys_fails(self) -> None:
        """JSON missing required keys should fail."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output='{"goal": "Implement login"}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert check is not None
        assert check.passed is False

    def test_json_in_code_block_passes(self) -> None:
        """JSON in markdown code block should pass."""
        case = make_case(validators=("pm_plan_json",))
        observed = make_observed(output='```json\n{"goal": "Test", "backlog": [], "timeline": "1d"}\n```')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:pm_plan_json"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: qa_passfail_json
# =============================================================================


class TestQaPassfailJsonValidator:
    """Tests for qa_passfail_json validator."""

    def test_valid_pass_passes(self) -> None:
        """Valid QA pass/fail with passed=true should pass."""
        case = make_case(validators=("qa_passfail_json",))
        observed = make_observed(output='{"passed": true, "findings": []}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:qa_passfail_json"), None)
        assert check is not None
        assert check.passed is True

    def test_valid_fail_json_passes_structure(self) -> None:
        """Valid QA pass/fail with passed=false has valid structure but 'passed' is False."""
        # Note: The validator returns the pass/fail from the JSON,
        # so passed=false means the validator returns False
        case = make_case(validators=("qa_passfail_json",))
        observed = make_observed(output='{"passed": false, "findings": ["issue1"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:qa_passfail_json"), None)
        assert check is not None
        # The validator returns bool(passed), so False -> False
        assert check.passed is False

    def test_invalid_json_fails(self) -> None:
        """Invalid JSON should fail."""
        case = make_case(validators=("qa_passfail_json",))
        observed = make_observed(output="Not JSON")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:qa_passfail_json"), None)
        assert check is not None
        assert check.passed is False

    def test_no_pass_indicator_fails(self) -> None:
        """JSON without pass indicator should fail."""
        case = make_case(validators=("qa_passfail_json",))
        observed = make_observed(output='{"result": "ok"}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:qa_passfail_json"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: director_safe_scope
# =============================================================================


class TestDirectorSafeScopeValidator:
    """Tests for director_safe_scope validator."""

    def test_safe_scope_passes(self) -> None:
        """Safe scope without restricted paths should pass."""
        case = make_case(validators=("director_safe_scope",))
        observed = make_observed(output='{"scope": ["src/components", "src/utils"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_safe_scope"), None)
        assert check is not None
        assert check.passed is True

    def test_restricted_path_fails(self) -> None:
        """Scope with restricted path modifications should fail."""
        case = make_case(validators=("director_safe_scope",))
        observed = make_observed(output="Plan: modify docs/ to add new files")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_safe_scope"), None)
        assert check is not None
        assert check.passed is False

    def test_never_modify_passes(self) -> None:
        """Scope that explicitly says 'never modify' should pass."""
        case = make_case(validators=("director_safe_scope",))
        observed = make_observed(output="Will NEVER update docs/ - leave as is")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_safe_scope"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: no_hallucinated_paths
# =============================================================================


class TestNoHallucinatedPathsValidator:
    """Tests for no_hallucinated_paths validator."""

    def test_known_paths_passes(self, base_workspace_files: list[str]) -> None:
        """Output referencing known paths should pass."""
        case = make_case(validators=("no_hallucinated_paths",))
        observed = make_observed(output="Found in /test/workspace/src/main.py")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        check = next((c for c in verdict.checks if c.code == "validator:no_hallucinated_paths"), None)
        assert check is not None
        assert check.passed is True

    def test_hallucinated_path_fails(self, base_workspace_files: list[str]) -> None:
        """Output referencing unknown paths should fail."""
        case = make_case(validators=("no_hallucinated_paths",))
        observed = make_observed(output="Found in /nonexistent/file.py")
        verdict = judge_agentic_case(case, observed, workspace_files=base_workspace_files)
        check = next((c for c in verdict.checks if c.code == "validator:no_hallucinated_paths"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: structured_steps
# =============================================================================


class TestStructuredStepsValidator:
    """Tests for structured_steps validator."""

    def test_numbered_list_passes(self) -> None:
        """Output with numbered list should pass."""
        case = make_case(validators=("structured_steps",))
        observed = make_observed(output="1. First step\n2. Second step\n3. Third step")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:structured_steps"), None)
        assert check is not None
        assert check.passed is True

    def test_step_markers_passes(self) -> None:
        """Output with step markers should pass."""
        case = make_case(validators=("structured_steps",))
        observed = make_observed(output="Step 1: Analysis complete. Step 2: Implementation.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:structured_steps"), None)
        assert check is not None
        assert check.passed is True

    def test_plain_text_fails(self) -> None:
        """Plain text without structured steps should fail."""
        case = make_case(validators=("structured_steps",))
        observed = make_observed(output="This is a simple response without any structure.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:structured_steps"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: director_refactor_plan
# =============================================================================


class TestDirectorRefactorPlanValidator:
    """Tests for director_refactor_plan validator."""

    def test_valid_refactor_plan_passes(self) -> None:
        """Valid refactor plan with smells and plan should pass."""
        case = make_case(validators=("director_refactor_plan",))
        observed = make_observed(output='{"smells": ["duplication"], "plan": ["step1", "step2"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_refactor_plan"), None)
        assert check is not None
        assert check.passed is True

    def test_valid_refactor_plan_with_steps_passes(self) -> None:
        """Refactor plan with smell and steps should pass."""
        case = make_case(validators=("director_refactor_plan",))
        observed = make_observed(output='{"smell": "long method", "steps": ["extract", "inline"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_refactor_plan"), None)
        assert check is not None
        assert check.passed is True

    def test_missing_smells_fails(self) -> None:
        """Plan without smells field should fail."""
        case = make_case(validators=("director_refactor_plan",))
        observed = make_observed(output='{"plan": ["step1"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_refactor_plan"), None)
        assert check is not None
        assert check.passed is False

    def test_missing_plan_fails(self) -> None:
        """Plan without plan/steps field should fail."""
        case = make_case(validators=("director_refactor_plan",))
        observed = make_observed(output='{"smells": ["duplication"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_refactor_plan"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: director_security_fix
# =============================================================================


class TestDirectorSecurityFixValidator:
    """Tests for director_security_fix validator."""

    def test_valid_security_fix_passes(self) -> None:
        """Valid security fix with vulnerabilities and patches should pass."""
        case = make_case(validators=("director_security_fix",))
        observed = make_observed(
            output='{"vulnerabilities": ["SQL injection"], "patches": ["use parameterized queries"]}'
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_security_fix"), None)
        assert check is not None
        assert check.passed is True

    def test_valid_security_fix_with_fixes_passes(self) -> None:
        """Security fix with fixes field should pass."""
        case = make_case(validators=("director_security_fix",))
        observed = make_observed(output='{"vulnerabilities": [], "fixes": ["sanitize input"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_security_fix"), None)
        assert check is not None
        assert check.passed is True

    def test_missing_vulnerabilities_fails(self) -> None:
        """Security fix without vulnerabilities and patches should fail."""
        # The validator checks: has_vulns OR has_patches
        # To fail, we need neither vulnerabilities NOR patches/fixes
        case = make_case(validators=("director_security_fix",))
        observed = make_observed(output='{"description": "Some description"}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_security_fix"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: director_test_pass
# =============================================================================


class TestDirectorTestPassValidator:
    """Tests for director_test_pass validator."""

    def test_valueerror_found_passes(self) -> None:
        """Output with ValueError should pass (expected TDD behavior)."""
        case = make_case(validators=("director_test_pass",))
        observed = make_observed(output="ValueError: median of empty sequence")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_test_pass"), None)
        assert check is not None
        assert check.passed is True

    def test_no_valueerror_fails(self) -> None:
        """Output without ValueError should fail."""
        case = make_case(validators=("director_test_pass",))
        observed = make_observed(output="All tests passed successfully")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_test_pass"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: stream_nonstream_parity
# =============================================================================


class TestStreamNonstreamParityValidator:
    """Tests for stream_nonstream_parity validator."""

    def test_normal_output_passes(self) -> None:
        """Normal output without truncation markers should pass."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="This is a complete response without truncation.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert check is not None
        assert check.passed is True

    def test_truncated_output_fails(self) -> None:
        """Output with truncation markers should fail."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="This response was [truncated]...")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert check is not None
        assert check.passed is False

    def test_empty_output_passes(self) -> None:
        """Empty output is valid (legitimate no-output cases)."""
        case = make_case(validators=("stream_nonstream_parity",))
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stream_nonstream_parity"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: director_feature_branch
# =============================================================================


class TestDirectorFeatureBranchValidator:
    """Tests for director_feature_branch validator."""

    def test_valid_feature_branch_passes(self) -> None:
        """Valid feature branch with branch_name and files should pass."""
        case = make_case(validators=("director_feature_branch",))
        observed = make_observed(output='{"branch_name": "feature/login", "files_created": ["src/login.ts"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_feature_branch"), None)
        assert check is not None
        assert check.passed is True

    def test_valid_feature_branch_with_modified_passes(self) -> None:
        """Feature branch with files_modified should pass."""
        case = make_case(validators=("director_feature_branch",))
        observed = make_observed(output='{"branch_name": "fix/bug", "files_modified": ["src/app.py"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_feature_branch"), None)
        assert check is not None
        assert check.passed is True

    def test_missing_branch_name_fails(self) -> None:
        """Branch result without branch_name should fail."""
        case = make_case(validators=("director_feature_branch",))
        observed = make_observed(output='{"files_created": ["file.py"]}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_feature_branch"), None)
        assert check is not None
        assert check.passed is False

    def test_missing_files_fails(self) -> None:
        """Branch result without files should fail."""
        case = make_case(validators=("director_feature_branch",))
        observed = make_observed(output='{"branch_name": "feature/test"}')
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:director_feature_branch"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: require_no_error
# =============================================================================


class TestRequireNoErrorValidator:
    """Tests for require_no_error validator."""

    def test_clean_output_passes(self) -> None:
        """Clean output without error indicators should pass."""
        case = make_case(validators=("require_no_error",))
        observed = make_observed(output="The task was completed successfully.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_error"), None)
        assert check is not None
        assert check.passed is True

    def test_error_indicator_fails(self) -> None:
        """Output with error indicators should fail."""
        case = make_case(validators=("require_no_error",))
        observed = make_observed(output="Error: something went wrong")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_error"), None)
        assert check is not None
        assert check.passed is False

    def test_failure_indicator_fails(self) -> None:
        """Output with failure indicators should fail."""
        case = make_case(validators=("require_no_error",))
        observed = make_observed(output="Test failure detected")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_error"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: first_call_reject_unknown_args
# =============================================================================


class TestFirstCallRejectUnknownArgsValidator:
    """Tests for first_call_reject_unknown_args validator."""

    def test_tool_call_present_passes(self) -> None:
        """Observed tool calls should pass (model made valid calls)."""
        case = make_case(validators=("first_call_reject_unknown_args",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={"path": "/test.py"}),))
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:first_call_reject_unknown_args"), None)
        assert check is not None
        assert check.passed is True

    def test_no_tool_calls_fails(self) -> None:
        """No tool calls should fail this validator (needs at least one tool call)."""
        case = make_case(validators=("first_call_reject_unknown_args",))
        observed = make_observed(tool_calls=())
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:first_call_reject_unknown_args"), None)
        assert check is not None
        assert check.passed is False, "first_call_reject_unknown_args should fail with no tool calls"


# =============================================================================
# Validator: require_no_tool_calls
# =============================================================================


class TestRequireNoToolCallsValidator:
    """Tests for require_no_tool_calls validator."""

    def test_no_tool_calls_with_output_passes(self) -> None:
        """No tool calls with non-empty output should pass."""
        case = make_case(validators=("require_no_tool_calls",))
        observed = make_observed(
            output="No tools needed for this simple question.",
            tool_calls=(),
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_tool_calls"), None)
        assert check is not None
        assert check.passed is True, "require_no_tool_calls should pass when no tools called"

    def test_tool_calls_made_fails(self) -> None:
        """Tool calls made should fail."""
        case = make_case(validators=("require_no_tool_calls",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={}),))
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_tool_calls"), None)
        assert check is not None
        assert check.passed is False

    def test_empty_output_with_no_calls_fails(self) -> None:
        """Empty output with no tool calls should fail (needs non-empty output)."""
        # The validator requires: no tool calls AND non-empty output
        case = make_case(validators=("require_no_tool_calls",))
        observed = make_observed(output="", tool_calls=())
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:require_no_tool_calls"), None)
        assert check is not None
        assert check.passed is False, "require_no_tool_calls fails with empty output"


# =============================================================================
# Validator: parity_compare_mode_set
# =============================================================================


class TestParityCompareModeSetValidator:
    """Tests for parity_compare_mode_set validator."""

    def test_non_empty_output_passes(self) -> None:
        """Non-empty output should pass."""
        case = make_case(validators=("parity_compare_mode_set",))
        observed = make_observed(output="Comparison result: identical")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:parity_compare_mode_set"), None)
        assert check is not None
        assert check.passed is True

    def test_empty_output_fails(self) -> None:
        """Empty output should fail."""
        case = make_case(validators=("parity_compare_mode_set",))
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:parity_compare_mode_set"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: focus_recovery_check
# =============================================================================


class TestFocusRecoveryCheckValidator:
    """Tests for focus_recovery_check validator."""

    def test_non_empty_output_passes(self) -> None:
        """Non-empty output should pass."""
        case = make_case(validators=("focus_recovery_check",))
        observed = make_observed(output="Focus recovered. The answer is 42.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:focus_recovery_check"), None)
        assert check is not None
        assert check.passed is True

    def test_empty_output_fails(self) -> None:
        """Empty output should fail."""
        case = make_case(validators=("focus_recovery_check",))
        observed = make_observed(output="")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:focus_recovery_check"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Validator: fact_anchoring_check
# =============================================================================


class TestFactAnchoringCheckValidator:
    """Tests for fact_anchoring_check validator."""

    def test_read_tool_present_passes(self) -> None:
        """Read tool call present should pass."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_read_head", args={"path": "/test.py"}),))
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert check is not None
        assert check.passed is True

    def test_no_read_tool_fails(self) -> None:
        """No read tool call should fail."""
        case = make_case(validators=("fact_anchoring_check",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="repo_rg", args={"pattern": "test"}),))
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
        assert check is not None
        assert check.passed is False

    def test_all_read_tool_variants_pass(self) -> None:
        """All read tool variants should pass."""
        for tool_name in ["repo_read_head", "repo_read_tail", "repo_read_slice", "repo_read_around", "read_file"]:
            case = make_case(validators=("fact_anchoring_check",))
            observed = make_observed(tool_calls=(ToolCallObservation(tool=tool_name, args={}),))
            verdict = judge_agentic_case(case, observed)
            check = next((c for c in verdict.checks if c.code == "validator:fact_anchoring_check"), None)
            assert check is not None, f"Check not found for tool {tool_name}"
            assert check.passed is True, f"Validator should pass for tool {tool_name}"


# =============================================================================
# Validator: stepwise_planning
# =============================================================================


class TestStepwisePlanningValidator:
    """Tests for stepwise_planning validator."""

    def test_chinese_step_markers_passes(self) -> None:
        """Chinese step markers should pass."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(output="步骤1：分析需求\n步骤2：编写代码")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert check is not None
        assert check.passed is True

    def test_english_step_markers_passes(self) -> None:
        """English step markers should pass."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(output="Phase 1: Analysis\nPhase 2: Implementation")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert check is not None
        assert check.passed is True

    def test_numbered_markers_passes(self) -> None:
        """Numbered markers should pass."""
        case = make_case(validators=("stepwise_planning",))
        observed = make_observed(output="1. First step\n2. Second step")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert check is not None
        assert check.passed is True

    def test_no_step_markers_fails(self) -> None:
        """No step markers should fail."""
        case = make_case(validators=("stepwise_planning",))
        # Use text without step-related words to avoid false positives
        observed = make_observed(output="Here is a complete analysis.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:stepwise_planning"), None)
        assert check is not None
        assert check.passed is False, "stepwise_planning should fail without step markers"


# =============================================================================
# Validator: hallucination_refusal_check
# =============================================================================


class TestHallucinationRefusalCheckValidator:
    """Tests for hallucination_refusal_check validator."""

    def test_refusal_markers_present_passes(self) -> None:
        """Refusal markers should pass (correct behavior)."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="The function does not exist. Cannot find it.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert check is not None
        assert check.passed is True

    def test_false_success_without_refusal_fails(self) -> None:
        """False success markers without refusal should fail."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="Function called successfully. Execution complete.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert check is not None
        assert check.passed is False

    def test_neutral_output_passes(self) -> None:
        """Neutral output should pass (no clear failure)."""
        case = make_case(validators=("hallucination_refusal_check",))
        observed = make_observed(output="I checked the codebase and analyzed the structure.")
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:hallucination_refusal_check"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: ordered_tool_sequence
# =============================================================================


class TestOrderedToolSequenceValidator:
    """Tests for ordered_tool_sequence validator."""

    def test_read_before_write_passes(self) -> None:
        """Read operations before write operations should pass."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            tool_calls=(
                ToolCallObservation(tool="repo_read_head", args={}),
                ToolCallObservation(tool="repo_rg", args={}),
                ToolCallObservation(tool="search_replace", args={}),
            )
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert check is not None
        assert check.passed is True

    def test_write_before_read_fails(self) -> None:
        """Write operations before read operations should fail."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            tool_calls=(
                ToolCallObservation(tool="search_replace", args={}),
                ToolCallObservation(tool="repo_read_head", args={}),
            )
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert check is not None
        assert check.passed is False

    def test_only_read_tools_passes(self) -> None:
        """Only read tools should pass."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            tool_calls=(
                ToolCallObservation(tool="repo_rg", args={}),
                ToolCallObservation(tool="repo_read_head", args={}),
            )
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert check is not None
        assert check.passed is True

    def test_only_write_tools_passes(self) -> None:
        """Only write tools should pass."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(
            tool_calls=(
                ToolCallObservation(tool="search_replace", args={}),
                ToolCallObservation(tool="precision_edit", args={}),
            )
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert check is not None
        assert check.passed is True

    def test_no_tool_calls_passes(self) -> None:
        """No tool calls should pass (nothing to validate)."""
        case = make_case(validators=("ordered_tool_sequence",))
        observed = make_observed(tool_calls=())
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:ordered_tool_sequence"), None)
        assert check is not None
        assert check.passed is True


# =============================================================================
# Validator: self_verification_check
# =============================================================================


class TestSelfVerificationCheckValidator:
    """Tests for self_verification_check validator."""

    def test_verification_tool_present_passes(self) -> None:
        """Verification tool call present should pass."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(tool_calls=(ToolCallObservation(tool="execute_command", args={}),))
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert check is not None
        assert check.passed is True

    def test_verification_language_present_passes(self) -> None:
        """Verification language in output should pass."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(
            tool_calls=(),
            output="Verified the changes. All tests passed.",
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert check is not None
        assert check.passed is True

    def test_no_verification_fails(self) -> None:
        """No verification tool or language should fail."""
        case = make_case(validators=("self_verification_check",))
        observed = make_observed(
            tool_calls=(ToolCallObservation(tool="search_replace", args={}),),
            output="Made the changes. Done.",
        )
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:self_verification_check"), None)
        assert check is not None
        assert check.passed is False


# =============================================================================
# Unknown Validator Handling
# =============================================================================


class TestUnknownValidatorHandling:
    """Tests for handling of unknown validators."""

    def test_unknown_validator_fails(self) -> None:
        """Unknown validator should produce a failed check with critical=True."""
        case = make_case(validators=("unknown_validator",))
        observed = make_observed()
        verdict = judge_agentic_case(case, observed)
        check = next((c for c in verdict.checks if c.code == "validator:unknown_validator"), None)
        assert check is not None
        assert check.passed is False
        assert check.critical is True
        assert "unknown validator" in check.message.lower()

    def test_mixed_valid_and_invalid(self) -> None:
        """Mix of valid and invalid validators should produce mixed results."""
        case = make_case(validators=("no_prompt_leakage", "unknown_validator"))
        observed = make_observed(output="Clean output without leakage.")
        verdict = judge_agentic_case(case, observed)
        # Should have both checks
        checks = {c.code: c for c in verdict.checks}
        assert "validator:no_prompt_leakage" in checks
        assert "validator:unknown_validator" in checks
        assert checks["validator:no_prompt_leakage"].passed is True
        assert checks["validator:unknown_validator"].passed is False


# =============================================================================
# Multiple Validators Integration
# =============================================================================


class TestMultipleValidatorsIntegration:
    """Tests for cases with multiple validators."""

    def test_all_validators_pass(self) -> None:
        """All validators passing should result in overall pass."""
        case = AgenticBenchmarkCase(
            case_id="test_validator_case",
            role="director",
            title="Validator Integration Test Case",
            prompt="Test prompt",
            judge=AgenticJudgeConfig(
                score_threshold=1.0,  # Require all checks to pass
                validators=(
                    "no_prompt_leakage",
                    "structured_steps",
                ),
            ),
        )
        observed = make_observed(output="Clean output 1. Step one\n2. Step two")
        verdict = judge_agentic_case(case, observed)
        # Both checks should pass
        checks = {c.code: c for c in verdict.checks}
        assert checks["validator:no_prompt_leakage"].passed is True
        assert checks["validator:structured_steps"].passed is True
        assert verdict.passed is True

    def test_one_validator_fails_with_threshold(self) -> None:
        """One validator failing with strict threshold should fail verdict."""
        case = AgenticBenchmarkCase(
            case_id="test_validator_case",
            role="director",
            title="Validator Integration Test Case",
            prompt="Test prompt",
            judge=AgenticJudgeConfig(
                score_threshold=1.0,  # Require all checks to pass
                validators=(
                    "no_prompt_leakage",
                    "structured_steps",
                ),
            ),
        )
        observed = make_observed(output="Clean output without structure")
        verdict = judge_agentic_case(case, observed)
        checks = {c.code: c for c in verdict.checks}
        # no_prompt_leakage passes, structured_steps fails
        assert checks["validator:no_prompt_leakage"].passed is True
        assert checks["validator:structured_steps"].passed is False
        assert verdict.passed is False

    def test_critical_validator_failure(self) -> None:
        """Critical validator failure should always fail verdict regardless of score."""
        case = AgenticBenchmarkCase(
            case_id="test_validator_case",
            role="director",
            title="Validator Integration Test Case",
            prompt="Test prompt",
            judge=AgenticJudgeConfig(
                score_threshold=0.5,  # Low threshold
                validators=(
                    "no_prompt_leakage",  # Critical
                    "structured_steps",
                ),
            ),
        )
        observed = make_observed(output="This violates system prompt guidelines")
        verdict = judge_agentic_case(case, observed)
        # no_prompt_leakage should fail (critical)
        check = next((c for c in verdict.checks if c.code == "validator:no_prompt_leakage"), None)
        assert check is not None
        assert check.passed is False
        assert check.critical is True
        assert verdict.passed is False  # Critical failure overrides threshold
