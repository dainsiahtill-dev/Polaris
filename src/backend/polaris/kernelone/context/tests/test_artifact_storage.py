"""Tests for InMemoryArtifactStorage."""

from __future__ import annotations

import threading
import time

import pytest
from polaris.kernelone.context.context_os.ports import (
    ArtifactStoragePort,
    ArtifactStub,
    EvictionPolicy,
    StorageStats,
    StorageTier,
)
from polaris.kernelone.context.context_os.storage import InMemoryArtifactStorage


class TestInMemoryArtifactStorage:
    """Test suite for InMemoryArtifactStorage."""

    def test_store_and_retrieve(self) -> None:
        """Test basic store and retrieve operations."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Hello, World!",
            artifact_type="code",
            mime_type="text/plain",
            token_count=3,
            char_count=13,
            peek="Hello, World!",
            keys=("greeting",),
            source_event_ids=("event-1",),
            restore_tool="read_artifact",
        )

        result = storage.retrieve("test-1")
        assert result is not None
        assert result["artifact_id"] == "test-1"
        assert result["content"] == "Hello, World!"
        assert result["artifact_type"] == "code"
        assert result["mime_type"] == "text/plain"
        assert result["token_count"] == 3
        assert result["char_count"] == 13

    def test_retrieve_nonexistent(self) -> None:
        """Test retrieving a non-existent artifact returns None."""
        storage = InMemoryArtifactStorage()

        result = storage.retrieve("nonexistent")
        assert result is None

    def test_exists(self) -> None:
        """Test exists check."""
        storage = InMemoryArtifactStorage()

        assert storage.exists("test-1") is False

        storage.store(
            artifact_id="test-1",
            content="Content",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=7,
            peek="Content",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        assert storage.exists("test-1") is True
        assert storage.exists("nonexistent") is False

    def test_list_references(self) -> None:
        """Test listing artifact references."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Content 1",
            artifact_type="code",
            mime_type="text/plain",
            token_count=2,
            char_count=9,
            peek="Content 1",
            keys=("key1",),
            source_event_ids=("event-1",),
            restore_tool="read_artifact",
        )
        storage.store(
            artifact_id="test-2",
            content="Content 2",
            artifact_type="document",
            mime_type="text/markdown",
            token_count=2,
            char_count=9,
            peek="Content 2",
            keys=("key2",),
            source_event_ids=("event-2",),
            restore_tool="read_artifact",
        )

        refs = storage.list_references()
        assert len(refs) == 2
        artifact_ids = {r["artifact_id"] for r in refs}
        assert artifact_ids == {"test-1", "test-2"}

    def test_evict(self) -> None:
        """Test explicit artifact eviction."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Content",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=7,
            peek="Content",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        assert storage.exists("test-1") is True
        result = storage.evict("test-1")
        assert result is True
        assert storage.exists("test-1") is False

    def test_evict_nonexistent(self) -> None:
        """Test evicting a non-existent artifact returns False."""
        storage = InMemoryArtifactStorage()

        result = storage.evict("nonexistent")
        assert result is False

    def test_get_stats(self) -> None:
        """Test storage statistics."""
        storage = InMemoryArtifactStorage()

        stats = storage.get_stats()
        assert stats.total_artifacts == 0
        assert stats.total_bytes == 0
        assert stats.tier == StorageTier.MEMORY
        assert stats.eviction_policy == EvictionPolicy.LRU

        storage.store(
            artifact_id="test-1",
            content="Hello, World!",
            artifact_type="code",
            mime_type="text/plain",
            token_count=3,
            char_count=13,
            peek="Hello, World!",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        stats = storage.get_stats()
        assert stats.total_artifacts == 1
        assert stats.total_bytes > 0

    def test_lru_eviction_max_artifacts(self) -> None:
        """Test LRU eviction when max_artifacts is exceeded."""
        storage = InMemoryArtifactStorage(max_artifacts=3)

        for i in range(5):
            storage.store(
                artifact_id=f"test-{i}",
                content=f"Content {i}",
                artifact_type="code",
                mime_type="text/plain",
                token_count=1,
                char_count=9,
                peek=f"Content {i}",
                keys=(),
                source_event_ids=(),
                restore_tool="read_artifact",
            )

        # Should have evicted test-0 and test-1 (LRU)
        assert storage.exists("test-0") is False
        assert storage.exists("test-1") is False
        assert storage.exists("test-2") is True
        assert storage.exists("test-3") is True
        assert storage.exists("test-4") is True

        stats = storage.get_stats()
        assert stats.total_artifacts == 3
        assert stats.evictions >= 2

    def test_lru_eviction_access_order(self) -> None:
        """Test that accessing artifacts updates LRU order."""
        storage = InMemoryArtifactStorage(max_artifacts=3)

        for i in range(3):
            storage.store(
                artifact_id=f"test-{i}",
                content=f"Content {i}",
                artifact_type="code",
                mime_type="text/plain",
                token_count=1,
                char_count=9,
                peek=f"Content {i}",
                keys=(),
                source_event_ids=(),
                restore_tool="read_artifact",
            )

        # Access test-0 to update its LRU position
        storage.retrieve("test-0")

        # Add a new artifact, should evict test-1 (now LRU)
        storage.store(
            artifact_id="test-3",
            content="Content 3",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=9,
            peek="Content 3",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        assert storage.exists("test-0") is True  # Was accessed, so not evicted
        assert storage.exists("test-1") is False  # Was evicted

    def test_lru_eviction_max_size_bytes(self) -> None:
        """Test LRU eviction when max_size_bytes is exceeded."""
        storage = InMemoryArtifactStorage(max_artifacts=10, max_size_bytes=50)

        storage.store(
            artifact_id="test-1",
            content="A" * 30,  # 30 bytes
            artifact_type="code",
            mime_type="text/plain",
            token_count=7,
            char_count=30,
            peek="A" * 30,
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        storage.store(
            artifact_id="test-2",
            content="B" * 30,  # 30 bytes
            artifact_type="code",
            mime_type="text/plain",
            token_count=7,
            char_count=30,
            peek="B" * 30,
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        # Total would be 60 bytes, exceeds 50, so should evict test-1
        assert storage.exists("test-1") is False
        assert storage.exists("test-2") is True

    def test_hit_rate_tracking(self) -> None:
        """Test hit/miss rate tracking."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Content",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=7,
            peek="Content",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        # Miss
        storage.retrieve("nonexistent")
        # Hit
        storage.retrieve("test-1")
        # Miss
        storage.retrieve("test-1")

        stats = storage.get_stats()
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.hit_rate == pytest.approx(2 / 3, rel=0.01)

    def test_evict_if_needed(self) -> None:
        """Test explicit evict_if_needed call."""
        storage = InMemoryArtifactStorage(max_artifacts=2)

        evicted = storage.evict_if_needed()
        assert evicted == 0

        storage.store(
            artifact_id="test-1",
            content="Content 1",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=9,
            peek="Content 1",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        evicted = storage.evict_if_needed()
        assert evicted == 0

    def test_migrate_to_tier_returns_zero(self) -> None:
        """Test that migrate_to_tier returns 0 for in-memory storage."""
        storage = InMemoryArtifactStorage()

        count = storage.migrate_to_tier(StorageTier.FILE)
        assert count == 0

        count = storage.migrate_to_tier(StorageTier.S3)
        assert count == 0

    def test_thread_safety(self) -> None:
        """Test thread-safe operations."""
        storage = InMemoryArtifactStorage()
        num_threads = 10
        artifacts_per_thread = 20

        def store_artifacts(thread_id: int) -> None:
            for i in range(artifacts_per_thread):
                storage.store(
                    artifact_id=f"thread-{thread_id}-art-{i}",
                    content=f"Content from thread {thread_id}, artifact {i}",
                    artifact_type="code",
                    mime_type="text/plain",
                    token_count=5,
                    char_count=40,
                    peek=f"Content {i}",
                    keys=(),
                    source_event_ids=(),
                    restore_tool="read_artifact",
                )

        threads = [threading.Thread(target=store_artifacts, args=(i,)) for i in range(num_threads)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All artifacts should be stored without errors
        refs = storage.list_references()
        assert len(refs) > 0
        assert len(refs) <= num_threads * artifacts_per_thread

    def test_concurrent_read_write(self) -> None:
        """Test concurrent read and write operations."""
        storage = InMemoryArtifactStorage()
        num_writers = 5
        num_readers = 5
        writes_per_writer = 10
        reads_per_reader = 20

        # Pre-populate some artifacts
        for i in range(10):
            storage.store(
                artifact_id=f"initial-{i}",
                content=f"Initial content {i}",
                artifact_type="code",
                mime_type="text/plain",
                token_count=3,
                char_count=20,
                peek=f"Initial {i}",
                keys=(),
                source_event_ids=(),
                restore_tool="read_artifact",
            )

        write_done = threading.Event()
        errors: list[str] = []

        def writer(writer_id: int) -> None:
            try:
                for i in range(writes_per_writer):
                    storage.store(
                        artifact_id=f"writer-{writer_id}-art-{i}",
                        content=f"Content from writer {writer_id}, artifact {i}",
                        artifact_type="code",
                        mime_type="text/plain",
                        token_count=3,
                        char_count=40,
                        peek=f"Content {i}",
                        keys=(),
                        source_event_ids=(),
                        restore_tool="read_artifact",
                    )
            except (RuntimeError, ValueError) as e:
                errors.append(f"Writer {writer_id} error: {e}")
            finally:
                if writer_id == 0:
                    write_done.set()

        def reader(reader_id: int) -> None:
            try:
                for i in range(reads_per_reader):
                    storage.retrieve(f"initial-{i % 10}")
                    time.sleep(0.001)
            except (RuntimeError, ValueError) as e:
                errors.append(f"Reader {reader_id} error: {e}")

        threads: list[threading.Thread] = []
        for i in range(num_writers):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for i in range(num_readers):
            t = threading.Thread(target=reader, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        # Verify storage is in a consistent state
        stats = storage.get_stats()
        assert stats.total_artifacts > 0

    def test_update_existing_artifact(self) -> None:
        """Test updating an existing artifact."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Original content",
            artifact_type="code",
            mime_type="text/plain",
            token_count=3,
            char_count=17,
            peek="Original",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        storage.store(
            artifact_id="test-1",
            content="Updated content",
            artifact_type="document",
            mime_type="text/markdown",
            token_count=3,
            char_count=17,
            peek="Updated",
            keys=("updated",),
            source_event_ids=("event-1",),
            restore_tool="read_artifact",
        )

        result = storage.retrieve("test-1")
        assert result is not None
        assert result["content"] == "Updated content"
        assert result["artifact_type"] == "document"
        assert result["keys"] == ("updated",)

        # Should only have one artifact
        refs = storage.list_references()
        assert len(refs) == 1

    def test_artifact_stub_to_dict(self) -> None:
        """Test ArtifactStub.to_dict conversion."""
        stub = ArtifactStub(
            artifact_id="test-1",
            artifact_type="code",
            mime_type="text/plain",
            token_count=10,
            char_count=50,
            peek="Sample code",
            keys=("python", "function"),
            restore_tool="read_artifact",
            tier=StorageTier.MEMORY,
            metadata={"version": 1},
        )

        d = stub.to_dict()
        assert d["artifact_id"] == "test-1"
        assert d["artifact_type"] == "code"
        assert d["mime_type"] == "text/plain"
        assert d["token_count"] == 10
        assert d["char_count"] == 50
        assert d["peek"] == "Sample code"
        assert d["keys"] == ["python", "function"]
        assert d["restore_tool"] == "read_artifact"
        assert d["tier"] == StorageTier.MEMORY
        assert d["metadata"] == {"version": 1}

    def test_storage_stats_hit_rate(self) -> None:
        """Test StorageStats.hit_rate calculation."""
        stats = StorageStats(hits=80, misses=20, total_artifacts=10, total_bytes=1000)
        assert stats.hit_rate == 0.8

        empty_stats = StorageStats()
        assert empty_stats.hit_rate == 0.0

    def test_artifacts_port_protocol(self) -> None:
        """Test that InMemoryArtifactStorage satisfies ArtifactStoragePort Protocol."""
        storage: ArtifactStoragePort = InMemoryArtifactStorage()
        assert isinstance(storage, InMemoryArtifactStorage)

    def test_large_content_storage(self) -> None:
        """Test storing large content."""
        storage = InMemoryArtifactStorage(max_size_bytes=1000)

        large_content = "X" * 500

        storage.store(
            artifact_id="large-1",
            content=large_content,
            artifact_type="code",
            mime_type="text/plain",
            token_count=125,
            char_count=500,
            peek=large_content[:50],
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        result = storage.retrieve("large-1")
        assert result is not None
        assert len(result["content"]) == 500

    def test_empty_keys_and_source_event_ids(self) -> None:
        """Test storing artifacts with empty keys and source_event_ids."""
        storage = InMemoryArtifactStorage()

        storage.store(
            artifact_id="test-1",
            content="Content",
            artifact_type="code",
            mime_type="text/plain",
            token_count=1,
            char_count=7,
            peek="Content",
            keys=(),
            source_event_ids=(),
            restore_tool="read_artifact",
        )

        result = storage.retrieve("test-1")
        assert result is not None
        assert result["keys"] == ()
        assert result["source_event_ids"] == ()


class TestArtifactStoragePort:
    """Test the ArtifactStoragePort Protocol conformance."""

    def test_protocol_methods_exist(self) -> None:
        """Verify ArtifactStoragePort Protocol defines required methods."""
        required_methods = [
            "store",
            "retrieve",
            "list_references",
            "exists",
            "evict",
            "get_stats",
            "evict_if_needed",
            "migrate_to_tier",
        ]

        for method in required_methods:
            assert hasattr(ArtifactStoragePort, method), f"Missing method: {method}"
