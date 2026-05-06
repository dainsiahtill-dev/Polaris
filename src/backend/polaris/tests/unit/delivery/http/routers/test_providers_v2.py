"""Tests for Polaris v2 LLM provider routes.

Covers v2 endpoints under /v2/llm/providers/*.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

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
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProviderInfo:
    """Fake ProviderInfo with a real __dict__ for list_providers."""

    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs.get("name", "Test Provider")
        self.type = kwargs.get("type", "openai_compat")
        self.description = kwargs.get("description", "A test provider")
        self.version = kwargs.get("version", "1.0.0")
        self.author = kwargs.get("author", "test")
        self.documentation_url = kwargs.get("documentation_url", "https://example.com")
        self.supported_features = kwargs.get("supported_features", ["chat", "streaming"])
        self.cost_class = kwargs.get("cost_class", "METERED")
        self.provider_category = kwargs.get("provider_category", "LLM")
        self.autonomous_file_access = kwargs.get("autonomous_file_access", False)
        self.requires_file_interfaces = kwargs.get("requires_file_interfaces", True)
        self.model_listing_method = kwargs.get("model_listing_method", "API")


@dataclass
class _FakeValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    normalized_config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# GET /v2/llm/providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_providers_success(client: AsyncClient) -> None:
    """List providers should return all registered providers."""
    mock_info = _FakeProviderInfo(name="OpenAI", type="openai_compat")

    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.list_provider_info.return_value = [mock_info]

        response = await client.get("/v2/llm/providers")
        assert response.status_code == 200
        data = response.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["name"] == "OpenAI"
        assert data["providers"][0]["type"] == "openai_compat"


@pytest.mark.asyncio
async def test_v2_list_providers_empty(client: AsyncClient) -> None:
    """List providers should return empty list when no providers registered."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.list_provider_info.return_value = []

        response = await client.get("/v2/llm/providers")
        assert response.status_code == 200
        data = response.json()
        assert data["providers"] == []


@pytest.mark.asyncio
async def test_v2_list_providers_internal_error(client: AsyncClient) -> None:
    """List providers should return 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.list_provider_info.side_effect = RuntimeError("registry failure")

        response = await client.get("/v2/llm/providers")
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# GET /v2/llm/providers/{provider_type}/info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_provider_info_success(client: AsyncClient) -> None:
    """Get provider info should return detailed provider metadata."""
    mock_info = _FakeProviderInfo(name="Anthropic", type="anthropic_compat")

    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_info.return_value = mock_info

        response = await client.get("/v2/llm/providers/anthropic_compat/info")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Anthropic"
        assert data["type"] == "anthropic_compat"


@pytest.mark.asyncio
async def test_v2_get_provider_info_not_found(client: AsyncClient) -> None:
    """Get provider info should 404 for unknown provider type."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_info.return_value = None

        response = await client.get("/v2/llm/providers/unknown/info")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_get_provider_info_internal_error(client: AsyncClient) -> None:
    """Get provider info should return 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_info.side_effect = ValueError("bad config")

        response = await client.get("/v2/llm/providers/openai_compat/info")
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# GET /v2/llm/providers/{provider_type}/config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_provider_config_success(client: AsyncClient) -> None:
    """Get provider default config should return config dict."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_default_config.return_value = {"base_url": "https://api.openai.com", "timeout": 30}

        response = await client.get("/v2/llm/providers/openai_compat/config")
        assert response.status_code == 200
        data = response.json()
        assert data["base_url"] == "https://api.openai.com"
        assert data["timeout"] == 30


@pytest.mark.asyncio
async def test_v2_get_provider_config_not_found(client: AsyncClient) -> None:
    """Get provider default config should 404 for unknown provider type."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_default_config.return_value = None

        response = await client.get("/v2/llm/providers/unknown/config")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_get_provider_config_internal_error(client: AsyncClient) -> None:
    """Get provider default config should return 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_default_config.side_effect = RuntimeError("config load failed")

        response = await client.get("/v2/llm/providers/openai_compat/config")
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# POST /v2/llm/providers/{provider_type}/validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_validate_provider_config_success(client: AsyncClient) -> None:
    """Validate provider config should return validation result."""
    mock_provider_class = MagicMock()
    mock_provider_class.validate_config.return_value = _FakeValidationResult(
        valid=True,
        errors=[],
        warnings=[],
        normalized_config={"base_url": "https://api.openai.com"},
    )

    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_class.return_value = mock_provider_class

        response = await client.post(
            "/v2/llm/providers/openai_compat/validate",
            json={"base_url": "https://api.openai.com"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []
        assert data["normalized_config"]["base_url"] == "https://api.openai.com"


@pytest.mark.asyncio
async def test_v2_validate_provider_config_invalid(client: AsyncClient) -> None:
    """Validate provider config should return errors for invalid config."""
    mock_provider_class = MagicMock()
    mock_provider_class.validate_config.return_value = _FakeValidationResult(
        valid=False,
        errors=["missing api_key"],
        warnings=["timeout not set"],
        normalized_config=None,
    )

    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_class.return_value = mock_provider_class

        response = await client.post(
            "/v2/llm/providers/openai_compat/validate",
            json={},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "missing api_key" in data["errors"]
        assert "timeout not set" in data["warnings"]


@pytest.mark.asyncio
async def test_v2_validate_provider_config_not_found(client: AsyncClient) -> None:
    """Validate provider config should 404 for unknown provider type."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_class.return_value = None

        response = await client.post(
            "/v2/llm/providers/unknown/validate",
            json={"base_url": "https://example.com"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"
        assert "unknown" in data["error"]["message"]


@pytest.mark.asyncio
async def test_v2_validate_provider_config_internal_error(client: AsyncClient) -> None:
    """Validate provider config should return 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.get_provider_class.side_effect = RuntimeError("registry down")

        response = await client.post(
            "/v2/llm/providers/openai_compat/validate",
            json={},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# POST /v2/llm/providers/{provider_id}/health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_provider_health_success(client: AsyncClient) -> None:
    """Provider health check should return health status."""
    mock_context = MagicMock()
    mock_context.provider_cfg = {"base_url": "https://api.openai.com"}
    mock_context.provider_type = "openai_compat"
    mock_context.api_key = "sk-test"

    with (
        patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context",
            return_value=mock_context,
        ),
        patch(
            "polaris.delivery.http.routers.providers.run_provider_action",
            return_value={"ok": True, "status": "healthy", "latency_ms": 120},
        ) as mock_action,
    ):
        response = await client.post(
            "/v2/llm/providers/openai/health",
            json={"api_key": "sk-test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["status"] == "healthy"
        mock_action.assert_called_once_with(
            action="health",
            provider_type="openai_compat",
            provider_cfg={"base_url": "https://api.openai.com"},
            api_key="sk-test",
        )


@pytest.mark.asyncio
async def test_v2_provider_health_provider_not_found(client: AsyncClient) -> None:
    """Provider health check should 404 when provider_id is not found in config."""
    from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError

    with patch(
        "polaris.delivery.http.routers.providers.resolve_provider_request_context",
        side_effect=ProviderNotFoundError("openai"),
    ):
        response = await client.post(
            "/v2/llm/providers/openai/health",
            json={},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_provider_health_validation_error(client: AsyncClient) -> None:
    """Provider health check should 400 on config validation errors."""
    from polaris.cells.llm.provider_config.public.contracts import ProviderConfigValidationError

    with patch(
        "polaris.delivery.http.routers.providers.resolve_provider_request_context",
        side_effect=ProviderConfigValidationError("missing required field"),
    ):
        response = await client.post(
            "/v2/llm/providers/openai/health",
            json={},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_v2_provider_health_runtime_error(client: AsyncClient) -> None:
    """Provider health check should 500 on runtime errors."""
    from polaris.cells.llm.provider_runtime.public.contracts import LlmProviderRuntimeError

    mock_context = MagicMock()
    mock_context.provider_cfg = {"base_url": "https://api.openai.com"}
    mock_context.provider_type = "openai_compat"
    mock_context.api_key = None

    with (
        patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context",
            return_value=mock_context,
        ),
        patch(
            "polaris.delivery.http.routers.providers.run_provider_action",
            side_effect=LlmProviderRuntimeError("connection refused", code="provider_health_failed"),
        ),
    ):
        response = await client.post(
            "/v2/llm/providers/openai/health",
            json={},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# POST /v2/llm/providers/{provider_id}/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_provider_models_success(client: AsyncClient) -> None:
    """Provider models list should return available models."""
    mock_context = MagicMock()
    mock_context.provider_cfg = {"base_url": "https://api.openai.com"}
    mock_context.provider_type = "openai_compat"
    mock_context.api_key = "sk-test"

    with (
        patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context",
            return_value=mock_context,
        ),
        patch(
            "polaris.delivery.http.routers.providers.run_provider_action",
            return_value={
                "ok": True,
                "models": [
                    {"id": "gpt-4", "name": "GPT-4"},
                    {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
                ],
            },
        ) as mock_action,
    ):
        response = await client.post(
            "/v2/llm/providers/openai/models",
            json={"api_key": "sk-test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["models"]) == 2
        mock_action.assert_called_once_with(
            action="models",
            provider_type="openai_compat",
            provider_cfg={"base_url": "https://api.openai.com"},
            api_key="sk-test",
        )


@pytest.mark.asyncio
async def test_v2_provider_models_provider_not_found(client: AsyncClient) -> None:
    """Provider models list should 404 when provider_id is not found."""
    from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError

    with patch(
        "polaris.delivery.http.routers.providers.resolve_provider_request_context",
        side_effect=ProviderNotFoundError("unknown"),
    ):
        response = await client.post(
            "/v2/llm/providers/unknown/models",
            json={},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_provider_models_unsupported_provider(client: AsyncClient) -> None:
    """Provider models list should map UnsupportedProviderTypeError to 400."""
    from polaris.cells.llm.provider_runtime.public.contracts import UnsupportedProviderTypeError

    mock_context = MagicMock()
    mock_context.provider_cfg = {"base_url": "https://api.openai.com"}
    mock_context.provider_type = "unknown_type"
    mock_context.api_key = None

    with (
        patch(
            "polaris.delivery.http.routers.providers.resolve_provider_request_context",
            return_value=mock_context,
        ),
        patch(
            "polaris.delivery.http.routers.providers.run_provider_action",
            side_effect=UnsupportedProviderTypeError("unknown_type"),
        ),
    ):
        response = await client.post(
            "/v2/llm/providers/unknown/models",
            json={},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# POST /v2/llm/providers/health-all  (bulk health check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_health_check_all_success(client: AsyncClient) -> None:
    """Bulk health check should return results for all configured providers."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.health_check_all.return_value = {
            "openai": {"ok": True, "status": "healthy"},
            "anthropic": {"ok": False, "error": "timeout"},
        }

        response = await client.post(
            "/v2/llm/providers/health-all",
            json={
                "providers": {
                    "openai": {"type": "openai_compat", "api_key": "sk-test"},
                    "anthropic": {"type": "anthropic_compat", "api_key": "sk-test"},
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["openai"]["ok"] is True
        assert data["anthropic"]["ok"] is False
        assert data["anthropic"]["error"] == "timeout"


@pytest.mark.asyncio
async def test_v2_health_check_all_empty(client: AsyncClient) -> None:
    """Bulk health check with empty payload should return empty results."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.health_check_all.return_value = {}

        response = await client.post(
            "/v2/llm/providers/health-all",
            json={},
        )
        assert response.status_code == 200
        data = response.json()
        assert data == {}


@pytest.mark.asyncio
async def test_v2_health_check_all_internal_error(client: AsyncClient) -> None:
    """Bulk health check should return 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.routers.providers._provider_manager",
    ) as mock_mgr:
        mock_mgr.health_check_all.side_effect = RuntimeError("health check crashed")

        response = await client.post(
            "/v2/llm/providers/health-all",
            json={"providers": {"openai": {"type": "openai_compat"}}},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"
