"""Tests for Polaris v2 PM router.

Covers PM v2 endpoints: run_once, start, start_loop, stop, status, run,
get orchestration, llm-events, cache-stats, cache-clear, token-budget-stats.
External services are mocked to avoid DI container and LLM dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# PM Service Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_run_once(client: AsyncClient) -> None:
    """PM run_once should execute single iteration."""
    mock_pm = MagicMock()
    mock_pm.run_once = AsyncMock(return_value={"ok": True, "result": "done"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/run_once")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["result"] == "done"


@pytest.mark.asyncio
async def test_pm_start(client: AsyncClient) -> None:
    """PM start should begin loop mode."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["state"] == "RUNNING"


@pytest.mark.asyncio
async def test_pm_start_with_resume(client: AsyncClient) -> None:
    """PM start with resume flag should pass it through."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start?resume=true")
        assert response.status_code == 200
        mock_pm.start_loop.assert_called_once_with(resume=True)


@pytest.mark.asyncio
async def test_pm_start_loop_deprecated(client: AsyncClient) -> None:
    """PM start_loop (deprecated) should still work."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start_loop")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True


@pytest.mark.asyncio
async def test_pm_stop(client: AsyncClient) -> None:
    """PM stop should halt the service."""
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock(return_value={"ok": True, "state": "STOPPED"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["state"] == "STOPPED"


@pytest.mark.asyncio
async def test_pm_stop_with_graceful_timeout(client: AsyncClient) -> None:
    """PM stop should accept graceful timeout parameters."""
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock(return_value={"ok": True, "state": "STOPPED"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/stop?graceful=true&graceful_timeout=10.0")
        assert response.status_code == 200
        mock_pm.stop.assert_called_once_with(graceful=True, graceful_timeout=10.0)


@pytest.mark.asyncio
async def test_pm_status(client: AsyncClient) -> None:
    """PM status should return current service status."""
    mock_pm = MagicMock()
    mock_pm.get_status.return_value = {"running": False, "state": "IDLE", "iterations": 0}

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.get("/v2/pm/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["state"] == "IDLE"


# ---------------------------------------------------------------------------
# PM Orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_run_orchestration(client: AsyncClient) -> None:
    """PM run orchestration should create a run via OrchestrationCommandService."""
    mock_result = MagicMock()
    mock_result.run_id = "run-123"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "test directive",
                "stage": "pm",
                "run_director": False,
                "director_iterations": 2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "running"
        assert data["stage"] == "pm"


@pytest.mark.asyncio
async def test_pm_run_orchestration_with_director(client: AsyncClient) -> None:
    """PM run orchestration with run_director enabled."""
    mock_result = MagicMock()
    mock_result.run_id = "run-456"
    mock_result.status = "running"
    mock_result.message = "PM architect run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "build a login page",
                "stage": "architect",
                "run_director": True,
                "director_iterations": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-456"
        assert data["stage"] == "architect"


@pytest.mark.asyncio
async def test_pm_get_orchestration_found(client: AsyncClient) -> None:
    """PM get orchestration should return run details."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.current_phase.value = "done"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_pm_get_orchestration_not_found(client: AsyncClient) -> None:
    """PM get orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/nonexistent")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pm_get_orchestration_server_error(client: AsyncClient) -> None:
    """PM get orchestration should 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(side_effect=RuntimeError("db failure"))
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123")
        assert response.status_code == 500
        assert "internal error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# LLM Events / Cache / Token Budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_llm_events(client: AsyncClient) -> None:
    """Get PM LLM events should return events."""
    mock_event = MagicMock()
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-1",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?run_id=run-1")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-1"
        assert len(data["events"]) == 1
        assert data["count"] == 1


@pytest.mark.asyncio
async def test_pm_llm_events_with_task_filter(client: AsyncClient) -> None:
    """Get PM LLM events should filter by task_id."""
    mock_event = MagicMock()
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "run_id": "run-1",
        "task_id": "task-1",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?run_id=run-1&task_id=task-1&limit=50")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        mock_emitter.get_events.assert_called_once_with(
            run_id="run-1",
            task_id="task-1",
            role="pm",
            limit=50,
        )


@pytest.mark.asyncio
async def test_pm_cache_stats(client: AsyncClient) -> None:
    """Get PM cache stats should return cache statistics."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 100, "misses": 20, "size": 120}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/pm/cache-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == 100
        assert data["misses"] == 20


@pytest.mark.asyncio
async def test_pm_cache_clear(client: AsyncClient) -> None:
    """Clear PM cache should return success."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        response = await client.post("/v2/pm/cache-clear")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_cache.clear.assert_called_once()


@pytest.mark.asyncio
async def test_pm_token_budget_stats(client: AsyncClient) -> None:
    """Get PM token budget stats should return budget information."""
    with patch(
        "polaris.delivery.http.v2.pm.get_global_token_budget",
    ) as mock_get_budget:
        mock_budget = MagicMock()
        mock_budget.get_stats.return_value = {
            "total_budget": 100000,
            "used_tokens": 5000,
            "remaining": 95000,
        }
        mock_get_budget.return_value = mock_budget

        response = await client.get("/v2/pm/token-budget-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_budget"] == 100000
        assert data["used_tokens"] == 5000
