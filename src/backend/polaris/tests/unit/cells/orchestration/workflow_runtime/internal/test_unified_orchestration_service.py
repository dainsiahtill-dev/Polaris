"""Tests for workflow_runtime internal unified_orchestration_service module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    OrchestrationMode,
    OrchestrationRunRequest,
    OrchestrationSignal,
    OrchestrationSnapshot,
    PipelineSpec,
    PipelineTask,
    RoleEntrySpec,
    RunStatus,
    SignalRequest,
    TaskPhase,
)
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    InMemoryOrchestrationRepository,
    InvalidSignalError,
    LoggingEventPublisher,
    NotFoundError,
    UnifiedOrchestrationService,
    ValidationError,
    get_orchestration_service,
    reset_orchestration_service,
)


class TestInMemoryOrchestrationRepository:
    @pytest.fixture
    def repo(self) -> InMemoryOrchestrationRepository:
        return InMemoryOrchestrationRepository()

    @pytest.mark.asyncio
    async def test_save_and_get_snapshot(self, repo: InMemoryOrchestrationRepository) -> None:
        snap = OrchestrationSnapshot(run_id="r1", workspace="/tmp")
        await repo.save_snapshot(snap)
        fetched = await repo.get_snapshot("r1")
        assert fetched is not None
        assert fetched.run_id == "r1"

    @pytest.mark.asyncio
    async def test_get_snapshot_missing(self, repo: InMemoryOrchestrationRepository) -> None:
        assert await repo.get_snapshot("missing") is None

    @pytest.mark.asyncio
    async def test_list_snapshots(self, repo: InMemoryOrchestrationRepository) -> None:
        await repo.save_snapshot(OrchestrationSnapshot(run_id="r1", workspace="/tmp"))
        await repo.save_snapshot(OrchestrationSnapshot(run_id="r2", workspace="/tmp", status=RunStatus.COMPLETED))
        results = await repo.list_snapshots(workspace="/tmp")
        assert len(results) == 2
        results_filtered = await repo.list_snapshots(status="completed")
        assert len(results_filtered) == 1

    @pytest.mark.asyncio
    async def test_save_and_get_request(self, repo: InMemoryOrchestrationRepository) -> None:
        req = OrchestrationRunRequest(run_id="r1", workspace=Path("/tmp"), mode=OrchestrationMode.WORKFLOW)
        await repo.save_request(req)
        fetched = await repo.get_request("r1")
        assert fetched is not None
        assert fetched.run_id == "r1"


class TestLoggingEventPublisher:
    @pytest.mark.asyncio
    async def test_publish_snapshot(self, caplog: pytest.LogCaptureFixture) -> None:
        pub = LoggingEventPublisher()
        snap = OrchestrationSnapshot(run_id="r1", workspace="/tmp")
        with caplog.at_level("INFO"):
            await pub.publish_snapshot("r1", snap)
        assert "Orchestration:r1" in caplog.text

    @pytest.mark.asyncio
    async def test_publish_event(self, caplog: pytest.LogCaptureFixture) -> None:
        pub = LoggingEventPublisher()
        with caplog.at_level("INFO"):
            await pub.publish_event("r1", "test_event", {"k": "v"})
        assert "test_event" in caplog.text


class TestUnifiedOrchestrationService:
    @pytest.fixture
    def service(self) -> UnifiedOrchestrationService:
        return UnifiedOrchestrationService()

    @pytest.mark.asyncio
    async def test_submit_run_validation_error(self, service: UnifiedOrchestrationService) -> None:
        req = OrchestrationRunRequest(run_id="", workspace=Path("/tmp"), mode=OrchestrationMode.WORKFLOW)
        with pytest.raises(ValidationError):
            await service.submit_run(req)

    @pytest.mark.asyncio
    async def test_submit_run_with_pipeline_spec(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        snap = await service.submit_run(req)
        assert snap.run_id == "r1"
        assert snap.status == RunStatus.PENDING

    @pytest.mark.asyncio
    async def test_query_run(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        snap = await service.query_run("r1")
        assert snap is not None
        assert snap.run_id == "r1"

    @pytest.mark.asyncio
    async def test_query_run_tasks(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        result = await service.query_run_tasks("r1")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_cancel_run(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        snap = await service.cancel_run("r1")
        assert snap.status == RunStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_signal_run_not_found(self, service: UnifiedOrchestrationService) -> None:
        with pytest.raises(NotFoundError):
            await service.signal_run("missing", SignalRequest(signal=OrchestrationSignal.CANCEL))

    @pytest.mark.asyncio
    async def test_signal_run_invalid_signal(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        with pytest.raises(InvalidSignalError):
            await service.signal_run("r1", SignalRequest(signal=OrchestrationSignal.PAUSE))

    @pytest.mark.asyncio
    async def test_list_runs(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        runs = await service.list_runs(workspace=str(tmp_path))
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_get_ui_state(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        spec = PipelineSpec(tasks=[PipelineTask(task_id="t1", role_entry=RoleEntrySpec(role_id="pm"))])
        req = OrchestrationRunRequest(run_id="r1", workspace=tmp_path, mode=OrchestrationMode.WORKFLOW, pipeline_spec=spec)
        await service.submit_run(req)
        ui_state = await service.get_ui_state("r1")
        assert ui_state is not None
        assert ui_state["run_id"] == "r1"

    def test_coerce_positive_int(self, service: UnifiedOrchestrationService) -> None:
        assert service._coerce_positive_int("5", 1) == 5
        assert service._coerce_positive_int("abc", 10) == 10
        assert service._coerce_positive_int(-3, 10) == 10

    def test_canonicalize_workflow_request(self, service: UnifiedOrchestrationService, tmp_path: Path) -> None:
        req = OrchestrationRunRequest(
            run_id="r1",
            workspace=tmp_path,
            mode=OrchestrationMode.WORKFLOW,
            role_entries=[RoleEntrySpec(role_id="pm")],
        )
        canonical = service._canonicalize_workflow_request(req)
        assert canonical.pipeline_spec is not None
        assert len(canonical.pipeline_spec.tasks) == 1


class TestSingleton:
    @pytest.mark.asyncio
    async def test_get_and_reset(self) -> None:
        reset_orchestration_service()
        svc = await get_orchestration_service()
        assert isinstance(svc, UnifiedOrchestrationService)
        reset_orchestration_service()
