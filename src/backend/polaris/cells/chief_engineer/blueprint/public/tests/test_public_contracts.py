"""Unit tests for `chief_engineer/blueprint` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    generate_task_blueprint,
    get_blueprint_status,
)


class TestGenerateTaskBlueprintCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
        )
        assert cmd.task_id == "task-1"
        assert cmd.workspace == "/repo"
        assert cmd.objective == "Implement login"

    def test_full(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
            run_id="run-1",
            constraints={"max_time": 300},
            context={"user": "alice"},
        )
        assert cmd.run_id == "run-1"
        assert cmd.constraints == {"max_time": 300}
        assert cmd.context == {"user": "alice"}

    def test_constraints_are_copied(self) -> None:
        original = {"max_time": 300}
        cmd = GenerateTaskBlueprintCommandV1(task_id="task-1", workspace="/repo", objective="x", constraints=original)
        original.clear()
        assert cmd.constraints == {"max_time": 300}


class TestGenerateTaskBlueprintCommandV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="", workspace="/r", objective="x")

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="  ", workspace="/r", objective="x")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="", objective="x")

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="objective"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="/r", objective="")


class TestGetBlueprintStatusQueryV1HappyPath:
    def test_minimal(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo")
        assert q.task_id == "task-1"
        assert q.workspace == "/repo"
        assert q.run_id is None

    def test_with_run_id(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo", run_id="run-1")
        assert q.run_id == "run-1"


class TestGetBlueprintStatusQueryV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GetBlueprintStatusQueryV1(task_id="", workspace="/repo")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetBlueprintStatusQueryV1(task_id="task-1", workspace="")


class TestTaskBlueprintGeneratedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.blueprint_path == "/repo/.blueprint/task-1.yaml"
        assert evt.risk_level is None

    def test_with_risk_level(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
            risk_level="medium",
        )
        assert evt.risk_level == "medium"


class TestTaskBlueprintGeneratedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            TaskBlueprintGeneratedEventV1(
                event_id="",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="/bp",
                generated_at="2026-03-24T10:00:00Z",
            )

    def test_empty_blueprint_path_raises(self) -> None:
        with pytest.raises(ValueError, match="blueprint_path"):
            TaskBlueprintGeneratedEventV1(
                event_id="e1",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="",
                generated_at="2026-03-24T10:00:00Z",
            )


class TestTaskBlueprintResultV1HappyPath:
    def test_success(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_path="/bp.yaml",
        )
        assert res.ok is True
        assert res.blueprint_id is None
        assert res.blueprint_path == "/bp.yaml"
        assert res.recommendations == ()
        assert res.risks == ()

    def test_with_blueprint_id(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_id="bp-1",
        )
        assert res.blueprint_id == "bp-1"

    def test_failure(self) -> None:
        res = TaskBlueprintResultV1(
            ok=False,
            task_id="task-1",
            workspace="/repo",
            status="failed",
            summary="Blueprint generation failed",
        )
        assert res.ok is False
        assert res.status == "failed"

    def test_recommendations_normalized_to_tuple(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="ok",
            recommendations=["use cache", "add retry"],  # type: ignore[arg-type]
        )
        assert res.recommendations == ("use cache", "add retry")


class TestTaskBlueprintResultV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            TaskBlueprintResultV1(ok=True, task_id="", workspace="/repo", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            TaskBlueprintResultV1(ok=True, task_id="task-1", workspace="/repo", status="")


class TestChiefEngineerBlueprintErrorV1:
    def test_default_values(self) -> None:
        err = ChiefEngineerBlueprintErrorV1("blueprint generation failed")
        assert str(err) == "blueprint generation failed"
        assert err.code == "chief_engineer_blueprint_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ChiefEngineerBlueprintErrorV1(
            "timeout",
            code="blueprint_timeout",
            details={"task_id": "task-1"},
        )
        assert err.code == "blueprint_timeout"
        assert err.details == {"task_id": "task-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            ChiefEngineerBlueprintErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ChiefEngineerBlueprintErrorV1("error", code="  ")


class TestChiefEngineerBlueprintPublicService:
    def test_generate_and_query_task_blueprint(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="PM-42",
            workspace=str(tmp_path),
            objective="Build Director task board",
            run_id="run-1",
            constraints={"guardrail": "no target project writes"},
            context={
                "task_title": "Director board",
                "target_files": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.blueprint_id is not None
        assert result.status == "generated"
        assert result.blueprint_path == f"runtime/blueprints/{result.blueprint_id}.json"

        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(
                task_id="PM-42",
                workspace=str(tmp_path),
                run_id="run-1",
            )
        )

        assert status.ok is True
        assert status.blueprint_id == result.blueprint_id
        assert status.summary.startswith("Chief Engineer blueprint for PM-42")

    def test_query_missing_task_blueprint(self, tmp_path) -> None:
        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(
                task_id="missing",
                workspace=str(tmp_path),
            )
        )

        assert status.ok is False
        assert status.status == "missing"
