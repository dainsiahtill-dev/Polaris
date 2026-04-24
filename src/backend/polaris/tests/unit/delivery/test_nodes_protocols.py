"""Tests for polaris.delivery.cli.pm.nodes.protocols.

Covers dataclasses, protocols, and helper methods with normal,
boundary, and edge cases.
"""

from __future__ import annotations

import argparse

from polaris.delivery.cli.pm.nodes.protocols import (
    OrchestrationConfig,
    OrchestrationState,
    RoleContext,
    RoleResult,
)


class TestRoleContext:
    """Tests for RoleContext dataclass."""

    def test_defaults(self) -> None:
        ctx = RoleContext()
        assert ctx.workspace_full == ""
        assert ctx.cache_root_full == ""
        assert ctx.run_dir == ""
        assert ctx.run_id == ""
        assert ctx.pm_iteration == 1
        assert ctx.requirements == ""
        assert ctx.plan_text == ""
        assert ctx.gap_report == ""
        assert ctx.last_qa == ""
        assert ctx.last_tasks == []
        assert ctx.pm_result is None
        assert ctx.chief_engineer_result is None
        assert ctx.director_result is None
        assert ctx.qa_result is None
        assert ctx.pm_state == {}
        assert ctx.args is None
        assert ctx.events_path == ""
        assert ctx.dialogue_path == ""
        assert ctx.trigger == ""
        assert ctx.trigger_source == ""
        assert ctx.metadata == {}
        assert ctx.usage_ctx == {}

    def test_get_previous_result_pm(self) -> None:
        ctx = RoleContext(pm_result={"tasks": ["t1"]})
        assert ctx.get_previous_result("pm") == {"tasks": ["t1"]}

    def test_get_previous_result_chief_engineer(self) -> None:
        ctx = RoleContext(chief_engineer_result={"blueprint": "b1"})
        assert ctx.get_previous_result("chiefengineer") == {"blueprint": "b1"}

    def test_get_previous_result_director(self) -> None:
        ctx = RoleContext(director_result={"status": "done"})
        assert ctx.get_previous_result("director") == {"status": "done"}

    def test_get_previous_result_qa(self) -> None:
        ctx = RoleContext(qa_result={"pass": True})
        assert ctx.get_previous_result("qa") == {"pass": True}

    def test_get_previous_result_unknown(self) -> None:
        ctx = RoleContext()
        assert ctx.get_previous_result("unknown") is None

    def test_get_previous_result_case_insensitive(self) -> None:
        ctx = RoleContext(pm_result={"tasks": ["t1"]})
        assert ctx.get_previous_result("PM") == {"tasks": ["t1"]}
        assert ctx.get_previous_result("Pm") == {"tasks": ["t1"]}

    def test_get_tasks_from_last_tasks(self) -> None:
        ctx = RoleContext(last_tasks=[{"id": "t1"}, {"id": "t2"}])
        assert ctx.get_tasks() == [{"id": "t1"}, {"id": "t2"}]

    def test_get_tasks_from_pm_result(self) -> None:
        ctx = RoleContext(pm_result={"tasks": [{"id": "t3"}]})
        assert ctx.get_tasks() == [{"id": "t3"}]

    def test_get_tasks_empty(self) -> None:
        ctx = RoleContext()
        assert ctx.get_tasks() == []

    def test_get_tasks_pm_result_not_dict(self) -> None:
        ctx = RoleContext(pm_result="not a dict")  # type: ignore[arg-type]
        assert ctx.get_tasks() == []

    def test_get_tasks_pm_result_no_tasks(self) -> None:
        ctx = RoleContext(pm_result={"other": "value"})
        assert ctx.get_tasks() == []

    def test_with_args(self) -> None:
        args = argparse.Namespace(workspace="/tmp", iterations=3)
        ctx = RoleContext(args=args)
        assert ctx.args == args


class TestRoleResult:
    """Tests for RoleResult dataclass."""

    def test_defaults(self) -> None:
        result = RoleResult()
        assert result.success is True
        assert result.exit_code == 0
        assert result.tasks == []
        assert result.contract is None
        assert result.blueprint is None
        assert result.report is None
        assert result.status_updates == {}
        assert result.error == ""
        assert result.error_code == ""
        assert result.warnings == []
        assert result.next_role == ""
        assert result.continue_reason == ""
        assert result.metadata == {}
        assert result.usage_ctx == {}
        assert result.duration_ms == 0
        assert result.tokens_used == 0

    def test_to_dict(self) -> None:
        result = RoleResult(
            success=False,
            exit_code=1,
            tasks=[{"id": "t1"}],
            error="something failed",
            duration_ms=1500,
            tokens_used=500,
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["exit_code"] == 1
        assert d["tasks"] == [{"id": "t1"}]
        assert d["error"] == "something failed"
        assert d["duration_ms"] == 1500
        assert d["tokens_used"] == 500
        assert d["contract"] is None

    def test_to_dict_full(self) -> None:
        result = RoleResult(
            success=True,
            exit_code=0,
            tasks=[{"id": "t1"}],
            contract={"name": "c1"},
            blueprint={"name": "b1"},
            report={"summary": "ok"},
            status_updates={"t1": "done"},
            error="",
            error_code="",
            warnings=["w1"],
            next_role="director",
            continue_reason="proceed",
            metadata={"key": "value"},
            usage_ctx={"model": "gpt-4"},
            duration_ms=1000,
            tokens_used=200,
        )
        d = result.to_dict()
        assert d["blueprint"] == {"name": "b1"}
        assert d["report"] == {"summary": "ok"}
        assert d["status_updates"] == {"t1": "done"}
        assert d["warnings"] == ["w1"]
        assert d["next_role"] == "director"
        assert d["continue_reason"] == "proceed"
        assert d["metadata"] == {"key": "value"}
        assert d["usage_ctx"] == {"model": "gpt-4"}


class TestOrchestrationState:
    """Tests for OrchestrationState dataclass."""

    def test_defaults(self) -> None:
        state = OrchestrationState()
        assert state.phase == "idle"
        assert state.role_states == {}
        assert state.current_role == ""
        assert state.completed_roles == []
        assert state.pending_roles == []
        assert state.iteration == 0
        assert state.run_id == ""
        assert state.global_state == {}

    def test_get_role_state_existing(self) -> None:
        state = OrchestrationState(role_states={"pm": {"status": "done"}})
        assert state.get_role_state("pm") == {"status": "done"}

    def test_get_role_state_missing(self) -> None:
        state = OrchestrationState()
        assert state.get_role_state("pm") == {}

    def test_set_role_state(self) -> None:
        state = OrchestrationState()
        state.set_role_state("pm", {"status": "running"})
        assert state.role_states["pm"] == {"status": "running"}

    def test_is_role_completed(self) -> None:
        state = OrchestrationState(completed_roles=["pm", "qa"])
        assert state.is_role_completed("pm") is True
        assert state.is_role_completed("director") is False

    def test_is_role_running(self) -> None:
        state = OrchestrationState(current_role="pm")
        assert state.is_role_running("pm") is True
        assert state.is_role_running("qa") is False


class TestOrchestrationConfig:
    """Tests for OrchestrationConfig dataclass."""

    def test_defaults(self) -> None:
        config = OrchestrationConfig()
        assert config.director_execution_mode == "single"
        assert config.max_directors == 1
        assert config.scheduling_policy == "priority"
        assert config.enable_chief_engineer is True
        assert config.enable_integration_qa is True
        assert config.enable_taskboard is True
        assert config.max_retries == 3
        assert config.retry_delay_seconds == 5
        assert config.role_timeout_seconds > 0

    def test_from_args_defaults(self) -> None:
        args = argparse.Namespace()
        config = OrchestrationConfig.from_args(args)
        assert config.director_execution_mode == "single"
        assert config.max_directors == 1
        assert config.scheduling_policy == "priority"

    def test_from_args_custom(self) -> None:
        args = argparse.Namespace(
            director_execution_mode="multi",
            max_directors=5,
            director_scheduling_policy="fifo",
        )
        config = OrchestrationConfig.from_args(args)
        assert config.director_execution_mode == "multi"
        assert config.max_directors == 5
        assert config.scheduling_policy == "fifo"

    def test_from_args_partial(self) -> None:
        args = argparse.Namespace(director_execution_mode="multi")
        config = OrchestrationConfig.from_args(args)
        assert config.director_execution_mode == "multi"
        assert config.max_directors == 1
        assert config.scheduling_policy == "priority"
