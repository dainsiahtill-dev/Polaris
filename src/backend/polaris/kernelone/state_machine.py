"""KernelOne Base State Machine - 统一状态机基类.

本模块提供状态机的通用抽象基类，作为KernelOne系统中各状态机的契约。

已实现的状态机：
- TaskStateMachine (polaris/domain/state_machine/task_phase.py)
- ToolState (polaris/kernelone/tool/state_machine.py) - 数据容器模式
- TurnStateMachine (polaris/cells/roles/kernel/internal/turn_state_machine.py)
- WorkflowRuntimeState (polaris/kernelone/workflow/engine.py) - 数据容器模式

数据容器模式 (ToolState, WorkflowRuntimeState):
    这些是@dataclass数据容器，转换逻辑由外部引擎处理。
    它们应实现 is_terminal() 等接口方法，但不需要继承BaseStateMachine。

继承模式 (TaskStateMachine, TurnStateMachine):
    这些是有完整状态机逻辑的类，应继承BaseStateMachine。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

# Re-export InvalidStateTransitionError for backward compatibility
from polaris.kernelone.errors import InvalidStateTransitionError

__all__ = ["InvalidStateTransitionError"]

# =============================================================================
# Generic Type Variables
# =============================================================================

S = TypeVar("S", bound=Enum)


# =============================================================================
# Base State Machine Protocol
# =============================================================================


class StateMachinePort(ABC, Generic[S]):
    """Protocol defining the minimal interface for all state machines.

    This protocol defines the contract that all state machine implementations
    must follow, regardless of whether they use inheritance or composition.

    Implementations must provide:
    - current_state property: Returns the current state enum value
    - can_transition_to(): Checks if a transition is valid
    - transition_to(): Executes a state transition
    - is_terminal(): Checks if current state is terminal

    Optional (recommended):
    - history: List of past transitions
    - is_failed(): Checks if current state is a failure state
    """

    @property
    @abstractmethod
    def current_state(self) -> S:
        """Return the current state enum value."""
        ...

    @abstractmethod
    def can_transition_to(self, target: S) -> bool:
        """Check if transition to target state is valid.

        Args:
            target: The target state to transition to.

        Returns:
            True if the transition is valid, False otherwise.
        """
        ...

    @abstractmethod
    def transition_to(self, target: S, **kwargs: Any) -> None:
        """Execute transition to target state.

        Args:
            target: The target state to transition to.
            **kwargs: Additional transition metadata.

        Raises:
            InvalidStateTransitionError: If transition is not valid.
        """
        ...

    @abstractmethod
    def is_terminal(self) -> bool:
        """Check if current state is terminal.

        Terminal states cannot transition to other states.

        Returns:
            True if current state is terminal, False otherwise.
        """
        ...


# =============================================================================
# Base State Machine (Inheritance Pattern)
# =============================================================================


class BaseStateMachine(Generic[S]):
    """Abstract base class for state machines using inheritance pattern.

    This base class provides common functionality for state machines that
    manage their own state transitions internally.

    Subclasses must:
    1. Define a StateEnum that extends Enum
    2. Define _VALID_TRANSITIONS as a dict[StateEnum, set[StateEnum]]
    3. Optionally define _FORBIDDEN_TRANSITIONS as a set of forbidden tuples

    Example:
        class MyState(str, Enum):
            PENDING = "pending"
            RUNNING = "running"
            COMPLETED = "completed"

        class MyMachine(BaseStateMachine[MyState]):
            _VALID_TRANSITIONS = {
                MyState.PENDING: {MyState.RUNNING},
                MyState.RUNNING: {MyState.COMPLETED},
                MyState.COMPLETED: set(),
            }

            def __init__(self, initial_state: MyState) -> None:
                super().__init__(initial_state)
    """

    # Subclasses must define these
    _VALID_TRANSITIONS: dict[S, set[S]] = {}
    _FORBIDDEN_TRANSITIONS: set[tuple[S, S]] = set()
    _TERMINAL_STATES: set[S] = set()

    def __init__(self, initial_state: S) -> None:
        """Initialize the state machine.

        Args:
            initial_state: The initial state of the machine.
        """
        self._current_state = initial_state
        self._history: list[tuple[S, datetime]] = [(initial_state, datetime.now(timezone.utc))]

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def current_state(self) -> S:
        """Return the current state enum value."""
        return self._current_state

    @property
    def history(self) -> list[tuple[S, datetime]]:
        """Return state transition history.

        Returns:
            List of (state, timestamp) tuples.
        """
        return list(self._history)

    # -------------------------------------------------------------------------
    # State Validation
    # -------------------------------------------------------------------------

    def can_transition_to(self, target: S) -> bool:
        """Check if transition to target state is valid.

        Args:
            target: The target state to transition to.

        Returns:
            True if the transition is valid, False otherwise.
        """
        # Check forbidden transitions first
        if (self._current_state, target) in self._FORBIDDEN_TRANSITIONS:
            return False

        # Check allowed transitions
        allowed = self._VALID_TRANSITIONS.get(self._current_state, set())
        return target in allowed

    def is_terminal(self) -> bool:
        """Check if current state is terminal.

        Terminal states cannot transition to other states.

        Returns:
            True if current state is terminal, False otherwise.
        """
        return self._current_state in self._TERMINAL_STATES

    def is_failed(self) -> bool:
        """Check if current state is a failure state.

        Returns:
            True if current state indicates failure, False otherwise.
        """
        # Subclasses should override if they have specific failure states
        return False

    # -------------------------------------------------------------------------
    # State Transition
    # -------------------------------------------------------------------------

    def transition_to(self, target: S, **kwargs: Any) -> None:
        """Execute transition to target state.

        Args:
            target: The target state to transition to.
            **kwargs: Additional context for the transition.

        Raises:
            InvalidStateTransitionError: If transition is not valid.
        """
        # Import here to avoid circular dependency at module level
        from polaris.kernelone.errors import InvalidStateTransitionError as _InvalidStateTransitionError

        # Get string names for the error message
        current_name = getattr(self._current_state, "name", str(self._current_state))
        target_name = getattr(target, "name", str(target))

        # Check forbidden transitions
        if (self._current_state, target) in self._FORBIDDEN_TRANSITIONS:
            raise _InvalidStateTransitionError(
                f"Invalid state transition: {current_name} -> {target_name}",
                current_state=current_name,
                target_state=target_name,
            )

        # Check allowed transitions
        allowed = self._VALID_TRANSITIONS.get(self._current_state, set())
        if target not in allowed:
            allowed_str = ", ".join(getattr(s, "name", str(s)) for s in allowed) if allowed else "none (terminal)"
            raise _InvalidStateTransitionError(
                f"Invalid state transition: {current_name} -> {target_name}. Valid transitions from {current_name}: {allowed_str}",
                current_state=current_name,
                target_state=target_name,
            )

        # Execute transition
        self._current_state = target
        self._history.append((target, datetime.now(timezone.utc)))

    def get_duration_ms(self) -> int | None:
        """Calculate duration from first to last state in milliseconds.

        Returns:
            Duration in milliseconds, or None if less than 2 states in history.
        """
        if len(self._history) < 2:
            return None
        start = self._history[0][1]
        end = self._history[-1][1]
        return int((end - start).total_seconds() * 1000)
