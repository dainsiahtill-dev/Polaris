"""AuditInterceptor Protocol for typed interceptor implementations.

Design:
- Uses Protocol for structural typing
- runtime_checkable for instance validation
- Provides circuit breaker state and statistics
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import AuditPriority

# Type alias for interceptor functions
AuditInterceptorFn = Protocol


@runtime_checkable
class AuditInterceptor(Protocol):
    """Protocol for audit interceptors.

    Interceptors receive audit events from the bus and can:
    - Log events
    - Transform events
    - Forward to external systems
    - Aggregate metrics

    All interceptors should be:
    - Idempotent (safe to call multiple times)
    - Exception-safe (never propagate exceptions)
    - Fast (avoid blocking the dispatch loop)

    Usage:
        class MyInterceptor:
            name = "my_interceptor"
            priority = AuditPriority.INFO
            _circuit_open = False

            def intercept(self, event: Any) -> None:
                print(f"Received: {event}")

            def open_circuit(self) -> None:
                self._circuit_open = True

            def close_circuit(self) -> None:
                self._circuit_open = False

            def get_stats(self) -> dict[str, Any]:
                return {"processed": self._count}
    """

    @property
    def name(self) -> str:
        """Interceptor name for logging and identification."""

    @property
    def priority(self) -> AuditPriority:
        """Default priority for events from this interceptor.

        Events from this interceptor will be processed at this priority.
        """

    @property
    def circuit_open(self) -> bool:
        """Whether circuit breaker is open.

        When True, the interceptor should stop processing events.
        """

    def intercept(self, event: Any) -> None:
        """Process an audit event.

        Args:
            event: The audit event envelope to process.

        This method should be exception-safe. If it raises, the error
        will be logged but not propagated.
        """

    def open_circuit(self) -> None:
        """Open circuit breaker.

        When called, the interceptor should stop processing events
        and return quickly.
        """

    def close_circuit(self) -> None:
        """Close circuit breaker.

        When called, the interceptor should resume normal operation.
        """

    def get_stats(self) -> dict[str, Any]:
        """Get interceptor statistics.

        Returns:
            Dictionary with interceptor-specific metrics.
        """


class BaseAuditInterceptor:
    """Base class for audit interceptors with common functionality.

    Provides:
    - Name and priority properties
    - Circuit breaker state
    - Basic statistics tracking
    - Exception-safe intercept method

    Subclasses should override intercept() and call super().intercept()
    at the start for automatic stats tracking.

    Usage:
        class LoggingInterceptor(BaseAuditInterceptor):
            def __init__(self) -> None:
                super().__init__(name="logging", priority=AuditPriority.INFO)

            def intercept(self, event: Any) -> None:
                super().intercept(event)
                # Custom processing...
    """

    def __init__(
        self,
        name: str,
        priority: AuditPriority,
    ) -> None:
        """Initialize the interceptor.

        Args:
            name: Interceptor name.
            priority: Default priority for events.
        """
        self._name = name
        self._priority = priority
        self._circuit_open = False
        self._events_processed = 0
        self._events_failed = 0

    @property
    def name(self) -> str:
        """Interceptor name."""
        return self._name

    @property
    def priority(self) -> AuditPriority:
        """Default priority for events."""
        return self._priority

    @property
    def circuit_open(self) -> bool:
        """Whether circuit breaker is open."""
        return self._circuit_open

    def intercept(self, event: Any) -> None:
        """Process an audit event with exception safety.

        Increments stats and catches all exceptions.
        Subclasses should call super().intercept(event) first.
        """
        if self._circuit_open:
            return

        with contextlib.suppress(Exception):
            self._events_processed += 1

    def open_circuit(self) -> None:
        """Open circuit breaker."""
        self._circuit_open = True

    def close_circuit(self) -> None:
        """Close circuit breaker."""
        self._circuit_open = False

    def get_stats(self) -> dict[str, Any]:
        """Get interceptor statistics."""
        return {
            "name": self._name,
            "priority": self._priority.value if hasattr(self._priority, "value") else str(self._priority),
            "circuit_open": self._circuit_open,
            "events_processed": self._events_processed,
            "events_failed": self._events_failed,
        }

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._events_processed = 0
        self._events_failed = 0
