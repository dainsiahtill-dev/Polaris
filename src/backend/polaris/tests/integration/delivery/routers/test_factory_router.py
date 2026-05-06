"""Contract tests for polaris.delivery.http.routers.factory module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import factory as factory_router
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.error_handlers import setup_exception_handlers


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


def _mock_run(run_id: str = "run-1", status: Any = None) -> MagicMock:
    """Return a mock FactoryRun."""
    run = MagicMock()
    run.id = run_id
    run.status = status or MagicMock()
    run.config.stages = ["pm_planning", "quality_gate"]
    run.stages_completed = []
    run.stages_failed = []
    run.metadata = {}
    run.recovery_point = None
    run.created_at = "2026-04-24T00:00:00"
    run.started_at = None
    run.updated_at = None
    run.completed_at = None
    return run


def _make_mock_service() -> MagicMock:
    """Return a mock FactoryRunService with async methods."""
    service = MagicMock()
    service.list_runs = AsyncMock(return_value=[])
    service.get_run = AsyncMock(return_value=None)
    service.create_run = AsyncMock(return_value=_mock_run("run-1"))
    service.start_run = AsyncMock(return_value=_mock_run("run-1"))
    service.get_run_events = AsyncMock(return_value=[])
    service.cancel_run = AsyncMock(return_value=_mock_run("run-1"))
    service.store = MagicMock()
    mock_artifact = MagicMock()
    mock_artifact.is_file = lambda: True
    mock_artifact.name = "artifact.txt"
    mock_artifact.relative_to = lambda p: "artifact.txt"
    mock_artifact.stat = lambda: MagicMock(st_size=100)

    service.store.get_run_dir.return_value = MagicMock(
        __truediv__=lambda self, other: MagicMock(
            exists=lambda: True,
            iterdir=lambda: [mock_artifact],
        ),
    )
    return service


class TestFactoryRouter:
    """Contract tests for the factory router."""

    def test_list_factory_runs_happy_path(self) -> None:
        """GET /v2/factory/runs returns 200 with run list."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_service.list_runs.return_value = [
            {"id": "run-1", "created_at": "2026-04-24T00:00:00"},
        ]
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1
        assert len(payload["runs"]) == 1

    def test_start_factory_run_happy_path(self) -> None:
        """POST /v2/factory/runs returns 200 with run status."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.create_run.return_value = mock_run
        mock_service.start_run.return_value = mock_run

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
            response = client.post(
                "/v2/factory/runs",
                json={
                    "directive": "test",
                    "workspace": ".",
                    "start_from": "pm",
                    "run_director": False,
                    "loop": False,
                    "director_iterations": 1,
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_start_factory_run_validation_error(self) -> None:
        """POST /v2/factory/runs with invalid payload returns 422."""
        client = _build_client()
        response = client.post("/v2/factory/runs", json={})
        assert response.status_code == 422

    def test_get_factory_run_status_happy_path(self) -> None:
        """GET /v2/factory/runs/{run_id} returns 200 with run status."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/run-1")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_get_factory_run_status_not_found(self) -> None:
        """GET /v2/factory/runs/{run_id} returns 404 for missing run."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/missing-id")

        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"].lower()

    def test_get_factory_run_events_happy_path(self) -> None:
        """GET /v2/factory/runs/{run_id}/events returns 200 with events."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run
        mock_service.get_run_events.return_value = [{"type": "test"}]

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/run-1/events")

        assert response.status_code == 200
        payload: list[dict[str, Any]] = response.json()
        assert len(payload) == 1

    def test_get_factory_run_events_not_found(self) -> None:
        """GET /v2/factory/runs/{run_id}/events returns 404 for missing run."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/missing-id/events")

        assert response.status_code == 404

    def test_control_factory_run_cancel_happy_path(self) -> None:
        """POST /v2/factory/runs/{run_id}/control cancel returns 200."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run
        mock_service.cancel_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.post(
                "/v2/factory/runs/run-1/control",
                json={"action": "cancel", "reason": "test"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"

    def test_control_factory_run_not_found(self) -> None:
        """POST /v2/factory/runs/{run_id}/control returns 404 for missing run."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.post(
                "/v2/factory/runs/missing-id/control",
                json={"action": "cancel", "reason": "test"},
            )

        assert response.status_code == 404

    def test_control_factory_run_unsupported_action(self) -> None:
        """POST /v2/factory/runs/{run_id}/control with unsupported action returns 501."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.post(
                "/v2/factory/runs/run-1/control",
                json={"action": "pause", "reason": "test"},
            )

        assert response.status_code == 501
        assert "not implemented" in response.json()["error"]["message"].lower()

    def test_get_factory_run_artifacts_happy_path(self) -> None:
        """GET /v2/factory/runs/{run_id}/artifacts returns 200 with artifacts."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_run = _mock_run("run-1")
        mock_service.get_run.return_value = mock_run

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/run-1/artifacts")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"
        assert len(payload["artifacts"]) == 1

    def test_get_factory_run_artifacts_not_found(self) -> None:
        """GET /v2/factory/runs/{run_id}/artifacts returns 404 for missing run."""
        client = _build_client()
        mock_service = _make_mock_service()
        mock_service.get_run.return_value = None

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            response = client.get("/v2/factory/runs/missing-id/artifacts")

        assert response.status_code == 404
