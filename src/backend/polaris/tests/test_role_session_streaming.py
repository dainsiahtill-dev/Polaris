"""Retired RoleSession streams reject before persistence or runtime scheduling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.delivery.http.routers import role_session
from polaris.delivery.http.routers._shared import require_auth


def _app(workspace: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(role_session.router)
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=workspace))
    app.dependency_overrides[require_auth] = lambda: None
    return app


@pytest.mark.asyncio
async def test_removed_http_stream_cannot_open_role_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("removed stream accessed session storage")

    monkeypatch.setattr(role_session, "RoleSessionService", forbidden)
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path)), base_url="http://test") as client:
        response = await client.post(
            "/v2/roles/sessions/session-1/messages/stream", json={"role": "user", "content": "inspect workspace"}
        )
    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_retired_session_jetstream_never_persists_or_reports_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("retired stream accessed session storage")

    monkeypatch.setattr(role_session, "RoleSessionService", forbidden)
    async with AsyncClient(transport=ASGITransport(app=_app(tmp_path)), base_url="http://test") as client:
        response = await client.post(
            "/v2/roles/sessions/session-1/messages/jetstream", json={"role": "user", "content": "inspect workspace"}
        )
    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert "session_id" not in response.json()
    assert list(tmp_path.iterdir()) == []
