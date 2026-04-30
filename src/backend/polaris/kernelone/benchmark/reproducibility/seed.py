"""
Seed Injection System for Reproducible Benchmark Execution

Provides deterministic randomness control across Python, NumPy, and PyTorch.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    import torch

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


@dataclass(frozen=True)
class SeedContext:
    """Immutable seed context capturing all random states."""

    master_seed: int
    python_random_state: tuple[Any, ...]
    numpy_random_state: Any  # Can be BitGenerator, Generator, or legacy tuple
    torch_random_state: bytes | None = None
    deterministic_algorithms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_torch(self) -> bool:
        """Check if torch was available when context was captured."""
        return self.torch_random_state is not None


class ReproducibleSeed:
    """
    Global seed manager ensuring reproducibility across random number generators.

    Usage:
        # Set global seed
        context = ReproducibleSeed.set_global_seed(42)

        # ... run non-deterministic code ...

        # Restore previous state
        ReproducibleSeed.restore(context)
    """

    _master_seed: int = 42
    _initialized: bool = False

    @classmethod
    def set_global_seed(cls, seed: int) -> SeedContext:
        """
        Set global seed across all random number generators.

        Args:
            seed: The master seed value

        Returns:
            SeedContext with captured states for later restoration
        """
        cls._master_seed = seed

        # Capture Python random state
        py_state = random.getstate()

        # Set seeds for all frameworks
        random.seed(seed)
        np.random.seed(seed)

        deterministic_algos: list[str] = ["python", "numpy"]

        # Capture NumPy state
        numpy_state = np.random.get_state()

        # Handle PyTorch if available
        torch_state: bytes | None = None
        if _TORCH_AVAILABLE:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            # Enable deterministic algorithms
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            # Capture torch RNG state
            torch_state = torch.get_rng_state().cpu().numpy().tobytes()
            deterministic_algos.append("torch")

        cls._initialized = True

        return SeedContext(
            master_seed=seed,
            python_random_state=py_state,
            numpy_random_state=numpy_state,
            torch_random_state=torch_state,
            deterministic_algorithms=tuple(deterministic_algos),
        )

    @classmethod
    def restore(cls, context: SeedContext) -> None:
        """
        Restore previous random state from context.

        Args:
            context: SeedContext captured from set_global_seed
        """
        # Restore Python random state
        random.setstate(context.python_random_state)

        # Restore NumPy state
        np.random.set_state(context.numpy_random_state)

        # Restore PyTorch state if available
        if context.has_torch and _TORCH_AVAILABLE and context.torch_random_state:
            state_array = np.frombuffer(context.torch_random_state, dtype=np.uint64)
            state_tensor = torch.tensor(state_array, dtype=torch.uint64)
            torch.set_rng_state(state_tensor)

    @classmethod
    def get_master_seed(cls) -> int:
        """Get current master seed value."""
        return cls._master_seed

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if seed system has been initialized."""
        return cls._initialized


def hash_method_seed(
    class_name: str,
    method_name: str,
    args: tuple[Any, ...],
    base_seed: int = 42,
) -> int:
    """
    Generate deterministic seed for a method based on its identity.

    Creates a unique, reproducible seed for method-level determinism.

    Args:
        class_name: Name of the class
        method_name: Name of the method
        args: Method arguments (for additional entropy)
        base_seed: Base seed for hashing

    Returns:
        Deterministic integer seed
    """
    # Create hash input
    hash_input = f"{class_name}:{method_name}:{args!s}:{base_seed}"

    # Generate deterministic hash
    hash_bytes = hashlib.sha256(hash_input.encode("utf-8")).digest()
    hash_int = int.from_bytes(hash_bytes[:8], byteorder="big")

    # Map to reasonable seed range
    return hash_int % (2**31)


def deterministic(seed: int | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Method-level deterministic decorator.

    Automatically generates a reproducible seed based on method identity
    and sets/restores random states around method execution.

    Args:
        seed: Optional explicit seed. If None, generates from method identity.

    Usage:
        @deterministic()
        def my_random_method(self, x):
            return random.random() * x

        @deterministic(seed=123)
        def my_pinned_method(self, x):
            return random.random() * x
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Get class name from first arg if available
            class_name = args[0].__class__.__name__ if args else "global"

            # Generate or use provided seed
            if seed is not None:
                method_seed = seed
            else:
                method_seed = hash_method_seed(
                    class_name=class_name,
                    method_name=func.__name__,
                    args=args[1:],  # Exclude self
                )

            # Set seed and capture context
            context = ReproducibleSeed.set_global_seed(method_seed)
            try:
                return func(*args, **kwargs)
            finally:
                ReproducibleSeed.restore(context)

        # Preserve function metadata
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


# Convenience function (module-level API)
def set_global_seed(seed: int) -> SeedContext:
    """
    Convenience function to set global seed.

    Equivalent to ReproducibleSeed.set_global_seed but importable directly.

    Args:
        seed: The master seed value

    Returns:
        SeedContext with captured states for later restoration
    """
    return ReproducibleSeed.set_global_seed(seed)
