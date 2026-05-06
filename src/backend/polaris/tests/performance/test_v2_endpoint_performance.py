"""Performance test stubs for critical v2 endpoints.

These tests establish baseline response-time thresholds with all external
services mocked. They are NOT load tests — they validate that endpoints
remain fast under minimal overhead.

Endpoints benchmarked:
    - GET  /v2/health                (< 10 ms)
    - GET  /v2/role/pm/chat/status   (< 100 ms)
    - POST /v2/role/pm/chat          (< 5 s)
    - GET  /v2/factory/runs          (< 50 ms)
    - GET  /v2/conversations         (< 50 ms)
    - GET  /v2/settings              (< 10 ms)
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import (
    conversations as conversations_router,
    factory as factory_router,
    pm_chat as pm_chat_router,
    system as system_router,
)
from polaris.delivery.http.routers._shared import require_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app_with_router(router) -> FastAPI:
    """Create a minimal FastAPI app with *router* and auth overridden."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = MagicMock()
    app.state.app_state.settings = MagicMock()
    app.state.app_state.settings.workspace = "."
    app.state.app_state.settings.ramdisk_root = ""
    app.state.app_state.settings.to_payload.return_value = {"workspace": "."}
    return app


def _mock_db_session() -> MagicMock:
    """Return a mock DB session for conversation routers."""
    db = MagicMock()
    query_mock = MagicMock()
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []
    db.query.return_value = query_mock
    db.add.return_value = None
    db.flush.return_value = None
    db.commit.return_value = None
    db.refresh.return_value = None
    db.delete.return_value = None
    return db


def _mock_conversation() -> MagicMock:
    """Return a mock Conversation object."""
    conv = MagicMock()
    conv.id = "conv-1"
    conv.title = "Test"
    conv.role = "pm"
    conv.workspace = "."
    conv.context_config = None
    conv.message_count = 0
    conv.created_at = "2026-04-24T00:00:00"
    conv.updated_at = "2026-04-24T00:00:00"
    conv.is_deleted = 0
    conv.to_dict.return_value = {
        "id": conv.id,
        "title": conv.title,
        "role": conv.role,
        "workspace": conv.workspace,
        "context_config": {},
        "message_count": conv.message_count,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "messages": None,
    }
    return conv


def _mock_factory_run() -> MagicMock:
    """Return a mock FactoryRun."""
    run = MagicMock()
    run.id = "run-1"
    run.status = MagicMock()
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


def _make_mock_factory_service() -> MagicMock:
    """Return a mock FactoryRunService with async methods."""
    service = MagicMock()
    service.list_runs = AsyncMock(return_value=[])
    service.get_run = AsyncMock(return_value=None)
    service.create_run = AsyncMock(return_value=_mock_factory_run())
    service.start_run = AsyncMock(return_value=_mock_factory_run())
    service.get_run_events = AsyncMock(return_value=[])
    service.cancel_run = AsyncMock(return_value=_mock_factory_run())
    service.store = MagicMock()
    service.store.get_run_dir.return_value = MagicMock(
        __truediv__=lambda _self, _other: MagicMock(
            exists=lambda: False,
            iterdir=lambda: [],
        ),
    )
    return service


# ---------------------------------------------------------------------------
# Performance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestV2HealthPerformance:
    """Baseline performance for /v2/health."""

    async def test_health_response_time(self) -> None:
        """GET /v2/health should respond in < 10 ms (mocked)."""
        app = _build_app_with_router(system_router.router)
        with (
            patch(
                "polaris.delivery.http.routers.system.get_lancedb_status",
                return_value={"ok": True, "python": "3.12"},
            ),
            patch(
                "polaris.infrastructure.di.container.get_container",
            ) as mock_get_container,
        ):
            mock_pm = MagicMock()
            mock_pm.get_status.return_value = {"status": "idle", "running": False}
            mock_director = MagicMock()
            mock_director.get_status = AsyncMock(return_value={"status": "idle", "state": "idle"})
            mock_container = MagicMock()
            mock_container.resolve_async = AsyncMock(
                side_effect=lambda cls: mock_pm if "PMService" in str(cls) else mock_director
            )
            mock_get_container.return_value = mock_container

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                start = time.perf_counter()
                response = await client.get("/v2/health")
                elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.01  # 10 ms


@pytest.mark.asyncio
class TestV2PMChatStatusPerformance:
    """Baseline performance for /v2/pm/chat/status."""

    async def test_pm_chat_status_response_time(self) -> None:
        """GET /v2/pm/chat/status should respond in < 100 ms (mocked)."""
        app = _build_app_with_router(pm_chat_router.router)
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
                start = time.perf_counter()
                response = await client.get("/v2/pm/chat/status")
                elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.1  # 100 ms


@pytest.mark.asyncio
class TestV2PMChatPerformance:
    """Baseline performance for /v2/pm/chat (non-streaming)."""

    async def test_pm_chat_response_time(self) -> None:
        """POST /v2/pm/chat should respond in < 5 s with mocked LLM."""
        app = _build_app_with_router(pm_chat_router.router)
        with patch(
            "polaris.delivery.http.routers.pm_chat.generate_role_response",
            new_callable=AsyncMock,
        ) as mock_generate:
            mock_generate.return_value = {
                "response": "Mock response",
                "thinking": "Mock thinking",
                "role": "pm",
                "model": "test-model",
                "provider": "test-provider",
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                start = time.perf_counter()
                response = await client.post(
                    "/v2/pm/chat",
                    json={"message": "Hello PM"},
                )
                elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 5.0  # 5 s


@pytest.mark.asyncio
class TestV2FactoryRunsPerformance:
    """Baseline performance for /v2/factory/runs."""

    async def test_list_factory_runs_response_time(self) -> None:
        """GET /v2/factory/runs should respond in < 50 ms (mocked)."""
        app = _build_app_with_router(factory_router.router)
        app.state.app_state = SimpleNamespace(
            settings=SimpleNamespace(workspace=".", ramdisk_root=""),
        )
        mock_service = _make_mock_factory_service()
        mock_service.list_runs.return_value = [
            {"id": "run-1", "created_at": "2026-04-24T00:00:00"},
        ]
        mock_service.get_run.return_value = _mock_factory_run()

        with patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
            return_value=mock_service,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                start = time.perf_counter()
                response = await client.get("/v2/factory/runs")
                elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.05  # 50 ms


@pytest.mark.asyncio
class TestV2ConversationsPerformance:
    """Baseline performance for /v2/conversations."""

    async def test_list_conversations_response_time(self) -> None:
        """GET /v2/conversations should respond in < 50 ms (mocked)."""
        from polaris.cells.roles.session.public import get_db as _original_get_db

        app = _build_app_with_router(conversations_router.router)
        app.dependency_overrides[_original_get_db] = lambda: _mock_db_session()

        db = _mock_db_session()
        mock_conv = _mock_conversation()

        def _query(model: Any) -> MagicMock:
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 1
            q.order_by.return_value = q
            q.offset.return_value = q
            q.limit.return_value = q
            q.all.return_value = [mock_conv]
            return q

        db.query.side_effect = _query
        app.dependency_overrides[_original_get_db] = lambda: db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            start = time.perf_counter()
            response = await client.get("/v2/conversations")
            elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.05  # 50 ms


@pytest.mark.asyncio
class TestV2SettingsPerformance:
    """Baseline performance for /v2/settings."""

    async def test_settings_response_time(self) -> None:
        """GET /v2/settings should respond in < 10 ms (mocked)."""
        app = _build_app_with_router(system_router.router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            start = time.perf_counter()
            response = await client.get("/v2/settings")
            elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.01  # 10 ms
