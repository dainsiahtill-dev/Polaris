"""Attention-Aware Pipeline Stages: enhanced stages for ContextOS 3.0.

This module provides attention-aware versions of pipeline stages that use
AttentionScorer and CandidateRanker for intelligent context selection.

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Attention scores influence ranking, not contract protection.

Usage:
    # Use attention-aware window collector
    collector = AttentionAwareWindowCollector(policy, enable_attention_scoring=True)
    window_out = collector.process(budget_out, patcher_out, canon_out, inp, decision_log)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.attention.ranker import CandidateRanker
from polaris.kernelone.context.context_os.attention.scorer import AttentionScorer, ScoringContext
from polaris.kernelone.context.context_os.decision_log import (
    ContextDecisionType,
    ReasonCode,
    create_decision,
)
from polaris.kernelone.context.context_os.helpers import _estimate_tokens, get_metadata_value
from polaris.kernelone.context.context_os.model_utils import validated_replace
from polaris.kernelone.context.context_os.models_v2 import (
    RoutingClassEnum as RoutingClass,
    TranscriptEventV2 as TranscriptEvent,
)
from polaris.kernelone.context.context_os.phase_detection import TaskPhase

from .contracts import (
    BudgetPlannerOutput,
    CanonicalizerOutput,
    PipelineInput,
    StatePatcherOutput,
    WindowCollectorOutput,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.decision_log import ContextDecisionLog
    from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

logger = logging.getLogger(__name__)


class AttentionAwareWindowCollector:
    """Stage 5 (Enhanced): Collect pinned active window events with attention scoring.

    This stage replaces the static rules in WindowCollector with a multi-signal
    attention scoring system. Each candidate gets a score based on:
    - Semantic similarity to current intent
    - Recency (time decay)
    - Contract overlap (goal, acceptance criteria)
    - Evidence weight (is this an evidence event?)
    - Phase affinity (does this content type match current phase?)
    - User pin boost (explicitly pinned by user)

    Key Constraint: Attention is advisory, not authoritative.
    Contract-protected content (goal, acceptance criteria) is NEVER excluded
    by attention scoring.
    """

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        summarizer: Any | None = None,
        enable_attention_scoring: bool = True,
        current_phase: TaskPhase = TaskPhase.INTAKE,
    ) -> None:
        self._policy = policy
        self._summarizer = summarizer
        self._lazy_summarizer: Any | None = None
        self._enable_attention_scoring = enable_attention_scoring
        self._current_phase = current_phase

        # Initialize attention components if enabled
        if enable_attention_scoring:
            self._scorer = AttentionScorer()
            self._ranker = CandidateRanker(scorer=self._scorer)
        else:
            self._scorer = None
            self._ranker = None

    def _get_summarizer(self) -> Any | None:
        """Lazy initialization of TieredSummarizer for JIT compression."""
        if self._summarizer is not None:
            return self._summarizer
        if self._lazy_summarizer is not None:
            return self._lazy_summarizer
        try:
            from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

            self._lazy_summarizer = TieredSummarizer()
        except ImportError:
            logger.debug("TieredSummarizer not available for WindowCollector")
        return self._lazy_summarizer

    def _build_scoring_context(
        self,
        working_state: Any,
    ) -> ScoringContext:
        """Build ScoringContext from WorkingState."""
        current_goal = ""
        acceptance_criteria: tuple[str, ...] = ()
        hard_constraints: tuple[str, ...] = ()
        current_task_id = ""

        if working_state is not None:
            task_state = getattr(working_state, "task_state", None)
            if task_state is not None:
                # Extract current goal
                if task_state.current_goal is not None:
                    current_goal = str(task_state.current_goal.value or "")

                # Extract acceptance criteria from deliverables
                for entry in getattr(task_state, "deliverables", ()):
                    if entry.value:
                        acceptance_criteria = (*acceptance_criteria, str(entry.value))

                # Extract hard constraints from blocked_on
                for entry in getattr(task_state, "blocked_on", ()):
                    if entry.value:
                        hard_constraints = (*hard_constraints, str(entry.value))

        return ScoringContext(
            current_intent=current_goal,
            current_goal=current_goal,
            acceptance_criteria=acceptance_criteria,
            hard_constraints=hard_constraints,
            current_task_id=current_task_id,
            current_phase=self._current_phase,
            current_time=time.time(),
        )

    def process(
        self,
        budget_out: BudgetPlannerOutput,
        patcher_out: StatePatcherOutput,
        canon_out: CanonicalizerOutput,
        inp: PipelineInput,
        decision_log: ContextDecisionLog | None = None,
    ) -> WindowCollectorOutput:
        """Collect active window events with optional attention scoring.

        Args:
            budget_out: Budget planner output
            patcher_out: State patcher output with WorkingState
            canon_out: Canonicalizer output with transcript
            inp: Pipeline input
            decision_log: Optional decision log for recording decisions

        Returns:
            WindowCollectorOutput with selected active window events
        """
        transcript = canon_out.transcript
        working_state = patcher_out.working_state
        budget_plan = budget_out.budget_plan
        recent_window_messages = inp.recent_window_messages

        if not transcript:
            return WindowCollectorOutput(active_window=())

        # Import decision log types if logging is enabled
        _log_decisions = decision_log is not None
        if _log_decisions:
            pass

        min_recent_floor = max(1, int(self._policy.min_recent_messages_pinned or 1))
        min_recent_floor = min(self._policy.max_active_window_messages, min_recent_floor)
        recent_limit = max(min_recent_floor, int(recent_window_messages or 1))
        recent_limit = max(1, min(self._policy.max_active_window_messages, recent_limit))
        recent_candidates = list(transcript[-recent_limit:])
        forced_recent_ids = {item.event_id for item in transcript[-min_recent_floor:]}
        pinned_sequences: set[int] = {item.sequence for item in recent_candidates}

        if working_state.task_state.current_goal is not None:
            pinned_sequences.update(self._sequences_from_turns(working_state.task_state.current_goal.source_turns))

        for collection in (
            working_state.task_state.accepted_plan,
            working_state.task_state.open_loops,
            working_state.task_state.blocked_on,
            working_state.task_state.deliverables,
            working_state.active_entities,
        ):
            for entry in collection:
                pinned_sequences.update(self._sequences_from_turns(entry.source_turns))

        active_artifact_ids = set(working_state.active_artifacts)
        active_window_ratio = getattr(self._policy, "active_window_budget_ratio", 0.45)
        token_budget = max(512, min(budget_plan.soft_limit, int(budget_plan.input_budget * active_window_ratio)))

        # Build scoring context for attention scoring
        scoring_context = self._build_scoring_context(working_state)

        # Phase 1: Use attention scoring if enabled
        if self._enable_attention_scoring and self._ranker is not None:
            return self._process_with_attention_scoring(
                transcript=transcript,
                forced_recent_ids=forced_recent_ids,
                pinned_sequences=pinned_sequences,
                active_artifact_ids=active_artifact_ids,
                token_budget=token_budget,
                scoring_context=scoring_context,
                decision_log=decision_log,
            )

        # Phase 2: Fallback to static rules (original behavior)
        return self._process_with_static_rules(
            transcript=transcript,
            forced_recent_ids=forced_recent_ids,
            pinned_sequences=pinned_sequences,
            active_artifact_ids=active_artifact_ids,
            token_budget=token_budget,
            decision_log=decision_log,
        )

    def _process_with_attention_scoring(
        self,
        transcript: tuple[TranscriptEvent, ...],
        forced_recent_ids: set[str],
        pinned_sequences: set[int],
        active_artifact_ids: set[str],
        token_budget: int,
        scoring_context: ScoringContext,
        decision_log: ContextDecisionLog | None,
    ) -> WindowCollectorOutput:
        """Process with attention scoring enabled."""
        # Score and rank all candidates
        ranked = self._ranker.rank_candidates(
            candidates=transcript,
            context=scoring_context,
            token_budget=token_budget,
            min_recent=self._policy.min_recent_messages_pinned,
        )

        # Select candidates based on ranking
        selected_events: dict[str, TranscriptEvent] = {}
        token_count = 0

        for ranked_candidate in ranked:
            if not ranked_candidate.selected:
                continue

            candidate = ranked_candidate.candidate
            candidate_id = str(getattr(candidate, "event_id", ""))

            # Skip if already selected
            if candidate_id in selected_events:
                continue

            # Check if this is a forced recent or pinned event
            is_forced_recent = candidate_id in forced_recent_ids
            is_pinned = (
                int(getattr(candidate, "sequence", 0)) in pinned_sequences
                or str(getattr(candidate, "artifact_id", "")) in active_artifact_ids
            )

            # Apply JIT compression if needed
            item_content = str(getattr(candidate, "content", "") or "")
            estimated = _estimate_tokens(item_content)
            compressed_via_jit = False

            if token_count + estimated > token_budget and (is_forced_recent or is_pinned):
                # Try JIT compression for pinned/forced events
                remaining_budget = token_budget - token_count
                remaining_chars = max(512, remaining_budget * 4)
                if remaining_chars < len(item_content):
                    summarizer = self._get_summarizer()
                    if summarizer is not None and len(item_content) > 300:
                        try:
                            content_type = self._infer_content_type(candidate)
                            target_tokens = max(50, remaining_budget)
                            item_content = summarizer.summarize(
                                item_content,
                                max_tokens=target_tokens,
                                content_type=content_type,
                            )
                            compressed_via_jit = True
                            estimated = _estimate_tokens(item_content)
                        except (RuntimeError, ValueError, TimeoutError):
                            logger.debug("JIT summarization failed", exc_info=True)

            # Check budget again after compression
            if token_count + estimated > token_budget:
                if decision_log is not None:
                    decision_log.record(create_decision(
                        decision_type=ContextDecisionType.EXCLUDE,
                        target_event_id=candidate_id,
                        reason="token_budget_exceeded",
                        reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
                        token_budget_before=token_budget,
                        token_budget_after=token_budget,
                        token_cost=estimated,
                        attention_score=ranked_candidate.score,
                        explanation=f"Candidate excluded: token budget exceeded (score={ranked_candidate.score.final_score:.2f})",
                    ))
                continue

            # Apply content mutation if compressed
            if compressed_via_jit:
                candidate = validated_replace(
                    candidate,
                    content=item_content,
                    metadata={**dict(getattr(candidate, "metadata", {}) or {}), "jit_compressed": True},
                )

            selected_events[candidate_id] = candidate
            token_count += estimated

            # Log decision
            if decision_log is not None:
                decision_log.record(create_decision(
                    decision_type=ContextDecisionType.INCLUDE_FULL,
                    target_event_id=candidate_id,
                    reason="attention_scored",
                    reason_codes=tuple(ranked_candidate.reason_codes),
                    token_budget_before=token_budget,
                    token_budget_after=token_budget,
                    token_cost=estimated,
                    attention_score=ranked_candidate.score,
                    explanation=f"Candidate included: attention_score={ranked_candidate.score.final_score:.2f}, rank={ranked_candidate.rank}",
                ))

        # Sort by sequence for consistent output
        active_window = tuple(
            sorted(selected_events.values(), key=lambda item: (item.sequence, item.event_id))
        )

        logger.info(
            "Attention-aware window: %d candidates scored, %d selected, token_used=%d/%d",
            len(ranked),
            len(active_window),
            token_count,
            token_budget,
        )

        return WindowCollectorOutput(active_window=active_window)

    def _process_with_static_rules(
        self,
        transcript: tuple[TranscriptEvent, ...],
        forced_recent_ids: set[str],
        pinned_sequences: set[int],
        active_artifact_ids: set[str],
        token_budget: int,
        decision_log: ContextDecisionLog | None,
    ) -> WindowCollectorOutput:
        """Process with static rules (original behavior)."""
        pinned_events: dict[str, TranscriptEvent] = {}
        token_count = 0

        for item in reversed(transcript):
            if item.route == RoutingClass.CLEAR and item.event_id not in forced_recent_ids:
                if decision_log is not None:
                    decision_log.record(create_decision(
                        decision_type=ContextDecisionType.EXCLUDE,
                        target_event_id=item.event_id,
                        reason="route_cleared",
                        reason_codes=(ReasonCode.ROUTE_CLEARED,),
                        token_budget_before=token_budget,
                        token_budget_after=token_budget,
                        explanation="Event excluded because route is CLEAR.",
                    ))
                continue

            is_reopened = bool(str(get_metadata_value(item.metadata, "reopen_hold") or "").strip())
            is_root = (
                item.sequence in pinned_sequences
                or (item.artifact_id in active_artifact_ids)
                or is_reopened
                or item.event_id in forced_recent_ids
            )

            root_reasons: list[ReasonCode] = []
            if item.sequence in pinned_sequences:
                root_reasons.append(ReasonCode.PINNED_BY_SYSTEM)
            if item.artifact_id in active_artifact_ids:
                root_reasons.append(ReasonCode.ACTIVE_ARTIFACT)
            if is_reopened:
                root_reasons.append(ReasonCode.OPEN_LOOP_REFERENCE)
            if item.event_id in forced_recent_ids:
                root_reasons.append(ReasonCode.FORCED_RECENT)

            can_add = is_root or len(pinned_events) < self._policy.max_active_window_messages
            if not can_add:
                if decision_log is not None:
                    decision_log.record(create_decision(
                        decision_type=ContextDecisionType.EXCLUDE,
                        target_event_id=item.event_id,
                        reason="max_window_messages_reached",
                        reason_codes=(ReasonCode.NOT_IN_ACTIVE_WINDOW,),
                        token_budget_before=token_budget,
                        token_budget_after=token_budget,
                        explanation="Non-root event excluded: max_active_window_messages reached.",
                    ))
                continue

            item_content = item.content
            estimated = _estimate_tokens(item_content)
            compressed_via_jit = False

            if token_count + estimated > token_budget and is_root:
                remaining_budget = token_budget - token_count
                remaining_chars = max(512, remaining_budget * 4)
                if remaining_chars < len(item_content):
                    summarizer = self._get_summarizer()
                    if summarizer is not None and len(item_content) > 300:
                        try:
                            content_type = self._infer_content_type(item)
                            target_tokens = max(50, remaining_budget)
                            item_content = summarizer.summarize(
                                item_content,
                                max_tokens=target_tokens,
                                content_type=content_type,
                            )
                            compressed_via_jit = True
                            estimated = _estimate_tokens(item_content)
                        except (RuntimeError, ValueError, TimeoutError):
                            logger.debug("JIT summarization failed", exc_info=True)

                if not compressed_via_jit:
                    item_content = item_content[:remaining_chars]
                    estimated = _estimate_tokens(item_content)

            if token_count + estimated > token_budget:
                if is_root:
                    logger.warning("Root event over budget: %s", item.event_id)
                if decision_log is not None:
                    decision_log.record(create_decision(
                        decision_type=ContextDecisionType.EXCLUDE,
                        target_event_id=item.event_id,
                        reason="token_budget_exceeded",
                        reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
                        token_budget_before=token_budget,
                        token_budget_after=token_budget,
                        token_cost=estimated,
                        explanation="Event excluded: token budget exceeded.",
                    ))
                continue

            if item.event_id in pinned_events:
                continue

            # Apply content mutation
            if compressed_via_jit:
                pinned_item = validated_replace(
                    item,
                    content=item_content,
                    metadata={**dict(item.metadata), "jit_compressed": True},
                )
            else:
                pinned_item = item

            pinned_events[item.event_id] = pinned_item
            token_count += estimated

            if decision_log is not None:
                decision_log.record(create_decision(
                    decision_type=ContextDecisionType.INCLUDE_FULL,
                    target_event_id=item.event_id,
                    reason="static_rule",
                    reason_codes=tuple(root_reasons) if root_reasons else (ReasonCode.NOT_IN_ACTIVE_WINDOW,),
                    token_budget_before=token_budget,
                    token_budget_after=token_budget,
                    token_cost=estimated,
                    explanation=f"Event included by static rules. Root={is_root}, Compressed={compressed_via_jit}.",
                ))

        active_window = tuple(sorted(pinned_events.values(), key=lambda item: (item.sequence, item.event_id)))
        return WindowCollectorOutput(active_window=active_window)

    @staticmethod
    def _infer_content_type(item: TranscriptEvent) -> str:
        """Infer content type for summarization strategy selection."""
        if item.kind in ("user_turn", "assistant_turn"):
            return "dialogue"
        content = item.content.strip().lower()
        if any(kw in content for kw in ("error", "exception", "traceback", "failed")):
            return "log"
        if (content.startswith("{") and content.endswith("}")) or (content.startswith("[") and content.endswith("]")):
            return "json"
        code_markers = ("def ", "class ", "import ", "from ", "function", "const ", "let ", "# ", "// ", "/*", "*/")
        if any(m in content for m in code_markers):
            return "code"
        return "text"

    @staticmethod
    def _sequences_from_turns(turns: tuple[str, ...]) -> set[int]:
        result: set[int] = set()
        for turn in turns:
            token = str(turn).strip().lower()
            if token.startswith("t") and token[1:].isdigit():
                result.add(int(token[1:]))
        return result

    @property
    def current_phase(self) -> TaskPhase:
        """Get current phase."""
        return self._current_phase

    @current_phase.setter
    def current_phase(self, phase: TaskPhase) -> None:
        """Set current phase."""
        self._current_phase = phase
