"""Tests for polaris.cells.chief_engineer.blueprint.public.contracts.

Covers all frozen dataclasses, validation logic in __post_init__,
and the custom error class.
"""

from __future__ import annotations

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintError,
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
)


class TestGenerateTaskBlueprintCommandV1:
    """Tests for GenerateTaskBlueprintCommandV1."""

    def test_valid_command(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(task_id="t1", workspace="/ws", objective="design api")
        assert cmd.run_id is None
        assert cmd.constraints == {}
        assert cmd.context == {}

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(task_id="", workspace="/ws", objective="design api")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(task_id="t1", workspace="", objective="design api")

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="objective must be a non-empty string"):
            GenerateTaskBlueprintCommandV1(task_id="t1", workspace="/ws", objective="")

    def test_constraints_copied(self) -> None:
        original = {"max_lines": 100}
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="t1", workspace="/ws", objective="design api", constraints=original
        )
        assert cmd.constraints == {"max_lines": 100}
        original["max_lines"] = 200
        assert cmd.constraints == {"max_lines": 100}

    def test_context_copied(self) -> None:
        original = {"repo": "polaris"}
        cmd = GenerateTaskBlueprintCommandV1(task_id="t1", workspace="/ws", objective="design api", context=original)
        assert cmd.context == {"repo": "polaris"}
        original["repo"] = "other"
        assert cmd.context == {"repo": "polaris"}


class TestGetBlueprintStatusQueryV1:
    """Tests for GetBlueprintStatusQueryV1."""

    def test_valid_query(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="t1", workspace="/ws")
        assert q.run_id is None

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetBlueprintStatusQueryV1(task_id="", workspace="/ws")


class TestTaskBlueprintGeneratedEventV1:
    """Tests for TaskBlueprintGeneratedEventV1."""

    def test_valid_event(self) -> None:
        ev = TaskBlueprintGeneratedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/ws",
            blueprint_path="/ws/blueprint.md",
            generated_at="2026-01-01T00:00:00Z",
        )
        assert ev.risk_level is None

    def test_empty_blueprint_path_raises(self) -> None:
        with pytest.raises(ValueError, match="blueprint_path must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="e1",
                task_id="t1",
                workspace="/ws",
                blueprint_path="",
                generated_at="2026-01-01T00:00:00Z",
            )

    def test_empty_generated_at_raises(self) -> None:
        with pytest.raises(ValueError, match="generated_at must be a non-empty string"):
            TaskBlueprintGeneratedEventV1(
                event_id="e1",
                task_id="t1",
                workspace="/ws",
                blueprint_path="/ws/blueprint.md",
                generated_at="",
            )


class TestTaskBlueprintResultV1:
    """Tests for TaskBlueprintResultV1."""

    def test_valid_result(self) -> None:
        r = TaskBlueprintResultV1(ok=True, task_id="t1", workspace="/ws", status="done")
        assert r.blueprint_path is None
        assert r.summary == ""
        assert r.recommendations == ()
        assert r.risks == ()

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            TaskBlueprintResultV1(ok=True, task_id="", workspace="/ws", status="done")

    def test_recommendations_coerced(self) -> None:
        r = TaskBlueprintResultV1(
            ok=True,
            task_id="t1",
            workspace="/ws",
            status="done",
            recommendations=["r1"],
        )
        assert r.recommendations == ("r1",)

    def test_risks_coerced(self) -> None:
        r = TaskBlueprintResultV1(ok=True, task_id="t1", workspace="/ws", status="done", risks=["risk1"])
        assert r.risks == ("risk1",)


class TestChiefEngineerBlueprintErrorV1:
    """Tests for ChiefEngineerBlueprintErrorV1."""

    def test_defaults(self) -> None:
        err = ChiefEngineerBlueprintErrorV1("boom")
        assert str(err) == "boom"
        assert err.code == "chief_engineer_blueprint_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ChiefEngineerBlueprintErrorV1("boom", code="E1", details={"k": "v"})
        assert err.code == "E1"
        assert err.details == {"k": "v"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            ChiefEngineerBlueprintErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            ChiefEngineerBlueprintErrorV1("boom", code="")


class TestBackwardCompatibleAlias:
    """Tests that ChiefEngineerBlueprintError is an alias for ChiefEngineerBlueprintErrorV1."""

    def test_alias_identity(self) -> None:
        assert ChiefEngineerBlueprintError is ChiefEngineerBlueprintErrorV1
