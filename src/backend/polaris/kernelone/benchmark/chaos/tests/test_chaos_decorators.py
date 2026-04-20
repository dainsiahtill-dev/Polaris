"""Tests for Chaos Decorators.

Tests for the chaos_test decorator and related utilities.
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.benchmark.chaos.decorators import (
    ChaosConfig,
    ChaosContext,
    ChaosScenario,
    chaos_test,
    create_chaos_scenario,
)

# ------------------------------------------------------------------
# Test ChaosConfig
# ------------------------------------------------------------------


class TestChaosConfig:
    """Tests for ChaosConfig validation."""

    def test_valid_config(self) -> None:
        """Test creating a valid config."""
        config = ChaosConfig(
            scenario=ChaosScenario.RATE_LIMIT,
            intensity=0.5,
        )
        assert config.intensity == 0.5
        assert config.scenario == ChaosScenario.RATE_LIMIT

    def test_invalid_intensity_too_high(self) -> None:
        """Test that intensity > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="intensity must be between"):
            ChaosConfig(
                scenario=ChaosScenario.NETWORK_JITTER,
                intensity=1.5,
            )

    def test_invalid_intensity_negative(self) -> None:
        """Test that negative intensity raises ValueError."""
        with pytest.raises(ValueError, match="intensity must be between"):
            ChaosConfig(
                scenario=ChaosScenario.NETWORK_JITTER,
                intensity=-0.1,
            )

    def test_config_with_scenario_params(self) -> None:
        """Test config with scenario-specific parameters."""
        config = ChaosConfig(
            scenario=ChaosScenario.NETWORK_JITTER,
            intensity=0.3,
            base_latency_ms=200.0,
            jitter_factor=0.2,
        )
        assert config.base_latency_ms == 200.0
        assert config.jitter_factor == 0.2


# ------------------------------------------------------------------
# Test chaos_test Decorator
# ------------------------------------------------------------------


class TestChaosTestDecorator:
    """Tests for the chaos_test decorator."""

    @pytest.mark.asyncio
    async def test_decorator_no_chaos_zero_intensity(self) -> None:
        """Test that intensity=0 runs without chaos."""
        call_count = 0

        @chaos_test(
            ChaosConfig(
                scenario=ChaosScenario.NETWORK_JITTER,
                intensity=0.0,
            )
        )
        async def func() -> int:
            nonlocal call_count
            call_count += 1
            return 42

        result = await func()
        assert result == 42
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_network_jitter(self) -> None:
        """Test network jitter decorator."""

        @chaos_test(
            ChaosConfig(
                scenario=ChaosScenario.NETWORK_JITTER,
                intensity=0.1,
                base_latency_ms=10.0,  # Small delay for testing
                seed=42,
            )
        )
        async def func() -> str:
            return "success"

        result = await func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_rate_limit(self) -> None:
        """Test rate limiting decorator."""

        @chaos_test(
            ChaosConfig(
                scenario=ChaosScenario.RATE_LIMIT,
                intensity=0.5,
                max_requests_per_second=1000.0,  # High limit for testing
            )
        )
        async def func() -> str:
            return "success"

        result = await func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_timeout(self) -> None:
        """Test timeout chaos decorator."""

        @chaos_test(
            ChaosConfig(
                scenario=ChaosScenario.API_TIMEOUT,
                intensity=0.0,  # Don't apply chaos
                timeout_seconds=10.0,
            )
        )
        async def func() -> str:
            await asyncio.sleep(0.01)
            return "success"

        result = await func()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_decorator_with_args(self) -> None:
        """Test decorator preserves function arguments."""

        @chaos_test(
            ChaosConfig(
                scenario=ChaosScenario.NETWORK_LATENCY,
                intensity=0.0,
            )
        )
        async def func(x: int, y: str, z: bool = True) -> tuple[int, str, bool]:
            return (x, y, z)

        result = await func(42, "hello", z=False)
        assert result == (42, "hello", False)


# ------------------------------------------------------------------
# Test ChaosContext
# ------------------------------------------------------------------


class TestChaosContext:
    """Tests for ChaosContext manager."""

    @pytest.mark.asyncio
    async def test_context_inactive(self) -> None:
        """Test that chaos is not applied when inactive."""
        config = ChaosConfig(
            scenario=ChaosScenario.NETWORK_JITTER,
            intensity=1.0,
            seed=42,
        )
        ctx = ChaosContext(config)

        # Before entering, should_fail returns False
        assert ctx.should_fail() is False

        latency = await ctx.inject_latency()
        assert latency == 0.0

    @pytest.mark.asyncio
    async def test_context_active(self) -> None:
        """Test chaos context when active."""
        config = ChaosConfig(
            scenario=ChaosScenario.NETWORK_JITTER,
            intensity=0.5,
            base_latency_ms=100.0,
            seed=42,
        )
        ctx = ChaosContext(config)

        async with ctx as active_ctx:
            assert active_ctx is ctx
            # Latency should be injected
            latency = await active_ctx.inject_latency()
            assert latency > 0.0

    @pytest.mark.asyncio
    async def test_context_with_statement(self) -> None:
        """Test using context with 'as' statement."""
        config = ChaosConfig(
            scenario=ChaosScenario.NETWORK_JITTER,
            intensity=0.0,
        )
        async with ChaosContext(config) as ctx:
            # Should not fail
            assert ctx.should_fail() is False


# ------------------------------------------------------------------
# Test Utility Functions
# ------------------------------------------------------------------


class TestCreateChaosScenario:
    """Tests for create_chaos_scenario helper."""

    def test_create_rate_limit_scenario(self) -> None:
        """Test creating rate limit scenario."""
        config = create_chaos_scenario(
            scenario="rate_limit",
            intensity=0.5,
            max_requests_per_second=100.0,
        )
        assert config.scenario == ChaosScenario.RATE_LIMIT
        assert config.intensity == 0.5
        assert config.max_requests_per_second == 100.0

    def test_create_network_jitter_scenario(self) -> None:
        """Test creating network jitter scenario."""
        config = create_chaos_scenario(
            scenario="network_jitter",
            intensity=0.3,
            base_latency_ms=200.0,
        )
        assert config.scenario == ChaosScenario.NETWORK_JITTER
        assert config.intensity == 0.3
        assert config.base_latency_ms == 200.0

    def test_invalid_scenario_name(self) -> None:
        """Test that invalid scenario name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown scenario"):
            create_chaos_scenario(
                scenario="invalid_scenario",
                intensity=0.5,
            )

    def test_case_insensitive(self) -> None:
        """Test that scenario name is case insensitive."""
        config1 = create_chaos_scenario("RATE_LIMIT", intensity=0.5)
        config2 = create_chaos_scenario("rate_limit", intensity=0.5)
        config3 = create_chaos_scenario("Rate_Limit", intensity=0.5)

        assert config1.scenario == config2.scenario == config3.scenario
