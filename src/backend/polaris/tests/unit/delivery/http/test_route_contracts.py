"""Full-app HTTP route ownership contracts."""

from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.schemas.common import PrimaryHealthResponse


def _http_routes() -> list[APIRoute]:
    app = create_app()
    return [route for route in app.routes if isinstance(route, APIRoute)]


def test_create_app_has_no_duplicate_http_method_paths() -> None:
    """Full app route ownership must not depend on registration order."""
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in _http_routes():
        endpoint = route.endpoint
        owner = f"{endpoint.__module__}.{endpoint.__name__}"
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            seen[(method, route.path)].append(owner)

    duplicates = {key: owners for key, owners in seen.items() if len(owners) > 1}

    assert duplicates == {}


def test_public_health_probe_has_single_primary_owner() -> None:
    """GET /health is the lightweight public process probe, not system health."""
    health_routes = [route for route in _http_routes() if route.path == "/health" and "GET" in (route.methods or set())]

    assert len(health_routes) == 1
    route = health_routes[0]
    assert route.endpoint.__module__ == "polaris.delivery.http.routers.primary"
    assert route.response_model is PrimaryHealthResponse


def test_enhanced_system_health_is_versioned() -> None:
    """Enhanced PM/Director health belongs to /v2/health."""
    v2_health_routes = [
        route for route in _http_routes() if route.path == "/v2/health" and "GET" in (route.methods or set())
    ]

    assert len(v2_health_routes) == 1
    route = v2_health_routes[0]
    assert route.endpoint.__module__ == "polaris.delivery.http.routers.system"
    assert route.response_model is not None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v2/role/pm/chat/stream"),
        ("POST", "/v2/pm/chat/stream"),
        ("POST", "/v2/stream/chat"),
        ("POST", "/v2/stream/chat/backpressure"),
        ("POST", "/v2/llm/interview/stream"),
        ("POST", "/llm/interview/stream"),
        ("POST", "/v2/llm/test/stream"),
        ("POST", "/llm/test/stream"),
        ("POST", "/v2/docs/init/dialogue/stream"),
        ("POST", "/docs/init/dialogue/stream"),
        ("POST", "/v2/docs/init/preview/stream"),
        ("POST", "/docs/init/preview/stream"),
        ("POST", "/v2/roles/sessions/session-1/messages/stream"),
        ("POST", "/v2/agent/sessions/session-1/messages/stream"),
        ("POST", "/v2/agent/v2/sessions/session-1/messages/stream"),
        ("GET", "/v2/factory/runs/run-1/stream"),
        ("GET", "/factory/runs/run-1/stream"),
    ],
)
def test_legacy_http_sse_routes_are_not_registered(monkeypatch, method: str, path: str) -> None:
    """Legacy HTTP SSE routes must not exist as a second realtime transport."""
    monkeypatch.setenv("KERNELONE_TOKEN", "test-token")
    app = create_app()
    route_keys = {
        (registered_method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for registered_method in (route.methods or set())
    }
    assert (method, path) not in route_keys

    client = TestClient(app)
    response = client.request(
        method,
        path,
        headers={"Authorization": "Bearer test-token"},
        json={"message": "hello"},
    )

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_role_runtime_chat_does_not_expose_queue_streaming_helper() -> None:
    """Role chat delivery must use Nat-JetStream instead of an in-process stream queue."""
    from polaris.delivery.http.routers import role_runtime_chat

    assert not hasattr(role_runtime_chat, "execute_role_chat_streaming")
