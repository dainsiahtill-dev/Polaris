"""Tests for polaris.kernelone.security.rate_limiter."""

from __future__ import annotations

import pytest
from polaris.kernelone.security.rate_limiter import RateLimiter


class TestRateLimiter:
    @pytest.fixture
    def limiter(self) -> RateLimiter:
        return RateLimiter(max_requests=3, window_seconds=60, max_clients=10)

    @pytest.mark.asyncio
    async def test_check_rate_limit_first_request(self, limiter: RateLimiter) -> None:
        allowed, remaining = await limiter.check_rate_limit("client_1")
        assert allowed is True
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(self, limiter: RateLimiter) -> None:
        for _ in range(3):
            await limiter.check_rate_limit("client_1")
        allowed, remaining = await limiter.check_rate_limit("client_1")
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_get_remaining(self, limiter: RateLimiter) -> None:
        assert await limiter.get_remaining("new_client") == 3
        await limiter.check_rate_limit("new_client")
        assert await limiter.get_remaining("new_client") == 2

    @pytest.mark.asyncio
    async def test_reset_client(self, limiter: RateLimiter) -> None:
        await limiter.check_rate_limit("client_1")
        await limiter.reset_client("client_1")
        assert await limiter.get_remaining("client_1") == 3

    @pytest.mark.asyncio
    async def test_different_clients_independent(self, limiter: RateLimiter) -> None:
        await limiter.check_rate_limit("client_1")
        await limiter.check_rate_limit("client_1")
        remaining = await limiter.get_remaining("client_2")
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_start_stop(self, limiter: RateLimiter) -> None:
        limiter.start()
        assert limiter._cleanup_task is not None
        limiter.stop()
        assert limiter._cleanup_task is None
