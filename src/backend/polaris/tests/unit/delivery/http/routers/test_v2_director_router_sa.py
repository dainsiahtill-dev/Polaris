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
from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event
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
    task = task_runtime.create_task_row(
        subject="Recover expired Director task",
        description="The projection row is stale but runtime lease has expired.",
        metadata={"pm_task_id": "PM-expired-runtime"},
    )
    claimed = task_runtime.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-expired-runtime",
        lease_ttl_seconds=60,
        selection_source="unit",
        external_task_id="PM-expired-runtime",
    )
    assert claimed["success"] is True

    session_path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task['id']}.session.json"))
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
                    "id": str(task["id"]),
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
        patch("polaris.delivery.http.v2.director.summarize_workflow_tasks", return_value={"tasks": []}),
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
    assert data["tasks"]["ready_task_ids"] == [str(task["id"])]
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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
    task = task_runtime.create_task_row(
        subject="Recover expired Director task",
        description="The projection row is stale but runtime lease has expired.",
        metadata={"pm_task_id": "PM-expired-runtime"},
    )
    claimed = task_runtime.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-expired-runtime",
        lease_ttl_seconds=60,
        selection_source="unit",
        external_task_id="PM-expired-runtime",
    )
    assert claimed["success"] is True

    session_path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task['id']}.session.json"))
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
                    "id": str(task["id"]),
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
        patch("polaris.delivery.http.v2.director.summarize_workflow_tasks", return_value={"tasks": []}),
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
    assert data["tasks"]["ready_task_ids"] == [str(task["id"])]
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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
    task = task_runtime.create_task_row(
        subject="Recover expired Director task",
        description="The projection row is stale but runtime lease has expired.",
        metadata={"pm_task_id": "PM-expired-runtime"},
    )
    claimed = task_runtime.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-expired-runtime",
        lease_ttl_seconds=60,
        selection_source="unit",
        external_task_id="PM-expired-runtime",
    )
    assert claimed["success"] is True

    session_path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task['id']}.session.json"))
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
                    "id": str(task["id"]),
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
        patch("polaris.delivery.http.v2.director.summarize_workflow_tasks", return_value={"tasks": []}),
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
    assert data["tasks"]["ready_task_ids"] == [str(task["id"])]
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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
            "polaris.delivery.http.v2.director.summarize_workflow_tasks",
            return_value={
                "tasks": [
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
            },
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


def test_runtime_task_rows_for_workspace_uses_observable_task_rows(tmp_path: Path) -> None:
    from polaris.delivery.http.v2 import director

    task_runtime = TaskRuntimeService(str(tmp_path))
    created = task_runtime.create_task_row(subject="Observable diagnostics row")
    task_id = str(created["id"])
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="failed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-diagnostics",
            payload={
                "task_id": task_id,
                "run_id": "run-diagnostics",
                "event_type": "failed",
                "status": "failed",
                "execution_state": "failed",
                "last_error": "diagnostics failure",
                "task_row_snapshot": created,
            },
        )
    )

    rows = director._runtime_task_rows_for_workspace(str(tmp_path))

    assert len(rows) == 1
    assert rows[0]["subject"] == "Observable diagnostics row"
    assert rows[0]["status"] == "failed"
    assert rows[0]["last_error"] == "diagnostics failure"
    assert rows[0]["metadata"]["status_source"] == "task_runtime.execution_fact"


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
