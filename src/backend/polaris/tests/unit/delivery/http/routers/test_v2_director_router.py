"""Tests for Polaris v2 Director router.

Covers Director v2 endpoints: start, stop, status, tasks (create/list/get/cancel),
workers (list/get), llm-events, cache-stats, cache-clear, token-budget-stats,
run orchestration, and get orchestration.
External services are mocked to avoid DI container and LLM dependencies.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.storage.io_paths import build_cache_root

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
        patch(
            "polaris.delivery.http.v2.director.build_llm_status",
            return_value={
                "roles": {
                    "director": {
                        "ready": True,
                        "runtime_supported": True,
                        "provider_id": "qwen",
                        "model": "qwen3-max",
                    }
                },
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["director"],
                "state": "READY",
            },
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


def _director_run_diagnostics(
    *,
    workspace: str = ".",
    can_execute: bool = True,
    execution_blockers: list[str] | None = None,
    ready_task_ids: list[str] | None = None,
    blueprint_ready_task_ids: list[str] | None = None,
) -> object:
    """Build a Director diagnostics response for /run preflight tests."""

    from polaris.delivery.http.v2.director import (
        DirectorDiagnosticsLLMSection,
        DirectorDiagnosticsResponse,
        DirectorDiagnosticsStatusSection,
        DirectorDiagnosticsTaskSection,
        DirectorDiagnosticsWorkerSection,
    )

    blockers = list(execution_blockers or [])
    return DirectorDiagnosticsResponse(
        ok=not blockers,
        can_execute=can_execute and not blockers,
        role="director",
        generated_at="2026-05-24T00:00:00Z",
        workspace=workspace,
        status=DirectorDiagnosticsStatusSection(
            ok=True,
            state="IDLE",
            running=False,
            source="workflow",
            projection_source="director_merged",
        ),
        tasks=DirectorDiagnosticsTaskSection(
            ok=True,
            source="workflow",
            total=1,
            pending=1,
            ready_to_execute=1,
            ready_task_ids=list(ready_task_ids or ["PM-42"]),
            blueprint_ready_task_ids=list(blueprint_ready_task_ids or []),
        ),
        workers=DirectorDiagnosticsWorkerSection(
            ok=True,
            total=1,
            idle=1,
            healthy=1,
        ),
        llm=DirectorDiagnosticsLLMSection(
            ok=True,
            state="ready",
            blocked_roles=[],
            unsupported_roles=[],
            required_ready_roles=["director"],
            provider_id="qwen",
            model="qwen3-max",
        ),
        issues=list(blockers),
        execution_blockers=blockers,
    )


def _patch_director_blueprint_persistence(payload_by_id: dict[str, dict[str, object]]) -> Any:
    """Patch Director's read-only CE blueprint persistence probe."""

    persistence = MagicMock()
    persistence.list_all.return_value = list(payload_by_id.keys())
    persistence.load.side_effect = lambda blueprint_id: payload_by_id.get(str(blueprint_id))
    return patch("polaris.delivery.http.v2.director.BlueprintPersistence", return_value=persistence)


def _ready_blueprint(blueprint_id: str, task_id: str, *, status: str = "generated") -> dict[str, object]:
    return {
        "blueprint_id": blueprint_id,
        "task_id": task_id,
        "status": status,
        "target_files": [f"src/{task_id.lower()}.ts"],
        "acceptance_criteria": [f"{task_id} acceptance is implemented"],
        "execution_checklist": [f"Implement {task_id}", f"Verify {task_id}"],
        "contract_completeness": {
            "handoff_ready": True,
            "missing_fields": [],
            "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
        },
        "handoff_ready": True,
    }


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

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch("polaris.delivery.http.v2.director.ensure_required_roles_ready") as mock_roles_ready,
    ):

        async def _resolve_start_stop(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_start_stop)

        start_resp = await client.post("/v2/director/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["ok"] is True
        assert start_resp.json()["state"] == "RUNNING"
        mock_roles_ready.assert_called_once()

        stop_resp = await client.post("/v2/director/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_director_lifecycle_routes_accept_workspace_query_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director desktop lifecycle controls should use the requested workspace for gates and evidence."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.start = AsyncMock()
    mock_director.stop = AsyncMock()
    mock_director.state.name = "RUNNING"
    mock_director.config.workspace = "C:/Temp/Stale"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch("polaris.delivery.http.v2.director.ensure_required_roles_ready") as mock_roles_ready,
    ):

        async def _resolve_start_stop(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_start_stop)

        start_resp = await client.post("/v2/director/start", params={"workspace": "C:/Temp/Product"})
        stop_resp = await client.post("/v2/director/stop", params={"workspace": "C:/Temp/Product"})

    assert start_resp.status_code == 200
    assert stop_resp.status_code == 200
    assert start_resp.json()["workspace"] == "C:/Temp/Product"
    assert stop_resp.json()["workspace"] == "C:/Temp/Product"
    role_gate_state = mock_roles_ready.call_args.args[0]
    assert Path(str(role_gate_state.settings.workspace)).as_posix().endswith("C:/Temp/Product")
    assert str(mock_settings.workspace) == "C:/Repo/Polaris"


@pytest.mark.asyncio
async def test_director_start_blocks_when_lifecycle_roles_not_ready(client: AsyncClient) -> None:
    """Director start should fail closed when Director LLM readiness is blocked."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    mock_director = MagicMock()
    mock_director.start = AsyncMock()
    mock_director.state.name = "RUNNING"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch("polaris.delivery.http.v2.director.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_roles_ready.side_effect = StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": ["director"],
                "missing_roles": ["director"],
            },
        )

        response = await client.post("/v2/director/start")

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == ["director"]
    mock_director.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_status(client: AsyncClient) -> None:
    """Director status should return workflow-aware merged state by default."""
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": True, "status": {"state": "ACTIVE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "COMPLETED"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["projection_source"] == "director_merged"
        assert data["running"] is False
        assert data["state"] == "COMPLETED"
        assert data["status"]["state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_director_status_local_source_uses_local_projection(client: AsyncClient) -> None:
    """Director status can still expose local role state when explicitly requested."""
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": True, "status": {"state": "ACTIVE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "COMPLETED"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status?source=local")
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
async def test_director_status_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director status projection should honor the desktop-selected workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    with patch(
        "polaris.cells.runtime.projection.public.service.RuntimeProjectionService.build_async",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_projection = MagicMock()
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/status?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


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


@pytest.mark.asyncio
async def test_director_diagnostics_reports_ready_queue_and_workers(client: AsyncClient) -> None:
    """Director diagnostics should expose queue and worker readiness evidence."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}
    mock_busy_worker = MagicMock()
    mock_busy_worker.to_dict.return_value = {
        "id": "worker-busy",
        "status": "busy",
        "current_task_id": "director-running",
        "healthy": True,
    }

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker, mock_busy_worker])
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
                    "id": "director-ready",
                    "subject": "Ready task",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "blueprint_id": "bp-PM-1",
                        "runtime_blueprint_path": "runtime/blueprints/bp-PM-1.json",
                    },
                },
                {
                    "id": "director-blocked",
                    "subject": "Blocked task",
                    "status": "BLOCKED",
                    "metadata": {"pm_task_id": "PM-2"},
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "director"
    assert data["status"]["projection_source"] == "director_merged"
    assert data["tasks"]["source"] == "workflow"
    assert data["tasks"]["total"] == 2
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["director-ready"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["director-ready"]
    assert data["tasks"]["missing_blueprint_task_ids"] == []
    assert data["tasks"]["blocked"] == 1
    assert data["workers"]["total"] == 2
    assert data["workers"]["idle"] == 1
    assert data["workers"]["busy"] == 1
    assert data["workers"]["active_task_ids"] == ["director-running"]
    assert data["llm"]["ok"] is True
    assert data["llm"]["required_ready_roles"] == ["director"]
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert "director_tasks_blocked" in data["issues"]
    assert "director_no_ready_tasks" not in data["issues"]
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_accepts_workspace_query_override(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Director diagnostics should bind projection and LLM checks to the requested workspace."""
    active_workspace = tmp_path / "active"
    requested_workspace = tmp_path / "requested"
    active_workspace.mkdir()
    requested_workspace.mkdir()
    mock_settings.workspace = str(active_workspace)
    mock_settings.workspace_path = ""

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[])
    mock_director.config.workspace = str(active_workspace)

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch("polaris.delivery.http.v2.director.select_task_rows_from_projection", return_value=[]),
        patch(
            "polaris.delivery.http.v2.director.build_llm_status",
            return_value={
                "roles": {
                    "director": {
                        "ready": True,
                        "runtime_supported": True,
                        "provider_id": "qwen",
                        "model": "qwen3-max",
                    }
                },
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["director"],
                "state": "READY",
            },
        ) as mock_llm_status,
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = None
        mock_build.return_value = mock_projection

        response = await client.get(
            "/v2/director/diagnostics",
            params={"workspace": str(requested_workspace)},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    build_args = mock_build.await_args
    assert build_args is not None
    assert Path(str(build_args.args[0])).resolve() == requested_workspace.resolve()
    llm_status_args = mock_llm_status.call_args
    assert llm_status_args is not None
    called_settings = llm_status_args.args[0]
    assert Path(str(called_settings.workspace)).resolve() == requested_workspace.resolve()
    assert Path(str(mock_settings.workspace)).resolve() == active_workspace.resolve()


@pytest.mark.asyncio
async def test_director_diagnostics_blocks_when_llm_role_not_ready(client: AsyncClient) -> None:
    """Director diagnostics should fail closed when the Director LLM role is blocked."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-ready",
                    "subject": "Ready task",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "blueprint_id": "bp-PM-1",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.build_llm_status",
            return_value={
                "roles": {
                    "director": {
                        "ready": False,
                        "runtime_supported": True,
                        "provider_id": "qwen",
                        "model": "qwen3-max",
                    }
                },
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": [],
                "state": "READY",
            },
        ),
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["llm"]["ok"] is False
    assert data["llm"]["blocked_roles"] == ["director"]
    assert data["llm"]["required_ready_roles"] == ["director"]
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_llm_not_ready"]
    assert data["issues"] == ["director_llm_not_ready"]


@pytest.mark.asyncio
async def test_director_diagnostics_blocks_workflow_ready_tasks_without_blueprints(
    client: AsyncClient,
) -> None:
    """Workflow tasks need Chief Engineer blueprint evidence before execution."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-without-blueprint",
                    "subject": "Missing CE blueprint",
                    "status": "PENDING",
                    "metadata": {"pm_task_id": "PM-missing-bp"},
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["source"] == "workflow"
    assert data["tasks"]["ready_to_execute"] == 0
    assert data["tasks"]["ready_task_ids"] == []
    assert data["tasks"]["missing_blueprint_task_ids"] == ["director-without-blueprint"]
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_ready_tasks_missing_blueprints"]
    assert data["issues"] == ["director_ready_tasks_missing_blueprints"]
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_accepts_matching_chief_engineer_blueprint_store_evidence(
    client: AsyncClient,
) -> None:
    """Workflow rows without inline blueprint refs can use CE store evidence keyed by PM task id."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-ready-from-ce-store",
                    "subject": "Ready via CE store",
                    "status": "PENDING",
                    "metadata": {"pm_task_id": "PM-ready"},
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence(
            {"bp-PM-ready": _ready_blueprint("bp-PM-ready", "PM-ready", status="generated")}
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["source"] == "workflow"
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["director-ready-from-ce-store"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["director-ready-from-ce-store"]
    assert data["tasks"]["missing_blueprint_task_ids"] == []
    assert data["tasks"]["invalid_blueprint_task_ids"] == []
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_does_not_count_expired_runtime_session_as_running(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Projection RUNNING rows must be normalized by canonical runtime lease state."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    mock_settings.workspace = str(workspace)
    mock_settings.workspace_path = ""

    task_runtime = TaskRuntimeService(str(workspace))
    task = task_runtime.create(
        subject="Recover expired Director task",
        description="The projection row is stale but runtime lease has expired.",
        metadata={"pm_task_id": "PM-expired-runtime"},
    )
    claimed = task_runtime.claim_execution(
        task.id,
        worker_id="director",
        role_id="director",
        run_id="run-expired-runtime",
        lease_ttl_seconds=60,
        selection_source="unit",
        external_task_id="PM-expired-runtime",
    )
    assert claimed["success"] is True

    session_path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task.id}.session.json"))
    session_payload = json.loads(session_path.read_text(encoding="utf-8"))
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    session_payload["last_heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
    session_payload["lease_expires_at"] = expired_at
    session_path.write_text(json.dumps(session_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
    mock_director.config.workspace = str(workspace)

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": str(task.id),
                    "subject": "Recover expired Director task",
                    "status": "RUNNING",
                    "claimed_by": "director",
                    "metadata": {
                        "pm_task_id": "PM-expired-runtime",
                        "runtime_execution": {
                            "status": "active",
                            "effective_status": "in_progress",
                        },
                    },
                }
            ],
        ),
        patch("polaris.delivery.http.v2.director.build_workflow_task_rows", return_value=[]),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence(
            {"bp-expired-runtime": _ready_blueprint("bp-expired-runtime", "PM-expired-runtime")}
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["source"] == "workflow"
    assert data["tasks"]["claimed"] == 0
    assert data["tasks"]["running"] == 0
    assert data["tasks"]["pending"] == 1
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == [str(task.id)]
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_blocks_workflow_tasks_with_invalid_blueprint_artifacts(
    client: AsyncClient,
) -> None:
    """Workflow blueprint references must resolve to readable CE artifacts for the task."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-stale-blueprint",
                    "subject": "Stale CE blueprint",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-stale-bp",
                        "blueprint_id": "bp-stale",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-stale": {"blueprint_id": "bp-stale", "task_id": "PM-other"}}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["ready_to_execute"] == 0
    assert data["tasks"]["ready_task_ids"] == []
    assert data["tasks"]["missing_blueprint_task_ids"] == []
    assert data["tasks"]["invalid_blueprint_task_ids"] == ["director-stale-blueprint"]
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_ready_tasks_invalid_blueprints"]
    assert data["issues"] == ["director_ready_tasks_invalid_blueprints"]
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_rejects_traceability_only_blueprint_artifacts(
    client: AsyncClient,
) -> None:
    """Traceability-only PM mirrors must not authorize Director execution."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
    mock_director.config.workspace = "."
    traceability_blueprint = _ready_blueprint("bp-trace", "PM-1")
    traceability_blueprint["source"] = "pm_dispatch.traceability_reference"
    traceability_blueprint["traceability_only"] = True

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "director-traceability-only",
                    "subject": "Traceability-only CE reference",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "blueprint_id": "bp-trace",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-trace": traceability_blueprint}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["ready_to_execute"] == 0
    assert data["tasks"]["invalid_blueprint_task_ids"] == ["director-traceability-only"]
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_ready_tasks_invalid_blueprints"]


@pytest.mark.asyncio
async def test_director_diagnostics_accepts_runtime_blueprint_task_update_map(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Aggregate CE runtime blueprints can prove task coverage through task_update_map."""
    mock_settings.workspace = str(tmp_path)
    mock_settings.workspace_path = ""
    blueprint_path = tmp_path / "chief_engineer.blueprint.json"
    blueprint_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "role": "ChiefEngineer",
                "hard_failure": False,
                "task_id": "PM-1",
                "target_files": ["src/pm-1.ts"],
                "acceptance_criteria": ["PM-1 acceptance is implemented"],
                "execution_checklist": ["Implement PM-1", "Verify PM-1"],
                "task_update_map": {"PM-1": {"task_id": "PM-1", "verify_ready": True}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
    mock_director.config.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "director-ready",
                    "subject": "Ready task",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "runtime_blueprint_path": "runtime/contracts/chief_engineer.blueprint.json",
                    },
                }
            ],
        ),
        patch("polaris.delivery.http.v2.director.resolve_artifact_path", return_value=str(blueprint_path)),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["director-ready"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["director-ready"]
    assert data["tasks"]["invalid_blueprint_task_ids"] == []
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []


@pytest.mark.asyncio
async def test_director_diagnostics_marks_workflow_task_ready_when_dependencies_completed(
    client: AsyncClient,
) -> None:
    """Workflow dependencies are blockers only while their referenced tasks are unfinished."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-base",
                    "subject": "Base task",
                    "status": "COMPLETED",
                    "metadata": {"pm_task_id": "PM-base"},
                },
                {
                    "id": "director-dependent",
                    "subject": "Dependent task",
                    "status": "PENDING",
                    "dependencies": ["PM-base"],
                    "metadata": {
                        "pm_task_id": "PM-dependent",
                        "blueprint_id": "bp-dependent",
                    },
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-dependent": _ready_blueprint("bp-dependent", "PM-dependent")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["total"] == 2
    assert data["tasks"]["completed"] == 1
    assert data["tasks"]["pending"] == 1
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["director-dependent"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["director-dependent"]
    assert data["tasks"]["missing_blueprint_task_ids"] == []
    assert data["tasks"]["invalid_blueprint_task_ids"] == []
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert data["issues"] == []
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_includes_unmaterialized_pm_contract_dependents(
    client: AsyncClient,
) -> None:
    """Director diagnostics must not declare terminal while PM contract tasks remain unmaterialized."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "runtime-base",
                    "subject": "Base task",
                    "status": "COMPLETED",
                    "metadata": {"pm_task_id": "PM-base"},
                }
            ],
        ),
        patch(
            "polaris.delivery.http.v2.director.build_workflow_task_rows",
            return_value=[
                {
                    "id": "PM-base",
                    "subject": "Base task",
                    "status": "COMPLETED",
                    "metadata": {"pm_task_id": "PM-base"},
                },
                {
                    "id": "PM-dependent",
                    "subject": "Dependent task",
                    "status": "PENDING",
                    "dependencies": ["PM-base"],
                    "metadata": {"pm_task_id": "PM-dependent"},
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-dependent": _ready_blueprint("bp-dependent", "PM-dependent")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["total"] == 2
    assert data["tasks"]["completed"] == 1
    assert data["tasks"]["pending"] == 1
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["PM-dependent"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["PM-dependent"]
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert data["issues"] == []
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_runtime_overlay_filters_workflow_shell(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Runtime rows should replace stale workflow shell rows while preserving contract gaps."""
    mock_settings.workspace = str(tmp_path)
    mock_settings.workspace_path = ""

    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
    mock_director.config.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "task-0-director",
                    "subject": "Director workflow shell",
                    "status": "RUNNING",
                    "metadata": {
                        "workflow_task_id": "task-0-director",
                        "role_id": "director",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.v2.director._runtime_task_rows_for_workspace",
            return_value=[
                {
                    "id": 101,
                    "subject": "Completed contract task 1",
                    "status": "completed",
                    "metadata": {
                        "pm_task_id": "T01",
                        "source_task_id": "T01",
                    },
                },
                {
                    "id": 102,
                    "subject": "Completed contract task 2",
                    "status": "completed",
                    "metadata": {
                        "pm_task_id": "T02",
                        "source_task_id": "T02",
                    },
                },
            ],
        ),
        patch(
            "polaris.delivery.http.v2.director.build_workflow_task_rows",
            return_value=[
                {
                    "id": "T01",
                    "subject": "Contract task 1",
                    "status": "PENDING",
                    "metadata": {"pm_task_id": "T01"},
                },
                {
                    "id": "T02",
                    "subject": "Contract task 2",
                    "status": "PENDING",
                    "metadata": {"pm_task_id": "T02"},
                },
                {
                    "id": "T03",
                    "subject": "Remaining contract task",
                    "status": "PENDING",
                    "dependencies": ["T02"],
                    "metadata": {
                        "pm_task_id": "T03",
                        "blueprint_id": "bp-T03",
                    },
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-T03": _ready_blueprint("bp-T03", "T03")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": True,
            "source": "workflow",
            "status": {"state": "RUNNING"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["source"] == "workflow"
    assert data["tasks"]["total"] == 3
    assert data["tasks"]["completed"] == 2
    assert data["tasks"]["pending"] == 1
    assert data["tasks"]["running"] == 0
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["T03"]
    assert data["tasks"]["blueprint_ready_task_ids"] == ["T03"]
    assert data["tasks"]["running_task_ids"] == []
    assert "task-0-director" not in data["tasks"]["ready_task_ids"]
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert data["issues"] == []
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_blocks_pending_workflow_task_when_dependency_failed(
    client: AsyncClient,
) -> None:
    """Pending workflow tasks blocked by failed dependencies must be terminal blockers, not ready work."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
                    "id": "director-failed",
                    "subject": "Failed task",
                    "status": "FAILED",
                    "metadata": {"pm_task_id": "PM-failed"},
                },
                {
                    "id": "director-dependent",
                    "subject": "Dependent task",
                    "status": "PENDING",
                    "dependencies": ["PM-failed"],
                    "metadata": {
                        "pm_task_id": "PM-dependent",
                        "blueprint_id": "bp-dependent",
                    },
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-dependent": _ready_blueprint("bp-dependent", "PM-dependent")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["total"] == 2
    assert data["tasks"]["failed"] == 1
    assert data["tasks"]["blocked"] == 1
    assert data["tasks"]["pending"] == 0
    assert data["tasks"]["ready_to_execute"] == 0
    assert data["tasks"]["ready_task_ids"] == []
    assert data["tasks"]["blocked_task_ids"] == ["director-dependent"]
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_tasks_blocked", "director_tasks_failed"]
    assert data["issues"] == ["director_tasks_blocked", "director_tasks_failed"]
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_diagnostics_falls_back_to_local_tasks_when_projection_empty(
    client: AsyncClient,
) -> None:
    """Director diagnostics should reuse local queue evidence when workflow projection is empty."""
    mock_idle_worker = MagicMock()
    mock_idle_worker.to_dict.return_value = {"id": "worker-idle", "status": "idle", "healthy": True}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(
        return_value=[
            {
                "id": "local-ready",
                "subject": "Local ready task",
                "status": "PENDING",
                "metadata": {"pm_task_id": "PM-local"},
            }
        ]
    )
    mock_director.list_workers = AsyncMock(return_value=[mock_idle_worker])
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
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["source"] == "local"
    assert data["tasks"]["total"] == 1
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["tasks"]["ready_task_ids"] == ["local-ready"]
    assert data["workers"]["idle"] == 1
    assert data["issues"] == []
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    mock_director.list_tasks.assert_awaited_once_with(status=None)


@pytest.mark.asyncio
async def test_director_diagnostics_uses_projection_workers_when_local_pool_empty(
    client: AsyncClient,
) -> None:
    """Director diagnostics should reuse projected worker rows when service workers are empty."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[])
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
                    "id": "director-ready",
                    "subject": "Ready task",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "blueprint_id": "bp-PM-1",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
            "workers": {
                "worker_rows": [
                    {
                        "id": "projected-worker-1",
                        "status": "idle",
                        "healthy": True,
                    }
                ]
            },
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["workers"]["total"] == 1
    assert data["workers"]["idle"] == 1
    assert data["workers"]["healthy"] == 1
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert data["tasks"]["blueprint_ready_task_ids"] == ["director-ready"]
    assert data["tasks"]["missing_blueprint_task_ids"] == []
    assert "director_no_workers" not in data["issues"]
    mock_director.list_workers.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_diagnostics_allows_start_when_worker_pool_not_initialized(
    client: AsyncClient,
) -> None:
    """A stopped Director may have no workers until service startup initializes the pool."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.list_workers = AsyncMock(return_value=[])
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
                    "id": "director-ready",
                    "subject": "Ready task",
                    "status": "PENDING",
                    "metadata": {
                        "pm_task_id": "PM-1",
                        "blueprint_id": "bp-PM-1",
                    },
                }
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        _patch_director_blueprint_persistence({"bp-PM-1": _ready_blueprint("bp-PM-1", "PM-1")}),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = {
            "running": False,
            "source": "workflow",
            "status": {"state": "IDLE"},
        }
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["ready_to_execute"] == 1
    assert data["workers"]["total"] == 0
    assert data["can_execute"] is True
    assert data["execution_blockers"] == []
    assert data["issues"] == ["director_no_workers"]


@pytest.mark.asyncio
async def test_director_diagnostics_reports_no_ready_tasks_without_workers(client: AsyncClient) -> None:
    """Blocked dependencies and an empty worker pool should be visible before a run is attempted."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(
        return_value=[
            {
                "id": "local-waiting",
                "subject": "Waiting on Chief Engineer",
                "status": "PENDING",
                "metadata": {"dependencies": ["blueprint-1"]},
            }
        ]
    )
    mock_director.list_workers = AsyncMock(return_value=[])
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
        mock_projection.task_rows = []
        mock_projection.director_local = {"running": False, "status": {"state": "IDLE"}}
        mock_projection.director_merged = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["tasks"]["ready_to_execute"] == 0
    assert data["workers"]["total"] == 0
    assert data["can_execute"] is False
    assert data["execution_blockers"] == ["director_no_ready_tasks"]
    assert "director_no_ready_tasks" in data["issues"]
    assert "director_no_workers" in data["issues"]


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
async def test_director_create_task_uses_workspace_query_for_service_and_metadata(client: AsyncClient) -> None:
    """Director task creation should be pinned to the explicitly requested workspace."""
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    target_workspace = "C:/Temp/Product"

    mock_task = MagicMock()
    mock_task.id = "task-workspace"
    mock_task.subject = "Workspace task"
    mock_task.description = "Description"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {}

    stale_director = MagicMock()
    stale_director.config.workspace = "C:/Other"
    stale_director.submit_task = AsyncMock()

    workspace_director = MagicMock()
    workspace_director.config.workspace = str(Path(target_workspace).resolve())
    workspace_director.submit_task = AsyncMock(return_value=mock_task)

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.dependencies.rebind_director_service",
            new_callable=AsyncMock,
            return_value=workspace_director,
        ) as mock_rebind,
    ):

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return stale_director
            return MagicMock()

        mock_container.return_value.has_registration.return_value = True
        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.post(
            "/v2/director/tasks",
            params={"workspace": target_workspace},
            json={
                "subject": "Workspace task",
                "description": "Description",
                "priority": "HIGH",
                "metadata": {"source": "desktop"},
            },
        )

    assert response.status_code == 200
    mock_rebind.assert_awaited_once_with(str(Path(target_workspace).resolve()))
    stale_director.submit_task.assert_not_awaited()
    workspace_director.submit_task.assert_awaited_once()
    submitted_metadata = workspace_director.submit_task.await_args.kwargs["metadata"]
    assert submitted_metadata["source"] == "desktop"
    assert submitted_metadata["workspace"] == target_workspace
    assert submitted_metadata["director_workspace"] == target_workspace
    data = response.json()
    assert data["id"] == "task-workspace"
    assert data["metadata"]["workspace"] == target_workspace
    assert data["metadata"]["director_workspace"] == target_workspace


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
async def test_director_create_task_accepts_lowercase_priority(client: AsyncClient) -> None:
    """Director create task should accept TaskPriority enum values."""
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-789"
    mock_task.subject = "Run focused tests"
    mock_task.description = ""
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
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

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Run focused tests",
                "priority": "high",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "task-789"
    assert data["priority"] == "HIGH"
    submit_call = mock_director.submit_task.await_args
    assert submit_call is not None
    assert submit_call.kwargs["priority"] is TaskPriority.HIGH


@pytest.mark.asyncio
async def test_director_create_task_rejects_invalid_priority(client: AsyncClient) -> None:
    """Invalid task priority should return a structured 400 instead of a 500."""
    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock()
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
                "subject": "Bad task",
                "priority": "urgent",
            },
        )

    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_TASK_PRIORITY"
    assert data["error"]["details"]["priority"] == "urgent"
    assert "HIGH" in data["error"]["details"]["allowed"]
    mock_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_list_tasks(client: AsyncClient) -> None:
    """Director list tasks should return an empty list when projection and local queue are empty."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(items=())

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
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
        mock_director.list_tasks.assert_awaited_once_with(status=None)


@pytest.mark.asyncio
async def test_director_list_tasks_auto_falls_back_to_local_queue(client: AsyncClient) -> None:
    """source=auto should expose local Director tasks when workflow projection is empty."""
    from polaris.domain.entities import TaskPriority, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "local-task-1"
    mock_task.subject = "Local Director task"
    mock_task.description = "Queued outside workflow projection"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = None
    mock_task.metadata = {"pm_task_id": "PM-local", "blueprint_id": "BP-local"}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[mock_task])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(items=())

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?source=auto")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "local-task-1"
    assert data[0]["subject"] == "Local Director task"
    assert data[0]["status"] == "PENDING"
    assert data[0]["pm_task_id"] == "PM-local"
    assert data[0]["blueprint_id"] == "BP-local"
    mock_director.list_tasks.assert_awaited_once_with(status=None)


@pytest.mark.asyncio
async def test_director_list_tasks_uses_task_market_execution_rows(client: AsyncClient) -> None:
    """Task-market pending_exec rows should be visible before runtime projection exists."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(
        items=(
            {
                "task_id": "PM-100",
                "stage": "pending_exec",
                "status": "pending_exec",
                "priority": "high",
                "claimed_by": "",
                "depends_on": [],
                "payload": {
                    "title": "Implement combat loop",
                    "goal": "Create deterministic combat loop",
                    "target_files": ["src/combat.ts"],
                    "scope_paths": ["src/combat.ts"],
                    "blueprint_id": "bp-PM-100",
                    "blueprint_path": "runtime/blueprints/bp-PM-100.json",
                    "runtime_blueprint_path": "runtime/blueprints/bp-PM-100.json",
                    "route": "chief_blueprint_required",
                    "blueprint_required": True,
                },
                "metadata": {"route": "chief_blueprint_required"},
            },
        )
    )

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?source=auto")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "PM-100"
    assert data[0]["subject"] == "Implement combat loop"
    assert data[0]["status"] == "PENDING"
    assert data[0]["blueprint_id"] == "bp-PM-100"
    assert data[0]["metadata"]["route"] == "chief_blueprint_required"
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_list_tasks_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task projection should use workspace_path before stale workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
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


@pytest.mark.asyncio
async def test_director_list_tasks_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task projection should honor the requested workspace query."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "C:/Temp/Stale"

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

        response = await client.get("/v2/director/tasks?source=workflow&workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


def test_director_debug_append_ignores_debug_log_failure() -> None:
    """Optional Director debug evidence should not leak filesystem failures."""
    with (
        patch.dict("os.environ", {"KERNELONE_BACKEND_DEBUG_LOG": "C:/Temp/director-debug.jsonl"}),
        patch(
            "polaris.delivery.http.v2.director.Path.open",
            side_effect=OSError("debug log locked"),
        ),
        patch("polaris.delivery.http.v2.director.logger.debug") as mock_debug,
    ):
        from polaris.delivery.http.v2.director import _append_debug

        _append_debug("test.event", {"ok": True})
        mock_debug.assert_called_once()


def test_director_debug_append_is_disabled_without_explicit_log_path() -> None:
    """High-frequency Director read routes must not write debug logs by default."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("polaris.delivery.http.v2.director.Path.open") as mock_open,
    ):
        from polaris.delivery.http.v2.director import _append_debug

        _append_debug("test.event", {"ok": True})

    mock_open.assert_not_called()


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
        assert data[0]["metadata"]["projection_source"] == "runtime_projection"


def test_runtime_backed_task_rows_expose_projection_source_from_runtime_lineage(
    tmp_path: Path,
) -> None:
    """Runtime-backed Director task rows should preserve source provenance for E2E audits."""
    from polaris.delivery.http.v2.director import _runtime_backed_task_rows

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    task_runtime = TaskRuntimeService(str(workspace))
    task = task_runtime.create(
        subject="Runtime backed task",
        description="Runtime lineage should be visible through the HTTP task projection.",
        metadata={
            "pm_task_id": "PM-runtime-source",
            "materialized_by": "runtime.task_runtime",
        },
    )

    rows = _runtime_backed_task_rows(
        [
            {
                "id": str(task.id),
                "subject": "Runtime backed task",
                "status": "RUNNING",
                "metadata": {"pm_task_id": "PM-runtime-source"},
            }
        ],
        workspace=str(workspace),
    )

    assert rows[0]["metadata"]["pm_task_id"] == "PM-runtime-source"
    assert rows[0]["metadata"]["projection_source"] == "runtime.task_runtime"


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
async def test_director_get_task_projection_fallback_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task detail fallback should honor the requested workspace query."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "C:/Temp/Stale"

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

        response = await client.get("/v2/director/tasks/PM-404?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 404
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


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
async def test_director_cancel_task_returns_requested_workspace_evidence(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director task cancel should echo the workspace used by desktop controls."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=True)
    mock_director.config.workspace = "C:/Temp/Stale"

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel)

        response = await client.post("/v2/director/tasks/task-123/cancel?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    assert response.json()["workspace"] == "C:/Temp/Verified"


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
async def test_director_list_workers_falls_back_to_projection(client: AsyncClient) -> None:
    """Director worker list should expose projected worker evidence after local service empties."""
    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[])
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
    ):

        async def _resolve_workers(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_workers)
        mock_projection = MagicMock()
        mock_projection.director_merged = {
            "workers": {
                "worker_rows": [
                    {
                        "id": "projected-worker-1",
                        "name": "Projected Worker",
                        "status": "busy",
                        "current_task_id": "projected-task-1",
                        "healthy": True,
                    }
                ]
            }
        }
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers")

    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "id": "projected-worker-1",
            "name": "Projected Worker",
            "status": "busy",
            "current_task_id": "projected-task-1",
            "healthy": True,
        }
    ]
    mock_director.list_workers.assert_awaited_once()
    mock_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_list_workers_projection_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Projected worker fallback should honor the requested workspace query."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[])
    mock_director.config.workspace = "C:/Temp/Stale"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
    ):

        async def _resolve_workers(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_workers)
        mock_projection = MagicMock()
        mock_projection.director_merged = {}
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


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
async def test_director_get_worker_falls_back_to_projection(client: AsyncClient) -> None:
    """Director worker detail should resolve projected workers after a local miss."""
    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=None)
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
    ):

        async def _resolve_worker_detail(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_worker_detail)
        mock_projection = MagicMock()
        mock_projection.director_merged = {
            "workers": {
                "worker_rows": [
                    {
                        "worker_id": "projected-worker-1",
                        "name": "Projected Worker",
                        "status": "busy",
                        "currentTaskId": "projected-task-1",
                        "healthy": True,
                    }
                ]
            }
        }
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers/projected-worker-1")

    assert response.status_code == 200
    data = response.json()
    assert data["worker_id"] == "projected-worker-1"
    assert data["name"] == "Projected Worker"
    assert data["status"] == "busy"
    assert data["currentTaskId"] == "projected-task-1"
    mock_director.get_worker.assert_awaited_once_with("projected-worker-1")
    mock_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_get_worker_not_found(client: AsyncClient) -> None:
    """Director get worker should 404 when worker doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=None)
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
    ):

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)
        mock_projection = MagicMock()
        mock_projection.director_merged = {}
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

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
    mock_event.metadata = {"workspace": "."}
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
async def test_director_task_llm_events_filters_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """Director task LLM events should filter shared history by requested workspace."""
    requested_workspace = tmp_path / "requested"
    other_workspace = tmp_path / "other"
    requested_workspace.mkdir()
    other_workspace.mkdir()

    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": str(requested_workspace)}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-requested",
        "task_id": "task-1",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/director/tasks/task-1/llm-events",
            params={"workspace": str(requested_workspace), "limit": "5"},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["stats"]["total"] == 1
    assert data["events"][0]["run_id"] == "run-requested"


@pytest.mark.asyncio
async def test_director_task_llm_events_filters_active_workspace_by_default(client: AsyncClient) -> None:
    """Director task LLM events should filter to active workspace without query workspace."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": "."}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-active",
        "task_id": "task-1",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": "/tmp/other-workspace"}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/tasks/task-1/llm-events?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "."
    assert data["stats"]["total"] == 1
    assert data["events"][0]["run_id"] == "run-active"


@pytest.mark.asyncio
async def test_director_global_llm_events(client: AsyncClient) -> None:
    """Get global LLM events should return all events."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_error"
    mock_event.metadata = {"workspace": "."}
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
async def test_director_global_llm_events_filters_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """Director global LLM events should not mix evidence from another workspace."""
    requested_workspace = tmp_path / "requested"
    other_workspace = tmp_path / "other"
    requested_workspace.mkdir()
    other_workspace.mkdir()

    matching_event = MagicMock()
    matching_event.event_type = "llm_error"
    matching_event.metadata = {"workspace": str(requested_workspace)}
    matching_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-requested",
        "role": "director",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_error"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-other",
        "role": "director",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/director/llm-events",
            params={"role": "director", "workspace": str(requested_workspace)},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-requested"


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
# Integration QA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_director_integration_qa_persists_to_active_runtime_root(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """API-triggered integration QA should persist artifacts in the active runtime root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_base = tmp_path / "runtime-base"
    mock_settings.workspace = str(workspace)
    mock_settings.workspace_path = str(workspace)
    mock_settings.ramdisk_root = str(runtime_base)
    cache_root = build_cache_root(str(runtime_base), str(workspace))

    director_result = {
        "status": "success",
        "successes": 1,
        "failures": 0,
        "blocked": 0,
        "tasks": [{"id": "PM-1", "status": "done"}],
    }
    task_rows = [
        {
            "task_id": "PM-1",
            "assigned_to": "director",
            "status": "done",
            "target_files": ["src/index.ts"],
            "metadata": {"pm_task_id": "PM-1"},
        }
    ]

    with (
        patch(
            "polaris.cells.orchestration.workflow_runtime.public.service.persist_director_result_from_runtime",
            return_value=director_result,
        ),
        patch(
            "polaris.cells.orchestration.workflow_runtime.public.service.build_integration_qa_tasks_from_director_result",
            return_value=task_rows,
        ),
        patch(
            "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._resolve_verify_runner",
            return_value=lambda _workspace: (True, "Integration verification passed", []),
        ),
        patch(
            "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._attach_pm_dispatch_qa_cognitive_receipt",
        ),
    ):
        response = await client.post(
            "/v2/director/integration-qa",
            json={
                "workspace": str(workspace),
                "run_id": "api-qa-1",
                "iteration": 2,
            },
        )

    assert response.status_code == 200
    data = response.json()
    result = data["result"]
    result_path = Path(result["result_path"])
    runtime_result_path = Path(result["runtime_result_path"])
    assert result_path == Path(cache_root) / "runs" / "api-qa-1" / "qa" / "integration_qa.result.json"
    assert runtime_result_path == Path(cache_root) / "results" / "integration_qa.result.json"
    assert result_path.is_file()
    assert runtime_result_path.is_file()
    persisted = json.loads(runtime_result_path.read_text(encoding="utf-8"))
    assert persisted["reason"] == "integration_qa_passed"
    assert persisted["passed"] is True


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
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

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
        mock_preflight.assert_awaited_once()


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
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace="C:/Temp/Product")

        response = await client.post(
            "/v2/director/run",
            json={
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    execute_args = mock_service.execute_director_run.await_args
    assert execute_args is not None
    _, kwargs = execute_args
    assert kwargs["workspace"] == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"
    mock_preflight.assert_awaited_once()
    preflight_args = mock_preflight.await_args
    assert preflight_args is not None
    assert preflight_args.args[1] == "C:/Temp/Product"


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
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace="D:/Explicit/Product")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": "D:/Explicit/Product",
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    execute_args = mock_service.execute_director_run.await_args
    assert execute_args is not None
    _, kwargs = execute_args
    assert kwargs["workspace"] == "D:/Explicit/Product"
    assert response.json()["workspace"] == "D:/Explicit/Product"
    mock_preflight.assert_awaited_once()
    preflight_args = mock_preflight.await_args
    assert preflight_args is not None
    assert preflight_args.args[1] == "D:/Explicit/Product"


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
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

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
        mock_preflight.assert_awaited_once()


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
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

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
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_uses_diagnostics_ready_tasks_when_no_task_selected(
    client: AsyncClient,
) -> None:
    """Director run should execute diagnostics-ready workflow tasks by default."""
    mock_result = MagicMock()
    mock_result.run_id = "run-ready"
    mock_result.status = "running"
    mock_result.message = "Director started for ready tasks"
    mock_result.metadata = {"tasks_queued": 1, "task_ids": ["PM-42"]}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["tasks"] == ["PM-42"]
        assert kwargs["options"]["task_filter"] == "PM-42"
        assert kwargs["options"]["metadata"]["task_selection_source"] == "diagnostics_ready"
        assert kwargs["options"]["metadata"]["selected_task_ids"] == ["PM-42"]
        assert response.json()["tasks_queued"] == 1
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_merges_direct_and_blueprint_ready_tasks(
    client: AsyncClient,
) -> None:
    """Director run should not hide direct PM tasks when CE blueprint tasks are also ready."""
    mock_result = MagicMock()
    mock_result.run_id = "run-mixed-ready"
    mock_result.status = "running"
    mock_result.message = "Director started for mixed ready tasks"
    mock_result.metadata = {"tasks_queued": 2, "task_ids": ["chief-1", "direct-1"]}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(
            workspace=".",
            ready_task_ids=["direct-1", "chief-1"],
            blueprint_ready_task_ids=["chief-1"],
        )

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["tasks"] == ["chief-1", "direct-1"]
        assert kwargs["options"]["task_filter"] is None
        assert kwargs["options"]["metadata"]["task_selection_source"] == "diagnostics_mixed_ready"
        assert kwargs["options"]["metadata"]["selected_task_ids"] == ["chief-1", "direct-1"]
        assert response.json()["tasks_queued"] == 2
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_blocks_when_diagnostics_cannot_execute(
    client: AsyncClient,
) -> None:
    """Director run should fail closed when readiness diagnostics report blockers."""
    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
    ):
        mock_preflight.return_value = _director_run_diagnostics(
            can_execute=False,
            execution_blockers=["director_no_ready_tasks"],
        )

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "DIRECTOR_EXECUTION_BLOCKED"
    assert data["error"]["details"]["execution_blockers"] == ["director_no_ready_tasks"]
    assert data["error"]["details"]["diagnostics"]["can_execute"] is False
    mock_service_cls.assert_not_called()


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
async def test_director_get_orchestration_honors_requested_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run evidence should not leak runs from a different desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "C:/Temp/Requested"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 200
    assert response.json()["workspace"] == "C:/Temp/Requested"


@pytest.mark.asyncio
async def test_director_get_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run detail should return 404 when a run belongs to another workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_get_orchestration_accepts_plain_string_status(client: AsyncClient) -> None:
    """Director run detail should tolerate runtime snapshots with string statuses."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-string-status"
    mock_snapshot.status = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = None

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-string-status")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-string-status"
        assert data["status"] == "completed"
        assert data["tasks_queued"] == 0


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
async def test_director_cancel_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run cancel should not cancel a run from another desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.tasks = {"task-1": MagicMock()}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    mock_service.cancel_run.assert_not_awaited()


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
async def test_director_cancel_orchestration_string_status_is_idempotent(client: AsyncClient) -> None:
    """Director cancel should not require enum-like status values for terminal runs."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-string-status"
    mock_snapshot.status = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = ["task-1", "task-2"]

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-string-status/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-string-status"
        assert data["status"] == "completed"
        assert data["tasks_queued"] == 2
        mock_service.query_run.assert_awaited_once_with("run-string-status")
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
