"""
Pytest Fixtures for Reproducible Benchmark Testing

Provides reusable fixtures for common testing scenarios.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

import pytest
from polaris.kernelone.benchmark.reproducibility.mocks import (
    DeterministicMockProvider,
    MockLLMResponse,
    MockProviderBuilder,
)
from polaris.kernelone.benchmark.reproducibility.seed import (
    ReproducibleSeed,
    SeedContext,
    set_global_seed,
)
from polaris.kernelone.benchmark.reproducibility.vcr import CacheReplay

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

# ============================================================================
# Seed Fixtures
# ============================================================================


@pytest.fixture
def default_seed() -> int:
    """Default seed value for reproducibility."""
    return 42


@pytest.fixture
def reproducible_seed_fixture(default_seed: int) -> Generator[SeedContext, None, None]:
    """
    Fixture that sets and restores global seed around a test.

    Usage:
        def test_something(reproducible_seed_fixture):
            # Random operations are now deterministic
            value = random.random()
    """
    context = ReproducibleSeed.set_global_seed(default_seed)
    yield context
    ReproducibleSeed.restore(context)


@pytest.fixture
def seeded_random(default_seed: int) -> random.Random:
    """
    Provide a seeded random instance for isolated randomness.

    Usage:
        def test_with_isolated_random(seeded_random):
            # This random is independent of global seed
            value = seeded_random.random()
    """
    rng = random.Random(default_seed)
    return rng


@pytest.fixture
def param_seed(request: pytest.FixtureRequest) -> int:
    """
    Parameterized seed fixture.

    Usage:
        @pytest.mark.parametrize("param_seed", [1, 2, 3, 42, 100])
        def test_multi_seed(param_seed):
            context = ReproducibleSeed.set_global_seed(param_seed)
            # ...
    """
    if hasattr(request, "param"):
        return int(request.param)
    return 42


# ============================================================================
# Cache Replay Fixtures
# ============================================================================


@pytest.fixture
def cache_replay_fixture(tmp_path: Path) -> Generator[CacheReplay, None, None]:
    """
    Provide a CacheReplay instance with temporary storage.

    Usage:
        def test_cached_llm(cache_replay_fixture):
            @cache_replay_fixture.replay
            async def call_llm(msg):
                return await actual_call(msg)

            # First call records
            result1 = await call_llm("test")
            # Second call replays
            result2 = await call_llm("test")
            assert result1 == result2
    """
    cache_dir = tmp_path / "vcr_cache"
    cache = CacheReplay(cache_dir=cache_dir, mode="both")
    yield cache
    # Cleanup is automatic with tmp_path


@pytest.fixture
def replay_only_cache(tmp_path: Path) -> CacheReplay:
    """
    Provide a replay-only CacheReplay (fails if missing).

    Usage:
        def test_replay_only(replay_only_cache):
            # Will raise if recording doesn't exist
            recording = replay_only_cache._load_recording("some_key")
    """
    return CacheReplay(cache_dir=tmp_path / "replay_only", mode="replay")


@pytest.fixture
def record_only_cache(tmp_path: Path) -> CacheReplay:
    """
    Provide a record-only CacheReplay (fails if exists).

    Usage:
        def test_record_only(record_only_cache):
            # Will fail if key already exists
            record_only_cache._save_recording("key", {})
    """
    return CacheReplay(cache_dir=tmp_path / "record_only", mode="record")


# ============================================================================
# Mock Provider Fixtures
# ============================================================================


@pytest.fixture
def default_mock_responses() -> list[MockLLMResponse]:
    """Default set of mock responses for testing."""
    return [
        MockLLMResponse(
            text="First response",
            tokens_used=50,
            latency_ms=10.0,
            seed=1,
        ),
        MockLLMResponse(
            text="Second response",
            tokens_used=75,
            latency_ms=15.0,
            seed=2,
        ),
        MockLLMResponse(
            text="Third response",
            tokens_used=100,
            latency_ms=20.0,
            seed=3,
        ),
    ]


@pytest.fixture
def mock_provider_fixture(
    default_mock_responses: list[MockLLMResponse],
    default_seed: int,
) -> Generator[DeterministicMockProvider, None, None]:
    """
    Provide a deterministic mock provider.

    Usage:
        def test_with_mock_provider(mock_provider_fixture):
            response = mock_provider_fixture.get_next_response()
            assert "choices" in response
    """
    provider = DeterministicMockProvider(
        responses=default_mock_responses,
        seed=default_seed,
    )
    yield provider
    provider.reset()


@pytest.fixture
def mock_provider_builder() -> MockProviderBuilder:
    """
    Provide a mock provider builder for custom scenarios.

    Usage:
        def test_custom(mock_provider_builder):
            provider = (
                mock_provider_builder
                .add_response("Response 1")
                .add_response("Response 2")
                .build()
            )
            # ...
    """
    return MockProviderBuilder(seed=42)


# ============================================================================
# Combined Fixtures for Integration Tests
# ============================================================================


@pytest.fixture
def reproducible_benchmark_setup(
    tmp_path: Path,
    default_seed: int,
    default_mock_responses: list[MockLLMResponse],
) -> dict[str, Any]:
    """
    Combined fixture providing all reproducibility components.

    Usage:
        def test_integration(reproducible_benchmark_setup):
            seed_context = reproducible_benchmark_setup["seed_context"]
            cache = reproducible_benchmark_setup["cache"]
            provider = reproducible_benchmark_setup["provider"]
    """
    # Set up seed
    seed_context = ReproducibleSeed.set_global_seed(default_seed)

    # Set up cache
    cache = CacheReplay(cache_dir=tmp_path / "vcr_cache", mode="both")

    # Set up mock provider
    provider = DeterministicMockProvider(
        responses=default_mock_responses,
        seed=default_seed,
    )

    return {
        "seed_context": seed_context,
        "cache": cache,
        "provider": provider,
        "seed": default_seed,
        "cache_dir": tmp_path / "vcr_cache",
    }


# Re-export commonly used items for convenience
__all__ = [
    "cache_replay_fixture",
    "default_mock_responses",
    "default_seed",
    "mock_provider_builder",
    "mock_provider_fixture",
    "param_seed",
    "record_only_cache",
    "replay_only_cache",
    "reproducible_benchmark_setup",
    "reproducible_seed_fixture",
    "seeded_random",
    "set_global_seed",
]
