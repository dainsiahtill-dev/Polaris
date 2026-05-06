from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.middleware.rate_limit import get_rate_limit_middleware


def _build_client(*, rps: float, burst: int, client_host: str = "testclient") -> TestClient:
    app = FastAPI()

    @app.get("/v2/test")
    def test_endpoint() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        get_rate_limit_middleware,
        rps=rps,
        burst=burst,
    )
    return TestClient(app, client=(client_host, 50000))


def test_rate_limit_allows_first_request_for_new_client(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "true")
    with _build_client(rps=1.0, burst=1) as client:
        first = client.get("/v2/test")
        second = client.get("/v2/test")

    assert first.status_code == 200
    assert first.headers.get("X-RateLimit-Limit") == "1"
    assert second.status_code == 429


def test_rate_limit_allows_initial_burst_capacity(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "true")
    with _build_client(rps=1.0, burst=2) as client:
        first = client.get("/v2/test")
        second = client.get("/v2/test")
        third = client.get("/v2/test")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_loopback_exemption_is_env_gated(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_EXEMPT_LOOPBACK", "1")
    with _build_client(rps=1.0, burst=1, client_host="127.0.0.1") as client:
        responses = [client.get("/v2/test") for _ in range(5)]

    assert all(response.status_code == 200 for response in responses)
