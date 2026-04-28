"""Memory: Akashic Memory Integration for ContextOS 3.0.

This module provides cross-session memory integration for ContextOS.
Memories can be recalled from previous sessions and injected into
the current context after passing relevance, freshness, and conflict checks.

Key Design Principle:
    "Memory is supplementary, not authoritative."
    Recalled memories enhance context but never override current facts.

Memory Pipeline:
    Memory Candidate → Relevance Scoring → Freshness Check → Conflict Check → Projection

Usage:
    from polaris.kernelone.context.context_os.memory import MemoryManager, MemoryCandidate

    manager = MemoryManager()
    candidates = manager.recall(query="implement feature X", limit=5)
    for candidate in candidates:
        if candidate.freshness == "current" and candidate.conflict_status == "none":
            # Inject into context
            pass
"""

from .candidates import MemoryCandidate, MemoryCandidateProvider
from .conflict_checker import ConflictChecker, ConflictStatus
from .manager import MemoryManager

__all__ = [
    "ConflictChecker",
    "ConflictStatus",
    "MemoryCandidate",
    "MemoryCandidateProvider",
    "MemoryManager",
]
