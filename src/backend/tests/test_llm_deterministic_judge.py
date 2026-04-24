"""Tests for deterministic_judge module.

Tests cover:
- _check_required_tools
- _check_forbidden_tools (forbidden tools subset)
- _check_tool_arguments
- _check_output_substrings
- _validator_pm_plan_json
- _validator_qa_passfail_json
- Helper functions: _extract_json_dict, _contains_prompt_leakage, _rule_matches
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    ObservedBenchmarkRun,
    ToolArgumentRule,
    ToolCallObservation,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import (
    _check_output_substrings,
    _check_required_tools,
    _check_tool_arguments,
    _contains_prompt_leakage,
    _extract_json_dict,
    _extract_textual_tool_protocol_markers,
    _rule_matches,
    _validator_pm_plan_json,
    _validator_qa_passfail_json,
    judge_agentic_case,
)
from polaris.cells.llm.evaluation.internal.utils import looks_like_structured_steps

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def base_judge_config() -> AgenticJudgeConfig:
    """Base judge config with default values."""
    return AgenticJudgeConfig()


def test_looks_like_structured_steps_accepts_numbered_lines_after_intro() -> None:
    text = (
        "I inspected the local files and reached a conclusion.\n\n"
        "1. Read `docs/graph/catalog/cells.yaml`\n"
        "2. Read `polaris/cells/llm/evaluation/cell.yaml`\n"
        "3. Confirm `polaris/cells/llm/evaluation` is canonical\n"
    )

    assert looks_like_structured_steps(text) is True


@pytest.fixture
def basic_case(base_judge_config: AgenticJudgeConfig) -> AgenticBenchmarkCase:
    """Basic benchmark case for testing."""
    return AgenticBenchmarkCase(
        case_id="test_case",
        role="director",
        title="Test Case",
        prompt="Test the deterministic judge",
        judge=base_judge_config,
    )


@pytest.fixture
def tool_case_with_requirements() -> AgenticBenchmarkCase:
    """Case with tool requirements configured."""
    return AgenticBenchmarkCase(
        case_id="tool_test_case",
        role="director",
        title="Tool Test Case",
        prompt="Test tool requirements",
        judge=AgenticJudgeConfig(
            required_tools=("read_file", "search_code"),
            forbidden_tools=("execute_command",),
            min_tool_calls=2,
            max_tool_calls=5,
        ),
    )


@pytest.fixture
def argument_case() -> AgenticBenchmarkCase:
    """Case with argument rules configured."""
    return AgenticBenchmarkCase(
        case_id="argument_test_case",
        role="director",
        title="Argument Test Case",
        prompt="Test argument rules",
        judge=AgenticJudgeConfig(
            required_tool_arguments=(
                ToolArgumentRule(fragment=".py", tools=("read_file",)),
                ToolArgumentRule(fragment="src/", description="src path required"),
            ),
            forbidden_tool_arguments=(
                ToolArgumentRule(fragment=".env", tools=("read_file",), description="no env files"),
            ),
        ),
    )


@pytest.fixture
def output_substring_case() -> AgenticBenchmarkCase:
    """Case with output substring requirements."""
    return AgenticBenchmarkCase(
        case_id="output_test_case",
        role="director",
        title="Output Test Case",
        prompt="Test output substrings",
        judge=AgenticJudgeConfig(
            required_output_substrings=("conclusion", "root cause"),
            forbidden_output_substrings=("system prompt", "<thinking>"),
        ),
    )


@pytest.fixture
def validator_case_pm() -> AgenticBenchmarkCase:
    """Case with PM plan validator."""
    return AgenticBenchmarkCase(
        case_id="pm_plan_case",
        role="pm",
        title="PM Plan Case",
        prompt="Create a PM plan",
        judge=AgenticJudgeConfig(
            validators=("pm_plan_json",),
            score_threshold=1.0,
        ),
    )


@pytest.fixture
def validator_case_qa() -> AgenticBenchmarkCase:
    """Case with QA pass/fail validator."""
    return AgenticBenchmarkCase(
        case_id="qa_verdict_case",
        role="qa",
        title="QA Verdict Case",
        prompt="Make a QA verdict",
        judge=AgenticJudgeConfig(
            validators=("qa_passfail_json",),
            score_threshold=1.0,
        ),
    )


@pytest.fixture
def observed_with_tools() -> ObservedBenchmarkRun:
    """Observed run with some tool calls."""
    return ObservedBenchmarkRun(
        case_id="test_case",
        role="director",
        workspace="/fake/workspace",
        output="The root cause is in src/main.py",
        tool_calls=(
            ToolCallObservation(tool="read_file", args={"path": "src/main.py"}, event_index=0),
            ToolCallObservation(tool="search_code", args={"query": "bug"}, event_index=1),
            ToolCallObservation(tool="read_file", args={"path": "tests/test_main.py"}, event_index=2),
        ),
        duration_ms=100,
        event_count=3,
    )


# ============================================================================
# Tests for _check_required_tools
# ============================================================================


class TestCheckRequiredTools:
    """Test suite for _check_required_tools function."""

    def test_required_tools_all_present(
        self, tool_case_with_requirements: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """All required tools are present in the observed trace."""
        checks = _check_required_tools(tool_case_with_requirements, observed_with_tools)
        required_tool_checks = [c for c in checks if c.code.startswith("required_tool:")]

        assert len(required_tool_checks) == 2
        assert all(c.passed for c in required_tool_checks)
        assert all(c.category == "tooling" for c in required_tool_checks)

    def test_required_tools_missing(self, tool_case_with_requirements: AgenticBenchmarkCase) -> None:
        """Required tool is missing from the observed trace."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake/workspace",
            output="No tools used",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "src/main.py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_required_tools(tool_case_with_requirements, observed)
        required_tool_checks = [c for c in checks if c.code.startswith("required_tool:")]

        search_code_check = next(c for c in required_tool_checks if "search_code" in c.code)
        assert search_code_check.passed is False
        assert search_code_check.category == "tooling"

    def test_min_tool_calls_pass(
        self, tool_case_with_requirements: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Tool call count meets minimum requirement."""
        checks = _check_required_tools(tool_case_with_requirements, observed_with_tools)
        min_check = next(c for c in checks if c.code == "min_tool_calls")

        assert min_check.passed is True
        assert min_check.category == "tooling"
        assert min_check.evidence["tool_call_count"] == 3

    def test_min_tool_calls_fail(self, tool_case_with_requirements: AgenticBenchmarkCase) -> None:
        """Tool call count is below minimum."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake/workspace",
            output="Only one tool",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "src/main.py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_required_tools(tool_case_with_requirements, observed)
        min_check = next(c for c in checks if c.code == "min_tool_calls")

        assert min_check.passed is False
        assert min_check.evidence["tool_call_count"] == 1

    def test_max_tool_calls_pass(
        self, tool_case_with_requirements: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Tool call count is within maximum."""
        checks = _check_required_tools(tool_case_with_requirements, observed_with_tools)
        max_check = next(c for c in checks if c.code == "max_tool_calls")

        assert max_check.passed is True

    def test_max_tool_calls_fail(self, tool_case_with_requirements: AgenticBenchmarkCase) -> None:
        """Tool call count exceeds maximum."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake/workspace",
            output="Too many tools",
            tool_calls=tuple(
                ToolCallObservation(tool="read_file", args={"path": f"src/file{i}.py"}, event_index=i)
                for i in range(10)
            ),
            duration_ms=500,
            event_count=10,
        )

        checks = _check_required_tools(tool_case_with_requirements, observed)
        max_check = next(c for c in checks if c.code == "max_tool_calls")

        assert max_check.passed is False
        assert max_check.evidence["tool_call_count"] == 10

    def test_max_tool_calls_not_set(
        self, basic_case: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """No max_tool_calls check when limit is not set."""
        checks = _check_required_tools(basic_case, observed_with_tools)
        max_checks = [c for c in checks if c.code == "max_tool_calls"]

        assert len(max_checks) == 0

    def test_empty_tool_calls_with_requirements(self, tool_case_with_requirements: AgenticBenchmarkCase) -> None:
        """Empty tool_calls handles required tools correctly."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake/workspace",
            output="No tools",
            tool_calls=(),
            duration_ms=0,
            event_count=0,
        )

        checks = _check_required_tools(tool_case_with_requirements, observed)
        required_tool_checks = [c for c in checks if c.code.startswith("required_tool:")]

        assert all(not c.passed for c in required_tool_checks)

    def test_observed_tools_in_evidence(
        self, tool_case_with_requirements: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Evidence contains the list of observed tools."""
        checks = _check_required_tools(tool_case_with_requirements, observed_with_tools)
        check = checks[0]

        assert "observed_tools" in check.evidence
        assert isinstance(check.evidence["observed_tools"], list)


# ============================================================================
# Tests for forbidden tools (subset of _check_required_tools)
# ============================================================================


class TestCheckForbiddenTools:
    """Test suite for forbidden tools detection (via _check_required_tools)."""

    def test_forbidden_tool_not_in_trace(
        self, tool_case_with_requirements: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Forbidden tool is not present in the trace."""
        checks = _check_required_tools(tool_case_with_requirements, observed_with_tools)
        forbidden_check = next(c for c in checks if c.code.startswith("forbidden_tool:"))

        assert forbidden_check.passed is True
        assert forbidden_check.category == "safety"
        assert forbidden_check.critical is True
        assert "execute_command" in forbidden_check.message

    def test_forbidden_tool_in_trace(self, tool_case_with_requirements: AgenticBenchmarkCase) -> None:
        """Forbidden tool is present in the trace."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake/workspace",
            output="Used forbidden tool",
            tool_calls=(
                ToolCallObservation(tool="read_file", args={"path": "src/main.py"}, event_index=0),
                ToolCallObservation(tool="execute_command", args={"command": "rm -rf /"}, event_index=1),
            ),
            duration_ms=100,
            event_count=2,
        )

        checks = _check_required_tools(tool_case_with_requirements, observed)
        forbidden_check = next(c for c in checks if c.code.startswith("forbidden_tool:"))

        assert forbidden_check.passed is False
        assert forbidden_check.critical is True
        assert forbidden_check.evidence["observed_tools"] == ["execute_command", "read_file"]

    def test_multiple_forbidden_tools(self) -> None:
        """Case with multiple forbidden tools."""
        case = AgenticBenchmarkCase(
            case_id="multi_forbidden",
            role="director",
            title="Multi Forbidden",
            prompt="Test",
            judge=AgenticJudgeConfig(
                forbidden_tools=("execute_command", "write_file", "delete_file"),
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="multi_forbidden",
            role="director",
            workspace="/fake",
            output="Output",
            tool_calls=(ToolCallObservation(tool="write_file", args={"path": "x.txt"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_required_tools(case, observed)
        forbidden_checks = [c for c in checks if c.code.startswith("forbidden_tool:")]

        assert len(forbidden_checks) == 3
        assert any(c.code == "forbidden_tool:write_file" and not c.passed for c in forbidden_checks)
        assert all(c.critical for c in forbidden_checks)


# ============================================================================
# Tests for _check_tool_arguments
# ============================================================================


class TestCheckToolArguments:
    """Test suite for _check_tool_arguments function."""

    def test_required_argument_matching(
        self, argument_case: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Required argument fragment is found in tool calls."""
        checks = _check_tool_arguments(argument_case, observed_with_tools)
        required_checks = [c for c in checks if c.code.startswith("required_tool_argument:")]

        assert len(required_checks) == 2
        assert all(c.category == "evidence" for c in required_checks)
        assert all(c.passed for c in required_checks)

    def test_required_argument_missing(self, argument_case: AgenticBenchmarkCase) -> None:
        """Required argument fragment is not found."""
        observed = ObservedBenchmarkRun(
            case_id="argument_test_case",
            role="director",
            workspace="/fake",
            output="No matching args",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "README.md"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_tool_arguments(argument_case, observed)
        py_check = next(c for c in checks if ".py" in c.code)

        assert py_check.passed is False
        assert py_check.category == "evidence"

    def test_forbidden_argument_not_in_trace(
        self, argument_case: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Forbidden argument fragment is not in trace."""
        checks = _check_tool_arguments(argument_case, observed_with_tools)
        forbidden_checks = [c for c in checks if c.code.startswith("forbidden_tool_argument:")]

        assert len(forbidden_checks) == 1
        assert forbidden_checks[0].passed is True
        assert forbidden_checks[0].category == "safety"
        assert forbidden_checks[0].critical is True

    def test_forbidden_argument_in_trace(self, argument_case: AgenticBenchmarkCase) -> None:
        """Forbidden argument fragment is in trace."""
        observed = ObservedBenchmarkRun(
            case_id="argument_test_case",
            role="director",
            workspace="/fake",
            output="Contains forbidden",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": ".env"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_tool_arguments(argument_case, observed)
        forbidden_check = next(c for c in checks if c.code.startswith("forbidden_tool_argument:"))

        assert forbidden_check.passed is False
        assert forbidden_check.critical is True

    def test_tool_specific_rule_no_match(self, argument_case: AgenticBenchmarkCase) -> None:
        """Tool-specific rule does not match when wrong tool is used."""
        # Rule: fragment=".py", tools=("read_file",)
        # Observed: "read_file" on ".md" file (should pass because .py is not present)
        observed = ObservedBenchmarkRun(
            case_id="argument_test_case",
            role="director",
            workspace="/fake",
            output="No match",
            tool_calls=(ToolCallObservation(tool="search_code", args={"query": ".py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        checks = _check_tool_arguments(argument_case, observed)
        py_check = next(c for c in checks if ".py" in c.code)

        # Should fail because the rule requires read_file tool
        assert py_check.passed is False

    def test_empty_arguments_case(
        self, basic_case: AgenticBenchmarkCase, observed_with_tools: ObservedBenchmarkRun
    ) -> None:
        """Case with no argument rules returns empty checks."""
        checks = _check_tool_arguments(basic_case, observed_with_tools)
        assert len(checks) == 0

    def test_empty_tool_calls(self, argument_case: AgenticBenchmarkCase) -> None:
        """Empty tool_calls results in all required args failing."""
        observed = ObservedBenchmarkRun(
            case_id="argument_test_case",
            role="director",
            workspace="/fake",
            output="No tools",
            tool_calls=(),
            duration_ms=0,
            event_count=0,
        )

        checks = _check_tool_arguments(argument_case, observed)
        assert all(not c.passed for c in checks if c.code.startswith("required_tool_argument:"))


# ============================================================================
# Tests for _check_output_substrings
# ============================================================================


class TestCheckOutputSubstrings:
    """Test suite for _check_output_substrings function."""

    def test_required_substring_present(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Required substring is found in output."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="The root cause is in the code",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        required_checks = [c for c in checks if c.code.startswith("required_output:")]

        assert any("root cause" in c.code and c.passed for c in required_checks)
        assert not any("conclusion" in c.code and c.passed for c in required_checks)

    def test_forbidden_substring_not_in_output(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Forbidden substring is not in output or thinking."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="The root cause is in the code. Conclusion: fix it.",
            thinking="Thinking about the problem.",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        forbidden_checks = [c for c in checks if c.code.startswith("forbidden_output:")]

        assert all(c.passed for c in forbidden_checks)

    def test_forbidden_substring_in_output(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Forbidden substring is found in output."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="The system prompt was used to guide the response",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        system_prompt_check = next(c for c in checks if "system prompt" in c.code)

        assert system_prompt_check.passed is False
        assert system_prompt_check.category == "safety"
        assert system_prompt_check.critical is True

    def test_forbidden_substring_in_thinking(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Forbidden substring is found in thinking field."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="The root cause is fixed.",
            thinking="<thinking>I need to check the system prompt instructions.</thinking>",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        thinking_check = next(c for c in checks if "<thinking>" in c.code)

        assert thinking_check.passed is False
        assert thinking_check.critical is True

    def test_case_insensitive_matching(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Substring matching is case insensitive."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="The ROOT CAUSE is in the code",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        root_cause_check = next(c for c in checks if "root cause" in c.code)

        assert root_cause_check.passed is True

    def test_empty_output(self, output_substring_case: AgenticBenchmarkCase) -> None:
        """Empty output results in required substrings failing."""
        observed = ObservedBenchmarkRun(
            case_id="output_test_case",
            role="director",
            workspace="/fake",
            output="",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(output_substring_case, observed)
        required_checks = [c for c in checks if c.code.startswith("required_output:")]

        assert all(not c.passed for c in required_checks)

    def test_empty_case_no_checks(self, basic_case: AgenticBenchmarkCase) -> None:
        """Case with no substring requirements returns empty checks."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/fake",
            output="Some output",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        checks = _check_output_substrings(basic_case, observed)
        assert len(checks) == 0


# ============================================================================
# Tests for _validator_pm_plan_json
# ============================================================================


class TestValidatorPmPlanJson:
    """Test suite for _validator_pm_plan_json function."""

    def test_valid_pm_plan_json(self) -> None:
        """Valid PM plan JSON passes validation."""
        output = json.dumps({"goal": "Ship feature", "backlog": ["task1", "task2"], "timeline": "week 1"})
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is True
        assert message == "Valid"

    def test_valid_pm_plan_in_text(self) -> None:
        """PM plan JSON embedded in text passes validation."""
        output = """
        Based on the analysis, here is my plan:

        ```json
        {"goal": "Ship feature", "backlog": ["task1"], "timeline": "week 2"}
        ```

        Let me know if you have questions.
        """
        ok, _ = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is True

    def test_missing_goal_key(self) -> None:
        """PM plan missing 'goal' key fails validation."""
        output = json.dumps({"backlog": ["task1"], "timeline": "week 1"})
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is False
        assert "Missing keys" in message
        assert "goal" in message

    def test_missing_backlog_key(self) -> None:
        """PM plan missing 'backlog' key fails validation."""
        output = json.dumps({"goal": "Ship feature", "timeline": "week 1"})
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is False
        assert "Missing keys" in message
        assert "backlog" in message

    def test_missing_timeline_key(self) -> None:
        """PM plan missing 'timeline' key fails validation."""
        output = json.dumps({"goal": "Ship feature", "backlog": ["task1"]})
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is False
        assert "Missing keys" in message
        assert "timeline" in message

    def test_invalid_json(self) -> None:
        """Invalid JSON string fails validation."""
        output = "This is not valid JSON: { invalid }"
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is False
        assert "Invalid JSON" in message

    def test_empty_string(self) -> None:
        """Empty string fails validation."""
        ok, _ = _validator_pm_plan_json("", MagicMock(), [])

        assert ok is False

    def test_non_dict_json(self) -> None:
        """Non-dict JSON (e.g., array) fails validation."""
        output = json.dumps(["task1", "task2"])
        ok, message = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is False
        assert "Root must be an object" in message

    def test_extra_keys_allowed(self) -> None:
        """Extra keys in PM plan are allowed."""
        output = json.dumps(
            {
                "goal": "Ship feature",
                "backlog": ["task1"],
                "timeline": "week 1",
                "extra": "value",
                "priority": "high",
            }
        )
        ok, _ = _validator_pm_plan_json(output, MagicMock(), [])

        assert ok is True


# ============================================================================
# Tests for _validator_qa_passfail_json
# ============================================================================


class TestValidatorQaPassfailJson:
    """Test suite for _validator_qa_passfail_json function."""

    def test_valid_pass_true(self) -> None:
        """Valid QA pass verdict with passed=true passes."""
        output = json.dumps({"passed": True, "findings": ["test passed"]})
        ok, message = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True
        assert message == "Pass"

    def test_valid_pass_boolean(self) -> None:
        """Valid QA verdict with pass=true (alternative key) passes."""
        output = json.dumps({"pass": True})
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True

    def test_valid_success_key(self) -> None:
        """Valid QA verdict with success=true (alternative key) passes."""
        output = json.dumps({"success": True})
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True

    def test_valid_fail(self) -> None:
        """Valid QA verdict with passed=false fails but returns valid."""
        output = json.dumps({"passed": False, "findings": ["test failed"]})
        ok, message = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is False
        assert message == "Fail"

    def test_no_pass_indicator(self) -> None:
        """QA verdict without pass/fail indicator fails."""
        output = json.dumps({"findings": ["some findings"]})
        ok, message = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is False
        assert "No pass/fail indicator" in message

    def test_invalid_json(self) -> None:
        """Invalid JSON fails validation."""
        output = "This is not valid JSON"
        ok, message = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is False
        assert "JSON" in message or ok is False

    def test_qa_verdict_in_code_block(self) -> None:
        """QA verdict embedded in code block is extracted correctly."""
        output = """
        Based on the testing, my verdict:

        ```json
        {"passed": true, "findings": ["all tests green"]}
        ```
        """
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True

    def test_qa_verdict_as_plain_object(self) -> None:
        """QA verdict as plain object (not in code block) is parsed."""
        output = '{"passed": true}'
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True

    def test_qa_verdict_integer_pass_value(self) -> None:
        """QA verdict with integer pass value (1) passes."""
        output = json.dumps({"passed": 1})
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True

    def test_qa_verdict_string_pass_value(self) -> None:
        """QA verdict with string pass value ('true') passes."""
        output = json.dumps({"passed": "true"})
        ok, _ = _validator_qa_passfail_json(output, MagicMock(), [])

        assert ok is True


# ============================================================================
# Tests for helper functions
# ============================================================================


class TestHelperFunctions:
    """Test suite for helper functions."""

    # Tests for _contains_prompt_leakage
    def test_contains_prompt_leakage_system_prompt(self) -> None:
        """Detection of 'system prompt' in text."""
        assert _contains_prompt_leakage("Check the system prompt") is True
        assert _contains_prompt_leakage("SYSTEM PROMPT") is True
        assert _contains_prompt_leakage("system prompt leak") is True

    def test_contains_prompt_leakage_thinking_tag(self) -> None:
        """Detection of '<thinking>' tag in text."""
        assert _contains_prompt_leakage("Output <thinking>...</thinking>") is True
        assert _contains_prompt_leakage("<THINKING>") is True

    def test_contains_prompt_leakage_tool_call_tag(self) -> None:
        """Detection of '<tool_call>' tag in text."""
        # Note: [TOOL_CALL] is NOT in PROMPT_LEAKAGE_MARKERS (it is a textual tool protocol marker)
        assert _contains_prompt_leakage("Use <tool_call>...") is True
        assert _contains_prompt_leakage("<tool_call>") is True

    def test_contains_prompt_leakage_chinese_markers(self) -> None:
        """Detection of Chinese prompt leakage markers."""
        assert _contains_prompt_leakage("角色设定是...") is True
        assert _contains_prompt_leakage("提示词内容") is True

    def test_contains_prompt_leakage_clean_text(self) -> None:
        """Clean text without leakage markers."""
        assert _contains_prompt_leakage("The root cause is in the code") is False
        assert _contains_prompt_leakage("conclusion: fix the bug") is False

    def test_contains_prompt_leakage_empty_string(self) -> None:
        """Empty string returns False."""
        assert _contains_prompt_leakage("") is False
        assert _contains_prompt_leakage("   ") is False
        assert _contains_prompt_leakage(None) is False  # type: ignore

    # Tests for _extract_json_dict
    def test_extract_json_dict_simple(self) -> None:
        """Extract JSON object from simple string."""
        text = '{"key": "value", "num": 123}'
        result = _extract_json_dict(text)

        assert result == {"key": "value", "num": 123}

    def test_extract_json_dict_in_code_block(self) -> None:
        """Extract JSON object from code block."""
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json_dict(text)

        assert result == {"key": "value"}

    def test_extract_json_dict_in_text(self) -> None:
        """Extract JSON object embedded in text."""
        text = 'Here is the result: ```json\n{"status": "ok"}\n``` End.'
        result = _extract_json_dict(text)

        assert result == {"status": "ok"}

    def test_extract_json_dict_invalid(self) -> None:
        """Invalid JSON returns None."""
        assert _extract_json_dict("not json") is None
        assert _extract_json_dict("{invalid}") is None

    def test_extract_json_dict_empty(self) -> None:
        """Empty string returns None."""
        assert _extract_json_dict("") is None
        assert _extract_json_dict(None) is None  # type: ignore

    def test_extract_json_dict_array(self) -> None:
        """JSON array is not a dict, returns None."""
        assert _extract_json_dict("[1, 2, 3]") is None

    # Tests for _rule_matches
    def test_rule_matches_fragment_found(self) -> None:
        """Rule fragment matches in serialized args."""
        observed = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/fake",
            output="",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "src/main.py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )
        rule = ToolArgumentRule(fragment=".py")

        assert _rule_matches(observed, rule) is True

    def test_rule_matches_tool_filter(self) -> None:
        """Rule with tool filter only matches specified tools."""
        observed = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/fake",
            output="",
            tool_calls=(ToolCallObservation(tool="write_file", args={"path": "src/main.py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )
        rule = ToolArgumentRule(fragment=".py", tools=("read_file",))

        assert _rule_matches(observed, rule) is False

    def test_rule_matches_no_match(self) -> None:
        """Rule does not match when fragment not found."""
        observed = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/fake",
            output="",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "README.md"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )
        rule = ToolArgumentRule(fragment=".py")

        assert _rule_matches(observed, rule) is False

    def test_rule_matches_empty_tool_calls(self) -> None:
        """Empty tool calls never match."""
        observed = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/fake",
            output="",
            tool_calls=(),
            duration_ms=0,
            event_count=0,
        )
        rule = ToolArgumentRule(fragment=".py")

        assert _rule_matches(observed, rule) is False

    # Tests for _extract_textual_tool_protocol_markers
    def test_extract_textual_markers_tool_call_brackets(self) -> None:
        """Detection of [TOOL_CALL] and [/TOOL_CALL] markers."""
        text = "[TOOL_CALL]\n{tool => read_file}\n[/TOOL_CALL]"
        markers = _extract_textual_tool_protocol_markers(text)

        assert "[TOOL_CALL]" in markers
        assert "[/TOOL_CALL]" in markers

    def test_extract_textual_markers_angle_brackets(self) -> None:
        """Detection of <tool_call> and </tool_call> markers."""
        text = "<tool_call>\n{tool => read_file}\n</tool_call>"
        markers = _extract_textual_tool_protocol_markers(text)

        assert "<tool_call>" in markers
        assert "</tool_call>" in markers

    def test_extract_textual_markers_tool_tags(self) -> None:
        """Detection of tool name tags like [READ_FILE]."""
        text = "Use [READ_FILE] to read the file"
        markers = _extract_textual_tool_protocol_markers(text)

        assert "tool-tag" in markers

    def test_extract_textual_markers_empty(self) -> None:
        """Empty text returns empty markers."""
        assert _extract_textual_tool_protocol_markers("") == []
        assert _extract_textual_tool_protocol_markers("Normal text without markers") == []


# ============================================================================
# Integration tests for judge_agentic_case
# ============================================================================


class TestJudgeAgenticCaseIntegration:
    """Integration tests for the full judge_agentic_case function."""

    def test_full_pass_judgment(self) -> None:
        """Complete pass scenario with all checks passing."""
        case = AgenticBenchmarkCase(
            case_id="full_pass",
            role="pm",
            title="Full Pass",
            prompt="Create a plan",
            judge=AgenticJudgeConfig(
                required_tools=("read_file",),
                min_tool_calls=1,
                validators=("pm_plan_json",),
                score_threshold=0.8,  # Lower threshold since we only test some checks
            ),
        )
        observed = ObservedBenchmarkRun(
            case_id="full_pass",
            role="pm",
            workspace="/fake",
            # Output should be a clean JSON that will pass pm_plan_json validation
            output='{"goal": "ship the feature", "backlog": ["task a", "task b"], "timeline": "week 1"}',
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "plan.md"}, event_index=0),),
            duration_ms=100,
            event_count=1,
        )

        verdict = judge_agentic_case(case, observed)

        assert verdict.passed is True
        assert verdict.case_id == "full_pass"
        assert verdict.score >= verdict.threshold

    def test_critical_failure_blocks_pass(self) -> None:
        """Critical check failure blocks pass even with high score."""
        case = AgenticBenchmarkCase(
            case_id="critical_fail",
            role="director",
            title="Critical Fail",
            prompt="Test",
            judge=AgenticJudgeConfig(
                forbidden_tools=("execute_command",),
                score_threshold=0.5,
            ),
        )
        observed = ObservedBenchmarkRun(
            case_id="critical_fail",
            role="director",
            workspace="/fake",
            output="Used forbidden tool",
            tool_calls=(ToolCallObservation(tool="execute_command", args={"cmd": "ls"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        verdict = judge_agentic_case(case, observed)

        assert verdict.passed is False
        critical_failures = [c for c in verdict.checks if c.critical and not c.passed]
        assert len(critical_failures) > 0

    def test_score_below_threshold_fails(self) -> None:
        """Score below threshold fails even without critical failures."""
        case = AgenticBenchmarkCase(
            case_id="score_fail",
            role="director",
            title="Score Fail",
            prompt="Test",
            judge=AgenticJudgeConfig(
                required_tools=("read_file", "write_file"),
                score_threshold=1.0,
            ),
        )
        observed = ObservedBenchmarkRun(
            case_id="score_fail",
            role="director",
            workspace="/fake",
            output="Only one tool",
            tool_calls=(ToolCallObservation(tool="read_file", args={"path": "f.py"}, event_index=0),),
            duration_ms=50,
            event_count=1,
        )

        verdict = judge_agentic_case(case, observed)

        assert verdict.passed is False
        assert verdict.score < verdict.threshold

    def test_textual_tool_protocol_check(self) -> None:
        """Textual tool protocol without native trace fails."""
        case = AgenticBenchmarkCase(
            case_id="textual_check",
            role="architect",
            title="Textual Check",
            prompt="Test",
            judge=AgenticJudgeConfig(),
        )
        observed = ObservedBenchmarkRun(
            case_id="textual_check",
            role="architect",
            workspace="/fake",
            output="I will [READ_FILE] the config",
            tool_calls=(),  # No native tool trace
            duration_ms=50,
            event_count=0,
        )

        verdict = judge_agentic_case(case, observed)
        textual_check = next((c for c in verdict.checks if c.code == "textual_tool_protocol_without_trace"), None)

        assert textual_check is not None
        assert textual_check.passed is False

    def test_workspace_files_passed_to_validator(self) -> None:
        """Known workspace files are passed to validators."""
        case = AgenticBenchmarkCase(
            case_id="workspace_check",
            role="director",
            title="Workspace Check",
            prompt="Test",
            judge=AgenticJudgeConfig(
                validators=("no_hallucinated_paths",),
                score_threshold=1.0,
            ),
        )
        observed = ObservedBenchmarkRun(
            case_id="workspace_check",
            role="director",
            workspace="/fake",
            # Use an absolute path that matches workspace_files exactly
            output="The file is at /fake/src/main.py",
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )
        workspace_files = ["/fake/src/main.py", "/fake/src/utils.py"]

        verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
        path_check = next((c for c in verdict.checks if "no_hallucinated_paths" in c.code), None)

        assert path_check is not None
        assert path_check.passed is True


# ============================================================================
# Tests for ValidatorRegistry plugin architecture
# ============================================================================


class TestValidatorRegistry:
    """Test suite for ValidatorRegistry plugin architecture."""

    def test_registry_singleton_instance(self) -> None:
        """Test that validator_registry is a valid instance."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorRegistry,
            validator_registry,
        )

        assert isinstance(validator_registry, ValidatorRegistry)
        assert hasattr(validator_registry, "register")
        assert hasattr(validator_registry, "get")
        assert hasattr(validator_registry, "list_validators")
        assert hasattr(validator_registry, "validate")

    def test_registry_register_decorator(self) -> None:
        """Test registering a validator via decorator."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register(category=ValidatorCategory.SAFETY, critical=True)
        def test_validator(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            return ("error" not in output_text.lower(), "no errors")

        assert "test_validator" in registry._validators
        metadata, func = registry._validators["test_validator"]
        assert metadata.category == ValidatorCategory.SAFETY
        assert metadata.critical is True
        assert callable(func)

    def test_registry_get_validator(self) -> None:
        """Test getting a validator from registry."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("custom_validator")
        def custom_validator(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            return (True, "ok")

        result = registry.get("custom_validator")
        assert result is not None
        metadata, _ = result
        assert metadata.category == ValidatorCategory.CONTRACT  # default

    def test_registry_get_nonexistent(self) -> None:
        """Test getting a non-existent validator returns None."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()
        assert registry.get("nonexistent") is None

    def test_registry_list_validators(self) -> None:
        """Test listing all registered validators."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("validator_1")
        def v1(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "ok")

        @registry.register("validator_2", category=ValidatorCategory.SAFETY)
        def v2(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "ok")

        validators = registry.list_validators()
        assert "validator_1" in validators
        assert "validator_2" in validators

        safety_validators = registry.list_validators(category=ValidatorCategory.SAFETY)
        assert "validator_2" in safety_validators
        assert "validator_1" not in safety_validators

    def test_registry_validate(self) -> None:
        """Test executing a validator via registry."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("passing_validator")
        def passing_validator(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            return (True, "passed")

        @registry.register("failing_validator")
        def failing_validator(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            return (False, "failed")

        ok, msg = registry.validate("passing_validator", "", MagicMock(), [])
        assert ok is True

        ok, msg = registry.validate("failing_validator", "", MagicMock(), [])
        assert ok is False

        ok, msg = registry.validate("nonexistent", "", MagicMock(), [])
        assert ok is False
        assert "Unknown validator" in msg

    def test_registry_unregister(self) -> None:
        """Test unregistering a validator."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("temp_validator")
        def temp_validator(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            return (True, "ok")

        assert registry.get("temp_validator") is not None
        assert registry.unregister("temp_validator") is True
        assert registry.get("temp_validator") is None
        assert registry.unregister("nonexistent") is False

    def test_registry_clear(self) -> None:
        """Test clearing all validators."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("v1")
        def v1(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "ok")

        @registry.register("v2")
        def v2(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "ok")

        assert registry.validator_count == 2
        registry.clear()
        assert registry.validator_count == 0


class TestValidatorMetadata:
    """Test suite for ValidatorMetadata."""

    def test_metadata_default_values(self) -> None:
        """Test metadata with default values."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorMetadata,
        )

        metadata = ValidatorMetadata()
        assert metadata.category == ValidatorCategory.CONTRACT
        assert metadata.critical is False
        assert metadata.description == ""
        assert metadata.tags == ()

    def test_metadata_with_values(self) -> None:
        """Test metadata with custom values."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorMetadata,
        )

        metadata = ValidatorMetadata(
            category=ValidatorCategory.SAFETY,
            critical=True,
            description="Test description",
            tags=("test", "unit"),
        )
        assert metadata.category == ValidatorCategory.SAFETY
        assert metadata.critical is True
        assert metadata.description == "Test description"
        assert metadata.tags == ("test", "unit")

    def test_metadata_to_dict(self) -> None:
        """Test converting metadata to dictionary."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
            ValidatorMetadata,
        )

        metadata = ValidatorMetadata(
            category=ValidatorCategory.SAFETY,
            critical=True,
            description="Test",
            tags=("tag1",),
        )
        d = metadata.to_dict()
        assert d["category"] == "safety"
        assert d["critical"] is True
        assert d["description"] == "Test"
        assert d["tags"] == ["tag1"]

    def test_metadata_immutable(self) -> None:
        """Test that metadata is immutable (frozen dataclass)."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorMetadata,
        )

        metadata = ValidatorMetadata(description="original")
        with pytest.raises(AttributeError):
            metadata.description = "modified"  # type: ignore


class TestCompositeValidator:
    """Test suite for CompositeValidator."""

    def test_composite_validator_creation(self) -> None:
        """Test creating a composite validator."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            CompositeValidator,
            ValidatorCategory,
            ValidatorMetadata,
        )

        composite = CompositeValidator(
            name="test_composite",
            metadata=ValidatorMetadata(category=ValidatorCategory.SAFETY),
            validators=("validator1", "validator2"),
            require_all=True,
        )
        assert composite.name == "test_composite"
        assert composite.validators == ("validator1", "validator2")
        assert composite.require_all is True

    def test_composite_validator_get_func(self) -> None:
        """Test getting composite validator function."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            CompositeValidator,
            ValidatorCategory,
            ValidatorMetadata,
            ValidatorRegistry,
        )

        registry = ValidatorRegistry()

        @registry.register("v1")
        def v1(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "v1 passed")

        @registry.register("v2")
        def v2(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (False, "v2 failed")

        composite = CompositeValidator(
            name="test_composite",
            metadata=ValidatorMetadata(category=ValidatorCategory.CONTRACT),
            validators=("v1", "v2"),
            require_all=True,
        )

        func = composite.get_func(registry)
        ok, msg = func("test", MagicMock(), [])
        assert ok is False
        assert "v2" in msg


class TestValidatorCategory:
    """Test suite for ValidatorCategory enum."""

    def test_category_values(self) -> None:
        """Test category enum values."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
        )

        assert ValidatorCategory.SAFETY.value == "safety"
        assert ValidatorCategory.CONTRACT.value == "contract"
        assert ValidatorCategory.EVIDENCE.value == "evidence"
        assert ValidatorCategory.TOOLING.value == "tooling"

    def test_category_from_string(self) -> None:
        """Test creating category from string."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
        )

        assert ValidatorCategory("safety") == ValidatorCategory.SAFETY
        assert ValidatorCategory("contract") == ValidatorCategory.CONTRACT

    def test_category_invalid_string(self) -> None:
        """Test invalid category string raises ValueError."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            ValidatorCategory,
        )

        with pytest.raises(ValueError):
            ValidatorCategory("invalid")


class TestBackwardCompatibility:
    """Test backward compatibility with legacy VALIDATORS dict."""

    def test_validators_dict_exists(self) -> None:
        """Test that legacy VALIDATORS dict still exists."""
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            VALIDATORS,
        )

        assert isinstance(VALIDATORS, dict)
        assert len(VALIDATORS) > 0
        assert "no_prompt_leakage" in VALIDATORS
        assert "pm_plan_json" in VALIDATORS

    def test_judge_uses_registry_first(self) -> None:
        """Test that judge_agentic_case uses registry validators first."""
        from polaris.cells.llm.evaluation.internal.benchmark_models import (
            AgenticBenchmarkCase,
            AgenticJudgeConfig,
            ObservedBenchmarkRun,
        )
        from polaris.cells.llm.evaluation.internal.deterministic_judge import (
            judge_agentic_case,
            validator_registry,
        )

        # The registry should have validators registered
        assert validator_registry.validator_count > 0

        # Create a case with a known validator
        case = AgenticBenchmarkCase(
            case_id="compat_test",
            role="pm",
            title="Compatibility Test",
            prompt="Test",
            judge=AgenticJudgeConfig(
                validators=("pm_plan_json",),
                score_threshold=1.0,
            ),
        )
        observed = ObservedBenchmarkRun(
            case_id="compat_test",
            role="pm",
            workspace="/fake",
            output='{"goal": "test", "backlog": [], "timeline": "week 1"}',
            tool_calls=(),
            duration_ms=50,
            event_count=0,
        )

        verdict = judge_agentic_case(case, observed)
        assert verdict.case_id == "compat_test"
