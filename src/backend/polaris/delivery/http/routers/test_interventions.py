"""Error-branch tests for the interventions router.

Regression guard for TS-1: every ``StructuredHTTPException`` raised by
``interventions.py`` previously used the non-existent ``detail=`` keyword,
which made the constructor raise ``TypeError`` and surface as an opaque
HTTP 500 instead of the intended structured 4xx/5xx body.

Each test below drives one error branch through a real ``TestClient`` and
asserts the unified ``{"error": {"code", "message", "details"}}`` body
produced by ``setup_exception_handlers``, proving no ``TypeError``/500
leaks. Filesystem boundaries are mocked so the tests stay hermetic and
independent of storage-root layout.

Tests follow Arrange-Act-Assert.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http import error_handlers
from polaris.delivery.http.routers import interventions


def _make_client(workspace: str) -> TestClient:
    """Build a TestClient for the interventions router with auth bypassed.

    The ``require_auth`` dependency is overridden with a no-op so requests
    reach the route handler under test. ``setup_exception_handlers`` is
    registered so ``StructuredHTTPException`` renders the unified
    ``{"error": {...}}`` body instead of the raw FastAPI default.
    """
    app = FastAPI()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=workspace, ramdisk_root=""),
    )
    error_handlers.setup_exception_handlers(app)
    app.include_router(interventions.router)
    app.dependency_overrides[interventions.require_auth] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def _assert_structured_error(
    response: Any,
    *,
    status_code: int,
    code: str,
    message_contains: str,
) -> None:
    """Assert the response carries the unified structured-error body."""
    assert response.status_code == status_code
    body = response.json()
    error = body["error"]
    assert error["code"] == code
    assert message_contains in error["message"]
    assert isinstance(error["details"], dict)


def test_list_without_workspace_returns_workspace_not_configured() -> None:
    # Arrange
    client = _make_client(workspace="")

    # Act
    response = client.get("/interventions/list")

    # Assert
    _assert_structured_error(
        response,
        status_code=400,
        code="WORKSPACE_NOT_CONFIGURED",
        message_contains="workspace is not configured",
    )


def test_action_without_workspace_returns_workspace_not_configured() -> None:
    # Arrange
    client = _make_client(workspace="")

    # Act
    response = client.post("/interventions/action", json={"id": "x", "action": "approve"})

    # Assert
    _assert_structured_error(
        response,
        status_code=400,
        code="WORKSPACE_NOT_CONFIGURED",
        message_contains="workspace is not configured",
    )


def test_list_with_invalid_json_file_returns_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_fs = MagicMock()
    fake_fs.exists.return_value = True
    fake_fs.read_json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    monkeypatch.setattr(interventions, "_path_fs", lambda _path: fake_fs)
    client = _make_client(workspace="/tmp/ws")

    # Act
    response = client.get("/interventions/list")

    # Assert
    _assert_structured_error(
        response,
        status_code=500,
        code="INTERVENTIONS_FILE_INVALID_JSON",
        message_contains="invalid INTERVENTIONS.json",
    )


def test_list_with_non_object_root_returns_not_object(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_fs = MagicMock()
    fake_fs.exists.return_value = True
    fake_fs.read_json.return_value = ["not", "an", "object"]
    monkeypatch.setattr(interventions, "_path_fs", lambda _path: fake_fs)
    client = _make_client(workspace="/tmp/ws")

    # Act
    response = client.get("/interventions/list")

    # Assert
    _assert_structured_error(
        response,
        status_code=500,
        code="INTERVENTIONS_FILE_NOT_OBJECT",
        message_contains="root must be an object",
    )


def test_action_with_non_object_body_returns_body_not_object() -> None:
    # Arrange
    client = _make_client(workspace="/tmp/ws")

    # Act: a JSON array body is valid JSON but not a dict
    response = client.post("/interventions/action", json=["not", "a", "dict"])

    # Assert
    _assert_structured_error(
        response,
        status_code=400,
        code="INTERVENTION_BODY_NOT_OBJECT",
        message_contains="request body must be an object",
    )


def test_action_without_id_returns_id_required() -> None:
    # Arrange
    client = _make_client(workspace="/tmp/ws")

    # Act
    response = client.post("/interventions/action", json={"action": "approve"})

    # Assert
    _assert_structured_error(
        response,
        status_code=400,
        code="INTERVENTION_ID_REQUIRED",
        message_contains="intervention id is required",
    )


def test_action_with_unsupported_action_returns_action_unsupported() -> None:
    # Arrange
    client = _make_client(workspace="/tmp/ws")

    # Act
    response = client.post("/interventions/action", json={"id": "abc", "action": "explode"})

    # Assert
    _assert_structured_error(
        response,
        status_code=400,
        code="INTERVENTION_ACTION_UNSUPPORTED",
        message_contains="unsupported intervention action",
    )


def test_action_with_unknown_id_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: empty store (file absent) so the id is never matched
    fake_fs = MagicMock()
    fake_fs.exists.return_value = False
    monkeypatch.setattr(interventions, "_path_fs", lambda _path: fake_fs)
    client = _make_client(workspace="/tmp/ws")

    # Act
    response = client.post("/interventions/action", json={"id": "missing-id", "action": "approve"})

    # Assert
    _assert_structured_error(
        response,
        status_code=404,
        code="INTERVENTION_NOT_FOUND",
        message_contains="intervention not found: missing-id",
    )
