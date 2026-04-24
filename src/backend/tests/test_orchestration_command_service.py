"""Tests for OrchestrationCommandService status-query diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import OrchestrationCommandService
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    OrchestrationSnapshot,
    RunStatus,
    TaskPhase,
    TaskSnapshot,
)


class _StubOrchestrationService:
    def __init__(self, snapshot: OrchestrationSnapshot | None) -> None:
        self._snapshot = snapshot

    async def query_run(self, run_id: str) -> OrchestrationSnapshot | None:
        if self._snapshot is None:
            return None
        return self._snapshot if self._snapshot.run_id == run_id else None


class _SubmitCaptureService:
    def __init__(self) -> None:
        self.request = None

    async def submit_run(self, request):
        self.request = request
        return OrchestrationSnapshot(
            run_id=request.run_id,
            workspace=str(request.workspace),
            mode=request.mode.value,
            status=RunStatus.PENDING,
            current_phase=TaskPhase.INIT,
        )


@pytest.mark.asyncio
async def test_query_run_status_includes_failed_task_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    snapshot = OrchestrationSnapshot(
        run_id="pm-run-001",
        workspace="C:/Temp/demo",
        mode="workflow",
        status=RunStatus.FAILED,
        current_phase=TaskPhase.EXECUTING,
        overall_progress=50.0,
    )

    pm_task = TaskSnapshot(
        task_id="task-0-pm",
        status=RunStatus.FAILED,
        phase=TaskPhase.EXECUTING,
        role_id="pm",
    )
    pm_task.error_category = "runtime"
    pm_task.error_message = "PM contract normalization failed: missing acceptance criteria"
    pm_task.updated_at = now

    qa_task = TaskSnapshot(
        task_id="task-1-qa",
        status=RunStatus.BLOCKED,
        phase=TaskPhase.EXECUTING,
        role_id="qa",
    )
    qa_task.error_category = "runtime"
    qa_task.error_message = "Upstream task failed"
    qa_task.updated_at = now - timedelta(seconds=1)

    snapshot.tasks = {
        pm_task.task_id: pm_task,
        qa_task.task_id: qa_task,
    }

    stub = _StubOrchestrationService(snapshot)

    async def _get_service() -> _StubOrchestrationService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.query_run_status("pm-run-001")

    assert result.status == "failed"
    assert "failed_task=task-0-pm (pm)" in str(result.message)
    assert "missing acceptance criteria" in str(result.message)
    assert isinstance(result.metadata, dict)
    assert result.metadata["failed_task_count"] == 2
    assert result.metadata["task_status_counts"]["failed"] == 1
    assert result.metadata["task_status_counts"]["blocked"] == 1
    failed_tasks = result.metadata["failed_tasks"]
    assert failed_tasks[0]["task_id"] == "task-0-pm"
    assert failed_tasks[0]["role_id"] == "pm"
    assert "missing acceptance criteria" in str(failed_tasks[0]["error_message"])


@pytest.mark.asyncio
async def test_query_run_status_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubOrchestrationService(None)

    async def _get_service() -> _StubOrchestrationService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.query_run_status("missing-run")

    assert result.status == "failed"
    assert result.reason_code == "RUN_NOT_FOUND"
    assert result.message == "Run missing-run not found"


@pytest.mark.asyncio
async def test_execute_pm_run_propagates_metadata_to_role_entry_and_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _SubmitCaptureService()

    async def _get_service() -> _SubmitCaptureService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.register_all_adapters",
        lambda _: None,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.execute_pm_run(
        workspace=str(tmp_path),
        run_type="pm",
        options={
            "directive": "生成受控投影计划",
            "metadata": {
                "execution_backend": "projection_generate",
                "projection": {
                    "scenario_id": "scenario_alpha",
                    "project_slug": "projection_lab",
                },
            },
        },
    )

    assert result.status == "pending"
    assert stub.request is not None
    assert stub.request.role_entries[0].metadata["execution_backend"] == "projection_generate"
    assert stub.request.metadata["execution_backend"] == "projection_generate"
    assert stub.request.metadata["projection"]["scenario_id"] == "scenario_alpha"


@pytest.mark.asyncio
async def test_execute_director_run_propagates_metadata_to_role_entry_and_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _SubmitCaptureService()

    async def _get_service() -> _SubmitCaptureService:
        return stub

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.get_orchestration_service",
        _get_service,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service.register_all_adapters",
        lambda _: None,
    )

    service = OrchestrationCommandService(settings={})
    result = await service.execute_director_run(
        workspace=str(tmp_path),
        tasks=["task-1"],
        options={
            "execution_mode": "parallel",
            "metadata": {
                "execution_backend": "projection_reproject",
                "projection": {
                    "scenario_id": "scenario_alpha",
                    "experiment_id": "exp-001",
                },
            },
        },
    )

    assert result.status == "pending"
    assert stub.request is not None
    assert stub.request.role_entries[0].metadata["execution_backend"] == "projection_reproject"
    assert stub.request.metadata["execution_backend"] == "projection_reproject"
    assert stub.request.metadata["projection"]["experiment_id"] == "exp-001"
