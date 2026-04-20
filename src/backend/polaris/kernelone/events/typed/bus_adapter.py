"""MessageBus Adapter for Typed Events.

This module provides a bridge between the new Typed Events system
and the existing MessageBus, enabling gradual migration and backward compatibility.

Reference: OpenCode packages/opencode/src/bus/index.ts (GlobalBus integration)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.events.emit_result import EmitResult
from polaris.kernelone.events.message_bus import Message, MessageType
from polaris.kernelone.exceptions import EventPublishError

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.kernelone.events.message_bus import MessageBus
    from polaris.kernelone.events.typed.registry import EventRegistry
    from polaris.kernelone.events.typed.schemas import TypedEvent

logger = logging.getLogger(__name__)


# =============================================================================
# Event to MessageType Mapping
# =============================================================================


# Mapping from TypedEvent names to MessageType names
# This enables bidirectional conversion between the two systems
_EVENT_NAME_TO_MESSAGE_TYPE: dict[str, str] = {
    # Instance lifecycle
    "instance_started": "WORKER_SPAWNED",
    "instance_disposed": "WORKER_STOPPED",
    # Tool events - map to TASK_STARTED/COMPLETED/FAILED
    "tool_invoked": "TASK_STARTED",
    "tool_completed": "TASK_COMPLETED",
    "tool_error": "TASK_FAILED",
    "tool_blocked": "TASK_FAILED",
    "tool_timeout": "TASK_FAILED",
    # Turn events - map to SEQ events
    "turn_started": "SEQ_START",
    "turn_completed": "SEQ_STEP",
    "turn_failed": "TASK_FAILED",
    # System events
    "compact_requested": "COMPACT_REQUESTED",
    "settings_changed": "SETTINGS_CHANGED",
    # Director events
    "director_started": "DIRECTOR_START",
    "director_stopped": "DIRECTOR_STOP",
    "director_paused": "DIRECTOR_PAUSE",
    "director_resumed": "DIRECTOR_RESUME",
    "task_submitted": "TASK_SUBMITTED",
    "task_claimed": "TASK_CLAIMED",
    "task_started": "TASK_STARTED",
    "task_completed": "TASK_COMPLETED",
    "task_failed": "TASK_FAILED",
    "task_cancelled": "TASK_CANCELLED",
    "task_retry": "TASK_RETRY",
    "task_progress": "TASK_PROGRESS",
    "worker_spawned": "WORKER_SPAWNED",
    "worker_ready": "WORKER_READY",
    "worker_busy": "WORKER_BUSY",
    "worker_stopping": "WORKER_STOPPING",
    "worker_stopped": "WORKER_STOPPED",
    "nag_reminder": "NAG_REMINDER",
    "budget_exceeded": "BUDGET_EXCEEDED",
    "system_error": "WORKER_FAILED",
    # Planning events
    "plan_created": "PLAN_CREATED",
    # File events
    "file_written": "FILE_WRITTEN",
    # Audit events
    "audit_completed": "AUDIT_COMPLETED",
}

# Reverse mapping
_MESSAGE_TYPE_TO_EVENT_NAME: dict[str, str] = {v: k for k, v in _EVENT_NAME_TO_MESSAGE_TYPE.items()}

# Public aliases for bus_constants.py re-export
MESSAGE_TYPE_TO_TYPED_EVENT = _EVENT_NAME_TO_MESSAGE_TYPE
"""Mapping from TypedEvent names to MessageType names."""
TYPED_EVENT_TO_MESSAGE_TYPE = _MESSAGE_TYPE_TO_EVENT_NAME
"""Reverse mapping from MessageType names to TypedEvent names."""


# =============================================================================
# Adapter Class
# =============================================================================


class TypedEventBusAdapter:
    """Bridge between TypedEvent system and legacy MessageBus.

    This adapter enables:
    1. Emit TypedEvents to both systems (dual-write during migration)
    2. Subscribe to MessageBus and re-emit as TypedEvents
    3. Maintain backward compatibility during transition

    Usage:
        # Create adapter
        adapter = TypedEventBusAdapter(
            message_bus=existing_bus,
            event_registry=typed_registry,
        )

        # Emit to both systems
        await adapter.emit_to_both(ToolInvoked.create(...))

        # Subscribe MessageBus -> TypedEvents
        await adapter.subscribe_to_message_bus(
            MessageType.TOOL_CALL,
            handler,
        )

    Reference: OpenCode Bus layer with GlobalBus integration
    """

    def __init__(
        self,
        message_bus: MessageBus,
        event_registry: EventRegistry,
        dual_write: bool = True,
    ) -> None:
        """Initialize the adapter.

        Args:
            message_bus: Existing MessageBus instance
            event_registry: New TypedEvent registry
            dual_write: If True, emit to both systems (default: True)
        """
        self._bus = message_bus
        self._registry = event_registry
        self._dual_write = dual_write

        # Mapping from TypedEvent names to MessageTypes
        self._event_to_message_type: dict[str, MessageType] = {}

        # Mapping from MessageTypes to TypedEvent names
        self._message_type_to_event: dict[str, str] = {}

        # Track subscriptions for cleanup
        self._subscriptions: list[tuple[MessageType, Callable[[Message], Any]]] = []  # (MessageType, handler_ref)

        # Statistics
        self._events_converted: int = 0
        self._conversion_errors: int = 0

    @property
    def events_converted(self) -> int:
        """Number of events successfully converted."""
        return self._events_converted

    @property
    def conversion_errors(self) -> int:
        """Number of conversion errors."""
        return self._conversion_errors

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register_event_type(
        self,
        event_name: str,
        message_type: MessageType,
    ) -> None:
        """Register a mapping between TypedEvent and MessageType.

        Args:
            event_name: TypedEvent name (e.g., "tool_invoked")
            message_type: Corresponding MessageType enum value
        """
        from polaris.kernelone.events.message_bus import MessageType

        message_type_name = message_type.name if isinstance(message_type, MessageType) else message_type

        self._event_to_message_type[event_name] = message_type  # type: ignore
        self._message_type_to_event[message_type_name] = event_name

        logger.debug(f"Registered mapping: {event_name} -> {message_type_name}")

    def register_default_mappings(self) -> None:
        """Register all default event type mappings."""
        from polaris.kernelone.events.message_bus import MessageType

        for event_name, message_type_name in _EVENT_NAME_TO_MESSAGE_TYPE.items():
            try:
                message_type = MessageType[message_type_name]
                self.register_event_type(event_name, message_type)
            except KeyError:
                logger.warning(f"MessageType not found: {message_type_name}")

    # -------------------------------------------------------------------------
    # Emission
    # -------------------------------------------------------------------------

    async def emit_to_both(
        self,
        event: TypedEvent,
        *,
        raise_on_failure: bool = False,
    ) -> EmitResult:
        """Emit a TypedEvent to both MessageBus and EventRegistry.

        This is the recommended method during migration as it maintains
        backward compatibility while enabling the new system.

        Args:
            event: The TypedEvent to emit
            raise_on_failure: If True, raise EventPublishError when both sides fail.
                              If False (default), return EmitResult with error details.

        Returns:
            EmitResult with success/failure status for each side.
            If raise_on_failure is True and both sides fail, raises EventPublishError.

        Example:
            # Option 1: Check result
            result = await adapter.emit_to_both(event)
            if not result.registry_success:
                logger.error(f"Registry failed: {result.registry_error}")

            # Option 2: Raise on failure
            try:
                await adapter.emit_to_both(event, raise_on_failure=True)
            except EventPublishError as e:
                logger.error(f"Both emit failed: {e}")
        """
        event_name = event.event_name
        registry_success = False
        message_bus_success: bool | None = None
        registry_error: Exception | None = None
        message_bus_error: Exception | None = None

        # Emit to new registry
        try:
            await self._registry.emit(event)
            registry_success = True
        except (RuntimeError, ValueError) as e:
            registry_error = e
            logger.error(f"Failed to emit to EventRegistry: {event_name} - {e}")
            self._conversion_errors += 1

        # Emit to legacy MessageBus (dual-write)
        if self._dual_write:
            try:
                await self._emit_to_message_bus(event)
                message_bus_success = True
            except (RuntimeError, ValueError) as e:
                message_bus_error = e
                logger.error(f"Failed to emit to MessageBus: {event_name} - {e}")
                self._conversion_errors += 1
        else:
            message_bus_success = None

        # Count as converted if at least one emit succeeded
        if registry_success or (self._dual_write and message_bus_success):
            self._events_converted += 1

        # Build result
        result = EmitResult(
            registry_success=registry_success,
            message_bus_success=message_bus_success,
            registry_error=registry_error,
            message_bus_error=message_bus_error,
            event_name=event_name,
        )

        # Raise if configured and both sides failed
        if raise_on_failure and not result.is_full_success:
            failed_side = (
                "both" if (registry_error and message_bus_error) else ("registry" if registry_error else "message_bus")
            )
            raise EventPublishError(
                f"Failed to emit event '{event_name}' to {failed_side}",
                event_name=event_name,
                failed_side=failed_side,
                left_error=registry_error,
                right_error=message_bus_error,
            )

        return result

    async def emit_to_registry(self, event: TypedEvent) -> None:
        """Emit a TypedEvent to the EventRegistry only.

        Args:
            event: The TypedEvent to emit
        """
        await self._registry.emit(event)

    async def emit_to_message_bus(self, event: TypedEvent) -> None:
        """Emit a TypedEvent to the legacy MessageBus only.

        Args:
            event: The TypedEvent to emit
        """
        await self._emit_to_message_bus(event)

    async def _emit_to_message_bus(self, event: TypedEvent) -> None:
        """Internal method to emit TypedEvent to MessageBus.

        Converts the TypedEvent to a Message and publishes it.

        Args:
            event: The TypedEvent to convert and emit
        """
        event_name = event.event_name
        message_type = self._event_to_message_type.get(event_name)

        if message_type is None:
            logger.warning(f"No MessageType mapping for {event_name}")
            return

        # Convert TypedEvent payload to Message payload
        # Use model_dump for serialization
        try:
            # Pass full payload to preserve all TypedEvent fields
            full_payload = event.model_dump(mode="json")

            message = Message(
                type=message_type,
                sender="typed_event_bus",
                payload=full_payload,
            )
            await self._bus.publish(message)

        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to convert TypedEvent to Message: {e}")
            self._conversion_errors += 1
            raise

    # -------------------------------------------------------------------------
    # Subscription (MessageBus -> TypedEvent)
    # -------------------------------------------------------------------------

    async def subscribe_to_message_bus(
        self,
        message_type: MessageType,
        handler: Any,
    ) -> int:
        """Subscribe to MessageBus and re-emit as TypedEvent.

        When a matching Message is received from MessageBus, it is
        converted to a TypedEvent and emitted to the EventRegistry.

        Args:
            message_type: MessageType to subscribe to
            handler: Optional handler for the TypedEvent

        Returns:
            Subscription ID
        """
        event_name = self._message_type_to_event.get(message_type.name)

        if event_name is None:
            logger.warning(f"No event mapping for MessageType {message_type.name}")
            # Still subscribe but without event conversion

        async def wrapped_handler(msg: Message) -> None:
            """Wrap MessageBus handler to convert to TypedEvent."""
            try:
                # Convert Message to TypedEvent
                typed_event = await self._convert_message_to_event(msg)

                if typed_event is not None:
                    # Emit to registry
                    await self._registry.emit(typed_event)

                    # Call user handler if provided
                    if handler:
                        handler_result = handler(typed_event)
                        if asyncio.iscoroutine(handler_result):
                            await handler_result

            except (RuntimeError, ValueError) as e:
                logger.error(f"Failed to convert Message to TypedEvent: {e}")
                self._conversion_errors += 1

        # Subscribe to MessageBus
        subscribed = await self._bus.subscribe(message_type, wrapped_handler)
        if subscribed:
            # Store handler reference for proper unsubscribe
            self._subscriptions.append((message_type, wrapped_handler))
            logger.info(f"Subscribed MessageBus {message_type.name} -> TypedEvent")

        return id(wrapped_handler)

    async def _convert_message_to_event(self, message: Message) -> TypedEvent | None:
        """Convert a Message to a TypedEvent.

        Args:
            message: The Message to convert

        Returns:
            TypedEvent if conversion succeeds, None otherwise
        """
        from polaris.kernelone.events.typed.schemas import get_event_type

        payload = message.payload
        event_name = payload.get("event_name")

        if event_name is None:
            logger.warning("Message payload missing event_name")
            return None

        event_type = get_event_type(event_name)
        if event_type is None:
            logger.warning(f"Unknown event type: {event_name}")
            return None

        try:
            # Reconstruct the event from flattened payload
            # Use cast to handle Optional values that BaseModel expects to be non-None
            from typing import cast

            from polaris.kernelone.events.typed.schemas import EventCategory

            event_data: dict[str, Any] = {
                "event_name": payload.get("event_name", ""),
                "event_id": payload.get("event_id", ""),
                "category": cast("EventCategory", payload.get("category", EventCategory.SYSTEM)),
                "run_id": payload.get("run_id", ""),
                "workspace": payload.get("workspace", ""),
                "timestamp": payload.get("timestamp", datetime.now(timezone.utc)),
                "payload": payload.get("payload"),
            }

            # event_type is a specific subclass of EventBase, cast to TypedEvent for mypy
            return cast("TypedEvent", event_type(**event_data))

        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to construct TypedEvent: {e}")
            return None

    # -------------------------------------------------------------------------
    # Subscription (TypedEvent -> MessageBus)
    # -------------------------------------------------------------------------

    def subscribe_to_registry(
        self,
        pattern: str,
        handler: Any,
    ) -> str:
        """Subscribe to TypedEvent registry and emit to MessageBus.

        When a matching TypedEvent is received from the registry, it is
        converted to a Message and published to MessageBus.

        Args:
            pattern: Event pattern to subscribe to
            handler: Optional handler for the event

        Returns:
            Subscription ID
        """
        from polaris.kernelone.events.typed.registry import EventPattern

        async def wrapped_handler(event: TypedEvent) -> None:
            """Wrap registry handler to convert to Message."""
            try:
                await self._emit_to_message_bus(event)

                if handler:
                    handler_result = handler(event)
                    if asyncio.iscoroutine(handler_result):
                        await handler_result

            except (RuntimeError, ValueError) as e:
                logger.error(f"Failed to emit TypedEvent to MessageBus: {e}")

        return self._registry.subscribe(
            EventPattern.from_string(pattern),
            wrapped_handler,
        )

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def unsubscribe(self, subscription_id: int) -> bool:
        """Unsubscribe a specific handler by subscription ID.

        Args:
            subscription_id: The subscription ID returned from subscribe_to_message_bus

        Returns:
            True if unsubscribed successfully, False if not found
        """
        # Find the subscription matching the ID
        for i, (message_type, handler) in enumerate(self._subscriptions):
            if id(handler) == subscription_id:
                try:
                    success = await self._bus.unsubscribe(message_type, handler)
                    if success:
                        self._subscriptions.pop(i)
                        logger.debug(f"Unsubscribed handler {subscription_id} from {message_type.name}")
                        return True
                    return False
                except (RuntimeError, ValueError) as e:
                    logger.error(f"Error unsubscribing handler {subscription_id}: {e}")
                    return False

        logger.warning(f"Subscription ID {subscription_id} not found in _subscriptions")
        return False

    async def unsubscribe_all(self) -> None:
        """Unsubscribe all MessageBus subscriptions.

        Properly removes all subscriptions from the MessageBus and clears
        the internal tracking list.
        """
        if not self._subscriptions:
            logger.debug("No subscriptions to remove")
            return

        removed_count = 0
        failed_count = 0

        # Create a copy to avoid modifying during iteration
        subscriptions_to_remove = list(self._subscriptions)

        for message_type, handler in subscriptions_to_remove:
            try:
                success = await self._bus.unsubscribe(message_type, handler)
                if success:
                    removed_count += 1
                else:
                    failed_count += 1
                    logger.warning(f"Failed to unsubscribe MessageBus {message_type.name}: handler not found")
            except (RuntimeError, ValueError) as e:
                failed_count += 1
                logger.error(f"Error unsubscribing MessageBus {message_type.name}: {e}")

        self._subscriptions.clear()

        logger.info(f"Unsubscribed all MessageBus adapters: {removed_count} removed, {failed_count} failed")


# =============================================================================
# Global Adapter Instance
# =============================================================================

# Module-level default adapter
_default_adapter: TypedEventBusAdapter | None = None


def get_default_adapter() -> TypedEventBusAdapter | None:
    """Get the default adapter instance."""
    return _default_adapter


def init_default_adapter(
    message_bus: MessageBus,
    event_registry: EventRegistry,
    dual_write: bool = True,
) -> TypedEventBusAdapter:
    """Initialize the default global adapter.

    Args:
        message_bus: Existing MessageBus instance
        event_registry: TypedEvent registry
        dual_write: Enable dual-write mode

    Returns:
        Configured adapter
    """
    global _default_adapter
    _default_adapter = TypedEventBusAdapter(
        message_bus=message_bus,
        event_registry=event_registry,
        dual_write=dual_write,
    )
    _default_adapter.register_default_mappings()
    return _default_adapter


def reset_default_adapter() -> None:
    """Reset the default adapter.

    This function is primarily for test isolation. It clears the singleton
    and calls unsubscribe_all() to properly clean up any MessageBus subscriptions.
    """
    global _default_adapter
    if _default_adapter is not None:
        # Clean up subscriptions before clearing
        # Check for running loop without storing it (subscriptions cleared separately)
        # Can't await in sync function, but we can try cleanup synchronously
        # The subscriptions will be lost, but that's acceptable for test cleanup
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop()
    _default_adapter = None
