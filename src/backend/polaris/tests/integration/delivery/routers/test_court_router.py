"""Contract tests for polaris.delivery.http.routers.court module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import court as court_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(court_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestCourtRouter:
    """Contract tests for the court router."""

    def test_get_topology_happy_path(self) -> None:
        """GET /court/topology returns 200 with topology data."""
        client = _build_client()
        mock_topology = [
            {"role_id": "emperor", "is_interactive": True},
            {"role_id": "pm", "is_interactive": True},
        ]
        mock_scenes = {"main": {"camera": "default"}}
        with (
            patch(
                "polaris.delivery.http.routers.court.get_court_topology",
                return_value=mock_topology,
            ),
            patch(
                "polaris.delivery.http.routers.court.get_scene_configs",
                return_value=mock_scenes,
            ),
        ):
            response = client.get("/court/topology")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["nodes"] == mock_topology
        assert payload["count"] == 2
        assert payload["total"] == 2
        assert payload["scenes"] == mock_scenes

    def test_get_state_happy_path(self) -> None:
        """GET /court/state returns 200 with court state."""
        client = _build_client()
        mock_state = {
            "phase": "draft",
            "current_scene": "main",
            "actors": {"pm": {"status": "active"}},
            "recent_events": [],
            "updated_at": 1234567890,
        }
        with (
            patch(
                "polaris.delivery.http.routers.court._get_engine_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_pm_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_director_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court.map_engine_to_court_state",
                return_value=mock_state,
            ),
        ):
            response = client.get("/court/state")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["phase"] == "draft"
        assert payload["actors"]["pm"]["status"] == "active"

    def test_get_actor_detail_happy_path(self) -> None:
        """GET /court/actors/{role_id} returns 200 for existing role."""
        client = _build_client()
        mock_state = {
            "actors": {
                "pm": {"status": "active", "tasks": []},
            },
        }
        mock_topology = [
            {"role_id": "pm", "name": "PM"},
        ]
        with (
            patch(
                "polaris.delivery.http.routers.court._get_engine_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_pm_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_director_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court.map_engine_to_court_state",
                return_value=mock_state,
            ),
            patch(
                "polaris.delivery.http.routers.court.get_court_topology",
                return_value=mock_topology,
            ),
        ):
            response = client.get("/court/actors/pm")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "active"
        assert payload["topology"]["name"] == "PM"

    def test_get_actor_detail_not_found(self) -> None:
        """GET /court/actors/{role_id} returns 404 for unknown role."""
        client = _build_client()
        mock_state: dict[str, Any] = {"actors": {}}
        with (
            patch(
                "polaris.delivery.http.routers.court._get_engine_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_pm_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court._get_director_status",
                return_value={"status": "ok"},
            ),
            patch(
                "polaris.delivery.http.routers.court.map_engine_to_court_state",
                return_value=mock_state,
            ),
        ):
            response = client.get("/court/actors/unknown_role")

        assert response.status_code == 404
        assert "unknown_role" in response.json()["detail"]

    def test_get_scene_detail_happy_path(self) -> None:
        """GET /court/scenes/{scene_id} returns 200 for existing scene."""
        client = _build_client()
        mock_scenes = {"main": {"camera": "default", "lights": []}}
        with patch(
            "polaris.delivery.http.routers.court.get_scene_configs",
            return_value=mock_scenes,
        ):
            response = client.get("/court/scenes/main")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["camera"] == "default"

    def test_get_scene_detail_not_found(self) -> None:
        """GET /court/scenes/{scene_id} returns 404 for unknown scene."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.court.get_scene_configs",
            return_value={},
        ):
            response = client.get("/court/scenes/unknown")

        assert response.status_code == 404
        assert "unknown" in response.json()["detail"]

    def test_get_role_mapping_happy_path(self) -> None:
        """GET /court/mapping returns 200 with role mapping."""
        client = _build_client()
        mock_mapping = {"pm": "PM"}
        mock_topology = [MagicMock(role_id="pm")]
        with (
            patch(
                "polaris.delivery.http.routers.court.TECH_TO_COURT_ROLE_MAPPING",
                mock_mapping,
            ),
            patch(
                "polaris.delivery.http.routers.court.COURT_TOPOLOGY",
                mock_topology,
            ),
        ):
            response = client.get("/court/mapping")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["tech_to_court"] == mock_mapping
        assert payload["version"] == "1.0"
