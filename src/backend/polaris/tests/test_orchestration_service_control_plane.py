"""Control-plane responsiveness tests for UnifiedOrchestrationService."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    OrchestrationMode,
    OrchestrationRunRequest,
    RoleEntrySpec,
)
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
    UnifiedOrchestrationService,
)


async def _wait_for_run_status(
    service: UnifiedOrchestrationService,
    run_id: str,
    expected_status: str,
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.02,
):
    deadline = time.perf_counter() + timeout_seconds
    latest = await service.query_run(run_id)
    while time.perf_counter() < deadline:
        latest = await service.query_run(run_id)
        if latest is not None and latest.status.value == expected_status:
            return latest
        await asyncio.sleep(poll_interval_seconds)
    return latest


class _BlockingAdapter:
    role_id = "pm"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    async def execute(self, task_id: str, input_data, context):
        await asyncio.sleep(0.2)
        return {"success": True, "task_id": task_id}


class _FailingAdapter:
    role_id = "pm"

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    async def execute(self, task_id: str, input_data, context):
        del input_data, context
        return {"success": False, "task_id": task_id, "error": "simulated adapter failure"}


@pytest.mark.asyncio
async def test_query_run_remains_responsive_while_adapter_executes_in_background(tmp_path: Path) -> None:
    service = UnifiedOrchestrationService(role_adapters=[_BlockingAdapter(str(tmp_path))])
    request = OrchestrationRunRequest(
        run_id="pm-control-plane-001",
        workspace=tmp_path,
        mode=OrchestrationMode.WORKFLOW,
        role_entries=[RoleEntrySpec(role_id="pm", input="execute control-plane test")],
    )

    snapshot = await service.submit_run(request)
    assert snapshot.run_id == "pm-control-plane-001"

    await asyncio.sleep(0.01)

    started = time.perf_counter()
    queried = await asyncio.wait_for(service.query_run(request.run_id), timeout=0.05)
    elapsed = time.perf_counter() - started

    assert queried is not None
    assert queried.run_id == request.run_id
    assert elapsed < 0.1

    final_snapshot = await _wait_for_run_status(
        service,
        request.run_id,
        "completed",
    )
    assert final_snapshot is not None
    assert final_snapshot.status.value == "completed"


@pytest.mark.asyncio
async def test_workflow_marks_run_failed_when_adapter_returns_success_false(tmp_path: Path) -> None:
    service = UnifiedOrchestrationService(role_adapters=[_FailingAdapter(str(tmp_path))])
    request = OrchestrationRunRequest(
        run_id="pm-control-plane-002",
        workspace=tmp_path,
        mode=OrchestrationMode.WORKFLOW,
        role_entries=[RoleEntrySpec(role_id="pm", input="execute failing test")],
    )

    await service.submit_run(request)
    snapshot = await _wait_for_run_status(
        service,
        request.run_id,
        "failed",
    )
    assert snapshot is not None
    assert snapshot.status.value == "failed"
