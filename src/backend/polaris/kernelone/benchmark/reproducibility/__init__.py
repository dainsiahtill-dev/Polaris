"""
Polaris Benchmark Reproducibility Framework

Provides deterministic execution guarantees for benchmark tests:
- Seed injection system for reproducible randomness
- VCR-based cache replay for LLM calls
- Standardized mock strategies
- Pytest fixtures for common test scenarios
"""

from polaris.kernelone.benchmark.reproducibility.fixtures import (
    cache_replay_fixture,
    mock_provider_fixture,
    reproducible_seed_fixture,
)
from polaris.kernelone.benchmark.reproducibility.mocks import (
    DeterministicMockProvider,
    MockLLMResponse,
)
from polaris.kernelone.benchmark.reproducibility.seed import (
    ReproducibleSeed,
    SeedContext,
    deterministic,
    hash_method_seed,
)
from polaris.kernelone.benchmark.reproducibility.vcr import (
    CacheReplay,
    Recording,
)

__all__ = [
    "CacheReplay",
    "DeterministicMockProvider",
    # Mock strategies
    "MockLLMResponse",
    # VCR / Cache replay
    "Recording",
    "ReproducibleSeed",
    # Seed management
    "SeedContext",
    "cache_replay_fixture",
    "deterministic",
    "hash_method_seed",
    "mock_provider_fixture",
    # Fixtures
    "reproducible_seed_fixture",
]

__version__ = "1.0.0"
