"""Task phase definitions for Director v2 state machine.

Intent: Models the BUSINESS LIFECYCLE phases of a task in Director v2 workflow.
- TaskPhase tracks: PENDING → PLANNING → VALIDATION → EXECUTION → VERIFICATION → ...
- This is orthogonal to TurnState which tracks AI turn execution mechanics.

IMPORTANT: Do NOT confuse TaskPhase with TurnState.
- TaskPhase = Business task lifecycle phases (this file)
- TurnState = AI turn execution state (polaris/cells/roles/kernel/internal/turn_state_machine.py)

Simplified 4-phase workflow:
- PLANNING: Goal definition + blueprint creation (merged from hp_start_run + hp_create_blueprint)
- VALIDATION: Policy compliance check (from hp_record_approval)
- EXECUTION: Implementation authorization (from hp_allow_implementation)
- VERIFICATION: Self-check verification (from hp_run_verify)

已适配 KernelOne StateMachinePort (polaris/kernelone/state_machine.py)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from polaris.kernelone.state_machine import InvalidStateTransitionError

# =============================================================================
# State Enum
# =============================================================================


class TaskPhase(str, Enum):
    """Task lifecycle phases in Director v2 (StrEnum for serialization)."""

    PENDING = "pending"
    PLANNING = "planning"  # Merged: start_run + blueprint
    VALIDATION = "validation"  # Policy compliance check
    EXECUTION = "execution"  # Implementation
    VERIFICATION = "verification"  # Self-check
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# =============================================================================
# Context and Result Data Classes
# =============================================================================


@dataclass
class PhaseContext:
    """Context passed between phases."""

    task_id: str
    workspace: str
    plan: str = ""
    blueprint: dict[str, Any] = field(default_factory=dict)
    policy_check_result: dict[str, Any] = field(default_factory=dict)
    snapshot_path: str | None = None
    changed_files: list[str] = field(default_factory=list)
    verification_result: dict[str, Any] = field(default_factory=dict)
    build_round: int = 0
    max_build_rounds: int = 4
    stall_count: int = 0
    previous_missing_targets: list[str] = field(default_factory=list)
    previous_unresolved_imports: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseResult:
    """Result of executing a phase."""

    success: bool
    phase: TaskPhase
    message: str = ""
    error_code: str | None = None
    can_retry: bool = False
    should_rollback: bool = False
    context_updates: dict[str, Any] = field(default_factory=dict)
    next_phase: TaskPhase | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseTransition:
    """Record of a phase transition."""

    from_phase: TaskPhase
    to_phase: TaskPhase
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    message: str = ""


# =============================================================================
# Terminal States Definition
# =============================================================================

_TERMINAL_PHASES: set[TaskPhase] = {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.ROLLED_BACK}


# =============================================================================
# Task State Machine (Implements StateMachinePort)
# =============================================================================


class TaskStateMachine:
    """Simplified 4-phase state machine for task execution.

    Implements StateMachinePort for KernelOne compatibility.

    Unlike the original 7-phase HP ritual, this version:
    - Merges start_run + blueprint into PLANNING
    - Removes snapshot/finalize as explicit phases (handled transparently)
    - Keeps VALIDATION as explicit gate (critical for governance)
    - Keeps VERIFICATION as explicit phase (critical for quality)

    State transitions:
        PENDING → PLANNING → VALIDATION → EXECUTION → VERIFICATION → COMPLETED
                                           ↓              ↓
                                        FAILED ←──── ROLLED_BACK

    Implements:
        - current_state property (returns current TaskPhase)
        - can_transition_to() method
        - transition_to() method
        - is_terminal() method
    """

    # Valid state transitions
    TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
        TaskPhase.PENDING: [TaskPhase.PLANNING],
        TaskPhase.PLANNING: [TaskPhase.VALIDATION, TaskPhase.FAILED],
        TaskPhase.VALIDATION: [TaskPhase.EXECUTION, TaskPhase.FAILED],
        TaskPhase.EXECUTION: [TaskPhase.VERIFICATION, TaskPhase.FAILED, TaskPhase.ROLLED_BACK],
        TaskPhase.VERIFICATION: [
            TaskPhase.COMPLETED,
            TaskPhase.EXECUTION,  # Retry loop
            TaskPhase.FAILED,
            TaskPhase.ROLLED_BACK,
        ],
        TaskPhase.COMPLETED: [],
        TaskPhase.FAILED: [TaskPhase.PLANNING],  # Can restart
        TaskPhase.ROLLED_BACK: [TaskPhase.PLANNING],  # Can restart
    }

    def __init__(self, task_id: str, initial_context: PhaseContext | None = None) -> None:
        self.task_id = task_id
        self.current_phase = TaskPhase.PENDING
        self.context = initial_context or PhaseContext(task_id=task_id, workspace=".")
        self.transitions: list[PhaseTransition] = []
        self.phase_results: dict[TaskPhase, PhaseResult] = {}
        self._phase_start_time: datetime | None = None

    # -------------------------------------------------------------------------
    # StateMachinePort Implementation
    # -------------------------------------------------------------------------

    @property
    def current_state(self) -> TaskPhase:
        """Return the current state (alias for current_phase)."""
        return self.current_phase

    def can_transition_to(self, target_phase: TaskPhase) -> bool:
        """Check if transition to target phase is valid."""
        return target_phase in self.TRANSITIONS.get(self.current_phase, [])

    def transition_to(
        self,
        target_phase: TaskPhase,
        message: str = "",
        force: bool = False,
    ) -> bool:
        """Attempt to transition to target phase.

        Args:
            target_phase: Phase to transition to
            message: Transition reason/message
            force: Force transition even if not in valid transitions

        Returns:
            True if transition succeeded

        Raises:
            InvalidStateTransitionError: If force=False and transition is invalid
        """
        if not force and not self.can_transition_to(target_phase):
            allowed = self.TRANSITIONS.get(self.current_phase, [])
            raise InvalidStateTransitionError(
                f"Invalid state transition from {self.current_phase} to {target_phase}: "
                f"valid transitions from {self.current_phase}: {[p.name for p in allowed]}",
                current_state=str(self.current_phase),
                target_state=str(target_phase),
                allowed_transitions=[p.name for p in allowed],
            )

        transition = PhaseTransition(
            from_phase=self.current_phase,
            to_phase=target_phase,
            message=message,
        )
        self.transitions.append(transition)
        self.current_phase = target_phase
        self._phase_start_time = datetime.now()
        return True

    def is_terminal(self) -> bool:
        """Check if current phase is terminal."""
        return self.current_phase in _TERMINAL_PHASES

    # -------------------------------------------------------------------------
    # Business Logic Methods
    # -------------------------------------------------------------------------

    def record_phase_result(self, result: PhaseResult) -> None:
        """Record the result of executing current phase."""
        self.phase_results[self.current_phase] = result

        # Apply context updates
        for key, value in result.context_updates.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)

        # Auto-transition if next_phase specified
        if result.next_phase and result.success:
            self.transition_to(result.next_phase, f"Auto-transition from {result.phase}")

    def get_phase_duration(self) -> float | None:
        """Get duration of current phase in seconds."""
        if self._phase_start_time is None:
            return None
        return (datetime.now() - self._phase_start_time).total_seconds()

    def get_trajectory(self) -> list[dict[str, Any]]:
        """Get full execution trajectory for audit."""
        return [
            {
                "from": t.from_phase.name,
                "to": t.to_phase.name,
                "timestamp": t.timestamp.isoformat(),
                "success": t.success,
                "message": t.message,
            }
            for t in self.transitions
        ]

    def check_stall(
        self,
        current_missing: list[str],
        current_unresolved: list[str],
    ) -> bool:
        """Check if execution is stalled (no progress).

        Returns True if no improvement compared to previous round.
        """
        # Compare with previous
        missing_improved = len(current_missing) < len(self.context.previous_missing_targets)
        unresolved_improved = len(current_unresolved) < len(self.context.previous_unresolved_imports)

        if not missing_improved and not unresolved_improved:
            self.context.stall_count += 1
        else:
            self.context.stall_count = 0

        # Update context for next comparison
        self.context.previous_missing_targets = current_missing
        self.context.previous_unresolved_imports = current_unresolved

        # Check if exceeded max stall threshold
        return self.context.stall_count >= 2  # stall_round_threshold

    def should_retry(self) -> bool:
        """Check if verification failure should trigger retry."""
        if self.current_phase != TaskPhase.VERIFICATION:
            return False

        result = self.phase_results.get(TaskPhase.VERIFICATION)
        if not result or result.success:
            return False

        # Check build round budget
        return (
            result.can_retry
            and self.context.build_round < self.context.max_build_rounds
            and not self.check_stall(
                result.context_updates.get("missing_targets", []),
                result.context_updates.get("unresolved_imports", []),
            )
        )

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize state machine to dict."""
        return {
            "task_id": self.task_id,
            "current_phase": self.current_phase.name,
            "context": {
                "task_id": self.context.task_id,
                "workspace": self.context.workspace,
                "build_round": self.context.build_round,
                "stall_count": self.context.stall_count,
                "changed_files": self.context.changed_files,
            },
            "trajectory": self.get_trajectory(),
            "is_terminal": self.is_terminal(),
        }
