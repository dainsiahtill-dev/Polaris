"""Contract tests for retired Ollama route aliases."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import ollama as ollama_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(ollama_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/ollama/models"),
        ("POST", "/ollama/stop"),
    ),
)
def test_retired_ollama_alias_routes_are_not_registered(method: str, path: str) -> None:
    client = _build_client()
    response = client.request(method, path, json={})
    assert response.status_code == 404
