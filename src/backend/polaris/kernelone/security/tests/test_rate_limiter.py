from __future__ import annotations

import time

import pytest
from polaris.kernelone.security.rate_limiter import RateLimiter


@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter(max_requests=5, window_seconds=60)


class TestRateLimiter:
    """Tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_check_rate_limit_allows_initial_requests(self, limiter: RateLimiter) -> None:
        """Test rate limiter allows initial requests."""
        allowed, remaining = await limiter.check_rate_limit("client1")
        assert allowed is True
        assert remaining == 4

    @pytest.mark.asyncio
    async def test_check_rate_limit_tracks_requests(self, limiter: RateLimiter) -> None:
        """Test rate limiter correctly tracks request counts."""
        for _i in range(5):
            allowed, _ = await limiter.check_rate_limit("client1")
            assert allowed is True

        # 6th request should be blocked
        allowed, remaining = await limiter.check_rate_limit("client1")
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_check_rate_limit_per_client(self, limiter: RateLimiter) -> None:
        """Test rate limiter enforces limits per client."""
        # Fill up client1
        for _ in range(5):
            await limiter.check_rate_limit("client1")

        # client2 should still be allowed
        allowed, remaining = await limiter.check_rate_limit("client2")
        assert allowed is True
        assert remaining == 4

    @pytest.mark.asyncio
    async def test_get_remaining_initial(self, limiter: RateLimiter) -> None:
        """Test get_remaining returns max for new client."""
        remaining = await limiter.get_remaining("new_client")
        assert remaining == 5

    @pytest.mark.asyncio
    async def test_get_remaining_after_requests(self, limiter: RateLimiter) -> None:
        """Test get_remaining returns correct count after requests."""
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client1")
        remaining = await limiter.get_remaining("client1")
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_reset_client(self, limiter: RateLimiter) -> None:
        """Test reset_client clears client's rate limit."""
        # Use up some requests
        for _ in range(3):
            await limiter.check_rate_limit("client1")

        # Reset
        await limiter.reset_client("client1")

        # Should be back to full
        remaining = await limiter.get_remaining("client1")
        assert remaining == 5

    @pytest.mark.asyncio
    async def test_rate_limit_expiry(self, limiter: RateLimiter) -> None:
        """Test rate limit window expires."""
        # Create limiter with short window
        short_limiter = RateLimiter(max_requests=2, window_seconds=1)

        # Use up requests
        await short_limiter.check_rate_limit("client1")
        await short_limiter.check_rate_limit("client1")
        allowed, _ = await short_limiter.check_rate_limit("client1")
        assert allowed is False

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again
        allowed, remaining = await short_limiter.check_rate_limit("client1")
        assert allowed is True
        assert remaining == 1

    @pytest.mark.asyncio
    async def test_multiple_clients_independent(self, limiter: RateLimiter) -> None:
        """Test multiple clients have independent rate limits."""
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client2")

        remaining1 = await limiter.get_remaining("client1")
        remaining2 = await limiter.get_remaining("client2")

        assert remaining1 == 4
        assert remaining2 == 4

    @pytest.mark.asyncio
    async def test_concurrent_requests_same_client(self) -> None:
        """Test concurrent requests from the same client are handled correctly."""
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        import asyncio

        results = await asyncio.gather(*[limiter.check_rate_limit("client1") for _ in range(10)])
        allowed_count = sum(1 for allowed, _ in results if allowed)
        assert allowed_count == 10

    @pytest.mark.asyncio
    async def test_concurrent_requests_different_clients(self) -> None:
        """Test concurrent requests from different clients are handled correctly."""
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        import asyncio

        results = await asyncio.gather(*[limiter.check_rate_limit(f"client{i}") for i in range(5)])
        allowed_count = sum(1 for allowed, _ in results if allowed)
        assert allowed_count == 5

    @pytest.mark.asyncio
    async def test_cleanup_after_max_clients(self) -> None:
        """Test stale client cleanup when max clients reached."""
        limiter = RateLimiter(max_requests=5, window_seconds=60, max_clients=3)
        for i in range(3):
            await limiter.check_rate_limit(f"client{i}")
        assert await limiter.get_remaining("client0") == 4

    def test_rate_limiter_initialization(self) -> None:
        """Test rate limiter initializes with correct defaults."""
        limiter = RateLimiter()
        assert limiter._max_requests == 100
        assert limiter._window_seconds == 60
        assert limiter._max_clients == 10000
