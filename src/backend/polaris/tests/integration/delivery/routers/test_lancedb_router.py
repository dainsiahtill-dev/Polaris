"""Contract tests for retired LanceDB route aliases."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import lancedb as lancedb_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(lancedb_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def test_retired_lancedb_status_alias_route_is_not_registered() -> None:
    client = _build_client()
    response = client.get("/lancedb/status")
    assert response.status_code == 404
