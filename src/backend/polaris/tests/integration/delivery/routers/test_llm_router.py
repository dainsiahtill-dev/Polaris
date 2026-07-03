"""Contract tests for polaris.delivery.http.routers.llm module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import llm as llm_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(llm_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestLlmRouter:
    """Contract tests for the LLM router."""

    def test_get_llm_config_happy_path(self) -> None:
        """GET /v2/llm/config returns 200 with redacted config."""
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
            response = client.get("/v2/llm/config")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["redacted"] is True

    def test_save_llm_config_happy_path(self) -> None:
        """POST /v2/llm/config returns 200 with saved config."""
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
                "polaris.delivery.http.routers.llm.apply_llm_config_updates_to_settings",
            ),
            patch(
                "polaris.delivery.http.routers.llm.save_persisted_settings",
            ),
        ):
            response = client.post("/v2/llm/config", json={"config": {"provider": "test"}})

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["saved"] is True

    def test_save_llm_config_invalid_payload(self) -> None:
        """POST /v2/llm/config with non-dict payload returns 400."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value="/tmp/cache",
        ):
            response = client.post("/v2/llm/config", json={"config": "not-a-dict"})
        assert response.status_code == 400
        assert response.json()["error"]["message"] == "invalid config payload"

    def test_save_llm_config_validation_error_returns_400(self) -> None:
        """POST /v2/llm/config exposes validation failures as client errors."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.llm.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.llm.llm_config.save_llm_config",
                side_effect=ValueError("Invalid LLM configuration: provider timeout too high"),
            ),
        ):
            response = client.post("/v2/llm/config", json={"providers": {}, "roles": {}})

        assert response.status_code == 400
        payload: dict[str, Any] = response.json()
        assert payload["error"]["code"] == "INVALID_LLM_CONFIG"
        assert "provider timeout too high" in payload["error"]["message"]

    def test_migrate_config_happy_path(self) -> None:
        """POST /v2/llm/config/migrate returns 200 with migrated config."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.migrate_legacy_config.return_value = {"migrated": True}
        with patch(
            "polaris.delivery.http.routers.llm._provider_manager",
            mock_manager,
        ):
            response = client.post("/v2/llm/config/migrate", json={"old": "config"})

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["migrated"] is True
        mock_manager.migrate_legacy_config.assert_called_once_with({"old": "config"})

    def test_migrate_config_runtime_error(self) -> None:
        """POST /v2/llm/config/migrate handles runtime error with 500."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.migrate_legacy_config.side_effect = ValueError("bad config")
        with patch(
            "polaris.delivery.http.routers.llm._provider_manager",
            mock_manager,
        ):
            response = client.post("/v2/llm/config/migrate", json={})

        assert response.status_code == 500
        assert response.json()["error"]["message"] == "internal error"

    def test_llm_status_happy_path(self) -> None:
        """GET /v2/llm/status returns 200 with status payload."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.llm.build_llm_status",
            return_value={"ready": True},
        ):
            response = client.get("/v2/llm/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True

    def test_get_runtime_status_happy_path(self) -> None:
        """GET /v2/llm/runtime-status returns 200 with roles status."""
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
            response = client.get("/v2/llm/runtime-status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "roles" in payload
        assert "timestamp" in payload
        for role in ("pm", "chief_engineer", "director", "qa", "architect"):
            assert role in payload["roles"]

    def test_get_role_runtime_status_happy_path(self) -> None:
        """GET /v2/llm/runtime-status/{role_id} returns 200 for valid role."""
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
            response = client.get("/v2/llm/runtime-status/director")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["roleId"] == "director"
        assert "running" in payload

    def test_get_role_runtime_status_chief_engineer(self) -> None:
        """GET /v2/llm/runtime-status/{role_id} returns 200 for Chief Engineer."""
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
            response = client.get("/v2/llm/runtime-status/chief_engineer")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["roleId"] == "chief_engineer"

    def test_get_role_runtime_status_invalid_role(self) -> None:
        """GET /v2/llm/runtime-status/{role_id} returns 400 for invalid role."""
        client = _build_client()
        response = client.get("/v2/llm/runtime-status/invalid_role")
        assert response.status_code == 400
        assert response.json()["error"]["message"] == "invalid role_id"

    def test_get_role_runtime_status_docs_alias(self) -> None:
        """GET /v2/llm/runtime-status/docs maps to architect role."""
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
            response = client.get("/v2/llm/runtime-status/docs")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["roleId"] == "architect"

    def test_retired_non_v2_llm_aliases_are_not_registered(self) -> None:
        """Non-v2 LLM aliases are retired; callers must use /v2/llm/*."""
        client = _build_client()

        responses = (
            client.get("/llm/config"),
            client.post("/llm/config", json={}),
            client.post("/llm/config/migrate", json={}),
            client.get("/llm/status"),
            client.get("/llm/runtime-status"),
            client.get("/llm/runtime-status/director"),
        )

        assert all(response.status_code == 404 for response in responses)
