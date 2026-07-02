"""Contract tests for retired agents route aliases."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import agents as agents_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(agents_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    (
        "/agents/apply",
        "/agents/feedback",
    ),
)
def test_retired_agents_alias_routes_are_not_registered(path: str) -> None:
    client = _build_client()
    response = client.post(path, json={})
    assert response.status_code == 404
