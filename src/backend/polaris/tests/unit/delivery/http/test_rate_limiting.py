"""Tests for API rate limiting behavior.

Covers rate limit enforcement, recovery, different endpoint policies,
and token-bucket algorithm correctness via mocked rate limiter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.delivery.http.middleware.rate_limit import (
    RateLimitEntry,
    RateLimitMiddleware,
    RateLimitStore,
    get_rate_limit_middleware,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.fixture
def rate_limit_app() -> FastAPI:
    """Create a minimal FastAPI app with rate limit middleware for unit tests."""
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/v2/chat")
    async def chat() -> dict[str, str]:
        return {"status": "chat"}

    @app.get("/v2/status")
    async def status() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> dict[str, str]:
        return {"status": "metrics"}

    return app


# ---------------------------------------------------------------------------
# RateLimitStore (token-bucket algorithm)
# ---------------------------------------------------------------------------


def test_rate_limit_store_init() -> None:
    """RateLimitStore should initialize with default parameters."""
    store = RateLimitStore()
    assert store._max_entries == 10000
    assert store._store == {}
    assert store._trust_x_forwarded_for is False


def test_rate_limit_store_get_client_key_direct() -> None:
    """Client key should fall back to direct client IP when no trusted proxies."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    key = store._get_client_key(request)
    assert key == "192.168.1.1"


def test_rate_limit_store_get_client_key_with_x_forwarded_for() -> None:
    """Client key should parse X-Forwarded-For from trusted proxy.

    Chain: "203.0.113.1, 10.0.0.2" — rightmost is the proxy directly connected
    to us. 10.0.0.2 is NOT in trusted_proxies, so it is the real client.
    """
    store = RateLimitStore(trusted_proxies=["10.0.0.1"])
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "203.0.113.1, 10.0.0.2"}

    key = store._get_client_key(request)
    # 10.0.0.2 is the first untrusted IP when walking backwards
    assert key == "10.0.0.2"


def test_rate_limit_store_check_rate_limit_allows_within_burst() -> None:
    """Requests within burst limit should be allowed."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    allowed, retry_after, tokens = store.check_rate_limit(request, rps=10.0, burst=5)
    assert allowed is True
    assert retry_after == 0.0
    assert tokens == 4.0


def test_rate_limit_store_check_rate_limit_blocks_when_exhausted() -> None:
    """Requests beyond burst should be blocked with progressive backoff."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    # Exhaust all tokens
    for _ in range(5):
        store.check_rate_limit(request, rps=10.0, burst=5)

    allowed, retry_after, tokens = store.check_rate_limit(request, rps=10.0, burst=5)
    assert allowed is False
    assert retry_after == 30  # First violation: 30s
    assert tokens < 1.0


def test_rate_limit_store_progressive_backoff() -> None:
    """Progressive backoff should double each time up to 240s."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    import time

    base_time = time.time()

    # Exhaust tokens and trigger violations
    durations = []
    for i in range(7):
        # Reset time forward past any block so we can trigger a new violation
        now = base_time + i * 300  # 5 min apart to ensure unblocked
        with patch("time.time", return_value=now):
            # Exhaust burst
            for _ in range(5):
                store.check_rate_limit(request, rps=10.0, burst=5)
            allowed, retry_after, _ = store.check_rate_limit(request, rps=10.0, burst=5)
            if not allowed:
                durations.append(int(retry_after))

    # Progressive: 30, 60, 120, 240, 240, 240...
    assert durations[0] == 30
    assert durations[1] == 60
    assert durations[2] == 120
    assert durations[3] == 240
    assert durations[4] == 240
    assert durations[5] == 240


def test_rate_limit_store_reset_all() -> None:
    """Reset without request should clear all entries."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    store.check_rate_limit(request, rps=10.0, burst=5)
    assert len(store._store) == 1

    store.reset()
    assert len(store._store) == 0


def test_rate_limit_store_reset_single_client() -> None:
    """Reset with request should clear only that client."""
    store = RateLimitStore()
    req1 = MagicMock(spec=Request)
    req1.client = MagicMock()
    req1.client.host = "192.168.1.1"
    req1.headers = {}

    req2 = MagicMock(spec=Request)
    req2.client = MagicMock()
    req2.client.host = "192.168.1.2"
    req2.headers = {}

    store.check_rate_limit(req1, rps=10.0, burst=5)
    store.check_rate_limit(req2, rps=10.0, burst=5)
    assert len(store._store) == 2

    store.reset(req1)
    assert len(store._store) == 1
    assert "192.168.1.2" in store._store


def test_rate_limit_store_cleanup_old_entries() -> None:
    """Cleanup should remove expired entries."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    # Exhaust and block
    for _ in range(5):
        store.check_rate_limit(request, rps=10.0, burst=5)
    store.check_rate_limit(request, rps=10.0, burst=5)

    # Move time forward past block duration
    import time

    now = time.time()
    with patch("time.time", return_value=now + 3600):
        store._cleanup_old_entries(now + 3600)

    assert len(store._store) == 0


def test_rate_limit_store_replenish_tokens() -> None:
    """Tokens should replenish over time based on RPS."""
    store = RateLimitStore()
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "192.168.1.1"
    request.headers = {}

    import time

    base_time = time.time()

    # Consume some tokens at a fixed base time
    with patch("time.time", return_value=base_time):
        for _ in range(3):
            store.check_rate_limit(request, rps=10.0, burst=5)

    entry = store._store["192.168.1.1"]
    assert entry.tokens == 2.0

    # Replenish after 0.1s at 10 RPS = 1 token
    with patch("time.time", return_value=base_time + 0.1):
        store._replenish_tokens(entry, base_time + 0.1, rps=10.0, burst=5)

    assert pytest.approx(entry.tokens, abs=0.001) == 3.0


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_disabled_allows_all_requests(rate_limit_app: FastAPI) -> None:
    """When disabled via environment, all requests should pass through."""
    with patch.dict("os.environ", {"KERNELONE_RATE_LIMIT_ENABLED": "false"}):
        middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=1.0, burst_size=1)

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        for _ in range(10):
            response = await ac.get("/v2/chat")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_middleware_excluded_paths_not_limited(rate_limit_app: FastAPI) -> None:
    """Excluded paths should not be rate limited."""
    middleware = RateLimitMiddleware(
        rate_limit_app,
        requests_per_second=1.0,
        burst_size=1,
        excluded_paths=["/health"],
    )

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        for _ in range(20):
            response = await ac.get("/health")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_middleware_rate_limit_headers_on_success(rate_limit_app: FastAPI) -> None:
    """Successful requests should include rate limit headers."""
    middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=10.0, burst_size=20)

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        response = await ac.get("/v2/chat")
        assert response.status_code == 200
        assert response.headers["x-ratelimit-limit"] == "20"
        assert "x-ratelimit-remaining" in response.headers
        assert int(response.headers["x-ratelimit-remaining"]) >= 0


@pytest.mark.asyncio
async def test_middleware_rate_limit_headers_on_429(rate_limit_app: FastAPI) -> None:
    """Blocked requests should include all rate limit headers."""
    middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=1.0, burst_size=1)

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        # Consume the single token
        response = await ac.get("/v2/chat")
        assert response.status_code == 200

        # Next request should be blocked
        response = await ac.get("/v2/chat")
        assert response.status_code == 429
        assert response.headers["x-ratelimit-limit"] == "1"
        assert response.headers["x-ratelimit-remaining"] == "0"
        assert "x-ratelimit-reset" in response.headers
        assert "retry-after" in response.headers
        assert int(response.headers["retry-after"]) == 30


@pytest.mark.asyncio
async def test_middleware_rapid_requests_return_429(rate_limit_app: FastAPI) -> None:
    """Rapid requests beyond burst should return 429 Too Many Requests."""
    middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=1.0, burst_size=2)

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        # Consume burst
        r1 = await ac.get("/v2/chat")
        r2 = await ac.get("/v2/chat")
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Third request should be rate limited
        r3 = await ac.get("/v2/chat")
        assert r3.status_code == 429
        assert r3.json()["error"] == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_middleware_health_endpoint_exempt(rate_limit_app: FastAPI) -> None:
    """Health endpoint should be exempt from rate limiting."""
    middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=1.0, burst_size=1)

    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        for _ in range(50):
            response = await ac.get("/health")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_middleware_loopback_exemption(rate_limit_app: FastAPI) -> None:
    """Loopback clients should be exempt when configured."""
    with patch.dict("os.environ", {"KERNELONE_RATE_LIMIT_EXEMPT_LOOPBACK": "true"}):
        middleware = RateLimitMiddleware(rate_limit_app, requests_per_second=1.0, burst_size=1)

    # ASGI transport uses 127.0.0.1 as client host by default
    async with AsyncClient(transport=ASGITransport(middleware), base_url="http://test") as ac:
        for _ in range(10):
            response = await ac.get("/v2/chat")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_middleware_is_registered_in_app_factory(mock_settings: Settings) -> None:
    """Rate limit middleware should be registered in the app factory."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    # FastAPI stores middleware as callables in user_middleware; check both
    # the factory function name and the actual class name.
    middleware_names = []
    for m in app.user_middleware:
        name = getattr(m, "cls", None)
        if name is not None:
            middleware_names.append(name.__name__ if hasattr(name, "__name__") else str(name))
        else:
            middleware_names.append(str(m))

    assert any("RateLimitMiddleware" in name or "get_rate_limit_middleware" in name for name in middleware_names), (
        f"Expected RateLimitMiddleware in {middleware_names}"
    )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def test_get_rate_limit_middleware_factory() -> None:
    """Factory should create middleware with environment-derived defaults."""
    app = FastAPI()
    with patch.dict("os.environ", {"KERNELONE_RATE_LIMIT_RPS": "5", "KERNELONE_RATE_LIMIT_BURST": "10"}):
        middleware = get_rate_limit_middleware(app)

    assert middleware._rps == 5.0
    assert middleware._burst == 10


# ---------------------------------------------------------------------------
# Integration tests via app_factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_health_never_rate_limited(client: AsyncClient) -> None:
    """Health endpoint should never return 429 regardless of request volume."""
    for _ in range(100):
        response = await client.get("/health")
        assert response.status_code == 200
        # Health is rate-limit exempt
        assert "x-ratelimit-limit" not in response.headers


@pytest.mark.asyncio
async def test_integration_ready_has_rate_limit_headers(client: AsyncClient) -> None:
    """Ready endpoint should have rate limit headers on success."""
    response = await client.get("/ready")
    assert response.status_code == 200
    assert "x-ratelimit-limit" in response.headers
    assert "x-ratelimit-remaining" in response.headers


@pytest.mark.asyncio
async def test_integration_mocked_rate_limit_enforcement(mock_settings: Settings) -> None:
    """Mocked rate limiter should return 429 with proper headers."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "polaris.delivery.http.middleware.rate_limit.RateLimitStore.check_rate_limit",
            return_value=(False, 30.0, 0.0),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/ready")
            assert response.status_code == 429
            assert response.headers["x-ratelimit-limit"] == "20"
            assert response.headers["x-ratelimit-remaining"] == "0"
            assert "x-ratelimit-reset" in response.headers
            assert response.headers["retry-after"] == "30"


@pytest.mark.asyncio
async def test_integration_mocked_rate_limit_recovery(mock_settings: Settings) -> None:
    """After mocked rate limit expires, subsequent request should succeed."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    call_count = 0

    def _mock_check(request: Request, rps: float, burst: int) -> tuple[bool, float, float]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False, 30.0, 0.0
        return True, 0.0, 19.0

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "polaris.delivery.http.middleware.rate_limit.RateLimitStore.check_rate_limit",
            side_effect=_mock_check,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            # First request blocked
            r1 = await ac.get("/ready")
            assert r1.status_code == 429

            # Second request allowed (simulating recovery)
            r2 = await ac.get("/ready")
            assert r2.status_code == 200
            assert r2.headers["x-ratelimit-remaining"] == "19"


@pytest.mark.asyncio
async def test_integration_chat_endpoint_rate_limited(mock_settings: Settings) -> None:
    """Chat-like endpoints should be subject to rate limiting."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "polaris.delivery.http.middleware.rate_limit.RateLimitStore.check_rate_limit",
            return_value=(False, 60.0, 0.0),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/v2/role/pm/chat")
            assert response.status_code == 429
            assert response.headers["retry-after"] == "60"


@pytest.mark.asyncio
async def test_integration_status_endpoint_rate_limited(mock_settings: Settings) -> None:
    """Status endpoints should also be rate limited (same bucket)."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "polaris.delivery.http.middleware.rate_limit.RateLimitStore.check_rate_limit",
            return_value=(False, 120.0, 0.0),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/v2/pm/status")
            assert response.status_code == 429
            assert response.headers["retry-after"] == "120"


@pytest.mark.asyncio
async def test_integration_metrics_exempt_from_rate_limit(mock_settings: Settings) -> None:
    """Metrics endpoint should be exempt from rate limiting."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.auth = None

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        # Even if we mock check_rate_limit to block, metrics should bypass
        patch(
            "polaris.delivery.http.middleware.rate_limit.RateLimitStore.check_rate_limit",
            return_value=(False, 30.0, 0.0),
        ) as mock_check,
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            response = await ac.get("/metrics")
            assert response.status_code == 200
            # check_rate_limit should NOT have been called for metrics
            mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# RateLimitEntry dataclass
# ---------------------------------------------------------------------------


def test_rate_limit_entry_defaults() -> None:
    """RateLimitEntry should have correct default values."""
    entry = RateLimitEntry()
    assert entry.tokens == 0.0
    assert entry.last_update == 0.0
    assert entry.blocked_until == 0.0
    assert entry.total_violations == 0


def test_rate_limit_entry_custom_values() -> None:
    """RateLimitEntry should accept custom values."""
    entry = RateLimitEntry(tokens=10.0, last_update=100.0, blocked_until=200.0, total_violations=3)
    assert entry.tokens == 10.0
    assert entry.last_update == 100.0
    assert entry.blocked_until == 200.0
    assert entry.total_violations == 3
