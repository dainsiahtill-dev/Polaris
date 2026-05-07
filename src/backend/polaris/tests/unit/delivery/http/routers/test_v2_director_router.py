"""Tests for Polaris v2 Director router.

Covers Director v2 endpoints: start, stop, status, tasks (create/list/get/cancel),
workers (list/get), llm-events, cache-stats, cache-clear, token-budget-stats,
run orchestration, and get orchestration.
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
# Director Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_start_stop(client: AsyncClient) -> None:
    """Director start and stop should delegate to DirectorService."""
    mock_director = MagicMock()
    mock_director.start = AsyncMock()
    mock_director.stop = AsyncMock()
    mock_director.state.name = "RUNNING"
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_start_stop(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_start_stop)

        start_resp = await client.post("/v2/director/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["ok"] is True
        assert start_resp.json()["state"] == "RUNNING"

        stop_resp = await client.post("/v2/director/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_director_status(client: AsyncClient) -> None:
    """Director status should return local role state via projection."""
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": True, "status": {"state": "ACTIVE"}}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["projection_source"] == "director_local"
        assert data["running"] is True
        assert data["state"] == "ACTIVE"
        assert data["status"]["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_director_status_auto_uses_merged_projection(client: AsyncClient) -> None:
    """Director status can expose workflow-aware merged state when requested."""
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "COMPLETED"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status?source=auto")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["projection_source"] == "director_merged"
        assert data["state"] == "COMPLETED"


# ---------------------------------------------------------------------------
# Task Management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_create_task(client: AsyncClient) -> None:
    """Director create task should return task response."""
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_task.subject = "Test task"
    mock_task.description = "Description"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.MEDIUM
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {}

    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock(return_value=mock_task)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_create(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_create)

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Test task",
                "description": "Description",
                "priority": "MEDIUM",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-123"
        assert data["subject"] == "Test task"
        assert data["status"] == "PENDING"


@pytest.mark.asyncio
async def test_director_create_task_with_command(client: AsyncClient) -> None:
    """Director create task should accept command field."""
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-456"
    mock_task.subject = "Run tests"
    mock_task.description = ""
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {"command": "pytest"}

    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock(return_value=mock_task)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Run tests",
                "command": "pytest -x",
                "priority": "HIGH",
                "blocked_by": ["task-123"],
                "timeout_seconds": 300,
                "metadata": {"command": "pytest"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-456"
        assert data["priority"] == "HIGH"


@pytest.mark.asyncio
async def test_director_list_tasks(client: AsyncClient) -> None:
    """Director list tasks should return task list via projection."""
    mock_director = MagicMock()
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data == []


@pytest.mark.asyncio
async def test_director_list_tasks_with_status_filter(client: AsyncClient) -> None:
    """Director list tasks should filter by status via projection."""
    mock_director = MagicMock()
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "t1",
                    "subject": "Task 1",
                    "status": "PENDING",
                    "priority": "HIGH",
                    "claimed_by": None,
                    "blueprint_id": "bp-1",
                    "runtime_blueprint_path": "runtime/contracts/bp-1.json",
                    "metadata": {"pm_task_id": "PM-1"},
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?status=PENDING")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "PENDING"
        assert data[0]["blueprint_id"] == "bp-1"
        assert data[0]["runtime_blueprint_path"] == "runtime/contracts/bp-1.json"
        assert data[0]["metadata"]["pm_task_id"] == "PM-1"


@pytest.mark.asyncio
async def test_director_get_task_found(client: AsyncClient) -> None:
    """Director get task should return task when found."""
    from polaris.domain.entities import TaskPriority, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_task.subject = "Found task"
    mock_task.description = "Desc"
    mock_task.status = TaskStatus.RUNNING
    mock_task.priority = TaskPriority.LOW
    mock_task.claimed_by = "worker-1"
    mock_task.result = None
    mock_task.metadata = {}

    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=mock_task)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/tasks/task-123")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-123"
        assert data["claimed_by"] == "worker-1"


@pytest.mark.asyncio
async def test_director_get_task_not_found(client: AsyncClient) -> None:
    """Director get task should 404 when task doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_get_task(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_get_task)

        response = await client.get("/v2/director/tasks/nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_director_cancel_task_success(client: AsyncClient) -> None:
    """Director cancel task should return ok when successful."""
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=True)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel)

        response = await client.post("/v2/director/tasks/task-123/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["task_id"] == "task-123"


@pytest.mark.asyncio
async def test_director_cancel_task_fails(client: AsyncClient) -> None:
    """Director cancel task should 400 when cancellation fails."""
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=False)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.post("/v2/director/tasks/task-123/cancel")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_list_workers(client: AsyncClient) -> None:
    """Director list workers should return worker list."""
    mock_worker = MagicMock()
    mock_worker.to_dict.return_value = {"id": "worker-1", "status": "idle"}

    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[mock_worker])
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/workers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "worker-1"


@pytest.mark.asyncio
async def test_director_get_worker_found(client: AsyncClient) -> None:
    """Director get worker should return worker when found."""
    mock_worker = MagicMock()
    mock_worker.to_dict.return_value = {"id": "worker-1", "status": "busy", "task_id": "task-1"}

    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=mock_worker)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/workers/worker-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "worker-1"


@pytest.mark.asyncio
async def test_director_get_worker_not_found(client: AsyncClient) -> None:
    """Director get worker should 404 when worker doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/workers/nonexistent")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# LLM Events / Cache / Token Budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_task_llm_events(client: AsyncClient) -> None:
    """Get task LLM events should return events for a specific task."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_start"
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-1",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/tasks/task-1/llm-events?run_id=run-1")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        assert data["stats"]["total"] == 1
        assert data["stats"]["call_start"] == 1


@pytest.mark.asyncio
async def test_director_global_llm_events(client: AsyncClient) -> None:
    """Get global LLM events should return all events."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_error"
    mock_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-1",
        "role": "director",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/llm-events?role=director")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "llm_error"


@pytest.mark.asyncio
async def test_director_cache_stats(client: AsyncClient) -> None:
    """Get Director cache stats should return cache statistics."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 50, "misses": 10, "size": 60}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/director/cache-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == 50
        assert data["misses"] == 10


@pytest.mark.asyncio
async def test_director_cache_clear(client: AsyncClient) -> None:
    """Clear Director cache should return success."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        response = await client.post("/v2/director/cache-clear")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_cache.clear.assert_called_once()


@pytest.mark.asyncio
async def test_director_token_budget_stats(client: AsyncClient) -> None:
    """Get Director token budget stats should return budget information."""
    with patch(
        "polaris.delivery.http.v2.director.get_global_token_budget",
    ) as mock_get_budget:
        mock_budget = MagicMock()
        mock_budget.get_stats.return_value = {
            "total_budget": 50000,
            "used_tokens": 2500,
            "remaining": 47500,
        }
        mock_get_budget.return_value = mock_budget

        response = await client.get("/v2/director/token-budget-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_budget"] == 50000
        assert data["used_tokens"] == 2500


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_run_orchestration(client: AsyncClient) -> None:
    """Director run orchestration should create a run."""
    mock_result = MagicMock()
    mock_result.run_id = "run-789"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "running"
        assert data["workspace"] == "."


@pytest.mark.asyncio
async def test_director_run_orchestration_serial_mode(client: AsyncClient) -> None:
    """Director run orchestration should support serial mode."""
    mock_result = MagicMock()
    mock_result.run_id = "run-abc"
    mock_result.status = "running"
    mock_result.message = None

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "serial",
                "task_filter": "priority:high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-abc"
        assert "serial" in data["message"]


@pytest.mark.asyncio
async def test_director_run_orchestration_accepts_task_id(client: AsyncClient) -> None:
    """Director run orchestration should forward selected task id into options."""
    mock_result = MagicMock()
    mock_result.run_id = "run-task"
    mock_result.status = "running"
    mock_result.message = "Director started for selected task"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
                "task_id": "PM-42",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["options"]["task_id"] == "PM-42"
        assert kwargs["options"]["task_filter"] == "PM-42"


@pytest.mark.asyncio
async def test_director_get_orchestration_found(client: AsyncClient) -> None:
    """Director get orchestration should return run details."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "running"


@pytest.mark.asyncio
async def test_director_get_orchestration_not_found(client: AsyncClient) -> None:
    """Director get orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/nonexistent")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_get_orchestration_server_error(client: AsyncClient) -> None:
    """Director get orchestration should 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(side_effect=RuntimeError("db failure"))
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789")
        assert response.status_code == 500
        assert "internal error" in response.json()["detail"]
