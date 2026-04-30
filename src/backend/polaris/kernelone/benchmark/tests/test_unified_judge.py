"""Tests for unified_judge.py.

These tests verify the deterministic judge engine.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.benchmark.unified_judge import (
    NoHallucinatedPathsValidator,
    NoPromptLeakageValidator,
    StructuredStepsValidator,
    UnifiedJudge,
    _ExcessiveNestingError,
    _safe_json_loads,
    _validate_pm_plan_json,
    _validate_qa_passfail,
)
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ObservedBenchmarkRun,
    ToolCallObservation,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.benchmark.validators.contextos_validators import (
    ContextOSDesynchronizationValidator,
    ContextOSIncorrectTruncationValidator,
    ContextOSLongSessionValidator,
    ContextOSLossValidator,
    ContextOSTraceAnalyzer,
    ContextOSTraceEvent,
)


class TestNoPromptLeakageValidator:
    """Tests for NoPromptLeakageValidator."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = NoPromptLeakageValidator()

    def test_clean_output_passes(self) -> None:
        """Test clean output passes validation."""
        ok, _msg = self.validator.validate(
            "Here is my analysis of the code.",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="Analysis complete",
            ),
            [],
        )
        assert ok is True

    def test_prompt_leakage_detected(self) -> None:
        """Test prompt leakage markers are detected."""
        ok, msg = self.validator.validate(
            "The system prompt says you should...",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="system prompt says...",
            ),
            [],
        )
        assert ok is False
        assert "prompt leakage marker found" in msg

    def test_thinking_tag_detected(self) -> None:
        """Test <thinking> tag is detected as leakage."""
        ok, _msg = self.validator.validate(
            "<thinking>Let me analyze this</thinking>",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="<thinking>Let me analyze</thinking>",
            ),
            [],
        )
        assert ok is False


class TestStructuredStepsValidator:
    """Tests for StructuredStepsValidator."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = StructuredStepsValidator()

    def test_numbered_steps_passes(self) -> None:
        """Test numbered steps pass validation."""
        ok, _msg = self.validator.validate(
            "1. First step\n2. Second step\n3. Third step",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="1. First step\n2. Second step",
            ),
            [],
        )
        assert ok is True

    def test_plain_text_fails(self) -> None:
        """Test plain text fails validation."""
        ok, _msg = self.validator.validate(
            "Here is my analysis of the codebase.",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="Here is my analysis",
            ),
            [],
        )
        assert ok is False


class TestNoHallucinatedPathsValidator:
    """Tests for NoHallucinatedPathsValidator."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.validator = NoHallucinatedPathsValidator()

    def test_known_paths_passes(self) -> None:
        """Test output with known paths passes."""
        known = ["src/main.py", "tests/test_main.py", "README.md"]
        ok, _msg = self.validator.validate(
            "I read src/main.py and tests/test_main.py",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="I read src/main.py",
            ),
            known,
        )
        assert ok is True

    def test_hallucinated_path_detected(self) -> None:
        """Test hallucinated paths are detected."""
        known = ["src/main.py", "README.md"]
        ok, _msg = self.validator.validate(
            "I read nonexistent/file.py which has the bug",
            ObservedBenchmarkRun(
                case_id="test",
                role="director",
                workspace="/tmp",
                output="I read nonexistent/file.py",
            ),
            known,
        )
        assert ok is False


class TestUnifiedJudge:
    """Tests for UnifiedJudge class."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.judge = UnifiedJudge()

    def test_judge_passes_when_all_required_tools_found(self) -> None:
        """Test passing when required tools are present."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Find and fix the bug",
            judge=JudgeConfig(
                required_tools=("search_code", "read_file"),
                score_threshold=0.75,
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Fixed the bug",
            tool_calls=(
                ToolCallObservation(tool="search_code", args={"query": "bug"}),
                ToolCallObservation(tool="read_file", args={"file": "src/bug.py"}),
            ),
        )

        verdict = self.judge.judge(case, observed)

        assert verdict.passed is True
        assert verdict.score >= 0.75

    def test_judge_fails_when_forbidden_tool_used(self) -> None:
        """Test failing when forbidden tool is used."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Safe scope planning",
            judge=JudgeConfig(
                forbidden_tools=("write_file",),
                score_threshold=0.75,
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="I wrote the file",
            tool_calls=(ToolCallObservation(tool="write_file", args={"path": "docs/README.md"}),),
        )

        verdict = self.judge.judge(case, observed)

        assert verdict.passed is False
        assert any(not c.passed and c.code.startswith("forbidden_tool:") for c in verdict.checks)

    def test_judge_checks_min_tool_calls(self) -> None:
        """Test min_tool_calls check."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Research the codebase",
            judge=JudgeConfig(
                min_tool_calls=3,
                score_threshold=0.75,
            ),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(ToolCallObservation(tool="read_file", args={}),),
        )

        verdict = self.judge.judge(case, observed)

        min_check = next(c for c in verdict.checks if c.code == "min_tool_calls")
        assert min_check.passed is False

    def test_judge_validates_required_output_substring(self) -> None:
        """Test required output substring check."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Diagnose the issue",
            judge=JudgeConfig(
                required_output_substrings=("root cause",),
                score_threshold=0.75,
            ),
        )

        # Output without required substring
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="The issue is fixed",
        )

        verdict = self.judge.judge(case, observed)

        output_check = next(c for c in verdict.checks if c.code == "required_output:root cause")
        assert output_check.passed is False

        # Output with required substring
        observed2 = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="The root cause is in src/main.py",
        )

        verdict2 = self.judge.judge(case, observed2)
        output_check2 = next(c for c in verdict2.checks if c.code == "required_output:root cause")
        assert output_check2.passed is True

    def test_judge_unknown_validator_fails(self) -> None:
        """Test unknown validator produces failing check."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test",
            judge=JudgeConfig(validators=("nonexistent_validator",)),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Test output",
        )

        verdict = self.judge.judge(case, observed)

        validator_check = next(c for c in verdict.checks if c.code == "validator:nonexistent_validator")
        assert validator_check.passed is False
        assert "unknown validator" in validator_check.message.lower()

    def test_judge_pm_plan_json_validator(self) -> None:
        """Test PM plan JSON validation."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="pm",
            title="Test",
            prompt="Create a plan",
            judge=JudgeConfig(validators=("pm_plan_json",)),
        )

        # Valid PM plan
        valid_json = '{"goal": "ship v1", "backlog": ["task1"], "timeline": "2 weeks"}'
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="pm",
            workspace="/tmp",
            output=f"```json\n{valid_json}\n```",
        )

        verdict = self.judge.judge(case, observed)
        pm_check = next(c for c in verdict.checks if c.code == "validator:pm_plan_json")
        assert pm_check.passed is True

    def test_register_custom_validator(self) -> None:
        """Test registering a custom validator."""

        class CustomValidator:
            name: str = "custom_validator"
            category: str = "contract"
            critical: bool = False

            def validate(self, output_text, observed, known_paths):
                return ("custom_pattern" in output_text, "custom check")

        self.judge.register_validator(CustomValidator())

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test",
            judge=JudgeConfig(validators=("custom_validator",)),
        )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="output with custom_pattern",
        )

        verdict = self.judge.judge(case, observed)
        custom_check = next(c for c in verdict.checks if c.code == "validator:custom_validator")
        assert custom_check.passed is True

    def test_judge_graceful_error_handling(self) -> None:
        """Test judge handles errors gracefully."""
        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test",
            judge=JudgeConfig(),
        )

        # Empty observation with no tool_calls - should not raise
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="",
            tool_calls=(),
        )

        verdict = self.judge.judge(case, observed)
        assert verdict.case_id == "test_case"


class TestSafeJsonLoads:
    """Tests for _safe_json_loads function."""

    def test_valid_json(self) -> None:
        """Test parsing valid JSON."""
        result = _safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    def test_excessive_nesting_raises(self) -> None:
        """Test excessive nesting raises error."""
        deep_json = "{" * 200 + "}" * 200
        with pytest.raises(_ExcessiveNestingError):
            _safe_json_loads(deep_json, max_depth=100)

    def test_valid_nested_json(self) -> None:
        """Test valid nested JSON within depth limit."""
        nested = '{"a": {"b": {"c": 1}}}'
        result = _safe_json_loads(nested, max_depth=10)
        assert result == {"a": {"b": {"c": 1}}}


class TestPmPlanJsonValidation:
    """Tests for PM plan JSON validation."""

    def test_valid_pm_plan(self) -> None:
        """Test valid PM plan passes."""
        valid = '{"goal": "ship v1", "backlog": ["task1"], "timeline": "2 weeks"}'
        ok, _msg = _validate_pm_plan_json(f"```json\n{valid}\n```")
        assert ok is True

    def test_missing_keys_fails(self) -> None:
        """Test missing required keys fails."""
        invalid = '{"goal": "ship v1"}'
        ok, msg = _validate_pm_plan_json(invalid)
        assert ok is False
        assert "missing keys" in msg


class TestQaPassfailValidation:
    """Tests for QA pass/fail JSON validation."""

    def test_valid_qa_verdict(self) -> None:
        """Test valid QA verdict passes."""
        valid = '{"passed": true, "findings": ["issue 1"]}'
        ok, _msg = _validate_qa_passfail(valid)
        assert ok is True

    def test_missing_keys_fails(self) -> None:
        """Test missing required keys fails."""
        invalid = '{"passed": true}'
        ok, _msg = _validate_qa_passfail(invalid)
        assert ok is False


# =============================================================================
# ContextOS Benchmark Tests
# =============================================================================


class TestContextOSTraceAnalyzer:
    """Tests for ContextOSTraceAnalyzer."""

    def test_parse_events(self) -> None:
        """Test parsing raw events into structured traces."""
        events = [
            {"event": "llm_call_start", "data": {"context_tokens_before": 5000, "iteration": 0}},
            {"event": "llm_call_end", "data": {"context_tokens_after": 5800, "iteration": 0}},
            {"event": "llm_call_start", "data": {"context_tokens_before": 5800, "iteration": 1}},
            {"event": "llm_call_end", "data": {"context_tokens_after": 6200, "iteration": 1}},
        ]

        analyzer = ContextOSTraceAnalyzer(events)
        traces = analyzer.traces

        assert len(traces) == 4
        assert traces[0].event_type == "llm_call_start"
        assert traces[0].context_tokens_before == 5000
        assert traces[0].turn_index == 0
        assert traces[1].event_type == "llm_call_end"
        assert traces[1].context_tokens_after == 5800

    def test_parse_events_ignores_non_llm_events(self) -> None:
        """Test that non-llm_call events are ignored."""
        events = [
            {"event": "tool_call", "data": {"tool": "read_file"}},
            {"event": "llm_call_start", "data": {"context_tokens_before": 5000, "iteration": 0}},
            {"event": "tool_result", "data": {"tool": "read_file"}},
            {"event": "llm_call_end", "data": {"context_tokens_after": 5800, "iteration": 0}},
        ]

        analyzer = ContextOSTraceAnalyzer(events)
        traces = analyzer.traces

        assert len(traces) == 2
        assert all(t.event_type.startswith("llm_call") for t in traces)

    def test_group_by_turn(self) -> None:
        """Test grouping traces by turn index."""
        events = [
            {"event": "llm_call_start", "data": {"context_tokens_before": 5000, "iteration": 0}},
            {"event": "llm_call_end", "data": {"context_tokens_after": 5800, "iteration": 0}},
            {"event": "llm_call_start", "data": {"context_tokens_before": 5800, "iteration": 1}},
            {"event": "llm_call_end", "data": {"context_tokens_after": 6200, "iteration": 1}},
        ]

        analyzer = ContextOSTraceAnalyzer(events)
        turns = analyzer.get_turn_traces()

        assert len(turns) == 2
        assert len(turns[0]) == 2
        assert len(turns[1]) == 2

    def test_calculate_token_change(self) -> None:
        """Test token change calculation."""
        event1 = ContextOSTraceEvent(
            event_type="llm_call_start",
            context_tokens_before=5000,
            context_tokens_after=None,
            compression_strategy=None,
            compression_applied=None,
            prompt_tokens=5000,
            completion_tokens=None,
            turn_index=0,
        )
        event2 = ContextOSTraceEvent(
            event_type="llm_call_end",
            context_tokens_before=None,
            context_tokens_after=5800,
            compression_strategy=None,
            compression_applied=None,
            prompt_tokens=None,
            completion_tokens=200,
            turn_index=0,
        )

        analyzer = ContextOSTraceAnalyzer([])
        change = analyzer.calculate_token_change(event1, event2)
        assert change == 800  # 5800 - 5000


class TestContextOSLossValidator:
    """Tests for ContextOSLossValidator - P0 Critical."""

    def test_null_context_tokens_before_fails(self) -> None:
        """Test null context_tokens_before is a CRITICAL failure."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={
                "event_traces": [
                    ContextOSTraceEvent(
                        event_type="llm_call_start",
                        context_tokens_before=None,  # NULL - ALWAYS FAILS
                        context_tokens_after=5000,
                        compression_strategy=None,
                        compression_applied=None,
                        prompt_tokens=5000,
                        completion_tokens=None,
                        turn_index=0,
                    ),
                ]
            },
        )

        validator = ContextOSLossValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is False
        assert "context_tokens_before is null" in msg
        assert "CRITICAL" in msg
        assert "index=0(turn=0)" in msg

    def test_valid_traces_pass(self) -> None:
        """Test valid traces with proper token tracking pass."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={
                "event_traces": [
                    ContextOSTraceEvent(
                        event_type="llm_call_start",
                        context_tokens_before=5000,
                        context_tokens_after=5200,
                        compression_strategy=None,
                        compression_applied=False,
                        prompt_tokens=5000,
                        completion_tokens=200,
                        turn_index=0,
                    ),
                ]
            },
        )

        validator = ContextOSLossValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "no null tokens found" in msg

    def test_insufficient_traces(self) -> None:
        """Test runs with < 2 traces pass (insufficient data)."""
        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={
                "event_traces": [
                    ContextOSTraceEvent(
                        event_type="llm_call_start",
                        context_tokens_before=5000,
                        context_tokens_after=5200,
                        compression_strategy=None,
                        compression_applied=False,
                        prompt_tokens=5000,
                        completion_tokens=200,
                        turn_index=0,
                    ),
                ]
            },
        )

        validator = ContextOSLossValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "insufficient" in msg.lower()


class TestContextOSLongSessionValidator:
    """Tests for ContextOSLongSessionValidator - P0 Critical."""

    def test_long_session_with_excessive_growth_fails(self) -> None:
        """Test long session with >30% growth CRITICAL fails."""
        # Simulate 60 turns with 80% growth
        # Each turn needs 2 traces (llm_call_start + llm_call_end)
        traces = []
        for i in range(60):
            # llm_call_start for turn i
            traces.append(
                ContextOSTraceEvent(
                    event_type="llm_call_start",
                    context_tokens_before=5000 + (i * 1000),  # Growing
                    context_tokens_after=None,
                    compression_strategy=None,
                    compression_applied=False,
                    prompt_tokens=5000,
                    completion_tokens=200,
                    turn_index=i,
                )
            )
            # llm_call_end for turn i
            traces.append(
                ContextOSTraceEvent(
                    event_type="llm_call_end",
                    context_tokens_before=None,
                    context_tokens_after=5000 + (i * 1000) + 200,  # start + completion
                    compression_strategy=None,
                    compression_applied=False,
                    prompt_tokens=None,
                    completion_tokens=200,
                    turn_index=i,
                )
            )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSLongSessionValidator(max_turns=50, max_growth_threshold=0.3)
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is False
        assert "context grew by" in msg
        assert "ContextOS" in msg

    def test_short_session_passes(self) -> None:
        """Test sessions with <50 turns pass (insufficient data)."""
        traces = []
        for i in range(30):  # Only 30 turns = 60 traces
            traces.append(
                ContextOSTraceEvent(
                    event_type="llm_call_start",
                    context_tokens_before=5000,
                    context_tokens_after=5200,
                    compression_strategy=None,
                    compression_applied=False,
                    prompt_tokens=5000,
                    completion_tokens=200,
                    turn_index=i,
                )
            )
            traces.append(
                ContextOSTraceEvent(
                    event_type="llm_call_end",
                    context_tokens_before=None,
                    context_tokens_after=5200,
                    compression_strategy=None,
                    compression_applied=False,
                    prompt_tokens=None,
                    completion_tokens=200,
                    turn_index=i,
                )
            )

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSLongSessionValidator(max_turns=50, max_growth_threshold=0.3)
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "session too short" in msg.lower()
        assert "30" in msg  # Should show turn count

    def test_no_start_tokens(self) -> None:
        """Test missing start tokens returns pass when session is too short."""
        # Only 1 trace - gets caught by "session too short" check first
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=None,
                context_tokens_after=5200,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=0,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSLongSessionValidator(max_turns=50, max_growth_threshold=0.3)
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "session too short" in msg.lower()


class TestContextOSDesynchronizationValidator:
    """Tests for ContextOSDesynchronizationValidator - P0 Critical."""

    def test_token_gap_detected_fails(self) -> None:
        """Test token gap >10% CRITICAL fails."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=5300,  # Turn 1 ends at 5300
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=300,
                turn_index=0,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=4000,  # Turn 2 starts at 4000 - 1300 gap!
                context_tokens_after=None,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=4000,
                completion_tokens=None,
                turn_index=1,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=4200,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=1,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSDesynchronizationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is False
        assert "desynchronization" in msg.lower()
        assert "5300" in msg and "4000" in msg

    def test_within_tolerance_passes(self) -> None:
        """Test small gaps (<10%) pass."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=5000,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=0,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=5100,  # Only 100 gap = 2% (<10%)
                context_tokens_after=None,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=5100,
                completion_tokens=None,
                turn_index=1,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=5300,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=1,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSDesynchronizationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "no context desynchronization" in msg

    def test_insufficient_traces(self) -> None:
        """Test < 3 traces pass (insufficient data)."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=5000,
                context_tokens_after=None,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=5000,
                completion_tokens=None,
                turn_index=0,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSDesynchronizationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "insufficient traces" in msg.lower()


class TestContextOSIncorrectTruncationValidator:
    """Tests for ContextOSIncorrectTruncationValidator - P1 High."""

    def test_suspicious_drop_without_compression_fails(self) -> None:
        """Test >30% drop without compression CRITICAL fails."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=6000,
                compression_strategy="none",
                compression_applied=False,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=0,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=4000,  # 2000 drop = 33% (>30%)
                context_tokens_after=None,
                compression_strategy="none",
                compression_applied=False,
                prompt_tokens=4000,
                completion_tokens=None,
                turn_index=1,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSIncorrectTruncationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is False
        assert "unexpected token drop" in msg.lower()
        assert "drop:" in msg.lower() or "33" in msg or "2000" in msg

    def test_drop_with_compression_allowed(self) -> None:
        """Test token drop with compression applied is allowed."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_end",
                context_tokens_before=None,
                context_tokens_after=10000,
                compression_strategy="aggressive",
                compression_applied=True,
                prompt_tokens=None,
                completion_tokens=200,
                turn_index=0,
            ),
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=3000,  # 70% drop but compression was applied in prev turn
                context_tokens_after=None,
                compression_strategy="none",
                compression_applied=False,
                prompt_tokens=3000,
                completion_tokens=None,
                turn_index=1,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSIncorrectTruncationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "no incorrect truncation" in msg.lower()

    def test_insufficient_traces(self) -> None:
        """Test < 2 traces pass (insufficient data)."""
        traces = [
            ContextOSTraceEvent(
                event_type="llm_call_start",
                context_tokens_before=5000,
                context_tokens_after=None,
                compression_strategy=None,
                compression_applied=False,
                prompt_tokens=5000,
                completion_tokens=None,
                turn_index=0,
            ),
        ]

        observed = ObservedBenchmarkRun(
            case_id="test_case",
            role="director",
            workspace="/tmp",
            output="Analysis complete",
            tool_calls=(),
            fingerprint={"event_traces": traces},
        )

        validator = ContextOSIncorrectTruncationValidator()
        ok, msg = validator.validate("Analysis complete", observed, [])

        assert ok is True
        assert "insufficient traces" in msg.lower()
