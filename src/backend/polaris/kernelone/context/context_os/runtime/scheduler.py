"""Task scheduling and queue management for State-First Context OS runtime.

This module provides the `_ContextOSSchedulerMixin` which encapsulates all
methods responsible for transcript merging, event canonicalization, and
active window collection.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from ..helpers import (
    _artifact_id,
    _clamp_confidence,
    _estimate_tokens,
    _event_id,
    _normalize_text,
    _trim_text,
    _utc_now_iso,
    get_metadata_value,
)
from ..model_utils import validated_replace
from ..models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    DialogAct,
    DialogActResultV2 as DialogActResult,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    TranscriptEventV2 as TranscriptEvent,
)
from .ports import (
    _extract_assistant_followup_action,
    _is_affirmative_response,
    _is_negative_response,
)

logger = logging.getLogger(__name__)


class _ContextOSSchedulerMixin:
    """Mixin for transcript merging, canonicalization, and window scheduling."""

    # These attributes are expected to exist on the composite class.
    policy: Any
    domain_adapter: Any
    _dialog_act_classifier: Any
    _notify_observers: Any
    _get_content_store: Any
    _sequences_from_turns: Any

    def _merge_transcript(
        self,
        *,
        existing: tuple[TranscriptEvent, ...],
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[TranscriptEvent, ...]:
        logger.debug(
            "[DEBUG][ContextOS] _merge_transcript start: existing=%d incoming_msgs=%d",
            len(existing),
            len(messages) if messages else 0,
        )
        merged: dict[str, TranscriptEvent] = {item.event_id: item for item in existing}
        next_sequence = max((item.sequence for item in existing), default=-1) + 1

        # First pass: collect all tool_calls from message metadata to emit
        # tool_call events BEFORE the main message processing loop.
        # This ensures tool_call events appear before tool_result events in
        # the transcript, preserving causal ordering.
        pending_tool_calls: list[tuple[int, dict[str, Any], str]] = []  # (sequence, tool_call, source_event_id)
        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            metadata: dict[str, Any] = dict(raw.get("metadata") or {})
            tool_calls = metadata.get("tool_calls")
            if not isinstance(tool_calls, (list, tuple)) or not tool_calls:
                continue

            # Determine sequence for this message's tool calls
            sequence_token = str(raw.get("sequence") or "").strip()
            seq = int(sequence_token) if sequence_token.isdigit() else next_sequence

            source_event_id = str(raw.get("event_id") or "").strip()
            if not source_event_id:
                source_event_id = _event_id(seq, "assistant", "tool_call_batch")

            # Collect tool calls to emit after main loop
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                pending_tool_calls.append((seq, tc, source_event_id))

        # Emit pending tool_call events with sequential integer sequences to maintain ordering.
        # BUG FIX: Previously used float sub-indices (seq + 0.01*idx) which were truncated
        # to int by TranscriptEvent.sequence (int field), making all tool_calls share the
        # same sequence. Now use monotonically increasing integers from next_sequence.
        for _idx, (_seq, tool_call, source_event_id) in enumerate(pending_tool_calls):
            call_sequence = next_sequence
            next_sequence += 1

            tool_name = str(tool_call.get("name") or tool_call.get("tool") or "unknown").strip()
            call_id = str(
                tool_call.get("id") or tool_call.get("call_id") or _event_id(int(call_sequence), "tool_call", tool_name)
            ).strip()
            args = tool_call.get("arguments") or tool_call.get("args") or {}
            if not isinstance(args, dict):
                args = {"raw": str(args)}

            # Create metadata for tool_call event with full tool metadata
            tc_metadata: dict[str, Any] = {
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "tool_args": args,
                "source_event_id": source_event_id,
                "event_kind": "tool_call",
            }
            # Preserve any additional metadata from the original tool call dict
            for key, value in tool_call.items():
                if key not in ("name", "tool", "id", "call_id", "arguments", "args"):
                    tc_metadata[key] = value

            event_id = _event_id(int(call_sequence), "tool_call", f"{tool_name}:{call_id}")
            tc_content = f"tool_call: {tool_name}({args})"
            tc_content_ref = None
            with contextlib.suppress(Exception):
                tc_content_ref = self._get_content_store().intern(tc_content)
            event = TranscriptEvent(
                event_id=event_id,
                sequence=int(call_sequence),
                role="assistant",
                kind="tool_call",
                route="",
                content=tc_content,
                source_turns=(f"t{int(call_sequence)}",),
                artifact_id=None,
                created_at=_utc_now_iso(),
                metadata=tc_metadata,
                content_ref_hash=tc_content_ref.hash if tc_content_ref else "",
                content_ref_size=int(tc_content_ref.size) if tc_content_ref else 0,
                content_ref_mime=tc_content_ref.mime if tc_content_ref else "",
            )
            merged[event.event_id] = event
            # === Lifecycle: on_event_created ===
            notify = getattr(self.domain_adapter, "on_event_created", None)
            if callable(notify):
                notify(event)
            self._notify_observers("on_event_created", event)

        # Second pass: process all messages (including tool_result from role=tool)
        # BUG FIX: Track which assistant messages already got tool_call events
        # in the first pass to avoid creating duplicate assistant_turn events.
        _assistant_source_ids_with_tool_calls: set[str] = set()
        for _, _, src_id in pending_tool_calls:
            if src_id:
                _assistant_source_ids_with_tool_calls.add(src_id)

        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip().lower()
            content = _normalize_text(raw.get("content") or raw.get("message") or "")

            # Extract tool_calls metadata before potentially skipping due to empty content
            metadata = dict(raw.get("metadata") or {})
            tool_calls_in_msg = metadata.get("tool_calls")

            # Skip message if no role, no content, AND no tool_calls to emit
            # (tool_call events were already emitted in first pass above)
            if not role or (not content and not tool_calls_in_msg):
                continue

            # BUG FIX: For assistant messages that already had tool_call events
            # emitted in the first pass, only create an event if there is actual
            # text content. Skip bare tool-call wrappers with no meaningful text.
            # This prevents duplicate transcript entries for the same turn.
            if role == "assistant" and tool_calls_in_msg:
                raw_event_id = str(raw.get("event_id") or "").strip()
                source_event_id = raw_event_id or _event_id(
                    int(str(raw.get("sequence") or "0").strip() or "0"),
                    "assistant",
                    "tool_call_batch",
                )
                if source_event_id in _assistant_source_ids_with_tool_calls and not content.strip():
                    continue  # Already emitted as tool_call event, no text content to add

            sequence_token = str(raw.get("sequence") or "").strip()
            if sequence_token.isdigit():
                sequence = int(sequence_token)
                next_sequence = max(next_sequence, sequence + 1)
            else:
                # Keep sequence monotonic across snapshot continuation.
                # Falling back to enumerate() resets to zero and breaks turn recency.
                sequence = next_sequence
                next_sequence += 1

            # SSOT: Extract all metadata fields from incoming event.
            # Preserves kind, route, dialog_act, source_turns, artifact_id, created_at
            # for complete event provenance and ContextOS event sourcing compliance.
            event_id = str(raw.get("event_id") or "").strip() or _event_id(sequence, role, content)
            kind = str(raw.get("kind") or "").strip() or ("tool_result" if role == "tool" else f"{role}_turn")
            route = str(raw.get("route") or "").strip()
            source_turns_raw = raw.get("source_turns")
            if isinstance(source_turns_raw, (list, tuple)) and source_turns_raw:
                source_turns = tuple(str(s) for s in source_turns_raw)
            else:
                source_turns = (f"t{sequence}",)
            artifact_id = raw.get("artifact_id")
            if artifact_id and isinstance(artifact_id, str) and artifact_id.strip():
                artifact_id = artifact_id.strip()
            else:
                artifact_id = None
            created_at = str(raw.get("created_at") or "").strip() or _utc_now_iso()

            # v2.1 dual-write: intern content
            content_ref = None
            if content:
                with contextlib.suppress(Exception):
                    content_ref = self._get_content_store().intern(content)

            event = TranscriptEvent(
                event_id=event_id,
                sequence=sequence,
                role=role,
                kind=kind,
                route=route,
                content=content,
                source_turns=source_turns,
                artifact_id=artifact_id,
                created_at=created_at,
                metadata=metadata,
                content_ref_hash=content_ref.hash if content_ref else "",
                content_ref_size=int(content_ref.size) if content_ref else 0,
                content_ref_mime=content_ref.mime if content_ref else "",
            )
            merged[event.event_id] = event
            # === Lifecycle: on_event_created ===
            notify = getattr(self.domain_adapter, "on_event_created", None)
            if callable(notify):
                notify(event)
            self._notify_observers("on_event_created", event)
        result = tuple(sorted(merged.values(), key=lambda item: (item.sequence, item.event_id)))
        _role_counts: dict[str, int] = {}
        for item in result:
            _role_counts[item.role] = _role_counts.get(item.role, 0) + 1
        logger.debug(
            "[DEBUG][ContextOS] _merge_transcript end: merged_total=%d roles=%s next_sequence=%d",
            len(result),
            _role_counts,
            next_sequence,
        )
        return result

    def _canonicalize_and_offload(
        self,
        transcript: tuple[TranscriptEvent, ...],
        *,
        existing_artifacts: tuple[ArtifactRecord, ...],
        current_pending_followup: PendingFollowUp | None = None,
    ) -> tuple[
        tuple[TranscriptEvent, ...],
        tuple[ArtifactRecord, ...],
        PendingFollowUp | None,
        dict[str, Any],
    ]:
        """Canonicalize transcript events and extract state hints in a single pass.

        Returns:
            Tuple of (updated_transcript, artifacts, pending_followup, state_hints_by_event_id)
            The state_hints_by_event_id maps event_id -> DomainStatePatchHints for efficient
            _patch_working_state by avoiding redundant extract_state_hints calls.
        """
        artifact_by_id = {item.artifact_id: item for item in existing_artifacts}
        updated_events: list[TranscriptEvent] = []
        # OPTIMIZATION: Pre-extract state hints during canonicalization to avoid
        # a second full traversal in _patch_working_state
        state_hints_by_event_id: dict[str, Any] = {}

        # Track pending follow-up state
        # IMPORTANT: Only track UNRESOLVED pending follow-ups to prevent
        # resolved follow-ups from continuing to occupy attention
        pending_followup: PendingFollowUp | None = None
        pending_followup_action = ""
        pending_followup_event_id = ""
        pending_followup_sequence = 0

        # Only inherit unresolved pending follow-up from existing snapshot
        if (
            current_pending_followup
            and current_pending_followup.action
            and current_pending_followup.status == "pending"
        ):
            # Only track pending (unresolved) follow-ups
            pending_followup = current_pending_followup
            pending_followup_action = current_pending_followup.action
            pending_followup_event_id = current_pending_followup.source_event_id
            pending_followup_sequence = current_pending_followup.source_sequence
        # else: Resolved follow-ups (confirmed/denied/paused) are NOT tracked

        for item in transcript:
            # Classify dialog act for user and assistant messages
            dialog_act_result: DialogActResult | None = None
            if item.role in ("user", "assistant") and self._dialog_act_classifier is not None:
                dialog_act_result = self._dialog_act_classifier.classify(item.content, role=item.role)

            if item.role == "assistant":
                # Extract follow-up action using generic patterns
                inferred_action = _extract_assistant_followup_action(item.content)

                # === A7: Code-domain follow-up enhancement ===
                # Also check domain adapter for code-specific follow-up classification
                if not inferred_action and hasattr(self.domain_adapter, "classify_assistant_followup"):
                    domain_decision = self.domain_adapter.classify_assistant_followup(
                        item,
                        policy=self.policy,
                    )
                    if domain_decision and domain_decision.reasons:
                        # Extract action from domain-specific reasons
                        for reason in domain_decision.reasons:
                            if reason.startswith("code_followup_"):
                                inferred_action = reason.replace("code_followup_", "")
                                break

                if inferred_action:
                    pending_followup_action = inferred_action
                    pending_followup_event_id = item.event_id
                    pending_followup_sequence = item.sequence

            followup_metadata: dict[str, Any] = {}
            followup_confirmed = False
            dialog_act_resolved = False
            dialog_act: str = DialogAct.UNKNOWN
            resolved_followup_status: str | None = None

            if item.role == "user" and pending_followup_action:
                # Use dialog act classification result
                if dialog_act_result:
                    dialog_act = dialog_act_result.act
                    if dialog_act == DialogAct.AFFIRM:
                        followup_confirmed = True
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif dialog_act == DialogAct.DENY:
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"
                    elif dialog_act == DialogAct.PAUSE:
                        dialog_act_resolved = True
                        resolved_followup_status = "paused"
                    elif dialog_act == DialogAct.REDIRECT:
                        dialog_act_resolved = True
                        resolved_followup_status = "redirected"

                # Fallback to pattern matching if dialog act not available
                if not dialog_act_resolved:
                    if _is_affirmative_response(item.content):
                        followup_confirmed = True
                        dialog_act = DialogAct.AFFIRM
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif _is_negative_response(item.content):
                        dialog_act = DialogAct.DENY
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"

                if dialog_act_resolved:
                    followup_metadata = {
                        "followup_action": pending_followup_action,
                        "followup_confirmed": str(followup_confirmed).lower(),
                        "followup_source_sequence": str(pending_followup_sequence),
                        "dialog_act": dialog_act,
                    }
                    # Update pending follow-up with resolution
                    # Use validated status (only valid values: pending|confirmed|denied|paused|redirected|expired)
                    final_status = (
                        resolved_followup_status
                        if resolved_followup_status in ("confirmed", "denied", "paused", "redirected", "expired")
                        else "expired"
                    )
                    pending_followup = PendingFollowUp(
                        action=pending_followup_action,
                        source_event_id=pending_followup_event_id,
                        source_sequence=pending_followup_sequence,
                        status=final_status,
                        updated_at=_utc_now_iso(),
                    )
                    # === Lifecycle: on_pending_followup_resolved ===
                    notify = getattr(self.domain_adapter, "on_pending_followup_resolved", None)
                    if callable(notify):
                        notify(pending_followup)
                    self._notify_observers("on_pending_followup_resolved", pending_followup)
                    # Clear local variables after successful resolution
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0
                else:
                    # User responded but dialog act was not recognized - mark as expired
                    # to prevent deadlock (pending follow-up never getting resolved)
                    if pending_followup and pending_followup.status == "pending":
                        pending_followup = PendingFollowUp(
                            action=pending_followup.action,
                            source_event_id=pending_followup.source_event_id,
                            source_sequence=pending_followup.source_sequence,
                            status="expired",
                            updated_at=_utc_now_iso(),
                        )
                        # === Lifecycle: on_pending_followup_resolved ===
                        notify = getattr(self.domain_adapter, "on_pending_followup_resolved", None)
                        if callable(notify):
                            notify(pending_followup)
                        self._notify_observers("on_pending_followup_resolved", pending_followup)
                    # Clear local variables
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0

            forced_route = str(get_metadata_value(item.metadata, "forced_route") or "").strip().lower()
            if forced_route:
                route = forced_route
                decision_metadata = {}
                routing_confidence = _clamp_confidence(
                    get_metadata_value(item.metadata, "routing_confidence"),
                    default=1.0,
                )
                routing_reasons = tuple(
                    str(value).strip()
                    for value in (get_metadata_value(item.metadata, "routing_reasons") or [])
                    if str(value).strip()
                ) or ("manual_reclassification",)
            elif followup_confirmed:
                route = RoutingClass.PATCH
                decision_metadata = dict(followup_metadata)
                routing_confidence = 0.94
                routing_reasons = ("assistant_followup_confirmation",)
            else:
                decision = self.domain_adapter.classify_event(item, policy=self.policy)
                route = decision.route or RoutingClass.SUMMARIZE
                decision_metadata = {
                    **dict(decision.metadata),
                    **followup_metadata,
                }
                routing_confidence = _clamp_confidence(decision.confidence, default=0.5)
                routing_reasons = tuple(decision.reasons or ())
            artifact_id = item.artifact_id if route == RoutingClass.ARCHIVE else None
            if route == RoutingClass.ARCHIVE:
                artifact_id = artifact_id or _artifact_id(item.content)
                existing_artifact = artifact_by_id.get(artifact_id)
                if existing_artifact is None or (not existing_artifact.content and item.content):
                    artifact = self.domain_adapter.build_artifact(
                        item,
                        artifact_id=artifact_id,
                        policy=self.policy,
                    )
                    if artifact is not None:
                        artifact_by_id[artifact_id] = artifact
                        # === Lifecycle: on_artifact_built ===
                        notify = getattr(self.domain_adapter, "on_artifact_built", None)
                        if callable(notify):
                            notify(artifact)
                        self._notify_observers("on_artifact_built", artifact)
            # Build dialog act metadata if available
            dialog_act_metadata: dict[str, Any] = {}
            if dialog_act_result is not None:
                dialog_act_metadata = {
                    "dialog_act": dialog_act_result.act,
                    "dialog_act_confidence": dialog_act_result.confidence,
                    "dialog_act_triggers": list(dialog_act_result.triggers),
                    "dialog_act_is_high_priority": DialogAct.is_high_priority(dialog_act_result.act),
                }
            updated_events.append(
                validated_replace(
                    item,
                    route=route,
                    artifact_id=artifact_id,
                    metadata={
                        **dict(item.metadata),
                        **decision_metadata,
                        **dialog_act_metadata,
                        "routing_confidence": routing_confidence,
                        "routing_reasons": list(routing_reasons),
                        "routing_adapter_id": self.domain_adapter.adapter_id,
                    },
                )
            )

            # OPTIMIZATION: Pre-extract state hints for non-CLEAR events.
            # This avoids a second full transcript traversal in _patch_working_state.
            # Skip CLEAR events (they don't contribute to working state per _patch_working_state).
            if route != RoutingClass.CLEAR:
                hints = self.domain_adapter.extract_state_hints(updated_events[-1])
                if hints is not None:
                    state_hints_by_event_id[updated_events[-1].event_id] = hints

        # Handle unresolved pending follow-up (created but not yet responded)
        # If we have a pending action but it wasn't resolved in this turn
        if pending_followup_action and not pending_followup:
            pending_followup = PendingFollowUp(
                action=pending_followup_action,
                source_event_id=pending_followup_event_id,
                source_sequence=pending_followup_sequence,
                status="pending",
                updated_at=_utc_now_iso(),
            )

        artifacts = tuple(sorted(artifact_by_id.values(), key=lambda item: item.artifact_id))
        _route_dist: dict[str, int] = {}
        for evt in updated_events:
            _route_dist[evt.route] = _route_dist.get(evt.route, 0) + 1
        logger.debug(
            "[DEBUG][ContextOS] _canonicalize_and_offload end: events=%d artifacts=%d pending=%s routes=%s hints=%d",
            len(updated_events),
            len(artifacts),
            pending_followup.status if pending_followup else "none",
            _route_dist,
            len(state_hints_by_event_id),
        )
        return tuple(updated_events), artifacts, pending_followup, state_hints_by_event_id

    def _collect_active_window(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        working_state: Any,
        recent_window_messages: int,
        budget_plan: Any,
    ) -> tuple[TranscriptEvent, ...]:
        if not transcript:
            return ()
        min_recent_floor = max(1, int(self.policy.min_recent_messages_pinned or 1))
        min_recent_floor = min(self.policy.max_active_window_messages, min_recent_floor)
        recent_limit = max(min_recent_floor, int(recent_window_messages or 1))
        recent_limit = max(1, min(self.policy.max_active_window_messages, recent_limit))
        recent_candidates = list(transcript[-recent_limit:])
        forced_recent_ids = {item.event_id for item in transcript[-min_recent_floor:]}
        pinned_sequences: set[int] = {item.sequence for item in recent_candidates}
        for entry in (
            [working_state.task_state.current_goal] if working_state.task_state.current_goal is not None else []
        ):
            pinned_sequences.update(self._sequences_from_turns(entry.source_turns))
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
        pinned_events: dict[str, TranscriptEvent] = {}
        # Use policy-based allocation ratio instead of hard-coded 0.45 (T3-6)
        active_window_ratio = getattr(self.policy, "active_window_budget_ratio", 0.45)
        token_budget = max(512, min(budget_plan.soft_limit, int(budget_plan.input_budget * active_window_ratio)))
        token_count = 0
        for item in reversed(transcript):
            if item.route == RoutingClass.CLEAR and item.event_id not in forced_recent_ids:
                continue
            is_reopened = bool(str(get_metadata_value(item.metadata, "reopen_hold") or "").strip())
            is_root = (
                item.sequence in pinned_sequences
                or (item.artifact_id in active_artifact_ids)
                or is_reopened
                or item.event_id in forced_recent_ids
            )
            can_add = is_root or len(pinned_events) < self.policy.max_active_window_messages
            if not can_add:
                continue
            item_content = item.content
            estimated = _estimate_tokens(item_content)
            # Root items that exceed budget: truncate content instead of skipping
            if token_count + estimated > token_budget and is_root:
                # Truncate to fit within remaining budget using token-consistent formula.
                # Use iterative truncation: start with ASCII-friendly estimate and verify
                # against _estimate_tokens (ascii_chars/4 + cjk_chars*1.5) until token count fits.
                remaining_budget = token_budget - token_count
                remaining_chars = max(512, remaining_budget * 4)  # ASCII: 1 token ≈ 4 chars
                if remaining_chars < len(item_content):
                    item_content = _trim_text(item_content, max_chars=remaining_chars)
                    truncated_tokens = _estimate_tokens(item_content)
                    # Iterate if token estimate still exceeds budget (handles CJK text)
                    while truncated_tokens > remaining_budget and remaining_chars > 128:
                        remaining_chars = int(remaining_chars * 0.8)
                        item_content = _trim_text(item_content, max_chars=remaining_chars)
                        truncated_tokens = _estimate_tokens(item_content)
                    logger.warning(
                        "Root event content truncated due to token budget: event_id=%s, "
                        "original_tokens=%d, truncated_to=%d, token_budget=%d",
                        item.event_id,
                        estimated,
                        truncated_tokens,
                        token_budget,
                    )
                estimated = _estimate_tokens(item_content)
            if token_count + estimated > token_budget:
                if is_root:
                    # Root items still over budget even after truncation - log warning and add anyway
                    logger.warning(
                        "Token budget exceeded for root event (after truncation): event_id=%s, "
                        "sequence=%d, estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                else:
                    # Non-root events are skipped when over budget
                    logger.debug(
                        "Skipping non-root event due to token budget: event_id=%s, sequence=%d, "
                        "estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                    continue
            if item.event_id in pinned_events:
                continue

            # Use truncated content if applicable
            from dataclasses import replace

            pinned_item = replace(item, content=item_content) if item_content != item.content else item
            pinned_events[item.event_id] = pinned_item
            token_count += estimated
        result = tuple(sorted(pinned_events.values(), key=lambda item: (item.sequence, item.event_id)))
        logger.debug(
            "[DEBUG][ContextOS] _collect_active_window: recent_limit=%d pinned=%d token_count=%d/%d budget=%s",
            recent_limit,
            len(result),
            token_count,
            token_budget,
            budget_plan.input_budget if budget_plan else 0,
        )
        return result
