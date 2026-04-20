"""Dedicated error types for ``runtime.task_market``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class TaskMarketError(RuntimeError):
    """Raised when ``runtime.task_market`` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "task_market_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class FSMTransitionError(TaskMarketError):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        from_status: str = "",
        to_status: str = "",
        event: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="fsm_transition_error",
            details={
                **(details or {}),
                "task_id": task_id,
                "from_status": from_status,
                "to_status": to_status,
                "event": event,
            },
        )
        self.task_id = task_id
        self.from_status = from_status
        self.to_status = to_status
        self.event = event


class LeaseAcquisitionError(TaskMarketError):
    """Raised when a lease cannot be acquired (already held by another worker)."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        held_by: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="lease_acquisition_error",
            details={
                **(details or {}),
                "task_id": task_id,
                "held_by": held_by,
            },
        )
        self.task_id = task_id
        self.held_by = held_by


class StaleLeaseTokenError(TaskMarketError):
    """Raised when a lease token is stale or does not match the current holder."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        presented_token: str = "",
        current_token: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="stale_lease_token",
            details={
                **(details or {}),
                "task_id": task_id,
            },
        )
        self.task_id = task_id
        self.presented_token = presented_token
        self.current_token = current_token


class TaskNotClaimableError(TaskMarketError):
    """Raised when a task cannot be claimed (e.g., visibility timeout not yet expired)."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="task_not_claimable",
            details={
                **(details or {}),
                "task_id": task_id,
                "reason": reason,
            },
        )
        self.task_id = task_id
        self.reason = reason


class RetryExhaustedError(TaskMarketError):
    """Raised when a task has exhausted its retry attempts."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        attempts: int = 0,
        max_attempts: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="retry_exhausted",
            details={
                **(details or {}),
                "task_id": task_id,
                "attempts": attempts,
                "max_attempts": max_attempts,
            },
        )
        self.task_id = task_id
        self.attempts = attempts
        self.max_attempts = max_attempts


class TaskNotFoundError(TaskMarketError):
    """Raised when a task cannot be found in the store."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            code="task_not_found",
            details={
                **(details or {}),
                "task_id": task_id,
            },
        )
        self.task_id = task_id


__all__ = [
    "FSMTransitionError",
    "LeaseAcquisitionError",
    "RetryExhaustedError",
    "StaleLeaseTokenError",
    "TaskMarketError",
    "TaskNotClaimableError",
    "TaskNotFoundError",
]
