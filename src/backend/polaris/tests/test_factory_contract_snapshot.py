"""Factory API contract snapshot tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRunService, StageResult
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.routers import factory as factory_router_module


class FakeStageExecutor:
    async def execute(self, stage, run, context):
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(temp_workspace: Path) -> FactoryRunService:
    return FactoryRunService(temp_workspace, executor=FakeStageExecutor())


_TEST_TOKEN = "test-factory-contract-snapshot-token-2026"


@pytest.fixture
def client(temp_workspace: Path, service: FactoryRunService, monkeypatch: pytest.MonkeyPatch):
    """Create a test client with a known auth token for security-hardened endpoints."""
    monkeypatch.setenv("KERNELONE_TOKEN", _TEST_TOKEN)
    app = create_app(Settings(workspace=temp_workspace))
    monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)

    with TestClient(app) as test_client:
        yield test_client


def test_factory_status_response_contract_is_stable(client: TestClient, temp_workspace: Path) -> None:
    response = client.post(
        "/v2/factory/runs",
        json={
            "workspace": str(temp_workspace),
            "start_from": "architect",
            "directive": "Build a release candidate",
            "run_director": True,
        },
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert set(payload.keys()) == {
        "run_id",
        "phase",
        "status",
        "current_stage",
        "last_successful_stage",
        "progress",
        "roles",
        "gates",
        "failure",
        "created_at",
        "started_at",
        "updated_at",
        "completed_at",
        "summary_md",
    }


def test_factory_stream_event_contract_is_stable(client: TestClient, service: FactoryRunService) -> None:
    run = asyncio.run(service.create_run(FactoryConfig(name="snapshot-run", stages=["pm_planning"])))
    asyncio.run(service.start_run(run.id))
    asyncio.run(service.execute_stage(run.id, "pm_planning"))
    asyncio.run(service.complete_run(run.id, success=True))

    frames: list[str] = []
    with client.stream(
        "GET", f"/v2/factory/runs/{run.id}/stream", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            frames.append(line)

    event_names = [line.split(": ", 1)[1] for line in frames if line.startswith("event: ")]
    assert "status" in event_names
    assert "event" in event_names
    assert event_names[-1] == "done"

    done_index = frames.index("event: done")
    done_payload_line = frames[done_index + 1]
    assert done_payload_line.startswith("data: ")
    done_payload = json.loads(done_payload_line[6:])
    assert set(done_payload.keys()) == {
        "run_id",
        "phase",
        "status",
        "current_stage",
        "last_successful_stage",
        "progress",
        "roles",
        "gates",
        "failure",
        "created_at",
        "started_at",
        "updated_at",
        "completed_at",
        "summary_md",
    }
