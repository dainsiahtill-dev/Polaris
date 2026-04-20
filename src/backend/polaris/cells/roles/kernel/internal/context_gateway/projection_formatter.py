"""Context gateway projection formatter - Convert ContextOS projections to messages.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import hashlib
from typing import Any

from polaris.kernelone.context.context_os.helpers import get_metadata_value
from polaris.kernelone.context.context_os.models import ContextOSProjection

from .constants import HIGH_PRIORITY_DIALOG_ACTS, ROUTE_PRIORITY


class ProjectionFormatter:
    """Formats ContextOSProjections into LLM-ready message lists."""

    @staticmethod
    def format_strategy_receipt_style(receipt: Any | None) -> str:
        """Format strategy receipt as a canonical system-message block.

        This is the canonical context format for role turns. When a StrategyReceipt
        is available (canonical path), this method formats it into a structured
        system message.
        """
        if receipt is None:
            return "【Strategy Context】\n(receipt unavailable)"

        lines = ["【Strategy Context】 (canonical format)"]

        # Identity
        lines.append(f"bundle: {receipt.bundle_id}")
        lines.append(f"profile: {receipt.profile_id}")
        lines.append(f"turn: {receipt.turn_index}")

        # Budget decisions
        if receipt.budget_decisions:
            lines.append(f"budget_decisions: {len(receipt.budget_decisions)} decision(s)")
            for bd in receipt.budget_decisions[:3]:
                lines.append(
                    f"  - {bd.kind.value}: {bd.decision} (tokens={bd.estimated_tokens}, headroom={bd.headroom_after})"
                )

        # Tool sequence
        if receipt.tool_sequence:
            lines.append(f"tool_sequence: {' → '.join(receipt.tool_sequence)}")

        # Exploration phase
        phase = getattr(receipt, "exploration_phase_reached", "") or ""
        if phase:
            lines.append(f"exploration_phase: {phase}")

        # Cache stats
        hits = getattr(receipt, "cache_hits", ()) or ()
        misses = getattr(receipt, "cache_misses", ()) or ()
        if hits or misses:
            lines.append(f"cache_hits: {len(hits)}, misses: {len(misses)}")

        # Compaction
        compaction = getattr(receipt, "compaction_triggered", False)
        if compaction:
            lines.append("compaction: triggered this turn")

        return "\n".join(lines)

    @staticmethod
    def format_context_os_snapshot(
        snapshot: dict[str, Any],
        verbosity: str = "summary",
    ) -> str:
        """Format ContextOS snapshot (from session) as a system-message block.

        Phase 5: This is the direct path for Context OS projection injection.
        The snapshot contains transcript_log, working_state, artifact_store, etc.
        """
        lines = ["【Context OS State】"]

        # Transcript summary
        transcript = snapshot.get("transcript_log") or []
        if transcript:
            lines.append(f"transcript_events: {len(transcript)} event(s)")

            if verbosity == "debug":
                # Full: print all events with metadata
                for event in transcript:
                    role = event.get("role", "?")
                    content = str(event.get("content", ""))
                    event_id = event.get("event_id", "")
                    sequence = event.get("sequence", 0)
                    metadata = event.get("metadata", {})
                    route = metadata.get("route", "")
                    dialog_act = metadata.get("dialog_act", "")

                    lines.append(f"  [seq={sequence}] {role} (id={event_id[:12]}) route={route} act={dialog_act}")
                    lines.append(f"    content: {content[:200]}...")
            else:
                # Summary: show last 5 events
                for event in transcript[-5:]:
                    role = event.get("role", "?")
                    content = str(event.get("content", ""))[:80]
                    lines.append(f"  [{role}] {content}...")
        else:
            lines.append("transcript_events: (empty)")

        # Working state summary
        working = snapshot.get("working_state") or {}
        if working:
            current_task = working.get("current_task", "")
            if current_task:
                lines.append(f"current_task: {current_task}")

        # Artifact store summary
        artifacts = snapshot.get("artifact_store") or []
        if artifacts:
            lines.append(f"artifacts: {len(artifacts)} record(s)")

        # Pending follow-up
        pending = snapshot.get("pending_followup") or {}
        if pending:
            pending_desc = pending.get("description", "")
            lines.append(f"pending_followup: {pending_desc}")

        return "\n".join(lines)

    @staticmethod
    def expand_transcript_to_messages(
        snapshot: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Expand context_os_snapshot.transcript_log into full dialogue messages.

        This is the core fix for Phase 5 context loss: transcript_log contains
        the complete event history (user, assistant, tool) with full content,
        not just summaries. We must expand it into proper message format for
        the LLM to understand the conversation context.
        """
        transcript = snapshot.get("transcript_log") or []
        if not transcript:
            return []

        messages: list[dict[str, str]] = []
        for event in transcript:
            role = str(event.get("role") or "").strip().lower()
            content = str(event.get("content") or "")

            # Skip empty or invalid events
            if not role or not content:
                continue

            # Normalize role names (tool_result -> tool)
            if role == "tool_result":
                role = "tool"

            messages.append({"role": role, "content": content})

        return messages

    @staticmethod
    def dedupe_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Deduplicate messages by content hash to avoid double-inclusion.

        When both snapshot.transcript_log and request.history contain the same
        events (e.g., from seeding _history from snapshot), we need to remove
        duplicates. We keep the first occurrence to preserve its metadata.
        """
        if not messages:
            return []

        seen: dict[str, int] = {}  # content_hash -> index
        result: list[dict[str, str]] = []

        for msg in messages:
            role = str(msg.get("role") or "")
            content = str(msg.get("content") or "")
            # T6-4 Fix: Use SHA-256 hash to avoid collision with truncated content
            content_hash = f"{role}:{hashlib.sha256(content.encode()).hexdigest()[:32]}"

            if content_hash in seen:
                # Skip duplicate - keep first occurrence with its metadata
                continue
            else:
                seen[content_hash] = len(result)
                result.append(msg)

        return result

    @staticmethod
    def dialog_act_priority(event: Any) -> int:
        """Return priority boost for high-value dialog acts.

        Args:
            event: TranscriptEvent with metadata.

        Returns:
            Priority boost (0, 1, or 2) for dialog act priority.
        """
        if not event.metadata:
            return 0
        act = str(get_metadata_value(event.metadata, "dialog_act", ""))
        if act.lower() in HIGH_PRIORITY_DIALOG_ACTS:
            return 2
        return 0

    @classmethod
    def sort_events_by_routing_priority(cls, active_window: tuple[Any, ...]) -> list[Any]:
        """Sort events by routing priority for message selection.

        Route priority: PATCH > SUMMARIZE > ARCHIVE > CLEAR.
        Within same route, prefers higher routing_confidence.
        High-priority dialog acts get additional boost.
        """
        if not active_window:
            return []

        def event_priority_key(event: Any) -> tuple[int, int, float]:
            """Compute priority key for sorting."""
            route = str(event.route or "clear").lower()
            route_priority = ROUTE_PRIORITY.get(route, 0)

            # Confidence from routing decision
            confidence = 0.5
            if event.metadata:
                confidence = float(get_metadata_value(event.metadata, "routing_confidence", 0.5))

            # Boost for high-priority dialog acts
            dialog_act_boost = cls.dialog_act_priority(event)

            # Combined confidence with dialog act boost (capped at 1.0)
            combined_confidence = min(1.0, confidence + (dialog_act_boost * 0.1))

            # FIX: Use sequence as primary key to maintain chronological order
            # Route priority is only used as tiebreaker for same-sequence events
            return (int(event.sequence), -route_priority, -combined_confidence)

        return sorted(active_window, key=event_priority_key)

    @classmethod
    def messages_from_projection(cls, projection: ContextOSProjection) -> list[dict[str, Any]]:
        """Convert ContextOSProjection to message list for LLM.

        Uses the projection's active_window with routing decisions to prioritize
        high-value events. Route priority: PATCH > SUMMARIZE > ARCHIVE > CLEAR.
        Within same route, prefers higher routing_confidence.
        High-priority dialog acts (affirm, deny, pause, redirect, clarify) get boosted priority.
        """
        messages: list[dict[str, Any]] = []

        # Add head anchor (summary of context state)
        if projection.head_anchor:
            messages.append(
                {
                    "role": "system",
                    "content": projection.head_anchor,
                    "name": "context_head_anchor",
                }
            )

        # Sort active_window by routing priority
        sorted_events = cls.sort_events_by_routing_priority(projection.active_window)

        # Add active window events with routing-aware processing
        for event in sorted_events:
            route = str(event.route or "clear").lower()

            # Skip CLEAR events unless they're recent/forced
            if route == "clear":
                is_forced = bool(get_metadata_value(event.metadata, "reopen_hold")) if event.metadata else False
                is_recent = event.sequence >= sorted_events[-1].sequence - 3 if sorted_events else False
                if not is_forced and not is_recent:
                    continue

            # ARCHIVE events: include stub only (content already offloaded)
            # FIX: Keep full content for recent events (last 3) to ensure tool results are visible
            if route == "archive":
                artifact_id = event.artifact_id or event.event_id
                is_recent = event.sequence >= sorted_events[-1].sequence - 3 if sorted_events else False
                metadata = dict(event.metadata) if event.metadata else {}
                metadata["route"] = route
                metadata["artifact_id"] = artifact_id
                # Keep full content for recent events, use stub for older ones
                content = event.content if is_recent else f"[Artifact stored: {artifact_id}]"
                messages.append(
                    {
                        "role": event.role,
                        "content": content,
                        "metadata": metadata,
                    }
                )
            else:
                metadata = dict(event.metadata) if event.metadata else {}
                metadata["route"] = route
                messages.append(
                    {
                        "role": event.role,
                        "content": event.content,
                        "metadata": metadata,
                    }
                )

        # Add tail anchor
        if projection.tail_anchor:
            messages.append(
                {
                    "role": "system",
                    "content": projection.tail_anchor,
                    "name": "context_tail_anchor",
                }
            )

        # Add run card as a system message for attention observability
        if projection.run_card is not None:
            run_card = projection.run_card
            run_card_lines = ["【Run Card】"]
            if run_card.current_goal:
                run_card_lines.append(f"Goal: {run_card.current_goal}")
            if run_card.open_loops:
                run_card_lines.append(f"Open loops: {len(list(run_card.open_loops))}")
            if run_card.latest_user_intent:
                run_card_lines.append(f"Latest intent: {run_card.latest_user_intent[:100]}")
            if run_card.pending_followup_action:
                run_card_lines.append(f"Pending: {run_card.pending_followup_action}")
            if run_card.last_turn_outcome:
                run_card_lines.append(f"Last outcome: {run_card.last_turn_outcome}")
            messages.append(
                {
                    "role": "system",
                    "content": "\n".join(run_card_lines),
                    "name": "run_card",
                }
            )

        # BUG FIX: Deduplicate messages by content hash to remove duplicate
        # events that accumulate through _merge_transcript and session_turn_events.
        # This prevents the LLM from seeing the same assistant content N times.
        messages = cls.dedupe_messages(messages)

        return messages


__all__ = ["ProjectionFormatter"]
