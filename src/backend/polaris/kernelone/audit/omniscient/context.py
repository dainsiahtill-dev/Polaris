"""AuditContext using contextvars for Correlation ID propagation across async tasks.

Design:
- Use contextvars.ContextVar for async-safe scope management
- AuditContext is a frozen dataclass with correlation fields
- Module-level ContextVar for current context
- Helper functions for context management
- Async context manager for automatic context lifecycle

Reference: polaris/infrastructure/di/scope.py for contextvars usage pattern.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Module-level ContextVar
# =============================================================================

audit_context: ContextVar[AuditContext | None] = ContextVar(
    "audit_context",
    default=None,
)


# =============================================================================
# AuditContext Dataclass
# =============================================================================


@dataclass(frozen=True)
class AuditContext:
    """Immutable audit context for correlation across async tasks.

    Attributes:
        run_id: Run identifier for the current execution session.
        turn_id: Turn identifier within the run.
        task_id: Task identifier within the turn.
        instance_id: Instance identifier for the executing agent/cell.
        span_id: Current span/operation identifier.
        parent_span_id: Parent span identifier for tracing.
        user_id: User identifier making the request.
        workspace: Workspace path for the execution.
        metadata: Additional key-value metadata.
    """

    run_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    instance_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    user_id: str = ""
    workspace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_span(self, span_id: str) -> AuditContext:
        """Create a new context with a new span_id, chaining to current."""
        return AuditContext(
            run_id=self.run_id,
            turn_id=self.turn_id,
            task_id=self.task_id,
            instance_id=self.instance_id,
            span_id=span_id,
            parent_span_id=self.span_id,
            user_id=self.user_id,
            workspace=self.workspace,
            metadata=dict(self.metadata),
        )

    def with_metadata(self, key: str, value: Any) -> AuditContext:
        """Create a new context with added metadata."""
        new_metadata = dict(self.metadata)
        new_metadata[key] = value
        return AuditContext(
            run_id=self.run_id,
            turn_id=self.turn_id,
            task_id=self.task_id,
            instance_id=self.instance_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            user_id=self.user_id,
            workspace=self.workspace,
            metadata=new_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for logging/serialization."""
        return {
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "user_id": self.user_id,
            "workspace": self.workspace,
            "metadata": dict(self.metadata),
        }


# =============================================================================
# Context Management Functions
# =============================================================================


def get_current_audit_context() -> AuditContext | None:
    """Get the current audit context from context variable.

    Returns:
        Current AuditContext if set, None otherwise.
    """
    return audit_context.get(None)


def set_audit_context(context: AuditContext) -> None:
    """Set the current audit context in the context variable.

    Args:
        context: The AuditContext to set.
    """
    audit_context.set(context)


def clear_audit_context() -> None:
    """Clear the current audit context."""
    audit_context.set(None)


# =============================================================================
# Async Context Manager
# =============================================================================


class _AuditContextManager:
    """Async context manager for managing audit context lifecycle.

    This class manages the audit context within an async with block,
    automatically setting and clearing the context.

    Usage:
        async with audit_context_manager(run_id="run_123", turn_id="turn_1"):
            ctx = get_current_audit_context()
            # ctx.run_id == "run_123" — propagates to all async children
        # Context automatically cleared on exit
    """

    def __init__(
        self,
        run_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        instance_id: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        workspace: str = "",
        **metadata: Any,
    ) -> None:
        """Initialize the context manager with correlation fields.

        Args:
            run_id: Run identifier.
            turn_id: Turn identifier.
            task_id: Task identifier.
            instance_id: Instance identifier.
            span_id: Current span identifier.
            parent_span_id: Parent span identifier.
            user_id: User identifier.
            workspace: Workspace path.
            **metadata: Additional metadata key-value pairs.
        """
        self._new_context = AuditContext(
            run_id=run_id,
            turn_id=turn_id,
            task_id=task_id,
            instance_id=instance_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            user_id=user_id,
            workspace=workspace,
            metadata=dict(metadata),
        )
        self._previous_context: AuditContext | None = None
        self._token: Token[AuditContext | None] | None = None

    async def __aenter__(self) -> AuditContext:
        """Enter async context and set the audit context."""
        self._previous_context = audit_context.get(None)
        self._token = audit_context.set(self._new_context)
        return self._new_context

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context and restore previous context."""
        if self._token is not None:
            audit_context.reset(self._token)
        self._token = None
        self._previous_context = None


# Module-level alias for public API
audit_context_manager = _AuditContextManager


# =============================================================================
# Thread-Safe Context Helpers (for sync callers)
# =============================================================================

# Thread-local storage for sync context management
_thread_local_context: threading.local = threading.local()


def get_thread_audit_context() -> AuditContext | None:
    """Get audit context from thread-local storage (for sync code)."""
    return getattr(_thread_local_context, "context", None)


def set_thread_audit_context(context: AuditContext) -> None:
    """Set audit context in thread-local storage (for sync code)."""
    _thread_local_context.context = context


def clear_thread_audit_context() -> None:
    """Clear audit context from thread-local storage (for sync code)."""
    _thread_local_context.context = None


class ThreadAuditContextManager:
    """Sync context manager for managing audit context in sync code.

    Usage:
        with ThreadAuditContextManager(run_id="run_123", turn_id="turn_1"):
            ctx = get_thread_audit_context()
            # ctx.run_id == "run_123"
        # Context automatically cleared on exit
    """

    def __init__(
        self,
        run_id: str = "",
        turn_id: str = "",
        task_id: str = "",
        instance_id: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        workspace: str = "",
        **metadata: Any,
    ) -> None:
        """Initialize the context manager with correlation fields.

        Args:
            run_id: Run identifier.
            turn_id: Turn identifier.
            task_id: Task identifier.
            instance_id: Instance identifier.
            span_id: Current span identifier.
            parent_span_id: Parent span identifier.
            user_id: User identifier.
            workspace: Workspace path.
            **metadata: Additional metadata key-value pairs.
        """
        self._new_context = AuditContext(
            run_id=run_id,
            turn_id=turn_id,
            task_id=task_id,
            instance_id=instance_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            user_id=user_id,
            workspace=workspace,
            metadata=dict(metadata),
        )
        self._previous_context: AuditContext | None = None

    def __enter__(self) -> AuditContext:
        """Enter sync context and set the audit context."""
        self._previous_context = get_thread_audit_context()
        set_thread_audit_context(self._new_context)
        return self._new_context

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit sync context and restore previous context."""
        if self._previous_context is None:
            clear_thread_audit_context()
        else:
            set_thread_audit_context(self._previous_context)
        self._previous_context = None
