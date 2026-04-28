"""Memory Candidates: representation of recallable memories.

This module provides the data structures for memory candidates
that can be recalled from previous sessions.

Key Design Principle:
    "Memory is supplementary, not authoritative."
    Recalled memories enhance context but never override current facts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class MemoryFreshness(str, Enum):
    """Freshness levels for memory candidates."""

    CURRENT = "current"  # From current session
    RECENT = "recent"  # From recent sessions (last 24h)
    STALE = "stale"  # From older sessions
    UNKNOWN = "unknown"  # Freshness cannot be determined


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A candidate memory that can be recalled from previous sessions.

    Attributes:
        memory_id: Unique identifier for the memory
        content: The memory content
        source_session_id: Session where this memory originated
        source_event_ids: Event IDs that contributed to this memory
        created_at: When the memory was created
        freshness: How fresh the memory is
        relevance_score: Relevance to current query (0-1)
        conflict_status: Whether the memory conflicts with current facts
        projection_reason: Why this memory was projected
    """

    memory_id: str
    content: str
    source_session_id: str
    source_event_ids: tuple[str, ...] = ()
    created_at: str = ""
    freshness: MemoryFreshness = MemoryFreshness.UNKNOWN
    relevance_score: float = 0.0
    conflict_status: str = "none"  # "none" | "possible" | "confirmed"
    projection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content[:200] + "..." if len(self.content) > 200 else self.content,
            "source_session_id": self.source_session_id,
            "source_event_ids": list(self.source_event_ids),
            "created_at": self.created_at,
            "freshness": self.freshness.value,
            "relevance_score": self.relevance_score,
            "conflict_status": self.conflict_status,
            "projection_reason": self.projection_reason,
        }


@dataclass
class MemoryCandidateProvider:
    """Provides memory candidates from previous sessions.

    This class interfaces with the session persistence layer
    to recall relevant memories.

    Usage:
        provider = MemoryCandidateProvider(workspace="/path/to/workspace")
        candidates = provider.recall(query="implement feature X", limit=5)
    """

    workspace: str = "."
    _session_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def recall(
        self,
        query: str,
        limit: int = 5,
        min_relevance: float = 0.3,
        max_age_hours: int = 168,  # 7 days
    ) -> list[MemoryCandidate]:
        """Recall memory candidates matching the query.

        Args:
            query: Search query
            limit: Maximum number of candidates
            min_relevance: Minimum relevance score
            max_age_hours: Maximum age in hours

        Returns:
            List of MemoryCandidate sorted by relevance
        """
        candidates: list[MemoryCandidate] = []

        try:
            # Try to load from session persistence
            from polaris.cells.roles.session.internal.session_persistence import (
                SessionPersistenceService,
            )

            persistence = SessionPersistenceService(workspace=self.workspace)

            # Get all session snapshots
            snapshots = self._load_session_snapshots(persistence)

            for session_id, snapshot in snapshots.items():
                # Extract memories from snapshot
                memories = self._extract_memories_from_snapshot(session_id, snapshot)

                for memory in memories:
                    # Calculate relevance
                    relevance = self._calculate_relevance(memory.content, query)

                    # Filter by relevance and age
                    if relevance >= min_relevance:
                        freshness = self._determine_freshness(memory.created_at)
                        if freshness in (MemoryFreshness.CURRENT, MemoryFreshness.RECENT):
                            candidates.append(
                                MemoryCandidate(
                                    memory_id=memory.memory_id,
                                    content=memory.content,
                                    source_session_id=session_id,
                                    source_event_ids=memory.source_event_ids,
                                    created_at=memory.created_at,
                                    freshness=freshness,
                                    relevance_score=relevance,
                                )
                            )

            # Sort by relevance and limit
            candidates.sort(key=lambda c: c.relevance_score, reverse=True)
            candidates = candidates[:limit]

        except ImportError:
            logger.debug("Session persistence not available")
        except (RuntimeError, ValueError, TypeError, OSError):
            logger.warning("Failed to recall memories", exc_info=True)

        logger.debug(
            "Memory recall: query=%s, limit=%d, found=%d",
            query[:50],
            limit,
            len(candidates),
        )
        return candidates

    def _load_session_snapshots(self, persistence: Any) -> dict[str, dict[str, Any]]:
        """Load session snapshots from persistence."""
        snapshots: dict[str, dict[str, Any]] = {}

        try:
            # List all session snapshot files
            manifest_path = persistence._get_manifest_path()
            if persistence.fs.exists(manifest_path):
                manifest = persistence.fs.read_json(manifest_path)
                sessions = manifest.get("sessions", {})

                for session_id, _session_info in sessions.items():
                    snapshot_path = persistence._get_snapshot_path(session_id)
                    if persistence.fs.exists(snapshot_path):
                        snapshot = persistence.fs.read_json(snapshot_path)
                        snapshots[session_id] = snapshot
        except (RuntimeError, ValueError, TypeError, OSError):
            logger.debug("Failed to load session snapshots", exc_info=True)

        return snapshots

    def _extract_memories_from_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> list[MemoryCandidate]:
        """Extract memory candidates from a session snapshot."""
        memories: list[MemoryCandidate] = []

        # Extract from transcript
        transcript = snapshot.get("transcript_log", [])
        for event in transcript:
            content = str(event.get("content", "") or "")
            if len(content) > 50:  # Only consider substantial content
                event_id = str(event.get("event_id", ""))
                created_at = str(event.get("created_at", ""))

                memories.append(
                    MemoryCandidate(
                        memory_id=f"{session_id}_{event_id}",
                        content=content,
                        source_session_id=session_id,
                        source_event_ids=(event_id,),
                        created_at=created_at,
                    )
                )

        # Extract from working state
        working_state = snapshot.get("working_state", {})
        task_state = working_state.get("task_state", {})

        # Extract goal
        current_goal = task_state.get("current_goal")
        if current_goal:
            goal_value = str(current_goal.get("value", "") or "")
            if goal_value:
                memories.append(
                    MemoryCandidate(
                        memory_id=f"{session_id}_goal",
                        content=f"Goal: {goal_value}",
                        source_session_id=session_id,
                        created_at=snapshot.get("updated_at", ""),
                    )
                )

        return memories

    def _calculate_relevance(self, content: str, query: str) -> float:
        """Calculate relevance score between content and query.

        V1: Simple keyword overlap (no embeddings).
        V2 (Future): Use embedding-based cosine similarity.
        """
        if not content or not query:
            return 0.0

        content_lower = content.lower()
        query_lower = query.lower()

        # Extract keywords
        query_words = set(query_lower.split())
        content_words = set(content_lower.split())

        if not query_words:
            return 0.0

        # Jaccard similarity
        intersection = query_words & content_words
        union = query_words | content_words

        return len(intersection) / len(union) if union else 0.0

    def _determine_freshness(self, created_at: str) -> MemoryFreshness:
        """Determine freshness based on creation time."""
        if not created_at:
            return MemoryFreshness.UNKNOWN

        try:
            from datetime import datetime

            created_time = datetime.fromisoformat(created_at)
            now = datetime.now()
            age_hours = (now - created_time).total_seconds() / 3600

            if age_hours < 1:
                return MemoryFreshness.CURRENT
            elif age_hours < 24:
                return MemoryFreshness.RECENT
            else:
                return MemoryFreshness.STALE
        except (ValueError, TypeError):
            return MemoryFreshness.UNKNOWN
