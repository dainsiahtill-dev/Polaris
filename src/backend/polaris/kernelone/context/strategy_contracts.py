"""Strategy Foundation contracts — zero behavior drift.

All dataclasses use kw_only=True (Pydantic V2 compatible).
All Protocol interfaces use @runtime_checkable.

This module defines the strategy framework schema only.
No existing logic is modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# Import ExplorationPhase and ExpansionDecision from canonical source (exploration_policy.py)
# This eliminates the duplicate definitions and # type: ignore[no-redef]
from .exploration_policy import ExpansionDecision, ExplorationPhase

if TYPE_CHECKING:
    from .budget_gate import ContextBudget
    from .exploration_policy import AssetCandidate, ExplorationContext


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class ReadEscalationDecision(str, Enum):
    """Decision for a read escalation request."""

    DIRECT_READ = "direct_read"
    RANGE_FIRST = "range_first"
    DENIED = "denied"


class CompactionDecision(str, Enum):
    """Decision for a compaction trigger."""

    TRIGGER = "trigger"
    DEFER = "defer"
    NONE = "none"


class BudgetDecisionKind(str, Enum):
    """Kind of budget decision recorded in a receipt."""

    CHECK = "check"
    APPROVED = "approved"
    DENIED = "denied"
    DEFERRED = "deferred"
    COMPACTION_SUGGESTED = "compaction_suggested"


# ------------------------------------------------------------------
# Profile metadata
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ProfileMetadata:
    """Static metadata for a strategy profile."""

    description: str = ""
    target_domain: str = "code"  # "code" | "document" | "fiction" | "research" | "universal"
    risk_level: str = "canonical"  # "canonical" | "experimental" | "reference"


# ------------------------------------------------------------------
# Strategy Protocol interfaces
# ------------------------------------------------------------------


@runtime_checkable
class ExplorationStrategyPort(Protocol):
    """Decide how to explore candidate assets in a turn."""

    def decide_expansion(
        self,
        ctx: ExplorationContext,
        budget: ContextBudget,
    ) -> ExpansionDecisionResult:
        """Return expansion decision result for the current exploration pass."""
        ...

    def get_phase(self) -> ExplorationPhase:
        """Return the current exploration phase label."""
        ...


@runtime_checkable
class ReadEscalationStrategyPort(Protocol):
    """Decide whether to upgrade a slice read to a full-file read."""

    def should_read_full(
        self,
        asset: AssetCandidate,
        budget: ContextBudget,
    ) -> ReadEscalationDecision:
        """Return the read escalation decision for the asset."""
        ...


@runtime_checkable
class HistoryMaterializationStrategyPort(Protocol):
    """Decide how tool receipts and prior turns enter prompt history."""

    def materialize(
        self,
        messages: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
    ) -> HistoryMaterialization:
        """Return the materialized history payload for the prompt."""
        ...


@runtime_checkable
class SessionContinuityStrategyPort(Protocol):
    """Decide how session continuity is assembled per turn."""

    def project(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a SessionContinuityPack-compatible dict."""
        ...


@runtime_checkable
class CompactionStrategyPort(Protocol):
    """Decide when to trigger compaction and how to compact."""

    def should_compact(
        self,
        budget: ContextBudget,
        history_size: int,
    ) -> CompactionDecision:
        """Return the compaction decision."""
        ...

    def compact(
        self,
        history: list[dict[str, Any]],
    ) -> CompactionResult:
        """Return the compaction result."""
        ...


@runtime_checkable
class CacheStrategyPort(Protocol):
    """Decide cache lookup order, TTLs, and invalidation."""

    def should_cache(self, asset_key: str, ttl_hint: float) -> bool:
        """Return True if the asset should be cached."""
        ...

    def get_tier(self, asset_key: str) -> str:
        """Return the cache tier name for the asset (e.g. 'hot', 'warm', 'cold')."""
        ...


@runtime_checkable
class EvaluationStrategyPort(Protocol):
    """Score and compare strategy runs."""

    def score(self, receipt: StrategyReceipt) -> Scorecard:
        """Return a scorecard for the run."""
        ...

    def compare(self, a: Scorecard, b: Scorecard) -> ScoreDiff:
        """Return the diff between two scorecards."""
        ...


# ------------------------------------------------------------------
# Supporting result types
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ExpansionDecisionResult:
    """Result of an expansion decision with reasoning.

    Note (P1-CTX-003b convergence):
        This is distinct from exploration_policy.ExpansionDecision (Enum).
        ExpansionDecision (Enum) = APPROVED, DENIED, DEFERRED
        ExpansionDecisionResult (dataclass) = decision, reason, asset_key
    """

    decision: str  # "approved" | "denied" | "deferred"
    reason: str = ""
    asset_key: str = ""


@dataclass(frozen=True, kw_only=True)
class ReadEscalationDecisionResult:
    """Result of a read escalation decision."""

    decision: ReadEscalationDecision
    asset_key: str = ""
    estimated_tokens: int = 0
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class CompactionResult:
    """Result of a compaction pass."""

    triggered: bool
    compacted_items: int = 0
    tokens_recovered: int = 0
    summary: str = ""


@dataclass(frozen=True, kw_only=True)
class HistoryMaterialization:
    """Result of history materialization."""

    history_tokens: int = 0
    receipt_tokens: int = 0
    total_tokens: int = 0
    message_count: int = 0
    receipt_count: int = 0
    micro_compacted: bool = False
    artifact_stub_count: int = 0
    materialized_messages: tuple[dict[str, Any], ...] = ()
    materialized_receipts: tuple[dict[str, Any], ...] = ()


# ------------------------------------------------------------------
# Strategy Bundle
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StrategyBundle:
    """A named, versioned collection of swappable sub-strategies."""

    bundle_id: str  # e.g. "kernelone.default.v1"
    bundle_version: str  # e.g. "1.0.0"
    exploration: ExplorationStrategyPort | None = None
    read_escalation: ReadEscalationStrategyPort | None = None
    history_materialization: HistoryMaterializationStrategyPort | None = None
    session_continuity: SessionContinuityStrategyPort | None = None
    compaction: CompactionStrategyPort | None = None
    cache: CacheStrategyPort | None = None
    evaluation: EvaluationStrategyPort | None = None


# ------------------------------------------------------------------
# Strategy Profile
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StrategyProfile:
    """A tuned configuration variant of a StrategyBundle.

    Attributes:
        profile_id: Unique identifier (e.g. "canonical_balanced").
        profile_version: Semver version of this profile definition.
        bundle_id: Which StrategyBundle this profile tunes.
        overrides: Profile-specific parameter overrides keyed by sub-strategy name.
        metadata: Static descriptive metadata.
    """

    profile_id: str
    profile_version: str = "1.0.0"
    bundle_id: str = "kernelone.default.v1"
    overrides: dict[str, Any] = field(default_factory=dict)
    metadata: ProfileMetadata = field(default_factory=ProfileMetadata)


# ------------------------------------------------------------------
# Strategy Receipt
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BudgetDecision:
    """A single budget gate decision recorded in a receipt."""

    kind: BudgetDecisionKind
    estimated_tokens: int = 0
    headroom_before: int = 0
    headroom_after: int = 0
    decision: str = ""  # "approved" | "denied" | "deferred"
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class ReadEscalation:
    """A single read escalation decision recorded in a receipt."""

    asset_key: str = ""
    decision: str = ""  # ReadEscalationDecision.value
    estimated_tokens: int = 0
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class StrategyReceipt:
    """Immutable record of a single strategy-run turn.

    Every turn that uses the strategy framework must emit a receipt.
    Receipts are the ground truth for later evaluation and replay.
    """

    # Identity
    bundle_id: str
    bundle_version: str
    profile_id: str
    profile_hash: str  # SHA-256 of resolved profile config
    turn_index: int

    # Timestamps
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Decisions
    budget_decisions: tuple[BudgetDecision, ...] = field(default_factory=tuple)
    read_escalations: tuple[ReadEscalation, ...] = field(default_factory=tuple)
    compaction_triggered: bool = False
    tool_sequence: tuple[str, ...] = field(default_factory=tuple)

    # Metrics
    prompt_tokens_estimate: int = 0
    exploration_phase_reached: str = ExplorationPhase.MAP.value

    # Cache
    cache_hits: tuple[str, ...] = field(default_factory=tuple)
    cache_misses: tuple[str, ...] = field(default_factory=tuple)

    # Run identity
    run_id: str = ""
    session_id: str = ""
    workspace: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON write."""
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "turn_index": self.turn_index,
            "timestamp": self.timestamp,
            "budget_decisions": [
                {
                    "kind": d.kind.value,
                    "estimated_tokens": d.estimated_tokens,
                    "headroom_before": d.headroom_before,
                    "headroom_after": d.headroom_after,
                    "decision": d.decision,
                    "reason": d.reason,
                }
                for d in self.budget_decisions
            ],
            "read_escalations": [
                {
                    "asset_key": r.asset_key,
                    "decision": r.decision,
                    "estimated_tokens": r.estimated_tokens,
                    "reason": r.reason,
                }
                for r in self.read_escalations
            ],
            "compaction_triggered": self.compaction_triggered,
            "tool_sequence": list(self.tool_sequence),
            "prompt_tokens_estimate": self.prompt_tokens_estimate,
            "exploration_phase_reached": self.exploration_phase_reached,
            "cache_hits": list(self.cache_hits),
            "cache_misses": list(self.cache_misses),
            "run_id": self.run_id,
            "session_id": self.session_id,
            "workspace": self.workspace,
        }


# ------------------------------------------------------------------
# Scorecard
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Scorecard:
    """Normalized quality scorecard for a strategy run.

    All sub-scores are 0.0–1.0 (higher = better).
    overall_score is a weighted average; weights are versioned in the
    evaluation strategy, not hardcoded here.
    """

    quality_score: float = 0.0
    efficiency_score: float = 0.0
    context_score: float = 0.0
    latency_score: float = 0.0
    cost_score: float = 0.0
    overall_score: float = 0.0
    bundle_id: str = ""
    profile_id: str = ""
    profile_hash: str = ""
    turn_index: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_score": self.quality_score,
            "efficiency_score": self.efficiency_score,
            "context_score": self.context_score,
            "latency_score": self.latency_score,
            "cost_score": self.cost_score,
            "overall_score": self.overall_score,
            "bundle_id": self.bundle_id,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "turn_index": self.turn_index,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
        }


@dataclass(frozen=True, kw_only=True)
class ScoreDiff:
    """Difference between two scorecards for A/B comparison."""

    profile_a: str
    profile_b: str
    quality_delta: float = 0.0
    efficiency_delta: float = 0.0
    context_delta: float = 0.0
    latency_delta: float = 0.0
    cost_delta: float = 0.0
    overall_delta: float = 0.0
    winner: str = ""  # "a" | "b" | "tie"


# ------------------------------------------------------------------
# Resolved Strategy
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ResolvedStrategy:
    """The fully resolved strategy for a run: profile + bundle + hash."""

    profile: StrategyProfile
    bundle: StrategyBundle
    profile_hash: str
    overrides_applied: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile.profile_id,
            "bundle_id": self.bundle.bundle_id,
            "profile_hash": self.profile_hash,
            "overrides_applied": self.overrides_applied,
        }


__all__ = [
    # Result types
    "BudgetDecision",
    # Dataclasses
    "BudgetDecisionKind",
    "CacheStrategyPort",
    "CompactionDecision",
    "CompactionResult",
    # Protocols
    "CompactionStrategyPort",
    "EvaluationStrategyPort",
    # Enums (imported from exploration_policy.py)
    "ExpansionDecision",
    "ExpansionDecisionResult",
    "ExplorationPhase",
    "ExplorationStrategyPort",
    "HistoryMaterialization",
    "HistoryMaterializationStrategyPort",
    # Metadata
    "ProfileMetadata",
    "ReadEscalation",
    "ReadEscalationDecision",
    "ReadEscalationDecisionResult",
    "ReadEscalationStrategyPort",
    # Core schema
    "ResolvedStrategy",
    "ScoreDiff",
    "Scorecard",
    "SessionContinuityStrategyPort",
    "StrategyBundle",
    "StrategyProfile",
    "StrategyReceipt",
]
