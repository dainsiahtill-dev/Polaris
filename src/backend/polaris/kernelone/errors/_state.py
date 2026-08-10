"""State-machine and task/worker state errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.errors._base import KernelOneError


class StateError(KernelOneError):
    """State machine related errors.

    Raised when state transitions fail or invalid states are encountered.

    Attributes:
        current_state: The current state.
        target_state: The attempted target state.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "STATE_ERROR",
        current_state: str = "",
        target_state: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        self.current_state = current_state
        self.target_state = target_state
        if current_state:
            self.details["current_state"] = current_state
        if target_state:
            self.details["target_state"] = target_state


class InvalidStateTransitionError(StateError):
    """Invalid state transition attempted."""

    def __init__(
        self,
        message: str,
        *,
        current_state: str = "",
        target_state: str = "",
        allowed_transitions: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_STATE_TRANSITION_ERROR",
            current_state=current_state,
            target_state=target_state,
            **kwargs,
        )
        self.allowed_transitions = allowed_transitions or []
        if self.allowed_transitions:
            self.details["allowed_transitions"] = self.allowed_transitions


class InvalidTaskStateTransitionError(StateError):
    """Invalid task state transition."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_TASK_STATE_TRANSITION_ERROR",
            **kwargs,
        )
        if task_id:
            self.details["task_id"] = task_id


class WorkerStateError(StateError):
    """Worker state error."""

    def __init__(
        self,
        message: str = "Invalid worker state transition",
        *,
        worker_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="WORKER_STATE_ERROR",
            **kwargs,
        )
        if worker_id:
            self.details["worker_id"] = worker_id


class TaskStateError(StateError):
    """Task state error."""

    def __init__(
        self,
        message: str = "Invalid task state transition",
        *,
        task_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TASK_STATE_ERROR",
            **kwargs,
        )
        if task_id:
            self.details["task_id"] = task_id


class InvalidToolStateTransitionError(StateError):
    """Invalid tool state transition."""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        current_status: Any = None,
        target_status: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INVALID_TOOL_STATE_TRANSITION_ERROR",
            **kwargs,
        )
        if tool_name:
            self.details["tool_name"] = tool_name
        if current_status is not None:
            self.current_status = current_status
        if target_status is not None:
            self.target_status = target_status
