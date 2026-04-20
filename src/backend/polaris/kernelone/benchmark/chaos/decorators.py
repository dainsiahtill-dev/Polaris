"""Chaos Testing Decorators.

This module provides decorators for injecting various chaos scenarios
into async functions for testing purposes.

Example
-------
    @chaos_test(ChaosConfig(scenario=ChaosScenario.NETWORK_JITTER, intensity=0.5))
    async def test_api_call():
        return await api.get("/data")
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.kernelone.errors import ChaosInjectionError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class ChaosScenario(Enum):
    """Types of chaos scenarios."""

    RATE_LIMIT = "rate_limit"
    NETWORK_LATENCY = "network_latency"
    NETWORK_JITTER = "network_jitter"
    MEMORY_PRESSURE = "memory_pressure"
    API_TIMEOUT = "api_timeout"
    PACKET_LOSS = "packet_loss"
    CIRCUIT_BREAKER = "circuit_breaker"
    CONNECTION_FLAPPING = "connection_flapping"


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ChaosConfig:
    """Configuration for chaos testing.

    Attributes:
        scenario: Type of chaos scenario to inject.
        intensity: Chaos intensity (0.0 - 1.0).
        seed: Random seed for reproducibility.
        **kwargs: Additional scenario-specific parameters.
    """

    scenario: ChaosScenario
    intensity: float  # 0.0 - 1.0
    seed: int | None = None
    # Scenario-specific parameters
    base_latency_ms: float | None = None
    jitter_factor: float | None = None
    max_requests_per_second: float | None = None
    timeout_seconds: float | None = None
    error_rate: float | None = None
    failure_threshold: int | None = None
    recovery_timeout: float | None = None

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError("intensity must be between 0.0 and 1.0")


@dataclass(frozen=True, kw_only=True)
class ChaosResult:
    """Result of a chaos-decorated function execution.

    Attributes:
        success: Whether the function succeeded.
        chaos_applied: Whether chaos was actually injected.
        actual_latency_ms: Actual latency including chaos injection.
        chaos_latency_ms: Latency added by chaos injection.
        error: Error if function failed.
    """

    success: bool
    chaos_applied: bool
    actual_latency_ms: float
    chaos_latency_ms: float
    error: str | None = None


# ------------------------------------------------------------------
# Chaos Decorators
# ------------------------------------------------------------------


def chaos_test(config: ChaosConfig) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for applying chaos scenarios to async functions.

    Args:
        config: Chaos configuration.

    Returns:
        Decorated function with chaos injection.

    Example
    -------
        @chaos_test(ChaosConfig(
            scenario=ChaosScenario.NETWORK_JITTER,
            intensity=0.5,
            base_latency_ms=100,
        ))
        async def fetch_data():
            return await api.get("/data")
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Seed random for reproducibility
            rng = random.Random(config.seed)

            # Decide if chaos should be applied based on intensity
            should_apply = rng.random() < config.intensity

            # Execute appropriate chaos scenario
            if config.scenario == ChaosScenario.RATE_LIMIT:
                return await _rate_limit_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.NETWORK_JITTER:
                return await _network_jitter_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.NETWORK_LATENCY:
                return await _network_latency_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.API_TIMEOUT:
                return await _timeout_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.MEMORY_PRESSURE:
                return await _memory_pressure_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.PACKET_LOSS:
                return await _packet_loss_chaos(func, config, args, kwargs, should_apply, rng)
            elif config.scenario == ChaosScenario.CIRCUIT_BREAKER:
                return await _circuit_breaker_chaos(func, config, args, kwargs, should_apply, rng)
            else:
                # Fallback: run without chaos
                return await func(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------------------------------------------
# Chaos Scenario Implementations
# ------------------------------------------------------------------


async def _rate_limit_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply rate limiting chaos."""
    from polaris.kernelone.benchmark.chaos.rate_limiter import (
        RateLimitExceededError,
        TokenBucketRateLimiter,
    )

    rate = config.max_requests_per_second or 10.0
    limiter = TokenBucketRateLimiter(rate)

    try:
        await limiter.acquire(timeout=1.0)
        return await func(*args, **kwargs)
    except RateLimitExceededError as e:
        raise ChaosInjectionError(f"Rate limit exceeded: {e}") from e


async def _network_jitter_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply network jitter chaos."""
    from polaris.kernelone.benchmark.chaos.network import (
        ConnectionFailedError,
        NetworkJitterSimulator,
    )

    base = config.base_latency_ms or 100.0
    jitter = config.jitter_factor or 0.1

    simulator = NetworkJitterSimulator(
        base_latency_ms=base,
        jitter_factor=jitter * config.intensity,
        seed=config.seed,
    )

    try:
        await simulator.simulate()
        return await func(*args, **kwargs)
    except ConnectionFailedError as e:
        raise ChaosInjectionError(f"Simulated connection failure: {e}") from e


async def _network_latency_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply fixed network latency chaos."""
    latency_ms = (config.base_latency_ms or 100.0) * config.intensity
    await asyncio.sleep(latency_ms / 1000.0)
    return await func(*args, **kwargs)


async def _timeout_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply timeout chaos."""
    timeout = config.timeout_seconds or 5.0
    effective_timeout = timeout * (1.0 - config.intensity * 0.9)  # Reduce timeout

    try:
        return await asyncio.wait_for(
            func(*args, **kwargs),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError as e:
        raise ChaosInjectionError(f"Simulated timeout after {effective_timeout}s") from e


async def _memory_pressure_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply memory pressure chaos."""
    # Allocate memory proportional to intensity
    if config.intensity > 0.5:
        # Large allocation for high intensity
        _ = bytearray(int(10 * 1024 * 1024 * config.intensity))

    return await func(*args, **kwargs)


async def _packet_loss_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply packet loss chaos."""
    if rng.random() < config.intensity:
        raise ChaosInjectionError("Simulated packet loss")

    return await func(*args, **kwargs)


async def _circuit_breaker_chaos(
    func: Callable[..., Awaitable[T]],
    config: ChaosConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    should_apply: bool,
    rng: random.Random,
) -> T:
    """Apply circuit breaker chaos."""
    from polaris.kernelone.benchmark.chaos.rate_limiter import (
        ChaosCircuitBreakerError,
        CircuitBreaker,
    )

    failure_threshold = config.failure_threshold or 3
    recovery_timeout = config.recovery_timeout or 5.0

    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
    )

    # Simulate failures if in degraded state
    if rng.random() < config.intensity * 0.5:
        breaker.record_failure()

    try:
        return await breaker.execute(func, *args, **kwargs)
    except ChaosCircuitBreakerError as e:
        raise ChaosInjectionError(f"Circuit breaker open: {e}") from e


# ------------------------------------------------------------------
# Context Manager Decorator
# ------------------------------------------------------------------


class ChaosContext:
    """Context manager for applying chaos scenarios.

    Example
    -------
        async with ChaosContext(config) as chaos:
            result = await api.get("/data")
    """

    __slots__ = ("_active", "_config", "_rng")

    def __init__(self, config: ChaosConfig) -> None:
        self._config = config
        self._rng = random.Random(config.seed)
        self._active = False

    async def __aenter__(self) -> ChaosContext:
        """Enter chaos context."""
        self._active = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Exit chaos context."""
        self._active = False
        return False

    async def inject_latency(self, base_ms: float = 100.0) -> float:
        """Inject latency based on configuration.

        Returns:
            Actual latency injected in milliseconds.
        """
        if not self._active:
            return 0.0

        base = self._config.base_latency_ms or base_ms
        jitter = self._config.jitter_factor or 0.1

        latency = base * (1.0 + self._rng.uniform(-jitter, jitter) * self._config.intensity)
        await asyncio.sleep(latency / 1000.0)
        return latency

    def should_fail(self) -> bool:
        """Determine if chaos should cause a failure.

        Returns:
            True if the operation should fail.
        """
        if not self._active:
            return False
        return self._rng.random() < self._config.intensity * 0.3


# ------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------


def create_chaos_scenario(
    scenario: str,
    intensity: float = 0.5,
    **kwargs: Any,
) -> ChaosConfig:
    """Create a chaos config from string scenario name.

    Args:
        scenario: Scenario name (case-insensitive).
        intensity: Chaos intensity.
        **kwargs: Additional parameters.

    Returns:
        Configured ChaosConfig.
    """
    try:
        scenario_enum = ChaosScenario(scenario.lower())
    except ValueError as e:
        valid = [s.value for s in ChaosScenario]
        raise ValueError(f"Unknown scenario: {scenario}. Valid: {valid}") from e

    return ChaosConfig(
        scenario=scenario_enum,
        intensity=intensity,
        **kwargs,
    )
