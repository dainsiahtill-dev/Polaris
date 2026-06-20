"""Focused test for off-loop blocking file I/O in AkashicSemanticMemory.

Verifies that ``_compact_jsonl`` (triggered by ``delete``) moves its blocking
lock acquisition and JSONL rewrite off the event loop via ``asyncio.to_thread``
while still correctly pruning deleted items from the on-disk file.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def semantic(temp_dir: Path) -> AkashicSemanticMemory:
    """Create a semantic memory instance with vector search disabled."""
    return AkashicSemanticMemory(
        workspace=str(temp_dir),
        memory_file=str(temp_dir / "memory.jsonl"),
        enable_vector_search=False,
    )


class TestCompactJsonlOffLoop:
    """_compact_jsonl runs its blocking I/O off the event loop."""

    @pytest.mark.asyncio
    async def test_compact_jsonl_runs_off_loop_and_prunes(
        self,
        temp_dir: Path,
        semantic: AkashicSemanticMemory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """delete() compacts via asyncio.to_thread and removes the row from disk."""
        # Arrange: two items persisted to JSONL; spy on asyncio.to_thread.
        keep_id = await semantic.add("keep me", importance=5)
        drop_id = await semantic.add("drop me", importance=5)
        recorded: list[Callable[..., Any]] = []
        real_to_thread = asyncio.to_thread

        async def _spy(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
            recorded.append(func)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _spy)

        # Act
        deleted = await semantic.delete(drop_id)

        # Assert: deletion succeeded, compaction went off-loop, file pruned.
        assert deleted is True
        assert semantic._compact_jsonl_blocking in recorded

        memory_file = temp_dir / "memory.jsonl"
        with open(memory_file, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        remaining_ids = {row["memory_id"] for row in rows}
        assert remaining_ids == {keep_id}
        assert drop_id not in remaining_ids

    @pytest.mark.asyncio
    async def test_compact_jsonl_noop_when_nothing_deleted(
        self, semantic: AkashicSemanticMemory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no pending deletions, _compact_jsonl skips the off-loop dispatch."""
        # Arrange
        recorded: list[Callable[..., Any]] = []
        real_to_thread = asyncio.to_thread

        async def _spy(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
            recorded.append(func)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _spy)

        # Act
        await semantic._compact_jsonl()

        # Assert: early return, no thread dispatch.
        assert recorded == []
