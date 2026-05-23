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


@pytest.mark.asyncio
async def test_director_status_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director status projection should use the active desktop workspace path."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_capabilities(client: AsyncClient) -> None:
    """Director capabilities should be exposed on the canonical v2 route."""
    with patch(
        "polaris.domain.entities.capability.get_role_capabilities",
        return_value={"electron_workbench": ["read_files"], "workflow": ["execute_tests"]},
    ) as get_capabilities:
        response = await client.get("/v2/director/capabilities")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["role"] == "director"
    assert data["capabilities"] == {
        "electron_workbench": ["read_files"],
        "workflow": ["execute_tests"],
    }
    get_capabilities.assert_called_once_with("director")


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
async def test_director_list_tasks_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task projection should use workspace_path before stale workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_director = MagicMock()
    mock_director.config.workspace = "C:/Temp/Product"

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
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Product"


def test_director_debug_append_ignores_debug_log_failure() -> None:
    """Optional Director debug evidence should not leak filesystem failures."""
    with (
        patch(
            "polaris.delivery.http.v2.director.Path.open",
            side_effect=OSError("debug log locked"),
        ),
        patch("polaris.delivery.http.v2.director.logger.debug") as mock_debug,
    ):
        from polaris.delivery.http.v2.director import _append_debug

        _append_debug("test.event", {"ok": True})
        mock_debug.assert_called_once()


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
async def test_director_get_task_falls_back_to_projection(client: AsyncClient) -> None:
    """Director task detail should resolve workflow/projection rows after a local miss."""
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "projection-row-1",
                    "subject": "Workflow projected task",
                    "description": "Visible from workflow projection",
                    "status": "RUNNING",
                    "priority": "HIGH",
                    "claimed_by": "worker-projection",
                    "goal": "Keep detail panel in sync with listed tasks",
                    "acceptance": ["projection detail returned"],
                    "metadata": {
                        "pm_task_id": "PM-42",
                        "blueprint_id": "BP-42",
                    },
                },
            ],
        ),
    ):

        async def _resolve_projection_fallback(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_projection_fallback)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/PM-42")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "projection-row-1"
    assert data["subject"] == "Workflow projected task"
    assert data["status"] == "RUNNING"
    assert data["worker"] == "worker-projection"
    assert data["pm_task_id"] == "PM-42"
    assert data["blueprint_id"] == "BP-42"
    assert data["acceptance"] == ["projection detail returned"]


@pytest.mark.asyncio
async def test_director_get_task_projection_fallback_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task detail projection fallback should use the active workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "C:/Temp/Product"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
    ):

        async def _resolve_projection_fallback(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_projection_fallback)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/PM-404")

    assert response.status_code == 404
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_get_task_not_found(client: AsyncClient) -> None:
    """Director get task should 404 when task doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
    ):

        async def _resolve_get_task(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_get_task)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

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
async def test_director_cancel_task_returns_service_success_payload(client: AsyncClient) -> None:
    """Director cancel task should preserve a successful service payload."""
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(
        return_value={
            "ok": True,
            "task_id": "task-123",
            "status": "CANCELLED",
            "worker": "worker-1",
        }
    )
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel_payload(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel_payload)

        response = await client.post("/v2/director/tasks/task-123/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["task_id"] == "task-123"
        assert data["status"] == "CANCELLED"
        assert data["worker"] == "worker-1"


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


@pytest.mark.asyncio
async def test_director_cancel_task_respects_service_failure_payload(client: AsyncClient) -> None:
    """Director cancel task should not treat a non-empty ok=false payload as success."""
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(
        return_value={
            "ok": False,
            "error": "Task not found or not cancellable",
            "task_id": "task-123",
        }
    )
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel_failure_payload(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel_failure_payload)

        response = await client.post("/v2/director/tasks/task-123/cancel")
        assert response.status_code == 400
        assert response.json()["detail"] == "Task not found or not cancellable"


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
    mock_result.metadata = {"tasks_queued": 2, "task_ids": ["PM-1", "PM-2"]}

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
        assert data["tasks_queued"] == 2


@pytest.mark.asyncio
async def test_director_run_orchestration_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run should resolve omitted workspace through active desktop settings."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-active"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"
    mock_result.metadata = {"tasks_queued": 0}

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
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_service.execute_director_run.await_args
    assert kwargs["workspace"] == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_run_orchestration_preserves_explicit_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run should not override explicit non-dot API workspace values."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-explicit"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"
    mock_result.metadata = {"tasks_queued": 0}

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
                "workspace": "D:/Explicit/Product",
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_service.execute_director_run.await_args
    assert kwargs["workspace"] == "D:/Explicit/Product"
    assert response.json()["workspace"] == "D:/Explicit/Product"


@pytest.mark.asyncio
async def test_director_run_orchestration_serial_mode(client: AsyncClient) -> None:
    """Director run orchestration should support serial mode."""
    mock_result = MagicMock()
    mock_result.run_id = "run-abc"
    mock_result.status = "running"
    mock_result.message = None
    mock_result.metadata = None

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
    mock_result.metadata = None

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
        assert kwargs["tasks"] == ["PM-42"]
        assert kwargs["options"]["task_id"] == "PM-42"
        assert kwargs["options"]["task_filter"] == "PM-42"
        assert response.json()["tasks_queued"] == 1


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
async def test_director_cancel_orchestration_run(client: AsyncClient) -> None:
    """Director cancel orchestration should call the runtime cancel path."""
    mock_current_snapshot = MagicMock()
    mock_current_snapshot.run_id = "run-789"
    mock_current_snapshot.status.value = "running"
    mock_current_snapshot.workspace = "."
    mock_current_snapshot.tasks = {"task-1": MagicMock()}

    mock_cancelled_snapshot = MagicMock()
    mock_cancelled_snapshot.run_id = "run-789"
    mock_cancelled_snapshot.status.value = "cancelled"
    mock_cancelled_snapshot.workspace = "."
    mock_cancelled_snapshot.tasks = {"task-1": MagicMock()}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_current_snapshot)
        mock_service.cancel_run = AsyncMock(return_value=mock_cancelled_snapshot)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "cancelled"
        assert data["tasks_queued"] == 1
        mock_service.query_run.assert_awaited_once_with("run-789")
        mock_service.cancel_run.assert_awaited_once_with("run-789")


@pytest.mark.asyncio
async def test_director_cancel_orchestration_terminal_run_is_idempotent(client: AsyncClient) -> None:
    """Director cancel orchestration should return terminal snapshots unchanged."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "completed"
        mock_service.query_run.assert_awaited_once_with("run-789")
        mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_not_found(client: AsyncClient) -> None:
    """Director cancel orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/nonexistent/cancel")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


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
