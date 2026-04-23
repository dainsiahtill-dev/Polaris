from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.middleware.rate_limit import get_rate_limit_middleware


def _build_client(*, rps: float, burst: int, window: float) -> TestClient:
    app = FastAPI()

    @app.get("/v2/test")
    def test_endpoint() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        get_rate_limit_middleware,
        rps=rps,
        burst=burst,
        window=window,
    )
    return TestClient(app)


def test_rate_limit_uses_rps_times_window_capacity(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "true")
    with _build_client(rps=10.0, burst=20, window=60.0) as client:
        responses = [client.get("/v2/test") for _ in range(25)]

    assert all(resp.status_code == 200 for resp in responses)
    assert responses[0].headers.get("X-RateLimit-Limit") == "600"


def test_rate_limit_still_enforces_small_capacity(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "true")
    with _build_client(rps=1.0, burst=2, window=1.0) as client:
        first = client.get("/v2/test")
        second = client.get("/v2/test")
        third = client.get("/v2/test")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
