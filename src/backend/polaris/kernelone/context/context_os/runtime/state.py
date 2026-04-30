"""State management and transitions for State-First Context OS runtime.

This module provides the `_ContextOSStateMixin` which encapsulates all
methods responsible for building, patching, and querying working state,
budget plans, run cards, context slice plans, and episode sealing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import replace
from typing import Any

from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from ..helpers import (
    _dedupe_state_entries,
    _estimate_tokens,
    _normalize_text,
    _slug,
    _StateAccumulator,
    _trim_text,
    _utc_now_iso,
    get_metadata_value,
)
from ..models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    ContextSlicePlanV2 as ContextSlicePlan,
    ContextSliceSelectionV2 as ContextSliceSelection,
    DecisionEntryV2 as DecisionEntry,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from .ports import MAX_INLINE_CHARS, MAX_STUB_CHARS, _decision_kind, _extract_hard_constraints

logger = logging.getLogger(__name__)


class _ContextOSStateMixin:
    """Mixin for state-building and state-management methods."""

    # These attributes are expected to exist on the composite class.
    policy: Any
    domain_adapter: Any
    _notify_observers: Any
    _get_hook_manager: Any
    _get_content_store: Any
    _sequences_from_turns: Any

    def _rebuild_prompt_view(
        self,
        snapshot: Any,
    ) -> dict[str, Any]:
        budget_plan = snapshot.budget_plan or self._plan_budget(snapshot.transcript_log, snapshot.artifact_store)
        active_window = self._collect_active_window(
            transcript=snapshot.transcript_log,
            working_state=snapshot.working_state,
            recent_window_messages=self.policy.default_history_window_messages,
            budget_plan=budget_plan,
        )
        artifact_stubs = self._select_artifacts_for_prompt(
            artifacts=snapshot.artifact_store,
            working_state=snapshot.working_state,
        )
        episode_cards = self._select_episodes_for_prompt(
            episodes=snapshot.episode_store,
            working_state=snapshot.working_state,
            focus="",
        )
        run_card = self._build_run_card(working_state=snapshot.working_state)
        context_slice_plan = self._build_context_slice_plan(
            transcript=snapshot.transcript_log,
            active_window=active_window,
            working_state=snapshot.working_state,
            artifact_stubs=artifact_stubs,
            episode_cards=episode_cards,
            budget_plan=budget_plan,
        )
        return {
            "budget_plan": budget_plan,
            "active_window": active_window,
            "artifact_stubs": artifact_stubs,
            "episode_cards": episode_cards,
            "run_card": run_card,
            "context_slice_plan": context_slice_plan,
        }

    def _patch_working_state(
        self,
        transcript: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        precomputed_hints: dict[str, Any] | None = None,
    ) -> WorkingState:
        """Build WorkingState from transcript.

        Args:
            transcript: Canonicalized transcript events.
            artifacts: Artifact records from canonicalization.
            precomputed_hints: Optional pre-extracted state hints from _canonicalize_and_offload.
                If provided, skips redundant extract_state_hints calls for O(n) -> O(1) lookup per event.
        """
        acc = _StateAccumulator()
        decisions: list[DecisionEntry] = []
        last_decision_by_kind: dict[str, DecisionEntry] = {}
        current_goal_candidates: list[StateEntry] = []
        accepted_plan: list[StateEntry] = []
        open_loops: list[StateEntry] = []
        blocked_on: list[StateEntry] = []
        deliverables: list[StateEntry] = []
        preferences: list[StateEntry] = []
        style: list[StateEntry] = []
        persistent_facts: list[StateEntry] = []
        temporal_facts: list[StateEntry] = []
        active_entities: list[StateEntry] = []

        for item in transcript:
            if item.route == RoutingClass.CLEAR:
                continue
            turns = item.source_turns or (f"t{item.sequence}",)
            # OPTIMIZATION: Use pre-computed hints if available (from _canonicalize_and_offload)
            # to avoid redundant extract_state_hints calls
            if precomputed_hints is not None and item.event_id in precomputed_hints:
                hints = precomputed_hints[item.event_id]
            else:
                hints = self.domain_adapter.extract_state_hints(item)
            for value in hints.goals:
                entry = acc.add(path="task_state.current_goal", value=value, source_turns=turns, confidence=0.96)
                if entry is not None:
                    current_goal_candidates.append(entry)
            for value in hints.accepted_plan:
                entry = acc.add(
                    path=f"task_state.accepted_plan::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.90,
                )
                if entry is not None:
                    accepted_plan.append(entry)
            for value in hints.open_loops:
                entry = acc.add(
                    path=f"task_state.open_loops::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.88,
                )
                if entry is not None:
                    open_loops.append(entry)
            for value in hints.blocked_on:
                entry = acc.add(
                    path=f"task_state.blocked_on::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.84,
                )
                if entry is not None:
                    blocked_on.append(entry)
            for value in hints.deliverables:
                entry = acc.add(
                    path=f"task_state.deliverables::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.82,
                )
                if entry is not None:
                    deliverables.append(entry)
            for value in hints.preferences:
                entry = acc.add(
                    path=f"user_profile.preferences::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    preferences.append(entry)
            for value in hints.style:
                entry = acc.add(
                    path=f"user_profile.style::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    style.append(entry)
            for value in hints.temporal_facts:
                entry = acc.add(
                    path=f"temporal_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    temporal_facts.append(entry)
            for value in hints.entities:
                entry = acc.add(
                    path=f"active_entities::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.78,
                )
                if entry is not None:
                    active_entities.append(entry)
            for value in hints.persistent_facts:
                entry = acc.add(
                    path=f"user_profile.persistent_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    persistent_facts.append(entry)
            for summary in hints.decisions:
                key = summary.lower()
                if not any(existing.summary.lower() == key for existing in decisions):
                    kind = _decision_kind(summary)
                    previous = last_decision_by_kind.get(kind)
                    decision = DecisionEntry(
                        decision_id=f"dec_{len(decisions) + 1}",
                        summary=summary,
                        source_turns=turns,
                        updated_at=_utc_now_iso(),
                        kind=kind,
                        supersedes=previous.decision_id if previous is not None else None,
                        basis_refs=tuple(item.value for item in active_entities[-2:]),
                    )
                    decisions.append(decision)
                    last_decision_by_kind[kind] = decision

        active_artifacts = tuple(item.artifact_id for item in artifacts[-self.policy.max_artifact_stubs :])

        # Build deduped active lists first so we can compute active_entry_ids
        deduped_preferences = _dedupe_state_entries(preferences, limit=self.policy.max_open_loops)
        deduped_style = _dedupe_state_entries(style, limit=self.policy.max_open_loops)
        deduped_persistent_facts = _dedupe_state_entries(persistent_facts, limit=self.policy.max_stable_facts)
        deduped_accepted_plan = _dedupe_state_entries(accepted_plan, limit=self.policy.max_open_loops)
        deduped_open_loops = _dedupe_state_entries(open_loops, limit=self.policy.max_open_loops)
        deduped_blocked_on = _dedupe_state_entries(blocked_on, limit=self.policy.max_open_loops)
        deduped_deliverables = _dedupe_state_entries(deliverables, limit=self.policy.max_open_loops)
        deduped_active_entities = _dedupe_state_entries(active_entities, limit=self.policy.max_stable_facts)
        deduped_temporal_facts = _dedupe_state_entries(temporal_facts, limit=self.policy.max_stable_facts)

        # Collect IDs of all entries currently in active lists
        active_entry_ids: set[str] = set()
        for entry in (
            deduped_preferences
            + deduped_style
            + deduped_persistent_facts
            + deduped_accepted_plan
            + deduped_open_loops
            + deduped_blocked_on
            + deduped_deliverables
            + deduped_active_entities
            + deduped_temporal_facts
        ):
            if hasattr(entry, "entry_id"):
                active_entry_ids.add(entry.entry_id)
        if current_goal_candidates:
            goal = current_goal_candidates[-1]
            if hasattr(goal, "entry_id"):
                active_entry_ids.add(goal.entry_id)

        # state_history: only keep superseded entries NOT in active lists
        # (avoids duplicating all active entries in history)
        state_history = tuple(
            e
            for e in acc.entries
            if getattr(e, "entry_id", "") not in active_entry_ids and getattr(e, "supersedes", None) is not None
        )

        working_state = WorkingState(
            user_profile=UserProfileState(
                preferences=deduped_preferences,
                style=deduped_style,
                persistent_facts=deduped_persistent_facts,
            ),
            task_state=TaskStateView(
                current_goal=current_goal_candidates[-1] if current_goal_candidates else None,
                accepted_plan=deduped_accepted_plan,
                open_loops=deduped_open_loops,
                blocked_on=deduped_blocked_on,
                deliverables=deduped_deliverables,
            ),
            decision_log=tuple(decisions[-self.policy.max_decisions :]),
            active_entities=deduped_active_entities,
            active_artifacts=active_artifacts,
            temporal_facts=deduped_temporal_facts,
            state_history=state_history,
        )
        logger.debug(
            "[DEBUG][ContextOS] _patch_working_state: goal=%r open_loops=%d blocked=%d decisions=%d active_entities=%d artifacts=%d",
            working_state.task_state.current_goal.value if working_state.task_state.current_goal else "<none>",
            len(working_state.task_state.open_loops),
            len(working_state.task_state.blocked_on),
            len(working_state.decision_log),
            len(working_state.active_entities),
            len(working_state.active_artifacts),
        )

        # === Hook: on_context_patched ===
        # Call registered hooks after working state is patched
        try:
            hook_manager = self._get_hook_manager()
            hook_manager.on_context_patched(
                working_state=working_state,
                transcript=transcript,
            )
        except (RuntimeError, ValueError) as e:
            logger.debug("Hook on_context_patched raised exception (ignored): %s", e)

        return working_state

    def _plan_budget(
        self,
        transcript: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
    ) -> BudgetPlan:
        # Use resolved context window (LLM Config > Hard-coded Table > Policy Default)
        window = max(4096, self.resolved_context_window)
        # Claude Code formula: output_reserve = max(max_expected_output, 0.18C)
        # output_reserve_min serves as max_expected_output (configurable floor)
        # ratio_based is 0.18*C as ceiling
        ratio_based = int(window * self.policy.output_reserve_ratio)  # 0.18 * C
        output_reserve = max(self.policy.output_reserve_min, ratio_based)
        tool_reserve = max(
            self.policy.tool_reserve_min,
            int(window * self.policy.tool_reserve_ratio),
        )
        # Claude Code formula: safety_margin = max(2048, 0.05C)
        safety_margin = max(self.policy.safety_margin_min, int(window * self.policy.safety_margin_ratio))
        input_budget = max(1024, window - output_reserve - tool_reserve - safety_margin)
        retrieval_budget = min(
            max(256, int(input_budget * self.policy.retrieval_ratio)),
            max(256, int(self.policy.planned_retrieval_tokens)),
        )
        current_input_tokens = sum(_estimate_tokens(item.content) for item in transcript)
        current_input_tokens += sum(min(item.token_count, 128) for item in artifacts)
        expected_next_input_tokens = (
            current_input_tokens + int(self.policy.p95_tool_result_tokens) + retrieval_budget + output_reserve
        )
        # A11 Fix: Validate expected_next_input_tokens doesn't exceed model_context_window
        validation_error = ""
        if expected_next_input_tokens > window:
            overrun = expected_next_input_tokens - window
            validation_error = (
                f"BudgetPlan invariant violated: expected_next_input_tokens "
                f"({expected_next_input_tokens}) exceeds model_context_window "
                f"({window}) by {overrun} tokens"
            )
        plan = BudgetPlan(
            model_context_window=window,
            output_reserve=output_reserve,
            tool_reserve=tool_reserve,
            safety_margin=safety_margin,
            input_budget=input_budget,
            retrieval_budget=retrieval_budget,
            soft_limit=max(512, int(input_budget * 0.55)),
            hard_limit=max(768, int(input_budget * 0.72)),
            emergency_limit=max(1024, int(input_budget * 0.85)),
            current_input_tokens=current_input_tokens,
            expected_next_input_tokens=expected_next_input_tokens,
            p95_tool_result_tokens=int(self.policy.p95_tool_result_tokens),
            planned_retrieval_tokens=int(self.policy.planned_retrieval_tokens),
            validation_error=validation_error,
        )
        logger.debug(
            "[DEBUG][ContextOS] _plan_budget: window=%d input=%d soft=%d hard=%d emergency=%d expected=%d current=%d",
            plan.model_context_window,
            plan.input_budget,
            plan.soft_limit,
            plan.hard_limit,
            plan.emergency_limit,
            plan.expected_next_input_tokens,
            plan.current_input_tokens,
        )
        return plan

    def _seal_closed_episodes(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
        existing_episodes: tuple[EpisodeCard, ...],
        pending_followup: PendingFollowUp | None = None,
    ) -> tuple[EpisodeCard, ...]:
        active_ids = {item.event_id for item in active_window}
        last_sealed_sequence = max(
            (item.to_sequence for item in existing_episodes if item.status == "sealed"),
            default=-1,
        )
        closed_events = tuple(
            item
            for item in transcript
            if item.route != RoutingClass.CLEAR
            and item.sequence > last_sealed_sequence
            and item.event_id not in active_ids
            and not str(get_metadata_value(item.metadata, "reopen_hold") or "").strip()
        )

        # === Seal Guard: Block sealing if pending follow-up exists ===
        if (
            self.policy.enable_seal_guard
            and self.policy.prevent_seal_on_pending
            and pending_followup
            and pending_followup.status == "pending"
        ):
            # Always emit seal_blocked event - this is a critical security guard behavior
            # It should NOT be gated by enable_attention_trace
            emit_debug_event(
                category="attention",
                label="seal_blocked",
                source="context_os.runtime",
                payload={
                    "reason": "pending_followup_unresolved",
                    "pending_action": pending_followup.action,
                    "pending_status": pending_followup.status,
                },
            )
            return existing_episodes

        if not self.domain_adapter.should_seal_episode(
            closed_events=closed_events,
            active_window=active_window,
            working_state=working_state,
        ):
            return existing_episodes
        if not closed_events:
            return existing_episodes

        # === Hook: on_before_episode_sealed ===
        # Call registered hooks before episode is sealed
        try:
            hook_manager = self._get_hook_manager()
            hook_results = hook_manager.on_before_episode_sealed(
                episode_events=closed_events,
                working_state=working_state,
            )
            # Check if any hook vetoed the sealing
            for result in hook_results:
                if isinstance(result, dict) and result.get("should_veto"):
                    logger.debug(
                        "Episode sealing vetoed by hook: %s",
                        result.get("veto_reason", "unknown reason"),
                    )
                    return existing_episodes
        except (RuntimeError, ValueError) as e:
            logger.debug("Hook on_before_episode_sealed raised exception (ignored): %s", e)
        artifact_ids = tuple(
            item.artifact_id
            for item in closed_events
            if item.artifact_id and any(artifact.artifact_id == item.artifact_id for artifact in artifacts)
        )
        combined = "\n".join(item.content for item in closed_events)
        intent = (
            working_state.task_state.current_goal.value
            if working_state.task_state.current_goal is not None
            else _trim_text(closed_events[0].content, max_chars=96)
        )
        outcome = (
            working_state.decision_log[-1].summary
            if working_state.decision_log
            else _trim_text(closed_events[-1].content, max_chars=160)
        )
        episode = EpisodeCard(
            episode_id=f"ep_{len(existing_episodes) + 1}",
            from_sequence=closed_events[0].sequence,
            to_sequence=closed_events[-1].sequence,
            intent=intent,
            outcome=outcome,
            decisions=tuple(item.summary for item in working_state.decision_log[-self.policy.max_decisions :]),
            facts=tuple(
                item.value for item in working_state.user_profile.persistent_facts[-self.policy.max_stable_facts :]
            ),
            artifact_refs=tuple(dict.fromkeys(artifact_ids)),
            entities=tuple(item.value for item in working_state.active_entities[-self.policy.max_stable_facts :]),
            reopen_conditions=tuple(
                item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :]
            ),
            source_spans=(f"t{closed_events[0].sequence}:t{closed_events[-1].sequence}",),
            digest_64=_trim_text(combined, max_chars=64),
            digest_256=_trim_text(combined, max_chars=256),
            digest_1k=_trim_text(combined, max_chars=1000),
            sealed_at=time.time(),
            status="sealed",
        )
        # === Lifecycle: on_episode_sealed ===
        notify = getattr(self.domain_adapter, "on_episode_sealed", None)
        if callable(notify):
            notify(episode)
        self._notify_observers("on_episode_sealed", episode)
        return (*tuple(existing_episodes), episode)

    def _truncate_artifact_if_needed(self, artifact: ArtifactRecord) -> ArtifactRecord:
        """Truncate artifact content if it exceeds MAX_INLINE_CHARS.

        Args:
            artifact: The artifact record to potentially truncate.

        Returns:
            The original artifact if small enough, or a new artifact with
            truncated content and updated metadata.
        """
        if len(artifact.content) <= MAX_INLINE_CHARS:
            return artifact
        stub_content = (
            artifact.content[:MAX_STUB_CHARS] + f"\n...[truncated, full content at {artifact.artifact_id}]..."
        )
        return replace(
            artifact,
            content=stub_content,
            metadata=tuple(
                sorted({**dict(artifact.metadata or {}), "truncated": True, "full_id": artifact.artifact_id}.items())
            ),
        )

    def _select_artifacts_for_prompt(
        self,
        *,
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
    ) -> tuple[ArtifactRecord, ...]:
        """Select artifacts for prompt injection.

        Implements offloading: large artifacts (>MAX_INLINE_CHARS) are replaced
        with stubs that reference external storage.
        """
        if not artifacts:
            return ()
        active_ids = set(working_state.active_artifacts)
        ordered: list[ArtifactRecord] = []
        seen: set[str] = set()

        # First add active artifacts (always include)
        for artifact_id in active_ids:
            artifact = next((item for item in artifacts if item.artifact_id == artifact_id), None)
            if artifact is not None and artifact.artifact_id not in seen:
                seen.add(artifact.artifact_id)
                ordered.append(self._truncate_artifact_if_needed(artifact))

        # Add remaining artifacts up to max limit
        for artifact in reversed(artifacts):
            if len(ordered) >= self.policy.max_artifact_stubs:
                break
            if artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            ordered.append(self._truncate_artifact_if_needed(artifact))

        # Add remaining artifacts up to max limit
        for artifact in reversed(artifacts):
            if len(ordered) >= self.policy.max_artifact_stubs:
                break
            if artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            ordered.append(self._truncate_artifact_if_needed(artifact))

        return tuple(ordered[: self.policy.max_artifact_stubs])

    def _select_episodes_for_prompt(
        self,
        *,
        episodes: tuple[EpisodeCard, ...],
        working_state: WorkingState,
        focus: str,
    ) -> tuple[EpisodeCard, ...]:
        if not episodes:
            return ()
        focus_terms = {
            token.lower() for token in re.findall(r"[A-Za-z0-9_.:/\\-]+", _normalize_text(focus)) if len(token) >= 2
        }
        ranked: list[tuple[float, EpisodeCard]] = []

        # T6-6 Fix: Use max sequence in the entire episode store as denominator
        # This fixes recency calculation for reopened episodes where episodes[-1].to_sequence
        # might not be the true maximum (reopened episodes have higher sequence numbers)
        max_seq = max((ep.to_sequence for ep in episodes), default=1)

        for episode in episodes:
            if episode.status != "sealed":
                continue
            text = " ".join((episode.intent, episode.outcome, episode.digest_256)).lower()
            lexical = 0.0
            if focus_terms:
                lexical = sum(1 for term in focus_terms if term in text) / max(1, len(focus_terms))
            # Use global max_seq instead of episodes[-1].to_sequence
            recency = max(0.0, episode.to_sequence / max(1, max_seq))
            open_loop_bonus = (
                0.25 if any(loop.value.lower() in text for loop in working_state.task_state.open_loops) else 0.0
            )
            ranked.append((lexical + recency + open_loop_bonus, episode))
        ranked.sort(key=lambda item: (item[0], item[1].to_sequence), reverse=True)
        return tuple(item[1] for item in ranked[: self.policy.max_episode_cards])

    def _build_run_card(
        self,
        *,
        working_state: WorkingState,
        transcript: tuple[TranscriptEvent, ...] | None = None,
        pending_followup: PendingFollowUp | None = None,
    ) -> RunCard:
        current_goal = (
            working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        )
        open_loops = tuple(item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :])
        active_entities = tuple(item.value for item in working_state.active_entities[: self.policy.max_stable_facts])
        recent_decisions = tuple(item.summary for item in working_state.decision_log[-self.policy.max_decisions :])
        next_action_hint = ""
        if open_loops:
            next_action_hint = open_loops[-1]
        elif working_state.task_state.deliverables:
            next_action_hint = working_state.task_state.deliverables[0].value

        # === Run Card v2: Extract attention runtime fields ===
        latest_user_intent = ""
        last_turn_outcome = ""
        latest_user_event: TranscriptEvent | None = None
        if transcript:
            ordered_transcript = sorted(
                transcript,
                key=lambda item: (item.sequence, item.created_at, item.event_id),
            )
            # Find the latest user turn for intent
            for event in reversed(ordered_transcript):
                if event.role == "user":
                    latest_user_event = event
                    latest_user_intent = event.content
                    break
            # Determine last_turn_outcome from the most recent meaningful event
            # (assistant response, tool execution, or user dialog act), not just
            # the user turn. Fixes the "unknown" freeze for open-ended tasks.
            for event in reversed(ordered_transcript):
                if event.role == "assistant":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or "assistant_response")
                    break
                elif event.role == "tool":
                    last_turn_outcome = "tool_execution"
                    break
                elif event.role == "user":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or "DialogAct.UNKNOWN")
                    break

        visible_followup: PendingFollowUp | None = pending_followup
        if visible_followup is not None and visible_followup.status != "pending":
            # Keep resolved follow-up visible only on the exact resolving turn.
            # Subsequent turns should not keep stale follow-up fields in run card.
            latest_resolved_now = bool(
                latest_user_event
                and str(get_metadata_value(latest_user_event.metadata, "followup_action") or "").strip()
            )
            if not latest_resolved_now:
                visible_followup = None

        run_card = RunCard(
            current_goal=current_goal,
            hard_constraints=_extract_hard_constraints(working_state),
            open_loops=open_loops,
            active_entities=active_entities,
            active_artifacts=tuple(working_state.active_artifacts),
            recent_decisions=recent_decisions,
            next_action_hint=next_action_hint,
            # Run Card v2 fields
            latest_user_intent=latest_user_intent,
            pending_followup_action=visible_followup.action if visible_followup else "",
            pending_followup_status=visible_followup.status if visible_followup else "",
            last_turn_outcome=last_turn_outcome,
        )
        logger.debug(
            "[DEBUG][ContextOS] _build_run_card: goal=%r open_loops=%d decisions=%d last_outcome=%r pending=%s",
            current_goal,
            len(open_loops),
            len(recent_decisions),
            last_turn_outcome,
            visible_followup.status if visible_followup else "none",
        )
        return run_card

    def _build_context_slice_plan(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[EpisodeCard, ...],
        budget_plan: BudgetPlan,
    ) -> ContextSlicePlan:
        included: list[ContextSliceSelection] = []
        excluded: list[ContextSliceSelection] = []
        roots = [
            "latest_user_turn",
            "current_goal",
            "open_loops",
        ]
        if _extract_hard_constraints(working_state):
            roots.append("hard_constraints")
        if artifact_stubs:
            roots.append("active_artifacts")

        if working_state.task_state.current_goal is not None:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref="task_state.current_goal",
                    reason="root",
                )
            )
        for open_loop_entry in working_state.task_state.open_loops[-self.policy.max_open_loops :]:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref=open_loop_entry.path,
                    reason="open_loop",
                )
            )
        for artifact_stub in artifact_stubs:
            included.append(
                ContextSliceSelection(
                    selection_type="artifact",
                    ref=artifact_stub.artifact_id,
                    reason="active_artifact"
                    if artifact_stub.artifact_id in working_state.active_artifacts
                    else "recent_artifact",
                )
            )
        for episode_card in episode_cards:
            included.append(
                ContextSliceSelection(
                    selection_type="episode",
                    ref=episode_card.episode_id,
                    reason="episode_recall",
                )
            )
        for window_event in active_window:
            reason = "recent_window"
            if window_event.sequence == max((event.sequence for event in active_window), default=window_event.sequence):
                reason = "latest_turn"
            elif (
                working_state.task_state.current_goal is not None
                and window_event.sequence
                in self._sequences_from_turns(working_state.task_state.current_goal.source_turns)
            ):
                reason = "goal_root"
            included.append(
                ContextSliceSelection(
                    selection_type="event",
                    ref=window_event.event_id,
                    reason=reason,
                )
            )

        active_ids = {window_event.event_id for window_event in active_window}
        included_refs = {item.ref for item in included}
        for transcript_event in transcript:
            if transcript_event.event_id in active_ids:
                continue
            if transcript_event.route == RoutingClass.CLEAR:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="event",
                        ref=transcript_event.event_id,
                        reason="low_signal",
                    )
                )
                continue
            if transcript_event.artifact_id and transcript_event.artifact_id not in included_refs:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="artifact",
                        ref=transcript_event.artifact_id,
                        reason="closed_and_unreachable",
                    )
                )
            else:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="event",
                        ref=transcript_event.event_id,
                        reason="inactive_history",
                    )
                )

        pressure_level = "normal"
        if budget_plan.expected_next_input_tokens >= budget_plan.emergency_limit:
            pressure_level = "emergency"
        elif budget_plan.expected_next_input_tokens >= budget_plan.hard_limit:
            pressure_level = "hard"
        elif budget_plan.expected_next_input_tokens >= budget_plan.soft_limit:
            pressure_level = "soft"

        plan = ContextSlicePlan(
            plan_id=f"slice_{hashlib.sha256('|'.join(included_refs or {'empty'}).encode('utf-8')).hexdigest()[:10]}",
            budget_tokens=budget_plan.input_budget,
            roots=tuple(roots),
            included=tuple(included),
            excluded=tuple(excluded[: max(12, self.policy.max_active_window_messages)]),
            pressure_level=pressure_level,
        )
        logger.debug(
            "[DEBUG][ContextOS] _build_context_slice_plan: included=%d excluded=%d roots=%s pressure=%s",
            len(plan.included),
            len(plan.excluded),
            plan.roots,
            plan.pressure_level,
        )
        return plan

    def _build_head_anchor(
        self,
        *,
        working_state: WorkingState,
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[EpisodeCard, ...],
    ) -> str:
        lines: list[str] = []
        goal = working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        if goal:
            lines.append(f"Current goal: {goal}")
        loops = [item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :]]
        if loops:
            lines.append("Open loops: " + "; ".join(loops))
        blocked = [item.value for item in working_state.task_state.blocked_on[: self.policy.max_open_loops]]
        if blocked:
            lines.append("Blocked on: " + "; ".join(blocked))
        decisions = [item.summary for item in working_state.decision_log[: self.policy.max_decisions]]
        if decisions:
            lines.append("Recent decisions: " + "; ".join(decisions))
        entities = [item.value for item in working_state.active_entities[: self.policy.max_stable_facts]]
        if entities:
            lines.append("Active entities: " + "; ".join(entities))
        if artifact_stubs:
            lines.append(
                "Artifacts: "
                + "; ".join(f"{item.artifact_id}<{item.artifact_type}> {item.peek}" for item in artifact_stubs)
            )
        if episode_cards:
            lines.append("Recent episodes: " + "; ".join(item.digest_64 for item in episode_cards))
        return "\n".join(lines).strip()

    def _build_tail_anchor(
        self,
        *,
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
    ) -> str:
        if not active_window:
            return ""
        last_event = active_window[-1]
        parts = [f"Last event: {last_event.role} -> {_trim_text(last_event.content, max_chars=180)}"]
        if working_state.task_state.open_loops:
            parts.append(f"Next focus: {working_state.task_state.open_loops[-1].value}")
        elif working_state.task_state.deliverables:
            parts.append(f"Next deliverable: {working_state.task_state.deliverables[0].value}")
        return "\n".join(parts).strip()
