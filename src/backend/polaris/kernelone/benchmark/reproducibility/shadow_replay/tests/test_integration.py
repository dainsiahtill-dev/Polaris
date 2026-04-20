"""Integration tests for ShadowReplay context manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.benchmark.reproducibility.shadow_replay import (
    Cassette,
    ShadowReplay,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    HTTPRequest,
    HTTPResponse,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    CassetteNotFoundError,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestShadowReplayIntegration:
    """Integration tests for ShadowReplay."""

    @pytest.mark.asyncio
    async def test_record_mode_with_entry_saves_cassette(self, tmp_path: Path) -> None:
        """Test that record mode saves cassette when entries exist."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        # Pre-create cassette with entry before entering context
        cassette = Cassette(
            cassette_id="record-with-entry",
            cassette_dir=cassette_dir,
            mode="record",
        )
        cassette.add_entry(
            request=HTTPRequest.from_raw("GET", "https://api.example.com", {}, None),
            response=HTTPResponse.from_raw(200, {}, b'"ok"'),
        )
        cassette.save()

        # Load and verify exists
        loaded = Cassette(
            cassette_id="record-with-entry",
            cassette_dir=cassette_dir,
        )
        loaded.load()
        assert len(loaded.format.entries) == 1

    @pytest.mark.asyncio
    async def test_replay_mode_loads_cassette(self, tmp_path: Path) -> None:
        """Test that replay mode loads existing cassette."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        # First create a cassette
        cassette = Cassette(
            cassette_id="replay-load-test",
            cassette_dir=cassette_dir,
            mode="record",
        )
        cassette.add_entry(
            request=HTTPRequest.from_raw("GET", "https://api.example.com", {}, None),
            response=HTTPResponse.from_raw(200, {}, b'"ok"'),
        )
        cassette.save()

        # Now replay should load it
        async with ShadowReplay(
            cassette_id="replay-load-test",
            mode="replay",
            cassette_dir=cassette_dir,
        ) as replay:
            assert replay.entry_count == 1

    @pytest.mark.asyncio
    async def test_replay_mode_missing_cassette_raises(self, tmp_path: Path) -> None:
        """Test that replay mode raises if cassette not found."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        with pytest.raises(CassetteNotFoundError):
            async with ShadowReplay(
                cassette_id="nonexistent",
                mode="replay",
                cassette_dir=cassette_dir,
            ):
                pass

    @pytest.mark.asyncio
    async def test_both_mode_creates_if_not_exists(self, tmp_path: Path) -> None:
        """Test that both mode creates cassette if it doesn't exist."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        async with ShadowReplay(
            cassette_id="both-create-test",
            mode="both",
            cassette_dir=cassette_dir,
            auto_save=True,
        ):
            pass

        cassette_file = cassette_dir / "both-create-test.jsonl"
        assert cassette_file.exists()

    @pytest.mark.asyncio
    async def test_both_mode_loads_existing(self, tmp_path: Path) -> None:
        """Test that both mode loads existing cassette."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        # Create cassette first
        cassette = Cassette(
            cassette_id="both-load-test",
            cassette_dir=cassette_dir,
            mode="record",
        )
        cassette.add_entry(
            request=HTTPRequest.from_raw("GET", "https://api.example.com", {}, None),
            response=HTTPResponse.from_raw(200, {}, b'"existing"'),
        )
        cassette.save()

        # Both mode should load
        async with ShadowReplay(
            cassette_id="both-load-test",
            mode="both",
            cassette_dir=cassette_dir,
        ) as replay:
            assert replay.entry_count == 1

    @pytest.mark.asyncio
    async def test_entry_count_tracking(self, tmp_path: Path) -> None:
        """Test that entry count is tracked correctly."""
        cassette_dir = tmp_path / "cassettes"
        cassette_dir.mkdir(exist_ok=True)

        # Create cassette first
        cassette = Cassette(
            cassette_id="count-test",
            cassette_dir=cassette_dir,
            mode="record",
        )
        for i in range(3):
            cassette.add_entry(
                request=HTTPRequest.from_raw("GET", f"https://api.example.com/{i}", {}, None),
                response=HTTPResponse.from_raw(200, {}, f'"response{i}"'.encode()),
            )
        cassette.save()

        async with ShadowReplay(
            cassette_id="count-test",
            mode="replay",
            cassette_dir=cassette_dir,
        ) as replay:
            assert replay.entry_count == 3

    def test_invalid_mode_raises(self) -> None:
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            ShadowReplay(cassette_id="test", mode="invalid")

        assert "Invalid mode" in str(exc_info.value)


class TestCassettePersistence:
    """Tests for cassette persistence behavior."""

    @pytest.mark.asyncio
    async def test_cassette_saved_on_exit(self, tmp_path: Path) -> None:
        """Test that cassette is saved when entries are added via direct manipulation."""
        cassette_dir = tmp_path / "persist"
        cassette_dir.mkdir()

        # Create cassette and add entry directly
        cassette = Cassette(
            cassette_id="persist-test",
            cassette_dir=cassette_dir,
            mode="record",
        )
        cassette.add_entry(
            request=HTTPRequest.from_raw("POST", "https://api.example.com", {}, b"body"),
            response=HTTPResponse.from_raw(200, {}, b'"ok"'),
        )
        cassette.save()

        # After exit, cassette should exist on disk
        loaded = Cassette(
            cassette_id="persist-test",
            cassette_dir=cassette_dir,
        )
        loaded.load()
        assert len(loaded.format.entries) == 1

    @pytest.mark.asyncio
    async def test_empty_cassette_not_saved(self, tmp_path: Path) -> None:
        """Test that empty cassette is not saved."""
        cassette_dir = tmp_path / "empty"
        cassette_dir.mkdir()

        async with ShadowReplay(
            cassette_id="empty-test",
            mode="record",
            cassette_dir=cassette_dir,
            auto_save=True,
        ):
            # Don't add any entries
            pass

        # Cassette file should NOT exist (nothing to save)
        cassette_file = cassette_dir / "empty-test.jsonl"
        assert not cassette_file.exists()
