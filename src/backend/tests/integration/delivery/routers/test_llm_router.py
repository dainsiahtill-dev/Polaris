"""Contract tests for polaris.delivery.http.routers.llm module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import llm as llm_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(llm_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestLlmRouter:
    """Contract tests for the LLM router."""

    def test_get_llm_config_happy_path(self) -> None:
        """GET /llm/config returns 200 with redacted config."""
        client = _build_client()
        mock_config: dict[str, Any] = {"providers": {}, "roles": {}}
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.load_llm_config",
                return_value=mock_config,
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.redact_llm_config",
                return_value={"providers": {}, "roles": {}, "redacted": True},
            ),
        ):
            response = client.get("/llm/config")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["redacted"] is True

    def test_save_llm_config_happy_path(self) -> None:
        """POST /llm/config returns 200 with saved config."""
        client = _build_client()
        mock_config: dict[str, Any] = {"providers": {}, "roles": {}}
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.save_llm_config",
                return_value=mock_config,
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.redact_llm_config",
                return_value={"providers": {}, "roles": {}, "saved": True},
            ),
            patch(
                "polaris.delivery.http.routers.llm.reconcile_llm_test_index",
            ),
            patch(
                "polaris.delivery.http.routers.llm.sync_settings_from_llm",
            ),
            patch(
                "polaris.delivery.http.routers.llm.save_persisted_settings",
            ),
        ):
            response = client.post("/llm/config", json={"config": {"provider": "test"}})

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["saved"] is True

    def test_save_llm_config_invalid_payload(self) -> None:
        """POST /llm/config with non-dict payload returns 400."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value="/tmp/cache",
        ):
            response = client.post("/llm/config", json={"config": "not-a-dict"})
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid config payload"

    def test_migrate_config_happy_path(self) -> None:
        """POST /llm/config/migrate returns 200 with migrated config."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.migrate_legacy_config.return_value = {"migrated": True}
        with patch(
            "polaris.delivery.http.routers.llm._provider_manager",
            mock_manager,
        ):
            response = client.post("/llm/config/migrate", json={"old": "config"})

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["migrated"] is True
        mock_manager.migrate_legacy_config.assert_called_once_with({"old": "config"})

    def test_migrate_config_runtime_error(self) -> None:
        """POST /llm/config/migrate handles runtime error with 500."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.migrate_legacy_config.side_effect = ValueError("bad config")
        with patch(
            "polaris.delivery.http.routers.llm._provider_manager",
            mock_manager,
        ):
            response = client.post("/llm/config/migrate", json={})

        assert response.status_code == 500
        assert response.json()["detail"] == "internal error"

    def test_llm_status_happy_path(self) -> None:
        """GET /llm/status returns 200 with status payload."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.llm.build_llm_status",
            return_value={"ready": True},
        ):
            response = client.get("/llm/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True

    def test_get_runtime_status_happy_path(self) -> None:
        """GET /llm/runtime-status returns 200 with roles status."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.resolve_artifact_path",
                return_value="/tmp/runtime",
            ),
            patch(
                "polaris.delivery.http.routers.llm.os.path.exists",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.llm.load_role_config",
                return_value=None,
            ),
        ):
            response = client.get("/llm/runtime-status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "roles" in payload
        assert "timestamp" in payload
        for role in ("pm", "director", "qa", "architect"):
            assert role in payload["roles"]

    def test_get_role_runtime_status_happy_path(self) -> None:
        """GET /llm/runtime-status/{role_id} returns 200 for valid role."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.resolve_artifact_path",
                return_value="/tmp/runtime",
            ),
            patch(
                "polaris.delivery.http.routers.llm.os.path.exists",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.llm.load_role_config",
                return_value=None,
            ),
        ):
            response = client.get("/llm/runtime-status/director")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["roleId"] == "director"
        assert "running" in payload

    def test_get_role_runtime_status_invalid_role(self) -> None:
        """GET /llm/runtime-status/{role_id} returns 400 for invalid role."""
        client = _build_client()
        response = client.get("/llm/runtime-status/invalid_role")
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid role_id"

    def test_get_role_runtime_status_docs_alias(self) -> None:
        """GET /llm/runtime-status/docs maps to architect role."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.resolve_artifact_path",
                return_value="/tmp/runtime",
            ),
            patch(
                "polaris.delivery.http.routers.llm.os.path.exists",
                return_value=False,
            ),
            patch(
                "polaris.delivery.http.routers.llm.load_role_config",
                return_value=None,
            ),
        ):
            response = client.get("/llm/runtime-status/docs")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["roleId"] == "architect"
