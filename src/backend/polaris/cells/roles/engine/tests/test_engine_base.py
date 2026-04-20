"""Tests for internal/base.py — EngineBudget, EngineContext, BaseEngine."""

from __future__ import annotations

import pytest
from polaris.cells.roles.engine.internal.base import (
    BaseEngine,
    EngineBudget,
    EngineContext,
    EngineResult,
    EngineStatus,
    EngineStrategy,
    StepResult,
    create_engine_budget,
)

# ---------------------------------------------------------------------------
# EngineStatus enum
# ---------------------------------------------------------------------------


def test_engine_status_values() -> None:
    assert EngineStatus.IDLE.value == "idle"
    assert EngineStatus.RUNNING.value == "running"
    assert EngineStatus.PAUSED.value == "paused"
    assert EngineStatus.COMPLETED.value == "completed"
    assert EngineStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# EngineStrategy enum
# ---------------------------------------------------------------------------


def test_engine_strategy_values() -> None:
    assert EngineStrategy.REACT.value == "react"
    assert EngineStrategy.PLAN_SOLVE.value == "plan_solve"
    assert EngineStrategy.TOT.value == "tot"
    assert EngineStrategy.SEQUENTIAL.value == "sequential"


# ---------------------------------------------------------------------------
# EngineBudget
# ---------------------------------------------------------------------------


def test_engine_budget_defaults() -> None:
    budget = EngineBudget()
    assert budget.max_steps == 12
    assert budget.max_tool_calls_total == 24
    assert budget.max_no_progress_steps == 3
    assert budget.max_wall_time_seconds == 120
    assert budget.max_same_error_fingerprint == 2
    assert budget.progress_info_incremental is False


def test_engine_budget_from_dict() -> None:
    data = {
        "max_steps": 20,
        "max_tool_calls_total": 50,
        "max_no_progress_steps": 5,
        "max_wall_time_seconds": 300,
        "max_same_error_fingerprint": 4,
        "progress_info_incremental": True,
    }
    budget = EngineBudget.from_dict(data)
    assert budget.max_steps == 20
    assert budget.max_tool_calls_total == 50
    assert budget.max_no_progress_steps == 5
    assert budget.max_wall_time_seconds == 300
    assert budget.max_same_error_fingerprint == 4
    assert budget.progress_info_incremental is True


def test_engine_budget_from_dict_partial() -> None:
    """Partial dict should fill in defaults for missing keys."""
    budget = EngineBudget.from_dict({"max_steps": 15})
    assert budget.max_steps == 15
    assert budget.max_tool_calls_total == 24  # default


def test_engine_budget_from_dict_rejects_wrong_type() -> None:
    with pytest.raises(TypeError, match="must be int"):
        EngineBudget.from_dict({"max_steps": "ten"})
    with pytest.raises(TypeError, match="must be int"):
        EngineBudget.from_dict({"max_tool_calls_total": None})


def test_engine_budget_from_dict_rejects_negative() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        EngineBudget.from_dict({"max_steps": 0})
    with pytest.raises(ValueError, match="must be >= 1"):
        EngineBudget.from_dict({"max_wall_time_seconds": 0})


def test_engine_budget_from_dict_rejects_wrong_bool() -> None:
    with pytest.raises(TypeError, match="must be bool"):
        EngineBudget.from_dict({"progress_info_incremental": "yes"})


def test_engine_budget_to_dict() -> None:
    budget = EngineBudget(max_steps=7)
    d = budget.to_dict()
    assert d["max_steps"] == 7
    assert "max_tool_calls_total" in d
    assert "max_no_progress_steps" in d
    assert "max_wall_time_seconds" in d
    assert "max_same_error_fingerprint" in d
    assert "progress_info_incremental" in d


def test_create_engine_budget_convenience() -> None:
    budget = create_engine_budget(max_steps=20, max_wall_time_seconds=60)
    assert budget.max_steps == 20
    assert budget.max_wall_time_seconds == 60


# ---------------------------------------------------------------------------
# EngineContext
# ---------------------------------------------------------------------------


def test_engine_context_requires_workspace_role_task() -> None:
    """All three required fields must be provided."""
    ctx = EngineContext(workspace="/tmp", role="pm", task="implement login")
    assert ctx.workspace == "/tmp"
    assert ctx.role == "pm"
    assert ctx.task == "implement login"


def test_engine_context_state_defaults_to_empty_dict() -> None:
    ctx = EngineContext(workspace="/tmp", role="pm", task="test")
    assert ctx.state == {}
    assert ctx.profile is None
    assert ctx.tool_gateway is None
    assert ctx.llm_caller is None


def test_engine_context_get_set() -> None:
    ctx = EngineContext(workspace="/tmp", role="pm", task="test")
    ctx.set("key1", "value1")
    assert ctx.get("key1") == "value1"
    assert ctx.get("nonexistent") is None
    assert ctx.get("nonexistent", "default") == "default"


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


def test_step_result_to_dict() -> None:
    result = StepResult(
        step_index=3,
        status=EngineStatus.RUNNING,
        thought="thinking",
        action="search_code",
        action_input={"query": "login"},
        observation="found 5 matches",
        progress_detected=True,
    )
    d = result.to_dict()
    assert d["step_index"] == 3
    assert d["status"] == "running"
    assert d["thought"] == "thinking"
    assert d["action"] == "search_code"
    assert d["action_input"] == {"query": "login"}
    assert d["observation"] == "found 5 matches"
    assert d["progress_detected"] is True
    assert d["tool_result"] is None
    assert d["error"] is None


def test_step_result_defaults() -> None:
    result = StepResult(step_index=0, status=EngineStatus.IDLE)
    assert result.thought == ""
    assert result.action == ""
    assert result.action_input == {}
    assert result.observation == ""


# ---------------------------------------------------------------------------
# EngineResult
# ---------------------------------------------------------------------------


def test_engine_result_requires_strategy() -> None:
    """EngineResult.strategy is a required field."""
    result = EngineResult(
        success=True,
        final_answer="All done",
        strategy=EngineStrategy.REACT,
        total_steps=5,
        total_tool_calls=3,
        execution_time_seconds=2.5,
        termination_reason="task_completed",
    )
    assert result.strategy == EngineStrategy.REACT


def test_engine_result_to_dict() -> None:
    result = EngineResult(
        success=True,
        final_answer="All done",
        strategy=EngineStrategy.REACT,
        total_steps=5,
        total_tool_calls=3,
        execution_time_seconds=2.5,
        termination_reason="task_completed",
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["final_answer"] == "All done"
    assert d["strategy"] == "react"
    assert d["total_steps"] == 5
    assert d["termination_reason"] == "task_completed"


def test_engine_result_failure() -> None:
    result = EngineResult(
        success=False,
        final_answer="",
        strategy=EngineStrategy.REACT,
        error="Model timeout",
    )
    assert result.success is False
    assert result.error == "Model timeout"


# ---------------------------------------------------------------------------
# BaseEngine
# ---------------------------------------------------------------------------


class _DummyEngine(BaseEngine):
    """Minimal concrete subclass for testing."""

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.REACT

    async def execute(self, context: EngineContext, initial_message: str = "") -> EngineResult:
        return self._create_result(success=True, final_answer="done", termination_reason="task_completed")

    async def step(self, context: EngineContext) -> StepResult:
        return StepResult(step_index=self._current_step, status=EngineStatus.RUNNING)

    def can_continue(self) -> bool:
        return self._current_step < 2


def test_base_engine_default_budget() -> None:
    engine = _DummyEngine(workspace="/tmp")
    assert engine.budget is not None
    assert engine.budget.max_steps == 12


def test_base_engine_custom_budget() -> None:
    budget = EngineBudget(max_steps=5)
    engine = _DummyEngine(workspace="/tmp", budget=budget)
    assert engine.budget.max_steps == 5


def test_base_engine_status_starts_idle() -> None:
    engine = _DummyEngine(workspace="/tmp")
    assert engine.status == EngineStatus.IDLE


def test_base_engine_reset() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._current_step = 3
    engine._status = EngineStatus.RUNNING
    engine._start_time = 1000.0

    engine.reset()

    assert engine._current_step == 0
    assert engine._status == EngineStatus.IDLE
    assert engine._start_time is None
    assert engine._steps == []
    assert engine._tool_calls == []
    assert engine._no_progress_count == 0
    assert engine._consecutive_error_count == 0


def test_base_engine_check_budget_allows_few_errors() -> None:
    """_check_budget returns True when consecutive errors < threshold."""
    engine = _DummyEngine(workspace="/tmp")
    engine._consecutive_error_count = 1
    # Default max_same_error_fingerprint = 2; 1 < 2 → True
    assert engine._check_budget() is True


def test_base_engine_check_budget_blocks_many_errors() -> None:
    """_check_budget returns False when consecutive errors >= threshold."""
    engine = _DummyEngine(workspace="/tmp")
    engine._consecutive_error_count = 2
    # Default max_same_error_fingerprint = 2; 2 >= 2 → False
    assert engine._check_budget() is False


def test_base_engine_check_budget_blocks_at_max_steps() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._current_step = engine.budget.max_steps
    assert engine._check_budget() is False


def test_base_engine_check_budget_blocks_at_max_tool_calls() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._tool_calls = [{"tool": "a"}] * engine.budget.max_tool_calls_total
    assert engine._check_budget() is False


def test_base_engine_check_budget_blocks_at_max_no_progress() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._no_progress_count = engine.budget.max_no_progress_steps
    assert engine._check_budget() is False


def test_base_engine_update_progress_resets_no_progress_on_success() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._no_progress_count = 2
    engine._update_progress(progress_detected=True)
    assert engine._no_progress_count == 0


def test_base_engine_update_progress_increments_no_progress_on_failure() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._no_progress_count = 0
    engine._update_progress(progress_detected=False)
    assert engine._no_progress_count == 1


def test_base_engine_update_progress_tracks_error_fingerprint() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._consecutive_error_count = 0
    engine._update_progress(progress_detected=False, error_fingerprint="err_fp_1")
    assert engine._consecutive_error_count == 1
    assert engine._last_progress_hash == "err_fp_1"


def test_base_engine_update_progress_same_fingerprint_increments() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._last_progress_hash = "err_fp_1"
    engine._consecutive_error_count = 1
    engine._update_progress(progress_detected=False, error_fingerprint="err_fp_1")
    assert engine._consecutive_error_count == 2


def test_base_engine_update_progress_different_fingerprint_resets() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._last_progress_hash = "err_fp_1"
    engine._consecutive_error_count = 2
    engine._update_progress(progress_detected=False, error_fingerprint="err_fp_2")
    assert engine._consecutive_error_count == 1


def test_base_engine_create_result_success() -> None:
    engine = _DummyEngine(workspace="/tmp")
    result = engine._create_result(
        success=True,
        final_answer="the answer",
        termination_reason="task_completed",
    )
    assert result.success is True
    assert result.final_answer == "the answer"
    assert result.termination_reason == "task_completed"
    assert result.strategy == EngineStrategy.REACT


def test_base_engine_create_result_failure() -> None:
    engine = _DummyEngine(workspace="/tmp")
    result = engine._create_result(
        success=False,
        final_answer="partial",
        error="something broke",
    )
    assert result.success is False
    assert result.error == "something broke"


def test_base_engine_create_result_includes_steps() -> None:
    engine = _DummyEngine(workspace="/tmp")
    from polaris.cells.roles.engine.internal.base import StepResult

    engine._steps.append(
        StepResult(
            step_index=0,
            status=EngineStatus.COMPLETED,
            thought="think",
            action="act",
            action_input={},
            observation="obs",
            progress_detected=True,
        )
    )
    result = engine._create_result(
        success=True,
        final_answer="done",
        termination_reason="task_completed",
    )
    assert result.total_steps == 1


def test_base_engine_call_llm_returns_empty_when_no_caller() -> None:
    """_call_llm returns empty string when no llm_caller injected (testability)."""
    engine = _DummyEngine(workspace="/tmp")
    import asyncio

    ctx = EngineContext(workspace="/tmp", role="pm", task="test")
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(engine._call_llm(ctx, "prompt"))
        assert result == ""
    finally:
        loop.close()


def test_base_engine_can_continue_respects_budget() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._current_step = 1
    # can_continue checks budget AND subclass override
    # With subclass returning True for step < 2, True for step 1
    assert engine.can_continue() is True


def test_base_engine_current_step_property() -> None:
    engine = _DummyEngine(workspace="/tmp")
    engine._current_step = 5
    assert engine.current_step == 5
