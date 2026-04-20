"""Prompt Chunk Taxonomy — canonical chunk type definitions for KernelOne context assembly.

Architecture:
    Chunk types are the fundamental unit of prompt assembly. Each chunk type has
    well-defined semantics, cache behavior, and priority ordering for budget
    management.

Design constraints:
    - All text uses UTF-8 encoding.
    - Chunk types are frozen enums to prevent drift.
    - Chunk metadata is immutable after construction.
    - All token estimates use char-based fallback when no estimator is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChunkType(str, Enum):
    """Canonical chunk types for prompt assembly.

    Order matters: chunks are assembled in this priority order for budget eviction.
    Lower-priority chunks are evicted first when budget is constrained.
    """

    # Tier 1: Core identity (never evicted)
    SYSTEM = "system"  # Role identity, responsibilities, constraints
    CURRENT_TURN = "current_turn"  # User's current message

    # Tier 2: Essential context (rarely evicted)
    CONTINUITY = "continuity"  # Session continuity summary
    WORKING_SET = "working_set"  # Current working files/symbols

    # Tier 3: Historical context (eviction candidate)
    HISTORY_DONE = "history_done"  # Completed conversation turns

    # Tier 4: Reference intelligence (first eviction candidates)
    EXAMPLES = "examples"  # Few-shot examples
    REMINDER = "reminder"  # Inline reminders, hints

    # Tier 5: Repository intelligence (second eviction candidates)
    REPO_INTELLIGENCE = "repo_intelligence"  # Repo map, symbol index
    READONLY_ASSETS = "readonly_assets"  # Read-only file contents

    @classmethod
    def tier_order(cls) -> list[ChunkType]:
        """Return chunk types ordered by eviction priority (safest first).

        Eviction order: HIGH priority types first -> LOW priority types first.
        Within same tier, smaller chunks survive larger ones.
        """
        return [
            # Tier 1: Never evicted
            cls.SYSTEM,
            cls.CURRENT_TURN,
            # Tier 2: Rarely evicted
            cls.CONTINUITY,
            cls.WORKING_SET,
            # Tier 3: Historical
            cls.HISTORY_DONE,
            # Tier 4: Reference
            cls.EXAMPLES,
            cls.REMINDER,
            # Tier 5: Intelligence (first to evict)
            cls.REPO_INTELLIGENCE,
            cls.READONLY_ASSETS,
        ]

    @property
    def eviction_priority(self) -> int:
        """Lower number = higher eviction resistance."""
        tier_map = {
            ChunkType.SYSTEM: 0,
            ChunkType.CURRENT_TURN: 1,
            ChunkType.CONTINUITY: 2,
            ChunkType.WORKING_SET: 3,
            ChunkType.HISTORY_DONE: 4,
            ChunkType.EXAMPLES: 5,
            ChunkType.REMINDER: 6,
            ChunkType.REPO_INTELLIGENCE: 7,
            ChunkType.READONLY_ASSETS: 8,
        }
        return tier_map.get(self, 99)

    @property
    def cacheable(self) -> bool:
        """Whether this chunk type supports cache-control headers."""
        return self in {
            ChunkType.SYSTEM,
            ChunkType.CONTINUITY,  # Stable task/session info
            ChunkType.EXAMPLES,
            ChunkType.REPO_INTELLIGENCE,
            ChunkType.READONLY_ASSETS,
        }


class CacheControl(str, Enum):
    """Cache control directives for chunk content."""

    EPHEMERAL = "ephemeral"  # Not cached, not reused
    TRANSIENT = "transient"  # Cached briefly (session scope)
    PERSISTENT = "persistent"  # Cached long-term (workspace scope)


@dataclass(frozen=True)
class ChunkMetadata:
    """Immutable metadata for a prompt chunk.

    This is a frozen dataclass to prevent mutation after construction.
    All fields are required to ensure complete metadata for observability.
    """

    chunk_type: ChunkType
    source: str  # Human-readable source (e.g., "role_profile", "session", "repo_map")
    cache_control: CacheControl = CacheControl.EPHEMERAL
    content_hash: str = ""  # SHA256 hash of content for cache invalidation
    created_at: float = 0.0  # Unix timestamp

    # Observability fields
    char_count: int = 0
    estimated_tokens: int = 0

    # Provenance
    role_id: str = ""
    session_id: str = ""
    turn_index: int = 0

    # Budget decision context
    was_evicted: bool = False
    eviction_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for receipt generation."""
        return {
            "chunk_type": self.chunk_type.value,
            "source": self.source,
            "cache_control": self.cache_control.value,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "char_count": self.char_count,
            "estimated_tokens": self.estimated_tokens,
            "role_id": self.role_id,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "was_evicted": self.was_evicted,
            "eviction_reason": self.eviction_reason,
        }


@dataclass
class PromptChunk:
    """A single prompt chunk with content and metadata.

    This is the fundamental building block for prompt assembly.
    Chunks are assembled by PromptChunkAssembler into final requests.

    Usage::

        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="You are Polaris...",
            metadata=ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="role_profile"),
        )
    """

    chunk_type: ChunkType
    content: str
    metadata: ChunkMetadata
    _content_str: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        # Auto-compute char count if not provided
        if self.metadata.char_count == 0:
            object.__setattr__(self.metadata, "char_count", len(self.content))
        # Auto-compute estimated tokens if not provided
        if self.metadata.estimated_tokens == 0 and self.content:
            # Rough fallback: ~4 chars/token for mixed text
            object.__setattr__(self.metadata, "estimated_tokens", max(1, len(self.content) // 4))

    @property
    def tokens(self) -> int:
        """Shorthand for estimated tokens."""
        return self.metadata.estimated_tokens

    @property
    def chars(self) -> int:
        """Shorthand for character count."""
        return self.metadata.char_count

    def to_dict(self) -> dict[str, Any]:
        """Serialize for debugging/receipt."""
        return {
            "chunk_type": self.chunk_type.value,
            "content_length": len(self.content),
            "estimated_tokens": self.tokens,
            "metadata": self.metadata.to_dict(),
        }

    def to_message(self) -> dict[str, Any]:
        """Convert to chat message format."""
        return {
            "role": _chunk_type_to_role(self.chunk_type),
            "content": self.content,
        }


def _chunk_type_to_role(chunk_type: ChunkType) -> str:
    """Map chunk type to chat message role."""
    role_map = {
        ChunkType.SYSTEM: "system",
        ChunkType.CURRENT_TURN: "user",
        ChunkType.CONTINUITY: "user",
        ChunkType.WORKING_SET: "user",
        ChunkType.HISTORY_DONE: "user",
        ChunkType.EXAMPLES: "user",
        ChunkType.REMINDER: "user",
        ChunkType.REPO_INTELLIGENCE: "user",
        ChunkType.READONLY_ASSETS: "user",
    }
    return role_map.get(chunk_type, "user")


__all__ = [
    "CacheControl",
    "ChunkMetadata",
    "ChunkType",
    "PromptChunk",
]
