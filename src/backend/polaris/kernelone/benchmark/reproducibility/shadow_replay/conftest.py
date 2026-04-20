"""Pytest integration for Chronos Mirror (ShadowReplay).

Provides fixtures and markers for reproducible HTTP recording/replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.benchmark.reproducibility.shadow_replay import (
    Cassette,
    ShadowReplay,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.player import (
    ShadowPlayer,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.recorder import (
    ShadowRecorder,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def shadow_replay_dir(tmp_path: Path) -> Path:
    """Temporary directory for shadow replay cassettes.

    Usage:
        def test_something(shadow_replay_dir):
            cassette = Cassette(cassette_id="test", cassette_dir=shadow_replay_dir)
    """
    cassette_dir = tmp_path / "shadow_replay"
    cassette_dir.mkdir(parents=True, exist_ok=True)
    return cassette_dir


@pytest.fixture
def shadow_cassette(shadow_replay_dir: Path) -> Cassette:
    """Provide a Cassette instance for testing.

    Usage:
        def test_cassette(shadow_cassette):
            cassette.add_entry(request, response)
            assert cassette.exists()
    """
    return Cassette(
        cassette_id="test-cassette",
        cassette_dir=shadow_replay_dir,
        mode="both",
    )


@pytest.fixture
async def shadow_replay_fixture(
    shadow_replay_dir: Path,
) -> AsyncGenerator[ShadowReplay, None]:
    """Provide a ShadowReplay instance for async tests.

    Usage:
        async def test_http_intercept(shadow_replay_fixture):
            async with shadow_replay_fixture as replay:
                # HTTP calls are intercepted
                result = await httpx.AsyncClient().post(url, json=data)
    """
    replay = ShadowReplay(
        cassette_id="pytest-replay",
        mode="both",
        cassette_dir=shadow_replay_dir,
    )
    async with replay:
        yield replay


@pytest.fixture
def shadow_recorder(shadow_cassette: Cassette) -> ShadowRecorder:
    """Provide a ShadowRecorder instance.

    Usage:
        def test_recorder(shadow_recorder):
            # Attach to patch and start recording
    """
    return ShadowRecorder(cassette=shadow_cassette, auto_save=False)


@pytest.fixture
def shadow_player(shadow_cassette: Cassette) -> ShadowPlayer:
    """Provide a ShadowPlayer instance.

    Usage:
        def test_player(shadow_player):
            response = await shadow_player.intercept(exchange)
    """
    return ShadowPlayer(cassette=shadow_cassette, strict=True)


# ============================================================================
# Markers
# ============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Register shadow replay markers."""
    config.addinivalue_line(
        "markers",
        "shadow_replay: mark test as using ShadowReplay HTTP interception",
    )
    config.addinivalue_line(
        "markers",
        "record_mode: mark test as running in record mode",
    )
    config.addinivalue_line(
        "markers",
        "replay_mode: mark test as running in replay mode",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Add shadow_replay markers to tests using shadow_replay_fixture."""
    for item in items:
        if "shadow_replay_fixture" in getattr(item, "fixturenames", []):
            item.add_marker(pytest.mark.shadow_replay)


# ============================================================================
# Re-export for convenience
# ============================================================================

__all__ = [
    "shadow_cassette",
    "shadow_player",
    "shadow_recorder",
    "shadow_replay_dir",
    "shadow_replay_fixture",
]
