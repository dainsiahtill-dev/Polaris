"""Network Chaos Simulator for Chaos Testing.

This module provides network condition simulation including:
- Latency injection
- Jitter simulation
- Packet loss simulation
- Connection failures

Example
-------
    simulator = NetworkJitterSimulator(base_latency_ms=100, jitter_factor=0.2)
    latency = await simulator.simulate()
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from polaris.kernelone.errors import NetworkChaosError


class ConnectionFailedError(NetworkChaosError):
    """Raised when simulated connection fails."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")

# ------------------------------------------------------------------
# Statistics
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class NetworkLatencyStats:
    """Statistics for network latency simulation."""

    total_calls: int = 0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        """Calculate average latency."""
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / self.total_calls

    def update(self, latency_ms: float) -> NetworkLatencyStats:
        """Create updated statistics with new latency."""
        return NetworkLatencyStats(
            total_calls=self.total_calls + 1,
            min_latency_ms=min(self.min_latency_ms, latency_ms),
            max_latency_ms=max(self.max_latency_ms, latency_ms),
            total_latency_ms=self.total_latency_ms + latency_ms,
        )


@dataclass
class NetworkJitterConfig:
    """Configuration for network jitter."""

    base_latency_ms: float = 100.0
    jitter_factor: float = 0.1  # 0.0 - 1.0, percentage of base
    packet_loss_rate: float = 0.0  # 0.0 - 1.0
    corruption_rate: float = 0.0  # 0.0 - 1.0

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not 0.0 <= self.jitter_factor <= 1.0:
            raise ValueError("jitter_factor must be between 0.0 and 1.0")
        if not 0.0 <= self.packet_loss_rate <= 1.0:
            raise ValueError("packet_loss_rate must be between 0.0 and 1.0")
        if not 0.0 <= self.corruption_rate <= 1.0:
            raise ValueError("corruption_rate must be between 0.0 and 1.0")


# ------------------------------------------------------------------
# Network Simulators
# ------------------------------------------------------------------


class NetworkJitterSimulator:
    """Simulates network jitter and variable latency.

    Attributes:
        config: Jitter configuration.
        seed: Random seed for reproducibility.
    """

    __slots__ = ("_config", "_rng", "_stats")

    def __init__(
        self,
        base_latency_ms: float = 100.0,
        jitter_factor: float = 0.1,
        seed: int | None = None,
    ) -> None:
        self._config = NetworkJitterConfig(
            base_latency_ms=base_latency_ms,
            jitter_factor=jitter_factor,
        )
        self._rng = random.Random(seed)
        self._stats = NetworkLatencyStats()

    @property
    def config(self) -> NetworkJitterConfig:
        """Get current configuration."""
        return self._config

    @property
    def stats(self) -> NetworkLatencyStats:
        """Get current statistics."""
        return self._stats

    def set_config(self, config: NetworkJitterConfig) -> None:
        """Update configuration."""
        self._config = config

    async def simulate(self) -> float:
        """Simulate network latency with jitter.

        Returns:
            Actual latency in milliseconds.
        """
        # Calculate jittered latency
        jitter_range = self._config.jitter_factor * self._config.base_latency_ms
        latency = self._config.base_latency_ms + self._rng.uniform(-jitter_range, jitter_range)
        latency = max(0.0, latency)  # Ensure non-negative

        # Simulate packet loss
        if self._rng.random() < self._config.packet_loss_rate:
            raise ConnectionFailedError("Simulated packet loss")

        # Wait for simulated latency
        await asyncio.sleep(latency / 1000.0)

        # Update stats
        self._stats = self._stats.update(latency)

        return latency

    async def wrap(
        self,
        coro: Awaitable[T],
        track_stats: bool = True,
    ) -> T:
        """Wrap an awaitable with network simulation.

        Args:
            coro: Coroutine to wrap.
            track_stats: Whether to track latency statistics.

        Returns:
            Result of the coroutine.

        Raises:
            ConnectionFailedError: If simulated connection fails.
        """
        latency = await self.simulate()
        try:
            return await coro
        finally:
            if not track_stats:
                self._stats = self._stats.update(latency)


class LatencyInjector:
    """Injects fixed or variable latency into async operations.

    This is useful for testing timeout behavior and client retry logic.
    """

    __slots__ = ("_latency_ms", "_rng", "_variability_ms")

    def __init__(
        self,
        latency_ms: float,
        variability_ms: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self._latency_ms = latency_ms
        self._variability_ms = variability_ms
        self._rng = random.Random(seed)

    async def inject(self) -> None:
        """Inject configured latency."""
        actual = self._latency_ms
        if self._variability_ms > 0:
            actual += self._rng.uniform(-self._variability_ms, self._variability_ms)
        actual = max(0.0, actual)
        await asyncio.sleep(actual / 1000.0)

    async def wrap(self, coro: Awaitable[T]) -> T:
        """Wrap coroutine with latency injection."""
        await self.inject()
        return await coro


class FlappingNetworkSimulator:
    """Simulates network flapping (intermittent failures).

    Network flapping occurs when a connection repeatedly fails
    and recovers in short succession.
    """

    __slots__ = (
        "_current_state",
        "_duration_range",
        "_rng",
        "_state_since",
        "_up_probability",
    )

    def __init__(
        self,
        up_probability: float = 0.5,
        min_up_duration_ms: float = 1000.0,
        max_up_duration_ms: float = 5000.0,
        seed: int | None = None,
    ) -> None:
        self._up_probability = up_probability
        self._duration_range = (min_up_duration_ms, max_up_duration_ms)
        self._rng = random.Random(seed)
        self._current_state = True
        self._state_since = asyncio.get_event_loop().time() * 1000

    @property
    def is_up(self) -> bool:
        """Check if network is currently up."""
        # Check if we should flip state
        if self._rng.random() < self._up_probability:
            return self._current_state
        else:
            return not self._current_state

    async def ensure_up(self) -> None:
        """Ensure network is up before proceeding."""
        while not self.is_up:
            await asyncio.sleep(0.01)


class ChaosProxy(Generic[T]):
    """Proxy that applies network chaos to wrapped functions.

    This is useful for wrapping external API calls with chaos.
    """

    __slots__ = ("_simulator", "_wrapped")

    def __init__(
        self,
        simulator: NetworkJitterSimulator,
        wrapped: Callable[..., Awaitable[T]],
    ) -> None:
        self._simulator = simulator
        self._wrapped = wrapped

    async def __call__(self, *args: Any, **kwargs: Any) -> T:
        """Execute wrapped function with chaos simulation."""
        return await self._simulator.wrap(self._wrapped(*args, **kwargs))


def create_chaos_proxy(
    simulator: NetworkJitterSimulator,
) -> Callable[[Callable[..., Awaitable[T]]], ChaosProxy[T]]:
    """Create a chaos proxy decorator.

    Example
    -------
        @create_chaos_proxy(simulator)
        async def fetch_data(url: str) -> dict:
            return await http_get(url)
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> ChaosProxy[T]:
        return ChaosProxy(simulator, func)

    return decorator


# ------------------------------------------------------------------
# Throttling
# ------------------------------------------------------------------


class ThrottledClient(Generic[T]):
    """Client wrapper that throttles requests.

    Useful for testing client-side rate limiting behavior.
    """

    __slots__ = ("_client", "_limiter")

    def __init__(
        self,
        wrapped: Callable[..., Awaitable[T]],
        requests_per_second: float,
    ) -> None:
        from polaris.kernelone.benchmark.chaos.rate_limiter import (
            TokenBucketRateLimiter,
        )

        self._limiter = TokenBucketRateLimiter(requests_per_second)
        self._client = wrapped

    async def request(self, *args: Any, **kwargs: Any) -> T:
        """Make a throttled request."""
        await self._limiter.acquire()
        return await self._client(*args, **kwargs)

    async def request_batch(
        self,
        requests: list[tuple[tuple[Any, ...], dict[str, Any]]],
    ) -> list[T]:
        """Execute multiple requests with throttling.

        Args:
            requests: List of (args, kwargs) tuples.

        Returns:
            List of results.
        """
        results: list[T] = []
        for args, kwargs in requests:
            result = await self.request(*args, **kwargs)
            results.append(result)
        return results
