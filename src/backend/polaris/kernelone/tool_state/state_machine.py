"""ToolState state machine for KernelOne.

This module provides:
- ToolStateStatus: Core state enum
- ToolState: Complete state with sub-state tracking
- ToolErrorKind: Error classification
- State machine with transition validation

Reference: OpenCode packages/opencode/src/session/message-v2.ts (ToolState types)

DATA CONTAINER PATTERN NOTE:
    ToolState is a @dataclass data container. State transitions are validated
    by the external tool executor (polaris/kernelone/tool/executor.py), not
    by ToolState itself. This design allows the executor to implement complex
    transition logic while ToolState remains a simple data holder.

    ToolState implements these StateMachinePort-compatible methods:
    - is_terminal property: Checks if state is terminal
    - is_pending / is_running / is_completed / is_failed properties

    If you need a class-based state machine with internal transition logic,
    see TurnStateMachine (polaris/cells/roles/kernel/internal/turn_state_machine.py)
    or BaseStateMachine (polaris/kernelone/state_machine.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from polaris.kernelone.constants import DEFAULT_MAX_RETRIES
from polaris.kernelone.errors import InvalidToolStateTransitionError

# =============================================================================
# Tool State Status
# =============================================================================


class ToolStateStatus(str, Enum):
    """Top-level tool state statuses.

    Lifecycle:
        PENDING -> RUNNING -> COMPLETED | ERROR | BLOCKED | TIMEOUT | CANCELLED
    """

    PENDING = "pending"  # Tool call queued, not yet executing
    RUNNING = "running"  # Tool is executing
    COMPLETED = "completed"  # Tool executed successfully
    ERROR = "error"  # Tool execution failed
    BLOCKED = "blocked"  # Tool blocked by policy/permission
    TIMEOUT = "timeout"  # Tool execution timed out
    CANCELLED = "cancelled"  # Tool was cancelled


# =============================================================================
# Sub-states
# =============================================================================


class ToolPendingSubState(str, Enum):
    """Sub-states for PENDING status."""

    QUEUED = "queued"  # In execution queue
    SCHEDULED = "scheduled"  # Scheduled for execution
    WAITING_INPUT = "waiting_input"  # Waiting for input resolution


class ToolRunningSubState(str, Enum):
    """Sub-states for RUNNING status."""

    INITIALIZING = "initializing"  # Setting up execution environment
    EXECUTING = "executing"  # Actively running
    FINALIZING = "finalizing"  # Cleaning up


# =============================================================================
# Error Classification
# =============================================================================


class ToolErrorKind(str, Enum):
    """Classification of tool errors for debugging and analytics."""

    EXCEPTION = "exception"  # Unhandled exception
    VALIDATION = "validation"  # Invalid arguments
    PERMISSION = "permission"  # Permission denied
    NOT_FOUND = "not_found"  # Tool not found
    RUNTIME = "runtime"  # Runtime error
    TIMEOUT = "timeout"  # Execution timeout
    CANCELLED = "cancelled"  # Execution cancelled
    NETWORK = "network"  # Network error
    RATE_LIMIT = "rate_limit"  # Rate limit exceeded
    UNKNOWN = "unknown"  # Unknown error


# =============================================================================
# State Machine
# =============================================================================


# Valid state transitions
_VALID_TRANSITIONS: dict[ToolStateStatus, set[ToolStateStatus]] = {
    ToolStateStatus.PENDING: {
        ToolStateStatus.RUNNING,
        ToolStateStatus.CANCELLED,
    },
    ToolStateStatus.RUNNING: {
        ToolStateStatus.COMPLETED,
        ToolStateStatus.ERROR,
        ToolStateStatus.TIMEOUT,
        ToolStateStatus.BLOCKED,
        ToolStateStatus.CANCELLED,
    },
    # Terminal states - no transitions allowed
    ToolStateStatus.COMPLETED: set(),
    ToolStateStatus.ERROR: set(),
    ToolStateStatus.BLOCKED: set(),
    ToolStateStatus.TIMEOUT: set(),
    ToolStateStatus.CANCELLED: set(),
}


# =============================================================================
# Tool State
# =============================================================================


@dataclass
class ToolState:
    """Complete tool state with sub-state tracking.

    This class represents the full state of a tool execution,
    including sub-states for detailed tracking and error classification.

    Example:
        state = ToolState(
            tool_call_id="call_abc123",
            tool_name="read_file",
            status=ToolStateStatus.PENDING,
        )

        # Transition to running
        state.transition(ToolStateStatus.RUNNING)

        # Transition to completed
        state.transition(
            ToolStateStatus.COMPLETED,
            result={"content": "file contents"}
        )

        # Check terminal state
        assert state.is_terminal  # True

    Attributes:
        tool_call_id: Unique identifier for this tool call
        tool_name: Name of the tool being executed
        status: Current top-level status
        sub_state: Optional sub-state for detailed tracking
        error_kind: Error classification (when status is ERROR/BLOCKED)
        error_message: Human-readable error message
        error_stack: Stack trace for debugging
        created_at: Timestamp when the tool call was created
        started_at: Timestamp when execution started
        completed_at: Timestamp when execution completed
        timeout_at: Timestamp when tool will timeout
        result: Execution result (when status is COMPLETED)
        output_size: Size of result output in bytes
        execution_lane: Execution context (direct, batch, etc.)
        retry_count: Number of retries attempted
        max_retries: Maximum allowed retries
        correlation_id: ID for correlating with parent operations
        metadata: Additional metadata for extensibility
        _history: Internal state transition history
    """

    # Identity
    tool_call_id: str
    tool_name: str

    # Primary state
    status: ToolStateStatus = ToolStateStatus.PENDING
    sub_state: ToolPendingSubState | ToolRunningSubState | None = field(default=ToolPendingSubState.QUEUED)

    # Error details (populated when status in ERROR, BLOCKED, TIMEOUT)
    error_kind: ToolErrorKind | None = None
    error_message: str | None = None
    error_stack: str | None = None

    # Timing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_at: datetime | None = None

    # Result
    result: Any = field(default=None, init=False)
    output_size: int = 0

    # Metadata
    execution_lane: str = "direct"
    retry_count: int = 0
    max_retries: int = 3
    correlation_id: str | None = None
    metadata: dict = field(default_factory=dict)

    # Internal
    _history: list[tuple[ToolStateStatus, datetime, str | None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Initialize history with initial state."""
        if not self._history:
            self._history.append((self.status, self.created_at, None))

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """Check if the state is a terminal state.

        Terminal states cannot transition to other states.
        """
        return self.status in {
            ToolStateStatus.COMPLETED,
            ToolStateStatus.ERROR,
            ToolStateStatus.BLOCKED,
            ToolStateStatus.TIMEOUT,
            ToolStateStatus.CANCELLED,
        }

    @property
    def is_pending(self) -> bool:
        """Check if tool is pending."""
        return self.status == ToolStateStatus.PENDING

    @property
    def is_running(self) -> bool:
        """Check if tool is currently running."""
        return self.status == ToolStateStatus.RUNNING

    @property
    def is_completed(self) -> bool:
        """Check if tool completed successfully."""
        return self.status == ToolStateStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        """Check if tool failed (error, timeout, blocked, cancelled)."""
        return self.status in {
            ToolStateStatus.ERROR,
            ToolStateStatus.TIMEOUT,
            ToolStateStatus.BLOCKED,
            ToolStateStatus.CANCELLED,
        }

    @property
    def duration_ms(self) -> int | None:
        """Calculate execution duration in milliseconds.

        Returns:
            Duration from started_at to completed_at, or None if not completed.
        """
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    @property
    def pending_duration_ms(self) -> int | None:
        """Calculate time spent in PENDING state in milliseconds.

        Returns:
            Duration from created_at to started_at, or None if not started.
        """
        if self.created_at and self.started_at:
            delta = self.started_at - self.created_at
            return int(delta.total_seconds() * 1000)
        return None

    @property
    def total_duration_ms(self) -> int | None:
        """Calculate total time from creation to completion.

        Returns:
            Duration from created_at to completed_at, or None if not completed.
        """
        if self.created_at and self.completed_at:
            delta = self.completed_at - self.created_at
            return int(delta.total_seconds() * 1000)
        return None

    @property
    def history(self) -> list[tuple[ToolStateStatus, datetime, str | None]]:
        """Get state transition history.

        Returns:
            List of (status, timestamp, note) tuples.
        """
        return list(self._history)

    # -------------------------------------------------------------------------
    # State Transitions
    # -------------------------------------------------------------------------

    def transition(
        self,
        new_status: ToolStateStatus,
        error_kind: ToolErrorKind | None = None,
        error_message: str | None = None,
        error_stack: str | None = None,
        result: Any = None,
        sub_state: ToolPendingSubState | ToolRunningSubState | None = None,
    ) -> None:
        """Transition to a new state with validation.

        Args:
            new_status: Target state
            error_kind: Error classification (for ERROR/BLOCKED states)
            error_message: Human-readable error message
            error_stack: Stack trace for debugging
            result: Execution result (for COMPLETED state)
            sub_state: Optional sub-state override

        Raises:
            InvalidToolStateTransitionError: If transition is not valid
        """
        # Validate transition
        valid_transitions = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid_transitions:
            raise InvalidToolStateTransitionError(
                message=f"Invalid state transition: {self.status.value} -> {new_status.value}. "
                f"Valid transitions from {self.status.value}: {', '.join(s.value for s in valid_transitions) if valid_transitions else 'none (terminal)'}",
                tool_name=self.tool_name,
                current_status=self.status,
                target_status=new_status,
            )

        now = datetime.now(timezone.utc)

        # Update timing based on transition
        if new_status == ToolStateStatus.RUNNING:
            self.started_at = now
            self.sub_state = sub_state or ToolRunningSubState.INITIALIZING

        elif new_status in {
            ToolStateStatus.COMPLETED,
            ToolStateStatus.ERROR,
            ToolStateStatus.TIMEOUT,
            ToolStateStatus.CANCELLED,
            ToolStateStatus.BLOCKED,
        }:
            self.completed_at = now
            self.sub_state = None

        # Update error details
        if new_status == ToolStateStatus.ERROR:
            self.error_kind = error_kind or ToolErrorKind.UNKNOWN
            self.error_message = error_message
            self.error_stack = error_stack
        elif new_status == ToolStateStatus.BLOCKED:
            self.error_kind = error_kind or ToolErrorKind.PERMISSION
            self.error_message = error_message
        elif new_status == ToolStateStatus.TIMEOUT:
            self.error_kind = ToolErrorKind.TIMEOUT
            self.error_message = error_message or "Tool execution timed out"
        elif new_status == ToolStateStatus.CANCELLED:
            self.error_kind = ToolErrorKind.CANCELLED
            self.error_message = error_message or "Tool execution was cancelled"

        # Update result
        if result is not None:
            self.result = result
            if isinstance(result, str):
                self.output_size = len(result)
            elif isinstance(result, dict):
                import json

                self.output_size = len(json.dumps(result))

        # Record history
        self.status = new_status
        self._history.append((new_status, now, error_message))

    def retry(self) -> None:
        """Reset state for retry.

        Increments retry_count and resets timing fields.
        Should only be called from terminal states.

        Raises:
            InvalidToolStateTransitionError: If retry is not allowed
        """
        if self.retry_count >= self.max_retries:
            raise ValueError(f"Max retries ({self.max_retries}) exceeded. Cannot retry.")

        if self.status == ToolStateStatus.RUNNING:
            raise InvalidToolStateTransitionError(
                message="Cannot retry from RUNNING state",
                tool_name=self.tool_name,
            )

        self.retry_count += 1
        self.status = ToolStateStatus.PENDING
        self.sub_state = ToolPendingSubState.QUEUED
        self.started_at = None
        self.completed_at = None
        self.error_kind = None
        self.error_message = None
        self.error_stack = None
        self.result = None
        self.output_size = 0

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all state fields.
        """
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "sub_state": self.sub_state.value if self.sub_state else None,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "result": self.result,
            "output_size": self.output_size,
            "execution_lane": self.execution_lane,
            "retry_count": self.retry_count,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ToolState:
        """Create ToolState from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            ToolState instance
        """
        # Parse timestamps
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)

        started_at = data.get("started_at")
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        completed_at = data.get("completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)

        # Parse enums
        status = ToolStateStatus(data["status"])
        sub_state = data.get("sub_state")
        if sub_state:
            if status == ToolStateStatus.PENDING:
                sub_state = ToolPendingSubState(sub_state)
            elif status == ToolStateStatus.RUNNING:
                sub_state = ToolRunningSubState(sub_state)

        error_kind = data.get("error_kind")
        if error_kind:
            error_kind = ToolErrorKind(error_kind)

        restored = cls(
            tool_call_id=data["tool_call_id"],
            tool_name=data["tool_name"],
            status=status,
            sub_state=sub_state,
            error_kind=error_kind,
            error_message=data.get("error_message"),
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            output_size=data.get("output_size", 0),
            execution_lane=data.get("execution_lane", "direct"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", DEFAULT_MAX_RETRIES),
            correlation_id=data.get("correlation_id"),
            metadata=data.get("metadata", {}),
        )
        # Set result after construction since it has init=False
        restored.result = data.get("result")
        return restored


# =============================================================================
# Factory Functions
# =============================================================================


def create_tool_state(
    tool_name: str,
    tool_call_id: str | None = None,
    execution_lane: str = "direct",
    correlation_id: str | None = None,
    metadata: dict | None = None,
) -> ToolState:
    """Create a new tool state in PENDING status.

    Args:
        tool_name: Name of the tool
        tool_call_id: Optional ID (generated if not provided)
        execution_lane: Execution context
        correlation_id: Parent operation correlation ID
        metadata: Additional metadata

    Returns:
        New ToolState in PENDING status
    """
    return ToolState(
        tool_call_id=tool_call_id or uuid.uuid4().hex[:12],
        tool_name=tool_name,
        status=ToolStateStatus.PENDING,
        sub_state=ToolPendingSubState.QUEUED,
        execution_lane=execution_lane,
        correlation_id=correlation_id,
        metadata=metadata or {},
    )
