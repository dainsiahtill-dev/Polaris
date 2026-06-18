"""Factory API contract snapshot tests."""

from __future__ import annotations

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


def test_factory_stream_route_fails_closed_to_nat_jetstream(client: TestClient) -> None:
    response = client.get(
        "/v2/factory/runs/snapshot-run/stream",
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
    )

    assert response.status_code == 410
    assert response.headers.get("content-type", "").startswith("application/json")
    assert "text/event-stream" not in response.headers.get("content-type", "")
    body = response.json()
    assert body["error"]["code"] == "SSE_REMOVED"
    assert body["error"]["details"] == {
        "replacement": "/v2/ws/runtime",
        "transport": "nat-jetstream",
    }
