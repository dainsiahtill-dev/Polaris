"""Tests for circuit breaker patterns in API resilience.

Covers circuit breaker states, transition logic, integration with v2 routes,
and edge cases. Tests the CircuitBreaker implementation from
polaris.kernelone.llm.engine.resilience in the context of HTTP delivery.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.kernelone.errors import CircuitBreakerOpenError
from polaris.kernelone.llm.engine.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    MultiProviderFallbackManager,
    ProviderEndpoint,
    ResilienceManager,
    TimeoutConfig,
    get_circuit_breaker_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_circuit_breaker_registry() -> None:
    """Clear the global registry before each test to prevent state leaks."""
    registry = get_circuit_breaker_registry()
    registry.clear()
    yield


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
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
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Circuit Breaker States
# ---------------------------------------------------------------------------


class TestCircuitBreakerStates:
    """Tests for the three circuit breaker states."""

    def test_initial_state_is_closed(self) -> None:
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_state_allows_requests(self) -> None:
        """CLOSED state allows requests to pass through."""
        cb = CircuitBreaker(name="test")

        async def succeed() -> str:
            return "success"

        result = await cb.call(succeed)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_open_state_fails_fast(self) -> None:
        """OPEN state fails fast with CircuitBreakerOpenError."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=60.0,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("always fail")

        # Trigger OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Next call should fail fast
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await cb.call(fail)

        assert exc_info.value.circuit_name == "test"

    @pytest.mark.asyncio
    async def test_half_open_allows_limited_requests(self) -> None:
        """HALF_OPEN state allows limited test requests."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                half_open_max_calls=2,
                success_threshold=3,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Trigger OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # Should transition to HALF_OPEN on next call attempt
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Was briefly HALF_OPEN before failure transitioned back to OPEN
        assert cb.half_open_calls >= 1


# ---------------------------------------------------------------------------
# Transition Logic
# ---------------------------------------------------------------------------


class TestCircuitBreakerTransitions:
    """Tests for circuit breaker state transitions."""

    @pytest.mark.asyncio
    async def test_opens_after_n_failures(self) -> None:
        """After N failures, circuit opens."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )

        async def fail() -> NoReturn:
            raise ValueError("test error")

        # Fail until threshold
        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self) -> None:
        """After timeout, circuit goes to HALF_OPEN."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Trigger OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # Next call transitions to HALF_OPEN then back to OPEN on failure
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.half_open_calls == 1

    @pytest.mark.asyncio
    async def test_closes_after_m_successes_in_half_open(self) -> None:
        """After M successes in half-open, circuit closes."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                success_threshold=2,
            ),
        )

        async def succeed() -> str:
            return "success"

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Force OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # First success in HALF_OPEN
        result1 = await cb.call(succeed)
        assert result1 == "success"
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.half_open_successes == 1

        # Second success should close circuit
        result2 = await cb.call(succeed)
        assert result2 == "success"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_reopens_after_failure_in_half_open(self) -> None:
        """After failure in half-open, circuit opens again."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Force OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # Transition to HALF_OPEN and fail immediately -> back to OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_in_closed_resets_failure_count(self) -> None:
        """Success in CLOSED state resets failure count."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(failure_threshold=3),
        )

        async def fail_once_then_succeed() -> str:
            if cb.failure_count < 1:
                raise ValueError("first failure")
            return "success"

        # First call fails
        with pytest.raises(ValueError):
            await cb.call(fail_once_then_succeed)

        assert cb.failure_count == 1

        # Second call succeeds, resets failure count
        result = await cb.call(fail_once_then_succeed)
        assert result == "success"
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Integration with v2 routes
# ---------------------------------------------------------------------------


class TestCircuitBreakerV2RouteIntegration:
    """Tests for circuit breaker integration around v2 route services."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_around_llm_provider_call(self) -> None:
        """Circuit breaker around LLM provider calls fails fast when open."""
        cb = CircuitBreaker(
            name="llm_provider",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60.0),
        )

        call_count = 0

        async def llm_call() -> str:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Provider unavailable")

        # First call fails and opens circuit
        with pytest.raises(ConnectionError):
            await cb.call(llm_call)

        assert cb.state == CircuitState.OPEN
        assert call_count == 1

        # Subsequent calls fail fast without hitting provider
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(llm_call)

        assert call_count == 1  # No additional calls

    @pytest.mark.asyncio
    async def test_circuit_breaker_around_factory_run_service(self) -> None:
        """Circuit breaker around factory run service prevents cascade."""
        cb = CircuitBreaker(
            name="factory_run",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30.0),
        )

        call_count = 0

        async def factory_execute() -> dict:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Factory execution failed")

        # Fail twice to open circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(factory_execute)

        assert cb.state == CircuitState.OPEN
        assert call_count == 2

        # Third call fails fast
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(factory_execute)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_behavior_when_circuit_open(self) -> None:
        """Fallback behavior returns degraded response when circuit is open."""
        cb = CircuitBreaker(
            name="service",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60.0),
        )

        async def primary_service() -> str:
            raise ConnectionError("Service down")

        async def fallback_service() -> str:
            return "fallback_response"

        # Open the circuit
        with pytest.raises(ConnectionError):
            await cb.call(primary_service)

        # Use fallback when circuit is open
        try:
            await cb.call(primary_service)
        except CircuitBreakerOpenError:
            result = await fallback_service()

        assert result == "fallback_response"

    @pytest.mark.asyncio
    async def test_resilience_manager_integration_with_circuit_breaker(self) -> None:
        """ResilienceManager integrates circuit breaker with timeout and retry."""
        cb = CircuitBreaker(
            name="resilient_service",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=10.0),
        )

        manager = ResilienceManager(
            timeout_config=TimeoutConfig(request_timeout=5.0),
            circuit_breaker=cb,
        )

        assert manager.circuit_breaker is cb
        status = manager.get_circuit_breaker_status()
        assert status is not None
        assert status["name"] == "resilient_service"
        assert status["state"] == "closed"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestCircuitBreakerEdgeCases:
    """Tests for circuit breaker edge cases."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_in_half_open(self) -> None:
        """Concurrent requests when circuit is half-open are handled correctly."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                half_open_max_calls=2,
                success_threshold=3,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Force OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        call_count = 0

        async def track_call() -> str:
            nonlocal call_count
            call_count += 1
            return f"success_{call_count}"

        # Launch multiple concurrent calls
        tasks = []
        for _ in range(5):
            tasks.append(asyncio.create_task(cb.call(track_call)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Some should succeed (up to half_open_max_calls)
        successes = [r for r in results if isinstance(r, str)]
        open_errors = [r for r in results if isinstance(r, CircuitBreakerOpenError)]

        assert len(successes) <= 2
        assert len(open_errors) >= 3

    @pytest.mark.asyncio
    async def test_different_failure_thresholds(self) -> None:
        """Circuit breaker with different failure thresholds."""
        # Low threshold
        cb_low = CircuitBreaker(
            name="low",
            config=CircuitBreakerConfig(failure_threshold=1),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await cb_low.call(fail)
        assert cb_low.state == CircuitState.OPEN

        # High threshold
        cb_high = CircuitBreaker(
            name="high",
            config=CircuitBreakerConfig(failure_threshold=5),
        )

        for _ in range(4):
            with pytest.raises(ValueError):
                await cb_high.call(fail)

        assert cb_high.state == CircuitState.CLOSED

        # Fifth failure opens
        with pytest.raises(ValueError):
            await cb_high.call(fail)

        assert cb_high.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_half_open_max_calls_enforcement(self) -> None:
        """HALF_OPEN rejects calls exceeding max calls."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                half_open_max_calls=1,
                success_threshold=3,
            ),
        )

        async def succeed() -> str:
            return "success"

        async def fail() -> NoReturn:
            raise ValueError("fail")

        # Force OPEN state
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Wait for recovery timeout
        await asyncio.sleep(0.05)

        # First call in HALF_OPEN succeeds
        result = await cb.call(succeed)
        assert result == "success"

        # Second call should be rejected (exceeds half_open_max_calls=1)
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(succeed)

    def test_time_until_reset_calculation(self) -> None:
        """Time until reset is calculated correctly."""
        cb = CircuitBreaker(
            name="test",
            config=CircuitBreakerConfig(recovery_timeout=10.0),
        )

        cb.last_failure_time = time.monotonic() - 5.0
        remaining = cb._time_until_reset()
        assert remaining is not None
        assert 4.0 <= remaining <= 6.0

    def test_time_until_reset_none_when_no_failure(self) -> None:
        """Time until reset returns 0 when no failure recorded."""
        cb = CircuitBreaker(name="test")
        assert cb._time_until_reset() == 0.0

    @pytest.mark.asyncio
    async def test_reset_manual(self) -> None:
        """Manual reset clears all state and returns to CLOSED."""
        cb = CircuitBreaker(name="test")

        # Manually set some state
        cb.failure_count = 10
        cb.success_count = 5
        cb.state = CircuitState.OPEN
        cb.last_failure_time = time.monotonic()

        cb.reset()

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0
        assert cb.last_failure_time is None
        assert cb.half_open_calls == 0
        assert cb.half_open_successes == 0

    @pytest.mark.asyncio
    async def test_cancelled_error_propagation(self) -> None:
        """CancelledError must propagate through circuit breaker without counting as failure."""
        cb = CircuitBreaker(name="test")

        async def cancelled_call() -> NoReturn:
            raise asyncio.CancelledError("task cancelled")

        with pytest.raises(asyncio.CancelledError):
            await cb.call(cancelled_call)

        # CancelledError should not count as failure
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_get_status_returns_all_fields(self) -> None:
        """Status includes all relevant metrics."""
        cb = CircuitBreaker(
            name="test_breaker",
            config=CircuitBreakerConfig(failure_threshold=5),
        )

        status = cb.get_status()

        assert status["name"] == "test_breaker"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0
        assert status["success_count"] == 0
        assert status["half_open_calls"] == 0
        assert status["half_open_successes"] == 0
        assert "config" in status
        assert status["config"]["failure_threshold"] == 5


# ---------------------------------------------------------------------------
# Registry Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerRegistry:
    """Tests for circuit breaker registry."""

    @pytest.mark.asyncio
    async def test_registry_get_or_create_same_instance(self) -> None:
        """Same name returns same breaker instance."""
        registry = get_circuit_breaker_registry()
        breaker1 = await registry.get_or_create("test")
        breaker2 = await registry.get_or_create("test")

        assert breaker1 is breaker2

    @pytest.mark.asyncio
    async def test_registry_get_or_create_different_names(self) -> None:
        """Different names return different breakers."""
        registry = get_circuit_breaker_registry()
        breaker1 = await registry.get_or_create("test1")
        breaker2 = await registry.get_or_create("test2")

        assert breaker1 is not breaker2
        assert breaker1.name == "test1"
        assert breaker2.name == "test2"

    def test_registry_get_all_status(self) -> None:
        """Get status of all registered breakers."""
        registry = get_circuit_breaker_registry()
        registry._breakers.clear()

        cb1 = CircuitBreaker(name="breaker1")
        cb2 = CircuitBreaker(name="breaker2")
        registry._breakers["breaker1"] = cb1
        registry._breakers["breaker2"] = cb2

        status = registry.get_all_status()

        assert "breaker1" in status
        assert "breaker2" in status
        assert status["breaker1"]["state"] == "closed"
        assert status["breaker2"]["state"] == "closed"

    @pytest.mark.asyncio
    async def test_registry_reset_all(self) -> None:
        """Reset all clears all breaker states."""
        registry = get_circuit_breaker_registry()
        registry._breakers.clear()

        cb = CircuitBreaker(name="test")
        cb.state = CircuitState.OPEN
        cb.failure_count = 5
        registry._breakers["test"] = cb

        await registry.reset_all()

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


# ---------------------------------------------------------------------------
# Multi-Provider Fallback Tests
# ---------------------------------------------------------------------------


class TestMultiProviderFallback:
    """Tests for multi-provider fallback with circuit breaker."""

    @pytest.mark.asyncio
    async def test_fallback_chain_on_provider_failure(self) -> None:
        """Fallback chain switches to secondary provider on failure."""

        async def primary() -> str:
            raise ConnectionError("Primary down")

        async def secondary() -> str:
            return "secondary_result"

        endpoints = [
            ProviderEndpoint(name="primary", invoke=primary),
            ProviderEndpoint(name="secondary", invoke=secondary),
        ]

        manager = MultiProviderFallbackManager(endpoints)
        result = await manager.invoke()

        assert result.provider == "secondary"
        assert result.fallback_used is True
        assert result.value == "secondary_result"

    @pytest.mark.asyncio
    async def test_fallback_chain_exhaustion(self) -> None:
        """Fallback chain raises last error when all providers fail."""

        async def fail1() -> NoReturn:
            raise ConnectionError("Error 1")

        async def fail2() -> NoReturn:
            raise ConnectionError("Error 2")

        endpoints = [
            ProviderEndpoint(name="first", invoke=fail1),
            ProviderEndpoint(name="second", invoke=fail2),
        ]

        manager = MultiProviderFallbackManager(endpoints)

        with pytest.raises(ConnectionError, match="Error 2"):
            await manager.invoke()

    @pytest.mark.asyncio
    async def test_fallback_with_circuit_breaker(self) -> None:
        """Circuit breaker + fallback: open circuit triggers fallback."""
        cb = CircuitBreaker(
            name="primary_provider",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60.0),
        )

        async def primary() -> str:
            raise ConnectionError("Primary down")

        async def fallback() -> str:
            return "fallback_result"

        # Open the circuit
        with pytest.raises(ConnectionError):
            await cb.call(primary)

        # When circuit is open, use fallback directly
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(primary)

        # Fallback is used
        result = await fallback()
        assert result == "fallback_result"


# ---------------------------------------------------------------------------
# HTTP-level Integration Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerHTTPIntegration:
    """Tests simulating circuit breaker behavior at HTTP level."""

    @pytest.mark.asyncio
    async def test_http_endpoint_with_circuit_breaker_mock(self, mock_settings: Settings) -> None:
        """Simulate circuit breaker protecting an HTTP endpoint."""
        from polaris.delivery.http.app_factory import create_app

        app = create_app(settings=mock_settings)
        app.state.auth = None

        cb = CircuitBreaker(
            name="http_endpoint",
            config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=30.0),
        )

        call_count = 0

        async def protected_operation() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("Service unavailable")
            return {"status": "ok"}

        # Simulate two failures opening the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(protected_operation)

        assert cb.state == CircuitState.OPEN

        # Third call fails fast
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(protected_operation)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_circuit_breaker_status_endpoint(self, client: AsyncClient) -> None:
        """Circuit breaker status can be exposed via an endpoint."""
        cb = CircuitBreaker(
            name="api_service",
            config=CircuitBreakerConfig(failure_threshold=3),
        )

        status = cb.get_status()
        assert status["state"] == "closed"
        assert status["failure_count"] == 0

        # Simulate failures
        async def fail() -> NoReturn:
            raise ValueError("fail")

        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(fail)

        status = cb.get_status()
        assert status["state"] == "open"
        assert status["failure_count"] == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery_flow(self) -> None:
        """Full recovery flow: closed -> open -> half_open -> closed."""
        cb = CircuitBreaker(
            name="recovery_test",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,
                success_threshold=1,
            ),
        )

        async def fail() -> NoReturn:
            raise ValueError("fail")

        async def succeed() -> str:
            return "recovered"

        # Step 1: CLOSED -> OPEN
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state == CircuitState.OPEN

        # Step 2: Wait for timeout
        await asyncio.sleep(0.05)

        # Step 3: OPEN -> HALF_OPEN -> CLOSED (on success)
        result = await cb.call(succeed)
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Configuration Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerConfig:
    """Tests for circuit breaker configuration."""

    def test_default_config_values(self) -> None:
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60.0
        assert config.half_open_max_calls == 3
        assert config.success_threshold == 2
        assert config.window_seconds == 120.0

    def test_config_from_options(self) -> None:
        """Test configuration from options dict."""
        config = CircuitBreakerConfig.from_options(
            {
                "cb_failure_threshold": 10,
                "cb_recovery_timeout": 120.0,
                "cb_half_open_max_calls": 5,
                "cb_success_threshold": 3,
            }
        )
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 120.0
        assert config.half_open_max_calls == 5
        assert config.success_threshold == 3

    def test_custom_config_on_breaker(self) -> None:
        """Custom config is applied to circuit breaker."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=5.0,
            half_open_max_calls=1,
            success_threshold=1,
        )
        cb = CircuitBreaker(name="custom", config=config)

        assert cb.config.failure_threshold == 1
        assert cb.config.recovery_timeout == 5.0
