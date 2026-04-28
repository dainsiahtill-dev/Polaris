"""Conflict Checker: detect conflicts between recalled memories and current facts.

This module checks whether recalled memories conflict with current
working state, transcript, or other memories.

Key Design Principle:
    "Memory is supplementary, not authoritative."
    Conflicting memories must be flagged and potentially excluded.

Conflict Types:
    - NONE: No conflict detected
    - POSSIBLE: Potential conflict (needs human review)
    - CONFIRMED: Definite conflict (memory should be excluded)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConflictStatus(str, Enum):
    """Status of conflict detection."""

    NONE = "none"
    POSSIBLE = "possible"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class ConflictResult:
    """Result of conflict detection."""

    status: ConflictStatus
    reason: str
    conflicting_memory_id: str = ""
    conflicting_fact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "conflicting_memory_id": self.conflicting_memory_id,
            "conflicting_fact": self.conflicting_fact,
        }


class ConflictChecker:
    """Checks for conflicts between recalled memories and current facts.

    This class implements multiple conflict detection strategies:
    1. Direct contradiction detection
    2. Temporal conflict detection (newer facts supersede older)
    3. Status conflict detection (e.g., "completed" vs "in progress")

    Usage:
        checker = ConflictChecker()
        result = checker.check(memory_candidate, current_facts)
    """

    # Patterns that indicate contradictions
    CONTRADICTION_PATTERNS = [
        (r"not\s+(\w+)", r"\1"),
        (r"do\s+not\s+(\w+)", r"\1"),
        (r"don\'t\s+(\w+)", r"\1"),
        (r"never\s+(\w+)", r"\1"),
        (r"no\s+(\w+)", r"\1"),
    ]

    # Status keywords that conflict
    STATUS_CONFLICTS = {
        "completed": {"in_progress", "pending", "blocked", "failed"},
        "in_progress": {"completed", "cancelled"},
        "failed": {"completed", "success"},
        "cancelled": {"in_progress", "pending"},
    }

    def check(
        self,
        memory_content: str,
        current_facts: list[str],
        memory_id: str = "",
    ) -> ConflictResult:
        """Check for conflicts between memory and current facts.

        Args:
            memory_content: Content of the recalled memory
            current_facts: List of current facts (from WorkingState, transcript)
            memory_id: ID of the memory being checked

        Returns:
            ConflictResult with status and reason
        """
        if not memory_content or not current_facts:
            return ConflictResult(
                status=ConflictStatus.NONE,
                reason="No content or facts to compare",
            )

        # Check for direct contradictions
        contradiction = self._check_contradiction(memory_content, current_facts)
        if contradiction:
            return ConflictResult(
                status=ConflictStatus.CONFIRMED,
                reason=f"Direct contradiction detected: {contradiction}",
                conflicting_memory_id=memory_id,
                conflicting_fact=contradiction,
            )

        # Check for status conflicts
        status_conflict = self._check_status_conflict(memory_content, current_facts)
        if status_conflict:
            return ConflictResult(
                status=ConflictStatus.POSSIBLE,
                reason=f"Status conflict detected: {status_conflict}",
                conflicting_memory_id=memory_id,
                conflicting_fact=status_conflict,
            )

        # Check for temporal conflicts (newer supersedes older)
        temporal_conflict = self._check_temporal_conflict(memory_content, current_facts)
        if temporal_conflict:
            return ConflictResult(
                status=ConflictStatus.POSSIBLE,
                reason=f"Temporal conflict: {temporal_conflict}",
                conflicting_memory_id=memory_id,
                conflicting_fact=temporal_conflict,
            )

        return ConflictResult(
            status=ConflictStatus.NONE,
            reason="No conflict detected",
        )

    def _check_contradiction(self, memory: str, facts: list[str]) -> str:
        """Check for direct contradictions."""
        memory_lower = memory.lower()

        # Extract negated words from memory
        negated_words: set[str] = set()
        for pattern, _ in self.CONTRADICTION_PATTERNS:
            matches = re.findall(pattern, memory_lower)
            negated_words.update(matches)

        if not negated_words:
            return ""

        # Check if any fact contains the non-negated version
        for fact in facts:
            fact_lower = fact.lower()
            for word in negated_words:
                if word in fact_lower and f"not {word}" not in fact_lower and f"no {word}" not in fact_lower:
                    return f"Memory negates '{word}', but fact affirms it"

        return ""

    def _check_status_conflict(self, memory: str, facts: list[str]) -> str:
        """Check for status conflicts."""
        memory_lower = memory.lower()

        # Extract status from memory
        memory_statuses: set[str] = set()
        for status in self.STATUS_CONFLICTS:
            if status in memory_lower:
                memory_statuses.add(status)

        if not memory_statuses:
            return ""

        # Check facts for conflicting statuses
        for fact in facts:
            fact_lower = fact.lower()
            for fact_status in self.STATUS_CONFLICTS:
                if fact_status in fact_lower:
                    # Check for conflict
                    for mem_status in memory_statuses:
                        if fact_status in self.STATUS_CONFLICTS.get(mem_status, set()):
                            return f"Memory says '{mem_status}', fact says '{fact_status}'"

        return ""

    def _check_temporal_conflict(self, memory: str, facts: list[str]) -> str:
        """Check for temporal conflicts (newer supersedes older)."""
        # Simple heuristic: if memory mentions "superseded" or "replaced"
        supersede_keywords = ["superseded", "replaced", "updated", "fixed", "corrected"]
        memory_lower = memory.lower()

        if any(kw in memory_lower for kw in supersede_keywords):
            # Check if facts contain the old version
            for fact in facts:
                fact_lower = fact.lower()
                # Look for overlapping content
                memory_words = set(memory_lower.split())
                fact_words = set(fact_lower.split())
                overlap = memory_words & fact_words
                if len(overlap) > 3:  # Significant overlap
                    return "Memory indicates it supersedes existing fact"

        return ""
