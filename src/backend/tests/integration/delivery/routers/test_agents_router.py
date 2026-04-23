"""Contract tests for polaris.delivery.http.routers.agents module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import agents as agents_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(agents_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestAgentsRouter:
    """Contract tests for the agents router."""

    def test_apply_agents_happy_path(self) -> None:
        """POST /agents/apply returns 200 when draft is copied successfully."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.agents.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.agents.resolve_safe_path",
                return_value="/tmp/workspace/.polaris/cache/agents_draft.md",
            ),
            patch("os.path.isfile", side_effect=lambda p: "agents_draft.md" in p),
            patch("os.makedirs"),
            patch("shutil.copyfile") as mock_copy,
        ):
            response = client.post(
                "/agents/apply",
                json={"draft_path": "agents_draft.md"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "target_path" in payload
        mock_copy.assert_called_once()

    def test_apply_agents_draft_not_found(self) -> None:
        """POST /agents/apply returns 404 when draft file does not exist."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.agents.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.agents.resolve_safe_path",
                return_value="/tmp/workspace/missing.md",
            ),
            patch("os.path.isfile", return_value=False),
        ):
            response = client.post(
                "/agents/apply",
                json={"draft_path": "missing.md"},
            )

        assert response.status_code == 404
        assert response.json()["detail"] == "draft not found"

    def test_apply_agents_already_exists(self) -> None:
        """POST /agents/apply returns 409 when AGENTS.md already exists."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.agents.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.agents.resolve_safe_path",
                return_value="/tmp/workspace/.polaris/cache/agents_draft.md",
            ),
            patch(
                "os.path.isfile",
                side_effect=lambda p: "agents_draft.md" in p or p.endswith("AGENTS.md"),
            ),
        ):
            response = client.post(
                "/agents/apply",
                json={"draft_path": "agents_draft.md"},
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "AGENTS.md already exists"

    def test_save_agents_feedback_happy_path(self) -> None:
        """POST /agents/feedback returns 200 when feedback is saved."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.agents.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.agents.resolve_artifact_path",
                return_value="/tmp/workspace/.polaris/cache/agents_feedback.md",
            ),
            patch("os.path.isfile", return_value=False),
            patch("os.makedirs"),
            patch(
                "builtins.open",
                create=True,
            ) as mock_open,
            patch(
                "polaris.delivery.http.routers.agents.format_mtime",
                return_value="2026-04-24T00:00:00",
            ),
        ):
            response = client.post(
                "/agents/feedback",
                json={"text": "Great work!"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert "path" in payload
        mock_open.assert_called_once()

    def test_save_agents_feedback_clear_empty(self) -> None:
        """POST /agents/feedback with empty text clears feedback file."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.agents.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.agents.resolve_artifact_path",
                return_value="/tmp/workspace/.polaris/cache/agents_feedback.md",
            ),
            patch("os.path.isfile", return_value=True),
            patch("os.remove") as mock_remove,
        ):
            response = client.post(
                "/agents/feedback",
                json={"text": ""},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["cleared"] is True
        mock_remove.assert_called_once()

    def test_apply_agents_validation_error(self) -> None:
        """POST /agents/apply with invalid payload returns 422."""
        client = _build_client()
        response = client.post("/agents/apply", json={"draft_path": 123})
        assert response.status_code == 422

    def test_save_agents_feedback_validation_error(self) -> None:
        """POST /agents/feedback with invalid payload returns 422."""
        client = _build_client()
        response = client.post("/agents/feedback", json={"text": 123})
        assert response.status_code == 422
