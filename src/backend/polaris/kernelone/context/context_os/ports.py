"""Artifact storage port interfaces for State-First Context OS.

This module defines the ArtifactStoragePort Protocol and supporting types
for abstracting artifact storage implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    pass


class StorageTier(StrEnum):
    """Storage tier types for artifact storage."""

    MEMORY = "memory"
    FILE = "file"
    S3 = "s3"


class EvictionPolicy(StrEnum):
    """Eviction policy types for artifact storage."""

    LRU = "lru"
    SIZE_BASED = "size_based"
    AGE_BASED = "age_based"
    PRIORITY = "priority"


@dataclass(frozen=True, slots=True)
class ArtifactStub:
    """Lightweight artifact reference for external storage.

    This is a minimal representation of an artifact used for referencing
    artifacts stored in external storage systems.
    """

    artifact_id: str
    artifact_type: str
    mime_type: str
    token_count: int
    char_count: int
    peek: str
    keys: tuple[str, ...] = ()
    restore_tool: str = "read_artifact"
    tier: StorageTier = StorageTier.MEMORY
    metadata: dict[str, Any] | tuple[tuple[str, Any], ...] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Convert dict metadata to immutable tuple during initialization."""
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", tuple(sorted(self.metadata.items())))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "mime_type": self.mime_type,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "peek": self.peek,
            "keys": list(self.keys),
            "restore_tool": self.restore_tool,
            "tier": self.tier,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class StorageStats:
    """Storage statistics for artifact storage."""

    total_artifacts: int = 0
    total_bytes: int = 0
    tier: StorageTier = StorageTier.MEMORY
    eviction_policy: EvictionPolicy = EvictionPolicy.LRU
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate as a ratio."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class ArtifactStoragePort(Protocol):
    """Protocol for artifact storage implementations.

    This Protocol defines the interface that all artifact storage
    implementations must support.
    """

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
        """Store an artifact.

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
        ...

    def retrieve(self, artifact_id: str) -> dict[str, Any] | None:
        """Retrieve an artifact by ID.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            Dictionary representation of the artifact, or None if not found.
        """
        ...

    def list_references(self) -> list[dict[str, Any]]:
        """List all artifact references.

        Returns:
            List of artifact reference dictionaries.
        """
        ...

    def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            True if the artifact exists, False otherwise.
        """
        ...

    def evict(self, artifact_id: str) -> bool:
        """Evict an artifact from storage.

        Args:
            artifact_id: Unique identifier for the artifact.

        Returns:
            True if the artifact was evicted, False if not found.
        """
        ...

    def get_stats(self) -> StorageStats:
        """Get storage statistics.

        Returns:
            StorageStats object with current storage metrics.
        """
        ...

    def evict_if_needed(self) -> int:
        """Evict artifacts if storage limits are exceeded.

        Returns:
            Number of artifacts evicted.
        """
        ...

    def migrate_to_tier(self, target_tier: StorageTier) -> int:
        """Migrate artifacts to a different storage tier.

        Args:
            target_tier: The target storage tier.

        Returns:
            Number of artifacts migrated.
        """
        ...
