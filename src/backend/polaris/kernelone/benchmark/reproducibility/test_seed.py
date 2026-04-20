"""
Tests for Seed Injection System

Verifies deterministic seed management across Python, NumPy, and PyTorch.
"""

from __future__ import annotations

import random

import numpy as np
import pytest
from polaris.kernelone.benchmark.reproducibility.seed import (
    ReproducibleSeed,
    SeedContext,
    deterministic,
    hash_method_seed,
)


class TestReproducibleSeed:
    """Test suite for ReproducibleSeed class."""

    def test_set_global_seed_returns_context(self) -> None:
        """Verify set_global_seed returns proper context."""
        context = ReproducibleSeed.set_global_seed(123)

        assert isinstance(context, SeedContext)
        assert context.master_seed == 123
        assert context.python_random_state is not None
        assert "python" in context.deterministic_algorithms
        assert "numpy" in context.deterministic_algorithms

    def test_set_global_seed_affects_python_random(self) -> None:
        """Verify seed affects Python random."""
        # Set seed
        ReproducibleSeed.set_global_seed(42)
        value1 = random.random()

        # Reset and get same value
        ReproducibleSeed.set_global_seed(42)
        value2 = random.random()

        assert value1 == value2

    def test_set_global_seed_affects_numpy(self) -> None:
        """Verify seed affects NumPy."""
        ReproducibleSeed.set_global_seed(42)
        np_value1 = np.random.random()

        ReproducibleSeed.set_global_seed(42)
        np_value2 = np.random.random()

        assert np_value1 == np_value2

    def test_restore_returns_to_previous_state(self) -> None:
        """Verify restore returns to previous random state.

        The key behavior: set_global_seed captures the state BEFORE it changes it,
        so restore returns to exactly that state.

        This enables wrapping non-deterministic code:
        1. set_global_seed(999) captures S0, seeds with 999
        2. Do seeded operations (deterministic)
        3. restore() returns to S0 (the pre-seeding state)
        4. Continue from where you left off
        """
        # Reset to known state
        random.seed(0)
        state_at_zero = random.getstate()

        # Set new seed (captures current state, then seeds)
        context = ReproducibleSeed.set_global_seed(999)

        # Generate a value with the seed
        _seeded_value = random.random()

        # Restore to the state from before seeding
        ReproducibleSeed.restore(context)

        # The first random after restore should give us back state_at_zero
        # Since we restored to state_at_zero, the next random should be the same
        # as if we never called set_global_seed
        restored_value = random.random()
        random.setstate(state_at_zero)
        expected_value = random.random()

        assert restored_value == expected_value

    def test_get_master_seed_returns_current_seed(self) -> None:
        """Verify get_master_seed returns correct value."""
        ReproducibleSeed.set_global_seed(77)
        assert ReproducibleSeed.get_master_seed() == 77

        ReproducibleSeed.set_global_seed(88)
        assert ReproducibleSeed.get_master_seed() == 88

    def test_is_initialized_after_set(self) -> None:
        """Verify is_initialized reflects state after seed is set."""
        # Reset _initialized flag for this test (class-level state may persist)
        ReproducibleSeed._initialized = False
        assert not ReproducibleSeed.is_initialized()
        ReproducibleSeed.set_global_seed(42)
        assert ReproducibleSeed.is_initialized()


class TestHashMethodSeed:
    """Test suite for hash_method_seed function."""

    def test_same_inputs_produce_same_seed(self) -> None:
        """Verify deterministic output for same inputs."""
        seed1 = hash_method_seed("TestClass", "test_method", (1, 2, 3))
        seed2 = hash_method_seed("TestClass", "test_method", (1, 2, 3))

        assert seed1 == seed2

    def test_different_inputs_produce_different_seeds(self) -> None:
        """Verify different inputs produce different seeds."""
        seed1 = hash_method_seed("TestClass", "test_method", (1, 2, 3))
        seed2 = hash_method_seed("TestClass", "test_method", (1, 2, 4))

        assert seed1 != seed2

    def test_different_classes_produce_different_seeds(self) -> None:
        """Verify different classes produce different seeds."""
        seed1 = hash_method_seed("ClassA", "method", (1,))
        seed2 = hash_method_seed("ClassB", "method", (1,))

        assert seed1 != seed2

    def test_seed_within_valid_range(self) -> None:
        """Verify seed is within valid integer range."""
        seed = hash_method_seed("Class", "method", (1, 2, 3))

        assert isinstance(seed, int)
        assert 0 <= seed < 2**31

    def test_custom_base_seed(self) -> None:
        """Verify base_seed parameter affects output."""
        seed1 = hash_method_seed("Class", "method", (1,), base_seed=42)
        seed2 = hash_method_seed("Class", "method", (1,), base_seed=100)

        assert seed1 != seed2


class TestDeterministicDecorator:
    """Test suite for deterministic decorator."""

    def test_decorator_makes_method_deterministic(self) -> None:
        """Verify decorated method produces deterministic results."""

        class TestClass:
            @deterministic()
            def random_method(self) -> float:
                return random.random()

        obj = TestClass()
        result1 = obj.random_method()
        result2 = obj.random_method()

        assert result1 == result2

    def test_decorator_with_explicit_seed(self) -> None:
        """Verify explicit seed parameter works."""

        class TestClass:
            @deterministic(seed=12345)
            def random_method(self) -> float:
                return random.random()

        obj = TestClass()
        result1 = obj.random_method()
        result2 = obj.random_method()

        assert result1 == result2

    def test_decorator_preserves_return_value(self) -> None:
        """Verify decorator preserves method return value."""

        class TestClass:
            @deterministic()
            def add_method(self, a: int, b: int) -> int:
                return a + b

        obj = TestClass()
        assert obj.add_method(1, 2) == 3
        assert obj.add_method(5, 10) == 15

    def test_decorator_restores_state_after_exception(
        self,
    ) -> None:
        """Verify state is restored even when exception occurs."""
        # Set a known seed and capture the state
        context = ReproducibleSeed.set_global_seed(100)
        _seeded_value = random.random()
        ReproducibleSeed.restore(context)

        # Now at the original state, capture it again
        # The decorator will:
        # 1. Capture current state
        # 2. Seed with a deterministic value
        # 3. Run the method (which raises)
        # 4. Restore to captured state

        class TestClass:
            @deterministic()
            def failing_method(self) -> int:
                raise ValueError("Test exception")

        obj = TestClass()
        with pytest.raises(ValueError):
            obj.failing_method()

        # After decorator restores, random should be at the same point
        # Generate a value after restore
        value_after = random.random()
        # Generate same value again after restoring
        ReproducibleSeed.restore(context)
        value_after_restore = random.random()
        assert value_after == value_after_restore


class TestSeedContext:
    """Test suite for SeedContext dataclass."""

    def test_context_is_frozen(self) -> None:
        """Verify SeedContext is immutable."""
        context = ReproducibleSeed.set_global_seed(42)

        with pytest.raises(AttributeError):  # frozen dataclass cannot be modified
            context.master_seed = 999  # type: ignore

    def test_has_torch_property(self) -> None:
        """Verify has_torch property reflects torch availability."""
        context = ReproducibleSeed.set_global_seed(42)

        # Property should exist and be boolean
        assert isinstance(context.has_torch, bool)
