"""Active Learning Engine - learns from errors to extract patterns and avoid repeating mistakes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class ErrorPattern:
    """Pattern extracted from an error.

    Attributes:
        pattern_id: Unique identifier for this pattern
        root_cause: Description of the root cause of the error
        avoidance_strategy: Strategy to avoid similar errors in the future
        learned_knowledge: New knowledge acquired from this error
        occurrence_count: Number of times this pattern has been observed
        last_seen: ISO timestamp of when this pattern was last seen
    """

    pattern_id: str
    root_cause: str
    avoidance_strategy: str
    learned_knowledge: str
    occurrence_count: int = 1
    last_seen: str | None = None


@dataclass(frozen=True)
class LearningResult:
    """Result of learning from an error.

    Attributes:
        patterns: Tuple of extracted error patterns
        judgment_updates: Suggested updates to judgment criteria
        new_knowledge_acquired: New knowledge gained from the error
    """

    patterns: tuple[ErrorPattern, ...] = field(default_factory=tuple)
    judgment_updates: tuple[str, ...] = field(default_factory=tuple)
    new_knowledge_acquired: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ActiveLearner:
    """Learns from errors and updates judgment criteria.

    This engine extracts patterns from errors, identifies root causes,
    and generates avoidance strategies to improve future performance.

    Attributes:
        _patterns_store: Internal storage for learned patterns
        _error_type_keywords: Keywords associated with common error types
    """

    _patterns_store: dict[str, ErrorPattern] = field(default_factory=dict)
    _error_type_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Initialize the active learner with common error type keywords."""
        self._error_type_keywords = {
            "timeout": (
                "timeout",
                "timed out",
                "deadline",
                "exceeded",
                "took too long",
            ),
            "memory": (
                "memory",
                "oom",
                "out of memory",
                "allocation failed",
                "heap",
            ),
            "io": (
                "io error",
                "read failed",
                "write failed",
                "file not found",
                "permission denied",
                "disk full",
            ),
            "network": (
                "network",
                "connection refused",
                "connection reset",
                "dns",
                "unreachable",
                "socket",
            ),
            "validation": (
                "validation",
                "invalid",
                "malformed",
                "constraint",
                "schema",
            ),
            "authentication": (
                "auth",
                "unauthorized",
                "forbidden",
                "credential",
                "token",
            ),
            "rate_limit": (
                "rate limit",
                "throttle",
                "quota",
                "too many requests",
            ),
            "resource_busy": (
                "resource busy",
                "locked",
                "conflict",
                "concurrent",
            ),
        }

    def _generate_pattern_id(
        self,
        root_cause: str,
        avoidance_strategy: str,
    ) -> str:
        """Generate a unique pattern ID from root cause and strategy.

        Args:
            root_cause: The root cause description
            avoidance_strategy: The avoidance strategy

        Returns:
            A unique hash-based pattern ID
        """
        combined = f"{root_cause}|{avoidance_strategy}"
        hash_digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
        return f"pattern_{hash_digest}"

    def _detect_error_type(
        self,
        error_description: str,
        provided_type: str | None,
    ) -> str | None:
        """Detect the error type from description or use provided type.

        Args:
            error_description: The error description text
            provided_type: Optional explicitly provided error type

        Returns:
            Detected error type or None
        """
        if provided_type:
            return provided_type.lower()

        error_lower = error_description.lower()
        for error_type, keywords in self._error_type_keywords.items():
            if any(keyword in error_lower for keyword in keywords):
                return error_type
        return None

    def _extract_root_cause(
        self,
        error_description: str,
        context_summary: str,
        error_type: str | None,
    ) -> str:
        """Extract the root cause from error description and context.

        Args:
            error_description: The error description
            context_summary: Summary of the context where error occurred
            error_type: Detected error type

        Returns:
            Extracted root cause description
        """
        root_cause_parts = []

        if error_type:
            root_cause_parts.append(f"Error type: {error_type} error")

        # Extract key phrases from error description
        error_sentences = re.split(r"[.!?]", error_description)
        if error_sentences:
            key_phrase = error_sentences[0].strip()
            if len(key_phrase) > 10:
                root_cause_parts.append(f"Error occurred: {key_phrase}")

        # Add context insight
        if context_summary:
            root_cause_parts.append(f"Context: {context_summary[:100]}")

        return "; ".join(root_cause_parts) if root_cause_parts else "Unknown root cause"

    def _generate_avoidance_strategy(
        self,
        error_description: str,
        context_summary: str,
        error_type: str | None,
    ) -> str:
        """Generate an avoidance strategy for the error.

        Args:
            error_description: The error description
            context_summary: Summary of the context
            error_type: Detected error type

        Returns:
            Avoidance strategy description
        """
        strategies = []

        type_strategies: dict[str, str] = {
            "timeout": "Implement retry with exponential backoff and increase timeout thresholds",
            "memory": "Add memory management, increase buffer limits, or process data in chunks",
            "io": "Add file existence checks, proper error handling, and validate file permissions",
            "network": "Implement connection pooling, retry logic, and proper timeout handling",
            "validation": "Add input validation early, use schema validation, and provide clear error messages",
            "authentication": "Refresh credentials proactively, implement proper token handling",
            "rate_limit": "Implement request throttling, respect rate limits, add request queuing",
            "resource_busy": "Implement proper locking, add retry with jitter, use optimistic concurrency",
        }

        if error_type and error_type in type_strategies:
            strategies.append(type_strategies[error_type])

        # Add context-specific advice
        if "retry" in error_description.lower():
            strategies.append("Review and fix retry logic - ensure idempotency and proper backoff")

        if "null" in error_description.lower() or "none" in error_description.lower():
            strategies.append("Add null/None checks before accessing object properties")

        if "index" in error_description.lower() or "out of range" in error_description.lower():
            strategies.append("Add bounds checking before array/list access")

        if not strategies:
            strategies.append("Review error handling logic and add comprehensive validation")

        return " ".join(strategies)

    def _generate_learned_knowledge(
        self,
        error_description: str,
        context_summary: str,
        error_type: str | None,
    ) -> str:
        """Generate new knowledge learned from the error.

        Args:
            error_description: The error description
            context_summary: Summary of the context
            error_type: Detected error type

        Returns:
            Knowledge learned from the error
        """
        knowledge_parts = []

        if error_type:
            knowledge_parts.append(f"Learned that {error_type} errors require specific handling patterns")

        # Extract key lessons
        if "timeout" in error_description.lower():
            knowledge_parts.append("Timeouts indicate resource contention or network issues")

        if "validation" in error_description.lower() or "invalid" in error_description.lower():
            knowledge_parts.append("Input validation should happen early in the execution path")

        if "memory" in error_description.lower():
            knowledge_parts.append("Memory management is critical for long-running operations")

        if not knowledge_parts:
            knowledge_parts.append("Each error provides an opportunity to improve system robustness")

        return " ".join(knowledge_parts)

    def _generate_judgment_updates(
        self,
        error_description: str,
        context_summary: str,
        error_type: str | None,
    ) -> tuple[str, ...]:
        """Generate suggested judgment criteria updates.

        Args:
            error_description: The error description
            context_summary: Summary of the context
            error_type: Detected error type

        Returns:
            Tuple of judgment update suggestions
        """
        updates: list[str] = []

        if error_type:
            updates.append(f"Add {error_type} error detection to pre-execution validation")

        if "retry" in error_description.lower():
            updates.append("Verify retry logic meets idempotency requirements")

        if "permission" in error_description.lower() or "auth" in error_description.lower():
            updates.append("Add permission and authentication state checks")

        if "memory" in error_description.lower():
            updates.append("Add memory usage monitoring to execution budget")

        if not updates:
            updates.append("Review error handling coverage in test cases")

        return tuple(updates)

    async def learn_from_error(
        self,
        error_description: str,
        context_summary: str,
        error_type: str | None = None,
    ) -> LearningResult:
        """Learn from an error to extract patterns.

        Analyzes:
        1. Root cause of the error
        2. How to avoid similar errors
        3. New knowledge needed
        4. How to update judgment criteria

        Args:
            error_description: Description of the error that occurred
            context_summary: Summary of the context when error occurred
            error_type: Optional explicit error type classification

        Returns:
            LearningResult containing extracted patterns and suggestions
        """
        # Detect or use provided error type
        detected_type = self._detect_error_type(error_description, error_type)

        # Extract components
        root_cause = self._extract_root_cause(error_description, context_summary, detected_type)
        avoidance_strategy = self._generate_avoidance_strategy(error_description, context_summary, detected_type)
        learned_knowledge = self._generate_learned_knowledge(error_description, context_summary, detected_type)
        judgment_updates = self._generate_judgment_updates(error_description, context_summary, detected_type)

        # Generate pattern ID
        pattern_id = self._generate_pattern_id(root_cause, avoidance_strategy)
        timestamp = datetime.now(timezone.utc).isoformat()

        # Check if similar pattern exists
        if pattern_id in self._patterns_store:
            existing = self._patterns_store[pattern_id]
            updated_pattern = ErrorPattern(
                pattern_id=pattern_id,
                root_cause=existing.root_cause,
                avoidance_strategy=existing.avoidance_strategy,
                learned_knowledge=existing.learned_knowledge,
                occurrence_count=existing.occurrence_count + 1,
                last_seen=timestamp,
            )
            self._patterns_store[pattern_id] = updated_pattern
            patterns = (updated_pattern,)
        else:
            new_pattern = ErrorPattern(
                pattern_id=pattern_id,
                root_cause=root_cause,
                avoidance_strategy=avoidance_strategy,
                learned_knowledge=learned_knowledge,
                occurrence_count=1,
                last_seen=timestamp,
            )
            self._patterns_store[pattern_id] = new_pattern
            patterns = (new_pattern,)

        # New knowledge acquired
        new_knowledge = (learned_knowledge,)

        return LearningResult(
            patterns=patterns,
            judgment_updates=judgment_updates,
            new_knowledge_acquired=new_knowledge,
        )

    async def get_learned_patterns(
        self,
        category: str | None = None,
    ) -> list[ErrorPattern]:
        """Retrieve previously learned patterns.

        Args:
            category: Optional category to filter patterns by error type

        Returns:
            List of ErrorPattern objects, optionally filtered by category
        """
        if category is None:
            return list(self._patterns_store.values())

        # Filter by category based on root cause keywords
        filtered_patterns: list[ErrorPattern] = []
        category_lower = category.lower()

        for pattern in self._patterns_store.values():
            if category_lower in pattern.root_cause.lower():
                filtered_patterns.append(pattern)

        return filtered_patterns

    async def merge_patterns(
        self,
        pattern1: ErrorPattern,
        pattern2: ErrorPattern,
    ) -> ErrorPattern:
        """Merge similar patterns into one.

        When two patterns have similar root causes or avoidance strategies,
        they can be merged into a single more robust pattern.

        Args:
            pattern1: First pattern to merge
            pattern2: Second pattern to merge

        Returns:
            Merged ErrorPattern with combined knowledge
        """
        # Generate new pattern ID for merged pattern
        combined_root = f"{pattern1.root_cause} + {pattern2.root_cause}"
        combined_strategy = f"{pattern1.avoidance_strategy} {pattern2.avoidance_strategy}"
        new_id = self._generate_pattern_id(combined_root, combined_strategy)

        # Combine knowledge
        combined_knowledge = f"{pattern1.learned_knowledge} Also: {pattern2.learned_knowledge}"

        # Use the higher occurrence count
        max_occurrences = max(pattern1.occurrence_count, pattern2.occurrence_count)

        # Use the most recent last_seen
        last_seen = pattern1.last_seen
        if pattern2.last_seen and (last_seen is None or pattern2.last_seen > last_seen):
            last_seen = pattern2.last_seen

        merged = ErrorPattern(
            pattern_id=new_id,
            root_cause=combined_root,
            avoidance_strategy=combined_strategy,
            learned_knowledge=combined_knowledge,
            occurrence_count=max_occurrences,
            last_seen=last_seen,
        )

        # Store the merged pattern
        self._patterns_store[new_id] = merged

        return merged
