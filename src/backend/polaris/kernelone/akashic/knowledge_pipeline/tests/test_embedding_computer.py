"""Tests for EmbeddingComputer."""

from __future__ import annotations

import hashlib

import pytest
from polaris.kernelone.akashic.knowledge_pipeline.embedding_computer import (
    EmbeddingComputer,
)


class MockEmbeddingPort:
    """Mock embedding port for testing."""

    def __init__(self, dims: int = 384) -> None:
        self._dims = dims
        self._call_count = 0

    def get_embedding(self, text: str, *, model: str = "nomic-embed-text") -> list[float]:
        """Return a deterministic fake embedding based on text hash."""
        self._call_count += 1
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Produce a deterministic fake vector
        vec: list[float] = [b / 255.0 for b in h]
        # Pad or trim to target dimension
        vec = vec + [0.0] * (self._dims - len(vec)) if len(vec) < self._dims else vec[: self._dims]
        return vec

    async def get_embedding_async(self, text: str, *, model: str = "nomic-embed-text") -> list[float]:
        """Async version."""
        return self.get_embedding(text, model=model)


class MockAsyncEmbeddingPort:
    """Mock async embedding port for testing."""

    def __init__(self, dims: int = 384) -> None:
        self._dims = dims
        self._call_count = 0

    async def get_embedding(self, text: str, *, model: str = "nomic-embed-text") -> list[float]:
        """Return a deterministic fake embedding."""
        self._call_count += 1
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec: list[float] = [b / 255.0 for b in h]
        vec = vec + [0.0] * (self._dims - len(vec)) if len(vec) < self._dims else vec[: self._dims]
        return vec


class TestEmbeddingComputer:
    """Tests for EmbeddingComputer."""

    @pytest.fixture
    def sync_port(self):
        """Create a sync mock embedding port."""
        return MockEmbeddingPort(dims=384)

    @pytest.fixture
    def async_port(self):
        """Create an async mock embedding port."""
        return MockAsyncEmbeddingPort(dims=384)

    @pytest.fixture
    def computer(self, sync_port):
        """Create an EmbeddingComputer for testing."""
        return EmbeddingComputer(
            embedding_port=sync_port,
            max_batch_size=8,
            max_concurrency=4,
            cache_size=16,
        )

    @pytest.mark.asyncio
    async def test_compute_batch_returns_correct_count(self, computer) -> None:
        """compute_batch returns one embedding per input text."""
        texts = ["hello world", "foo bar", "baz qux"]
        results = await computer.compute_batch(texts)

        assert len(results) == 3
        assert all(isinstance(r, list) for r in results)
        assert all(len(r) == 384 for r in results)

    @pytest.mark.asyncio
    async def test_compute_batch_empty_input(self, computer) -> None:
        """Empty input returns empty list."""
        results = await computer.compute_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_compute_single(self, computer) -> None:
        """compute_single returns single embedding."""
        result = await computer.compute_single("hello")
        assert isinstance(result, list)
        assert len(result) == 384

    @pytest.mark.asyncio
    async def test_cache_hits_on_repeat(self, computer, sync_port) -> None:
        """Same text returns cached embedding without calling port."""
        text = "cached text"
        # First call
        await computer.compute_batch([text])
        first_call_count = sync_port._call_count

        # Second call - should hit cache
        await computer.compute_batch([text])
        second_call_count = sync_port._call_count

        assert first_call_count == 1
        assert second_call_count == 1  # No new call

    @pytest.mark.asyncio
    async def test_different_texts_call_port(self, computer, sync_port) -> None:
        """Different texts each call the embedding port."""
        texts = ["text one", "text two", "text three"]
        await computer.compute_batch(texts)
        assert sync_port._call_count == 3

    @pytest.mark.asyncio
    async def test_get_stats(self, computer) -> None:
        """get_stats returns expected keys."""
        stats = computer.get_stats()
        assert "model" in stats
        assert "cache_size" in stats
        assert "cache_capacity" in stats
        assert stats["model"] == "nomic-embed-text"

    def test_text_hash_deterministic(self, computer) -> None:
        """_text_hash produces same hash for same text."""
        h1 = computer._text_hash("hello world")
        h2 = computer._text_hash("hello world")
        assert h1 == h2

    def test_text_hash_different_for_different_text(self, computer) -> None:
        """_text_hash produces different hash for different texts."""
        h1 = computer._text_hash("hello")
        h2 = computer._text_hash("world")
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_zero_embedding_fallback(self, computer) -> None:
        """Zero embedding returned on failure."""

        # Computer with a port that always raises
        class FailingPort:
            def get_embedding(self, text: str, *, model: str = "nomic-embed-text") -> list[float]:
                raise RuntimeError("Embedding failed")

        failing_computer = EmbeddingComputer(
            embedding_port=FailingPort(),  # type: ignore[arg-type]
            max_batch_size=8,
        )

        texts = ["test"]
        results = await failing_computer.compute_batch(texts)
        assert len(results) == 1
        assert results[0] == [0.0] * 384


class TestEmbeddingComputerAsync:
    """Tests for EmbeddingComputer with async port."""

    @pytest.fixture
    def async_port(self):
        return MockAsyncEmbeddingPort(dims=384)

    @pytest.fixture
    def computer(self, async_port):
        return EmbeddingComputer(
            embedding_port=async_port,
            max_batch_size=8,
        )

    @pytest.mark.asyncio
    async def test_async_port_works(self, computer) -> None:
        """Async embedding port is correctly awaited."""
        texts = ["async test"]
        results = await computer.compute_batch(texts)
        assert len(results) == 1
        assert len(results[0]) == 384
