"""Focused tests for off-loop blocking file I/O in AkashicEpisodicMemory.

These tests assert that the file-scan fallbacks in ``get_turn`` and
``get_episode`` move their blocking JSONL reads off the event loop via
``asyncio.to_thread`` while still returning correct, fully parsed records.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.akashic.episodic_memory import AkashicEpisodicMemory


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def episodic(temp_dir: Path) -> AkashicEpisodicMemory:
    """Create an episodic memory instance backed by temp JSONL files."""
    return AkashicEpisodicMemory(
        workspace=str(temp_dir),
        turns_file=str(temp_dir / "turns.jsonl"),
        episodes_file=str(temp_dir / "episodes.jsonl"),
    )


def _spy_on_to_thread(monkeypatch: pytest.MonkeyPatch, recorded: list[Callable[..., Any]]) -> None:
    """Patch asyncio.to_thread to record dispatched callables, then delegate."""
    real_to_thread = asyncio.to_thread

    async def _spy(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        recorded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)


class TestGetTurnOffLoop:
    """get_turn file-scan fallback runs off the event loop."""

    @pytest.mark.asyncio
    async def test_get_turn_from_file_runs_off_loop(
        self, episodic: AkashicEpisodicMemory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache-miss turn lookup reads the file via asyncio.to_thread."""
        # Arrange: persist a turn, then evict it from the in-memory cache so the
        # file-scan fallback is exercised.
        await episodic.store_turn(7, [{"role": "user", "content": "hi"}])
        episodic._turns_cache.clear()
        calls: list[Callable[..., Any]] = []
        _spy_on_to_thread(monkeypatch, calls)

        # Act
        turn = await episodic.get_turn(7)

        # Assert: correct record returned AND blocking read went off-loop.
        assert turn is not None
        assert turn["turn_index"] == 7
        assert turn["messages"] == [{"role": "user", "content": "hi"}]
        assert episodic._read_turn_from_file in calls

    @pytest.mark.asyncio
    async def test_get_turn_missing_returns_none_off_loop(
        self, episodic: AkashicEpisodicMemory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing turn still dispatches the read off-loop and returns None."""
        # Arrange
        calls: list[Callable[..., Any]] = []
        _spy_on_to_thread(monkeypatch, calls)

        # Act
        turn = await episodic.get_turn(999)

        # Assert
        assert turn is None
        assert episodic._read_turn_from_file in calls


class TestGetEpisodeOffLoop:
    """get_episode file-scan fallback runs off the event loop."""

    @pytest.mark.asyncio
    async def test_get_episode_from_file_runs_off_loop(
        self, episodic: AkashicEpisodicMemory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache-miss episode lookup reads the file via asyncio.to_thread."""
        # Arrange: seal an episode, then evict it from cache.
        await episodic.store_turn(0, [{"role": "user", "content": "hello"}])
        episode_id = await episodic.seal_episode("session_a", "greeting")
        episodic._episodes_cache.clear()
        calls: list[Callable[..., Any]] = []
        _spy_on_to_thread(monkeypatch, calls)

        # Act
        episode = await episodic.get_episode(episode_id)

        # Assert
        assert episode is not None
        assert episode["episode_id"] == episode_id
        assert episode["session_id"] == "session_a"
        assert episode["summary"] == "greeting"
        assert episodic._read_episode_from_file in calls

    @pytest.mark.asyncio
    async def test_get_episode_missing_returns_none_off_loop(
        self, episodic: AkashicEpisodicMemory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing episode still dispatches the read off-loop and returns None."""
        # Arrange
        calls: list[Callable[..., Any]] = []
        _spy_on_to_thread(monkeypatch, calls)

        # Act
        episode = await episodic.get_episode("episode_missing")

        # Assert
        assert episode is None
        assert episodic._read_episode_from_file in calls
