"""Tests for Polaris v2 History router.

Covers History v2 endpoints: runs, run manifest, run events,
task snapshots, and factory snapshots.
External archive services are mocked to avoid filesystem dependencies.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["KERNELONE_RATE_LIMIT_ENABLED"] = "false"
os.environ["KERNELONE_METRICS_ENABLED"] = "false"

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "polaris.kernelone.storage.io_paths.resolve_storage_roots",
            return_value=MagicMock(runtime_root="/tmp/polaris_test_runtime"),
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_v2_history_runs_runtime_only(client: AsyncClient) -> None:
    """GET /v2/history/runs should return runtime runs when source=runtime."""
    with (
        patch(
            "polaris.delivery.http.routers.history.os.path.isdir",
            return_value=False,
        ),
        patch(
            "polaris.delivery.http.routers.history.list_archived_runs",
            return_value=[],
        ),
    ):
        response = await client.get("/v2/history/runs?source=runtime")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_v2_history_runs_archived_only(client: AsyncClient) -> None:
    """GET /v2/history/runs should return archived runs when source=archived."""
    with patch(
        "polaris.delivery.http.routers.history.list_archived_runs",
        return_value=[
            {
                "run_id": "run-1",
                "status": "completed",
                "archive_timestamp": 1234567890,
                "reason": "auto",
            },
        ],
    ):
        response = await client.get("/v2/history/runs?source=archived")
        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 1
        assert data["runs"][0]["id"] == "run-1"
        assert data["runs"][0]["source"] == "archived"
        assert data["total"] == 1


@pytest.mark.asyncio
async def test_v2_history_runs_with_pagination(client: AsyncClient) -> None:
    """GET /v2/history/runs should respect limit and offset."""
    with patch(
        "polaris.delivery.http.routers.history.list_archived_runs",
        return_value=[
            {"run_id": f"run-{i}", "status": "completed", "archive_timestamp": i, "reason": ""} for i in range(5)
        ],
    ):
        response = await client.get("/v2/history/runs?source=archived&limit=2&offset=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 2
        assert data["total"] == 5


@pytest.mark.asyncio
async def test_v2_history_run_manifest_found(client: AsyncClient) -> None:
    """GET /v2/history/runs/{run_id}/manifest should return manifest when found."""
    with patch(
        "polaris.delivery.http.routers.history.get_run_manifest",
        return_value={"run_id": "run-123", "tasks": []},
    ):
        response = await client.get("/v2/history/runs/run-123/manifest")
        assert response.status_code == 200
        data = response.json()
        assert data["manifest"]["run_id"] == "run-123"


@pytest.mark.asyncio
async def test_v2_history_run_manifest_not_found(client: AsyncClient) -> None:
    """GET /v2/history/runs/{run_id}/manifest should 404 when manifest missing."""
    with patch(
        "polaris.delivery.http.routers.history.get_run_manifest",
        return_value=None,
    ):
        response = await client.get("/v2/history/runs/run-999/manifest")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MANIFEST_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_history_run_manifest_server_error(client: AsyncClient) -> None:
    """GET /v2/history/runs/{run_id}/manifest should 500 on unexpected error."""
    with patch(
        "polaris.delivery.http.routers.history.get_run_manifest",
        side_effect=RuntimeError("db failure"),
    ):
        response = await client.get("/v2/history/runs/run-123/manifest")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_v2_history_run_events(client: AsyncClient) -> None:
    """GET /v2/history/runs/{run_id}/events should return events list."""
    with patch(
        "polaris.delivery.http.routers.history.get_run_events",
        return_value=[{"type": "start", "ts": 1}],
    ):
        response = await client.get("/v2/history/runs/run-123/events")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert len(data["events"]) == 1
        assert data["count"] == 1


@pytest.mark.asyncio
async def test_v2_history_run_events_server_error(client: AsyncClient) -> None:
    """GET /v2/history/runs/{run_id}/events should 500 on unexpected error."""
    with patch(
        "polaris.delivery.http.routers.history.get_run_events",
        side_effect=RuntimeError("decompress error"),
    ):
        response = await client.get("/v2/history/runs/run-123/events")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_v2_history_task_snapshots(client: AsyncClient) -> None:
    """GET /v2/history/tasks/snapshots should return task snapshots."""
    with patch(
        "polaris.delivery.http.routers.history.list_task_snapshots",
        return_value=[{"snapshot_id": "snap-1", "task_id": "task-1"}],
    ):
        response = await client.get("/v2/history/tasks/snapshots")
        assert response.status_code == 200
        data = response.json()
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["snapshot_id"] == "snap-1"
        assert data["total"] == 1


@pytest.mark.asyncio
async def test_v2_history_task_snapshots_server_error(client: AsyncClient) -> None:
    """GET /v2/history/tasks/snapshots should 500 on unexpected error."""
    with patch(
        "polaris.delivery.http.routers.history.list_task_snapshots",
        side_effect=RuntimeError("db failure"),
    ):
        response = await client.get("/v2/history/tasks/snapshots")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_v2_history_factory_snapshots(client: AsyncClient) -> None:
    """GET /v2/history/factory/snapshots should return factory runs."""
    with patch(
        "polaris.delivery.http.routers.history.list_factory_runs",
        return_value=[{"run_id": "factory-1", "status": "completed"}],
    ):
        response = await client.get("/v2/history/factory/snapshots")
        assert response.status_code == 200
        data = response.json()
        assert len(data["factory_runs"]) == 1
        assert data["factory_runs"][0]["run_id"] == "factory-1"
        assert data["total"] == 1


@pytest.mark.asyncio
async def test_v2_history_factory_snapshots_server_error(client: AsyncClient) -> None:
    """GET /v2/history/factory/snapshots should 500 on unexpected error."""
    with patch(
        "polaris.delivery.http.routers.history.list_factory_runs",
        side_effect=RuntimeError("db failure"),
    ):
        response = await client.get("/v2/history/factory/snapshots")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
