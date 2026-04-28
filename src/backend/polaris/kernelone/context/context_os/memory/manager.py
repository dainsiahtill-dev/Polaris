"""Memory Manager: orchestrates memory recall and injection.

This module provides the main interface for memory management in ContextOS 3.0.
It orchestrates the memory pipeline:
    Memory Candidate → Relevance Scoring → Freshness Check → Conflict Check → Projection

Key Design Principle:
    "Memory is supplementary, not authoritative."
    Recalled memories enhance context but never override current facts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .candidates import MemoryCandidate, MemoryCandidateProvider, MemoryFreshness
from .conflict_checker import ConflictChecker, ConflictResult, ConflictStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryProjection:
    """A memory that has been validated and is ready for injection."""

    memory: MemoryCandidate
    conflict_result: ConflictResult
    injection_allowed: bool
    injection_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": self.memory.to_dict(),
            "conflict_result": self.conflict_result.to_dict(),
            "injection_allowed": self.injection_allowed,
            "injection_reason": self.injection_reason,
        }


@dataclass
class MemoryManager:
    """Orchestrates memory recall and injection for ContextOS 3.0.

    This class manages the complete memory pipeline:
    1. Recall candidates from previous sessions
    2. Score relevance to current query
    3. Check freshness
    4. Check for conflicts with current facts
    5. Project validated memories for injection

    Usage:
        manager = MemoryManager(workspace="/path/to/workspace")
        projections = manager.process(
            query="implement feature X",
            current_facts=["goal: implement feature X", "status: in_progress"],
            limit=5,
        )
        for proj in projections:
            if proj.injection_allowed:
                # Inject memory into context
                pass
    """

    workspace: str = "."
    _provider: MemoryCandidateProvider = field(default_factory=MemoryCandidateProvider)
    _conflict_checker: ConflictChecker = field(default_factory=ConflictChecker)

    def process(
        self,
        query: str,
        current_facts: list[str],
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[MemoryProjection]:
        """Process memory recall pipeline.

        Args:
            query: Current query or goal
            current_facts: Current facts from WorkingState
            limit: Maximum number of memories to recall
            min_relevance: Minimum relevance score

        Returns:
            List of MemoryProjection (validated memories ready for injection)
        """
        # Step 1: Recall candidates
        candidates = self._provider.recall(
            query=query,
            limit=limit,
            min_relevance=min_relevance,
        )

        if not candidates:
            logger.debug("No memory candidates found for query: %s", query[:50])
            return []

        # Step 2: Process each candidate
        projections: list[MemoryProjection] = []
        for candidate in candidates:
            # Check conflict
            conflict_result = self._conflict_checker.check(
                memory_content=candidate.content,
                current_facts=current_facts,
                memory_id=candidate.memory_id,
            )

            # Determine if injection is allowed
            injection_allowed, injection_reason = self._should_inject(candidate, conflict_result)

            projection = MemoryProjection(
                memory=candidate,
                conflict_result=conflict_result,
                injection_allowed=injection_allowed,
                injection_reason=injection_reason,
            )
            projections.append(projection)

        # Sort by relevance (injected first, then by relevance)
        projections.sort(key=lambda p: (not p.injection_allowed, -p.memory.relevance_score))

        logger.info(
            "Memory pipeline: %d candidates, %d conflicts, %d allowed for injection",
            len(projections),
            sum(1 for p in projections if p.conflict_result.status != ConflictStatus.NONE),
            sum(1 for p in projections if p.injection_allowed),
        )

        return projections

    def _should_inject(
        self,
        candidate: MemoryCandidate,
        conflict_result: ConflictResult,
    ) -> tuple[bool, str]:
        """Determine if a memory should be injected.

        Args:
            candidate: Memory candidate
            conflict_result: Conflict detection result

        Returns:
            Tuple of (allowed, reason)
        """
        # Rule 1: Confirmed conflicts are never injected
        if conflict_result.status == ConflictStatus.CONFIRMED:
            return False, f"Confirmed conflict: {conflict_result.reason}"

        # Rule 2: Possible conflicts need higher relevance
        if conflict_result.status == ConflictStatus.POSSIBLE:
            if candidate.relevance_score < 0.7:
                return False, f"Possible conflict with low relevance: {conflict_result.reason}"
            return True, f"Possible conflict but high relevance: {conflict_result.reason}"

        # Rule 3: Stale memories need higher relevance
        if candidate.freshness == MemoryFreshness.STALE:
            if candidate.relevance_score < 0.5:
                return False, "Stale memory with low relevance"
            return True, "Stale memory but high relevance"

        # Rule 4: Current/recent memories with sufficient relevance
        if candidate.relevance_score >= min(0.3, candidate.relevance_score):
            return True, f"Valid memory with relevance {candidate.relevance_score:.2f}"

        return False, "Low relevance"
