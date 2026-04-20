"""Canonical Finite State Machine for ``runtime.task_market`` stage transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import FSMTransitionError

if TYPE_CHECKING:
    from .models import TaskWorkItemRecord


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Queued stages — tasks sitting in the market waiting to be claimed.
QUEUE_STAGES: frozenset[str] = frozenset(
    {
        "pending_design",
        "pending_exec",
        "pending_qa",
        "waiting_human",
    }
)

# In-progress stages — tasks actively being worked on (lease held).
IN_PROGRESS_STAGES: frozenset[str] = frozenset(
    {
        "in_design",
        "in_execution",
        "in_qa",
    }
)

# Terminal stages — tasks that have reached a final resolved state.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "resolved",
        "rejected",
        "dead_letter",
    }
)

# All valid stages.
ALL_STAGES: frozenset[str] = QUEUE_STAGES | IN_PROGRESS_STAGES | TERMINAL_STATUSES

# Mapping from queue stage -> in-progress status label.
IN_PROGRESS_BY_QUEUE_STAGE: dict[str, str] = {
    "pending_design": "in_design",
    "pending_exec": "in_execution",
    "pending_qa": "in_qa",
    "waiting_human": "waiting_human",
}

# Priority ordering (higher = more urgent).
PRIORITY_WEIGHT: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# ---------------------------------------------------------------------------
# FSM Rules
# ---------------------------------------------------------------------------

# Legal state transitions, keyed by event name.
# Format: from_stage -> {to_stage: (guard_condition_or_None), ...}
# A guard of None means the transition is always allowed.
_TRANSITION_RULES: dict[str, dict[str, tuple[str, str]]] = {
    # ── Publish ──────────────────────────────────────────────────────────────
    "publish": {
        # "" represents the initial state (task creation).
        "": ("_always", "publish initial task"),
        # Any queue stage can be the target of republish.
        "pending_design": ("_always", "publish to pending_design"),
        "pending_exec": ("_always", "publish to pending_exec"),
        "pending_qa": ("_always", "publish to pending_qa"),
        "waiting_human": ("_always", "publish to waiting_human"),
    },
    # ── Claim ─────────────────────────────────────────────────────────────────
    "claim": {
        "pending_design": ("_always", "claim pending_design"),
        "pending_exec": ("_always", "claim pending_exec"),
        "pending_qa": ("_always", "claim pending_qa"),
        "waiting_human": ("_always", "claim waiting_human"),
    },
    # ── Acknowledge ──────────────────────────────────────────────────────────
    "ack": {
        # in_design can advance to pending_exec, or requeue to pending_design
        # (DESIGN_FAILED expressed via requeue_stage="pending_design").
        "in_design": ("_always", "ack from in_design"),
        # in_execution can advance to pending_qa, or retry via requeue.
        "in_execution": ("_always", "ack from in_execution"),
        # pending_qa and waiting_human only accept terminal ack.
        "pending_qa": ("_is_terminal_target", "ack -> resolved/rejected"),
        "waiting_human": ("_is_terminal_target", "ack -> resolved/rejected"),
        # Terminal statuses only accept terminal ack.
        "resolved": ("_is_terminal_target", "ack -> resolved"),
        "rejected": ("_is_terminal_target", "ack -> rejected"),
        "dead_letter": ("_is_terminal_target", "ack -> dead_letter"),
    },
    # ── Fail ─────────────────────────────────────────────────────────────────
    # All non-terminal statuses can fail (retry or dead-letter).
    "fail": {
        "pending_design": ("_always", "fail from pending_design"),
        "in_design": ("_always", "fail from in_design"),
        "pending_exec": ("_always", "fail from pending_exec"),
        "in_execution": ("_always", "fail from in_execution"),
        "pending_qa": ("_always", "fail from pending_qa"),
        "in_qa": ("_always", "fail from in_qa"),
        "waiting_human": ("_always", "fail from waiting_human"),
    },
    # ── Requeue ──────────────────────────────────────────────────────────────
    "requeue": {
        "pending_design": ("_always", "requeue to pending_design"),
        "pending_exec": ("_always", "requeue to pending_exec"),
        "pending_qa": ("_always", "requeue to pending_qa"),
    },
    # ── Dead Letter ───────────────────────────────────────────────────────────
    # Explicit dead-letter move from any non-terminal status.
    "dead_letter": {
        "pending_design": ("_always", "dead_letter from pending_design"),
        "in_design": ("_always", "dead_letter from in_design"),
        "pending_exec": ("_always", "dead_letter from pending_exec"),
        "in_execution": ("_always", "dead_letter from in_execution"),
        "pending_qa": ("_always", "dead_letter from pending_qa"),
        "in_qa": ("_always", "dead_letter from in_qa"),
        "waiting_human": ("_always", "dead_letter from waiting_human"),
    },
}


# ---------------------------------------------------------------------------
# TaskStageFSM
# ---------------------------------------------------------------------------


class TaskStageFSM:
    """Canonical FSM for task market stage transitions.

    This FSM enforces the single-writer principle: only one authoritative
    component (``TaskMarketService``) mutates task state; all others
    interact via the publish / claim / ack / fail / requeue commands.

    State Diagram
    -------------
    ``[*]`` --> PENDING_DESIGN
    PENDING_DESIGN --> IN_DESIGN: claim
    IN_DESIGN --> PENDING_EXEC: ack (blueprint ready)
    IN_DESIGN --> PENDING_DESIGN: fail+requeue_stage=pending_design (design failed)

    PENDING_EXEC --> IN_EXECUTION: claim
    IN_EXECUTION --> PENDING_QA: ack (execution complete)
    IN_EXECUTION --> PENDING_EXEC: fail+requeue_stage=pending_exec (retry)

    PENDING_QA --> RESOLVED: ack(terminal_status=resolved)
    PENDING_QA --> REJECTED: ack(terminal_status=rejected)
    REJECTED --> PENDING_EXEC: fail+requeue_stage=pending_exec
    REJECTED --> PENDING_DESIGN: fail+requeue_stage=pending_design

    Any non-terminal --> DEAD_LETTER: fail(to_dead_letter=True) | retry_exhausted

    DEAD_LETTER --> WAITING_HUMAN: manual escalation
    WAITING_HUMAN --> PENDING_DESIGN: human resolve (requeue_design)
    WAITING_HUMAN --> PENDING_EXEC: human resolve (requeue_exec)
    WAITING_HUMAN --> RESOLVED: human resolve (force_resolve)
    WAITING_HUMAN --> REJECTED: human resolve (close_as_invalid)
    """

    __slots__ = ()

    # ---- Public API ---------------------------------------------------------

    def validate_transition(
        self,
        item: TaskWorkItemRecord,
        event: str,
        *,
        next_stage: str | None = None,
        terminal_status: str | None = None,
    ) -> None:
        """Validate that a state transition is legal.

        Raises:
            FSMTransitionError: if the transition is not allowed.
        """
        from_status = item.status
        to_status = terminal_status or (next_stage if next_stage else from_status)

        if not self._is_allowed(event, from_status, to_status, next_stage, terminal_status):
            raise FSMTransitionError(
                f"Illegal transition: event={event!r} from_status={from_status!r} to_status={to_status!r}",
                task_id=item.task_id,
                from_status=from_status,
                to_status=to_status,
                event=event,
            )

    def can_transition(
        self,
        from_status: str,
        to_status: str,
        event: str,
        *,
        next_stage: str | None = None,
        terminal_status: str | None = None,
    ) -> bool:
        """Return True if the transition is allowed, False otherwise."""
        return self._is_allowed(event, from_status, to_status, next_stage, terminal_status)

    def get_in_progress_status(self, queue_stage: str) -> str:
        """Return the in-progress status label for a queue stage."""
        return IN_PROGRESS_BY_QUEUE_STAGE.get(queue_stage, "")

    def get_queue_stage_for_status(self, status: str) -> str | None:
        """Return the canonical queue stage for an in-progress status."""
        for qs, ips in IN_PROGRESS_BY_QUEUE_STAGE.items():
            if ips == status:
                return qs
        return None

    def is_terminal(self, status: str) -> bool:
        """Return True if the status is terminal."""
        return status in TERMINAL_STATUSES

    def is_queue_stage(self, stage: str) -> bool:
        """Return True if the stage is a queue stage."""
        return stage in QUEUE_STAGES

    def is_in_progress(self, status: str) -> bool:
        """Return True if the status is in-progress."""
        return status in IN_PROGRESS_STAGES

    # ---- Internal helpers ---------------------------------------------------

    def _is_allowed(
        self,
        event: str,
        from_status: str,
        to_status: str,
        next_stage: str | None,
        terminal_status: str | None,
    ) -> bool:
        """Check if a transition is allowed under the FSM rules."""
        rules = _TRANSITION_RULES.get(event, {})
        rule = rules.get(from_status)

        if not rule:
            return False

        guard, _description = rule

        if guard == "_always":
            # For claim events, additionally validate that to_status matches
            # the expected in-progress status for this queue stage.
            if event == "claim":
                expected = IN_PROGRESS_BY_QUEUE_STAGE.get(from_status, "")
                return to_status == expected
            return True
        if guard == "_is_terminal_target":
            # Terminal status transitions are only allowed when explicitly set.
            return terminal_status is not None and terminal_status in TERMINAL_STATUSES
        if guard == "_requires_next_stage":
            return next_stage is not None
        if guard == "_requires_terminal":
            return terminal_status is not None

        return False


# ---- Module-level singleton -----------------------------------------------

_fsm: TaskStageFSM | None = None


def get_fsm() -> TaskStageFSM:
    global _fsm
    if _fsm is None:
        _fsm = TaskStageFSM()
    return _fsm


__all__ = [
    "ALL_STAGES",
    "IN_PROGRESS_BY_QUEUE_STAGE",
    "IN_PROGRESS_STAGES",
    "PRIORITY_WEIGHT",
    "QUEUE_STAGES",
    "TERMINAL_STATUSES",
    "FSMTransitionError",
    "TaskStageFSM",
    "get_fsm",
]
