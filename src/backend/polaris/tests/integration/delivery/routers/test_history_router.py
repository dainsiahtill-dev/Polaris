"""Contract tests for polaris.delivery.http.routers.history module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import history as history_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(history_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestHistoryRouter:
    """Contract tests for the history router."""

    def test_history_runs_happy_path(self) -> None:
        """GET /history/runs returns 200 with runs list."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.os.path.isdir",
                return_value=True,
            ),
            patch(
                "polaris.delivery.http.routers.history.os.scandir",
                return_value=[
                    MagicMock(is_dir=lambda: True, name="run-1", path="/tmp/run-1"),
                ],
            ),
            patch(
                "polaris.delivery.http.routers.history.os.path.isfile",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.history.format_mtime",
                return_value="2026-04-24T00:00:00",
            ),
            patch(
                "polaris.delivery.http.routers.history.list_archived_runs",
                return_value=[],
            ),
        ):
            response = client.get("/history/runs")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "runs" in payload

    def test_history_tasks_happy_path(self) -> None:
        """GET /history/tasks returns 200 with rounds list."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history._read_task_rounds",
                return_value=[
                    {"round_id": "r1", "timestamp": "2026-04-24T00:00:00"},
                ],
            ),
        ):
            response = client.get("/history/tasks")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert len(payload["rounds"]) == 1
        assert payload["rounds"][0]["round_id"] == "r1"

    def test_history_rounds_happy_path(self) -> None:
        """GET /history/rounds returns 200 with merged rounds."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history._load_merged_rounds",
                return_value=[
                    {"round_id": "r1", "timestamp": "2026-04-24T00:00:00"},
                ],
            ),
        ):
            response = client.get("/history/rounds")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert len(payload["rounds"]) == 1

    def test_history_factory_overview_happy_path(self) -> None:
        """GET /history/factory/overview returns 200 with summary and rounds."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history._load_merged_rounds",
                return_value=[
                    {
                        "round_id": "r1",
                        "timestamp": "2026-04-24T00:00:00",
                        "factory_flow": {
                            "pipeline_status": {"status": "passed"},
                            "non_director_execution": {"results": []},
                            "defect_loop": {"generated_count": 0},
                        },
                    },
                ],
            ),
        ):
            response = client.get("/history/factory/overview")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "summary" in payload
        assert "rounds" in payload
        assert payload["summary"]["passed_rounds"] == 1

    def test_history_round_detail_not_found(self) -> None:
        """GET /history/round/{round_id} returns 404 for missing round."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history._load_merged_rounds",
                return_value=[],
            ),
        ):
            response = client.get("/history/round/missing-id")

        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"].lower()

    def test_v2_history_runs_happy_path(self) -> None:
        """GET /v2/history/runs returns 200 with paginated runs."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.os.path.isdir",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.history.list_archived_runs",
                return_value=[
                    {
                        "run_id": "run-1",
                        "archive_timestamp": "2026-04-24T00:00:00",
                        "status": "completed",
                        "reason": "success",
                    },
                ],
            ),
        ):
            response = client.get("/v2/history/runs?limit=10&offset=0")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "runs" in payload
        assert "total" in payload

    def test_v2_history_run_manifest_not_found(self) -> None:
        """GET /v2/history/runs/{run_id}/manifest returns 404 when manifest missing."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_run_manifest",
                return_value=None,
            ),
        ):
            response = client.get("/v2/history/runs/run-1/manifest")

        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"].lower()

    def test_v2_history_run_manifest_happy_path(self) -> None:
        """GET /v2/history/runs/{run_id}/manifest returns 200 with manifest."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_run_manifest",
                return_value={"run_id": "run-1", "status": "ok"},
            ),
        ):
            response = client.get("/v2/history/runs/run-1/manifest")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["manifest"]["run_id"] == "run-1"

    def test_v2_history_run_events_happy_path(self) -> None:
        """GET /v2/history/runs/{run_id}/events returns 200 with events."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_run_events",
                return_value=[{"type": "test"}],
            ),
        ):
            response = client.get("/v2/history/runs/run-1/events")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["run_id"] == "run-1"
        assert payload["count"] == 1

    def test_v2_history_task_snapshots_happy_path(self) -> None:
        """GET /v2/history/tasks/snapshots returns 200 with snapshots."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.list_task_snapshots",
                return_value=[{"id": "snap-1"}],
            ),
        ):
            response = client.get("/v2/history/tasks/snapshots")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1

    def test_v2_history_factory_snapshots_happy_path(self) -> None:
        """GET /v2/history/factory/snapshots returns 200 with factory runs."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.list_factory_runs",
                return_value=[{"run_id": "f1"}],
            ),
        ):
            response = client.get("/v2/history/factory/snapshots")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["total"] == 1

    def test_history_run_manifest_v1_not_found(self) -> None:
        """GET /history/runs/{run_id}/manifest returns 404 when manifest missing."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_run_manifest",
                return_value=None,
            ),
        ):
            response = client.get("/history/runs/run-1/manifest")

        assert response.status_code == 404

    def test_history_task_snapshot_manifest_not_found(self) -> None:
        """GET /history/tasks/{snapshot_id}/manifest returns 404 when missing."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_task_snapshot_manifest",
                return_value=None,
            ),
        ):
            response = client.get("/history/tasks/snap-1/manifest")

        assert response.status_code == 404

    def test_history_factory_manifest_not_found(self) -> None:
        """GET /history/factory/{run_id}/manifest returns 404 when missing."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.history.get_factory_manifest",
                return_value=None,
            ),
        ):
            response = client.get("/history/factory/f1/manifest")

        assert response.status_code == 404
