"""Task Orchestration Audit Interceptor for tracking task DAGs.

This interceptor subscribes to the OmniscientAuditBus and processes
task orchestration events, tracking state transitions and detecting
potential issues like deadlocks and timeout warnings.

Design:
- Subscribes to task state transition events
- Tracks task dependencies and builds mini DAG
- Detects deadlocks (tasks waiting on each other)
- Detects timeout warnings
- Aggregates task execution metrics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

logger = logging.getLogger(__name__)


# Task states
class TaskState:
    """Task state constants."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskOrchestrationInterceptor(BaseAuditInterceptor):
    """Interceptor for auditing task orchestration.

    Captures:
    - Task state transitions
    - Task dependencies
    - Deadlock detection
    - Timeout warnings
    - DAG structure

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = TaskOrchestrationInterceptor(bus)
        # Events will be automatically processed
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        deadlock_detection: bool = True,
        timeout_warning_threshold_ms: int = 300000,
    ) -> None:
        """Initialize the task orchestration interceptor.

        Args:
            bus: The audit bus to subscribe to.
            deadlock_detection: Whether to enable deadlock detection.
            timeout_warning_threshold_ms: Threshold for timeout warnings.
        """
        super().__init__(name="task_audit", priority=AuditPriority.INFO)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Task state tracking
        self._task_states: dict[str, str] = {}
        self._task_dependencies: dict[str, list[str]] = {}
        self._task_start_times: dict[str, float] = {}
        self._task_blocked_by: dict[str, list[str]] = {}
        self._task_waiting_on: dict[str, set[str]] = {}  # task_id -> set of task_ids it's waiting on

        # DAG tracking
        self._dag_id: str | None = None
        self._dag_tasks: list[str] = []

        # Detection features
        self._deadlock_detection = deadlock_detection
        self._timeout_warning_threshold_ms = timeout_warning_threshold_ms
        self._deadlocks_detected: list[dict[str, Any]] = []
        self._timeout_warnings: list[dict[str, Any]] = []

        # Metrics
        self._state_transitions: dict[str, int] = {}
        self._completed_tasks = 0
        self._failed_tasks = 0
        self._retried_tasks = 0

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process a task audit event.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        # Call base implementation first for stats tracking
        super().intercept(event)

        # Extract event data
        if isinstance(event, AuditEventEnvelope):
            event_data = event.event
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        # Process task orchestration events
        if isinstance(event_data, dict):
            event_type = event_data.get("type", "")
            if event_type in (
                "task_submitted",
                "task_claimed",
                "task_started",
                "task_completed",
                "task_failed",
                "task_cancelled",
                "task_retry",
                "task_orchestration",
            ):
                self._process_task_event(event_data)

    def _process_task_event(self, event: dict[str, Any]) -> None:
        """Process a single task orchestration event.

        Args:
            event: The task orchestration event dict.
        """

        event_type = event.get("type", "")
        task_id = event.get("task_id", "")
        dag_id = event.get("dag_id", "") or self._dag_id or ""

        # Track DAG ID
        if dag_id and self._dag_id is None:
            self._dag_id = dag_id
        if task_id and task_id not in self._dag_tasks:
            self._dag_tasks.append(task_id)

        # Process based on event type
        if event_type == "task_submitted":
            self._handle_task_submitted(event)
        elif event_type == "task_claimed":
            self._handle_task_claimed(event)
        elif event_type == "task_started":
            self._handle_task_started(event)
        elif event_type == "task_completed":
            self._handle_task_completed(event)
        elif event_type == "task_failed":
            self._handle_task_failed(event)
        elif event_type == "task_cancelled":
            self._handle_task_cancelled(event)
        elif event_type == "task_retry":
            self._handle_task_retry(event)
        elif event_type == "task_orchestration":
            self._handle_task_orchestration(event)

        # Check for deadlock
        if self._deadlock_detection:
            self._check_deadlock()

        logger.debug(
            "[task_audit] Processed task event: type=%s, task_id=%s",
            event_type,
            task_id,
        )

    def _handle_task_submitted(self, event: dict[str, Any]) -> None:
        """Handle task submitted event.

        Args:
            event: The task submitted event.
        """
        task_id = event.get("task_id", "")
        blocked_by = event.get("blocked_by", [])

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.SUBMITTED
        self._record_transition(task_id, previous_state, TaskState.SUBMITTED)

        if blocked_by:
            self._task_blocked_by[task_id] = blocked_by
            # Update waiting relationships
            for blocker_id in blocked_by:
                if blocker_id not in self._task_waiting_on:
                    self._task_waiting_on[blocker_id] = set()
                if task_id not in self._task_waiting_on[blocker_id]:
                    # This is inverted: task_id is blocked by blocker_id
                    # So blocker_id completes -> task_id can proceed
                    pass

    def _handle_task_claimed(self, event: dict[str, Any]) -> None:
        """Handle task claimed event.

        Args:
            event: The task claimed event.
        """
        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.CLAIMED
        self._record_transition(task_id, previous_state, TaskState.CLAIMED)

    def _handle_task_started(self, event: dict[str, Any]) -> None:
        """Handle task started event.

        Args:
            event: The task started event.
        """
        import time

        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.RUNNING
        self._task_start_times[task_id] = time.time()
        self._record_transition(task_id, previous_state, TaskState.RUNNING)

    def _handle_task_completed(self, event: dict[str, Any]) -> None:
        """Handle task completed event.

        Args:
            event: The task completed event.
        """
        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.COMPLETED
        self._completed_tasks += 1
        self._record_transition(task_id, previous_state, TaskState.COMPLETED)

        # Clean up waiting relationships
        self._task_start_times.pop(task_id, None)
        self._task_blocked_by.pop(task_id, None)

        # Remove this task from others' waiting lists
        for waiting_set in self._task_waiting_on.values():
            waiting_set.discard(task_id)

    def _handle_task_failed(self, event: dict[str, Any]) -> None:
        """Handle task failed event.

        Args:
            event: The task failed event.
        """
        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.FAILED
        self._failed_tasks += 1
        self._record_transition(task_id, previous_state, TaskState.FAILED)

        # Clean up
        self._task_start_times.pop(task_id, None)
        self._task_blocked_by.pop(task_id, None)

    def _handle_task_cancelled(self, event: dict[str, Any]) -> None:
        """Handle task cancelled event.

        Args:
            event: The task cancelled event.
        """
        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.CANCELLED
        self._record_transition(task_id, previous_state, TaskState.CANCELLED)

        # Clean up
        self._task_start_times.pop(task_id, None)
        self._task_blocked_by.pop(task_id, None)

    def _handle_task_retry(self, event: dict[str, Any]) -> None:
        """Handle task retry event.

        Args:
            event: The task retry event.
        """
        task_id = event.get("task_id", "")

        previous_state = self._task_states.get(task_id, "")
        self._task_states[task_id] = TaskState.RETRYING
        self._retried_tasks += 1
        self._record_transition(task_id, previous_state, TaskState.RETRYING)

    def _handle_task_orchestration(self, event: dict[str, Any]) -> None:
        """Handle generic task orchestration event.

        Args:
            event: The task orchestration event.
        """
        task_id = event.get("task_id", "")
        state_before = event.get("state_before", "")
        state_after = event.get("state_after", "")
        parent_task_ids = event.get("parent_task_ids", [])

        if task_id:
            self._task_states[task_id] = state_after
            self._record_transition(task_id, state_before, state_after)

        if parent_task_ids:
            self._task_dependencies[task_id] = parent_task_ids

    def _record_transition(self, task_id: str, from_state: str, to_state: str) -> None:
        """Record a state transition.

        Args:
            task_id: The task ID.
            from_state: Previous state.
            to_state: New state.
        """
        key = f"{from_state}->{to_state}" if from_state else to_state
        self._state_transitions[key] = self._state_transitions.get(key, 0) + 1

    def _check_deadlock(self) -> None:
        """Check for potential deadlocks among waiting tasks."""
        import time

        current_time = time.time()

        # Check for tasks that have been waiting too long
        for task_id, start_time in list(self._task_start_times.items()):
            if self._task_states.get(task_id) == TaskState.RUNNING:
                duration_ms = (current_time - start_time) * 1000
                if duration_ms > self._timeout_warning_threshold_ms:
                    self._timeout_warnings.append(
                        {
                            "task_id": task_id,
                            "duration_ms": duration_ms,
                            "threshold_ms": self._timeout_warning_threshold_ms,
                            "timestamp": current_time,
                        }
                    )
                    logger.warning(
                        "[task_audit] Timeout warning for task %s: %.2fs",
                        task_id,
                        duration_ms / 1000,
                    )

        # Check for circular dependencies (deadlock)
        # A task is deadlocked if it's waiting on a task that's waiting on it
        for task_id, blocked_list in self._task_blocked_by.items():
            for blocked_on_id in blocked_list:
                if blocked_on_id in self._task_blocked_by and task_id in self._task_blocked_by[blocked_on_id]:
                    deadlock = {
                        "task_id": task_id,
                        "blocked_on": blocked_on_id,
                        "timestamp": current_time,
                    }
                    if deadlock not in self._deadlocks_detected:
                        self._deadlocks_detected.append(deadlock)
                        logger.error(
                            "[task_audit] Deadlock detected: %s <-> %s",
                            task_id,
                            blocked_on_id,
                        )

    def get_stats(self) -> dict[str, Any]:
        """Get task orchestration audit statistics.

        Returns:
            Dictionary with task-specific metrics.
        """
        base_stats = super().get_stats()
        return {
            **base_stats,
            "task_states": dict(self._task_states),
            "task_dependencies": dict(self._task_dependencies),
            "dag_id": self._dag_id,
            "dag_task_count": len(self._dag_tasks),
            "completed_tasks": self._completed_tasks,
            "failed_tasks": self._failed_tasks,
            "retried_tasks": self._retried_tasks,
            "state_transitions": dict(self._state_transitions),
            "deadlocks_detected": len(self._deadlocks_detected),
            "deadlock_details": list(self._deadlocks_detected),
            "timeout_warnings_count": len(self._timeout_warnings),
            "timeout_warnings": list(self._timeout_warnings[-10:]),  # Last 10
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        super().reset_stats()
        self._task_states.clear()
        self._task_dependencies.clear()
        self._task_start_times.clear()
        self._task_blocked_by.clear()
        self._task_waiting_on.clear()
        self._dag_id = None
        self._dag_tasks.clear()
        self._deadlocks_detected.clear()
        self._timeout_warnings.clear()
        self._state_transitions.clear()
        self._completed_tasks = 0
        self._failed_tasks = 0
        self._retried_tasks = 0
