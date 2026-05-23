"""Contract tests for polaris.delivery.http.routers.role_chat module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from polaris.cells.roles.kernel.public.service import LLMCallEvent
from polaris.delivery.http.auth.roles import UserRole
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import role_chat as role_chat_router
from polaris.delivery.http.routers._shared import require_auth
from polaris.kernelone.auth_context import SimpleAuthContext
from starlette.responses import Response


def _build_app() -> FastAPI:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(role_chat_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    app.state.app_state.settings.workspace_path = "."
    app.state.app_state.settings.ramdisk_root = ""

    @app.middleware("http")
    async def _trusted_test_role(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.auth_context = SimpleAuthContext(
            principal="test",
            auth_token="test-token",
            scopes=frozenset({"*"}),
            metadata={"roles": [UserRole.ADMIN.value]},
        )
        return await call_next(request)

    return app


@pytest.mark.asyncio
class TestRoleChatRouter:
    """Contract tests for the role chat router."""

    async def test_ping_returns_200(self) -> None:
        """GET /v2/role/chat/ping returns 200 with status."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director"],
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/chat/ping")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "ok"
        assert payload["message"] == "Role Chat router is working"
        assert "supported_roles" in payload

    async def test_role_status_returns_200(self) -> None:
        """GET /v2/role/{role}/chat/status returns 200 with role status."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={"roles": {"pm": {"ready": True}}},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={
                    "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                    "providers": {"openai": {"type": "openai"}},
                },
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is True
        assert payload["configured"] is True
        assert payload["role"] == "pm"
        assert payload["role_config"]["provider_id"] == "openai"
        assert payload["role_config"]["model"] == "gpt-4"

    async def test_role_status_prefers_active_workspace_path(self) -> None:
        """Role chat status should load LLM config from the active workspace_path."""
        app = _build_app()
        app.state.app_state.settings.workspace = "C:/Repo/Polaris"
        app.state.app_state.settings.workspace_path = "C:/Temp/Product"

        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ) as build_cache_root,
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={"roles": {"pm": {"ready": True}}},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={
                    "roles": {"pm": {"provider_id": "openai", "model": "gpt-4"}},
                    "providers": {"openai": {"type": "openai"}},
                },
            ) as load_config,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        build_cache_root.assert_called_once_with("", "C:/Temp/Product")
        load_config.assert_awaited_once_with(
            "C:/Temp/Product",
            "/tmp/cache",
            app.state.app_state.settings,
        )

    async def test_role_status_returns_not_configured(self) -> None:
        """GET /v2/role/{role}/chat/status returns not configured when role missing."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={"roles": {}, "providers": {}},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.get_registered_roles",
                return_value=["pm", "architect"],
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/architect/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is False
        assert payload["configured"] is False
        assert "Role not configured" in payload["error"]

    async def test_role_status_returns_provider_not_set(self) -> None:
        """GET /v2/role/{role}/chat/status returns error when provider not set."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={
                    "roles": {"pm": {"provider_id": "", "model": ""}},
                    "providers": {},
                },
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.get_registered_roles",
                return_value=["pm"],
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is False
        assert "provider or model not set" in payload["error"]

    async def test_role_status_returns_provider_not_found(self) -> None:
        """GET /v2/role/{role}/chat/status returns error when provider not found."""
        app = _build_app()
        with (
            patch(
                "polaris.delivery.http.routers.role_chat.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_test_index_async",
                return_value={},
            ),
            patch(
                "polaris.delivery.http.routers.role_chat._load_llm_config_async",
                return_value={
                    "roles": {"pm": {"provider_id": "missing", "model": "gpt-4"}},
                    "providers": {},
                },
            ),
            patch(
                "polaris.delivery.http.routers.role_chat.get_registered_roles",
                return_value=["pm"],
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/pm/chat/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ready"] is False
        assert "Provider not found" in payload["error"]

    async def test_list_supported_roles_returns_200(self) -> None:
        """GET /v2/role/chat/roles returns 200 with role list."""
        app = _build_app()
        with patch(
            "polaris.delivery.http.routers.role_chat.get_registered_roles",
            return_value=["pm", "architect", "director", "qa"],
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/chat/roles")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "roles" in payload
        assert payload["count"] == 4
        assert "pm" in payload["roles"]

    async def test_get_role_llm_events_returns_200(self) -> None:
        """GET /v2/role/{role}/llm-events returns 200."""
        app = _build_app()
        event = LLMCallEvent(
            event_type="llm_call_start",
            role="chief_engineer",
            run_id="run-1",
            task_id="task-1",
        )
        emitter = MagicMock()
        emitter.get_events.return_value = [event]

        with patch("polaris.delivery.http.routers.role_chat.get_global_emitter", return_value=emitter):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/chief_engineer/llm-events?limit=5")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["role"] == "chief_engineer"
        assert payload["events"][0]["role"] == "chief_engineer"
        assert payload["stats"]["total"] == 1
        assert payload["stats"]["call_start"] == 1
        emitter.get_events.assert_called_once_with(
            run_id=None,
            task_id=None,
            role="chief_engineer",
            limit=5,
        )

    async def test_get_role_llm_events_with_filters(self) -> None:
        """GET /v2/role/{role}/llm-events returns 200 with filters."""
        app = _build_app()
        emitter = MagicMock()
        emitter.get_events.return_value = []

        with patch("polaris.delivery.http.routers.role_chat.get_global_emitter", return_value=emitter):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/v2/role/pm/llm-events?run_id=run-1&task_id=task-1&limit=20",
                )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["role"] == "pm"
        assert payload["run_id"] == "run-1"
        assert payload["task_id"] == "task-1"
        assert payload["events"] == []
        emitter.get_events.assert_called_once_with(
            run_id="run-1",
            task_id="task-1",
            role="pm",
            limit=20,
        )

    async def test_get_all_llm_events_returns_200(self) -> None:
        """GET /v2/role/llm-events returns 200."""
        app = _build_app()
        event = LLMCallEvent(
            event_type="llm_call_end",
            role="pm",
            run_id="run-1",
        )
        emitter = MagicMock()
        emitter.get_events.return_value = [event]

        with patch("polaris.delivery.http.routers.role_chat.get_global_emitter", return_value=emitter):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/llm-events?role=pm&limit=3")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["count"] == 1
        assert payload["events"][0]["event_type"] == "llm_call_end"
        emitter.get_events.assert_called_once_with(
            run_id=None,
            task_id=None,
            role="pm",
            limit=3,
        )

    async def test_get_llm_cache_stats_returns_200(self) -> None:
        """GET /v2/role/cache-stats returns 200."""
        app = _build_app()
        cache = MagicMock()
        cache.get_stats.return_value = {"hits": 2, "misses": 1}

        with patch("polaris.delivery.http.routers.role_chat.get_global_llm_cache", return_value=cache):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/role/cache-stats")

        assert response.status_code == 200
        assert response.json() == {"hits": 2, "misses": 1}
        cache.get_stats.assert_called_once_with()

    async def test_clear_llm_cache_returns_200(self) -> None:
        """POST /v2/role/cache-clear returns 200."""
        app = _build_app()
        cache = MagicMock()

        with patch("polaris.delivery.http.routers.role_chat.get_global_llm_cache", return_value=cache):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/v2/role/cache-clear")

        assert response.status_code == 200
        assert response.json() == {"ok": True, "message": "Cache cleared"}
        cache.clear.assert_called_once_with()

    async def test_nonexistent_endpoint_returns_404(self) -> None:
        """GET /v2/role/nonexistent returns 404."""
        app = _build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v2/role/nonexistent")

        assert response.status_code == 404
