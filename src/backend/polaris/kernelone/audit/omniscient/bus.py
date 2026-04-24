"""OmniscientAuditBus — core event bus with Priority Queue + Back-pressure.

Design:
- Uses asyncio.PriorityQueue for ordered event processing
- Non-blocking emit() using asyncio.create_task
- Background dispatch loop for event distribution
- Built-in storm detection for back-pressure
- Fallback to KernelAuditRuntime for persistence
- Fallback to TypedEventBusAdapter.emit_to_registry() if available

Key methods:
- emit(event, priority) — async, non-blocking
- subscribe(interceptor) — register interceptor
- unsubscribe(interceptor) — unregister
- start() — starts background dispatch task
- stop() — graceful shutdown
- get_default() — singleton pattern with lock
- get_optional() — returns None if not initialized
- track_llm_interaction(tool_name) — context manager for LLM calls
- track_tool_execution(tool_name) — context manager for tool calls
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.context import (
    AuditContext,
    get_current_audit_context,
)
from polaris.kernelone.audit.omniscient.redaction import (
    SensitiveFieldRedactor,
    get_default_redactor,
)
from polaris.kernelone.audit.omniscient.storm_detector import (
    AuditStormDetector,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from polaris.kernelone.audit.omniscient.high_availability import (
        AuditCircuitBreaker,
        MemoryBoundedBatcher,
    )

logger = logging.getLogger(__name__)
"""Audit bus logger with unified prefix: [audit.bus]"""


# =============================================================================
# Priority and Degradation Level Enums
# =============================================================================


class AuditPriority(IntEnum):
    """Priority levels for audit event processing.

    Lower values have higher priority. Events are processed in order
    of increasing priority value (CRITICAL first, DEBUG last).
    """

    CRITICAL = 0
    ERROR = 1
    WARNING = 2
    INFO = 3
    DEBUG = 4


class AuditDegradationLevel(str):
    """Degradation levels for audit system back-pressure.

    Attributes:
        NORMAL: Normal operation, all events processed.
        DEGRADED: Elevated load, some non-essential events dropped.
        CIRCUIT_OPEN: Circuit breaker open, only CRITICAL/ERROR events.
        EMERGENCY: Emergency mode, minimal audit only.
    """

    NORMAL = "normal"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    EMERGENCY = "emergency"


# =============================================================================
# Event Envelope
# =============================================================================


@dataclass(frozen=True, order=False)
class AuditEventEnvelope:
    """Envelope wrapping an audit event with metadata.

    Attributes:
        priority: Processing priority for the event.
        event: The actual event data.
        timestamp: When the event was created.
        correlation_context: AuditContext for correlation.
        metadata: Additional event metadata.
        envelope_id: Unique identifier for this envelope.
    """

    priority: AuditPriority
    event: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_context: AuditContext | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    envelope_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __lt__(self, other: object) -> bool:
        """Compare priorities for PriorityQueue ordering."""
        if not isinstance(other, AuditEventEnvelope):
            return NotImplemented
        # Lower priority value = higher priority
        return self.priority < other.priority


# =============================================================================
# OmniscientAuditBus
# =============================================================================


class OmniscientAuditBus:
    """Core event bus for Omniscient Audit with Priority Queue + Back-pressure.

    Features:
    - Non-blocking emit() with asyncio.create_task
    - Priority-based event processing
    - Built-in storm detection for back-pressure
    - Multiple interceptor subscriptions
    - Graceful shutdown with event draining
    - Singleton pattern for default bus

    Usage:
        # Initialize bus
        bus = OmniscientAuditBus.get_default()
        bus.start()

        # Emit events (non-blocking)
        await bus.emit({"type": "test", "data": "hello"}, priority=AuditPriority.INFO)

        # Subscribe to events
        async def my_handler(envelope):
            print(f"Received: {envelope.event}")

        bus.subscribe(my_handler)

        # Track LLM calls
        async with bus.track_llm_interaction("claude"):
            result = await llm.call()

        # Shutdown
        await bus.stop()
    """

    _instances: dict[str, OmniscientAuditBus] = {}
    _instances_lock = threading.RLock()

    def __init__(
        self,
        name: str = "default",
        max_queue_size: int = 10000,
        storm_detector: AuditStormDetector | None = None,
        redactor: SensitiveFieldRedactor | None = None,
        runtime_root: Path | None = None,
        batcher: MemoryBoundedBatcher | None = None,
        circuit_breaker: AuditCircuitBreaker | None = None,
    ) -> None:
        """Initialize the audit bus.

        Args:
            name: Bus instance name for multi-bus support.
            max_queue_size: Maximum queue size before back-pressure.
            storm_detector: Storm detector instance, or None for default.
            redactor: Sensitive field redactor, or None for default.
            runtime_root: Runtime root path for fallback persistence.
            batcher: Memory-bounded batcher for HA (optional).
            circuit_breaker: Circuit breaker for write fault tolerance (optional).
        """
        self._name = name
        self._max_queue_size = max_queue_size
        self._storm_detector = storm_detector or AuditStormDetector()
        self._redactor = redactor or get_default_redactor()
        self._runtime_root = runtime_root

        # HA components
        self._batcher = batcher
        self._circuit_breaker = circuit_breaker

        # Interceptor registry
        self._interceptors: list[Callable[[AuditEventEnvelope], Awaitable[None] | None]] = []
        self._interceptor_lock = threading.Lock()

        # Priority queue for ordered processing
        self._queue: asyncio.PriorityQueue[AuditEventEnvelope] | None = None

        # Background dispatch task
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Circuit breaker state (mirrors _circuit_breaker if provided)
        self._circuit_open = False
        self._degradation_level = AuditDegradationLevel.NORMAL

        # Statistics
        self._events_emitted = 0
        self._events_processed = 0
        self._events_dropped = 0
        self._events_lost = 0  # [P1-AUDIT-003] Track events lost due to fallback failures
        self._emitter_lock = threading.Lock()

        # Runtime references (lazy loaded, guarded by _fallback_lock)
        self._kernel_audit_runtime: Any = None
        self._typed_event_adapter: Any = None
        self._fallback_lock: asyncio.Lock | None = None

    # =========================================================================
    # Singleton Pattern
    # =========================================================================

    @classmethod
    def get_default(cls) -> OmniscientAuditBus:
        """Get the default singleton bus instance.

        Returns:
            The default OmniscientAuditBus instance.
        """
        return cls.get_instance("default")

    @classmethod
    def get_instance(cls, name: str = "default") -> OmniscientAuditBus:
        """Get or create a named bus instance.

        Args:
            name: Bus instance name.

        Returns:
            Named OmniscientAuditBus instance.
        """
        with cls._instances_lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name=name)
            return cls._instances[name]

    @classmethod
    def get_optional(cls) -> OmniscientAuditBus | None:
        """Get optional bus instance (for decorator fallthrough).

        Unlike get_default(), this returns None if the bus hasn't been
        explicitly accessed, allowing decorators to skip audit when
        no bus is configured.

        Returns:
            Default bus instance if accessed, None otherwise.
        """
        with cls._instances_lock:
            return cls._instances.get("default")

    @classmethod
    def reset_default(cls) -> None:
        """Reset the default singleton (for testing)."""
        with cls._instances_lock:
            default = cls._instances.pop("default", None)
            if default is not None and default._dispatch_task and not default._dispatch_task.done():
                # Cancel any running dispatch task
                default._dispatch_task.cancel()
            # Clear all instances
            cls._instances.clear()

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the background dispatch loop.

        Creates the asyncio queue and starts the dispatch task.
        Idempotent — safe to call multiple times.
        """
        if self._running:
            return

        self._queue = asyncio.PriorityQueue(maxsize=self._max_queue_size)
        self._running = True
        self._shutdown_event.clear()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.debug("[audit.bus] Started dispatch loop for %s", self._name)

    async def stop(self, timeout: float = 5.0) -> None:
        """Gracefully shutdown the bus.

        Stops accepting new events and drains the queue before exit.

        Args:
            timeout: Maximum seconds to wait for queue drain.
        """
        if not self._running:
            return

        logger.debug("[audit.bus] Stopping %s", self._name)
        self._running = False
        self._shutdown_event.set()

        # Cancel dispatch task
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._dispatch_task, timeout=timeout)

        self._dispatch_task = None

        # Drain remaining queue items
        # Use try/except pattern to avoid race with empty() check
        if self._queue is not None:
            queue = self._queue
            self._queue = None  # Prevent new emissions during drain
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break  # Queue is empty, drain complete

        logger.debug("[audit.bus] Stopped %s", self._name)

    # =========================================================================
    # Event Emission (Non-blocking)
    # =========================================================================

    async def emit(
        self,
        event: Any,
        priority: AuditPriority = AuditPriority.INFO,
        correlation_context: AuditContext | None = None,
        **metadata: Any,
    ) -> str:
        """Emit an audit event (non-blocking).

        Creates an envelope and queues it for async processing.
        Returns immediately — actual dispatch happens in background.

        Args:
            event: The event data to audit.
            priority: Processing priority.
            correlation_context: Optional audit context override.
            **metadata: Additional metadata for the envelope.

        Returns:
            Envelope ID for tracking.
        """
        # Get current audit context if not provided
        if correlation_context is None:
            correlation_context = get_current_audit_context()

        # Create envelope
        envelope = AuditEventEnvelope(
            priority=priority,
            event=event,
            correlation_context=correlation_context,
            metadata=dict(metadata),
        )

        # Check storm detector for back-pressure
        if self._storm_detector.should_drop():
            self._events_dropped += 1
            logger.debug("[audit.bus] Dropped event due to storm: %s", envelope.envelope_id)
            return envelope.envelope_id

        # Should we skip the body?
        should_skip_body = self._storm_detector.should_skip_body()

        # Redact sensitive fields
        redacted_event = self._redactor.redact(event)

        # Update envelope with redacted event if needed
        if should_skip_body and redacted_event != event:
            envelope = AuditEventEnvelope(
                priority=envelope.priority,
                event=redacted_event,
                timestamp=envelope.timestamp,
                correlation_context=envelope.correlation_context,
                metadata=dict(envelope.metadata),
                envelope_id=envelope.envelope_id,
            )

        # Record event in storm detector
        event_type = str(event.get("type", "unknown") if isinstance(event, dict) else type(event).__name__)
        self._storm_detector.record_event(event_type)

        # Increment counter
        with self._emitter_lock:
            self._events_emitted += 1

        # Queue for async dispatch (fire-and-forget)
        # Capture queue reference to avoid race with stop()
        queue = self._queue
        if queue is not None:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                self._events_dropped += 1
                logger.warning("[audit.bus] Queue full, dropped event: %s", envelope.envelope_id)

        return envelope.envelope_id

    def emit_sync(
        self,
        event: Any,
        priority: AuditPriority = AuditPriority.INFO,
        **metadata: Any,
    ) -> str:
        """Synchronous emit wrapper for sync code paths.

        Creates a task to handle the async emit.

        Args:
            event: The event data to audit.
            priority: Processing priority.
            **metadata: Additional metadata.

        Returns:
            Envelope ID for tracking (empty if no running loop).
        """
        try:
            loop = asyncio.get_running_loop()
            # Schedule async emit as background task (fire-and-forget)
            # We cannot await the result here without blocking
            _ = loop.create_task(self.emit(event, priority, **metadata))  # noqa: RUF006
            # Return empty string since we can't await the result
            # The envelope_id is not available in sync context
            return ""
        except RuntimeError:
            # No running loop, silently skip (audit loss acceptable in sync context)
            return ""

    # =========================================================================
    # Interceptor Management
    # =========================================================================

    def subscribe(
        self,
        interceptor: Callable[[AuditEventEnvelope], Awaitable[None] | None],
        name: str | None = None,
    ) -> str:
        """Subscribe an interceptor to audit events.

        Args:
            interceptor: Async callable to handle events.
            name: Optional name for the interceptor.

        Returns:
            Interceptor ID for unsubscribe.
        """
        with self._interceptor_lock:
            interceptor_id = name or f"interceptor_{len(self._interceptors)}"
            self._interceptors.append(interceptor)
            logger.debug("[audit.bus] Subscribed interceptor: %s", interceptor_id)
            return interceptor_id

    def unsubscribe(
        self,
        interceptor: Callable[[AuditEventEnvelope], Awaitable[None] | None],
    ) -> bool:
        """Unsubscribe an interceptor from audit events.

        Args:
            interceptor: The interceptor to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._interceptor_lock:
            try:
                self._interceptors.remove(interceptor)
                logger.debug("[audit.bus] Unsubscribed interceptor: %s", interceptor)
                return True
            except ValueError:
                return False

    def unsubscribe_by_name(self, name: str) -> bool:
        """Unsubscribe an interceptor by name.

        Args:
            name: The interceptor name to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._interceptor_lock:
            for i, interceptor in enumerate(self._interceptors):
                if getattr(interceptor, "__name__", "") == name:
                    self._interceptors.pop(i)
                    logger.debug("[audit.bus] Unsubscribed interceptor by name: %s", name)
                    return True
            return False

    # =========================================================================
    # Context Managers for Tracking
    # =========================================================================

    @asynccontextmanager
    async def track_llm_interaction(
        self,
        tool_name: str,
        priority: AuditPriority = AuditPriority.INFO,
    ):
        """Context manager for tracking LLM interactions.

        Automatically emits start/complete/error events.

        Args:
            tool_name: Name of the LLM/tool.
            priority: Event priority.

        Yields:
            None

        Usage:
            async with bus.track_llm_interaction("claude"):
                result = await llm.call()
        """
        start_time = datetime.now(timezone.utc)

        try:
            # Emit start event
            await self.emit(
                {
                    "type": "llm_interaction_start",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                },
                priority=priority,
            )
            yield
            # Emit complete event
            end_time = datetime.now(timezone.utc)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            await self.emit(
                {
                    "type": "llm_interaction_complete",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_ms": duration_ms,
                },
                priority=priority,
            )
        except (RuntimeError, ValueError) as exc:
            # Emit error event
            end_time = datetime.now(timezone.utc)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            await self.emit(
                {
                    "type": "llm_interaction_error",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_ms": duration_ms,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                priority=AuditPriority.ERROR,
            )
            raise

    @asynccontextmanager
    async def track_tool_execution(
        self,
        tool_name: str,
        priority: AuditPriority = AuditPriority.INFO,
    ):
        """Context manager for tracking tool executions.

        Automatically emits start/complete/error events.

        Args:
            tool_name: Name of the tool.
            priority: Event priority.

        Yields:
            None

        Usage:
            async with bus.track_tool_execution("read_file"):
                result = read_file("/path/to/file")
        """
        start_time = datetime.now(timezone.utc)

        try:
            # Emit start event
            await self.emit(
                {
                    "type": "tool_execution_start",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                },
                priority=priority,
            )
            yield
            # Emit complete event
            end_time = datetime.now(timezone.utc)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            await self.emit(
                {
                    "type": "tool_execution_complete",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_ms": duration_ms,
                },
                priority=priority,
            )
        except (RuntimeError, ValueError) as exc:
            # Emit error event
            end_time = datetime.now(timezone.utc)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            await self.emit(
                {
                    "type": "tool_execution_error",
                    "tool_name": tool_name,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_ms": duration_ms,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                priority=AuditPriority.ERROR,
            )
            raise

    # =========================================================================
    # Circuit Breaker
    # =========================================================================

    def open_circuit(self) -> None:
        """Open the circuit breaker — drop all non-CRITICAL/ERROR events."""
        self._circuit_open = True
        self._degradation_level = AuditDegradationLevel.CIRCUIT_OPEN
        logger.warning("[audit.bus] Circuit breaker opened for %s", self._name)

    def close_circuit(self) -> None:
        """Close the circuit breaker — resume normal operation."""
        self._circuit_open = False
        self._degradation_level = AuditDegradationLevel.NORMAL
        logger.info("[audit.bus] Circuit breaker closed for %s", self._name)

    @property
    def circuit_open(self) -> bool:
        """Check if circuit breaker is open."""
        return self._circuit_open

    @property
    def degradation_level(self) -> str:
        """Get current degradation level."""
        return self._degradation_level

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get bus statistics.

        [P1-AUDIT-003] Includes events_lost counter for fallback failure monitoring.

        Returns:
            Dictionary with event counts, queue size, storm level, etc.
        """
        storm_stats = self._storm_detector.get_stats()

        stats = {
            "name": self._name,
            "running": self._running,
            "circuit_open": self._circuit_open,
            "degradation_level": str(self._degradation_level),
            "events_emitted": self._events_emitted,
            "events_processed": self._events_processed,
            "events_dropped": self._events_dropped,
            "events_lost": self._events_lost,  # [P1-AUDIT-003]
            "queue_size": self._queue.qsize() if self._queue else 0,
            "max_queue_size": self._max_queue_size,
            "interceptor_count": len(self._interceptors),
            "storm": storm_stats,
        }

        # Add HA component stats
        if self._batcher:
            stats["batcher"] = self._batcher.get_stats()
        if self._circuit_breaker:
            stats["circuit_breaker"] = self._circuit_breaker.get_stats()

        return stats

    def get_storm_level(self) -> str:
        """Get current storm detection level.

        Returns:
            Storm level string: normal, elevated, warning, critical, emergency.
        """
        return self._storm_detector.get_level().value

    # =========================================================================
    # Internal Dispatch Loop
    # =========================================================================

    async def _dispatch_loop(self) -> None:
        """Background loop for processing events from the queue.

        Runs as a background task, processing events in priority order.
        """
        # Exit only when both conditions met: not running AND shutdown is set
        while True:
            # Check shutdown condition first
            if not self._running and self._shutdown_event.is_set():
                break

            # Capture queue reference to avoid race with stop()
            queue = self._queue
            if queue is None:
                # Queue not initialized or stopped, sleep briefly
                await asyncio.sleep(0.1)
                continue

            try:
                # Wait for event with timeout for graceful shutdown
                envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Timeout is normal, check shutdown condition in next iteration
                continue
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as exc:
                logger.error("[audit.bus] Error getting from queue: %s", exc)
                continue

            # Process event
            if envelope is not None:
                await self._dispatch_event(envelope)

    async def _dispatch_event(self, envelope: AuditEventEnvelope) -> None:
        """Dispatch a single event to all interceptors.

        Args:
            envelope: The event envelope to dispatch.
        """
        # Sync circuit breaker state from HA component if provided
        if self._circuit_breaker:
            cb_state = self._circuit_breaker.state
            self._circuit_open = cb_state == "open"
            if self._circuit_open:
                self._degradation_level = AuditDegradationLevel.CIRCUIT_OPEN
            elif self._degradation_level == AuditDegradationLevel.CIRCUIT_OPEN:
                self._degradation_level = AuditDegradationLevel.NORMAL

        # Check circuit breaker
        if self._circuit_open and envelope.priority > AuditPriority.ERROR:
            # Drop non-CRITICAL/ERROR events when circuit is open
            self._events_dropped += 1
            return  # Do NOT persist dropped events

        # Get interceptors snapshot
        with self._interceptor_lock:
            interceptors = list(self._interceptors)

        # Dispatch to all interceptors and track tasks
        tasks: list[asyncio.Task[None]] = []
        for interceptor in interceptors:
            try:
                result = interceptor(envelope)
                if asyncio.iscoroutine(result):
                    tasks.append(asyncio.create_task(result))
            except (RuntimeError, ValueError) as exc:
                logger.error("[audit.bus] Interceptor error: %s", exc)

        # Wait for all interceptors to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Update processed counter
        with self._emitter_lock:
            self._events_processed += 1

        # Try fallback persistence
        await self._fallback_persist(envelope)

    async def _fallback_persist(self, envelope: AuditEventEnvelope) -> None:
        """Attempt to persist event to KernelAuditRuntime or TypedEventBusAdapter.

        Args:
            envelope: The event envelope to persist.
        """
        # Use a lock to safely initialize adapters (Bug #4 fix)
        if self._fallback_lock is None:
            self._fallback_lock = asyncio.Lock()

        async with self._fallback_lock:
            await self._fallback_persist_unlocked(envelope)

    async def _fallback_persist_unlocked(self, envelope: AuditEventEnvelope) -> None:
        """Internal fallback persist (must be called with _fallback_lock held).

        Args:
            envelope: The event envelope to persist.
        """
        # Try KernelAuditRuntime
        try:
            if self._kernel_audit_runtime is None and self._runtime_root:
                from polaris.kernelone.audit.runtime import KernelAuditRuntime

                self._kernel_audit_runtime = KernelAuditRuntime.get_instance(self._runtime_root)

            if self._kernel_audit_runtime is not None:
                # Convert envelope to KernelAuditEvent
                event = envelope.event
                if isinstance(event, dict):
                    # Extract trace_id from UnifiedAuditContext if available
                    trace_id = ""
                    if envelope.correlation_context is not None:
                        ctx = envelope.correlation_context
                        # Only UnifiedAuditContext has trace_id; AuditContext does not
                        if hasattr(ctx, "trace_id"):
                            trace_id = str(ctx.trace_id or "")

                    self._kernel_audit_runtime.emit_event(
                        event_type=event.get("type", "unknown"),
                        role=str(
                            envelope.correlation_context.user_id or "system"
                            if envelope.correlation_context
                            else "system"
                        ),
                        workspace=str(
                            envelope.correlation_context.workspace or "" if envelope.correlation_context else ""
                        ),
                        task_id=str(envelope.correlation_context.task_id or "" if envelope.correlation_context else ""),
                        run_id=str(envelope.correlation_context.run_id or "" if envelope.correlation_context else ""),
                        trace_id=trace_id,
                        data=event,
                    )
        except (RuntimeError, ValueError) as exc:
            # [P1-AUDIT-003] Fallback failure should use warning level and track lost events
            logger.warning(
                "[audit.bus] KernelAuditRuntime fallback failed: %s (lost_event_id=%s)",
                exc,
                envelope.envelope_id,
            )
            # Track lost events for monitoring
            self._track_lost_event(envelope)

        # Try TypedEventBusAdapter
        try:
            if self._typed_event_adapter is None:
                from polaris.kernelone.events.typed import get_default_adapter

                self._typed_event_adapter = get_default_adapter()

            if self._typed_event_adapter is not None:
                # Emit to registry — convert dict to TypedEvent to avoid type errors (Bug #1 fix)
                event = envelope.event
                if isinstance(event, dict) and "event_name" in event:
                    try:
                        from polaris.kernelone.events.typed import TypedEvent

                        typed_event = TypedEvent.model_validate(event)  # type: ignore[attr-defined]
                        await self._typed_event_adapter.emit_to_registry(typed_event)
                    except (RuntimeError, ValueError) as exc:
                        # [P1-AUDIT-003] TypedEvent failure should use warning level
                        logger.warning(
                            "[audit.bus] TypedEvent conversion/emit failed: %s (event_id=%s)",
                            exc,
                            envelope.envelope_id,
                        )
                        self._track_lost_event(envelope)
        except (RuntimeError, ValueError) as exc:
            # [P1-AUDIT-003] TypedEventBusAdapter failure should use warning level
            logger.warning(
                "[audit.bus] TypedEventBusAdapter fallback failed: %s (lost_event_id=%s)",
                exc,
                envelope.envelope_id,
            )
            self._track_lost_event(envelope)

    def _track_lost_event(self, envelope: AuditEventEnvelope) -> None:
        """Track events lost due to fallback failures.

        [P1-AUDIT-003] Increment lost event counter for monitoring.

        Args:
            envelope: The event envelope that was lost.
        """
        with self._emitter_lock:
            self._events_lost += 1

        # Also log at debug level for forensic purposes
        logger.debug(
            "[audit.bus] Lost event tracked: id=%s, priority=%s, event_type=%s",
            envelope.envelope_id,
            envelope.priority,
            type(envelope.event).__name__,
        )


# =============================================================================
# Aliases for backward compatibility
# =============================================================================

# Aliases for the context manager methods
# These provide a cleaner API for tracking operations
LLMInteractionTracker = "track_llm_interaction"  # Use: bus.track_llm_interaction()
ToolExecutionTracker = "track_tool_execution"  # Use: bus.track_tool_execution()
