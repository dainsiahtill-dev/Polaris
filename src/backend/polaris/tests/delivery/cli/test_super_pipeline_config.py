"""Tests for super_pipeline_config module."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.cli.super_pipeline_config import (
    DEFAULT_SUPER_PIPELINE,
    PipelineResult,
    PipelineStage,
    StageConstraint,
    StageResult,
    SuperPipelineConfig,
)


class TestStageConstraint:
    """Tests for StageConstraint dataclass."""

    def test_default_values(self) -> None:
        sc = StageConstraint()
        assert sc.max_exploration_turns == 0
        assert sc.tool_choice == "auto"
        assert sc.forbidden_tools == ()
        assert sc.delivery_mode == "analyze_only"
        assert sc.force_write_on_timeout is False

    def test_custom_values(self) -> None:
        sc = StageConstraint(
            max_exploration_turns=3,
            tool_choice="required",
            forbidden_tools=("tool_a", "tool_b"),
            delivery_mode="materialize_changes",
            force_write_on_timeout=True,
        )
        assert sc.max_exploration_turns == 3
        assert sc.tool_choice == "required"
        assert sc.forbidden_tools == ("tool_a", "tool_b")
        assert sc.delivery_mode == "materialize_changes"
        assert sc.force_write_on_timeout is True

    def test_to_prompt_text_with_forbidden_tools(self) -> None:
        sc = StageConstraint(forbidden_tools=("repo_tree", "glob"))
        text = sc.to_prompt_text()
        assert "Do NOT call repo_tree, glob" in text

    def test_to_prompt_text_with_exploration_limit(self) -> None:
        sc = StageConstraint(max_exploration_turns=2)
        text = sc.to_prompt_text()
        assert "at most 2 time(s)" in text

    def test_to_prompt_text_with_required_tool_choice(self) -> None:
        sc = StageConstraint(tool_choice="required")
        text = sc.to_prompt_text()
        assert "MUST call at least one tool" in text

    def test_to_prompt_text_with_materialize_mode(self) -> None:
        sc = StageConstraint(delivery_mode="materialize_changes")
        text = sc.to_prompt_text()
        assert "EXECUTE code modifications" in text

    def test_to_prompt_text_empty(self) -> None:
        sc = StageConstraint()
        text = sc.to_prompt_text()
        assert text == ""

    def test_to_api_tool_choice(self) -> None:
        sc = StageConstraint(tool_choice="required")
        assert sc.to_api_tool_choice() == "required"


class TestStageResult:
    """Tests for StageResult dataclass."""

    def test_default_values(self) -> None:
        sr = StageResult(role="pm", success=True)
        assert sr.role == "pm"
        assert sr.success is True
        assert sr.content == ""
        assert sr.error is None
        assert sr.retry_count == 0
        assert sr.duration_seconds == 0.0
        assert sr.llm_calls == 0
        assert sr.tool_calls == 0
        assert sr.skipped is False
        assert sr.degraded is False

    def test_failure_result(self) -> None:
        sr = StageResult(role="director", success=False, error="timeout")
        assert sr.success is False
        assert sr.error == "timeout"

    def test_skipped_result(self) -> None:
        sr = StageResult(role="qa", success=False, skipped=True)
        assert sr.skipped is True


class TestPipelineResult:
    """Tests for PipelineResult dataclass."""

    def test_empty_stages(self) -> None:
        pr = PipelineResult(stages=(), final_role="")
        assert pr.completed_roles == ()
        assert pr.failed_roles == ()
        assert pr.stage_for("pm") is None

    def test_completed_roles(self) -> None:
        stages = (
            StageResult(role="architect", success=True),
            StageResult(role="pm", success=True),
            StageResult(role="director", success=False),
        )
        pr = PipelineResult(stages=stages, final_role="director")
        assert pr.completed_roles == ("architect", "pm")
        assert pr.failed_roles == ("director",)

    def test_skipped_not_in_failed(self) -> None:
        stages = (
            StageResult(role="architect", success=False, skipped=True),
            StageResult(role="pm", success=True),
        )
        pr = PipelineResult(stages=stages, final_role="pm")
        assert pr.failed_roles == ()

    def test_stage_for_found(self) -> None:
        stages = (StageResult(role="pm", success=True),)
        pr = PipelineResult(stages=stages, final_role="pm")
        result = pr.stage_for("pm")
        assert result is not None
        assert result.success is True

    def test_stage_for_not_found(self) -> None:
        pr = PipelineResult(stages=(), final_role="")
        assert pr.stage_for("nonexistent") is None

    def test_saw_error(self) -> None:
        pr = PipelineResult(stages=(), final_role="", saw_error=True)
        assert pr.saw_error is True


class TestSuperPipelineConfig:
    """Tests for SuperPipelineConfig dataclass."""

    def test_default_values(self) -> None:
        config = SuperPipelineConfig(stages=())
        assert config.stages == ()
        assert config.max_total_duration_seconds == 1200
        assert config.orchestrator_mode == "session_orchestrator"
        assert config.persist_blueprints is True

    def test_custom_values(self) -> None:
        config = SuperPipelineConfig(
            stages=(),
            max_total_duration_seconds=600,
            orchestrator_mode="stream_chat",
            persist_blueprints=False,
        )
        assert config.max_total_duration_seconds == 600
        assert config.orchestrator_mode == "stream_chat"
        assert config.persist_blueprints is False


class TestDefaultSuperPipeline:
    """Tests for DEFAULT_SUPER_PIPELINE configuration."""

    def test_has_four_stages(self) -> None:
        assert len(DEFAULT_SUPER_PIPELINE.stages) == 4

    def test_stage_roles(self) -> None:
        roles = [s.role for s in DEFAULT_SUPER_PIPELINE.stages]
        assert roles == ["architect", "pm", "chief_engineer", "director"]

    def test_architect_stage(self) -> None:
        stage = DEFAULT_SUPER_PIPELINE.stages[0]
        assert stage.role == "architect"
        assert stage.constraint.max_exploration_turns == 1
        assert stage.max_retries == 1
        assert stage.timeout_seconds == 180

    def test_pm_stage(self) -> None:
        stage = DEFAULT_SUPER_PIPELINE.stages[1]
        assert stage.role == "pm"
        assert stage.constraint.max_exploration_turns == 0
        assert "repo_tree" in stage.constraint.forbidden_tools
        assert stage.max_retries == 2

    def test_director_stage(self) -> None:
        stage = DEFAULT_SUPER_PIPELINE.stages[3]
        assert stage.role == "director"
        assert stage.constraint.tool_choice == "required"
        assert stage.constraint.delivery_mode == "materialize_changes"
        assert stage.constraint.force_write_on_timeout is True
        assert stage.on_failure == "degrade"

    def test_total_duration_default(self) -> None:
        assert DEFAULT_SUPER_PIPELINE.max_total_duration_seconds == 1200

    def test_orchestrator_mode_default(self) -> None:
        assert DEFAULT_SUPER_PIPELINE.orchestrator_mode == "session_orchestrator"

    def test_persist_blueprints_default(self) -> None:
        assert DEFAULT_SUPER_PIPELINE.persist_blueprints is True

    def test_stages_are_frozen(self) -> None:
        with pytest.raises(AttributeError):
            DEFAULT_SUPER_PIPELINE.stages = ()


class TestPipelineStage:
    """Tests for PipelineStage dataclass."""

    def test_stage_creation(self) -> None:
        def dummy_handoff(**kw: Any) -> str:
            return "test"

        stage = PipelineStage(
            role="test",
            handoff_builder=dummy_handoff,
            max_retries=3,
            timeout_seconds=100,
        )
        assert stage.role == "test"
        assert stage.max_retries == 3
        assert stage.timeout_seconds == 100
        assert stage.on_failure == "retry"
        assert stage.skip_condition is None

    def test_stage_with_skip_condition(self) -> None:
        def always_skip(ctx: Any) -> bool:
            return True

        stage = PipelineStage(
            role="skipper",
            handoff_builder=lambda **kw: "",
            skip_condition=always_skip,
        )
        assert stage.skip_condition is not None
        assert stage.skip_condition(None) is True

    def test_stage_with_constraint(self) -> None:
        constraint = StageConstraint(max_exploration_turns=5)
        stage = PipelineStage(
            role="constrained",
            handoff_builder=lambda **kw: "",
            constraint=constraint,
        )
        assert stage.constraint.max_exploration_turns == 5
