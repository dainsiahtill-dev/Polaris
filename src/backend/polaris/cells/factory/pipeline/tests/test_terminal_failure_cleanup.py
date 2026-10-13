from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.tests.test_terminal_failure_cleanup import _failed_read_batch
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRunService,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.tests.test_factory_workspace_admission import (
    _create_active_factory_child,
    _settle_factory_child,
    _SuccessfulStageExecutor,
)


async def _failed_run(workspace: Path) -> tuple[FactoryRunService, str]:
    workspace.mkdir()
    service = FactoryRunService(workspace, executor=_SuccessfulStageExecutor())
    run = await service.create_run(FactoryConfig(name="failed-read-cleanup", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, task_id, identity = _create_active_factory_child(workspace, factory_run_id=run.id)
    assert _settle_factory_child(runtime, identity, outcome="failed", summary="policy denied read")["success"]
    _failed_read_batch(
        workspace, factory_run_id=run.id, run_id="director-child-run", task_id=str(task_id), append_task=False
    )
    return service, run.id


@pytest.mark.asyncio
async def test_failed_read_closeout_releases_real_factory_lease_without_qa(tmp_path: Path) -> None:
    service, run_id = await _failed_run(tmp_path / "workspace")
    terminal = await service.complete_run(run_id, success=False)
    assert terminal.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert terminal.metadata["factory_run_ledger_settlement_barrier"]["closed"] is True
    assert terminal.metadata["factory_run_ledger_settlement_barrier"]["passed"] is False
    assert "required_evidence_missing" in terminal.metadata["factory_run_ledger_settlement_barrier"]["blocking_reasons"]


@pytest.mark.asyncio
async def test_restarted_failed_cleanup_rejects_empty_process_as_physical_proof(tmp_path: Path) -> None:
    service, run_id = await _failed_run(tmp_path / "workspace")
    run = await service.get_run(run_id)
    assert run is not None
    run.status = FactoryRunStatus.FAILED
    await service.store.save_run(run)
    restarted = FactoryRunService(service.workspace, executor=_SuccessfulStageExecutor())
    terminal = await restarted.settle_terminal_run(run_id)
    assert terminal.metadata["factory_workspace_run_lease"]["state"] == "draining"
    assert (
        terminal.metadata["factory_workspace_run_drain_conflict"]["code"] == "factory_physical_attempt_replay_required"
    )


@pytest.mark.asyncio
async def test_failed_missing_qa_does_not_force_fail_live_child_session(tmp_path: Path) -> None:
    service, run_id = await _failed_run(tmp_path / "workspace")
    runtime, task_id, identity = _create_active_factory_child(service.workspace, factory_run_id=run_id)
    del identity
    terminal = await service.complete_run(run_id, success=False)
    assert terminal.metadata["factory_workspace_run_lease"]["state"] == "draining"
    assert (
        terminal.metadata["factory_workspace_run_drain_conflict"]["code"]
        == "factory_workspace_run_child_session_inflight"
    )
    assert any(
        str(row["id"]) == str(task_id) and row["status"] == "in_progress" for row in runtime.list_observable_task_rows()
    )
