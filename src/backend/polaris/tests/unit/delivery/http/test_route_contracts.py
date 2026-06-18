"""Full-app HTTP route ownership contracts."""

from __future__ import annotations

from collections import defaultdict

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
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
    ("method", "path", "replacement"),
    [
        ("POST", "/v2/role/pm/chat/stream", "/v2/role/pm/chat/jetstream"),
        ("POST", "/v2/pm/chat/stream", "/v2/role/pm/chat/jetstream"),
        ("POST", "/v2/stream/chat", "/v2/role/{role}/chat/jetstream"),
        ("POST", "/v2/stream/chat/backpressure", "/v2/role/{role}/chat/jetstream"),
        ("POST", "/v2/llm/interview/stream", "/v2/llm/interview/jetstream"),
        ("POST", "/llm/interview/stream", "/v2/llm/interview/jetstream"),
        ("POST", "/v2/llm/test/stream", "/v2/llm/test/jetstream"),
        ("POST", "/llm/test/stream", "/v2/llm/test/jetstream"),
        ("POST", "/v2/docs/init/dialogue/stream", "/v2/docs/init/dialogue/jetstream"),
        ("POST", "/docs/init/dialogue/stream", "/v2/docs/init/dialogue/jetstream"),
        ("POST", "/v2/docs/init/preview/stream", "/v2/docs/init/preview/jetstream"),
        ("POST", "/docs/init/preview/stream", "/v2/docs/init/preview/jetstream"),
        ("POST", "/v2/roles/sessions/session-1/messages/stream", "/v2/roles/sessions/session-1/messages/jetstream"),
        ("POST", "/v2/agent/sessions/session-1/messages/stream", "/v2/roles/sessions/session-1/messages/jetstream"),
        ("POST", "/v2/agent/v2/sessions/session-1/messages/stream", "/v2/roles/sessions/session-1/messages/jetstream"),
        ("GET", "/v2/factory/runs/run-1/stream", "/v2/ws/runtime"),
        ("GET", "/factory/runs/run-1/stream", "/v2/ws/runtime"),
    ],
)
def test_legacy_http_sse_routes_fail_closed(monkeypatch, method: str, path: str, replacement: str) -> None:
    """Legacy HTTP SSE routes must not expose a second realtime transport."""
    monkeypatch.setenv("KERNELONE_TOKEN", "test-token")
    app = create_app()
    client = TestClient(app)
    response = client.request(
        method,
        path,
        headers={"Authorization": "Bearer test-token"},
        json={"message": "hello"},
    )

    assert response.status_code == 410
    assert response.headers.get("content-type", "").startswith("application/json")
    assert "text/event-stream" not in response.headers.get("content-type", "")
    body = response.json()
    assert body["error"]["code"] == "SSE_REMOVED"
    assert body["error"]["details"]["replacement"] == replacement
    assert body["error"]["details"]["transport"] == "nat-jetstream"


def test_role_runtime_chat_does_not_expose_queue_streaming_helper() -> None:
    """Role chat delivery must use Nat-JetStream instead of an in-process stream queue."""
    from polaris.delivery.http.routers import role_runtime_chat

    assert not hasattr(role_runtime_chat, "execute_role_chat_streaming")
