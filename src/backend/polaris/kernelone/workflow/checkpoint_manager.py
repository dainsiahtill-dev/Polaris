"""Checkpoint Manager - Workflow State Recovery and Checkpoint Management.

This module provides checkpoint management for the Chronos Hourglass system.
It enables:
1. Periodic checkpoint creation during workflow execution
2. Workflow state recovery after service restart
3. Checkpoint listing and inspection

Checkpoint is implemented as an event-sourced pattern using the existing
WorkflowRuntimeStore. Checkpoints are stored as events with type
`workflow_checkpoint` in the event log.

Design principles:
- All state is already persisted via WorkflowRuntimeStore (SQLite)
- Checkpoints are bookmarks in the event stream for fast replay
- On restart, we replay events since last checkpoint to reconstruct state
- No duplicate storage - reuses existing event infrastructure

References:
- Base: kernelone/workflow/engine.py (WorkflowRuntimeStore protocol)
- Saga: kernelone/workflow/saga_engine.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils import _now

if TYPE_CHECKING:
    from .engine import WorkflowRuntimeStorePort

logger = logging.getLogger(__name__)

# Checkpoint event types
_EVENT_CHECKPOINT_CREATED = "checkpoint_created"
_EVENT_CHECKPOINT_RESTORED = "checkpoint_restored"


@dataclass
class CheckpointRecord:
    """A checkpoint record stored in the event log."""

    checkpoint_id: str
    workflow_id: str
    created_at: str
    seq: int  # Last event seq at time of checkpoint
    task_states_snapshot: dict[str, dict[str, Any]]
    task_outputs: dict[str, dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckpointSummary:
    """Summary of a checkpoint for listing."""

    checkpoint_id: str
    workflow_id: str
    created_at: str
    seq: int
    task_count: int


class CheckpointManager:
    """Manages workflow checkpoints for recovery.

    This manager provides:
    1. Creating checkpoints during workflow execution
    2. Listing available checkpoints
    3. Reconstructing state from checkpoints

    Checkpoints are stored as events in the WorkflowRuntimeStore, making
    them naturally persistent and replicated with the store.

    The checkpoint contains:
    - Snapshot of all task states at checkpoint time
    - All task outputs accumulated so far
    - Event sequence number (for replay optimization)

    On recovery, the workflow engine replays events from the last checkpoint
    to reconstruct the full state, rather than from event #1.
    """

    def __init__(self, store: WorkflowRuntimeStorePort) -> None:
        """Initialize checkpoint manager.

        Args:
            store: WorkflowRuntimeStorePort implementation (e.g., SqliteRuntimeStore)
        """
        self._store = store

    @staticmethod
    def _now() -> str:
        return _now()

    @staticmethod
    def _get_event_attr(event: Any, attr: str, default: Any = None) -> Any:
        """Get an attribute from an event, handling both dict and object styles."""
        if isinstance(event, dict):
            return event.get(attr, default)
        return getattr(event, attr, default)

    async def create_checkpoint(
        self,
        workflow_id: str,
        task_states: dict[str, Any],
        task_outputs: dict[str, dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        """Create a checkpoint of the current workflow state.

        Args:
            workflow_id: Workflow identifier.
            task_states: Current task states (task_id -> state dict).
            task_outputs: All task outputs accumulated so far.
            metadata: Optional metadata to store with checkpoint.

        Returns:
            CheckpointRecord with checkpoint details.
        """
        checkpoint_id = f"chk_{workflow_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        now = self._now()

        # Get the current event sequence number
        events = await self._store.get_events(workflow_id, limit=1)
        last_seq = self._get_event_attr(events[-1], "seq", 0) if events else 0

        # Serialize task states
        task_states_snapshot = {}
        for task_id, state in task_states.items():
            if hasattr(state, "__dict__"):
                # Convert dataclass to dict
                task_states_snapshot[task_id] = {
                    "task_id": getattr(state, "task_id", task_id),
                    "status": getattr(state, "status", "unknown"),
                    "attempt": getattr(state, "attempt", 0),
                    "result": getattr(state, "result", None),
                    "error": getattr(state, "error", ""),
                }
            elif isinstance(state, dict):
                task_states_snapshot[task_id] = state

        checkpoint_payload = {
            "checkpoint_id": checkpoint_id,
            "task_states_snapshot": task_states_snapshot,
            "task_outputs": task_outputs,
            "last_seq": last_seq,
            "metadata": metadata or {},
        }

        # Append checkpoint event
        await self._store.append_event(
            workflow_id,
            _EVENT_CHECKPOINT_CREATED,
            checkpoint_payload,
        )

        logger.info(
            "Checkpoint created: %s for workflow %s (seq=%d, tasks=%d)",
            checkpoint_id,
            workflow_id,
            last_seq,
            len(task_states_snapshot),
        )

        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            workflow_id=workflow_id,
            created_at=now,
            seq=last_seq,
            task_states_snapshot=task_states_snapshot,
            task_outputs=task_outputs,
            metadata=metadata or {},
        )

    async def get_latest_checkpoint(
        self,
        workflow_id: str,
    ) -> CheckpointRecord | None:
        """Get the most recent checkpoint for a workflow.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            CheckpointRecord if found, None otherwise.
        """
        events = await self._store.get_events(workflow_id, limit=1000)

        # Search backwards for latest checkpoint
        for event in reversed(events):
            if self._get_event_attr(event, "event_type") == _EVENT_CHECKPOINT_CREATED:
                payload = self._get_event_attr(event, "payload", {}) or {}
                return CheckpointRecord(
                    checkpoint_id=payload.get("checkpoint_id", ""),
                    workflow_id=workflow_id,
                    created_at=self._get_event_attr(event, "created_at", ""),
                    seq=payload.get("last_seq", 0),
                    task_states_snapshot=payload.get("task_states_snapshot", {}),
                    task_outputs=payload.get("task_outputs", {}),
                    metadata=payload.get("metadata", {}),
                )

        return None

    async def list_checkpoints(
        self,
        workflow_id: str,
    ) -> list[CheckpointSummary]:
        """List all checkpoints for a workflow.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            List of CheckpointSummary ordered by creation time (newest first).
        """
        events = await self._store.get_events(workflow_id, limit=10000)

        checkpoints: list[CheckpointSummary] = []
        for event in events:
            if self._get_event_attr(event, "event_type") == _EVENT_CHECKPOINT_CREATED:
                payload = self._get_event_attr(event, "payload", {}) or {}
                task_states = payload.get("task_states_snapshot", {})
                checkpoints.append(
                    CheckpointSummary(
                        checkpoint_id=payload.get("checkpoint_id", ""),
                        workflow_id=workflow_id,
                        created_at=self._get_event_attr(event, "created_at", ""),
                        seq=payload.get("last_seq", 0),
                        task_count=len(task_states),
                    )
                )

        # Sort by created_at descending (newest first)
        checkpoints.sort(key=lambda c: c.created_at, reverse=True)
        return checkpoints

    async def get_checkpoint_events_since(
        self,
        workflow_id: str,
        since_seq: int,
        limit: int = 1000,
    ) -> list[Any]:
        """Get events since a checkpoint sequence number.

        This is used for fast replay during recovery - instead of replaying
        all events from #1, we only replay events since the last checkpoint.

        Args:
            workflow_id: Workflow identifier.
            since_seq: Starting sequence number (exclusive).
            limit: Maximum number of events to return.

        Returns:
            List of events since the checkpoint.
        """
        all_events = await self._store.get_events(workflow_id, limit=10000)

        # Filter events after since_seq
        recent_events = [e for e in all_events if self._get_event_attr(e, "seq", 0) > since_seq]

        return recent_events[:limit]

    async def restore_checkpoint(
        self,
        workflow_id: str,
        checkpoint_id: str,
    ) -> CheckpointRecord | None:
        """Mark a checkpoint as restored (for audit trail).

        Args:
            workflow_id: Workflow identifier.
            checkpoint_id: Checkpoint to mark as restored.

        Returns:
            The restored CheckpointRecord if found.
        """
        checkpoint = await self._get_checkpoint_by_id(workflow_id, checkpoint_id)
        if checkpoint is None:
            return None

        await self._store.append_event(
            workflow_id,
            _EVENT_CHECKPOINT_RESTORED,
            {
                "checkpoint_id": checkpoint_id,
                "restored_at": self._now(),
                "seq": checkpoint.seq,
            },
        )

        logger.info(
            "Checkpoint restored: %s for workflow %s",
            checkpoint_id,
            workflow_id,
        )

        return checkpoint

    async def _get_checkpoint_by_id(
        self,
        workflow_id: str,
        checkpoint_id: str,
    ) -> CheckpointRecord | None:
        """Find a checkpoint by its ID."""
        events = await self._store.get_events(workflow_id, limit=10000)

        for event in events:
            if self._get_event_attr(event, "event_type") == _EVENT_CHECKPOINT_CREATED:
                payload = self._get_event_attr(event, "payload", {}) or {}
                if payload.get("checkpoint_id") == checkpoint_id:
                    return CheckpointRecord(
                        checkpoint_id=checkpoint_id,
                        workflow_id=workflow_id,
                        created_at=self._get_event_attr(event, "created_at", ""),
                        seq=payload.get("last_seq", 0),
                        task_states_snapshot=payload.get("task_states_snapshot", {}),
                        task_outputs=payload.get("task_outputs", {}),
                        metadata=payload.get("metadata", {}),
                    )

        return None

    async def get_recovery_info(
        self,
        workflow_id: str,
    ) -> dict[str, Any]:
        """Get information needed to recover a workflow.

        This is the main entry point for recovery - returns everything
        needed to reconstruct the workflow state.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            Dict with recovery information including:
            - latest_checkpoint: CheckpointRecord or None
            - events_since_checkpoint: List of events to replay
            - task_states: Latest task states from store
        """
        latest_checkpoint = await self.get_latest_checkpoint(workflow_id)

        if latest_checkpoint:
            events_since = await self.get_checkpoint_events_since(
                workflow_id,
                latest_checkpoint.seq,
            )
            # Filter out the checkpoint event itself - we only want events AFTER the checkpoint
            events_since = [
                e for e in events_since if self._get_event_attr(e, "event_type") != _EVENT_CHECKPOINT_CREATED
            ]
        else:
            # No checkpoint - need full replay
            events_since = await self._store.get_events(workflow_id, limit=10000)
            latest_checkpoint = None

        task_states = await self._store.list_task_states(workflow_id)

        return {
            "workflow_id": workflow_id,
            "latest_checkpoint": latest_checkpoint,
            "events_since_checkpoint": events_since,
            "task_states": task_states,
            "recovery_needed": bool(events_since),
        }
