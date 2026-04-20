"""Tool call tracker for KernelOne.

This module provides ToolCallTracker for managing multiple
tool calls in flight with event emission support.

Reference: OpenCode tool call tracking patterns
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.events.typed import EventRegistry
    from polaris.kernelone.tool_state import ToolErrorKind, ToolState, ToolStateStatus

logger = logging.getLogger(__name__)


# Type aliases
ToolStateCallback = Callable[["ToolState"], Awaitable[None]]
SyncToolStateCallback = Callable[["ToolState"], None]


# =============================================================================
# Tool Call Tracker
# =============================================================================


class ToolCallTracker:
    """Tracks multiple tool calls in flight.

    This class provides:
    - Thread-safe tracking of all active tool calls
    - State transition with validation
    - Event emission for state changes
    - Query capabilities for state inspection

    Example:
        tracker = ToolCallTracker()

        # Create a new tool call
        state = await tracker.create("read_file", execution_lane="direct")

        # Transition to running
        await tracker.transition(state.tool_call_id, ToolStateStatus.RUNNING)

        # Transition to completed
        await tracker.transition(
            state.tool_call_id,
            ToolStateStatus.COMPLETED,
            result={"content": "file contents"},
        )

        # Get current state
        current = await tracker.get(state.tool_call_id)
        assert current.is_completed
    """

    def __init__(self, event_registry: EventRegistry | None = None) -> None:
        """Initialize the tracker.

        Args:
            event_registry: Optional event registry for emitting state change events
        """
        self._states: dict[str, ToolState] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._event_registry = event_registry
        self._state_callbacks: list[ToolStateCallback] = []
        self._sync_callbacks: list[SyncToolStateCallback] = []

    def _get_lock(self) -> asyncio.Lock:
        """Return a loop-local lock to avoid cross-loop binding issues.

        Uses double-checked locking to avoid race conditions during lock creation.
        """
        loop = asyncio.get_running_loop()
        # Fast path: lock already exists for this loop
        if self._lock is not None and self._lock_loop is loop:
            return self._lock
        # Slow path: need to create lock
        lock = asyncio.Lock()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = lock
            self._lock_loop = loop
        return self._lock

    # -------------------------------------------------------------------------
    # State Management
    # -------------------------------------------------------------------------

    async def create(
        self,
        tool_name: str,
        tool_call_id: str | None = None,
        execution_lane: str = "direct",
        correlation_id: str | None = None,
        metadata: dict | None = None,
    ) -> ToolState:
        """Create a new tool state.

        Args:
            tool_name: Name of the tool
            tool_call_id: Optional ID (generated if not provided)
            execution_lane: Execution context
            correlation_id: Parent operation correlation ID
            metadata: Additional metadata

        Returns:
            New ToolState instance

        Raises:
            ValueError: If tool_call_id already exists
        """
        from polaris.kernelone.tool_state import ToolState

        call_id = tool_call_id or uuid.uuid4().hex[:12]

        async with self._get_lock():
            if call_id in self._states:
                raise ValueError(f"Tool call {call_id} already exists")

            state = ToolState(
                tool_call_id=call_id,
                tool_name=tool_name,
                execution_lane=execution_lane,
                correlation_id=correlation_id,
                metadata=metadata or {},
            )
            self._states[call_id] = state
            return state

    async def get(self, tool_call_id: str) -> ToolState | None:
        """Get tool state by ID.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            ToolState if found, None otherwise
        """
        async with self._get_lock():
            return self._states.get(tool_call_id)

    async def list_all(self) -> list[ToolState]:
        """Get all tracked tool states.

        Returns:
            List of all ToolState instances
        """
        async with self._get_lock():
            return list(self._states.values())

    async def list_by_status(self, status: ToolStateStatus) -> list[ToolState]:
        """Get all tool states with a specific status.

        Args:
            status: Status to filter by

        Returns:
            List of matching ToolState instances
        """
        async with self._get_lock():
            return [s for s in self._states.values() if s.status == status]

    async def list_running(self) -> list[ToolState]:
        """Get all currently running tool states.

        Returns:
            List of running ToolState instances
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.list_by_status(ToolStateStatus.RUNNING)

    async def list_pending(self) -> list[ToolState]:
        """Get all pending tool states.

        Returns:
            List of pending ToolState instances
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.list_by_status(ToolStateStatus.PENDING)

    async def list_terminal(self) -> list[ToolState]:
        """Get all terminal (completed/failed) tool states.

        Returns:
            List of terminal ToolState instances
        """
        async with self._get_lock():
            return [s for s in self._states.values() if s.is_terminal]

    async def count(self) -> int:
        """Get total number of tracked tool calls.

        Returns:
            Number of tool calls
        """
        async with self._get_lock():
            return len(self._states)

    async def count_by_status(self, status: ToolStateStatus) -> int:
        """Get count of tool calls with a specific status.

        Args:
            status: Status to count

        Returns:
            Number of tool calls with this status
        """
        async with self._get_lock():
            return sum(1 for s in self._states.values() if s.status == status)

    async def remove(self, tool_call_id: str) -> bool:
        """Remove a tool state.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            True if removed, False if not found
        """
        async with self._get_lock():
            if tool_call_id in self._states:
                del self._states[tool_call_id]
                return True
            return False

    async def clear(self) -> int:
        """Clear all tracked tool states.

        Returns:
            Number of states cleared
        """
        async with self._get_lock():
            count = len(self._states)
            self._states.clear()
            return count

    # -------------------------------------------------------------------------
    # State Transitions
    # -------------------------------------------------------------------------

    async def transition(
        self,
        tool_call_id: str,
        new_status: ToolStateStatus,
        **kwargs: Any,
    ) -> ToolState | None:
        """Transition a tool call to a new state.

        Args:
            tool_call_id: Tool call identifier
            new_status: Target state
            **kwargs: Additional arguments for transition:
                - error_kind: ToolErrorKind
                - error_message: str
                - error_stack: str
                - result: execution result
                - sub_state: optional sub-state

        Returns:
            Updated ToolState if found, None if not found
        """
        async with self._get_lock():
            state = self._states.get(tool_call_id)
            if state is None:
                logger.warning(f"Tool call not found: {tool_call_id}")
                return None

            # Perform transition
            old_status = state.status
            state.transition(new_status, **kwargs)

            logger.debug(f"Tool state transition: {tool_call_id} {old_status.value} -> {new_status.value}")

            # Call callbacks
            await self._notify_callbacks(state)
            await self._emit_state_transition_event(
                state=state,
                old_status=old_status,
                new_status=new_status,
            )

            return state

    async def start(self, tool_call_id: str) -> ToolState | None:
        """Transition a tool call from PENDING to RUNNING.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            Updated ToolState if found, None if not found
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.transition(tool_call_id, ToolStateStatus.RUNNING)

    async def complete(self, tool_call_id: str, result: Any = None) -> ToolState | None:
        """Transition a tool call to COMPLETED.

        Args:
            tool_call_id: Tool call identifier
            result: Execution result

        Returns:
            Updated ToolState if found, None if not found
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.transition(tool_call_id, ToolStateStatus.COMPLETED, result=result)

    async def fail(
        self,
        tool_call_id: str,
        error: str,
        error_kind: ToolErrorKind | None = None,
        **kwargs: Any,
    ) -> ToolState | None:
        """Transition a tool call to ERROR.

        Args:
            tool_call_id: Tool call identifier
            error: Error message
            error_kind: Error classification
            **kwargs: Additional arguments

        Returns:
            Updated ToolState if found, None if not found
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.transition(
            tool_call_id,
            ToolStateStatus.ERROR,
            error_kind=error_kind,
            error_message=error,
            **kwargs,
        )

    async def timeout(self, tool_call_id: str, timeout_seconds: int | None = None) -> ToolState | None:
        """Transition a tool call to TIMEOUT.

        Args:
            tool_call_id: Tool call identifier
            timeout_seconds: Configured timeout for error message

        Returns:
            Updated ToolState if found, None if not found
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        error_msg = "Tool execution timed out"
        if timeout_seconds:
            error_msg += f" ({timeout_seconds}s)"

        return await self.transition(tool_call_id, ToolStateStatus.TIMEOUT, error_message=error_msg)

    async def cancel(self, tool_call_id: str) -> ToolState | None:
        """Transition a tool call to CANCELLED.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            Updated ToolState if found, None if not found
        """
        from polaris.kernelone.tool_state import ToolStateStatus

        return await self.transition(tool_call_id, ToolStateStatus.CANCELLED, error_message="Cancelled by user")

    async def retry(self, tool_call_id: str) -> ToolState | None:
        """Reset a tool call for retry.

        Args:
            tool_call_id: Tool call identifier

        Returns:
            Updated ToolState if found, None if not found

        Raises:
            ValueError: If max retries exceeded
        """
        async with self._get_lock():
            state = self._states.get(tool_call_id)
            if state is None:
                return None

            state.retry()
            await self._notify_callbacks(state)
            return state

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def add_callback(self, callback: ToolStateCallback | SyncToolStateCallback) -> None:
        """Add a callback for state changes.

        Args:
            callback: Async or sync callback function
        """
        if inspect.iscoroutinefunction(callback):
            self._state_callbacks.append(callback)  # type: ignore[arg-type, misc]
        else:
            self._sync_callbacks.append(callback)  # type: ignore[arg-type, misc]

    def remove_callback(self, callback: ToolStateCallback | SyncToolStateCallback) -> bool:
        """Remove a callback.

        Args:
            callback: Callback to remove

        Returns:
            True if removed, False if not found
        """
        if inspect.iscoroutinefunction(callback):
            try:
                self._state_callbacks.remove(callback)  # type: ignore
                return True
            except ValueError:
                return False
        else:
            try:
                self._sync_callbacks.remove(callback)  # type: ignore
                return True
            except ValueError:
                return False

    async def _notify_callbacks(self, state: ToolState) -> None:
        """Notify all callbacks of state change.

        Args:
            state: Updated tool state
        """
        # Sync callbacks first
        for callback in self._sync_callbacks:
            try:
                callback(state)
            except (RuntimeError, ValueError) as e:
                logger.warning(f"State callback error: {e}")

        # Async callbacks
        for async_callback in self._state_callbacks:
            try:
                await async_callback(state)
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Async state callback error: {e}")

    async def _emit_state_transition_event(
        self,
        *,
        state: ToolState,
        old_status: ToolStateStatus,
        new_status: ToolStateStatus,
    ) -> None:
        """Emit typed events for state transitions when a registry is configured."""
        del old_status
        if self._event_registry is None:
            return

        from polaris.kernelone.events.typed import (
            ToolBlocked,
            ToolCompleted,
            ToolError,
            ToolErrorKind as TypedToolErrorKind,
            ToolInvoked,
            ToolTimeout,
        )
        from polaris.kernelone.tool_state import ToolStateStatus

        metadata = state.metadata if isinstance(state.metadata, dict) else {}
        run_id = str(metadata.get("run_id") or "").strip()
        workspace = str(metadata.get("workspace") or "").strip()
        correlation_id = str(state.correlation_id or "").strip() or None

        typed_event: Any | None = None
        if new_status == ToolStateStatus.RUNNING:
            arguments = metadata.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            typed_event = ToolInvoked.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                arguments=arguments,
                execution_lane=state.execution_lane,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )
        elif new_status == ToolStateStatus.COMPLETED:
            typed_event = ToolCompleted.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                result=state.result,
                duration_ms=state.duration_ms,
                output_size=state.output_size,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )
        elif new_status == ToolStateStatus.ERROR:
            typed_event = ToolError.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                error=str(state.error_message or "tool execution failed"),
                error_type=self._to_typed_error_kind(state.error_kind),
                stack_trace=state.error_stack,
                duration_ms=state.duration_ms,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )
        elif new_status == ToolStateStatus.TIMEOUT:
            timeout_seconds = self._safe_int(metadata.get("timeout_seconds"), default=0)
            if timeout_seconds <= 0:
                timeout_seconds = self._safe_int(metadata.get("timeout"), default=0)
            if timeout_seconds <= 0:
                timeout_seconds = 1
            typed_event = ToolTimeout.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                timeout_seconds=timeout_seconds,
                duration_ms=state.duration_ms,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )
        elif new_status == ToolStateStatus.BLOCKED:
            policy = str(metadata.get("policy") or "").strip() or None
            typed_event = ToolBlocked.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                reason=str(state.error_message or "tool blocked by policy"),
                policy=policy,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )
        elif new_status == ToolStateStatus.CANCELLED:
            typed_event = ToolError.create(
                tool_name=state.tool_name,
                tool_call_id=state.tool_call_id,
                error=str(state.error_message or "cancelled"),
                error_type=TypedToolErrorKind.CANCELLED,
                duration_ms=state.duration_ms,
                run_id=run_id,
                workspace=workspace,
                correlation_id=correlation_id,
            )

        if typed_event is None:
            return

        try:
            await self._event_registry.emit(typed_event)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to emit typed tool event tool_call_id=%s status=%s: %s",
                state.tool_call_id,
                new_status.value,
                exc,
            )

    def _to_typed_error_kind(self, error_kind: Any) -> Any | None:
        if error_kind is None:
            return None
        from polaris.kernelone.events.typed import ToolErrorKind as TypedToolErrorKind

        raw_value = str(getattr(error_kind, "value", error_kind) or "").strip()
        if not raw_value:
            return None
        try:
            return TypedToolErrorKind(raw_value)
        except ValueError:
            return TypedToolErrorKind.UNKNOWN

    def _safe_int(self, value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)
