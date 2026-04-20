"""Tests for public contracts (frozen dataclasses + error types)."""

from __future__ import annotations

import pytest
from polaris.cells.roles.engine.public.contracts import (
    ClassifyTaskQueryV1,
    EngineExecutionResultV1,
    EngineRegistrySnapshotQueryV1,
    EngineRegistrySnapshotResultV1,
    EngineSelectedEventV1,
    EngineSelectionResultV1,
    IRoleEngineService,
    RegisterEngineCommandV1,
    RolesEngineError,
    SelectEngineCommandV1,
)

# ---------------------------------------------------------------------------
# ClassifyTaskQueryV1
# ---------------------------------------------------------------------------


def test_classify_task_query_v1_valid() -> None:
    q = ClassifyTaskQueryV1(task="analyse codebase")
    assert q.task == "analyse codebase"
    assert q.role is None
    assert q.context == {}


def test_classify_task_query_v1_with_role() -> None:
    q = ClassifyTaskQueryV1(task="analyse", role="architect")
    assert q.role == "architect"


def test_classify_task_query_v1_with_context() -> None:
    q = ClassifyTaskQueryV1(task="analyse", context={"lang": "python"})
    assert q.context == {"lang": "python"}


def test_classify_task_query_v1_strips_whitespace() -> None:
    q = ClassifyTaskQueryV1(task="  analyse  ", role="  architect  ")
    assert q.task == "analyse"
    assert q.role == "architect"


def test_classify_task_query_v1_rejects_empty_task() -> None:
    with pytest.raises(ValueError, match="task"):
        ClassifyTaskQueryV1(task="")
    with pytest.raises(ValueError, match="task"):
        ClassifyTaskQueryV1(task="   ")


def test_classify_task_query_v1_rejects_empty_role() -> None:
    with pytest.raises(ValueError, match="role"):
        ClassifyTaskQueryV1(task="analyse", role="")


def test_classify_task_query_v1_is_frozen() -> None:
    q = ClassifyTaskQueryV1(task="analyse")
    with pytest.raises(Exception):
        q.task = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SelectEngineCommandV1
# ---------------------------------------------------------------------------


def test_select_engine_command_v1_valid() -> None:
    cmd = SelectEngineCommandV1(workspace="/ws", task="implement login")
    assert cmd.workspace == "/ws"
    assert cmd.task == "implement login"
    assert cmd.role is None
    assert cmd.preferred_strategy is None
    assert cmd.context == {}


def test_select_engine_command_v1_with_options() -> None:
    cmd = SelectEngineCommandV1(
        workspace="/ws",
        task="implement login",
        role="architect",
        preferred_strategy="react",
        context={"env": "prod"},
    )
    assert cmd.role == "architect"
    assert cmd.preferred_strategy == "react"
    assert cmd.context == {"env": "prod"}


def test_select_engine_command_v1_rejects_empty_workspace() -> None:
    with pytest.raises(ValueError, match="workspace"):
        SelectEngineCommandV1(workspace="", task="impl")
    with pytest.raises(ValueError, match="workspace"):
        SelectEngineCommandV1(workspace="   ", task="impl")


def test_select_engine_command_v1_rejects_empty_task() -> None:
    with pytest.raises(ValueError, match="task"):
        SelectEngineCommandV1(workspace="/ws", task="")


def test_select_engine_command_v1_rejects_empty_preferred_strategy() -> None:
    with pytest.raises(ValueError, match="preferred_strategy"):
        SelectEngineCommandV1(workspace="/ws", task="impl", preferred_strategy="")


def test_select_engine_command_v1_is_frozen() -> None:
    cmd = SelectEngineCommandV1(workspace="/ws", task="impl")
    with pytest.raises(Exception):
        cmd.workspace = "/other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RegisterEngineCommandV1
# ---------------------------------------------------------------------------


def test_register_engine_command_v1_valid() -> None:
    cmd = RegisterEngineCommandV1(strategy="react", engine_class="ReActEngine")
    assert cmd.strategy == "react"
    assert cmd.engine_class == "ReActEngine"
    assert cmd.workspace is None
    assert cmd.defaults == {}


def test_register_engine_command_v1_with_workspace() -> None:
    cmd = RegisterEngineCommandV1(
        strategy="react",
        engine_class="ReActEngine",
        workspace="/ws",
        defaults={"max_steps": 10},
    )
    assert cmd.workspace == "/ws"
    assert cmd.defaults == {"max_steps": 10}


def test_register_engine_command_v1_rejects_empty_strategy() -> None:
    with pytest.raises(ValueError, match="strategy"):
        RegisterEngineCommandV1(strategy="", engine_class="X")


def test_register_engine_command_v1_rejects_empty_engine_class() -> None:
    with pytest.raises(ValueError, match="engine_class"):
        RegisterEngineCommandV1(strategy="react", engine_class="")


def test_register_engine_command_v1_is_frozen() -> None:
    cmd = RegisterEngineCommandV1(strategy="react", engine_class="X")
    with pytest.raises(Exception):
        cmd.strategy = "plansolve"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EngineRegistrySnapshotQueryV1
# ---------------------------------------------------------------------------


def test_engine_registry_snapshot_query_v1_defaults() -> None:
    q = EngineRegistrySnapshotQueryV1()
    assert q.include_instances is False


def test_engine_registry_snapshot_query_v1_with_flag() -> None:
    q = EngineRegistrySnapshotQueryV1(include_instances=True)
    assert q.include_instances is True


# ---------------------------------------------------------------------------
# EngineSelectedEventV1
# ---------------------------------------------------------------------------


def test_engine_selected_event_v1_valid() -> None:
    evt = EngineSelectedEventV1(
        event_id="e1",
        workspace="/ws",
        role="pm",
        strategy="react",
        selected_at="2026-03-23T10:00:00Z",
    )
    assert evt.event_id == "e1"
    assert evt.strategy == "react"
    assert evt.task is None


def test_engine_selected_event_v1_with_task() -> None:
    evt = EngineSelectedEventV1(
        event_id="e1",
        workspace="/ws",
        role="pm",
        strategy="react",
        selected_at="2026-03-23T10:00:00Z",
        task="implement feature",
    )
    assert evt.task == "implement feature"


def test_engine_selected_event_v1_rejects_empty_event_id() -> None:
    with pytest.raises(ValueError, match="event_id"):
        EngineSelectedEventV1(
            event_id="",
            workspace="/ws",
            role="pm",
            strategy="react",
            selected_at="now",
        )


def test_engine_selected_event_v1_rejects_empty_strategy() -> None:
    with pytest.raises(ValueError, match="strategy"):
        EngineSelectedEventV1(
            event_id="e1",
            workspace="/ws",
            role="pm",
            strategy="",
            selected_at="now",
        )


# ---------------------------------------------------------------------------
# EngineSelectionResultV1
# ---------------------------------------------------------------------------


def test_engine_selection_result_v1_ok() -> None:
    res = EngineSelectionResultV1(
        ok=True,
        status="selected",
        strategy="react",
        engine_class="ReActEngine",
        reason="matched pattern",
    )
    assert res.ok is True
    assert res.engine_class == "ReActEngine"


def test_engine_selection_result_v1_failure_requires_error() -> None:
    # ok=False must have error_code or error_message
    with pytest.raises(ValueError, match="failed result must include"):
        EngineSelectionResultV1(
            ok=False,
            status="failed",
            strategy="react",
        )


def test_engine_selection_result_v1_failure_with_error_code() -> None:
    res = EngineSelectionResultV1(
        ok=False,
        status="failed",
        strategy="react",
        error_code="ENGINE_NOT_FOUND",
        error_message="Engine not registered",
    )
    assert res.ok is False
    assert res.error_code == "ENGINE_NOT_FOUND"


def test_engine_selection_result_v1_metadata() -> None:
    res = EngineSelectionResultV1(
        ok=True,
        status="ok",
        strategy="react",
        metadata={"cache_hit": True},
    )
    assert res.metadata == {"cache_hit": True}


def test_engine_selection_result_v1_is_frozen() -> None:
    res = EngineSelectionResultV1(ok=True, status="ok", strategy="react")
    with pytest.raises(Exception):
        res.ok = False  # type: ignore[misc]


def test_engine_selection_result_v1_engine_class_none_is_ok() -> None:
    """engine_class=None is allowed (optional field)."""
    res = EngineSelectionResultV1(
        ok=True,
        status="ok",
        strategy="react",
        engine_class=None,
    )
    assert res.engine_class is None


def test_engine_selection_result_v1_engine_class_empty_rejected() -> None:
    """engine_class='' is rejected by __post_init__."""
    with pytest.raises(ValueError, match="engine_class"):
        EngineSelectionResultV1(
            ok=True,
            status="ok",
            strategy="react",
            engine_class="",
        )


# ---------------------------------------------------------------------------
# EngineExecutionResultV1
# ---------------------------------------------------------------------------


def test_engine_execution_result_v1_success() -> None:
    res = EngineExecutionResultV1(
        ok=True,
        status="completed",
        strategy="react",
        final_answer="Done",
    )
    assert res.ok is True
    assert res.status == "completed"
    assert res.final_answer == "Done"


def test_engine_execution_result_v1_with_counters() -> None:
    res = EngineExecutionResultV1(
        ok=True,
        status="completed",
        strategy="react",
        final_answer="Done",
        total_steps=5,
        total_tool_calls=3,
        execution_time_seconds=1.5,
    )
    assert res.total_steps == 5
    assert res.execution_time_seconds == 1.5


def test_engine_execution_result_v1_failure_requires_error() -> None:
    with pytest.raises(ValueError, match="failed result must include"):
        EngineExecutionResultV1(
            ok=False,
            status="error",
            strategy="react",
            final_answer="",
        )


def test_engine_execution_result_v1_failure_with_errors() -> None:
    res = EngineExecutionResultV1(
        ok=False,
        status="error",
        strategy="react",
        final_answer="",
        error_code="LLM_ERROR",
        error_message="Model unavailable",
    )
    assert res.ok is False
    assert res.error_code == "LLM_ERROR"


def test_engine_execution_result_v1_negative_counters_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        EngineExecutionResultV1(
            ok=True,
            status="ok",
            strategy="react",
            final_answer="x",
            total_steps=-1,
        )
    with pytest.raises(ValueError, match=">= 0"):
        EngineExecutionResultV1(
            ok=True,
            status="ok",
            strategy="react",
            final_answer="x",
            total_tool_calls=-1,
        )
    with pytest.raises(ValueError, match=">= 0"):
        EngineExecutionResultV1(
            ok=True,
            status="ok",
            strategy="react",
            final_answer="x",
            execution_time_seconds=-0.1,
        )


def test_engine_execution_result_v1_termination_reason() -> None:
    res = EngineExecutionResultV1(
        ok=True,
        status="completed",
        strategy="react",
        final_answer="done",
        termination_reason="task_completed",
    )
    assert res.termination_reason == "task_completed"


def test_engine_execution_result_v1_is_frozen() -> None:
    res = EngineExecutionResultV1(
        ok=True,
        status="ok",
        strategy="react",
        final_answer="x",
    )
    with pytest.raises(Exception):
        res.final_answer = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EngineRegistrySnapshotResultV1
# ---------------------------------------------------------------------------


def test_engine_registry_snapshot_result_v1_valid() -> None:
    res = EngineRegistrySnapshotResultV1(
        strategies=("react", "plansolve", "tot"),
        registered_count=3,
        cached_count=0,
    )
    assert res.strategies == ("react", "plansolve", "tot")
    assert res.registered_count == 3


def test_engine_registry_snapshot_result_v1_normalises_whitespace() -> None:
    res = EngineRegistrySnapshotResultV1(
        strategies=(" react ", "plansolve ", "  tot  "),
        registered_count=3,
        cached_count=0,
    )
    assert res.strategies == ("react", "plansolve", "tot")


def test_engine_registry_snapshot_result_v1_filters_empty() -> None:
    res = EngineRegistrySnapshotResultV1(
        strategies=("react", "", "  ", "plansolve"),
        registered_count=2,
        cached_count=0,
    )
    assert res.strategies == ("react", "plansolve")


def test_engine_registry_snapshot_result_v1_negative_counts_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        EngineRegistrySnapshotResultV1(
            strategies=(),
            registered_count=-1,
        )


# ---------------------------------------------------------------------------
# RolesEngineError
# ---------------------------------------------------------------------------


def test_roles_engine_error_default() -> None:
    err = RolesEngineError("something went wrong")
    assert str(err) == "something went wrong"
    assert err.code == "roles_engine_error"
    assert err.details == {}


def test_roles_engine_error_with_code_and_details() -> None:
    err = RolesEngineError(
        "bad thing",
        code="ENGINE_ERROR",
        details={"strategy": "react", "step": 5},
    )
    assert err.code == "ENGINE_ERROR"
    assert err.details == {"strategy": "react", "step": 5}


def test_roles_engine_error_rejects_empty_message() -> None:
    with pytest.raises(ValueError, match="message"):
        RolesEngineError("")


def test_roles_engine_error_rejects_empty_code() -> None:
    with pytest.raises(ValueError, match="code"):
        RolesEngineError("msg", code="")


# ---------------------------------------------------------------------------
# IRoleEngineService (structural duck-typing via runtime_checkable)
# ---------------------------------------------------------------------------


def test_irole_engine_service_protocol() -> None:
    """Protocol defines the required methods."""
    assert hasattr(IRoleEngineService, "classify_task")
    assert hasattr(IRoleEngineService, "select_engine")
    assert hasattr(IRoleEngineService, "get_registry_snapshot")
