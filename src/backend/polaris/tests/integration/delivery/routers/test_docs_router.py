"""Contract tests for retired docs-init route aliases."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import docs as docs_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(docs_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    (
        "/docs/init/dialogue",
        "/docs/init/suggest",
        "/docs/init/preview",
        "/docs/init/apply",
    ),
)
def test_retired_docs_init_alias_routes_are_not_registered(path: str) -> None:
    client = _build_client()

    response = client.post(path, json={})

    assert response.status_code == 404
