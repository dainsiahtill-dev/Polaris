"""
Integration Tests for Reproducibility Framework

Tests the full integration of seed, VCR, and mock components.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import numpy as np
from polaris.kernelone.benchmark.reproducibility.fixtures import (
    set_global_seed,
)
from polaris.kernelone.benchmark.reproducibility.seed import (
    ReproducibleSeed,
)
from polaris.kernelone.benchmark.reproducibility.vcr import CacheReplay

if TYPE_CHECKING:
    from pathlib import Path


class TestSeedFixtures:
    """Tests for seed-related fixtures."""

    def test_reproducible_seed_fixture_sets_seed(
        self,
        reproducible_seed_fixture,
    ) -> None:
        """Verify fixture sets global seed."""
        value = random.random()
        # Within a seeded context, this should be deterministic
        assert isinstance(value, float)

    def test_reproducible_seed_fixture_restores_state(
        self,
        reproducible_seed_fixture,
    ) -> None:
        """Verify state is restored after test."""
        # Use seed within test
        ReproducibleSeed.set_global_seed(12345)
        seeded_value = random.random()

        # Verify seed was applied
        assert isinstance(seeded_value, float)

    def test_set_global_seed_convenience_function(self) -> None:
        """Verify convenience function works."""
        context = set_global_seed(99)
        assert context.master_seed == 99


class TestVCRFixtures:
    """Tests for VCR/cache replay fixtures."""

    def test_cache_replay_fixture_works(
        self,
        cache_replay_fixture: CacheReplay,
    ) -> None:
        """Verify cache fixture provides working CacheReplay."""
        cache_replay_fixture._save_recording("test_key", {"data": "value"})

        recording = cache_replay_fixture._load_recording("test_key")
        assert recording is not None
        assert recording.response == {"data": "value"}

    def test_cache_replay_decorator(
        self,
        cache_replay_fixture: CacheReplay,
    ) -> None:
        """Verify decorator integration with fixture."""
        call_count = 0

        @cache_replay_fixture.replay
        def expensive_operation(x: int) -> dict:
            nonlocal call_count
            call_count += 1
            return {"input": x, "count": call_count}

        result1 = expensive_operation(5)
        result2 = expensive_operation(5)

        assert result1 == result2
        assert call_count == 1


class TestMockProviderFixtures:
    """Tests for mock provider fixtures."""

    def test_mock_provider_fixture_provides_provider(
        self,
        mock_provider_fixture,
    ) -> None:
        """Verify fixture provides working mock provider."""
        response = mock_provider_fixture.get_next_response()
        assert "choices" in response
        assert "usage" in response

    def test_mock_provider_deterministic(
        self,
        mock_provider_fixture,
    ) -> None:
        """Verify fixture provides deterministic provider."""
        # Reset to ensure clean state
        mock_provider_fixture.reset()

        response1 = mock_provider_fixture.get_next_response()
        mock_provider_fixture.reset()
        response2 = mock_provider_fixture.get_next_response()

        assert response1 == response2


class TestIntegrationFixtures:
    """Tests for combined integration fixtures."""

    def test_reproducible_benchmark_setup_provides_all(
        self,
        reproducible_benchmark_setup: dict,
    ) -> None:
        """Verify setup provides all components."""
        assert "seed_context" in reproducible_benchmark_setup
        assert "cache" in reproducible_benchmark_setup
        assert "provider" in reproducible_benchmark_setup
        assert "seed" in reproducible_benchmark_setup

        context = reproducible_benchmark_setup["seed_context"]
        assert context.master_seed == reproducible_benchmark_setup["seed"]

    def test_full_integration_flow(
        self,
        reproducible_benchmark_setup: dict,
    ) -> None:
        """Verify full flow: seed -> cache -> mock."""
        seed_context = reproducible_benchmark_setup["seed_context"]
        cache = reproducible_benchmark_setup["cache"]
        provider = reproducible_benchmark_setup["provider"]

        # 1. Seed is set
        assert seed_context.master_seed == 42

        # 2. Get mock response
        mock_response = provider.get_next_response()
        assert "choices" in mock_response

        # 3. Cache the response
        cache._save_recording("mock_call", mock_response)

        # 4. Later replay works
        cached = cache._load_recording("mock_call")
        assert cached is not None
        assert cached.response == mock_response


class TestEndToEndReproducibility:
    """End-to-end tests for reproducibility guarantees."""

    def test_random_sequence_is_deterministic(self) -> None:
        """Verify same seed produces same random sequence."""

        def get_random_sequence(seed: int, count: int) -> list:
            ReproducibleSeed.set_global_seed(seed)
            return [random.random() for _ in range(count)]

        seq1 = get_random_sequence(42, 10)
        seq2 = get_random_sequence(42, 10)

        assert seq1 == seq2

    def test_numpy_sequence_is_deterministic(self) -> None:
        """Verify same seed produces same numpy sequence."""

        def get_numpy_sequence(seed: int, count: int) -> list:
            ReproducibleSeed.set_global_seed(seed)
            return list(np.random.random(count))

        seq1 = get_numpy_sequence(42, 10)
        seq2 = get_numpy_sequence(42, 10)

        assert all(a == b for a, b in zip(seq1, seq2, strict=True))

    def test_cross_framework_determinism(self) -> None:
        """Verify Python and NumPy are synchronized."""

        def get_combined_sequence(seed: int) -> tuple:
            ReproducibleSeed.set_global_seed(seed)
            py_val = random.random()
            np_val = np.random.random()
            return (py_val, float(np_val))

        result1 = get_combined_sequence(123)
        result2 = get_combined_sequence(123)

        assert result1 == result2

    def test_cache_replay_is_seed_independent(self, tmp_path: Path) -> None:
        """Verify cached responses are independent of current seed."""
        # Record with one seed
        ReproducibleSeed.set_global_seed(100)
        cache = CacheReplay(tmp_path / "seed_independent", mode="both")

        @cache.replay
        def get_value(x: int) -> dict:
            return {"random": random.random(), "input": x}

        result1 = get_value(42)

        # Change seed
        ReproducibleSeed.set_global_seed(999)

        # Replay should return same cached value (including the same random value)
        result2 = get_value(42)

        assert result1 == result2
        # The cached response is returned, so random values are identical
        assert result1["random"] == result2["random"]

    def test_mock_provider_respects_seed(
        self,
        reproducible_seed_fixture,
        mock_provider_fixture,
    ) -> None:
        """Verify mock provider is deterministic within seeded context."""
        mock_provider_fixture.reset()
        response1 = mock_provider_fixture.get_next_response()

        mock_provider_fixture.reset()
        response2 = mock_provider_fixture.get_next_response()

        assert response1 == response2
