"""Tests for workflow_runtime internal runtime_contracts module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    CompatibilityMapper,
    OrchestrationMode,
    OrchestrationRunRequest,
    OrchestrationSignal,
    PipelineSpec,
    PipelineTask,
    RoleEntrySpec,
    RunStatus,
    SignalRequest,
    TaskPhase,
    TaskSnapshot,
)


class TestRunStatus:
    def test_is_terminal(self) -> None:
        assert RunStatus.COMPLETED.is_terminal() is True
        assert RunStatus.FAILED.is_terminal() is True
        assert RunStatus.CANCELLED.is_terminal() is True
        assert RunStatus.TIMEOUT.is_terminal() is True
        assert RunStatus.PENDING.is_terminal() is False
        assert RunStatus.RUNNING.is_terminal() is False

    def test_can_retry(self) -> None:
        assert RunStatus.FAILED.can_retry() is True
        assert RunStatus.TIMEOUT.can_retry() is True
        assert RunStatus.COMPLETED.can_retry() is False


class TestRoleEntrySpec:
    def test_validate_ok(self) -> None:
        spec = RoleEntrySpec(role_id="pm", input="do something")
        assert spec.validate() == []

    def test_validate_invalid_role(self) -> None:
        spec = RoleEntrySpec(role_id="unknown")
        errors = spec.validate()
        assert any("Invalid role_id" in e for e in errors)

    def test_validate_path_traversal(self) -> None:
        spec = RoleEntrySpec(role_id="pm", scope_paths=["../etc"])
        errors = spec.validate()
        assert any("Path traversal" in e for e in errors)

    def test_validate_input_too_large(self) -> None:
        spec = RoleEntrySpec(role_id="pm", input="x" * 11_000_000)
        errors = spec.validate()
        assert any("exceeds 10MB" in e for e in errors)


class TestPipelineTask:
    def test_validate_ok(self) -> None:
        task = PipelineTask(
            task_id="task-1",
            role_entry=RoleEntrySpec(role_id="pm"),
        )
        assert task.validate() == []

    def test_validate_empty_task_id(self) -> None:
        task = PipelineTask(
            task_id="",
            role_entry=RoleEntrySpec(role_id="pm"),
        )
        errors = task.validate()
        assert any("task_id cannot be empty" in e for e in errors)

    def test_validate_invalid_task_id_format(self) -> None:
        task = PipelineTask(
            task_id="task@1!",
            role_entry=RoleEntrySpec(role_id="pm"),
        )
        errors = task.validate()
        assert any("Invalid task_id format" in e for e in errors)

    def test_validate_bad_concurrency(self) -> None:
        task = PipelineTask(
            task_id="t1",
            role_entry=RoleEntrySpec(role_id="pm"),
            max_concurrency=0,
        )
        errors = task.validate()
        assert any("max_concurrency must be >= 1" in e for e in errors)

    def test_validate_bad_timeout(self) -> None:
        task = PipelineTask(
            task_id="t1",
            role_entry=RoleEntrySpec(role_id="pm"),
            timeout_seconds=0,
        )
        errors = task.validate()
        assert any("timeout_seconds must be >= 1" in e for e in errors)


class TestPipelineSpec:
    def test_validate_empty(self) -> None:
        spec = PipelineSpec(tasks=[])
        errors = spec.validate()
        assert any("at least one task" in e for e in errors)

    def test_validate_duplicate_task_id(self) -> None:
        spec = PipelineSpec(
            tasks=[
                PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm")),
                PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm")),
            ]
        )
        errors = spec.validate()
        assert any("Duplicate task_id" in e for e in errors)

    def test_validate_missing_dependency(self) -> None:
        spec = PipelineSpec(
            tasks=[
                PipelineTask(
                    task_id="t1",
                    role_entry=RoleEntrySpec(role_id="pm"),
                    depends_on=["missing"],
                ),
            ]
        )
        errors = spec.validate()
        assert any("depends on unknown task" in e for e in errors)

    def test_validate_cycle_detected(self) -> None:
        spec = PipelineSpec(
            tasks=[
                PipelineTask(task_id="a", role_entry=RoleEntrySpec(role_id="pm"), depends_on=["b"]),
                PipelineTask(task_id="b", role_entry=RoleEntrySpec(role_id="pm"), depends_on=["a"]),
            ]
        )
        errors = spec.validate()
        assert any("Circular dependency" in e for e in errors)

    def test_validate_ok(self) -> None:
        spec = PipelineSpec(
            tasks=[
                PipelineTask(task_id="a", role_entry=RoleEntrySpec(role_id="pm")),
                PipelineTask(task_id="b", role_entry=RoleEntrySpec(role_id="pm"), depends_on=["a"]),
            ]
        )
        assert spec.validate() == []


class TestOrchestrationRunRequest:
    def test_validate_empty_run_id(self) -> None:
        req = OrchestrationRunRequest(run_id="", workspace=Path("."), mode=OrchestrationMode.WORKFLOW)
        errors = req.validate()
        assert any("run_id cannot be empty" in e for e in errors)

    def test_validate_nonexistent_workspace(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "does_not_exist"
        req = OrchestrationRunRequest(
            run_id="r1",
            workspace=bad_path,
            mode=OrchestrationMode.WORKFLOW,
        )
        errors = req.validate()
        assert any("Workspace does not exist" in e for e in errors)

    def test_validate_needs_pipeline_or_roles(self, tmp_path: Path) -> None:
        req = OrchestrationRunRequest(
            run_id="r1",
            workspace=tmp_path,
            mode=OrchestrationMode.WORKFLOW,
        )
        errors = req.validate()
        assert any("Either pipeline_spec or role_entries" in e for e in errors)

    def test_validate_with_pipeline_spec(self, tmp_path: Path) -> None:
        spec = PipelineSpec(
            tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))]
        )
        req = OrchestrationRunRequest(
            run_id="r1",
            workspace=tmp_path,
            mode=OrchestrationMode.WORKFLOW,
            pipeline_spec=spec,
        )
        assert req.validate() == []

    def test_validate_with_role_entries(self, tmp_path: Path) -> None:
        req = OrchestrationRunRequest(
            run_id="r1",
            workspace=tmp_path,
            mode=OrchestrationMode.CHAT,
            role_entries=[RoleEntrySpec(role_id="pm")],
        )
        assert req.validate() == []


class TestCompatibilityMapper:
    def test_pm_mode_mapping(self) -> None:
        assert CompatibilityMapper.pm_mode_to_orchestration("run_once") == OrchestrationMode.WORKFLOW
        assert CompatibilityMapper.pm_mode_to_orchestration("chat") == OrchestrationMode.CHAT
        assert CompatibilityMapper.pm_mode_to_orchestration("unknown") == OrchestrationMode.WORKFLOW

    def test_director_mode_mapping(self) -> None:
        assert CompatibilityMapper.director_mode_to_orchestration("one_shot") == OrchestrationMode.WORKFLOW
        assert CompatibilityMapper.director_mode_to_orchestration("chat") == OrchestrationMode.CHAT

    def test_legacy_status_mapping(self) -> None:
        assert CompatibilityMapper.legacy_status_to_unified("idle") == RunStatus.PENDING
        assert CompatibilityMapper.legacy_status_to_unified("running") == RunStatus.RUNNING
        assert CompatibilityMapper.legacy_status_to_unified("success") == RunStatus.COMPLETED
        assert CompatibilityMapper.legacy_status_to_unified("failure") == RunStatus.FAILED
        assert CompatibilityMapper.legacy_status_to_unified("unknown") == RunStatus.PENDING


class TestTaskSnapshot:
    def test_to_dict(self) -> None:
        snap = TaskSnapshot(task_id="t1", status=RunStatus.RUNNING, phase=TaskPhase.EXECUTING, role_id="pm")
        d = snap.to_dict()
        assert d["task_id"] == "t1"
        assert d["status"] == "running"
        assert d["phase"] == "executing"
        assert d["role_id"] == "pm"


class TestSignalRequest:
    def test_creation(self) -> None:
        sig = SignalRequest(signal=OrchestrationSignal.CANCEL, task_id="t1")
        assert sig.signal == OrchestrationSignal.CANCEL
        assert sig.task_id == "t1"
