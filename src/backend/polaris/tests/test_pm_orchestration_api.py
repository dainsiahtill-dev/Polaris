"""Route tests for PM orchestration status endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    OrchestrationSnapshot,
    RunStatus,
    TaskPhase,
    TaskSnapshot,
)
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.v2 import pm as pm_router_module


@dataclass
class _FakeOrchestrationService:
    snapshot: OrchestrationSnapshot | None

    async def query_run(self, run_id: str) -> OrchestrationSnapshot | None:
        return self.snapshot


def test_pm_get_orchestration_uses_current_contract_without_snapshot_metadata(tmp_path: Path, monkeypatch) -> None:
    test_token = "test-pm-orchestration-token"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    snapshot = OrchestrationSnapshot(
        run_id="pm-demo-001",
        workspace=str(tmp_path),
        status=RunStatus.RUNNING,
        current_phase=TaskPhase.PLANNING,
        tasks={
            "task-0-pm": TaskSnapshot(
                task_id="task-0-pm",
                status=RunStatus.RUNNING,
                phase=TaskPhase.PLANNING,
                role_id="pm",
            )
        },
    )
    app = create_app(Settings(workspace=tmp_path))

    async def fake_get_orchestration_service() -> _FakeOrchestrationService:
        return _FakeOrchestrationService(snapshot)

    monkeypatch.setattr(pm_router_module, "get_orchestration_service", fake_get_orchestration_service)

    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as client:
        response = client.get("/v2/pm/runs/pm-demo-001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "pm-demo-001"
    assert payload["status"] == "running"
    assert payload["stage"] == "pm"
