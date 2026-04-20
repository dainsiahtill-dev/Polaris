"""Event Registry with wildcard subscription support.

This module provides an EventRegistry that supports:
- Wildcard pattern matching (e.g., "tool.*", "*.error")
- Priority-based handler ordering
- Backward compatibility with MessageBus

Reference: OpenCode packages/opencode/src/bus/index.ts
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.events.typed.schemas import TypedEvent

logger = logging.getLogger(__name__)


# =============================================================================
# Event Pattern Types
# =============================================================================


@dataclass(frozen=True)
class EventPattern:
    """Pattern for wildcard event subscription.

    Supports:
    - fnmatch-style wildcards (*, ?)
    - Category prefixes (tool.*, *.error)
    - Regex patterns (when is_regex=True)

    Examples:
        EventPattern("tool.*")      # Matches tool_invoked, tool_completed, etc.
        EventPattern("*.error")    # Matches tool_error, turn_failed, etc.
        EventPattern("lifecycle.*") # Matches lifecycle events
    """

    pattern: str
    is_regex: bool = False

    def matches(self, event_name: str) -> bool:
        """Check if this pattern matches the given event name.

        Args:
            event_name: The event name to match against

        Returns:
            True if the pattern matches, False otherwise
        """
        if self.is_regex:
            return bool(re.match(self.pattern, event_name))
        return fnmatch.fnmatch(event_name, self.pattern)

    @classmethod
    def from_string(cls, pattern: str) -> EventPattern:
        """Create an EventPattern from a string.

        Detects if the pattern looks like a regex vs fnmatch wildcard:
        - Regex: contains |, or starts with ^ and ends with $, or uses [abc] character classes
        - Wildcard: uses * or ? without regex-specific syntax

        Args:
            pattern: Pattern string

        Returns:
            EventPattern instance
        """
        # Check for clear regex indicators
        has_alternation = "|" in pattern
        has_explicit_anchors = pattern.startswith("^") and pattern.endswith("$")
        has_character_class = "[" in pattern and "]" in pattern

        # If it looks like a real regex, treat it as such
        is_regex = has_alternation or has_explicit_anchors or has_character_class

        return cls(pattern=pattern, is_regex=is_regex)


# =============================================================================
# Subscription Types
# =============================================================================


@dataclass
class Subscription:
    """A single event subscription.

    Attributes:
        id: Unique subscription identifier
        pattern: Event pattern to match
        handler: Callback function for matching events
        priority: Handler priority (higher = called first)
        active: Whether the subscription is active
    """

    id: str
    pattern: EventPattern
    handler: Callable[[TypedEvent], Any]
    priority: int = 0
    active: bool = field(default=True, init=False)

    def unsubscribe(self) -> None:
        """Mark this subscription as inactive."""
        self.active = False


# Type alias for event handlers
EventHandler = Callable[["TypedEvent"], Any]
AsyncEventHandler = Callable[["TypedEvent"], Awaitable[Any]]


# =============================================================================
# Event Registry
# =============================================================================


class EventRegistry:
    """Registry for typed events with wildcard subscription support.

    This registry replaces the static MessageType Enum with a dynamic,
    pattern-based event subscription system.

    Features:
    - Wildcard patterns (tool.*, *.error)
    - Priority-based handler ordering
    - Idempotent subscribe/unsubscribe
    - Thread-safe operations
    - Backward compatibility with MessageBus

    Example:
        registry = EventRegistry()

        # Subscribe to all tool events
        sub_id = registry.subscribe("tool.*", on_tool_event)

        # Subscribe to all errors
        registry.subscribe("*.error", on_error)

        # Emit an event
        await registry.emit(ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="abc123"
        ))

        # Unsubscribe
        registry.unsubscribe(sub_id)

    Reference: OpenCode Bus.Service
    """

    def __init__(self) -> None:
        """Initialize the EventRegistry."""
        # Direct subscriptions: pattern_str -> list[Subscription]
        self._direct_subscriptions: dict[str, list[Subscription]] = {}

        # Wildcard subscriptions: pattern -> list[Subscription]
        # These are checked for every event
        self._wildcard_subscriptions: list[Subscription] = []

        # All subscriptions by ID for O(1) lookup
        self._subscriptions_by_id: dict[str, Subscription] = {}

        # Lock for thread-safe operations
        self._lock: threading.Lock | None = None

        # Statistics
        self._emit_count: int = 0
        self._handler_invocation_count: int = 0

    def _get_lock(self) -> threading.Lock:
        """Return a lock for thread-safe operations.

        Uses double-checked locking to avoid race conditions during lock creation.
        threading.Lock is used instead of asyncio.Lock because asyncio.Lock
        cross-thread behavior is undefined.

        The pattern ensures only one thread creates the lock, and subsequent
        threads get the same lock instance for mutual exclusion.
        """
        # Fast path: lock already exists
        if self._lock is not None:
            return self._lock
        # Slow path: create lock with mutual exclusion
        with threading.Lock():
            # Double-check after acquiring lock
            if self._lock is None:
                self._lock = threading.Lock()
            return self._lock

    # -------------------------------------------------------------------------
    # Subscription Management
    # -------------------------------------------------------------------------

    def subscribe(
        self,
        pattern: str | EventPattern,
        handler: EventHandler,
        subscription_id: str | None = None,
        priority: int = 0,
    ) -> str:
        """Subscribe to events matching a pattern.

        Args:
            pattern: Event name pattern (supports * and ? wildcards)
            handler: Callback for matching events (sync or async)
            subscription_id: Optional subscription ID (defaults to handler id)
            priority: Handler priority (higher = called first)

        Returns:
            Subscription ID for later unsubscription

        Example:
            registry.subscribe("tool.*", on_tool_event)
            registry.subscribe("*.error", on_error, priority=10)
        """
        pattern_obj = EventPattern.from_string(pattern) if isinstance(pattern, str) else pattern

        sub_id = subscription_id or f"sub_{uuid.uuid4().hex[:12]}"

        # Check for duplicate
        if sub_id in self._subscriptions_by_id:
            existing = self._subscriptions_by_id[sub_id]
            if existing.active:
                logger.debug(f"Subscription {sub_id} already exists and is active")
                return sub_id
            # Reactivate if was inactive
            existing.active = True
            return sub_id

        subscription = Subscription(
            id=sub_id,
            pattern=pattern_obj,
            handler=handler,
            priority=priority,
        )

        self._subscriptions_by_id[sub_id] = subscription

        # Categorize subscription
        if self._is_wildcard_pattern(pattern_obj.pattern):
            self._wildcard_subscriptions.append(subscription)
            self._wildcard_subscriptions.sort(key=lambda s: -s.priority)
        else:
            if pattern_obj.pattern not in self._direct_subscriptions:
                self._direct_subscriptions[pattern_obj.pattern] = []
            self._direct_subscriptions[pattern_obj.pattern].append(subscription)

        logger.debug(f"Subscribed: pattern={pattern_obj.pattern!r} id={sub_id}")
        return sub_id

    def subscribe_once(
        self,
        pattern: str | EventPattern,
        handler: EventHandler,
        subscription_id: str | None = None,
        priority: int = 0,
    ) -> str:
        """Subscribe for a single event, then auto-unsubscribe.

        Args:
            pattern: Event name pattern
            handler: One-time callback
            subscription_id: Optional subscription ID
            priority: Handler priority

        Returns:
            Subscription ID
        """
        sub_id = self.subscribe(pattern, handler, subscription_id, priority)

        # Wrap handler to auto-unsubscribe
        original_handler = self._subscriptions_by_id[sub_id].handler

        def auto_unsubscribe(event: TypedEvent) -> Any:
            result = original_handler(event)
            self.unsubscribe(sub_id)
            return result

        self._subscriptions_by_id[sub_id].handler = auto_unsubscribe
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe by subscription ID.

        Args:
            subscription_id: Subscription ID returned from subscribe()

        Returns:
            True if found and removed, False if not found
        """
        subscription = self._subscriptions_by_id.get(subscription_id)

        if subscription is None:
            logger.debug(f"Subscription not found: {subscription_id}")
            return False

        if not subscription.active:
            logger.debug(f"Subscription already inactive: {subscription_id}")
            return False

        subscription.active = False

        # Remove from direct subscriptions
        direct_list = self._direct_subscriptions.get(subscription.pattern.pattern)
        if direct_list:
            direct_list[:] = [s for s in direct_list if s.id != subscription_id]
            if not direct_list:
                del self._direct_subscriptions[subscription.pattern.pattern]

        # Remove from wildcard subscriptions
        self._wildcard_subscriptions[:] = [s for s in self._wildcard_subscriptions if s.id != subscription_id]

        # Remove from ID index
        del self._subscriptions_by_id[subscription_id]

        logger.debug(f"Unsubscribed: id={subscription_id}")
        return True

    def unsubscribe_all(self) -> int:
        """Unsubscribe all active subscriptions.

        Returns:
            Number of subscriptions removed
        """
        count = len(self._subscriptions_by_id)
        for sub_id in list(self._subscriptions_by_id.keys()):
            self.unsubscribe(sub_id)
        return count

    # -------------------------------------------------------------------------
    # Event Emission
    # -------------------------------------------------------------------------

    async def emit(self, event: TypedEvent) -> None:
        """Emit an event to all matching subscribers.

        Args:
            event: The event to emit
        """
        self._emit_count += 1
        event_name = event.event_name

        logger.debug(f"Emitting: event={event_name!r} id={event.event_id}")

        # Get matching subscriptions under lock to prevent race with subscribe/unsubscribe
        with self._get_lock():
            subscriptions = self._get_matching_subscriptions_locked(event_name)

        if not subscriptions:
            logger.debug(f"No subscribers for event: {event_name}")
            return

        # Invoke handlers (outside lock for performance)
        await self._invoke_handlers(event, subscriptions)

    def _get_matching_subscriptions(self, event_name: str) -> list[Subscription]:
        """Get all subscriptions that match the event name.

        This method checks:
        1. Wildcard subscriptions that match the event name
        2. Direct subscriptions with exact pattern match

        Args:
            event_name: The event name to match

        Returns:
            List of matching subscriptions, sorted by priority (descending)
        """
        with self._get_lock():
            return self._get_matching_subscriptions_locked(event_name)

    def _get_matching_subscriptions_locked(self, event_name: str) -> list[Subscription]:
        """Get matching subscriptions. Caller must hold _get_lock().

        Args:
            event_name: The event name to match

        Returns:
            List of matching subscriptions, sorted by priority (descending)
        """
        result: list[Subscription] = []
        seen_ids: set[str] = set()

        # First: Check wildcard subscriptions
        # These are evaluated for EVERY event to support patterns like "tool.*"
        for subscription in self._wildcard_subscriptions:
            if subscription.active and subscription.pattern.matches(event_name) and subscription.id not in seen_ids:
                result.append(subscription)
                seen_ids.add(subscription.id)

        # Second: Check direct subscriptions
        # Also check if direct subscriptions match via pattern matching
        # (handles cases where exact name was registered as wildcard)
        for sub_list in self._direct_subscriptions.values():
            for subscription in sub_list:
                if (
                    subscription.active
                    and subscription.id not in seen_ids
                    and (subscription.pattern.pattern == event_name or subscription.pattern.matches(event_name))
                ):
                    result.append(subscription)
                    seen_ids.add(subscription.id)

        # Sort by priority (descending)
        result.sort(key=lambda s: -s.priority)
        return result

    async def _invoke_handlers(
        self,
        event: TypedEvent,
        subscriptions: list[Subscription],
    ) -> None:
        """Invoke handlers for matching subscriptions.

        Args:
            event: The event to handle
            subscriptions: Matching subscriptions
        """
        pending_tasks: list[asyncio.Task[Any]] = []
        sync_handlers_called: int = 0

        for subscription in subscriptions:
            if not subscription.active:
                continue

            try:
                handler = subscription.handler
                result = handler(event)

                # Handle async handlers
                if asyncio.iscoroutine(result):
                    pending_tasks.append(asyncio.create_task(result))  # type: ignore[arg-type]
                elif asyncio.isfuture(result):
                    pending_tasks.append(asyncio.shield(result))  # type: ignore[arg-type]
                else:
                    # Sync handler called successfully
                    sync_handlers_called += 1

            except (RuntimeError, ValueError) as e:
                logger.warning(
                    f"Event handler error: subscription={subscription.id} event={event.event_name} error={e}",
                    exc_info=True,
                )

        # Update sync handler count immediately
        self._handler_invocation_count += sync_handlers_called

        # Wait for all async handlers
        if pending_tasks:
            try:
                results = await asyncio.gather(*pending_tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning(
                            f"Async event handler error: index={i} error={result}",
                            exc_info=True,
                        )
                    else:
                        self._handler_invocation_count += 1

            except asyncio.CancelledError:
                # Properly cancel all pending tasks to prevent leaks
                for task in pending_tasks:
                    if not task.done():
                        task.cancel()
                        # Give cancelled tasks a chance to clean up
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(task),
                                timeout=0.1,
                            )
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                        except (RuntimeError, ValueError) as e:
                            logger.debug(f"Task cleanup error: {e}")
                raise

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    @property
    def subscription_count(self) -> int:
        """Get the number of active subscriptions."""
        return sum(1 for s in self._subscriptions_by_id.values() if s.active)

    @property
    def emit_count(self) -> int:
        """Get the total number of events emitted."""
        return self._emit_count

    @property
    def handler_invocation_count(self) -> int:
        """Get the total number of handler invocations."""
        return self._handler_invocation_count

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        """Get subscription by ID.

        Args:
            subscription_id: Subscription ID

        Returns:
            Subscription if found, None otherwise
        """
        return self._subscriptions_by_id.get(subscription_id)

    def get_active_subscriptions(self) -> list[Subscription]:
        """Get all active subscriptions.

        Returns:
            List of active subscriptions
        """
        return [s for s in self._subscriptions_by_id.values() if s.active]

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def _is_wildcard_pattern(pattern: str) -> bool:
        """Check if a pattern contains wildcards.

        Args:
            pattern: Pattern string

        Returns:
            True if pattern contains wildcards
        """
        return "*" in pattern or "?" in pattern


# =============================================================================
# Global Registry Instance
# =============================================================================

# Module-level default registry for convenience
_default_registry: EventRegistry | None = None


def get_default_registry() -> EventRegistry:
    """Get the default global EventRegistry instance.

    Returns:
        Default EventRegistry (singleton)
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = EventRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """Reset the default global EventRegistry.

    This function is primarily for test isolation. It clears the singleton
    so tests can start with a fresh registry without accumulated subscriptions.
    """
    global _default_registry
    _default_registry = None


async def emit_event(event: TypedEvent) -> None:
    """Emit an event to the default registry.

    Args:
        event: The event to emit
    """
    await get_default_registry().emit(event)


def subscribe(
    pattern: str | EventPattern,
    handler: EventHandler,
    subscription_id: str | None = None,
    priority: int = 0,
) -> str:
    """Subscribe to events on the default registry.

    Args:
        pattern: Event name pattern
        handler: Callback function
        subscription_id: Optional subscription ID
        priority: Handler priority

    Returns:
        Subscription ID
    """
    return get_default_registry().subscribe(pattern, handler, subscription_id, priority)


def subscribe_once(
    pattern: str | EventPattern,
    handler: EventHandler,
    subscription_id: str | None = None,
    priority: int = 0,
) -> str:
    """Subscribe once to an event on the default registry.

    Args:
        pattern: Event name pattern
        handler: One-time callback
        subscription_id: Optional subscription ID
        priority: Handler priority

    Returns:
        Subscription ID
    """
    return get_default_registry().subscribe_once(pattern, handler, subscription_id, priority)


def unsubscribe(subscription_id: str) -> bool:
    """Unsubscribe from the default registry.

    Args:
        subscription_id: Subscription ID

    Returns:
        True if found and removed
    """
    return get_default_registry().unsubscribe(subscription_id)
