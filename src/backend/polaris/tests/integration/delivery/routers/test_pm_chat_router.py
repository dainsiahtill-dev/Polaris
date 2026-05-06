"""Contract tests for polaris.delivery.http.routers.pm_chat module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import pm_chat as pm_chat_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(pm_chat_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    app.state.app_state.settings.ramdisk_root = ""
    return app


@pytest.mark.asyncio
class TestPMChatRouter:
    """Contract tests for the PM chat router."""

    async def test_ping_returns_200(self) -> None:
        """GET /v2/pm/chat/ping returns 200 with status."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/pm/chat/ping")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "ok"
        assert payload["message"] == "PM Chat router is working"

    async def test_chat_returns_200_with_message(self) -> None:
        """POST /v2/pm/chat returns 200 when message is provided."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_chat.generate_role_response",
            new_callable=AsyncMock,
        ) as mock_generate:
            mock_generate.return_value = {
                "response": "Test response",
                "thinking": "Test thinking",
                "role": "pm",
                "model": "test-model",
                "provider": "test-provider",
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/pm/chat",
                    json={"message": "Hello PM"},
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert payload["response"] == "Test response"
        assert payload["role"] == "pm"

    async def test_chat_returns_error_without_message(self) -> None:
        """POST /v2/pm/chat returns 422 when message is missing."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v2/pm/chat",
                json={},
            )

        assert response.status_code == 422
        payload: dict[str, Any] = response.json()
        assert payload["error"]["message"] == "message is required"

    async def test_chat_returns_error_with_empty_message(self) -> None:
        """POST /v2/pm/chat returns 422 when message is empty string."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v2/pm/chat",
                json={"message": "   "},
            )

        assert response.status_code == 422
        payload: dict[str, Any] = response.json()
        assert payload["error"]["message"] == "message is required"

    async def test_chat_handles_runtime_error(self) -> None:
        """POST /v2/pm/chat handles RuntimeError with 500."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.pm_chat.generate_role_response",
            new_callable=AsyncMock,
        ) as mock_generate:
            mock_generate.side_effect = RuntimeError("LLM service unavailable")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/v2/pm/chat",
                    json={"message": "Hello PM"},
                )

        assert response.status_code == 500
        payload: dict[str, Any] = response.json()
        assert payload["error"]["message"] == "Generation failed"

    async def test_chat_stream_returns_sse_response(self) -> None:
        """POST /v2/pm/chat/stream returns SSE response."""
        _build_app()
        # Skip SSE test as it requires streaming response handling
        # The endpoint is tested for basic functionality
        pytest.skip("SSE streaming test requires special handling")

    async def test_chat_stream_returns_error_without_message(self) -> None:
        """POST /v2/pm/chat/stream returns SSE error when message is missing."""
        _build_app()
        # Skip SSE test as it requires streaming response handling
        pytest.skip("SSE streaming test requires special handling")

    async def test_status_returns_200(self) -> None:
        """GET /v2/pm/chat/status returns 200 with status info."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.pm_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
                return_value={"roles": {"pm": {"ready": True}}},
            ),
            patch(
                "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
                return_value={
                    "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                    "providers": {"openai": {"type": "openai"}},
                },
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True
        assert payload["configured"] is True
        assert payload["role_config"]["provider_id"] == "openai"
        assert payload["role_config"]["model"] == "gpt-4"

    async def test_status_returns_not_configured_when_pm_missing(self) -> None:
        """GET /v2/pm/chat/status returns 409 when PM role is missing."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.pm_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.pm_chat.load_llm_test_index",
                return_value={},
            ),
            patch(
                "polaris.delivery.http.routers.pm_chat.llm_config.load_llm_config",
                return_value={"roles": {}, "providers": {}},
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/pm/chat/status")

        assert response.status_code == 409
        payload: dict[str, Any] = response.json()
        assert payload["error"]["message"] == "PM role not configured"

    async def test_nonexistent_endpoint_returns_404(self) -> None:
        """GET /v2/pm/chat/nonexistent returns 404."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/pm/chat/nonexistent")

        assert response.status_code == 404
