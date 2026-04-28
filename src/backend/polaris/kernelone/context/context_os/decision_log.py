"""Context Decision Log: structured, inspectable decision trace for ContextOS projections.

This module implements the Audit/Replay Layer of ContextOS 3.0.
Every projection produces a structured decision log that can be inspected,
audited, and used for improvement.

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Decision logs record WHY decisions were made, not just WHAT was decided.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ContextDecisionType(str, Enum):
    """Types of context management decisions."""

    INCLUDE_FULL = "include_full"
    INCLUDE_EXTRACTIVE = "include_extractive"
    INCLUDE_STRUCTURED = "include_structured"
    INCLUDE_STUB = "include_stub"
    COMPRESS = "compress"
    EXCLUDE = "exclude"
    PIN = "pin"
    SUMMARIZE = "summarize"
    EVICT = "evict"


class ReasonCode(str, Enum):
    """Machine-readable reason codes for decisions."""

    # Inclusion reasons
    MATCHES_CURRENT_GOAL = "MATCHES_CURRENT_GOAL"
    REFERENCED_BY_CONTRACT = "REFERENCED_BY_CONTRACT"
    REFERENCED_BY_ACCEPTANCE_CRITERIA = "REFERENCED_BY_ACCEPTANCE_CRITERIA"
    RECENT_TOOL_OUTPUT = "RECENT_TOOL_OUTPUT"
    PINNED_BY_USER = "PINNED_BY_USER"
    PINNED_BY_SYSTEM = "PINNED_BY_SYSTEM"
    FORCED_RECENT = "FORCED_RECENT"
    OPEN_LOOP_REFERENCE = "OPEN_LOOP_REFERENCE"
    DELIVERABLE_REFERENCE = "DELIVERABLE_REFERENCE"
    ACTIVE_ARTIFACT = "ACTIVE_ARTIFACT"

    # Exclusion reasons
    LOW_ATTENTION_SCORE = "LOW_ATTENTION_SCORE"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"
    ROUTE_CLEARED = "ROUTE_CLEARED"
    NOT_IN_ACTIVE_WINDOW = "NOT_IN_ACTIVE_WINDOW"
    SUPERSEDED_BY_NEWER = "SUPERSEDED_BY_NEWER"

    # Compression reasons
    JIT_SEMANTIC_COMPRESSION = "JIT_SEMANTIC_COMPRESSION"
    BRUTE_FORCE_TRUNCATION = "BRUTE_FORCE_TRUNCATION"
    BUDGET_PRESSURE = "BUDGET_PRESSURE"
    PHASE_AFFINITY_LOW = "PHASE_AFFINITY_LOW"

    # Phase reasons
    PHASE_DETECTED = "PHASE_DETECTED"
    PHASE_TRANSITION = "PHASE_TRANSITION"
    PHASE_HYSTERESIS = "PHASE_HYSTERESIS"


@dataclass(frozen=True, slots=True)
class AttentionScore:
    """Multi-dimensional attention score for a context candidate."""

    semantic_similarity: float = 0.0
    recency_score: float = 0.0
    contract_overlap: float = 0.0
    evidence_weight: float = 0.0
    phase_affinity: float = 0.0
    user_pin_boost: float = 0.0
    final_score: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "semantic": self.semantic_similarity,
            "recency": self.recency_score,
            "contract_overlap": self.contract_overlap,
            "evidence_weight": self.evidence_weight,
            "phase_affinity": self.phase_affinity,
            "user_pin_boost": self.user_pin_boost,
            "final": self.final_score,
        }


@dataclass(frozen=True, slots=True)
class ContextDecision:
    """A single context management decision with full provenance.

    This is the core data structure of the Audit/Replay Layer.
    Every decision to include, compress, or exclude content must produce
    one of these records.
    """

    timestamp: str
    decision_type: ContextDecisionType
    target_event_id: str | None
    reason: str
    reason_codes: tuple[ReasonCode, ...]

    # Quantitative rationale
    token_budget_before: int = 0
    token_budget_after: int = 0
    token_cost: int = 0

    # Attention scoring (optional, for Phase 3)
    attention_score: AttentionScore | None = None

    # Phase context (optional, for Phase 2)
    phase: str | None = None

    # Content metadata
    content_source: str = ""  # "truthlog" | "file" | "memory" | "artifact" | "tool_output"
    content_source_ref: str = ""  # e.g., "cas://sha256:abc"

    # Resolution metadata (for Phase 1: Multi-Resolution Store)
    resolution_used: str = ""  # "full" | "extractive" | "structured" | "stub"
    alternative_resolution_available: bool = False

    # Human-readable explanation
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": self.timestamp,
            "decision_type": self.decision_type.value,
            "target_event_id": self.target_event_id,
            "reason": self.reason,
            "reason_codes": [rc.value for rc in self.reason_codes],
            "token_budget_before": self.token_budget_before,
            "token_budget_after": self.token_budget_after,
            "token_cost": self.token_cost,
            "content_source": self.content_source,
            "content_source_ref": self.content_source_ref,
            "resolution_used": self.resolution_used,
            "alternative_resolution_available": self.alternative_resolution_available,
            "explanation": self.explanation,
        }
        if self.attention_score is not None:
            result["attention_score"] = self.attention_score.to_dict()
        if self.phase is not None:
            result["phase"] = self.phase
        return result


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """Comprehensive report for a single ContextOS projection.

    This is the top-level artifact of the Audit/Replay Layer.
    Every projection MUST produce one of these.
    """

    projection_id: str
    run_id: str
    turn_id: str
    timestamp: str

    # Phase context (populated in Phase 2)
    phase: str | None = None

    # Budget summary
    input_token_budget: int = 0
    reserved_output_tokens: int = 0
    reserved_tool_tokens: int = 0

    # Candidate statistics
    candidate_count: int = 0
    included_count: int = 0
    compressed_count: int = 0
    stubbed_count: int = 0
    excluded_count: int = 0

    # Individual decisions
    decisions: tuple[ContextDecision, ...] = ()

    # Performance metrics
    projection_duration_ms: float = 0.0
    stage_durations_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "input_token_budget": self.input_token_budget,
            "reserved_output_tokens": self.reserved_output_tokens,
            "reserved_tool_tokens": self.reserved_tool_tokens,
            "candidate_count": self.candidate_count,
            "included_count": self.included_count,
            "compressed_count": self.compressed_count,
            "stubbed_count": self.stubbed_count,
            "excluded_count": self.excluded_count,
            "decisions": [d.to_dict() for d in self.decisions],
            "projection_duration_ms": self.projection_duration_ms,
            "stage_durations_ms": self.stage_durations_ms,
        }


class ContextDecisionLog:
    """Immutable log of all context management decisions.

    This is the core component of the Audit/Replay Layer.
    It enables:
    1. Debugging: "Why was my error log truncated?"
    2. Audit: "Did we lose critical information?"
    3. Learning: "Which decisions led to poor outcomes?"
    4. Replay: "Reconstruct context with different parameters"

    Design Constraints:
    - Decisions are append-only (never modified after creation)
    - The log is thread-safe (all mutations go through _decisions list)
    - Old decisions can be archived to cold storage
    """

    def __init__(self, max_decisions: int = 1000) -> None:
        self._decisions: list[ContextDecision] = []
        self._max_decisions = max_decisions
        self._projection_count: int = 0

    def record(self, decision: ContextDecision) -> None:
        """Record a single decision. Thread-safe via list.append()."""
        self._decisions.append(decision)
        if len(self._decisions) > self._max_decisions:
            self._archive_old_decisions()

    def record_many(self, decisions: tuple[ContextDecision, ...]) -> None:
        """Record multiple decisions at once."""
        for decision in decisions:
            self.record(decision)

    def get_decisions(
        self,
        event_id: str | None = None,
        decision_type: ContextDecisionType | None = None,
        reason_code: ReasonCode | None = None,
    ) -> tuple[ContextDecision, ...]:
        """Query decisions by criteria."""
        results = list(self._decisions)

        if event_id is not None:
            results = [d for d in results if d.target_event_id == event_id]

        if decision_type is not None:
            results = [d for d in results if d.decision_type == decision_type]

        if reason_code is not None:
            results = [d for d in results if reason_code in d.reason_codes]

        return tuple(results)

    def get_last_n(self, n: int = 10) -> tuple[ContextDecision, ...]:
        """Get the last N decisions."""
        return tuple(self._decisions[-n:])

    def clear(self) -> None:
        """Clear all decisions (for testing only)."""
        self._decisions.clear()

    @property
    def count(self) -> int:
        """Total number of recorded decisions."""
        return len(self._decisions)

    @property
    def included_count(self) -> int:
        """Number of include decisions."""
        return sum(
            1
            for d in self._decisions
            if d.decision_type
            in (
                ContextDecisionType.INCLUDE_FULL,
                ContextDecisionType.INCLUDE_EXTRACTIVE,
                ContextDecisionType.INCLUDE_STRUCTURED,
                ContextDecisionType.INCLUDE_STUB,
            )
        )

    @property
    def excluded_count(self) -> int:
        """Number of exclude decisions."""
        return sum(1 for d in self._decisions if d.decision_type == ContextDecisionType.EXCLUDE)

    @property
    def compressed_count(self) -> int:
        """Number of compress decisions."""
        return sum(1 for d in self._decisions if d.decision_type == ContextDecisionType.COMPRESS)

    def build_projection_report(
        self,
        projection_id: str,
        run_id: str = "",
        turn_id: str = "",
        phase: str | None = None,
        budget_plan: Any = None,
        stage_durations_ms: dict[str, float] | None = None,
    ) -> ProjectionReport:
        """Build a ProjectionReport from accumulated decisions."""
        self._projection_count += 1
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

        # Count by type
        included = 0
        compressed = 0
        stubbed = 0
        excluded = 0

        for d in self._decisions:
            if d.decision_type in (
                ContextDecisionType.INCLUDE_FULL,
                ContextDecisionType.INCLUDE_EXTRACTIVE,
                ContextDecisionType.INCLUDE_STRUCTURED,
            ):
                included += 1
            elif d.decision_type == ContextDecisionType.INCLUDE_STUB:
                stubbed += 1
            elif d.decision_type == ContextDecisionType.COMPRESS:
                compressed += 1
            elif d.decision_type == ContextDecisionType.EXCLUDE:
                excluded += 1

        # Extract budget info
        input_budget = 0
        output_reserve = 0
        tool_reserve = 0
        if budget_plan is not None:
            input_budget = getattr(budget_plan, "input_budget", 0)
            output_reserve = getattr(budget_plan, "output_reserve", 0)
            tool_reserve = getattr(budget_plan, "tool_reserve", 0)

        return ProjectionReport(
            projection_id=projection_id,
            run_id=run_id,
            turn_id=turn_id,
            timestamp=timestamp,
            phase=phase,
            input_token_budget=input_budget,
            reserved_output_tokens=output_reserve,
            reserved_tool_tokens=tool_reserve,
            candidate_count=len(self._decisions),
            included_count=included,
            compressed_count=compressed,
            stubbed_count=stubbed,
            excluded_count=excluded,
            decisions=tuple(self._decisions),
            stage_durations_ms=stage_durations_ms or {},
        )

    def _archive_old_decisions(self) -> None:
        """Archive old decisions to cold storage (placeholder for Phase 5)."""
        # For now, just trim to max_decisions
        if len(self._decisions) > self._max_decisions:
            self._decisions = self._decisions[-self._max_decisions :]

    def to_jsonl(self, filepath: Path | str) -> None:
        """Write all decisions to a JSONL file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for decision in self._decisions:
                f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
        logger.info("Wrote %d decisions to %s", len(self._decisions), path)


def create_decision(
    decision_type: ContextDecisionType,
    target_event_id: str | None,
    reason: str,
    reason_codes: tuple[ReasonCode, ...],
    **kwargs: Any,
) -> ContextDecision:
    """Factory function for creating decisions with current timestamp."""
    return ContextDecision(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        decision_type=decision_type,
        target_event_id=target_event_id,
        reason=reason,
        reason_codes=reason_codes,
        **kwargs,
    )
