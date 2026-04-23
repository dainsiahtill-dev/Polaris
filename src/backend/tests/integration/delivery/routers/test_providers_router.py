"""Contract tests for polaris.delivery.http.routers.providers module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import providers as providers_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(providers_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class _FakeProviderInfo:
    """Fake provider info object for testing."""

    def __init__(self) -> None:
        self.name = "TestProvider"
        self.type = "test"
        self.description = "A test provider"
        self.version = "1.0"
        self.author = "tester"
        self.documentation_url = "https://example.com"
        self.supported_features = ["chat"]
        self.cost_class = "low"


class TestProvidersRouter:
    """Contract tests for the providers router."""

    def test_list_providers_happy_path(self) -> None:
        """GET /llm/providers returns 200 with provider list."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.list_provider_info.return_value = [_FakeProviderInfo()]
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "providers" in payload
        assert len(payload["providers"]) == 1
        assert payload["providers"][0]["name"] == "TestProvider"

    def test_list_providers_runtime_error(self) -> None:
        """GET /llm/providers handles runtime error with 500."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.list_provider_info.side_effect = RuntimeError("boom")
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers")

        assert response.status_code == 500
        assert response.json()["detail"] == "internal error"

    def test_get_provider_info_happy_path(self) -> None:
        """GET /llm/providers/{type}/info returns 200 with provider info."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_info.return_value = _FakeProviderInfo()
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers/test/info")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["name"] == "TestProvider"
        assert payload["type"] == "test"

    def test_get_provider_info_not_found(self) -> None:
        """GET /llm/providers/{type}/info returns 404 when provider not found."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_info.return_value = None
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers/unknown/info")

        assert response.status_code == 404
        assert response.json()["detail"] == "Provider not found"

    def test_get_provider_default_config_happy_path(self) -> None:
        """GET /llm/providers/{type}/config returns 200 with default config."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_default_config.return_value = {
            "base_url": "https://api.test.com",
        }
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers/test/config")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["base_url"] == "https://api.test.com"

    def test_get_provider_default_config_not_found(self) -> None:
        """GET /llm/providers/{type}/config returns 404 when provider not found."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_default_config.return_value = None
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.get("/llm/providers/unknown/config")

        assert response.status_code == 404
        assert response.json()["detail"] == "Provider not found"

    def test_validate_provider_config_happy_path(self) -> None:
        """POST /llm/providers/{type}/validate returns validation result."""
        client = _build_client()
        fake_result = MagicMock()
        fake_result.valid = True
        fake_result.errors = []
        fake_result.warnings = []
        fake_result.normalized_config = {"model": "gpt-4"}

        fake_cls = MagicMock()
        fake_cls.validate_config.return_value = fake_result

        mock_manager = MagicMock()
        mock_manager.get_provider_class.return_value = fake_cls
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.post(
                "/llm/providers/test/validate",
                json={"api_key": "secret"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["valid"] is True
        assert payload["normalized_config"] == {"model": "gpt-4"}

    def test_validate_provider_config_unknown_type(self) -> None:
        """POST /llm/providers/{type}/validate returns invalid for unknown type."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_class.return_value = None
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.post(
                "/llm/providers/unknown/validate",
                json={},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["valid"] is False
        assert "unknown" in payload["errors"][0].lower()

    def test_health_check_all_happy_path(self) -> None:
        """POST /llm/providers/health-all returns aggregated health results."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.health_check_all.return_value = {"openai": {"ok": True}}
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ):
            response = client.post(
                "/llm/providers/health-all",
                json={"providers": {"openai": {}}},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["openai"]["ok"] is True

    def test_provider_health_happy_path(self) -> None:
        """POST /llm/providers/{id}/health returns health status."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_info.return_value = _FakeProviderInfo()
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ), patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context"
        ) as mock_resolve, patch(
            "polaris.delivery.http.routers.providers.run_provider_action"
        ) as mock_run:
            mock_resolve.return_value = SimpleNamespace(
                provider_cfg={"base_url": "https://api.test.com"},
                provider_type="test",
                api_key="secret",
            )
            mock_run.return_value = {"status": "healthy", "latency_ms": 42}
            response = client.post(
                "/llm/providers/test/health",
                json={"api_key": "secret", "headers": {}},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "healthy"
        assert payload["latency_ms"] == 42

    def test_provider_models_happy_path(self) -> None:
        """POST /llm/providers/{id}/models returns model list."""
        client = _build_client()
        mock_manager = MagicMock()
        mock_manager.get_provider_info.return_value = _FakeProviderInfo()
        with patch(
            "polaris.delivery.http.routers.providers._provider_manager",
            mock_manager,
        ), patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context"
        ) as mock_resolve, patch(
            "polaris.delivery.http.routers.providers.run_provider_action"
        ) as mock_run:
            mock_resolve.return_value = SimpleNamespace(
                provider_cfg={"base_url": "https://api.test.com"},
                provider_type="test",
                api_key="secret",
            )
            mock_run.return_value = {"models": [{"id": "gpt-4", "name": "GPT-4"}]}
            response = client.post(
                "/llm/providers/test/models",
                json={"api_key": "secret", "headers": {}},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "models" in payload
        assert len(payload["models"]) == 1
        assert payload["models"][0]["id"] == "gpt-4"
