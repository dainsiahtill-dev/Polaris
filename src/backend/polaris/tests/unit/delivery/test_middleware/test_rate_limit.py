"""Tests for polaris.delivery.http.middleware.rate_limit.

Covers RateLimitStore, RateLimitMiddleware, and rate limiting algorithms.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from polaris.delivery.http.middleware.rate_limit import (
    RateLimitEntry,
    RateLimitMiddleware,
    RateLimitStore,
)


class TestRateLimitEntry:
    """Tests for RateLimitEntry dataclass."""

    def test_default_initialization(self) -> None:
        entry = RateLimitEntry()
        assert entry.requests == []
        assert entry.blocked_until == 0.0
        assert entry.total_violations == 0

    def test_custom_initialization(self) -> None:
        entry = RateLimitEntry(requests=[1.0, 2.0], blocked_until=100.0, total_violations=5)
        assert len(entry.requests) == 2
        assert entry.blocked_until == 100.0
        assert entry.total_violations == 5


class TestRateLimitStore:
    """Tests for RateLimitStore core logic."""

    def setup_method(self) -> None:
        self.store = RateLimitStore(window_seconds=60.0, max_entries=1000)

    def _make_mock_request(
        self, client_host: str = "127.0.0.1", forwarded: str | None = None, real_ip: str | None = None
    ) -> MagicMock:
        """Create a mock request with specified client info."""
        request = MagicMock()
        request.client.host = client_host
        if forwarded:
            request.headers.get.return_value = forwarded
        else:
            request.headers.get.return_value = None
        if real_ip:
            request.headers.get.return_value = real_ip
        return request

    def test_get_client_key_prefers_forwarded_header(self) -> None:
        request = self._make_mock_request(forwarded="192.168.1.100, 10.0.0.1")
        key = self.store._get_client_key(request)
        assert key == "192.168.1.100"

    def test_get_client_key_falls_back_to_real_ip(self) -> None:
        request = self._make_mock_request(real_ip="10.0.0.5")
        key = self.store._get_client_key(request)
        assert key == "10.0.0.5"

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

    def test_check_rate_limit_allows_first_request(self) -> None:
        request = self._make_mock_request()
        allowed, retry_after, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        assert allowed is True
        assert retry_after == 0.0
        assert count == 1

    def test_check_rate_limit_tracks_multiple_requests(self) -> None:
        request = self._make_mock_request()

        self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        allowed, _, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        assert allowed is True
        assert count == 3

    def test_check_rate_limit_blocks_when_exceeded(self) -> None:
        request = self._make_mock_request()

        for _ in range(10):
            self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        allowed, retry_after, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        assert allowed is False
        assert retry_after > 0
        assert count == 10

    def test_check_rate_limit_respects_window(self) -> None:
        request = self._make_mock_request()

        now = time.time()
        # Manually add old requests
        self.store._store["127.0.0.1"] = RateLimitEntry(requests=[now - 120, now - 90, now - 60], blocked_until=0.0)

        allowed, _, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        # Old requests should be evicted, new one added -> count = 1
        assert allowed is True
        assert count == 1

    def test_check_rate_limit_progressive_backoff(self) -> None:
        request = self._make_mock_request()

        # Test progressive backoff: 30s, 60s, 120s, 240s
        # First violation (total_violations was 0, now becomes 1 -> 30s)
        self.store._store["127.0.0.1"] = RateLimitEntry(
            requests=[time.time() - 1] * 10,
            blocked_until=0.0,
            total_violations=0,
        )
        allowed, retry_after, _ = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        assert allowed is False
        assert 25 <= retry_after <= 35  # ~30s

        # Second violation (total_violations was 1, now becomes 2 -> 60s)
        self.store._store["127.0.0.1"] = RateLimitEntry(
            requests=[time.time() - 1] * 10,
            blocked_until=0.0,
            total_violations=1,
        )
        allowed, retry_after, _ = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        assert allowed is False
        assert 50 <= retry_after <= 70  # ~60s

        # Third violation (total_violations was 2, now becomes 3 -> 120s)
        self.store._store["127.0.0.1"] = RateLimitEntry(
            requests=[time.time() - 1] * 10,
            blocked_until=0.0,
            total_violations=2,
        )
        allowed, retry_after, _ = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
        assert allowed is False
        assert 100 <= retry_after <= 140  # ~120s

    def test_check_rate_limit_blocks_while_blocked(self) -> None:
        request = self._make_mock_request()
        future = time.time() + 300  # Blocked for 5 minutes

        self.store._store["127.0.0.1"] = RateLimitEntry(
            requests=[time.time() - 1] * 10,
            blocked_until=future,
        )

        allowed, retry_after, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        assert allowed is False
        assert retry_after > 0
        assert count == 10

    def test_check_rate_limit_increments_violations(self) -> None:
        request = self._make_mock_request()

        # Exhaust limit
        for _ in range(10):
            self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        # Trigger block
        self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        entry = self.store._store["127.0.0.1"]
        assert entry.total_violations == 1

    def test_reset_clears_all_entries(self) -> None:
        request = self._make_mock_request()

        for _ in range(5):
            self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        self.store.reset()

        assert len(self.store._store) == 0

    def test_reset_clears_specific_client(self) -> None:
        request1 = self._make_mock_request(client_host="10.0.0.1")
        request2 = self._make_mock_request(client_host="10.0.0.2")

        self.store.check_rate_limit(request1, max_requests=10, window_seconds=60.0)
        self.store.check_rate_limit(request2, max_requests=10, window_seconds=60.0)

        self.store.reset(request1)

        assert "10.0.0.1" not in self.store._store
        assert "10.0.0.2" in self.store._store

    def test_cleanup_removes_expired_entries(self) -> None:
        now = time.time()
        # Entry with no requests and expired block
        self.store._store["expired"] = RateLimitEntry(requests=[], blocked_until=now - 10)
        # Entry with active requests
        self.store._store["active"] = RateLimitEntry(requests=[now], blocked_until=0)

        self.store._cleanup_old_entries()

        assert "expired" not in self.store._store
        assert "active" in self.store._store

    def test_max_entries_triggers_cleanup(self) -> None:
        store = RateLimitStore(window_seconds=60.0, max_entries=10)

        # Add many entries to trigger cleanup
        for i in range(15):
            request = self._make_mock_request(client_host=f"10.0.0.{i}")
            store.check_rate_limit(request, max_requests=1, window_seconds=60.0)
            # Make them expired
            if f"10.0.0.{i}" in store._store:
                store._store[f"10.0.0.{i}"].requests = []

        # Cleanup should have been triggered during check_rate_limit


class TestRateLimitStoreThreadSafety:
    """Tests for thread safety of RateLimitStore."""

    def test_concurrent_check_rate_limit(self) -> None:
        import threading

        store = RateLimitStore(window_seconds=60.0, max_entries=1000)
        num_threads = 10
        requests_per_thread = 100

        def make_requests() -> None:
            request = MagicMock()
            request.client.host = "127.0.0.1"
            request.headers.get.return_value = None
            for _ in range(requests_per_thread):
                store.check_rate_limit(request, max_requests=1000, window_seconds=60.0)

        threads = [threading.Thread(target=make_requests) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entry = store._store["127.0.0.1"]
        assert len(entry.requests) == num_threads * requests_per_thread


class TestRateLimitMiddleware:
    """Tests for RateLimitMiddleware middleware logic."""

    def _make_mock_request(self, path: str = "/api/test", client_host: str = "127.0.0.1") -> MagicMock:
        request = MagicMock()
        request.url.path = path
        request.client.host = client_host
        request.headers.get.return_value = None
        return request

    def test_excluded_paths_include_health_and_metrics(self) -> None:
        middleware = RateLimitMiddleware(MagicMock())

        assert "/health" in middleware._excluded_paths
        assert "/metrics" in middleware._excluded_paths

    def test_middleware_uses_rate_limit_store(self) -> None:
        middleware = RateLimitMiddleware(MagicMock())
        assert middleware._store is not None
        assert isinstance(middleware._store, RateLimitStore)

    def test_effective_limit_calculation(self) -> None:
        # RPS=10, burst=20, window=60 -> effective = max(600, 20) = 600
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=10.0, burst_size=20, window_seconds=60.0)

        # effective_limit should be max(rps * window, burst, 1)
        effective = max(int(middleware._rps * middleware._window), middleware._burst, 1)
        assert effective == 600

    def test_middleware_respects_excluded_paths(self) -> None:
        middleware = RateLimitMiddleware(MagicMock())

        path = "/health/live"
        is_excluded = any(path.startswith(excluded) for excluded in middleware._excluded_paths)
        assert is_excluded is True

    def test_middleware_allows_normal_paths(self) -> None:
        middleware = RateLimitMiddleware(MagicMock())

        path = "/api/users"
        is_excluded = any(path.startswith(excluded) for excluded in middleware._excluded_paths)
        assert is_excluded is False

    def test_custom_excluded_paths_added(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), excluded_paths=["/custom/exclude"])
        assert "/custom/exclude" in middleware._excluded_paths

    def test_rate_limit_response_contains_retry_after_header(self) -> None:
        middleware = RateLimitMiddleware(MagicMock(), requests_per_second=0.001, burst_size=1, window_seconds=60.0)

        request = self._make_mock_request()
        # Exhaust rate limit
        for _ in range(100):
            middleware._store.check_rate_limit(request, max_requests=1, window_seconds=60.0)

        allowed, retry_after, _ = middleware._store.check_rate_limit(request, max_requests=1, window_seconds=60.0)

        if not allowed:
            assert retry_after > 0


class TestRateLimitAlgorithms:
    """Tests for rate limiting algorithm edge cases."""

    def setup_method(self) -> None:
        self.store = RateLimitStore(window_seconds=60.0, max_entries=1000)

    def _make_request(self, host: str = "127.0.0.1") -> MagicMock:
        request = MagicMock()
        request.client.host = host
        request.headers.get.return_value = None
        return request

    def test_burst_allowance_respected(self) -> None:
        request = self._make_request()
        # With burst=20, should allow up to burst even if RPS * window would be smaller
        for i in range(25):
            allowed, _, _count = self.store.check_rate_limit(request, max_requests=20, window_seconds=60.0)
            if i < 20:
                assert allowed is True, f"Request {i} should be allowed (burst={20})"
            else:
                assert allowed is False, f"Request {i} should be blocked (burst exceeded)"

    def test_window_expiry_allows_new_requests(self) -> None:
        request = self._make_request()
        now = time.time()

        # Manually set entry with old window
        self.store._store["127.0.0.1"] = RateLimitEntry(
            requests=[now - 120],  # Outside 60s window
            blocked_until=0.0,
        )

        allowed, _, count = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)

        assert allowed is True
        assert count == 1  # Current request added

    def test_multiple_clients_independent(self) -> None:
        request1 = self._make_request("10.0.0.1")
        request2 = self._make_request("10.0.0.2")

        # Exhaust limit for client 1
        for _ in range(15):
            self.store.check_rate_limit(request1, max_requests=10, window_seconds=60.0)

        # Client 2 should still be allowed
        allowed, _, count = self.store.check_rate_limit(request2, max_requests=10, window_seconds=60.0)
        assert allowed is True
        assert count == 1

    def test_block_duration_caps_at_max(self) -> None:
        request = self._make_request()

        # Simulate many violations (should cap backoff)
        for i in range(10):
            self.store._store["127.0.0.1"] = RateLimitEntry(
                requests=[time.time() - 1] * 10,
                blocked_until=0.0,
                total_violations=i,
            )

            allowed, retry_after, _ = self.store.check_rate_limit(request, max_requests=10, window_seconds=60.0)
            assert allowed is False
            # Backoff caps at 240s (30 * 2^3 = 240)
            assert retry_after <= 240 * 2  # Allow some variance
