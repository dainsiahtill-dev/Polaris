"""Akashic Nexus: Multi-tier Memory Engine for AI/Agent Runtimes.

Architecture:
    - WorkingMemoryWindow: Short-term sliding context window
    - SemanticCacheInterceptor: Embedding-based LLM call caching
    - MemoryManager: Unified DI container for cross-tier memory orchestration
    - CompressionDaemon: Preemptive background context compression

Design constraints:
    - DIP: All storage backends via Protocol/ABC injection
    - UTF-8 explicit text I/O
    - Type-safe generics (TypeVar) for memory item types
    - Lazy evaluation via generators for large dataset handling
"""

from __future__ import annotations

from polaris.kernelone.akashic.compression_daemon import (
    CompressionDaemon,
    CompressionStats,
    DaemonConfig,
    DaemonState,
)
from polaris.kernelone.akashic.episodic_memory import (
    AkashicEpisodicMemory,
    EpisodeRecord,
    TurnRecord,
)
from polaris.kernelone.akashic.memory_manager import MemoryManager, MemoryManagerConfig
from polaris.kernelone.akashic.protocols import (
    AVAILABLE_EMBEDDING_MODELS,
    DemotionCandidate,
    EpisodicMemoryPort,
    MemoryItemBase,
    MemoryManagerPort,
    PromotionCandidate,
    SemanticCacheConfig,
    SemanticCacheEntry,
    SemanticCachePort,
    SemanticMemoryPort,
    SnapshotBase,
    TierCoordinatorPort,
    WorkingMemoryConfig,
    WorkingMemoryPort,
)
from polaris.kernelone.akashic.semantic_cache import (
    SemanticCacheInterceptor,
)
from polaris.kernelone.akashic.semantic_memory import (
    AkashicSemanticMemory,
)
from polaris.kernelone.akashic.working_memory import (
    ChunkPriority,
    MemoryChunk,
    WorkingMemorySnapshot,
    WorkingMemoryWindow,
)

__version__ = "0.1.0"

__all__ = [
    # Constants
    "AVAILABLE_EMBEDDING_MODELS",
    "AkashicEpisodicMemory",
    "AkashicSemanticMemory",
    "ChunkPriority",
    "CompressionDaemon",
    "CompressionStats",
    "DaemonConfig",
    "DaemonState",
    "DemotionCandidate",
    "EpisodeRecord",
    "EpisodicMemoryPort",
    "MemoryChunk",
    "MemoryItemBase",
    # Core
    "MemoryManager",
    "MemoryManagerConfig",
    "MemoryManagerPort",
    # Types
    "PromotionCandidate",
    "SemanticCacheConfig",
    "SemanticCacheEntry",
    "SemanticCacheInterceptor",
    "SemanticCachePort",
    "SemanticMemoryPort",
    "SnapshotBase",
    # Ports
    "TierCoordinatorPort",
    "TurnRecord",
    "WorkingMemoryConfig",
    "WorkingMemoryPort",
    "WorkingMemorySnapshot",
    # Sub-systems
    "WorkingMemoryWindow",
]
