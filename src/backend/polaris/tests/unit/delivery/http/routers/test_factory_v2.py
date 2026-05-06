"""Tests for Polaris v2 factory router.

Covers GET /v2/factory/runs, POST /v2/factory/runs,
GET /v2/factory/runs/{run_id}, GET /v2/factory/runs/{run_id}/events,
GET /v2/factory/runs/{run_id}/audit-bundle,
GET /v2/factory/runs/{run_id}/stream,
POST /v2/factory/runs/{run_id}/control,
and GET /v2/factory/runs/{run_id}/artifacts.
External services are mocked to avoid storage and orchestration dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_factory_run(
    run_id: str = "factory_test123",
    status: str = "running",
    stages: list[str] | None = None,
    stages_completed: list[str] | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a FactoryRun."""
    from polaris.cells.factory.pipeline.public.service import FactoryRunStatus

    run = MagicMock()
    run.id = run_id
    run.status = FactoryRunStatus(status)
    run.config = MagicMock()
    run.config.stages = stages or ["pm_planning", "quality_gate"]
    run.config.name = "Test Run"
    run.config.description = "test"
    run.stages_completed = stages_completed or []
    run.stages_failed = []
    run.recovery_point = None
    run.created_at = "2024-01-01T00:00:00+00:00"
    run.started_at = run.created_at
    run.updated_at = run.created_at
    run.completed_at = None
    run.metadata = metadata or {
        "current_stage": "pm_planning",
        "last_successful_stage": None,
        "last_failed_stage": None,
        "failure": None,
    }
    return run


# ---------------------------------------------------------------------------
# GET /v2/factory/runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_factory_runs_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs should return a paginated list of runs."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs = AsyncMock(
            return_value=[
                {
                    "id": "factory_abc",
                    "name": "Test Run",
                    "status": "running",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "current_stage": "pm_planning",
                    "last_successful_stage": None,
                    "stages_completed": 0,
                    "stages_failed": 0,
                }
            ]
        )
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.get("/v2/factory/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["runs"]) == 1
        assert data["runs"][0]["run_id"] == "factory_abc"
        mock_svc.list_runs.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_factory_runs_empty(client: AsyncClient) -> None:
    """GET /v2/factory/runs should return empty list when no runs exist."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs = AsyncMock(return_value=[])

        response = await client.get("/v2/factory/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["runs"] == []


# ---------------------------------------------------------------------------
# POST /v2/factory/runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_factory_run_success(client: AsyncClient) -> None:
    """POST /v2/factory/runs should create and start a factory run."""
    run = _make_factory_run(run_id="factory_new123", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.factory.create_task_with_context",
        ),
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_run = AsyncMock(return_value=run)
        mock_svc.start_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "pm",
                "directive": "Build a thing",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_new123"
        assert data["status"] == "running"
        mock_svc.create_run.assert_awaited_once()
        mock_svc.start_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_factory_run_missing_workspace(client: AsyncClient) -> None:
    """POST /v2/factory/runs when workspace is not configured should return 400."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with patch(
        "polaris.delivery.http.routers.factory._resolve_workspace",
        side_effect=StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace not configured",
        ),
    ):
        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "auto",
                "directive": "test",
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "WORKSPACE_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_status_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id} should return run status."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.get("/v2/factory/runs/factory_abc")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "running"
        assert "roles" in data


@pytest.mark.asyncio
async def test_get_factory_run_status_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id} should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_events_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/events should return events."""
    run = _make_factory_run(run_id="factory_abc")
    events = [
        {"type": "started", "message": "Run started"},
        {"type": "stage_started", "stage": "pm_planning"},
    ]

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=events)

        response = await client.get("/v2/factory/runs/factory_abc/events")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 2
        assert data["events"][0]["type"] == "started"


@pytest.mark.asyncio
async def test_get_factory_run_events_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/events should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/events")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/audit-bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_audit_bundle_success(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/audit-bundle should return audit bundle."""
    run = _make_factory_run(run_id="factory_abc", status="completed", stages_completed=["quality_gate"])
    run.metadata["summary_md"] = "# Summary"
    run.metadata["summary_json"] = {"ok": True}

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.json").write_text('{"passed": true}')

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=[])
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/audit-bundle")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "completed"
        assert "evidence_counts" in data


@pytest.mark.asyncio
async def test_get_factory_run_audit_bundle_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/audit-bundle should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/audit-bundle")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_factory_run_events_headers(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/stream should return SSE headers.

    Full SSE consumption is skipped because the endpoint uses an async
    generator with an infinite polling loop.
    """
    run = _make_factory_run(run_id="factory_abc", status="completed")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.create_sse_jetstream_consumer",
        ) as mock_consumer_factory,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=[])

        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        mock_consumer.connect = AsyncMock(return_value=False)
        mock_consumer_factory.return_value = mock_consumer

        response = await client.get("/v2/factory/runs/factory_abc/stream")
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/event-stream")
        assert response.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_stream_factory_run_events_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/stream should 404 when run missing and JetStream fails."""
    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.create_sse_jetstream_consumer",
        ) as mock_consumer_factory,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        mock_consumer.connect = AsyncMock(return_value=False)
        mock_consumer_factory.return_value = mock_consumer

        response = await client.get("/v2/factory/runs/missing/stream")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v2/factory/runs/{run_id}/control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_factory_run_cancel(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control with cancel should cancel run."""
    run = _make_factory_run(run_id="factory_abc", status="running")
    cancelled = _make_factory_run(run_id="factory_abc", status="cancelled")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.cancel_run = AsyncMock(return_value=cancelled)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "cancel", "reason": "user request"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "cancelled"
        mock_svc.cancel_run.assert_awaited_once_with("factory_abc", "user request")


@pytest.mark.asyncio
async def test_control_factory_run_not_found(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.post(
            "/v2/factory/runs/missing/control",
            json={"action": "cancel"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_control_factory_run_unsupported_action(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control with unsupported action should 501."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "pause"},
        )
        assert response.status_code == 501
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"
        assert "pause" in data["error"]["message"]


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_success(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should list artifact files."""
    run = _make_factory_run(run_id="factory_abc", status="completed")
    run.metadata["summary_md"] = "# Summary"
    run.metadata["summary_json"] = {"ok": True}

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.json").write_text('{"passed": true}')

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/artifacts")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["name"] == "report.json"


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/artifacts")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_empty(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should return empty list when no artifacts."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/artifacts")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["artifacts"] == []
