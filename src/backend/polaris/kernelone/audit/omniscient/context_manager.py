"""Unified AuditContext — bridges PolarisContext and AuditContext.

Design:
- PolarisContext (kernelone/trace/context.py) is the PRIMARY source of truth
- AuditContext (kernelone/audit/omniscient/context.py) inherits from it
- Single contextvars entry point: this module exposes unified factory
- All audit events automatically get trace_id from the unified context
- Async context manager for automatic propagation to async tasks

The key insight: we don't need two separate context systems.
PolarisContext fields map to AuditContext fields:

    PolarisContext     ->  AuditContext
    ─────────────────────────────────────────
    trace_id               ->  trace_id
    run_id                 ->  run_id
    task_id                ->  task_id
    workspace              ->  workspace
    request_id             ->  (stored in metadata)
    workflow_id            ->  (stored in metadata)
    span_stack[-1].span_id  ->  span_id
    span_stack[-1].parent  ->  parent_span_id
    metadata               ->  metadata

Usage:
    from polaris.kernelone.audit.omniscient.context_manager import (
        get_current_audit_context,
        audit_context_scope,
        UnifiedContextFactory,
    )

    # In FastAPI middleware:
    async with audit_context_scope(run_id="run-123", workspace="/path"):
        ctx = get_current_audit_context()
        # ctx.trace_id is auto-generated, ctx.run_id="run-123"

        # In any async child task:
        child_ctx = get_current_audit_context()  # Same trace_id!
"""

from __future__ import annotations

import threading
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Unified AuditContext (replaces the separate omniscient/context.py AuditContext)
# =============================================================================


@dataclass(frozen=True)
class UnifiedAuditContext:
    """Unified audit context bridging PolarisContext and AuditContext.

    This is the SINGLE audit context that all omniscient audit events use.
    It inherits all fields from PolarisContext and adds audit-specific fields.

    Attributes:
        trace_id: Correlation ID for distributed tracing (16-char hex).
        run_id: Execution session identifier.
        task_id: Task identifier within the turn.
        workspace: Workspace path for multi-tenant isolation.
        span_id: Current span/operation identifier.
        parent_span_id: Parent span for call chain.
        user_id: User identifier making the request.
        instance_id: Instance identifier for the executing agent/cell.
        turn_id: Turn identifier within the run.
        metadata: Additional key-value metadata.
    """

    trace_id: str = ""
    run_id: str = ""
    task_id: str = ""
    workspace: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    user_id: str = ""
    instance_id: str = ""
    turn_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_span(self, span_id: str) -> UnifiedAuditContext:
        """Create new context with new span_id, chaining to current."""
        return UnifiedAuditContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            task_id=self.task_id,
            workspace=self.workspace,
            span_id=span_id,
            parent_span_id=self.span_id,
            user_id=self.user_id,
            instance_id=self.instance_id,
            turn_id=self.turn_id,
            metadata=dict(self.metadata),
        )

    def with_task(self, task_id: str) -> UnifiedAuditContext:
        """Create new context with updated task_id."""
        return UnifiedAuditContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            task_id=task_id,
            workspace=self.workspace,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            user_id=self.user_id,
            instance_id=self.instance_id,
            turn_id=self.turn_id,
            metadata=dict(self.metadata),
        )

    def with_turn(self, turn_id: str) -> UnifiedAuditContext:
        """Create new context with updated turn_id."""
        return UnifiedAuditContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            task_id=self.task_id,
            workspace=self.workspace,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            user_id=self.user_id,
            instance_id=self.instance_id,
            turn_id=turn_id,
            metadata=dict(self.metadata),
        )

    def with_metadata(self, key: str, value: Any) -> UnifiedAuditContext:
        """Create new context with added metadata."""
        new_metadata = dict(self.metadata)
        new_metadata[key] = value
        return UnifiedAuditContext(
            trace_id=self.trace_id,
            run_id=self.run_id,
            task_id=self.task_id,
            workspace=self.workspace,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            user_id=self.user_id,
            instance_id=self.instance_id,
            turn_id=self.turn_id,
            metadata=new_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for logging/serialization."""
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "workspace": self.workspace,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "user_id": self.user_id,
            "instance_id": self.instance_id,
            "turn_id": self.turn_id,
            "metadata": dict(self.metadata),
        }


# =============================================================================
# ContextVar for async propagation
# =============================================================================

_unified_audit_context: ContextVar[UnifiedAuditContext | None] = ContextVar(
    "_hp_unified_audit_context",
    default=None,
)


def _generate_trace_id() -> str:
    """Generate a standardized trace_id (16-char hex)."""
    return uuid.uuid4().hex[:16]


def _generate_span_id() -> str:
    """Generate a standardized span_id (16-char hex)."""
    return uuid.uuid4().hex[:16]


# =============================================================================
# Context Access Functions
# =============================================================================


def get_current_audit_context() -> UnifiedAuditContext | None:
    """Get current unified audit context from contextvars.

    Returns:
        Current UnifiedAuditContext if set, None otherwise.
    """
    return _unified_audit_context.get(None)


def set_audit_context(context: UnifiedAuditContext) -> None:
    """Set current unified audit context in contextvars.

    Args:
        context: The UnifiedAuditContext to set.
    """
    _unified_audit_context.set(context)


def clear_audit_context() -> None:
    """Clear the current unified audit context."""
    _unified_audit_context.set(None)


# =============================================================================
# Unified Context Factory — bridges PolarisContext and AuditContext
# =============================================================================


class UnifiedContextFactory:
    """Factory for creating UnifiedAuditContext from various sources.

    This is the SINGLE entry point for creating audit contexts.
    It bridges PolarisContext (trace) and AuditContext (omniscient).

    Usage:
        # From PolarisContext
        ctx = UnifiedContextFactory.from_polaris_context(hp_ctx)

        # From env vars
        ctx = UnifiedContextFactory.from_env_vars()

        # From scratch
        ctx = UnifiedContextFactory.create(
            trace_id="abc123",
            run_id="run-456",
            workspace="/path/to/workspace",
        )

        # Inherit from current context (for async propagation)
        child_ctx = UnifiedContextFactory.inherit(
            task_id="task-new",
        )
    """

    @staticmethod
    def from_polaris_context(hp_ctx: Any) -> UnifiedAuditContext:
        """Create UnifiedAuditContext from PolarisContext.

        Args:
            hp_ctx: PolarisContext instance from kernelone.trace.context.

        Returns:
            UnifiedAuditContext with fields mapped from PolarisContext.
        """
        # Extract span_id from span_stack if available
        span_id = ""
        parent_span_id = ""
        if hp_ctx.span_stack:
            current_span = hp_ctx.span_stack[-1]
            span_id = current_span.get("span_id", "")
            parent_span_id = current_span.get("parent_span_id", "")

        return UnifiedAuditContext(
            trace_id=hp_ctx.trace_id or _generate_trace_id(),
            run_id=hp_ctx.run_id or "",
            task_id=hp_ctx.task_id or "",
            workspace=hp_ctx.workspace or "",
            span_id=span_id,
            parent_span_id=parent_span_id,
            metadata=dict(hp_ctx.metadata or {}),
        )

    @staticmethod
    def from_env_vars() -> UnifiedAuditContext | None:
        """Create UnifiedAuditContext from environment variables.

        Checks KERNELONE_* vars first, then KERNELONE_* for compatibility.

        Returns:
            UnifiedAuditContext if trace_id found, None otherwise.
        """
        import os

        trace_id = os.environ.get("KERNELONE_TRACE_ID") or os.environ.get("KERNELONE_TRACE_ID")
        if not trace_id:
            return None

        return UnifiedAuditContext(
            trace_id=trace_id,
            run_id=os.environ.get("KERNELONE_RUN_ID") or os.environ.get("KERNELONE_RUN_ID") or "",
            task_id=os.environ.get("KERNELONE_TASK_ID") or os.environ.get("KERNELONE_TASK_ID") or "",
            workspace=os.environ.get("KERNELONE_WORKSPACE") or os.environ.get("KERNELONE_WORKSPACE") or "",
            metadata={},
        )

    @staticmethod
    def create(
        trace_id: str = "",
        run_id: str = "",
        task_id: str = "",
        workspace: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        instance_id: str = "",
        turn_id: str = "",
        **metadata: Any,
    ) -> UnifiedAuditContext:
        """Create a new UnifiedAuditContext from scratch.

        If trace_id is empty, auto-generates one.

        Args:
            trace_id: Correlation ID (auto-generated if empty).
            run_id: Execution session ID.
            task_id: Task ID.
            workspace: Workspace path.
            span_id: Current span ID.
            parent_span_id: Parent span ID.
            user_id: User ID.
            instance_id: Instance ID.
            turn_id: Turn ID.
            **metadata: Additional metadata.

        Returns:
            New UnifiedAuditContext.
        """
        return UnifiedAuditContext(
            trace_id=trace_id or _generate_trace_id(),
            run_id=run_id,
            task_id=task_id,
            workspace=workspace,
            span_id=span_id or _generate_span_id(),
            parent_span_id=parent_span_id,
            user_id=user_id,
            instance_id=instance_id,
            turn_id=turn_id,
            metadata=dict(metadata),
        )

    @staticmethod
    def inherit(
        trace_id: str = "",
        run_id: str = "",
        task_id: str = "",
        workspace: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        instance_id: str = "",
        turn_id: str = "",
        **metadata: Any,
    ) -> UnifiedAuditContext:
        """Inherit from current context, applying overrides.

        If a field is empty/None, inherits from current context.
        Auto-generates trace_id if both current and provided are empty.

        Args:
            trace_id: Override trace_id.
            run_id: Override run_id.
            task_id: Override task_id.
            workspace: Override workspace.
            span_id: Override span_id.
            parent_span_id: Override parent_span_id.
            user_id: Override user_id.
            instance_id: Override instance_id.
            turn_id: Override turn_id.
            **metadata: Metadata to merge.

        Returns:
            UnifiedAuditContext with inherited fields.
        """
        current = get_current_audit_context()

        # If no current context, create new with provided/empty values
        if current is None:
            return UnifiedContextFactory.create(
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                workspace=workspace,
                span_id=span_id,
                parent_span_id=parent_span_id,
                user_id=user_id,
                instance_id=instance_id,
                turn_id=turn_id,
                **metadata,
            )

        # Merge metadata
        merged_metadata = dict(current.metadata)
        merged_metadata.update(metadata)

        return UnifiedAuditContext(
            trace_id=trace_id or current.trace_id or _generate_trace_id(),
            run_id=run_id or current.run_id,
            task_id=task_id or current.task_id,
            workspace=workspace or current.workspace,
            span_id=span_id or _generate_span_id(),  # Always new span on inherit
            parent_span_id=parent_span_id or current.span_id,
            user_id=user_id or current.user_id,
            instance_id=instance_id or current.instance_id,
            turn_id=turn_id or current.turn_id,
            metadata=merged_metadata,
        )


# =============================================================================
# Async Context Manager for automatic lifecycle
# =============================================================================


class _AuditContextScope:
    """Async context manager for managing unified audit context lifecycle.

    Usage:
        async with AuditContextScope(run_id="run-123", workspace="/path"):
            ctx = get_current_audit_context()
            # ctx.run_id == "run-123" — propagates to all async children
        # Context automatically cleared on exit
    """

    def __init__(
        self,
        trace_id: str = "",
        run_id: str = "",
        task_id: str = "",
        workspace: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        instance_id: str = "",
        turn_id: str = "",
        **metadata: Any,
    ) -> None:
        self._new_context = UnifiedContextFactory.create(
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            workspace=workspace,
            span_id=span_id,
            parent_span_id=parent_span_id,
            user_id=user_id,
            instance_id=instance_id,
            turn_id=turn_id,
            **metadata,
        )
        self._previous_context: UnifiedAuditContext | None = None
        self._token: Token[UnifiedAuditContext | None] | None = None

    async def __aenter__(self) -> UnifiedAuditContext:
        """Enter async context and set the audit context."""
        self._previous_context = _unified_audit_context.get(None)
        self._token = _unified_audit_context.set(self._new_context)
        return self._new_context

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context and restore previous context."""
        # Always clear our state variables first, then reset context
        token = self._token
        self._token = None
        self._previous_context = None
        if token is not None:
            _unified_audit_context.reset(token)


# Public alias
audit_context_scope = _AuditContextScope


# =============================================================================
# Thread-Safe Context for sync code paths
# =============================================================================

_thread_local_context: threading.local = threading.local()


def get_thread_audit_context() -> UnifiedAuditContext | None:
    """Get audit context from thread-local storage (for sync code)."""
    return getattr(_thread_local_context, "context", None)


def set_thread_audit_context(context: UnifiedAuditContext) -> None:
    """Set audit context in thread-local storage (for sync code)."""
    _thread_local_context.context = context


def clear_thread_audit_context() -> None:
    """Clear audit context from thread-local storage (for sync code)."""
    _thread_local_context.context = None


class ThreadAuditContextScope:
    """Sync context manager for managing audit context in sync code.

    Usage:
        with ThreadAuditContextScope(run_id="run-123", workspace="/path"):
            ctx = get_thread_audit_context()
            # ctx.run_id == "run-123"
        # Context automatically cleared on exit
    """

    def __init__(
        self,
        trace_id: str = "",
        run_id: str = "",
        task_id: str = "",
        workspace: str = "",
        span_id: str = "",
        parent_span_id: str = "",
        user_id: str = "",
        instance_id: str = "",
        turn_id: str = "",
        **metadata: Any,
    ) -> None:
        self._new_context = UnifiedContextFactory.create(
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            workspace=workspace,
            span_id=span_id,
            parent_span_id=parent_span_id,
            user_id=user_id,
            instance_id=instance_id,
            turn_id=turn_id,
            **metadata,
        )
        self._previous_context: UnifiedAuditContext | None = None

    def __enter__(self) -> UnifiedAuditContext:
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


# =============================================================================
# Backward compatibility aliases
# =============================================================================

# Keep old AuditContext name as alias for migration
AuditContext = UnifiedAuditContext

# Keep old context manager name as alias
AuditContextManager = _AuditContextScope
AuditContextScope = _AuditContextScope  # backward compat alias

__all__ = [
    "AuditContext",  # backward compat alias
    "AuditContextScope",  # backward compat alias
    "ThreadAuditContextScope",
    "UnifiedAuditContext",
    "UnifiedContextFactory",
    "audit_context_scope",
    "clear_audit_context",
    "clear_thread_audit_context",
    "get_current_audit_context",
    "get_thread_audit_context",
    "set_audit_context",
    "set_thread_audit_context",
]
