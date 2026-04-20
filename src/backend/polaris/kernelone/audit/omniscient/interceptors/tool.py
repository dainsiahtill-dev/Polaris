"""Tool Audit Interceptor for tracking tool executions.

This interceptor subscribes to the OmniscientAuditBus and processes
tool execution events, capturing execution times, errors, and implementing
circuit breaker functionality.

Design:
- Subscribes to tool execution events from the bus
- Extracts tool name, arguments, result, duration, error info
- Implements circuit breaker (opens on repeated failures)
- Aggregates tool execution metrics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class ToolAuditInterceptor(BaseAuditInterceptor):
    """Interceptor for auditing tool executions.

    Captures:
    - Tool name and arguments
    - Execution duration
    - Success/failure status
    - Error details
    - Circuit breaker state

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = ToolAuditInterceptor(bus)
        # Events will be automatically processed
    """

    # Write tools that should be tracked specially
    WRITE_TOOLS: frozenset[str] = frozenset(
        {
            "write_file",
            "create_file",
            "edit_file",
            "delete_file",
            "repo_write",
            "file_write",
        }
    )

    def __init__(
        self,
        bus: OmniscientAuditBus,
        failure_threshold: int = 5,
        window_seconds: int = 60,
        recovery_timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the tool audit interceptor.

        Args:
            bus: The audit bus to subscribe to.
            failure_threshold: Number of consecutive failures to open circuit.
            window_seconds: Time window for failure tracking.
            recovery_timeout: Seconds to wait before attempting circuit recovery.
        """
        super().__init__(name="tool_audit", priority=AuditPriority.INFO)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Metrics tracking
        self._tool_counts: dict[str, int] = {}
        self._tool_errors: dict[str, int] = {}
        self._tool_latencies: dict[str, list[float]] = {}
        self._write_operation_count = 0
        self._read_operation_count = 0

        # Circuit breaker state
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        self._recovery_timeout = recovery_timeout
        self._consecutive_failures = 0
        self._total_failures = 0
        self._circuit_opened_at: float | None = None

        # Track tool-specific circuit breakers
        self._tool_circuits: dict[str, tuple[int, float]] = {}  # tool_name -> (failures, last_failure_time)

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process a tool audit event.

        Args:
            event: The audit event (AuditEventEnvelope or dict).
        """
        # Call base implementation first for stats tracking
        super().intercept(event)

        # Extract event data
        if isinstance(event, AuditEventEnvelope):
            event_data = event.event
        elif isinstance(event, dict):
            event_data = event
        else:
            return

        # Process tool execution events
        if isinstance(event_data, dict):
            event_type = event_data.get("type", "")
            if event_type in (
                "tool_execution",
                "tool_execution_start",
                "tool_execution_complete",
                "tool_execution_error",
            ):
                self._process_tool_event(event_data)

    def _process_tool_event(self, event: dict[str, Any]) -> None:
        """Process a single tool execution event.

        Args:
            event: The tool execution event dict.
        """

        event_type = event.get("type", "")
        tool_name = event.get("tool_name", "unknown")
        duration_ms = event.get("duration_ms", 0.0)
        error = event.get("error")

        # Update tool counts
        self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1

        # Track write vs read operations
        if tool_name in self.WRITE_TOOLS:
            self._write_operation_count += 1
        else:
            self._read_operation_count += 1

        # Track latency per tool
        if tool_name not in self._tool_latencies:
            self._tool_latencies[tool_name] = []
        self._tool_latencies[tool_name].append(duration_ms)

        # Keep only recent latencies (last 100 per tool)
        if len(self._tool_latencies[tool_name]) > 100:
            self._tool_latencies[tool_name] = self._tool_latencies[tool_name][-100:]

        # Track errors
        if error or event_type == "tool_execution_error":
            self._tool_errors[tool_name] = self._tool_errors.get(tool_name, 0) + 1
            self._total_failures += 1
            self._consecutive_failures += 1
            self._update_tool_circuit(tool_name)
            self._check_global_failure_threshold()
        else:
            self._consecutive_failures = 0
            self._close_tool_circuit(tool_name)

        logger.debug(
            "[tool_audit] Processed tool event: tool=%s, type=%s, duration=%.2fms, error=%s",
            tool_name,
            event_type,
            duration_ms,
            error,
        )

    def _update_tool_circuit(self, tool_name: str) -> None:
        """Update circuit breaker state for a specific tool.

        Args:
            tool_name: The tool name to update.
        """
        import time

        current_time = time.time()
        failures, last_time = self._tool_circuits.get(tool_name, (0, 0.0))

        # Reset if outside window
        if current_time - last_time > self._window_seconds:
            failures = 0

        failures += 1
        self._tool_circuits[tool_name] = (failures, current_time)

        # Open circuit if threshold exceeded
        if failures >= self._failure_threshold and not self._circuit_open:
            self._circuit_opened_at = current_time
            self.open_circuit()
            logger.warning(
                "[tool_audit] Circuit breaker opened for tool %s after %d failures",
                tool_name,
                failures,
            )

    def _close_tool_circuit(self, tool_name: str) -> None:
        """Reset circuit breaker state for a tool on success.

        Args:
            tool_name: The tool name to reset.
        """
        if tool_name in self._tool_circuits:
            del self._tool_circuits[tool_name]

        # Check if we should close the global circuit
        if self._circuit_open and self._should_attempt_recovery():
            self.close_circuit()
            self._circuit_opened_at = None
            logger.info("[tool_audit] Circuit breaker closed after recovery")

    def _check_global_failure_threshold(self) -> None:
        """Check if global failure threshold is exceeded."""
        import time

        if self._consecutive_failures >= self._failure_threshold and not self._circuit_open:
            self._circuit_opened_at = time.time()
            self.open_circuit()
            logger.warning(
                "[tool_audit] Global circuit breaker opened after %d consecutive failures",
                self._consecutive_failures,
            )

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt circuit recovery.

        Returns:
            True if recovery should be attempted.
        """
        import time

        if self._circuit_opened_at is None:
            return True
        return (time.time() - self._circuit_opened_at) >= self._recovery_timeout

    def get_stats(self) -> dict[str, Any]:
        """Get tool audit statistics.

        Returns:
            Dictionary with tool-specific metrics.
        """
        base_stats = super().get_stats()

        # Calculate average latencies per tool
        avg_latencies = {tool: sum(lats) / len(lats) if lats else 0.0 for tool, lats in self._tool_latencies.items()}

        return {
            **base_stats,
            "tool_counts": dict(self._tool_counts),
            "tool_errors": dict(self._tool_errors),
            "tool_latencies_avg": avg_latencies,
            "write_operation_count": self._write_operation_count,
            "read_operation_count": self._read_operation_count,
            "total_failures": self._total_failures,
            "consecutive_failures": self._consecutive_failures,
            "tool_circuits_open": len(self._tool_circuits),
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        super().reset_stats()
        self._tool_counts.clear()
        self._tool_errors.clear()
        self._tool_latencies.clear()
        self._write_operation_count = 0
        self._read_operation_count = 0
        self._consecutive_failures = 0
        self._total_failures = 0
        self._tool_circuits.clear()
        self._circuit_opened_at = None
