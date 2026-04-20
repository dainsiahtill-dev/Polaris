"""Pytest Fixtures for Chaos Testing.

This module provides pytest fixtures for common chaos testing scenarios.

Example
-------
    import pytest

    @pytest.mark.asyncio
    async def test_with_chaos(chaos_network_simulator):
        result = await api.get("/data")
        assert result is not None
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator

    from polaris.kernelone.benchmark.chaos.deadlock import DeadlockDetector
    from polaris.kernelone.benchmark.chaos.decorators import ChaosContext
    from polaris.kernelone.benchmark.chaos.degradation import GracefulDegradationBenchmark
    from polaris.kernelone.benchmark.chaos.network import NetworkJitterSimulator
    from polaris.kernelone.benchmark.chaos.rate_limiter import TokenBucketRateLimiter

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def chaos_seed() -> int:
    """Provide a fixed random seed for reproducibility."""
    return 42


@pytest.fixture
def chaos_intensity() -> float:
    """Default chaos intensity for tests."""
    return 0.5


@pytest.fixture
def rate_limiter_tokens() -> TokenBucketRateLimiter:
    """Create a token bucket rate limiter for testing.

    Provides a limiter configured for 100 requests per second.
    """
    from polaris.kernelone.benchmark.chaos.rate_limiter import TokenBucketRateLimiter

    return TokenBucketRateLimiter(max_requests_per_second=100.0)


@pytest.fixture
def network_jitter_simulator(chaos_seed: int) -> NetworkJitterSimulator:
    """Create a network jitter simulator for testing.

    Provides a simulator with 100ms base latency and 10% jitter.
    """
    from polaris.kernelone.benchmark.chaos.network import NetworkJitterSimulator

    return NetworkJitterSimulator(
        base_latency_ms=100.0,
        jitter_factor=0.1,
        seed=chaos_seed,
    )


@pytest.fixture
def deadlock_detector() -> Generator[DeadlockDetector, None, None]:
    """Create a deadlock detector for testing.

    Yields:
        DeadlockDetector instance.
    """
    from polaris.kernelone.benchmark.chaos.deadlock import DeadlockDetector

    detector = DeadlockDetector(check_interval_ms=10.0)
    yield detector
    detector.stop_monitoring()


@pytest.fixture
def degradation_benchmark() -> GracefulDegradationBenchmark:
    """Create a degradation benchmark for testing."""
    from polaris.kernelone.benchmark.chaos.degradation import GracefulDegradationBenchmark

    return GracefulDegradationBenchmark()


@pytest.fixture
def chaos_context(chaos_intensity: float, chaos_seed: int) -> ChaosContext:
    """Create a chaos context for testing.

    Args:
        chaos_intensity: Intensity of chaos to inject.
        chaos_seed: Random seed for reproducibility.

    Returns:
        ChaosContext instance.
    """
    from polaris.kernelone.benchmark.chaos.decorators import ChaosConfig, ChaosContext

    config = ChaosConfig(
        scenario=chaos_intensity,  # type: ignore
        intensity=chaos_intensity,
        seed=chaos_seed,
    )
    return ChaosContext(config)


# ------------------------------------------------------------------
# Async Fixtures
# ------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_rate_limiter() -> AsyncIterator[TokenBucketRateLimiter]:
    """Async fixture providing a rate limiter.

    Yields:
        TokenBucketRateLimiter instance.
    """
    from polaris.kernelone.benchmark.chaos.rate_limiter import TokenBucketRateLimiter

    limiter = TokenBucketRateLimiter(max_requests_per_second=50.0)
    yield limiter


@pytest_asyncio.fixture
async def async_network_simulator(chaos_seed: int) -> AsyncIterator[NetworkJitterSimulator]:
    """Async fixture providing a network simulator.

    Yields:
        NetworkJitterSimulator instance.
    """
    from polaris.kernelone.benchmark.chaos.network import NetworkJitterSimulator

    simulator = NetworkJitterSimulator(
        base_latency_ms=50.0,
        jitter_factor=0.2,
        seed=chaos_seed,
    )
    yield simulator


# ------------------------------------------------------------------
# Parameterized Fixtures
# ------------------------------------------------------------------


def chaos_intensity_params() -> list[float]:
    """Provide chaos intensity values for parameterized tests."""
    return [0.0, 0.25, 0.5, 0.75, 1.0]


@pytest.fixture(params=chaos_intensity_params())
def chaos_intensity_param(request: pytest.FixtureRequest) -> float:
    """Parameterized chaos intensity fixture.

    Use this fixture to run tests across multiple intensity levels.
    """
    return request.param


# ------------------------------------------------------------------
# Scenarios Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def rate_limit_scenario() -> dict[str, Any]:
    """Provide rate limiting scenario parameters."""
    return {
        "scenario": "rate_limit",
        "max_requests_per_second": 10.0,
        "burst_capacity": 5.0,
    }


@pytest.fixture
def network_jitter_scenario() -> dict[str, Any]:
    """Provide network jitter scenario parameters."""
    return {
        "scenario": "network_jitter",
        "base_latency_ms": 100.0,
        "jitter_factor": 0.2,
        "packet_loss_rate": 0.05,
    }


@pytest.fixture
def timeout_scenario() -> dict[str, Any]:
    """Provide timeout scenario parameters."""
    return {
        "scenario": "api_timeout",
        "timeout_seconds": 5.0,
        "intensity": 0.5,
    }


# ------------------------------------------------------------------
# Marker Helpers
# ------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "chaos: mark test as a chaos engineering test")
    config.addinivalue_line("markers", "chaos_rate_limit: rate limiting chaos scenario")
    config.addinivalue_line("markers", "chaos_network: network chaos scenario")
    config.addinivalue_line("markers", "chaos_deadlock: deadlock detection scenario")
    config.addinivalue_line("markers", "chaos_degradation: graceful degradation scenario")


# ------------------------------------------------------------------
# Test Helpers
# ------------------------------------------------------------------


async def run_with_chaos(
    func: Any,
    scenario: str,
    intensity: float = 0.5,
    **kwargs: Any,
) -> Any:
    """Helper to run a function with chaos injection.

    Args:
        func: Async function to run.
        scenario: Chaos scenario name.
        intensity: Chaos intensity.
        **kwargs: Additional scenario parameters.

    Returns:
        Result of the function.
    """
    from polaris.kernelone.benchmark.chaos.decorators import (
        ChaosConfig,
        ChaosScenario,
        chaos_test,
    )

    config = ChaosConfig(
        scenario=ChaosScenario(scenario),
        intensity=intensity,
        **kwargs,
    )

    decorated = chaos_test(config)(func)
    return await decorated()
