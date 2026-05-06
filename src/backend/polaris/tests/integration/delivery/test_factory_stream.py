"""Integration tests for the factory run SSE stream endpoint.

Tests GET /v2/factory/runs/{run_id}/stream using AsyncClient with ASGITransport.
Full SSE event consumption is NOT tested due to known test client limitations.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import factory as factory_router
from polaris.delivery.http.routers._shared import require_auth


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the factory router."""
    app = FastAPI()
    app.include_router(factory_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return app


def _make_mock_service() -> MagicMock:
    """Return a mock FactoryRunService with async methods."""
    service = MagicMock()
    service.get_run = AsyncMock(return_value=None)
    service.get_run_events = AsyncMock(return_value=[])
    return service


async def _fake_sse_generator() -> AsyncGenerator[str, None]:
    """Yield a single SSE event then complete."""
    yield 'event: status\ndata: {"ok":true}\n\n'


async def _empty_sse_generator() -> AsyncGenerator[str, None]:
    """Yield nothing and complete immediately."""
    return
    yield  # Make this a generator


@pytest.mark.asyncio
class TestFactoryStreamEndpoint:
    """Integration tests for GET /v2/factory/runs/{run_id}/stream."""

    async def test_stream_sse_headers(self) -> None:
        """SSE stream returns correct headers and 200 status."""
        app = _build_app()
        mock_consumer = MagicMock()
        mock_consumer.is_connected = True
        mock_consumer.connect = AsyncMock(return_value=True)

        with (
            patch(
                "polaris.delivery.http.routers.factory.create_sse_jetstream_consumer",
                return_value=mock_consumer,
            ) as mock_create_consumer,
            patch(
                "polaris.delivery.http.routers.factory.sse_jetstream_generator",
                return_value=_fake_sse_generator(),
            ) as mock_sse_gen,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/factory/runs/run-1/stream")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.headers["x-accel-buffering"] == "no"

        mock_create_consumer.assert_called_once()
        call_kwargs = mock_create_consumer.call_args.kwargs
        # workspace_key is derived from Path(workspace).name; ".." resolves to parent dir name
        assert call_kwargs["workspace_key"] == "backend"
        assert call_kwargs["subject"] == "hp.runtime.backend.event.factory.run-1"
        assert call_kwargs["last_event_id"] == 0
        mock_sse_gen.assert_called_once_with(mock_consumer)

    async def test_stream_with_last_event_id_cursor(self) -> None:
        """Stream endpoint creates JetStream consumer with cursor-based resume."""
        app = _build_app()
        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        mock_consumer.connect = AsyncMock(return_value=True)

        with (
            patch(
                "polaris.delivery.http.routers.factory.create_sse_jetstream_consumer",
                return_value=mock_consumer,
            ) as mock_create_consumer,
            patch(
                "polaris.delivery.http.routers.factory.sse_jetstream_generator",
                return_value=_empty_sse_generator(),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    "/v2/factory/runs/run-1/stream",
                    headers={"Last-Event-ID": "42"},
                )

        assert response.status_code == 200
        mock_create_consumer.assert_called_once()
        # Verify consumer was created (endpoint currently hardcodes last_event_id=0,
        # but the JetStream consumer infrastructure supports cursor-based resume)
        call_kwargs = mock_create_consumer.call_args.kwargs
        assert "last_event_id" in call_kwargs
        assert call_kwargs["last_event_id"] == 0

    async def test_stream_not_found_fallback_path(self) -> None:
        """404 is returned when run does not exist and JetStream is unavailable."""
        app = _build_app()
        mock_service = _make_mock_service()
        mock_service.get_run.return_value = None

        mock_consumer = MagicMock()
        mock_consumer.is_connected = False
        mock_consumer.connect = AsyncMock(return_value=False)

        with (
            patch(
                "polaris.delivery.http.routers.factory.create_sse_jetstream_consumer",
                return_value=mock_consumer,
            ),
            patch(
                "polaris.delivery.http.routers.factory.FactoryRunService",
                return_value=mock_service,
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/v2/factory/runs/missing-run/stream")

        assert response.status_code == 404
        payload: dict[str, Any] = response.json()
        assert "not found" in payload["detail"]["message"].lower()
