"""
Global pytest configuration for reproducibility fixtures.

This conftest.py provides project-wide fixtures and configuration
for reproducible benchmark testing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure polaris packages are importable
polaris_root = Path(__file__).parent.parent.parent.parent.parent
if str(polaris_root) not in sys.path:
    sys.path.insert(0, str(polaris_root))

# Import fixtures module to register fixtures
from polaris.kernelone.benchmark.reproducibility import fixtures  # noqa: E402

# Re-export fixtures from conftest for pytest discovery
reproducible_seed_fixture = fixtures.reproducible_seed_fixture
seeded_random = fixtures.seeded_random
param_seed = fixtures.param_seed
cache_replay_fixture = fixtures.cache_replay_fixture
replay_only_cache = fixtures.replay_only_cache
record_only_cache = fixtures.record_only_cache
mock_provider_fixture = fixtures.mock_provider_fixture
mock_provider_builder = fixtures.mock_provider_builder
reproducible_benchmark_setup = fixtures.reproducible_benchmark_setup
default_seed = fixtures.default_seed
default_mock_responses = fixtures.default_mock_responses


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with reproducibility markers."""
    config.addinivalue_line(
        "markers",
        "reproducible: mark test as requiring reproducibility guarantees",
    )
    config.addinivalue_line(
        "markers",
        "seeded: mark test as using seeded random values",
    )
    config.addinivalue_line(
        "markers",
        "vcr: mark test as using VCR cache replay",
    )
    config.addinivalue_line(
        "markers",
        "mock_llm: mark test as using LLM mocks",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """
    Modify test collection to add reproducibility markers.

    Tests in reproducibility directory are automatically marked.
    """
    for item in items:
        # Check if test file is in reproducibility directory
        if "reproducibility" in str(item.fspath):
            item.add_marker(pytest.mark.reproducible)

        # Check for fixture usage
        if "reproducible_seed_fixture" in getattr(item, "fixturenames", []):
            item.add_marker(pytest.mark.seeded)

        if "cache_replay_fixture" in getattr(item, "fixturenames", []):
            item.add_marker(pytest.mark.vcr)

        if "mock_provider_fixture" in getattr(item, "fixturenames", []):
            item.add_marker(pytest.mark.mock_llm)


# Note: pytest-asyncio should be configured in the top-level conftest.py
# Nested conftest files cannot use pytest_plugins (deprecated since pytest 2.8)
