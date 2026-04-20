"""In-memory artifact storage implementation for State-First Context OS.

This module provides the InMemoryArtifactStorage class that implements
the ArtifactStoragePort protocol using in-memory storage with LRU eviction.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from .ports import (
    ArtifactStub,
    EvictionPolicy,
    StorageStats,
    StorageTier,
)


class InMemoryArtifactStorage:
    """In-memory artifact storage with LRU eviction.

    This implementation provides thread-safe in-memory storage for artifacts
    with automatic eviction based on max_artifacts or max_size_bytes limits.

    Args:
        max_artifacts: Maximum number of artifacts to store (default: 100).
        max_size_bytes: Maximum total size in bytes (default: 10MB).
        eviction_policy: Eviction policy to use (default: LRU).
    """

    def __init__(
        self,
        max_artifacts: int = 100,
        max_size_bytes: int = 10 * 1024 * 1024,  # 10MB
        eviction_policy: EvictionPolicy = EvictionPolicy.LRU,
    ) -> None:
        self._artifacts: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_artifacts = max_artifacts
        self._max_size_bytes = max_size_bytes
        self._current_size_bytes = 0
        self._eviction_policy = eviction_policy
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def store(
        self,
        artifact_id: str,
        content: str,
        artifact_type: str,
        mime_type: str,
        token_count: int,
        char_count: int,
        peek: str,
        keys: tuple[str, ...],
        source_event_ids: tuple[str, ...],
        restore_tool: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store an artifact in memory.

        Args:
            artifact_id: Unique identifier for the artifact.
            content: Full content of the artifact.
            artifact_type: Type of artifact (e.g., "code", "document").
            mime_type: MIME type of the content.
            token_count: Estimated token count.
            char_count: Character count of the content.
            peek: Short preview/snippet of the content.
            keys: Searchable keywords associated with the artifact.
            source_event_ids: Event IDs that generated this artifact.
            restore_tool: Tool name to use for restoring full content.
            metadata: Optional additional metadata.
        """
        with self._lock:
            content_bytes = len(content.encode("utf-8"))

            # Evict if needed before storing
            self._evict_if_needed_locked(content_bytes)

            # If artifact already exists, remove its size first
            if artifact_id in self._artifacts:
                old_artifact = self._artifacts[artifact_id]
                self._current_size_bytes -= len(old_artifact["content"].encode("utf-8"))
                del self._artifacts[artifact_id]

            # Store the artifact
            artifact = {
                "artifact_id": artifact_id,
                "content": content,
                "artifact_type": artifact_type,
                "mime_type": mime_type,
                "token_count": token_count,
                "char_count": char_count,
                "peek": peek,
                "keys": keys,
                "source_event_ids": source_event_ids,
                "restore_tool": restore_tool,
                "metadata": metadata or {},
            }
            self._artifacts[artifact_id] = artifact
            self._current_size_bytes += content_bytes

    def retrieve(self, artifact_id: str) -> dict[str, Any] | None:
        """Retrieve an artifact by ID.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            Dictionary representation of the artifact, or None if not found.
        """
        with self._lock:
            if artifact_id not in self._artifacts:
                self._misses += 1
                return None
            # Move to end for LRU
            self._artifacts.move_to_end(artifact_id)
            self._hits += 1
            artifact = self._artifacts[artifact_id]
            return {
                "artifact_id": artifact["artifact_id"],
                "artifact_type": artifact["artifact_type"],
                "mime_type": artifact["mime_type"],
                "token_count": artifact["token_count"],
                "char_count": artifact["char_count"],
                "peek": artifact["peek"],
                "keys": artifact["keys"],
                "content": artifact["content"],
                "source_event_ids": artifact["source_event_ids"],
                "restore_tool": artifact["restore_tool"],
                "metadata": artifact["metadata"],
            }

    def list_references(self) -> list[dict[str, Any]]:
        """List all artifact references.

        Returns:
            List of artifact reference dictionaries (stubs).
        """
        with self._lock:
            return [self._to_stub(artifact).to_dict() for artifact in self._artifacts.values()]

    def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            True if the artifact exists, False otherwise.
        """
        with self._lock:
            return artifact_id in self._artifacts

    def evict(self, artifact_id: str) -> bool:
        """Evict an artifact from storage.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            True if the artifact was evicted, False if not found.
        """
        with self._lock:
            if artifact_id not in self._artifacts:
                return False
            artifact = self._artifacts.pop(artifact_id)
            self._current_size_bytes -= len(artifact["content"].encode("utf-8"))
            self._evictions += 1
            return True

    def get_stats(self) -> StorageStats:
        """Get storage statistics.

        Returns:
            StorageStats object with current storage metrics.
        """
        with self._lock:
            return StorageStats(
                total_artifacts=len(self._artifacts),
                total_bytes=self._current_size_bytes,
                tier=StorageTier.MEMORY,
                eviction_policy=self._eviction_policy,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
            )

    def evict_if_needed(self) -> int:
        """Evict artifacts if storage limits are exceeded.

        Returns:
            Number of artifacts evicted.
        """
        with self._lock:
            return self._evict_if_needed_locked(0)

    def migrate_to_tier(self, target_tier: StorageTier) -> int:
        """Migrate artifacts to a different storage tier.

        Note: In-memory storage cannot migrate to other tiers.
        This is a no-op for MEMORY tier.

        Args:
            target_tier: The target storage tier.

        Returns:
            Number of artifacts migrated (0 for in-memory storage).
        """
        # In-memory storage cannot migrate to other tiers
        return 0

    def _evict_if_needed_locked(self, additional_bytes: int = 0) -> int:
        """Evict artifacts if storage limits are exceeded.

        Must be called with lock held.

        Args:
            additional_bytes: Additional bytes that will be added after eviction.

        Returns:
            Number of artifacts evicted.
        """
        evicted = 0

        while len(self._artifacts) >= self._max_artifacts:
            self._artifacts.popitem(last=False)
            self._evictions += 1
            evicted += 1

        while self._current_size_bytes + additional_bytes > self._max_size_bytes and self._artifacts:
            _artifact_id, artifact = self._artifacts.popitem(last=False)
            self._current_size_bytes -= len(artifact["content"].encode("utf-8"))
            self._evictions += 1
            evicted += 1

        return evicted

    def _to_stub(self, artifact: dict[str, Any]) -> ArtifactStub:
        """Convert an artifact dict to an ArtifactStub.

        Args:
            artifact: Artifact dictionary.

        Returns:
            ArtifactStub instance.
        """
        return ArtifactStub(
            artifact_id=artifact["artifact_id"],
            artifact_type=artifact["artifact_type"],
            mime_type=artifact["mime_type"],
            token_count=artifact["token_count"],
            char_count=artifact["char_count"],
            peek=artifact["peek"],
            keys=artifact["keys"],
            restore_tool=artifact["restore_tool"],
            tier=StorageTier.MEMORY,
            metadata=artifact["metadata"],
        )
