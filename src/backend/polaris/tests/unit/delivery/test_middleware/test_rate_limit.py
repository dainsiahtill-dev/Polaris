"""Tests for token-bucket rate limiting and endpoint policy integration."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from polaris.delivery.http.middleware.rate_limit import RateLimitEntry, RateLimitMiddleware, RateLimitStore


class TestRateLimitEntry:
    """Tests for RateLimitEntry dataclass."""

    def test_default_initialization(self) -> None:
        entry = RateLimitEntry()
        assert entry.tokens == 0.0
        assert entry.last_update == 0.0
        assert entry.blocked_until == 0.0
        assert entry.total_violations == 0

    def test_custom_initialization(self) -> None:
        entry = RateLimitEntry(tokens=2.5, last_update=10.0, blocked_until=100.0, total_violations=5)
        assert entry.tokens == 2.5
        assert entry.last_update == 10.0
        assert entry.blocked_until == 100.0
        assert entry.total_violations == 5


class TestRateLimitStore:
    """Tests for RateLimitStore core token-bucket behavior."""

    def setup_method(self) -> None:
        self.store = RateLimitStore(max_entries=1000)

    def _make_mock_request(
        self,
        client_host: str = "127.0.0.1",
        forwarded: str | None = None,
        real_ip: str | None = None,
    ) -> MagicMock:
        request = MagicMock()
        request.client.host = client_host

        def _header_get(name: str, default: str | None = None) -> str | None:
            if name == "X-Forwarded-For":
                return forwarded
            if name == "X-Real-Ip":
                return real_ip
            return default

        request.headers.get.side_effect = _header_get
        return request

    def test_get_client_key_does_not_trust_forwarded_header_by_default(self) -> None:
        request = self._make_mock_request(client_host="10.0.0.10", forwarded="192.168.1.100")
        key = self.store._get_client_key(request)
        assert key == "10.0.0.10"

    def test_get_client_key_trusts_forwarded_header_from_trusted_proxy(self) -> None:
        store = RateLimitStore(trusted_proxies=["10.0.0.10"])
        request = self._make_mock_request(client_host="10.0.0.10", forwarded="192.168.1.100, 10.0.0.10")
        key = store._get_client_key(request)
        assert key == "192.168.1.100"

    def test_get_client_key_uses_client_host(self) -> None:
        request = self._make_mock_request(client_host="192.168.1.1")
        key = self.store._get_client_key(request)
        assert key == "192.168.1.1"

    def test_get_client_key_handles_unknown_client(self) -> None:
        request = MagicMock()
        request.headers.get.return_value = None
        request.client = None

        key = self.store._get_client_key(request)
        assert key == "unknown"

    def test_check_rate_limit_allows_first_request_with_full_burst(self) -> None:
        request = self._make_mock_request()
        allowed, retry_after, tokens = self.store.check_rate_limit(request, rps=10.0, burst=20)

        assert allowed is True
        assert retry_after == 0.0
        assert tokens == 19.0

    def test_check_rate_limit_blocks_when_burst_exhausted(self) -> None:
        request = self._make_mock_request()

        for _ in range(2):
            allowed, _, _ = self.store.check_rate_limit(request, rps=0.001, burst=2)
            assert allowed is True

        allowed, retry_after, tokens = self.store.check_rate_limit(request, rps=0.001, burst=2)

        assert allowed is False
        assert retry_after > 0
        assert tokens < 1.0

    def test_check_rate_limit_respects_blocked_until(self) -> None:
        request = self._make_mock_request()
        future = time.time() + 300
        self.store._store["127.0.0.1"] = RateLimitEntry(tokens=5.0, last_update=time.time(), blocked_until=future)

        allowed, retry_after, tokens = self.store.check_rate_limit(request, rps=10.0, burst=20)

        assert allowed is False
        assert retry_after > 0
        assert tokens == 5.0

    def test_check_rate_limit_increments_violations(self) -> None:
        request = self._make_mock_request()
        self.store._store["127.0.0.1"] = RateLimitEntry(tokens=0.0, last_update=time.time())

        allowed, retry_after, _ = self.store.check_rate_limit(request, rps=0.0, burst=1)

        assert allowed is False
        assert retry_after >= 30
        assert self.store._store["127.0.0.1"].total_violations == 1

    def test_reset_clears_all_entries(self) -> None:
        request = self._make_mock_request()
        self.store.check_rate_limit(request, rps=10.0, burst=20)

        self.store.reset()

        assert self.store._store == {}

    def test_reset_clears_specific_client(self) -> None:
        request1 = self._make_mock_request(client_host="10.0.0.1")
        request2 = self._make_mock_request(client_host="10.0.0.2")
        self.store.check_rate_limit(request1, rps=10.0, burst=20)
        self.store.check_rate_limit(request2, rps=10.0, burst=20)

        self.store.reset(request1)

        assert "10.0.0.1" not in self.store._store
        assert "10.0.0.2" in self.store._store


class TestRateLimitMiddleware:
    """Tests for RateLimitMiddleware endpoint policy behavior."""

    def _make_mock_request(self, path: str = "/api/test", client_host: str = "127.0.0.1") -> MagicMock:
        request = MagicMock()
        request.url.path = path
        request.client.host = client_host
        request.headers.get.return_value = None
        return request

    async def _call_next(self, _request: MagicMock) -> MagicMock:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        return response

    def test_middleware_uses_rate_limit_store(self) -> None:
        middleware = RateLimitMiddleware(MagicMock())
        assert isinstance(middleware._store, RateLimitStore)

    @pytest.mark.asyncio
    async def test_public_probe_is_not_rate_limited(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=0.0, burst_size=1)

        response = await middleware.dispatch(self._make_mock_request("/health"), self._call_next)

        assert response.status_code != 429

    @pytest.mark.asyncio
    async def test_health_probe_is_not_rate_limited(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=0.0, burst_size=1)

        response = await middleware.dispatch(self._make_mock_request("/health"), self._call_next)

        assert response.status_code != 429

    @pytest.mark.asyncio
    async def test_bootstrap_loopback_endpoint_is_not_rate_limited(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=0.0, burst_size=1)

        response = await middleware.dispatch(self._make_mock_request("/settings", "127.0.0.1"), self._call_next)

        assert response.status_code != 429

    @pytest.mark.asyncio
    async def test_normal_remote_path_is_rate_limited(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=0.0, burst_size=1)
        request = self._make_mock_request("/api/test", "10.0.0.5")

        first = await middleware.dispatch(request, self._call_next)
        second = await middleware.dispatch(request, self._call_next)

        assert first.status_code != 429
        assert second.status_code == 429

    def test_custom_excluded_paths_are_still_supported(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), excluded_paths=["/custom/exclude"])
        assert "/custom/exclude" in middleware._excluded_paths


class TestRateLimitStoreThreadSafety:
    """Tests for thread safety of RateLimitStore."""

    def test_concurrent_check_rate_limit(self) -> None:
        import threading

        store = RateLimitStore(max_entries=1000)
        num_threads = 10
        requests_per_thread = 20

        def make_requests() -> None:
            request = MagicMock()
            request.client.host = "127.0.0.1"
            request.headers.get.return_value = None
            for _ in range(requests_per_thread):
                store.check_rate_limit(request, rps=1000.0, burst=1000)

        threads = [threading.Thread(target=make_requests) for _ in range(num_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        entry = store._store["127.0.0.1"]
        assert entry.tokens <= 1000
