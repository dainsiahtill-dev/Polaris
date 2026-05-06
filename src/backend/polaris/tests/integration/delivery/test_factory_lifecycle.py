"""Integration tests for the complete factory run lifecycle.

Covers happy path lifecycle, error paths, and list/filter operations
for the /v2/factory/runs API using AsyncClient with ASGITransport.
External services are mocked to avoid storage and orchestration dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryRunStatus as ServiceRunStatus,
)
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import factory as factory_router
from polaris.delivery.http.routers._shared import require_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the factory router and auth bypassed."""
    from types import SimpleNamespace

    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return app


def _mock_factory_run(
    run_id: str = "factory_abc123",
    status: ServiceRunStatus = ServiceRunStatus.PENDING,
) -> MagicMock:
    """Return a mock FactoryRun with configurable status."""
    run = MagicMock()
    run.id = run_id
    run.status = status
    run.config = MagicMock()
    run.config.stages = ["pm_planning", "quality_gate"]
    run.config.name = "Test Run"
    run.config.description = "test"
    run.stages_completed = []
    run.stages_failed = []
    run.recovery_point = None
    run.created_at = "2024-01-01T00:00:00+00:00"
    run.started_at = None
    run.updated_at = run.created_at
    run.completed_at = None
    run.metadata = {
        "current_stage": None,
        "last_successful_stage": None,
        "last_failed_stage": None,
        "failure": None,
    }
    return run


def _make_mock_factory_service() -> MagicMock:
    """Return a mock FactoryRunService with async methods."""
    service = MagicMock()
    service.list_runs = AsyncMock(return_value=[])
    service.get_run = AsyncMock(return_value=None)
    service.create_run = AsyncMock(return_value=_mock_factory_run("factory_abc123", ServiceRunStatus.PENDING))
    service.start_run = AsyncMock(return_value=_mock_factory_run("factory_abc123", ServiceRunStatus.RUNNING))
    service.cancel_run = AsyncMock(return_value=_mock_factory_run("factory_abc123", ServiceRunStatus.CANCELLED))
    service.get_run_events = AsyncMock(return_value=[])
    service.store = MagicMock()
    service.store.get_run_dir.return_value = MagicMock(
        __truediv__=lambda self, other: MagicMock(  # type: ignore[misc]
            exists=lambda: True,
            iterdir=lambda: [],
        ),
    )
    return service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Create an async test client with the factory router."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Happy Path Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFactoryRunLifecycle:
    """Happy path tests for the complete factory run lifecycle."""

    async def test_create_run(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs creates a run and returns CREATED status."""
        mock_service = _make_mock_factory_service()
        created_run = _mock_factory_run("factory_abc123", ServiceRunStatus.PENDING)
        mock_service.create_run.return_value = created_run
        mock_service.start_run.return_value = _mock_factory_run("factory_abc123", ServiceRunStatus.RUNNING)

        with (
            patch(
                "polaris.delivery.http.routers.factory.FactoryRunService",
                return_value=mock_service,
            ),
            patch(
                "polaris.delivery.http.routers.factory.sync_process_settings_environment",
            ),
            patch(
                "polaris.delivery.http.routers.factory.save_persisted_settings",
            ),
            patch(
                "polaris.delivery.http.routers.factory.create_task_with_context",
            ),
            patch(
                "polaris.delivery.http.routers.factory._check_docs_ready",
                return_value=True,
            ),
        ):
            response = await client.post(
                "/v2/factory/runs",
                json={
                    "workspace": ".",
                    "start_from": "pm",
                    "directive": "Build a thing",
                    "run_director": False,
                    "loop": False,
                    "director_iterations": 1,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert data["status"] == "running"
        mock_service.create_run.assert_awaited_once()
        mock_service.start_run.assert_awaited_once()

    async def test_get_run_created_status(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs/{id} returns CREATED/PENDING status before start."""
        mock_service = _make_mock_factory_service()
        pending_run = _mock_factory_run("factory_abc123", ServiceRunStatus.PENDING)
        mock_service.get_run.return_value = pending_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/factory_abc123")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert data["status"] == "pending"

    async def test_get_run_running_status(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs/{id} returns RUNNING status after start."""
        mock_service = _make_mock_factory_service()
        running_run = _mock_factory_run("factory_abc123", ServiceRunStatus.RUNNING)
        mock_service.get_run.return_value = running_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/factory_abc123")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert data["status"] == "running"

    async def test_cancel_run(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs/{id}/control with cancel returns CANCELLED status."""
        mock_service = _make_mock_factory_service()
        running_run = _mock_factory_run("factory_abc123", ServiceRunStatus.RUNNING)
        cancelled_run = _mock_factory_run("factory_abc123", ServiceRunStatus.CANCELLED)
        mock_service.get_run.return_value = running_run
        mock_service.cancel_run.return_value = cancelled_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.post(
                "/v2/factory/runs/factory_abc123/control",
                json={"action": "cancel", "reason": "user request"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert data["status"] == "cancelled"
        mock_service.cancel_run.assert_awaited_once_with("factory_abc123", "user request")

    async def test_get_run_cancelled_status(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs/{id} returns CANCELLED status after cancel."""
        mock_service = _make_mock_factory_service()
        cancelled_run = _mock_factory_run("factory_abc123", ServiceRunStatus.CANCELLED)
        mock_service.get_run.return_value = cancelled_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/factory_abc123")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert data["status"] == "cancelled"

    async def test_get_run_artifacts(self, client: AsyncClient, tmp_path: Path) -> None:
        """GET /v2/factory/runs/{id}/artifacts lists artifact files."""
        mock_service = _make_mock_factory_service()
        completed_run = _mock_factory_run("factory_abc123", ServiceRunStatus.COMPLETED)
        completed_run.metadata["summary_md"] = "# Summary"
        completed_run.metadata["summary_json"] = {"ok": True}
        mock_service.get_run.return_value = completed_run

        run_dir = tmp_path / "factory_abc123"
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "report.json").write_text('{"passed": true}')
        mock_service.store.get_run_dir.return_value = run_dir

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/factory_abc123/artifacts")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc123"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["name"] == "report.json"

    async def test_full_lifecycle(self, client: AsyncClient, tmp_path: Path) -> None:
        """Execute the complete happy path lifecycle end-to-end."""
        mock_service = _make_mock_factory_service()

        # Step 1: Create run
        pending_run = _mock_factory_run("factory_lifecycle01", ServiceRunStatus.PENDING)
        running_run = _mock_factory_run("factory_lifecycle01", ServiceRunStatus.RUNNING)
        cancelled_run = _mock_factory_run("factory_lifecycle01", ServiceRunStatus.CANCELLED)

        mock_service.create_run.return_value = pending_run
        mock_service.start_run.return_value = running_run
        # get_run is called by POST /v2/factory/runs (via _start_factory_run_core -> start_run)
        # and then by GET /v2/factory/runs/{id}, POST control, GET after cancel, GET artifacts
        mock_service.get_run.side_effect = [
            running_run,  # GET after create (if any)
            running_run,  # GET before cancel
            cancelled_run,  # GET after cancel
            cancelled_run,  # GET artifacts
        ]
        mock_service.cancel_run.return_value = cancelled_run

        run_dir = tmp_path / "factory_lifecycle01"
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "report.json").write_text('{"passed": true}')
        mock_service.store.get_run_dir.return_value = run_dir

        with (
            patch(
                "polaris.delivery.http.routers.factory.FactoryRunService",
                return_value=mock_service,
            ),
            patch(
                "polaris.delivery.http.routers.factory.sync_process_settings_environment",
            ),
            patch(
                "polaris.delivery.http.routers.factory.save_persisted_settings",
            ),
            patch(
                "polaris.delivery.http.routers.factory.create_task_with_context",
            ),
            patch(
                "polaris.delivery.http.routers.factory._check_docs_ready",
                return_value=True,
            ),
        ):
            # Create
            resp_create = await client.post(
                "/v2/factory/runs",
                json={
                    "workspace": ".",
                    "start_from": "pm",
                    "directive": "Full lifecycle test",
                    "run_director": False,
                    "loop": False,
                    "director_iterations": 1,
                },
            )
            assert resp_create.status_code == 200
            assert resp_create.json()["status"] == "running"

            # Verify running
            resp_get = await client.get("/v2/factory/runs/factory_lifecycle01")
            assert resp_get.status_code == 200
            assert resp_get.json()["status"] == "running"

            # Cancel
            resp_control = await client.post(
                "/v2/factory/runs/factory_lifecycle01/control",
                json={"action": "cancel", "reason": "test cleanup"},
            )
            assert resp_control.status_code == 200
            assert resp_control.json()["status"] == "cancelled"

            # Verify cancelled
            resp_get2 = await client.get("/v2/factory/runs/factory_lifecycle01")
            assert resp_get2.status_code == 200
            assert resp_get2.json()["status"] == "cancelled"

            # Artifacts
            resp_artifacts = await client.get("/v2/factory/runs/factory_lifecycle01/artifacts")
            assert resp_artifacts.status_code == 200
            assert len(resp_artifacts.json()["artifacts"]) == 1


# ---------------------------------------------------------------------------
# Error Paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFactoryRunErrorPaths:
    """Error path tests for factory run operations."""

    async def test_create_run_invalid_workspace(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs with invalid workspace returns 400."""
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
                    "workspace": "",
                    "start_from": "auto",
                    "directive": "test",
                },
            )

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "WORKSPACE_NOT_CONFIGURED"

    async def test_control_nonexistent_run(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs/{id}/control for non-existent run returns 404."""
        mock_service = _make_mock_factory_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.post(
                "/v2/factory/runs/nonexistent/control",
                json={"action": "cancel"},
            )

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"

    async def test_get_artifacts_nonexistent_run(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs/{id}/artifacts for non-existent run returns 404."""
        mock_service = _make_mock_factory_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/nonexistent/artifacts")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"

    async def test_invalid_control_action(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs/{id}/control with invalid action returns 501."""
        mock_service = _make_mock_factory_service()
        running_run = _mock_factory_run("factory_abc123", ServiceRunStatus.RUNNING)
        mock_service.get_run.return_value = running_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.post(
                "/v2/factory/runs/factory_abc123/control",
                json={"action": "pause"},
            )

        assert response.status_code == 501
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"
        assert "pause" in data["error"]["message"]

    async def test_get_run_not_found(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs/{id} for non-existent run returns 404."""
        mock_service = _make_mock_factory_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs/nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"

    async def test_create_run_validation_error(self, client: AsyncClient) -> None:
        """POST /v2/factory/runs with invalid payload returns 422."""
        response = await client.post("/v2/factory/runs", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# List and Filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFactoryRunListAndFilter:
    """Tests for listing and filtering factory runs."""

    async def test_list_all_runs(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs returns a paginated list of all runs."""
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = [
            {
                "id": "factory_run001",
                "name": "Run One",
                "status": "running",
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "current_stage": "pm_planning",
                "last_successful_stage": None,
                "stages_completed": 0,
                "stages_failed": 0,
            },
            {
                "id": "factory_run002",
                "name": "Run Two",
                "status": "completed",
                "created_at": "2024-01-02T00:00:00+00:00",
                "updated_at": "2024-01-02T00:00:00+00:00",
                "current_stage": "quality_gate",
                "last_successful_stage": "pm_planning",
                "stages_completed": 2,
                "stages_failed": 0,
            },
        ]

        run1 = _mock_factory_run("factory_run001", ServiceRunStatus.RUNNING)
        run2 = _mock_factory_run("factory_run002", ServiceRunStatus.COMPLETED)
        mock_service.get_run.side_effect = [run1, run2]

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["runs"]) == 2
        assert data["runs"][0]["run_id"] == "factory_run001"
        assert data["runs"][1]["run_id"] == "factory_run002"

    async def test_list_runs_empty(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs returns empty list when no runs exist."""
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = []

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["runs"] == []

    async def test_list_runs_pagination(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs supports pagination via limit and offset."""
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = [
            {
                "id": f"factory_run{i:03d}",
                "name": f"Run {i}",
                "status": "running",
                "created_at": f"2024-01-{i:02d}T00:00:00+00:00",
                "updated_at": f"2024-01-{i:02d}T00:00:00+00:00",
                "current_stage": "pm_planning",
                "last_successful_stage": None,
                "stages_completed": 0,
                "stages_failed": 0,
            }
            for i in range(1, 6)
        ]

        runs = [_mock_factory_run(f"factory_run{i:03d}", ServiceRunStatus.RUNNING) for i in range(1, 6)]
        mock_service.get_run.side_effect = runs[2:4]

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs?limit=2&offset=2")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["runs"]) == 2
        assert data["page_size"] == 2
        assert data["runs"][0]["run_id"] == "factory_run003"
        assert data["runs"][1]["run_id"] == "factory_run004"

    async def test_list_runs_filter_by_status(self, client: AsyncClient) -> None:
        """GET /v2/factory/runs returns runs that can be filtered by status."""
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = [
            {
                "id": "factory_run001",
                "name": "Run One",
                "status": "running",
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "current_stage": "pm_planning",
                "last_successful_stage": None,
                "stages_completed": 0,
                "stages_failed": 0,
            },
            {
                "id": "factory_run002",
                "name": "Run Two",
                "status": "completed",
                "created_at": "2024-01-02T00:00:00+00:00",
                "updated_at": "2024-01-02T00:00:00+00:00",
                "current_stage": "quality_gate",
                "last_successful_stage": "pm_planning",
                "stages_completed": 2,
                "stages_failed": 0,
            },
            {
                "id": "factory_run003",
                "name": "Run Three",
                "status": "failed",
                "created_at": "2024-01-03T00:00:00+00:00",
                "updated_at": "2024-01-03T00:00:00+00:00",
                "current_stage": "director_dispatch",
                "last_successful_stage": "pm_planning",
                "stages_completed": 1,
                "stages_failed": 1,
            },
        ]

        run1 = _mock_factory_run("factory_run001", ServiceRunStatus.RUNNING)
        run2 = _mock_factory_run("factory_run002", ServiceRunStatus.COMPLETED)
        run3 = _mock_factory_run("factory_run003", ServiceRunStatus.FAILED)
        mock_service.get_run.side_effect = [run1, run2, run3]

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = await client.get("/v2/factory/runs")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        statuses = {run["status"] for run in data["runs"]}
        assert statuses == {"running", "completed", "failed"}

    async def test_list_runs_pagination_edge_cases(self, client: AsyncClient) -> None:
        """Pagination handles offset beyond total and limit of zero gracefully."""
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = [
            {
                "id": "factory_run001",
                "name": "Run One",
                "status": "running",
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "current_stage": "pm_planning",
                "last_successful_stage": None,
                "stages_completed": 0,
                "stages_failed": 0,
            },
        ]

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            # Offset beyond total
            resp_offset = await client.get("/v2/factory/runs?limit=10&offset=100")
            assert resp_offset.status_code == 200
            data_offset = resp_offset.json()
            assert data_offset["total"] == 1
            assert data_offset["runs"] == []

            # Limit of zero
            resp_limit_zero = await client.get("/v2/factory/runs?limit=0&offset=0")
            assert resp_limit_zero.status_code == 200
            data_limit_zero = resp_limit_zero.json()
            assert data_limit_zero["total"] == 1
            assert data_limit_zero["runs"] == []
