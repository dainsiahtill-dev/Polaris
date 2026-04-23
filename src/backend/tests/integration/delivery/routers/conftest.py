"""Shared fixtures for delivery router integration tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.runtime.state_owner.public.service import Auth


def _make_minimal_app() -> FastAPI:
    """Create a bare FastAPI app with auth overridden for tests.

    Individual test modules should include the specific router under test
    and set ``app.state.app_state`` with a minimal settings namespace.
    """
    app = FastAPI()
    app.state.auth = Auth("")
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(
            workspace=".",
            ramdisk_root="",
            server=SimpleNamespace(cors_origins=["*"]),
        ),
    )
    return app


@pytest.fixture
def client_factory():
    """Return a factory that creates TestClient for a given router module.

    Usage::

        def test_something(client_factory):
            from polaris.delivery.http.routers import files
            client = client_factory(files.router)
            response = client.get("/files/read", params={"path": "x"})
    """

    def _factory(router, *, dependencies=None):
        app = _make_minimal_app()
        app.include_router(router)
        if dependencies:
            app.dependency_overrides.update(dependencies)
        return TestClient(app)

    return _factory


@pytest.fixture
def mock_service() -> MagicMock:
    """Return a reusable MagicMock for patching service calls."""
    return MagicMock()
