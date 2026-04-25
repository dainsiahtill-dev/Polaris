"""Unit tests for polaris.cells.llm.evaluation.internal.benchmark_models."""

from __future__ import annotations

import pytest

from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    AgenticJudgeVerdict,
    JudgeCheck,
    ObservedBenchmarkRun,
    ToolArgumentRule,
    ToolCallObservation,
)


class TestToolArgumentRule:
    """Tests for ToolArgumentRule dataclass."""

    def test_valid_rule(self) -> None:
        rule = ToolArgumentRule(fragment="search", tools=("repo_rg",))
        assert rule.fragment == "search"
        assert rule.tools == ("repo_rg",)
        assert rule.description == ""

    def test_empty_fragment(self) -> None:
        with pytest.raises(ValueError, match="fragment must be a non-empty string"):
            ToolArgumentRule(fragment="", tools=())

    def test_to_dict(self) -> None:
        rule = ToolArgumentRule(fragment="search", tools=("repo_rg",), description="Find text")
        assert rule.to_dict() == {
            "fragment": "search",
            "tools": ["repo_rg"],
            "description": "Find text",
        }

    def test_from_dict(self) -> None:
        rule = ToolArgumentRule.from_dict({"fragment": "search", "tools": ["repo_rg"]})
        assert rule.fragment == "search"
        assert rule.tools == ("repo_rg",)


class TestAgenticJudgeConfig:
    """Tests for AgenticJudgeConfig dataclass."""

    def test_valid_config(self) -> None:
        config = AgenticJudgeConfig(score_threshold=0.8, min_tool_calls=1)
        assert config.score_threshold == 0.8
        assert config.min_tool_calls == 1
        assert config.max_tool_calls is None

    def test_invalid_threshold_low(self) -> None:
        with pytest.raises(ValueError, match="score_threshold must be between"):
            AgenticJudgeConfig(score_threshold=-0.1)

    def test_invalid_threshold_high(self) -> None:
        with pytest.raises(ValueError, match="score_threshold must be between"):
            AgenticJudgeConfig(score_threshold=1.1)

    def test_invalid_min_tool_calls(self) -> None:
        with pytest.raises(ValueError, match="min_tool_calls must be >= 0"):
            AgenticJudgeConfig(min_tool_calls=-1)

    def test_invalid_max_tool_calls(self) -> None:
        with pytest.raises(ValueError, match="max_tool_calls must be >= min_tool_calls"):
            AgenticJudgeConfig(min_tool_calls=5, max_tool_calls=3)

    def test_tool_argument_normalization(self) -> None:
        config = AgenticJudgeConfig(
            required_tool_arguments=[{"fragment": "search", "tools": ["repo_rg"]}],
        )
        assert len(config.required_tool_arguments) == 1
        assert isinstance(config.required_tool_arguments[0], ToolArgumentRule)

    def test_to_dict(self) -> None:
        config = AgenticJudgeConfig(score_threshold=0.8)
        d = config.to_dict()
        assert d["score_threshold"] == 0.8
        assert d["min_tool_calls"] == 0

    def test_from_dict(self) -> None:
        config = AgenticJudgeConfig.from_dict({"score_threshold": 0.9, "min_tool_calls": 2})
        assert config.score_threshold == 0.9
        assert config.min_tool_calls == 2


class TestAgenticBenchmarkCase:
    """Tests for AgenticBenchmarkCase dataclass."""

    def test_valid_case(self) -> None:
        case = AgenticBenchmarkCase(
            case_id="c1",
            role="pm",
            title="Test case",
            prompt="Do something",
        )
        assert case.case_id == "c1"
        assert case.role == "pm"
        assert case.title == "Test case"
        assert case.description == ""
        assert case.workspace_fixture == ""
        assert case.history == ()
        assert case.tags == ()

    def test_empty_case_id(self) -> None:
        with pytest.raises(ValueError, match="case_id, role, title, and prompt must be non-empty"):
            AgenticBenchmarkCase(case_id="", role="pm", title="Test", prompt="Do")

    def test_empty_role(self) -> None:
        with pytest.raises(ValueError, match="case_id, role, title, and prompt must be non-empty"):
            AgenticBenchmarkCase(case_id="c1", role="", title="Test", prompt="Do")

    def test_role_lowercase(self) -> None:
        case = AgenticBenchmarkCase(
            case_id="c1",
            role="PM",
            title="Test",
            prompt="Do",
        )
        assert case.role == "pm"

    def test_history_normalization(self) -> None:
        case = AgenticBenchmarkCase(
            case_id="c1",
            role="pm",
            title="Test",
            prompt="Do",
            history=[{"role": "user", "content": "hello"}],
        )
        assert case.history == (("user", "hello"),)

    def test_history_tuple_normalization(self) -> None:
        case = AgenticBenchmarkCase(
            case_id="c1",
            role="pm",
            title="Test",
            prompt="Do",
            history=[["user", "hello"]],
        )
        assert case.history == (("user", "hello"),)

    def test_to_dict(self) -> None:
        case = AgenticBenchmarkCase(
            case_id="c1",
            role="pm",
            title="Test",
            prompt="Do",
        )
        d = case.to_dict()
        assert d["case_id"] == "c1"
        assert d["role"] == "pm"
        assert "judge" in d

    def test_from_dict(self) -> None:
        case = AgenticBenchmarkCase.from_dict({
            "case_id": "c1",
            "role": "pm",
            "title": "Test",
            "prompt": "Do",
        })
        assert case.case_id == "c1"


class TestToolCallObservation:
    """Tests for ToolCallObservation dataclass."""

    def test_valid_observation(self) -> None:
        obs = ToolCallObservation(tool="read_file", args={"path": "/tmp"})
        assert obs.tool == "read_file"
        assert obs.args == {"path": "/tmp"}
        assert obs.event_index == 0

    def test_empty_tool(self) -> None:
        with pytest.raises(ValueError, match="tool must be a non-empty string"):
            ToolCallObservation(tool="", args={})

    def test_to_dict(self) -> None:
        obs = ToolCallObservation(tool="read_file", args={"path": "/tmp"}, event_index=5)
        assert obs.to_dict() == {"tool": "read_file", "args": {"path": "/tmp"}, "event_index": 5}


class TestObservedBenchmarkRun:
    """Tests for ObservedBenchmarkRun dataclass."""

    def test_valid_run(self) -> None:
        run = ObservedBenchmarkRun(
            case_id="c1",
            role="pm",
            workspace="/tmp",
            output="hello",
        )
        assert run.case_id == "c1"
        assert run.role == "pm"
        assert run.output == "hello"
        assert run.thinking == ""
        assert run.tool_calls == ()
        assert run.error == ""
        assert run.duration_ms == 0

    def test_role_lowercase(self) -> None:
        run = ObservedBenchmarkRun(
            case_id="c1",
            role="PM",
            workspace="/tmp",
            output="hello",
        )
        assert run.role == "pm"

    def test_tool_calls_normalization(self) -> None:
        run = ObservedBenchmarkRun(
            case_id="c1",
            role="pm",
            workspace="/tmp",
            output="hello",
            tool_calls=[{"tool": "read_file", "args": {}}],
        )
        assert len(run.tool_calls) == 1
        assert isinstance(run.tool_calls[0], ToolCallObservation)

    def test_to_dict(self) -> None:
        run = ObservedBenchmarkRun(
            case_id="c1",
            role="pm",
            workspace="/tmp",
            output="hello",
        )
        d = run.to_dict()
        assert d["case_id"] == "c1"
        assert d["output"] == "hello"


class TestJudgeCheck:
    """Tests for JudgeCheck dataclass."""

    def test_valid_check(self) -> None:
        check = JudgeCheck(
            code="tool_pass",
            category="tooling",
            passed=True,
            message="Tool found",
        )
        assert check.code == "tool_pass"
        assert check.category == "tooling"
        assert check.passed is True
        assert check.critical is False
        assert check.evidence == {}

    def test_to_dict(self) -> None:
        check = JudgeCheck(
            code="tool_pass",
            category="tooling",
            passed=True,
            message="Tool found",
            critical=True,
            evidence={"tool": "read_file"},
        )
        d = check.to_dict()
        assert d["code"] == "tool_pass"
        assert d["critical"] is True
        assert d["evidence"] == {"tool": "read_file"}


class TestAgenticJudgeVerdict:
    """Tests for AgenticJudgeVerdict dataclass."""

    def test_valid_verdict(self) -> None:
        verdict = AgenticJudgeVerdict(
            case_id="c1",
            passed=True,
            score=0.85,
            threshold=0.75,
        )
        assert verdict.case_id == "c1"
        assert verdict.passed is True
        assert verdict.score == 0.85
        assert verdict.threshold == 0.75
        assert verdict.summary == ""
        assert verdict.checks == ()

    def test_checks_normalization(self) -> None:
        verdict = AgenticJudgeVerdict(
            case_id="c1",
            passed=True,
            score=0.85,
            threshold=0.75,
            checks=[{"code": "c1", "category": "tooling", "passed": True, "message": "ok"}],
        )
        assert len(verdict.checks) == 1
        assert isinstance(verdict.checks[0], JudgeCheck)

    def test_to_dict(self) -> None:
        verdict = AgenticJudgeVerdict(
            case_id="c1",
            passed=True,
            score=0.85,
            threshold=0.75,
        )
        d = verdict.to_dict()
        assert d["case_id"] == "c1"
        assert d["passed"] is True
        assert d["score"] == 0.85
