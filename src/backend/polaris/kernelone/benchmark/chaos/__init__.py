"""Chaos and Concurrency Benchmark Framework.

This module provides comprehensive chaos engineering and concurrency
testing capabilities for Polaris/KernelOne systems.

Features
--------
- Rate limiting simulation
- Network jitter and latency injection
- Deadlock detection
- Graceful degradation benchmarking
- Load testing
- Chaos decorators for test functions

Example
-------
    # Using decorators
    from polaris.kernelone.benchmark.chaos import ChaosConfig, ChaosScenario, chaos_test

    @chaos_test(ChaosConfig(
        scenario=ChaosScenario.NETWORK_JITTER,
        intensity=0.5,
        seed=42,
    ))
    async def test_api():
        return await api.get("/data")

    # Using simulators directly
    from polaris.kernelone.benchmark.chaos import (
        TokenBucketRateLimiter,
        NetworkJitterSimulator,
    )

    limiter = TokenBucketRateLimiter(max_requests_per_second=100.0)
    simulator = NetworkJitterSimulator(base_latency_ms=100, jitter_factor=0.1)

Modules
-------
- decorators: Chaos injection decorators
- rate_limiter: Token bucket and leaky bucket rate limiters
- network: Network jitter and latency simulators
- deadlock: Thread and async deadlock detection
- degradation: Graceful degradation benchmarking
- fixtures: Pytest fixtures for chaos testing
"""

from __future__ import annotations

# Deadlock
from polaris.kernelone.benchmark.chaos.deadlock import (
    AsyncDeadlockDetector,
    CompositeDeadlockDetector,
    DeadlockDetectedError,
    DeadlockDetector,
    DeadlockReport,
    LockAcquisition,
)

# Decorators
from polaris.kernelone.benchmark.chaos.decorators import (
    ChaosConfig,
    ChaosContext,
    ChaosResult,
    ChaosScenario,
    chaos_test,
    create_chaos_scenario,
)

# Degradation
from polaris.kernelone.benchmark.chaos.degradation import (
    DegradationMetrics,
    DegradationStage,
    DegradationStageConfig,
    GracefulDegradationBenchmark,
    LoadTestConfig,
    LoadTester,
    LoadTestResult,
    RequestResult,
)

# Network
from polaris.kernelone.benchmark.chaos.network import (
    ChaosProxy,
    ConnectionFailedError,
    FlappingNetworkSimulator,
    LatencyInjector,
    NetworkJitterConfig,
    NetworkJitterSimulator,
    NetworkLatencyStats,
    ThrottledClient,
    create_chaos_proxy,
)

# Rate Limiting
from polaris.kernelone.benchmark.chaos.rate_limiter import (
    ChaosCircuitBreakerError,
    CircuitBreaker,
    CircuitBreakerOpenError,  # Backward compatibility alias
    LeakyBucketRateLimiter,
    RateLimiterStats,
    RateLimitExceededError,
    StreamingRateLimiter,
    TokenBucketRateLimiter,
)

# Import LockTimeoutError from unified errors
from polaris.kernelone.errors import LockTimeoutError

__all__ = [
    # Deadlock
    "AsyncDeadlockDetector",
    # Rate Limiting
    "ChaosCircuitBreakerError",
    # Decorators
    "ChaosConfig",
    "ChaosContext",
    # Network
    "ChaosProxy",
    "ChaosResult",
    "ChaosScenario",
    "CircuitBreaker",
    "CircuitBreakerOpenError",  # Backward compatibility alias
    "CompositeDeadlockDetector",
    "ConnectionFailedError",
    "DeadlockDetectedError",
    "DeadlockDetector",
    "DeadlockReport",
    # Degradation
    "DegradationMetrics",
    "DegradationStage",
    "DegradationStageConfig",
    "FlappingNetworkSimulator",
    "GracefulDegradationBenchmark",
    "LatencyInjector",
    "LeakyBucketRateLimiter",
    "LoadTestConfig",
    "LoadTestResult",
    "LoadTester",
    "LockAcquisition",
    "LockTimeoutError",
    "NetworkJitterConfig",
    "NetworkJitterSimulator",
    "NetworkLatencyStats",
    "RateLimitExceededError",
    "RateLimiterStats",
    "RequestResult",
    "StreamingRateLimiter",
    "ThrottledClient",
    "TokenBucketRateLimiter",
    "chaos_test",
    "create_chaos_proxy",
    "create_chaos_scenario",
]
