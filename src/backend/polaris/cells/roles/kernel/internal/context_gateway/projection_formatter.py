"""Context gateway projection formatter - Convert ContextOS projections to messages.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import hashlib
from typing import Any

from polaris.kernelone.context.context_os.helpers import get_metadata_value
from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection
from polaris.kernelone.context.projection_engine import render_run_card

from .constants import HIGH_PRIORITY_DIALOG_ACTS, ROUTE_PRIORITY
from .prompt_safety import format_tool_failure_summary, parse_tool_failure_summary, prompt_safe_message_content


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
                    content = prompt_safe_message_content(str(role), event.get("content", ""))
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
                    content = prompt_safe_message_content(str(role), event.get("content", ""))[:80]
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
    ) -> list[dict[str, Any]]:
        """Expand context_os_snapshot.transcript_log into full dialogue messages.

        This is the core fix for Phase 5 context loss: transcript_log contains
        the complete event history (user, assistant, tool) with full content,
        not just summaries. We must expand it into proper message format for
        the LLM to understand the conversation context.
        """
        transcript = snapshot.get("transcript_log") or []
        if not transcript:
            return []

        messages: list[dict[str, Any]] = []
        for event in transcript:
            role = str(event.get("role") or "").strip().lower()
            content = str(event.get("content") or "")

            # Skip empty or invalid events
            if not role or not content:
                continue

            # Normalize role names (tool_result -> tool)
            if role == "tool_result":
                role = "tool"

            messages.append({"role": role, "content": prompt_safe_message_content(role, content)})

        return ProjectionFormatter.compact_tool_failure_summaries(messages)

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
    def compact_tool_failure_summaries(
        messages: list[dict[str, Any]],
        *,
        max_failure_kinds: int = 4,
    ) -> list[dict[str, Any]]:
        """Collapse repeated prompt-safe tool failures into one digest message."""

        if not messages:
            return []

        original_messages = [dict(message) for message in messages]
        aggregate: dict[tuple[str, str, str], dict[str, Any]] = {}
        first_failure_index: int | None = None
        result: list[dict[str, Any]] = []
        failure_count = 0
        for msg in messages:
            payload = parse_tool_failure_summary(msg.get("content", ""))
            if payload is None:
                result.append(msg)
                continue
            failure_count += 1
            if first_failure_index is None:
                first_failure_index = len(result)
                result.append({})
            key = (
                str(payload.get("tool") or "unknown"),
                str(payload.get("error_type") or "tool_failure"),
                str(payload.get("reason") or "tool execution failed"),
            )
            entry = aggregate.setdefault(
                key,
                {
                    "tool": key[0],
                    "error_type": key[1],
                    "reason": key[2],
                    "count": 0,
                },
            )
            entry["count"] = int(entry["count"]) + 1

        if first_failure_index is None or failure_count <= 1:
            return original_messages

        failures = sorted(aggregate.values(), key=lambda item: (-int(item["count"]), str(item["tool"])))
        included = failures[: max(1, int(max_failure_kinds))]
        digest = {
            "schema_version": "tool_failure_summary_digest.v1",
            "failure_count": sum(int(item["count"]) for item in failures),
            "unique_failure_count": len(failures),
            "failures": included,
            "omitted_failure_kinds": max(0, len(failures) - len(included)),
            "prompt_safe": True,
            "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
        }
        result[first_failure_index] = {
            "role": "system",
            "content": format_tool_failure_summary(digest),
            "name": "tool_failure_summary_digest",
        }
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
        import logging

        _logger = logging.getLogger(__name__)
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
        _route_counts: dict[str, int] = {}
        for evt in sorted_events:
            _route_counts[evt.route] = _route_counts.get(evt.route, 0) + 1
        _logger.debug(
            "[DEBUG][ProjectionFormatter] messages_from_projection: active_window=%d sorted=%d routes=%s run_card_goal=%r",
            len(projection.active_window),
            len(sorted_events),
            _route_counts,
            projection.run_card.current_goal if projection.run_card else "<none>",
        )

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
                content = (
                    prompt_safe_message_content(event.role, event.content)
                    if is_recent
                    else f"[Artifact stored: {artifact_id}]"
                )
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
                        "content": prompt_safe_message_content(event.role, event.content),
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
            rendered_run_card = render_run_card(
                projection.run_card,
                last_turn_outcome_sanitizer=lambda value: prompt_safe_message_content("assistant", value),
            )
            if rendered_run_card:
                messages.append(
                    {
                        "role": "system",
                        "content": rendered_run_card,
                        "name": "run_card",
                    }
                )

        # BUG FIX: Deduplicate messages by content hash to remove duplicate
        # events that accumulate through _merge_transcript and session_turn_events.
        # This prevents the LLM from seeing the same assistant content N times.
        before_dedupe = len(messages)
        messages = cls.dedupe_messages(messages)
        messages = cls.compact_tool_failure_summaries(messages)
        _logger.debug(
            "[DEBUG][ProjectionFormatter] messages_from_projection end: before_dedupe=%d after_dedupe=%d system=%d user=%d assistant=%d tool=%d",
            before_dedupe,
            len(messages),
            sum(1 for m in messages if m.get("role") == "system"),
            sum(1 for m in messages if m.get("role") == "user"),
            sum(1 for m in messages if m.get("role") == "assistant"),
            sum(1 for m in messages if m.get("role") == "tool"),
        )

        return messages


__all__ = ["ProjectionFormatter"]
