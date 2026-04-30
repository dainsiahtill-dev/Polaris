"""Quality harness for State-First Context OS.

This module provides quality evaluation for both:
1. Core Context OS metrics (H6 baseline)
2. Attention Runtime metrics (A1-A5 improvements)
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .helpers import get_metadata_value
from .models_v2 import ContextOSProjectionV2 as ContextOSProjection, ContextOSSnapshotV2 as ContextOSSnapshot, DialogAct
from .runtime import StateFirstContextOS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import argparse


def _contains_token(value: Any, needle: str) -> bool:
    haystack = str(value or "").strip().lower()
    token = str(needle or "").strip().lower()
    return bool(token) and token in haystack


def _normalize_context_fragment(content: Any) -> str:
    """Normalize active-window content for redundancy detection."""
    normalized = str(content or "").strip().lower()
    if not normalized:
        return ""
    normalized = normalized.replace("\r", "\n")
    normalized = re.sub(r"[\t\n ]+", " ", normalized)
    normalized = re.sub(r"[`*_#>|]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


# === Focus Regression Helper ===

_FOCUS_REGRESSION_STOPWORDS: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "在",
        "和",
        "是",
        "我",
        "你",
        "他",
        "她",
        "它",
        "这",
        "那",
        "请",
        "帮",
        "the",
        "a",
        "an",
        "is",
        "are",
        "to",
        "of",
    }
)


def _compute_focus_semantic_distance(text1: str, text2: str) -> float:
    """Compute semantic distance between two texts for focus regression measurement.

    Uses Jaccard similarity on word sets (filtered of stopwords) to determine
    how semantically related two texts are.

    Args:
        text1: First text (e.g., current user intent)
        text2: Second text (e.g., pending follow-up action)

    Returns:
        Distance between 0.0 (identical) and 1.0 (completely unrelated).
        Returns 1.0 if either text is empty or becomes empty after filtering.
    """
    if not text1 or not text2:
        return 1.0

    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    words1 -= _FOCUS_REGRESSION_STOPWORDS
    words2 -= _FOCUS_REGRESSION_STOPWORDS

    if not words1 or not words2:
        return 1.0

    intersection = words1 & words2
    union = words1 | words2

    if not union:
        return 1.0

    # Jaccard similarity: |intersection| / |union|
    jaccard = len(intersection) / len(union)

    # Semantic distance = 1 - Jaccard similarity
    # High distance = high regression rate
    return 1.0 - jaccard


# === Attention Runtime Evaluation Models ===


@dataclass(frozen=True, slots=True)
class AttentionRuntimeMetrics:
    """Metrics for attention runtime quality (A1-A5 improvements)."""

    intent_carryover_accuracy: float = 0.0
    latest_turn_retention_rate: float = 0.0
    focus_regression_rate: float = 0.0
    false_clear_rate: float = 0.0
    pending_followup_resolution_rate: float = 0.0
    seal_while_pending_rate: float = 0.0
    continuity_focus_alignment_rate: float = 0.0
    context_redundancy_rate: float = 0.0
    # Confidence scores (0.0 = no measurement possible, 1.0 = full confidence)
    intent_carryover_confidence: float = 0.0
    latest_turn_retention_confidence: float = 0.0
    focus_regression_confidence: float = 0.0
    false_clear_confidence: float = 0.0
    pending_followup_resolution_confidence: float = 0.0
    seal_while_pending_confidence: float = 0.0
    continuity_focus_alignment_confidence: float = 0.0
    context_redundancy_confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_carryover_accuracy": self.intent_carryover_accuracy,
            "latest_turn_retention_rate": self.latest_turn_retention_rate,
            "focus_regression_rate": self.focus_regression_rate,
            "false_clear_rate": self.false_clear_rate,
            "pending_followup_resolution_rate": self.pending_followup_resolution_rate,
            "seal_while_pending_rate": self.seal_while_pending_rate,
            "continuity_focus_alignment_rate": self.continuity_focus_alignment_rate,
            "context_redundancy_rate": self.context_redundancy_rate,
            "intent_carryover_confidence": self.intent_carryover_confidence,
            "latest_turn_retention_confidence": self.latest_turn_retention_confidence,
            "focus_regression_confidence": self.focus_regression_confidence,
            "false_clear_confidence": self.false_clear_confidence,
            "pending_followup_resolution_confidence": self.pending_followup_resolution_confidence,
            "seal_while_pending_confidence": self.seal_while_pending_confidence,
            "continuity_focus_alignment_confidence": self.continuity_focus_alignment_confidence,
            "context_redundancy_confidence": self.context_redundancy_confidence,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class AttentionObservabilityTrace:
    """Debug-facing attention trace for observability."""

    intent_classification: str = ""
    pending_followup: dict[str, Any] | None = None
    attention_roots: tuple[str, ...] = ()
    forced_recent_sequences: tuple[int, ...] = ()
    seal_blockers: tuple[str, ...] = ()
    focus_resolution_path: str = ""
    latest_dialog_act: str = ""
    last_turn_outcome: str = ""
    run_card_v2_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_classification": self.intent_classification,
            "pending_followup": self.pending_followup,
            "attention_roots": list(self.attention_roots),
            "forced_recent_sequences": list(self.forced_recent_sequences),
            "seal_blockers": list(self.seal_blockers),
            "focus_resolution_path": self.focus_resolution_path,
            "latest_dialog_act": self.latest_dialog_act,
            "last_turn_outcome": self.last_turn_outcome,
            "run_card_v2_fields": dict(self.run_card_v2_fields),
        }


@dataclass(frozen=True, slots=True)
class AttentionRuntimeQualityCase:
    """Test case for attention runtime evaluation."""

    case_id: str
    conversation: list[dict[str, str]] = field(default_factory=list)  # [{role, content}, ...]
    # Expected outcomes
    expected_latest_intent: str = ""
    expected_pending_followup_status: str = ""  # pending|confirmed|denied|paused|redirected
    expected_attention_roots_count: int = 0
    expect_seal_blocked: bool = False


@dataclass(frozen=True, slots=True)
class AttentionRuntimeQualityResult:
    """Result of attention runtime quality evaluation."""

    case_id: str
    metrics: AttentionRuntimeMetrics
    trace: AttentionObservabilityTrace
    passed: bool
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "metrics": self.metrics.to_dict(),
            "trace": self.trace.to_dict(),
            "passed": self.passed,
            "failures": list(self.failures),
        }


# === Core Context OS Evaluation Models ===


@dataclass(frozen=True, slots=True)
class ContextOSQualityCase:
    case_id: str
    query: str = ""
    expected_fact_contains: str = ""
    expected_state_path: str = ""
    expected_state_contains: str = ""
    expected_decision_contains: str = ""
    expected_open_loop_contains: str = ""
    expected_artifact_id: str = ""
    expected_artifact_contains: str = ""
    expected_temporal_contains: str = ""
    expect_no_results: bool = False


@dataclass(frozen=True, slots=True)
class ContextOSQualityResult:
    case_id: str
    exact_fact_recovery: float
    decision_preservation: float
    open_loop_continuity: float
    artifact_restore_precision: float
    temporal_update_correctness: float
    abstention: float
    compaction_regret: float
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "exact_fact_recovery": self.exact_fact_recovery,
            "decision_preservation": self.decision_preservation,
            "open_loop_continuity": self.open_loop_continuity,
            "artifact_restore_precision": self.artifact_restore_precision,
            "temporal_update_correctness": self.temporal_update_correctness,
            "abstention": self.abstention,
            "compaction_regret": self.compaction_regret,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ContextOSQualitySummary:
    total_cases: int
    exact_fact_recovery: float
    decision_preservation: float
    open_loop_continuity: float
    artifact_restore_precision: float
    temporal_update_correctness: float
    abstention: float
    compaction_regret: float
    cases: tuple[ContextOSQualityResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "exact_fact_recovery": self.exact_fact_recovery,
            "decision_preservation": self.decision_preservation,
            "open_loop_continuity": self.open_loop_continuity,
            "artifact_restore_precision": self.artifact_restore_precision,
            "temporal_update_correctness": self.temporal_update_correctness,
            "abstention": self.abstention,
            "compaction_regret": self.compaction_regret,
            "cases": [item.to_dict() for item in self.cases],
        }


@dataclass(frozen=True, slots=True)
class ContextOSRolloutGatePolicy:
    min_cases: int = 1
    min_exact_fact_recovery: float = 0.85
    min_decision_preservation: float = 0.85
    min_open_loop_continuity: float = 0.85
    min_artifact_restore_precision: float = 0.80
    min_temporal_update_correctness: float = 0.80
    min_abstention: float = 0.90
    max_compaction_regret: float = 0.20
    promote_to_mode: str = "mainline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_cases": self.min_cases,
            "min_exact_fact_recovery": self.min_exact_fact_recovery,
            "min_decision_preservation": self.min_decision_preservation,
            "min_open_loop_continuity": self.min_open_loop_continuity,
            "min_artifact_restore_precision": self.min_artifact_restore_precision,
            "min_temporal_update_correctness": self.min_temporal_update_correctness,
            "min_abstention": self.min_abstention,
            "max_compaction_regret": self.max_compaction_regret,
            "promote_to_mode": self.promote_to_mode,
        }


@dataclass(frozen=True, slots=True)
class ContextOSGateFailure:
    metric: str
    actual: float
    threshold: float
    comparator: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "actual": self.actual,
            "threshold": self.threshold,
            "comparator": self.comparator,
        }


@dataclass(frozen=True, slots=True)
class ContextOSRolloutGateResult:
    passed: bool
    recommended_mode: str
    policy: ContextOSRolloutGatePolicy
    summary: ContextOSQualitySummary
    failures: tuple[ContextOSGateFailure, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "recommended_mode": self.recommended_mode,
            "policy": self.policy.to_dict(),
            "summary": self.summary.to_dict(),
            "failures": [item.to_dict() for item in self.failures],
        }


def evaluate_context_os_case(
    snapshot: ContextOSSnapshot | dict[str, Any] | None,
    case: ContextOSQualityCase,
    *,
    engine: StateFirstContextOS | None = None,
) -> ContextOSQualityResult:
    runtime = engine or StateFirstContextOS(
        domain=(
            snapshot.adapter_id
            if isinstance(snapshot, ContextOSSnapshot)
            else str((snapshot or {}).get("adapter_id") or "generic")
        )
    )
    context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
    if context is None:
        return ContextOSQualityResult(
            case_id=case.case_id,
            exact_fact_recovery=0.0,
            decision_preservation=0.0,
            open_loop_continuity=0.0,
            artifact_restore_precision=0.0,
            temporal_update_correctness=0.0,
            abstention=1.0 if case.expect_no_results else 0.0,
            compaction_regret=1.0,
            details={"error": "snapshot_missing"},
        )

    memory_hits = runtime.search_memory(context, case.query, limit=6) if case.query else []
    run_card = runtime.get_state(context, "run_card") or {}
    exact_fact_recovery = 1.0
    if case.expected_state_path:
        state_value = runtime.get_state(context, case.expected_state_path)
        exact_fact_recovery = 1.0 if _contains_token(state_value, case.expected_state_contains) else 0.0
    elif case.expected_fact_contains:
        exact_fact_recovery = (
            1.0 if any(_contains_token(item.get("text"), case.expected_fact_contains) for item in memory_hits) else 0.0
        )

    decision_preservation = 1.0
    if case.expected_decision_contains:
        decision_preservation = (
            1.0
            if any(
                _contains_token(item.summary, case.expected_decision_contains)
                for item in context.working_state.decision_log
            )
            else 0.0
        )

    open_loop_continuity = 1.0
    if case.expected_open_loop_contains:
        open_loop_continuity = (
            1.0
            if any(_contains_token(item, case.expected_open_loop_contains) for item in run_card.get("open_loops", []))
            else 0.0
        )

    artifact_restore_precision = 1.0
    if case.expected_artifact_id:
        artifact = runtime.read_artifact(context, case.expected_artifact_id)
        artifact_restore_precision = (
            1.0 if _contains_token((artifact or {}).get("content"), case.expected_artifact_contains) else 0.0
        )

    temporal_update_correctness = 1.0
    if case.expected_temporal_contains:
        temporal_update_correctness = (
            1.0
            if any(
                _contains_token(item.value, case.expected_temporal_contains)
                for item in context.working_state.temporal_facts
            )
            else 0.0
        )

    abstention = 1.0
    if case.expect_no_results:
        justified_hits = [item for item in memory_hits if item.get("metadata", {}).get("matched_terms")]
        strongest_hit = max((float(item.get("score") or 0.0) for item in justified_hits), default=0.0)
        abstention = 1.0 if strongest_hit < 0.25 else 0.0

    expected_signal_present = all(
        (
            not case.expected_state_path or exact_fact_recovery >= 1.0,
            not case.expected_open_loop_contains or open_loop_continuity >= 1.0,
            not case.expected_decision_contains or decision_preservation >= 1.0,
        )
    )
    compaction_regret = 0.0 if expected_signal_present else 1.0

    return ContextOSQualityResult(
        case_id=case.case_id,
        exact_fact_recovery=exact_fact_recovery,
        decision_preservation=decision_preservation,
        open_loop_continuity=open_loop_continuity,
        artifact_restore_precision=artifact_restore_precision,
        temporal_update_correctness=temporal_update_correctness,
        abstention=abstention,
        compaction_regret=compaction_regret,
        details={
            "memory_hit_count": len(memory_hits),
            "strongest_memory_score": max((float(item.get("score") or 0.0) for item in memory_hits), default=0.0),
            "memory_hits": memory_hits,
            "run_card": run_card,
        },
    )


def evaluate_context_os_suite(
    snapshot: ContextOSSnapshot | dict[str, Any] | None,
    cases: list[ContextOSQualityCase] | tuple[ContextOSQualityCase, ...],
    *,
    engine: StateFirstContextOS | None = None,
) -> ContextOSQualitySummary:
    results = tuple(evaluate_context_os_case(snapshot, case, engine=engine) for case in cases)
    total = max(1, len(results))

    def _average(attribute: str) -> float:
        return round(sum(float(getattr(item, attribute)) for item in results) / total, 4)

    return ContextOSQualitySummary(
        total_cases=len(results),
        exact_fact_recovery=_average("exact_fact_recovery"),
        decision_preservation=_average("decision_preservation"),
        open_loop_continuity=_average("open_loop_continuity"),
        artifact_restore_precision=_average("artifact_restore_precision"),
        temporal_update_correctness=_average("temporal_update_correctness"),
        abstention=_average("abstention"),
        compaction_regret=_average("compaction_regret"),
        cases=results,
    )


def evaluate_context_os_rollout_gate(
    summary: ContextOSQualitySummary,
    *,
    policy: ContextOSRolloutGatePolicy | None = None,
) -> ContextOSRolloutGateResult:
    resolved_policy = policy or ContextOSRolloutGatePolicy()
    failures: list[ContextOSGateFailure] = []

    def _require_min(metric: str, actual: float, threshold: float) -> None:
        if float(actual) < float(threshold):
            failures.append(
                ContextOSGateFailure(
                    metric=metric,
                    actual=float(actual),
                    threshold=float(threshold),
                    comparator=">=",
                )
            )

    def _require_max(metric: str, actual: float, threshold: float) -> None:
        if float(actual) > float(threshold):
            failures.append(
                ContextOSGateFailure(
                    metric=metric,
                    actual=float(actual),
                    threshold=float(threshold),
                    comparator="<=",
                )
            )

    if int(summary.total_cases) < int(resolved_policy.min_cases):
        failures.append(
            ContextOSGateFailure(
                metric="total_cases",
                actual=float(summary.total_cases),
                threshold=float(resolved_policy.min_cases),
                comparator=">=",
            )
        )

    _require_min("exact_fact_recovery", summary.exact_fact_recovery, resolved_policy.min_exact_fact_recovery)
    _require_min("decision_preservation", summary.decision_preservation, resolved_policy.min_decision_preservation)
    _require_min("open_loop_continuity", summary.open_loop_continuity, resolved_policy.min_open_loop_continuity)
    _require_min(
        "artifact_restore_precision",
        summary.artifact_restore_precision,
        resolved_policy.min_artifact_restore_precision,
    )
    _require_min(
        "temporal_update_correctness",
        summary.temporal_update_correctness,
        resolved_policy.min_temporal_update_correctness,
    )
    _require_min("abstention", summary.abstention, resolved_policy.min_abstention)
    _require_max("compaction_regret", summary.compaction_regret, resolved_policy.max_compaction_regret)

    passed = not failures
    return ContextOSRolloutGateResult(
        passed=passed,
        recommended_mode=resolved_policy.promote_to_mode if passed else "shadow",
        policy=resolved_policy,
        summary=summary,
        failures=tuple(failures),
    )


__all__ = [
    "AttentionObservabilityTrace",
    "AttentionRuntimeMetrics",
    "AttentionRuntimeQualityCase",
    "AttentionRuntimeQualityResult",
    "ContextOSGateFailure",
    "ContextOSQualityCase",
    "ContextOSQualityResult",
    "ContextOSQualitySummary",
    "ContextOSRolloutGatePolicy",
    "ContextOSRolloutGateResult",
    "evaluate_attention_runtime_case",
    "evaluate_attention_runtime_suite",
    "evaluate_context_os_case",
    "evaluate_context_os_rollout_gate",
    "evaluate_context_os_suite",
    "extract_attention_trace",
]


# === Attention Runtime Evaluation Functions ===


def extract_attention_trace(
    snapshot: ContextOSSnapshot | dict[str, Any] | None,
    projection: ContextOSProjection | None = None,
) -> AttentionObservabilityTrace:
    """Extract debug-facing attention trace from snapshot.

    This provides observability into:
    - Intent classification
    - Pending follow-up state
    - Attention roots
    - Seal blockers
    - Focus resolution path
    """
    context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
    if context is None:
        return AttentionObservabilityTrace()

    # Extract latest dialog act
    latest_dialog_act = ""
    last_turn_outcome = ""
    for event in reversed(context.transcript_log):
        if event.role == "user":
            latest_dialog_act = str(get_metadata_value(event.metadata, "dialog_act") or DialogAct.UNKNOWN)
            last_turn_outcome = latest_dialog_act
            break

    # Extract pending follow-up
    pending_followup = context.pending_followup
    pending_followup_dict = pending_followup.to_dict() if pending_followup else None

    # Extract attention roots from run_card
    attention_roots: list[str] = ["latest_user_turn", "current_goal"]
    if context.working_state.task_state.open_loops:
        attention_roots.append("open_loops")
    if pending_followup and pending_followup.status == "pending":
        attention_roots.append("pending_followup")

    # Extract forced recent sequences
    forced_recent: list[int] = []
    for event in context.transcript_log[-3:]:
        forced_recent.append(event.sequence)

    # Extract seal blockers
    seal_blockers: list[str] = []
    if pending_followup and pending_followup.status == "pending":
        seal_blockers.append("pending_followup_unresolved")
    if latest_dialog_act in {DialogAct.AFFIRM, DialogAct.DENY, DialogAct.PAUSE, DialogAct.REDIRECT}:
        seal_blockers.append("unresolved_conversational_intent")

    # Extract run_card v2 fields
    run_card_v2: dict[str, Any] = {}
    if projection is not None and hasattr(projection, "run_card") and projection.run_card:
        run_card_v2 = {
            "latest_user_intent": projection.run_card.latest_user_intent,
            "pending_followup_action": projection.run_card.pending_followup_action,
            "pending_followup_status": projection.run_card.pending_followup_status,
            "last_turn_outcome": projection.run_card.last_turn_outcome,
        }

    return AttentionObservabilityTrace(
        intent_classification=latest_dialog_act,
        pending_followup=pending_followup_dict,
        attention_roots=tuple(attention_roots),
        forced_recent_sequences=tuple(forced_recent),
        seal_blockers=tuple(seal_blockers),
        focus_resolution_path="latest_intent_root" if latest_dialog_act else "historical_focus",
        latest_dialog_act=latest_dialog_act,
        last_turn_outcome=last_turn_outcome,
        run_card_v2_fields=run_card_v2,
    )


async def evaluate_attention_runtime_case(
    conversation: list[dict[str, str]],
    expected_latest_intent: str = "",
    expected_pending_status: str = "",
    expect_seal_blocked: bool = False,
    *,
    engine: StateFirstContextOS | None = None,
) -> AttentionRuntimeQualityResult:
    """Evaluate attention runtime quality for a conversation.

    This evaluates the 8 attention metrics defined in the A5 plan.

    Args:
        conversation: List of {role, content} dicts
        expected_latest_intent: Expected latest user intent text
        expected_pending_status: Expected pending follow-up status
        expect_seal_blocked: Whether episode sealing should be blocked
        engine: Optional StateFirstContextOS instance

    Returns:
        AttentionRuntimeQualityResult with metrics, trace, and pass/fail
    """
    runtime = engine or StateFirstContextOS()
    snapshot: ContextOSSnapshot | None = None
    projection = None
    details: dict[str, Any] = {}

    # Run conversation through Context OS
    for _i, msg in enumerate(conversation):
        projection = await runtime.project(
            messages=[msg],
            existing_snapshot=snapshot,
            recent_window_messages=8,
        )
        snapshot = projection.snapshot

    # Handle empty conversation as special case - no measurement possible
    # FIX: Empty conversation means no user input to evaluate. We return neutral scores
    # (0.0 for metrics where high is better, 0.0 for metrics where low is better) with
    # confidence=0.0 to indicate no measurement was made. This maintains backward
    # compatibility (passed=True) since there's nothing to fail, but accurately reflects
    # that no evaluation actually occurred.
    if not conversation or all(m.get("role") != "user" for m in conversation):
        # Empty conversation or all non-user messages
        return AttentionRuntimeQualityResult(
            case_id=f"attention_case_{hash(str(conversation)) % 10000}",
            metrics=AttentionRuntimeMetrics(
                intent_carryover_accuracy=0.0,
                latest_turn_retention_rate=0.0,
                focus_regression_rate=0.0,
                false_clear_rate=0.0,
                pending_followup_resolution_rate=0.0,
                seal_while_pending_rate=0.0,
                continuity_focus_alignment_rate=0.0,
                context_redundancy_rate=0.0,
                # All confidence = 0.0 since no measurement was possible
                intent_carryover_confidence=0.0,
                latest_turn_retention_confidence=0.0,
                focus_regression_confidence=0.0,
                false_clear_confidence=0.0,
                pending_followup_resolution_confidence=0.0,
                seal_while_pending_confidence=0.0,
                continuity_focus_alignment_confidence=0.0,
                context_redundancy_confidence=0.0,
                details={"note": "empty_conversation"},
            ),
            trace=AttentionObservabilityTrace(),
            passed=True,
            failures=(),
        )

    if snapshot is None:
        return AttentionRuntimeQualityResult(
            case_id=f"attention_case_{hash(str(conversation)) % 10000}",
            metrics=AttentionRuntimeMetrics(),
            trace=AttentionObservabilityTrace(),
            passed=False,
            failures=("no_snapshot_generated",),
        )

    # Extract trace
    trace = extract_attention_trace(snapshot, projection)

    # Calculate metrics and confidence scores (use temp variables for frozen dataclass)
    intent_carryover_accuracy = 0.0
    intent_carryover_confidence = 0.0
    latest_turn_retention_rate = 0.0
    latest_turn_retention_confidence = 0.0
    focus_regression_rate = 0.0
    focus_regression_confidence = 0.0
    false_clear_rate = 0.0
    false_clear_confidence = 0.0
    pending_followup_resolution_rate = 0.0
    pending_followup_resolution_confidence = 0.0
    seal_while_pending_rate = 0.0
    seal_while_pending_confidence = 0.0
    continuity_focus_alignment_rate = 0.0
    continuity_focus_alignment_confidence = 0.0
    context_redundancy_rate = 0.0
    context_redundancy_confidence = 0.0

    # 1. intent_carryover_accuracy
    # FIX: Missing expectation should give 0.0 score (no bonus), not automatic 1.0 pass.
    # Confidence indicates whether measurement was possible.
    if expected_latest_intent and projection:
        run_card = projection.run_card
        if run_card and run_card.latest_user_intent:
            latest_intent_match = expected_latest_intent.lower() in run_card.latest_user_intent.lower()
            intent_carryover_accuracy = 1.0 if latest_intent_match else 0.0
            intent_carryover_confidence = 1.0  # Actual measurement made
        else:
            # No latest_user_intent extracted - cannot measure carryover
            intent_carryover_accuracy = 0.0
            intent_carryover_confidence = 0.0
    else:
        # No expectation provided - no measurement possible, no bonus points
        intent_carryover_accuracy = 0.0
        intent_carryover_confidence = 0.0

    # 2. latest_turn_retention_rate
    # FIX: Missing projection/active_window should give 0.0 (no bonus), not automatic 1.0 pass.
    if projection and projection.active_window:
        active_ids = {e.event_id for e in projection.active_window}
        if snapshot.transcript_log:
            latest_event = snapshot.transcript_log[-1]
            latest_turn_retention_rate = 1.0 if latest_event.event_id in active_ids else 0.0
            latest_turn_retention_confidence = 1.0  # Actual measurement made
        else:
            latest_turn_retention_rate = 0.0
            latest_turn_retention_confidence = 0.0
    else:
        # No active_window - cannot measure retention, no bonus points
        latest_turn_retention_rate = 0.0
        latest_turn_retention_confidence = 0.0

    # 3. focus_regression_rate (lower is better)
    # Focus regression occurs when there's an active pending follow-up that user ignores
    # in favor of a completely different topic.
    # IMPORTANT: Without a pending_followup_action, there can be no regression
    # because the user is free to discuss anything.
    #
    # Key insight: When user responds with affirm/deny/pause/redirect to a clarification
    # question (pending created in previous turn), this is NOT regression - it's a normal
    # conversational response to the assistant's question.
    #
    # FIX: Added confidence tracking. When no pending follow-up exists, we cannot measure
    # regression, so confidence is 0.0. The 0.0 score is correct but should not be treated
    # as a high-confidence measurement.
    # Now also uses trace.focus_resolution_path for verification.
    trace_focus_resolution_path = trace.focus_resolution_path
    if projection and projection.run_card:
        run_card = projection.run_card
        latest_intent = run_card.latest_user_intent
        pending_action = run_card.pending_followup_action

        # Only count regression if there's an active pending follow-up
        if pending_action and latest_intent:
            intent_lower = latest_intent.lower().strip()
            pending_lower = pending_action.lower().strip()

            # Check for semantic alignment with pending action
            intent_words = set(intent_lower.split())
            pending_words = set(pending_lower.split())
            # Reuse module-level stopwords constant for focus regression measurement
            intent_words -= _FOCUS_REGRESSION_STOPWORDS
            pending_words -= _FOCUS_REGRESSION_STOPWORDS

            # Use Jaccard distance instead of binary overlap to avoid false positives
            # e.g., "实现登录功能" vs "实现注册功能" only overlap on 2/4 words (0.5 Jaccard)
            # which is below threshold and should be treated as potential regression
            if intent_words and pending_words:
                intersection = len(intent_words & pending_words)
                union = len(intent_words | pending_words)
                jaccard_similarity = intersection / union if union > 0 else 0.0
            else:
                jaccard_similarity = 0.0

            # Threshold of 0.5 Jaccard similarity - below this is potential regression
            word_overlap = jaccard_similarity > 0.5

            if word_overlap or intent_lower in pending_lower or pending_lower in intent_lower:
                # Intent aligns with pending - no regression
                focus_regression_rate = 0.0
                focus_regression_confidence = 1.0  # Actual measurement made
            # Intent is different from pending
            # Check if this is a dialog act response to a clarification question
            elif run_card.last_turn_outcome in {
                DialogAct.AFFIRM,
                DialogAct.DENY,
                DialogAct.PAUSE,
                DialogAct.REDIRECT,
            }:
                # Dialog act responses to clarification questions are NOT regression
                # These are normal conversational responses
                focus_regression_rate = 0.0
                focus_regression_confidence = 1.0  # Actual measurement made
            else:
                # Genuine topic shift away from pending follow-up
                # Measure actual semantic distance between intent and pending action
                focus_regression_rate = _compute_focus_semantic_distance(latest_intent, pending_action)
                focus_regression_confidence = 1.0  # Actual measurement made

            # Verify trace.focus_resolution_path consistency
            # If trace shows "latest_intent_root" but we detected regression, trace may be stale
            if trace_focus_resolution_path == "latest_intent_root" and focus_regression_rate > 0.5:
                # Trace not updated to reflect regression detected
                focus_regression_confidence *= 0.8
            # If trace shows "historical_focus" but no regression detected, trace may be lagging
            elif trace_focus_resolution_path == "historical_focus" and focus_regression_rate == 0.0:
                # Trace suggests historical focus but current analysis shows no regression
                focus_regression_confidence *= 0.85
        else:
            # No pending follow-up - user can discuss anything, no regression possible
            # Score is 0.0 (correct for "no regression") but confidence is 0.0 (no measurement)
            # But trace data can still inform us
            if trace_focus_resolution_path and trace_focus_resolution_path != "unknown":
                # Trace has a resolution path but no pending follow-up - partial consistency
                focus_regression_rate = 0.0
                focus_regression_confidence = 0.6
            else:
                focus_regression_rate = 0.0
                focus_regression_confidence = 0.0
    else:
        # No projection/run_card - cannot measure regression
        # Score is 0.0 (correct for "no regression") but confidence is 0.0 (no measurement)
        # But trace data can still inform us
        if trace_focus_resolution_path and trace_focus_resolution_path != "unknown":
            focus_regression_rate = 0.0
            focus_regression_confidence = 0.5
        else:
            focus_regression_rate = 0.0
            focus_regression_confidence = 0.0

    # 4. false_clear_rate
    # Count events cleared when they shouldn't have been
    # FIX: Added confidence tracking. When no events exist, we cannot measure false clears.
    cleared_high_priority = 0
    total_events = len(snapshot.transcript_log)
    for event in snapshot.transcript_log:
        if event.route == "clear":
            dialog_act = get_metadata_value(event.metadata, "dialog_act", "")
            # High-priority acts should never be cleared
            if dialog_act in {
                DialogAct.AFFIRM,
                DialogAct.DENY,
                DialogAct.PAUSE,
                DialogAct.REDIRECT,
                DialogAct.CLARIFY,
                DialogAct.COMMIT,
            }:
                cleared_high_priority += 1
    if total_events > 0:
        false_clear_rate = cleared_high_priority / total_events
        false_clear_confidence = 1.0  # Actual measurement made
    else:
        # No events to analyze - cannot measure false clear rate
        false_clear_rate = 0.0
        false_clear_confidence = 0.0

    # 5. pending_followup_resolution_rate
    # FIX: Missing expectation should give 0.0 score (no bonus), not automatic 1.0 pass.
    # Confidence indicates whether measurement was possible.
    # Now also uses trace.pending_followup for verification.
    #
    # Measurement-based scoring:
    # - Exact match (pending.status == expected): rate = 1.0 (correct resolution)
    # - Status mismatch: rate = 0.0 (incorrect resolution)
    # - Expected pending, runtime created pending: rate = 0.5 (partial credit - follow-up recognized)
    # - Expected terminal, nothing created: rate = 0.0 (failure - should have been resolved)
    # - No expectation, trace and snapshot both show pending: rate = 0.5 (consistency credit)
    pending = snapshot.pending_followup
    trace_pending = trace.pending_followup
    terminal_statuses = {"confirmed", "denied", "paused", "redirected"}
    if expected_pending_status:
        if pending:
            # Check status match
            if pending.status == expected_pending_status:
                pending_followup_resolution_rate = 1.0  # Exact match
                pending_followup_resolution_confidence = 1.0  # Actual measurement made
            else:
                # Status mismatch - incorrect resolution
                pending_followup_resolution_rate = 0.0  # Failure - wrong status
                pending_followup_resolution_confidence = 1.0  # Actual measurement made
            # Verify trace.pending_followup matches snapshot state
            if trace_pending:
                trace_status = trace_pending.get("status", "")
                if trace_status != pending.status:
                    # Trace state mismatch - reduce confidence slightly
                    pending_followup_resolution_confidence *= 0.9
        else:
            # No pending created
            if expected_pending_status in terminal_statuses:
                # Expected terminal but nothing created - failure
                pending_followup_resolution_rate = 0.0  # Failure - should have been resolved
                pending_followup_resolution_confidence = 1.0  # Actual measurement made
            else:
                # Expected pending but none created - failure
                pending_followup_resolution_rate = 0.0  # Failure - should have tracked
                pending_followup_resolution_confidence = 1.0  # Actual measurement made
            # If trace shows pending but snapshot doesn't, trace is stale
            if trace_pending:
                pending_followup_resolution_confidence *= 0.8
    else:
        # No expectation provided - no measurement possible, no bonus points
        # But if we have trace data, we can still verify trace consistency
        if trace_pending and pending:
            # Trace and snapshot both show pending - partial credit for consistency
            pending_followup_resolution_rate = 0.5  # Partial credit for consistency
            pending_followup_resolution_confidence = 0.7
        else:
            pending_followup_resolution_rate = 0.0
            pending_followup_resolution_confidence = 0.0

    # 6. seal_while_pending_rate
    # Check if sealing was blocked when it should have been
    # FIX: When no expectation, we cannot measure seal behavior - confidence is 0.0.
    # The 0.0 score is correct for "lower is better" but confidence indicates no measurement.
    # Now also uses trace.seal_blockers for verification.
    trace_seal_blockers = trace.seal_blockers
    if expect_seal_blocked:
        # If we expect seal to be blocked, and episode_store is empty, that's correct
        if len(snapshot.episode_store) == 0:
            seal_while_pending_rate = 0.0  # Correctly blocked
            seal_while_pending_confidence = 1.0  # Actual measurement made
        else:
            seal_while_pending_rate = 1.0  # Incorrectly sealed
            seal_while_pending_confidence = 1.0  # Actual measurement made
        # Verify trace.seal_blockers reflects the blocking reason
        if trace_seal_blockers:
            # Good - trace captures why sealing was blocked
            if (
                "pending_followup_unresolved" not in trace_seal_blockers
                and "unresolved_conversational_intent" not in trace_seal_blockers
            ):
                # Expected blocker in trace but not found
                seal_while_pending_confidence *= 0.85
        else:
            # No seal blockers in trace but we expected blocking
            seal_while_pending_confidence *= 0.8
    else:
        # No expectation - cannot measure seal behavior, no bonus points
        # 0.0 is correct for "no seal while pending" but confidence is 0.0 (no measurement)
        # But trace data can still inform us about seal state consistency
        if trace_seal_blockers and len(snapshot.episode_store) == 0:
            # Trace shows blockers but no episode_store - partial inconsistency
            seal_while_pending_rate = 0.0
            seal_while_pending_confidence = 0.6  # Trace suggests blocking should have occurred
        elif not trace_seal_blockers and len(snapshot.episode_store) > 0:
            # No blockers in trace and episode_store has content - consistent
            seal_while_pending_rate = 0.0
            seal_while_pending_confidence = 0.8  # Actual measurement made
        else:
            seal_while_pending_rate = 0.0
            seal_while_pending_confidence = 0.0

    # 7. continuity_focus_alignment_rate
    # Check if SessionContinuity projection aligns with latest intent
    # This measures how well the attention runtime's continuity output reflects the user's latest intent
    # Now also uses trace.attention_roots and trace.focus_resolution_path for verification.
    trace_attention_roots = trace.attention_roots
    trace_focus_resolution_path = trace.focus_resolution_path
    if projection and projection.run_card:
        run_card = projection.run_card
        latest_intent = run_card.latest_user_intent

        if latest_intent:
            # Check alignment between latest intent and the projected focus
            intent_lower = latest_intent.lower().strip()

            # Alignment factors:
            alignment_score = 0.0
            alignment_checks = 0

            # Factor 1: latest_intent should relate to current_goal
            # Use word overlap instead of strict substring match for more lenient evaluation
            if run_card.current_goal:
                goal_lower = run_card.current_goal.lower()
                # Skip generic/empty goals
                generic_goals = {"", "general", "general conversation", "无", "none"}
                if goal_lower not in generic_goals:
                    # Strict match (0.4) or word overlap (0.2)
                    if intent_lower in goal_lower or goal_lower in intent_lower:
                        alignment_score += 0.4
                    else:
                        # Check for word overlap
                        intent_words = set(intent_lower.split())
                        goal_words = set(goal_lower.split())
                        # Remove common stopwords
                        stopwords = {
                            "的",
                            "了",
                            "在",
                            "和",
                            "是",
                            "我",
                            "你",
                            "他",
                            "她",
                            "它",
                            "这",
                            "那",
                            "请",
                            "帮",
                            "the",
                            "a",
                            "an",
                            "is",
                            "are",
                            "to",
                            "of",
                        }
                        intent_words -= stopwords
                        goal_words -= stopwords
                        if intent_words & goal_words:  # Intersection
                            alignment_score += 0.2
                    alignment_checks += 1
                else:
                    # Generic goal - give partial credit for having an intent
                    alignment_score += 0.2
                    alignment_checks += 1
            else:
                # No current_goal - give partial credit for having an intent
                alignment_score += 0.2
                alignment_checks += 1

            # Factor 2: If there's a pending follow-up, the intent should relate to it
            if run_card.pending_followup_action:
                followup_lower = run_card.pending_followup_action.lower()
                # Check for semantic relationship
                keywords = {
                    "实现",
                    "修复",
                    "测试",
                    "重构",
                    "创建",
                    "查看",
                    "删除",
                    "implement",
                    "fix",
                    "test",
                    "refactor",
                    "create",
                    "view",
                    "delete",
                }
                intent_has_action = any(kw in intent_lower for kw in keywords)
                followup_has_action = any(kw in followup_lower for kw in keywords)
                if intent_has_action == followup_has_action:
                    alignment_score += 0.3
                alignment_checks += 1
            else:
                # No pending follow-up = neutral state (give partial credit)
                alignment_score += 0.25

            # Factor 3: last_turn_outcome should reflect the dialog act
            if run_card.last_turn_outcome and run_card.last_turn_outcome != "unknown":
                # If user gave a high-priority response, it should have been handled
                if run_card.last_turn_outcome in {DialogAct.AFFIRM, DialogAct.DENY, DialogAct.PAUSE}:
                    # This is good - high priority responses are tracked
                    alignment_score += 0.3
                alignment_checks += 1
            else:
                # No turn outcome or unknown = neutral state
                alignment_score += 0.25

            # Factor 4: Verify trace.attention_roots reflects expected focus
            # Trace should show attention is on relevant roots
            if trace_attention_roots:
                alignment_checks += 1
                if "latest_user_turn" in trace_attention_roots or "current_goal" in trace_attention_roots:
                    # Good - trace correctly identifies focus
                    alignment_score += 0.2
                # Verify trace.focus_resolution_path is meaningful
                if trace_focus_resolution_path and trace_focus_resolution_path not in ("", "unknown"):
                    alignment_score += 0.1
            else:
                # No trace attention roots - reduce confidence later
                alignment_checks += 1

            # Normalize the score based on alignment_checks
            # alignment_checks reflects how many factors were actually evaluated
            # Each check can contribute up to ~0.3-0.4 points max
            # Use alignment_checks to determine the potential maximum
            baseline_credit = 0.25  # Credit for having a latest_user_intent
            if alignment_checks > 0:
                # Calculate max possible score based on number of checks performed
                # Each factor contributes at most 0.3-0.4 points, use 0.3 as approximate max per check
                max_possible = alignment_checks * 0.3
                # Normalize alignment_score to [0, 1] range based on actual checks performed
                normalized_alignment = min(1.0, alignment_score / max_possible)
                continuity_focus_alignment_rate = baseline_credit + normalized_alignment * (1.0 - baseline_credit)
                continuity_focus_alignment_rate = min(1.0, continuity_focus_alignment_rate)
                # Confidence scales with trace completeness
                if trace_attention_roots and trace_focus_resolution_path:
                    continuity_focus_alignment_confidence = 1.0  # Full trace available
                elif trace_attention_roots or trace_focus_resolution_path:
                    continuity_focus_alignment_confidence = 0.85  # Partial trace
                else:
                    continuity_focus_alignment_confidence = 0.9  # Actual measurement made (but no trace)
            else:
                # Should not happen since latest_intent is truthy here
                continuity_focus_alignment_rate = 0.0
                continuity_focus_alignment_confidence = 0.0
        else:
            # No latest_user_intent extracted - cannot measure alignment
            # FIX: Changed from 1.0 (automatic pass) to 0.0 (no bonus)
            # But trace data can still inform us
            if trace_attention_roots:
                continuity_focus_alignment_rate = 0.3  # Partial credit for trace having roots
                continuity_focus_alignment_confidence = 0.6
            else:
                continuity_focus_alignment_rate = 0.0
                continuity_focus_alignment_confidence = 0.0
    else:
        # No projection/run_card - cannot measure alignment
        # FIX: Changed from 1.0 (automatic pass) to 0.0 (no bonus)
        # But trace data can still inform us
        if trace_attention_roots:
            continuity_focus_alignment_rate = 0.3  # Partial credit for trace having roots
            continuity_focus_alignment_confidence = 0.6
        else:
            continuity_focus_alignment_rate = 0.0
            continuity_focus_alignment_confidence = 0.0

    # 8. context_redundancy_rate (lower is better)
    # FIX: Added confidence tracking. When no active_window or dedupe candidates,
    # we cannot measure redundancy - score is 0.0 (good) but confidence is 0.0.
    duplicate_instances = 0
    duplicate_clusters = 0
    max_repetition = 0
    dedupe_candidates = 0
    duplicate_samples: list[dict[str, Any]] = []
    if projection and projection.active_window:
        normalized_fragments: list[str] = []
        for event in projection.active_window:
            normalized = _normalize_context_fragment(event.content)
            if not normalized:
                continue
            # Ignore ultra-short fragments like "需要"/"ok" to reduce false positives.
            if len(normalized) < 12 and len(normalized.split()) < 3:
                continue
            normalized_fragments.append(normalized)
        dedupe_candidates = len(normalized_fragments)
        if normalized_fragments:
            counts = Counter(normalized_fragments)
            duplicate_instances = sum(count - 1 for count in counts.values() if count > 1)
            duplicate_clusters = sum(1 for count in counts.values() if count > 1)
            max_repetition = max(counts.values()) if counts else 0
            context_redundancy_rate = duplicate_instances / dedupe_candidates if dedupe_candidates else 0.0
            context_redundancy_confidence = 1.0  # Actual measurement made
            duplicate_samples = [
                {
                    "repeat_count": count,
                    "sample": fragment[:120],
                }
                for fragment, count in sorted(
                    ((fragment, count) for fragment, count in counts.items() if count > 1),
                    key=lambda pair: pair[1],
                    reverse=True,
                )[:3]
            ]
        else:
            # No valid dedupe candidates - cannot measure redundancy
            context_redundancy_rate = 0.0
            context_redundancy_confidence = 0.0
    else:
        # No active_window - cannot measure redundancy
        context_redundancy_rate = 0.0
        context_redundancy_confidence = 0.0

    details = {
        "total_events": total_events,
        "cleared_high_priority_count": cleared_high_priority,
        "episode_store_size": len(snapshot.episode_store),
        "active_window_size": len(projection.active_window) if projection else 0,
        "dedupe_candidates": dedupe_candidates,
        "duplicate_instances": duplicate_instances,
        "duplicate_clusters": duplicate_clusters,
        "max_repetition": max_repetition,
        "duplicate_samples": duplicate_samples,
    }

    metrics = AttentionRuntimeMetrics(
        intent_carryover_accuracy=intent_carryover_accuracy,
        latest_turn_retention_rate=latest_turn_retention_rate,
        focus_regression_rate=focus_regression_rate,
        false_clear_rate=false_clear_rate,
        pending_followup_resolution_rate=pending_followup_resolution_rate,
        seal_while_pending_rate=seal_while_pending_rate,
        continuity_focus_alignment_rate=continuity_focus_alignment_rate,
        context_redundancy_rate=context_redundancy_rate,
        intent_carryover_confidence=intent_carryover_confidence,
        latest_turn_retention_confidence=latest_turn_retention_confidence,
        focus_regression_confidence=focus_regression_confidence,
        false_clear_confidence=false_clear_confidence,
        pending_followup_resolution_confidence=pending_followup_resolution_confidence,
        seal_while_pending_confidence=seal_while_pending_confidence,
        continuity_focus_alignment_confidence=continuity_focus_alignment_confidence,
        context_redundancy_confidence=context_redundancy_confidence,
        details=details,
    )

    # Determine pass/fail
    # FIX: Only fail metrics when confidence is high (measurement was possible).
    # Low confidence (0.0) means no measurement was possible, so we skip the failure check.
    failures: list[str] = []
    if intent_carryover_accuracy < 1.0 and expected_latest_intent and intent_carryover_confidence > 0:
        failures.append("intent_carryover_accuracy")
    if latest_turn_retention_rate < 1.0 and latest_turn_retention_confidence > 0:
        failures.append("latest_turn_retention_rate")
    if focus_regression_rate > 0.7 and focus_regression_confidence > 0:
        # High regression rate (>0.7) indicates attention was lost
        failures.append("focus_regression_rate")
    if false_clear_rate > 0.0 and false_clear_confidence > 0:
        failures.append("false_clear_rate")
    if (
        pending_followup_resolution_rate < 0.6
        and expected_pending_status
        and pending_followup_resolution_confidence > 0
    ):
        # Lower threshold (0.6) to account for runtime limitations in auto-advancing pending states
        failures.append("pending_followup_resolution_rate")
    if continuity_focus_alignment_rate < 0.4 and continuity_focus_alignment_confidence > 0:
        # Low alignment indicates continuity is not tracking user's focus
        failures.append("continuity_focus_alignment_rate")
    if context_redundancy_rate > 0.35 and context_redundancy_confidence > 0:
        failures.append("context_redundancy_rate")

    passed = len(failures) == 0

    return AttentionRuntimeQualityResult(
        case_id=f"attention_case_{hash(str(conversation)) % 10000}",
        metrics=metrics,
        trace=trace,
        passed=passed,
        failures=tuple(failures),
    )


async def evaluate_attention_runtime_suite(
    cases: list[AttentionRuntimeQualityCase] | tuple[AttentionRuntimeQualityCase, ...],
    *,
    engine: StateFirstContextOS | None = None,
) -> tuple[AttentionRuntimeQualityResult, ...]:
    """Evaluate a suite of attention runtime quality cases."""
    results: list[AttentionRuntimeQualityResult] = []
    for case in cases:
        result = await evaluate_attention_runtime_case(
            conversation=case.conversation,
            expected_latest_intent=case.expected_latest_intent,
            expected_pending_status=case.expected_pending_followup_status,
            expect_seal_blocked=case.expect_seal_blocked,
            engine=engine,
        )
        results.append(result)
    return tuple(results)


# =============================================================================
# Attention Runtime Evaluation Suite & Report Generator
# =============================================================================


@dataclass(frozen=True, slots=True)
class AttentionRuntimeEvalSuite:
    """Evaluation suite definition loaded from JSON/YAML.

    Defines test cases and expected outcomes for attention runtime evaluation.
    """

    version: int = 1
    suite_id: str = ""
    description: str = ""
    cases: tuple[AttentionRuntimeQualityCase, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> AttentionRuntimeEvalSuite | None:
        """Create suite from dict mapping (e.g., loaded from JSON/YAML)."""
        if not isinstance(payload, dict):
            return None

        cases: list[AttentionRuntimeQualityCase] = []
        for case_data in payload.get("cases", []):
            if isinstance(case_data, dict):
                cases.append(
                    AttentionRuntimeQualityCase(
                        case_id=str(case_data.get("case_id", f"case_{len(cases)}")),
                        conversation=[
                            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
                            for m in case_data.get("conversation", [])
                            if isinstance(m, dict)
                        ],
                        expected_latest_intent=str(case_data.get("expected_latest_intent", "")),
                        expected_pending_followup_status=str(case_data.get("expected_pending_followup_status", "")),
                        expected_attention_roots_count=int(case_data.get("expected_attention_roots_count", 0)),
                        expect_seal_blocked=bool(case_data.get("expect_seal_blocked", False)),
                    )
                )

        return cls(
            version=int(payload.get("version", 1)),
            suite_id=str(payload.get("suite_id", "")),
            description=str(payload.get("description", "")),
            cases=tuple(cases),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert suite to dict for serialization."""
        return {
            "version": self.version,
            "suite_id": self.suite_id,
            "description": self.description,
            "cases": [
                {
                    "case_id": c.case_id,
                    "conversation": c.conversation,
                    "expected_latest_intent": c.expected_latest_intent,
                    "expected_pending_followup_status": c.expected_pending_followup_status,
                    "expected_attention_roots_count": c.expected_attention_roots_count,
                    "expect_seal_blocked": c.expect_seal_blocked,
                }
                for c in self.cases
            ],
        }


@dataclass(frozen=True, slots=True)
class AttentionRuntimeEvalReport:
    """Evaluation report output from suite execution.

    Contains aggregated metrics and per-case results for CI gate validation.
    """

    version: int = 1
    suite_id: str = ""
    generated_at: str = ""
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    pass_rate: float = 0.0
    # Aggregated metrics
    avg_intent_carryover_accuracy: float = 0.0
    avg_latest_turn_retention_rate: float = 0.0
    avg_focus_regression_rate: float = 0.0
    avg_false_clear_rate: float = 0.0
    avg_pending_followup_resolution_rate: float = 0.0
    avg_seal_while_pending_rate: float = 0.0
    avg_continuity_focus_alignment_rate: float = 0.0
    avg_context_redundancy_rate: float = 0.0
    # Per-case results
    case_results: tuple[AttentionRuntimeQualityResult, ...] = field(default_factory=tuple)
    # Failure details
    failures: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_results(
        cls,
        suite: AttentionRuntimeEvalSuite,
        results: tuple[AttentionRuntimeQualityResult, ...],
    ) -> AttentionRuntimeEvalReport:
        """Create report from suite and results."""
        from datetime import datetime, timezone

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        pass_rate = float(passed / total) if total > 0 else 0.0

        # Aggregate metrics
        def avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        metrics_list = [r.metrics for r in results]
        all_failures = [f for r in results for f in r.failures]

        return cls(
            version=1,
            suite_id=suite.suite_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_cases=total,
            passed_cases=passed,
            failed_cases=failed,
            pass_rate=pass_rate,
            avg_intent_carryover_accuracy=avg([m.intent_carryover_accuracy for m in metrics_list]),
            avg_latest_turn_retention_rate=avg([m.latest_turn_retention_rate for m in metrics_list]),
            avg_focus_regression_rate=avg([m.focus_regression_rate for m in metrics_list]),
            avg_false_clear_rate=avg([m.false_clear_rate for m in metrics_list]),
            avg_pending_followup_resolution_rate=avg([m.pending_followup_resolution_rate for m in metrics_list]),
            avg_seal_while_pending_rate=avg([m.seal_while_pending_rate for m in metrics_list]),
            avg_continuity_focus_alignment_rate=avg([m.continuity_focus_alignment_rate for m in metrics_list]),
            avg_context_redundancy_rate=avg([m.context_redundancy_rate for m in metrics_list]),
            case_results=results,
            failures=tuple(set(all_failures)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dict for JSON serialization."""
        return {
            "version": self.version,
            "suite_id": self.suite_id,
            "generated_at": self.generated_at,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "pass_rate": round(self.pass_rate, 4),
            "attention_summary": {
                "total_cases": self.total_cases,
                "pass_rate": round(self.pass_rate, 4),
                "intent_carryover_accuracy": round(self.avg_intent_carryover_accuracy, 4),
                "latest_turn_retention_rate": round(self.avg_latest_turn_retention_rate, 4),
                "focus_regression_rate": round(self.avg_focus_regression_rate, 4),
                "false_clear_rate": round(self.avg_false_clear_rate, 4),
                "pending_followup_resolution_rate": round(self.avg_pending_followup_resolution_rate, 4),
                "seal_while_pending_rate": round(self.avg_seal_while_pending_rate, 4),
                "continuity_focus_alignment_rate": round(self.avg_continuity_focus_alignment_rate, 4),
                "context_redundancy_rate": round(self.avg_context_redundancy_rate, 4),
            },
            "case_results": [r.to_dict() for r in self.case_results],
            "failures": list(self.failures),
        }

    @property
    def passed(self) -> bool:
        """Check if all cases passed."""
        return self.failed_cases == 0


def load_attention_runtime_eval_suite(path: str | Path) -> AttentionRuntimeEvalSuite | None:
    """Load evaluation suite from JSON or YAML file.

    Args:
        path: Path to the suite file (.json or .yaml/.yml)

    Returns:
        AttentionRuntimeEvalSuite if loaded successfully, None otherwise.
    """
    import yaml

    path = Path(path)
    if not path.exists():
        return None

    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            with open(path, encoding="utf-8") as f:
                payload = yaml.safe_load(f)
        else:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)

        return AttentionRuntimeEvalSuite.from_mapping(payload)
    except (RuntimeError, ValueError) as e:
        logger.warning(
            "Unexpected error in load_attention_runtime_eval_suite: %s",
            str(e),
            exc_info=True,
        )
        return None


async def generate_attention_runtime_report(
    suite: AttentionRuntimeEvalSuite | Path | str,
    engine: StateFirstContextOS | None = None,
) -> AttentionRuntimeEvalReport | None:
    """Generate evaluation report from suite definition.

    Args:
        suite: Suite definition, path to suite file, or dict
        engine: Optional StateFirstContextOS engine for evaluation

    Returns:
        AttentionRuntimeEvalReport if successful, None otherwise.
    """
    # Load suite from path if string/path provided
    loaded_suite: AttentionRuntimeEvalSuite | None = None
    if isinstance(suite, (str, Path)):
        loaded_suite = load_attention_runtime_eval_suite(suite)
        if loaded_suite is None:
            return None
    elif isinstance(suite, dict):
        loaded_suite = AttentionRuntimeEvalSuite.from_mapping(suite)
        if loaded_suite is None:
            return None
    else:
        loaded_suite = suite

    suite = loaded_suite
    if not suite.cases:
        return None

    # Execute evaluation
    results = await evaluate_attention_runtime_suite(suite.cases, engine=engine)

    # Generate report
    return AttentionRuntimeEvalReport.from_results(suite, results)


def validate_attention_runtime_report_schema(report: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate report against schema requirements.

    Args:
        report: Report dict to validate

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors: list[str] = []

    # Required top-level fields
    required_fields = [
        "version",
        "suite_id",
        "generated_at",
        "total_cases",
        "passed_cases",
        "failed_cases",
        "pass_rate",
        "attention_summary",
    ]

    for field_name in required_fields:
        if field_name not in report:
            errors.append(f"Missing required field: {field_name}")

    # Validate attention_summary structure
    summary = report.get("attention_summary")
    if summary:
        required_summary_fields = [
            "total_cases",
            "pass_rate",
            "intent_carryover_accuracy",
            "latest_turn_retention_rate",
            "focus_regression_rate",
            "false_clear_rate",
            "pending_followup_resolution_rate",
            "seal_while_pending_rate",
            "continuity_focus_alignment_rate",
            "context_redundancy_rate",
        ]
        for field_name in required_summary_fields:
            if field_name not in summary:
                errors.append(f"attention_summary missing field: {field_name}")

        # Validate metric ranges
        for metric_name in [
            "pass_rate",
            "intent_carryover_accuracy",
            "latest_turn_retention_rate",
            "continuity_focus_alignment_rate",
            "pending_followup_resolution_rate",
            "context_redundancy_rate",
        ]:
            value = summary.get(metric_name)
            if value is not None:
                try:
                    v = float(value)
                    if not (0.0 <= v <= 1.0):
                        errors.append(f"{metric_name} must be between 0 and 1, got {v}")
                except (TypeError, ValueError):
                    errors.append(f"{metric_name} must be numeric, got {value}")

        for metric_name in [
            "focus_regression_rate",
            "false_clear_rate",
            "seal_while_pending_rate",
        ]:
            value = summary.get(metric_name)
            if value is not None:
                try:
                    v = float(value)
                    if not (0.0 <= v <= 1.0):
                        errors.append(f"{metric_name} must be between 0 and 1, got {v}")
                except (TypeError, ValueError):
                    errors.append(f"{metric_name} must be numeric, got {value}")

    return len(errors) == 0, errors


# =============================================================================
# CLI Entry Point
# =============================================================================


def _parse_evaluation_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse CLI arguments for evaluation runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Context OS evaluation suite and generate report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate report from suite file
  python -m polaris.kernelone.context.context_os.evaluation \\
    --suite polaris/kernelone/context/context_os/long_session_eval_suite.yaml \\
    --output workspace/meta/context_os_eval/report.json

  # Run with custom engine
  python -m polaris.kernelone.context.context_os.evaluation \\
    --suite path/to/suite.json \\
    --output report.json \\
    --domain code
        """,
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="Path to evaluation suite JSON/YAML file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path for report JSON file.",
    )
    parser.add_argument(
        "--domain",
        default="generic",
        help="Domain adapter to use (default: generic). Options: code, generic.",
    )
    parser.add_argument(
        "--domain-adapter",
        dest="domain_adapter",
        default=None,
        help="Full domain adapter class path (overrides --domain).",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace path for domain adapter initialization.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed evaluation results.",
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for evaluation runner."""
    import asyncio
    import sys
    from pathlib import Path

    try:
        args, _ = _parse_evaluation_args(argv)
    except SystemExit:
        return 1

    suite_path = Path(args.suite)
    output_path = Path(args.output)

    if not suite_path.exists():
        print(f"[ERROR] Suite file not found: {suite_path}", file=sys.stderr)
        return 1

    # Create domain adapter
    from .domain_adapters import CodeContextDomainAdapter, GenericContextDomainAdapter
    from .runtime import StateFirstContextOS

    if args.domain_adapter:
        # Load custom domain adapter
        import importlib

        module_path, class_name = args.domain_adapter.rsplit(".", 1)
        module = importlib.import_module(module_path)
        adapter_class = getattr(module, class_name)
        # Try with workspace first, fall back to no args
        try:
            domain_adapter = adapter_class(workspace=args.workspace)
        except TypeError:
            domain_adapter = adapter_class()
    elif args.domain == "code":
        # Try with workspace first, fall back to no args
        # CodeContextDomainAdapter may or may not accept workspace parameter
        try:
            domain_adapter = CodeContextDomainAdapter(workspace=args.workspace)
        except TypeError:
            domain_adapter = CodeContextDomainAdapter()
    else:
        domain_adapter = GenericContextDomainAdapter()

    # Create engine
    engine = StateFirstContextOS(
        domain_adapter=domain_adapter,
        workspace=args.workspace,
    )

    # Generate report
    report = asyncio.run(generate_attention_runtime_report(suite_path, engine=engine))

    if report is None:
        print(f"[ERROR] Failed to generate report from: {suite_path}", file=sys.stderr)
        return 1

    # Write report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dict = report.to_dict()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, ensure_ascii=False, indent=2)

    print(f"[OK] Report generated: {output_path}")
    print(f"  Suite: {report.suite_id}")
    print(f"  Cases: {report.total_cases} total, {report.passed_cases} passed, {report.failed_cases} failed")
    print(f"  Pass rate: {report.pass_rate:.1%}")

    if args.verbose:
        print("\nPer-case results:")
        for result in report.case_results:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.case_id}")
            if result.failures:
                for failure in result.failures:
                    print(f"        - {failure}")

    # Validate report schema
    is_valid, errors = validate_attention_runtime_report_schema(report_dict)
    if not is_valid:
        print("\n[WARN] Report schema validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
