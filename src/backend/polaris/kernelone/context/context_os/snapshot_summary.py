"""Lightweight ContextOS snapshot summaries.

This module owns prompt-facing summary extraction for ContextOS snapshots.  It
accepts snapshot-like objects from both the current Pydantic model family and
older persisted dataclass snapshots without importing the deprecated model
module.
"""

from __future__ import annotations

from typing import Any


class SnapshotSummaryView:
    """Lightweight snapshot summary that avoids full ``to_dict()`` serialization."""

    @staticmethod
    def from_snapshot(snapshot: Any) -> dict[str, Any]:
        """Extract only the fields needed for LLM context injection.

        The method intentionally uses structural access because ContextOS may
        load old dataclass snapshots and current Pydantic V2 snapshots from the
        same persisted runtime store.
        """
        working_state = getattr(snapshot, "working_state", None)
        task_state = getattr(working_state, "task_state", None) if working_state is not None else None
        current_goal = getattr(task_state, "current_goal", None) if task_state is not None else None
        goal_value = getattr(current_goal, "value", None) if current_goal is not None else None

        return {
            "version": getattr(snapshot, "version", None),
            "transcript_events_count": len(getattr(snapshot, "transcript_log", ()) or ()),
            "goal": goal_value,
            "open_loops_count": len(getattr(task_state, "open_loops", ()) or ()) if task_state is not None else 0,
            "decisions_count": (
                len(getattr(working_state, "decision_log", ()) or ()) if working_state is not None else 0
            ),
            "artifacts_count": len(getattr(snapshot, "artifact_store", ()) or ()),
            "episodes_count": len(getattr(snapshot, "episode_store", ()) or ()),
            "has_pending_followup": getattr(snapshot, "pending_followup", None) is not None,
            "content_map_entries": len(getattr(snapshot, "content_map", {}) or {}),
        }


__all__ = ["SnapshotSummaryView"]
