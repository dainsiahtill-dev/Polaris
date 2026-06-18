"""Full-app HTTP route ownership contracts."""

from __future__ import annotations

from collections import defaultdict

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


def test_legacy_http_sse_routes_fail_closed(monkeypatch) -> None:
    """Legacy HTTP SSE routes must not expose a second realtime transport."""
    monkeypatch.setenv("KERNELONE_TOKEN", "test-token")
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/v2/role/pm/chat/stream",
        headers={"Authorization": "Bearer test-token"},
        json={"message": "hello"},
    )

    assert response.status_code == 410
    assert response.headers.get("content-type", "").startswith("application/json")
    assert "text/event-stream" not in response.headers.get("content-type", "")
    body = response.json()
    assert body["error"]["code"] == "SSE_REMOVED"
    assert body["error"]["details"]["replacement"] == "/v2/role/pm/chat/jetstream"
