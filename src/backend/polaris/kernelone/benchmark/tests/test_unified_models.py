"""Tests for unified_models.py.

These tests verify the core data models of the unified benchmark framework.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.benchmark.unified_models import (
    SCORE_WEIGHTS,
    BudgetConditions,
    JudgeCheck,
    JudgeConfig,
    ObservedBenchmarkRun,
    ToolArgumentRule,
    ToolCallObservation,
    UnifiedBenchmarkCase,
    UnifiedJudgeVerdict,
)


class TestBudgetConditions:
    """Tests for BudgetConditions dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        bc = BudgetConditions()
        assert bc.max_tokens == 200_000
        assert bc.max_turns == 10
        assert bc.max_wall_time_seconds == 300.0

    def test_custom_values(self) -> None:
        """Test custom values."""
        bc = BudgetConditions(
            max_tokens=100_000,
            max_turns=5,
            max_wall_time_seconds=60.0,
        )
        assert bc.max_tokens == 100_000
        assert bc.max_turns == 5
        assert bc.max_wall_time_seconds == 60.0

    def test_invalid_max_tokens(self) -> None:
        """Test invalid max_tokens raises ValueError."""
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            BudgetConditions(max_tokens=0)

    def test_to_dict(self) -> None:
        """Test serialization to dict."""
        bc = BudgetConditions(max_tokens=50_000, max_turns=3, max_wall_time_seconds=30.0)
        d = bc.to_dict()
        assert d["max_tokens"] == 50_000
        assert d["max_turns"] == 3
        assert d["max_wall_time_seconds"] == 30.0

    def test_from_dict(self) -> None:
        """Test deserialization from dict."""
        d = {"max_tokens": 75_000, "max_turns": 8, "max_wall_time_seconds": 120.0}
        bc = BudgetConditions.from_dict(d)
        assert bc.max_tokens == 75_000
        assert bc.max_turns == 8
        assert bc.max_wall_time_seconds == 120.0


class TestToolArgumentRule:
    """Tests for ToolArgumentRule dataclass."""

    def test_basic_rule(self) -> None:
        """Test basic rule creation."""
        rule = ToolArgumentRule(fragment="docs/graph")
        assert rule.fragment == "docs/graph"
        assert rule.tools == ()
        assert rule.description == ""

    def test_rule_with_tools(self) -> None:
        """Test rule with tool scope."""
        rule = ToolArgumentRule(
            fragment="cells.yaml",
            tools=("read_file", "search_code"),
            description="must read cells.yaml",
        )
        assert rule.fragment == "cells.yaml"
        assert rule.tools == ("read_file", "search_code")
        assert rule.description == "must read cells.yaml"

    def test_empty_fragment_raises(self) -> None:
        """Test empty fragment raises ValueError."""
        with pytest.raises(ValueError, match="fragment must be non-empty"):
            ToolArgumentRule(fragment="")

    def test_to_dict(self) -> None:
        """Test serialization."""
        rule = ToolArgumentRule(
            fragment="test.py",
            tools=("read_file",),
            description="test file",
        )
        d = rule.to_dict()
        assert d["fragment"] == "test.py"
        assert d["tools"] == ["read_file"]
        assert d["description"] == "test file"

    def test_from_dict(self) -> None:
        """Test deserialization."""
        d = {"fragment": "config.json", "tools": ["read_file"], "description": "config"}
        rule = ToolArgumentRule.from_dict(d)
        assert rule.fragment == "config.json"
        assert rule.tools == ("read_file",)


class TestJudgeConfig:
    """Tests for JudgeConfig dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        cfg = JudgeConfig()
        assert cfg.score_threshold == 0.75
        assert cfg.required_tools == ()
        assert cfg.forbidden_tools == ()
        assert cfg.min_tool_calls == 0
        assert cfg.max_tool_calls is None
        assert cfg.mode == "agentic"

    def test_threshold_bounds(self) -> None:
        """Test score_threshold bounds."""
        with pytest.raises(ValueError, match="score_threshold must be between"):
            JudgeConfig(score_threshold=1.5)
        with pytest.raises(ValueError, match="score_threshold must be between"):
            JudgeConfig(score_threshold=-0.1)

    def test_tool_count_validation(self) -> None:
        """Test min/max tool call validation."""
        with pytest.raises(ValueError, match="min_tool_calls must be >= 0"):
            JudgeConfig(min_tool_calls=-1)

        with pytest.raises(ValueError, match="max_tool_calls must be >= min_tool_calls"):
            JudgeConfig(min_tool_calls=5, max_tool_calls=3)

    def test_mode_validation(self) -> None:
        """Test benchmark mode validation."""
        cfg = JudgeConfig(mode="strategy")
        assert cfg.mode == "strategy"

        with pytest.raises(ValueError, match="mode must be one of"):
            JudgeConfig(mode="invalid")  # Intentionally testing invalid mode

    def test_from_dict_with_nested_objects(self) -> None:
        """Test from_dict with nested objects."""
        d = {
            "score_threshold": 0.8,
            "required_tools": ["read_file", "search_code"],
            "required_tool_arguments": [{"fragment": "test", "tools": ["read_file"]}],
        }
        cfg = JudgeConfig.from_dict(d)
        assert cfg.score_threshold == 0.8
        assert cfg.required_tools == ("read_file", "search_code")
        assert len(cfg.required_tool_arguments) == 1
        assert cfg.required_tool_arguments[0].fragment == "test"


class TestUnifiedBenchmarkCase:
    """Tests for UnifiedBenchmarkCase dataclass."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        case = UnifiedBenchmarkCase(
            case_id="test_001",
            role="director",
            title="Test Case",
            prompt="Find the bug",
        )
        assert case.case_id == "test_001"
        assert case.role == "director"
        assert case.title == "Test Case"
        assert case.prompt == "Find the bug"

    def test_role_normalized_to_lowercase(self) -> None:
        """Test role is normalized to lowercase."""
        case = UnifiedBenchmarkCase(
            case_id="test",
            role="DIRECTOR",
            title="Test",
            prompt="prompt",
        )
        assert case.role == "director"

    def test_missing_required_field_raises(self) -> None:
        """Test missing required field raises ValueError."""
        with pytest.raises(ValueError, match="case_id must be non-empty"):
            UnifiedBenchmarkCase(
                case_id="",
                role="director",
                title="Test",
                prompt="prompt",
            )

    def test_history_normalization(self) -> None:
        """Test history is normalized to tuples."""
        case = UnifiedBenchmarkCase(
            case_id="test",
            role="director",
            title="Test",
            prompt="prompt",
            history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],  # Testing normalization from dict format
        )
        assert case.history == (("user", "hello"), ("assistant", "hi"))

    def test_to_dict_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        case = UnifiedBenchmarkCase(
            case_id="test_roundtrip",
            role="qa",
            title="Roundtrip Test",
            prompt="Check this",
            judge=JudgeConfig(
                required_tools=("read_file",),
                score_threshold=0.8,
            ),
        )
        d = case.to_dict()
        restored = UnifiedBenchmarkCase.from_dict(d)
        assert restored.case_id == case.case_id
        assert restored.role == case.role
        assert restored.judge.required_tools == case.judge.required_tools


class TestToolCallObservation:
    """Tests for ToolCallObservation dataclass."""

    def test_basic_observation(self) -> None:
        """Test basic observation."""
        obs = ToolCallObservation(tool="read_file", args={"path": "test.py"})
        assert obs.tool == "read_file"
        assert obs.args == {"path": "test.py"}
        assert obs.event_index == 0

    def test_empty_tool_raises(self) -> None:
        """Test empty tool name raises ValueError."""
        with pytest.raises(ValueError, match="tool must be non-empty"):
            ToolCallObservation(tool="")

    def test_negative_event_index_corrected(self) -> None:
        """Test negative event_index is corrected to 0."""
        obs = ToolCallObservation(tool="read_file", event_index=-5)
        assert obs.event_index == 0


class TestObservedBenchmarkRun:
    """Tests for ObservedBenchmarkRun dataclass."""

    def test_basic_run(self) -> None:
        """Test basic run observation."""
        run = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/tmp",
            output="Found bug",
            tool_calls=(ToolCallObservation(tool="search_code", args={"query": "bug"}),),
        )
        assert run.case_id == "test"
        assert run.output == "Found bug"
        assert len(run.tool_calls) == 1

    def test_error_tracking(self) -> None:
        """Test error is tracked."""
        run = ObservedBenchmarkRun(
            case_id="test",
            role="director",
            workspace="/tmp",
            output="",
            error="connection failed",
        )
        assert run.error == "connection failed"


class TestJudgeCheck:
    """Tests for JudgeCheck dataclass."""

    def test_basic_check(self) -> None:
        """Test basic check."""
        check = JudgeCheck(
            code="required_tool:read_file",
            category="tooling",
            passed=True,
            message="tool found",
        )
        assert check.code == "required_tool:read_file"
        assert check.category == "tooling"
        assert check.passed is True
        assert check.critical is False

    def test_critical_check(self) -> None:
        """Test critical check."""
        check = JudgeCheck(
            code="forbidden_tool:write_file",
            category="safety",
            passed=False,
            message="forbidden tool used",
            critical=True,
        )
        assert check.critical is True


class TestUnifiedJudgeVerdict:
    """Tests for UnifiedJudgeVerdict dataclass."""

    def test_pass_verdict(self) -> None:
        """Test passing verdict."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=True,
            score=0.85,
            threshold=0.75,
            categories={"tooling": 1.0, "safety": 0.8},
            summary="all checks passed",
        )
        assert verdict.passed is True
        assert verdict.score == 0.85
        assert verdict.threshold == 0.75

    def test_fail_verdict(self) -> None:
        """Test failing verdict."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=False,
            score=0.5,
            threshold=0.75,
            summary="failed checks: required_tool:search_code",
            checks=(
                JudgeCheck(
                    code="required_tool:search_code",
                    category="tooling",
                    passed=False,
                    message="required tool missing",
                ),
            ),
        )
        assert verdict.passed is False

    def test_critical_failures_property(self) -> None:
        """Test critical_failures property."""
        verdict = UnifiedJudgeVerdict(
            case_id="test",
            passed=False,
            score=0.5,
            threshold=0.75,
            checks=(
                JudgeCheck(
                    code="forbidden_tool:delete",
                    category="safety",
                    passed=False,
                    message="forbidden tool used",
                    critical=True,
                ),
                JudgeCheck(
                    code="required_tool:read",
                    category="tooling",
                    passed=False,
                    message="required tool missing",
                    critical=False,
                ),
            ),
        )
        critical = verdict.critical_failures
        assert len(critical) == 1
        assert critical[0].code == "forbidden_tool:delete"


class TestScoreWeights:
    """Tests for SCORE_WEIGHTS constant."""

    def test_weights_sum_to_one(self) -> None:
        """Test weights sum to 1.0."""
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_all_categories_positive(self) -> None:
        """Test all weights are positive."""
        for name, weight in SCORE_WEIGHTS.items():
            assert weight > 0, f"weight for {name} must be positive"
