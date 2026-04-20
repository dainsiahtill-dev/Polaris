"""Context Management Audit Interceptor for tracking context operations.

This interceptor subscribes to the OmniscientAuditBus and processes
context management events, tracking window occupancy, compaction,
and memory pressure indicators.

Design:
- Subscribes to context window status events
- Subscribes to compaction events
- Tracks occupancy over time
- Detects memory pressure
- Aggregates context management metrics
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.bus import AuditEventEnvelope, AuditPriority
from polaris.kernelone.audit.omniscient.interceptors.base import BaseAuditInterceptor

if TYPE_CHECKING:
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

logger = logging.getLogger(__name__)


class ContextAuditInterceptor(BaseAuditInterceptor):
    """Interceptor for auditing context management.

    Captures:
    - Context window occupancy
    - Compaction events
    - Token usage patterns
    - Memory pressure indicators
    - OOM intercepts

    Usage:
        bus = OmniscientAuditBus.get_default()
        await bus.start()
        interceptor = ContextAuditInterceptor(bus)
        # Events will be automatically processed
    """

    def __init__(
        self,
        bus: OmniscientAuditBus,
        critical_threshold_pct: float = 80.0,
    ) -> None:
        """Initialize the context management interceptor.

        Args:
            bus: The audit bus to subscribe to.
            critical_threshold_pct: Percentage threshold for critical warnings.
        """
        super().__init__(name="context_audit", priority=AuditPriority.INFO)
        self._bus = bus
        self._bus.subscribe(self._handle_envelope)

        # Window tracking
        self._current_tokens = 0
        self._max_tokens = 0
        self._current_occupancy_pct = 0.0
        self._peak_occupancy_pct = 0.0

        # Operation counts
        self._compaction_count = 0
        self._eviction_count = 0
        self._load_count = 0
        self._render_count = 0
        self._llm_call_triggers = 0
        self._oom_intercepts = 0

        # Token history (for pattern detection)
        self._token_history: list[tuple[float, int]] = []
        self._max_history_size = 100

        # Critical events
        self._critical_occupancy_events: list[dict[str, Any]] = []
        self._compaction_events: list[dict[str, Any]] = []

        # Segment breakdown
        self._current_segment_breakdown: dict[str, int] = {}

        # Templates used
        self._template_usage: dict[str, int] = {}

    def _handle_envelope(self, envelope: AuditEventEnvelope) -> None:
        """Handle incoming audit event envelope.

        Args:
            envelope: The audit event envelope.
        """
        self.intercept(envelope)

    def intercept(self, event: Any) -> None:
        """Process a context audit event.

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

        # Process context management events
        if isinstance(event_data, dict):
            event_type = event_data.get("type", "")
            if event_type in (
                "context_window_status",
                "context_management",
                "compact_requested",
                "context_render",
                "context_compact",
                "context_evict",
                "context_load",
                "context_save",
            ):
                self._process_context_event(event_data)

    def _process_context_event(self, event: dict[str, Any]) -> None:
        """Process a single context management event.

        Args:
            event: The context management event dict.
        """
        import time

        event_type = event.get("type", "")
        current_time = time.time()

        # Update window status
        if "current_tokens" in event:
            self._update_window_status(event)

        # Process specific event types
        if event_type == "context_window_status":
            self._handle_window_status(event, current_time)
        elif event_type == "context_management":
            self._handle_context_management(event, current_time)
        elif event_type == "compact_requested":
            self._handle_compact_requested(event, current_time)

        # Track critical occupancy
        if self._current_occupancy_pct >= 80.0:
            self._critical_occupancy_events.append(
                {
                    "timestamp": current_time,
                    "occupancy_pct": self._current_occupancy_pct,
                    "tokens": self._current_tokens,
                }
            )

        logger.debug(
            "[context_audit] Processed context event: type=%s, tokens=%d, occupancy=%.1f%%",
            event_type,
            self._current_tokens,
            self._current_occupancy_pct,
        )

    def _update_window_status(self, event: dict[str, Any]) -> None:
        """Update current window status from event.

        Args:
            event: The event with window status.
        """
        import time

        self._current_tokens = event.get("current_tokens", self._current_tokens)
        self._max_tokens = event.get("max_tokens", self._max_tokens)

        # Calculate occupancy
        if self._max_tokens > 0:
            self._current_occupancy_pct = (self._current_tokens / self._max_tokens) * 100.0
        else:
            self._current_occupancy_pct = 0.0

        # Track peak
        self._peak_occupancy_pct = max(self._peak_occupancy_pct, self._current_occupancy_pct)

        # Update token history
        self._token_history.append((time.time(), self._current_tokens))
        if len(self._token_history) > self._max_history_size:
            self._token_history = self._token_history[-self._max_history_size :]

        # Update segment breakdown
        segment_breakdown = event.get("segment_breakdown", {})
        if segment_breakdown:
            self._current_segment_breakdown = segment_breakdown

    def _handle_window_status(self, event: dict[str, Any], timestamp: float) -> None:
        """Handle context window status event.

        Args:
            event: The window status event.
            timestamp: Event timestamp.
        """
        remaining_tokens = event.get("remaining_tokens", 0)
        usage_percentage = event.get("usage_percentage", self._current_occupancy_pct)
        is_critical = event.get("is_critical", usage_percentage >= 80.0)
        is_exhausted = event.get("is_exhausted", False)

        if is_exhausted:
            logger.warning(
                "[context_audit] Context exhausted: tokens=%d, remaining=%d",
                self._current_tokens,
                remaining_tokens,
            )

        if is_critical and not is_exhausted:
            logger.warning(
                "[context_audit] Critical occupancy: %.1f%% (tokens=%d/%d)",
                usage_percentage,
                self._current_tokens,
                self._max_tokens,
            )

    def _handle_context_management(self, event: dict[str, Any], timestamp: float) -> None:
        """Handle context management event.

        Args:
            event: The context management event.
            timestamp: Event timestamp.
        """
        operation = event.get("operation", "unknown")
        template_name = event.get("template_name")
        evicted_entries = event.get("evicted_entries", 0)
        loaded_entries = event.get("loaded_entries", 0)
        llm_call_triggered = event.get("llm_call_triggered", False)
        oom_intercepted = event.get("oom_intercepted", False)
        occupancy_before = event.get("window_occupancy_before_pct", 0.0)
        occupancy_after = event.get("window_occupancy_after_pct", 0.0)

        # Update operation counts
        if operation == "compact":
            self._compaction_count += 1
            self._compaction_events.append(
                {
                    "timestamp": timestamp,
                    "template": template_name,
                    "occupancy_before": occupancy_before,
                    "occupancy_after": occupancy_after,
                }
            )
        elif operation == "evict":
            self._eviction_count += 1
        elif operation == "load":
            self._load_count += 1
        elif operation == "render":
            self._render_count += 1

        if evicted_entries > 0:
            self._eviction_count += evicted_entries

        if loaded_entries > 0:
            self._load_count += loaded_entries

        if llm_call_triggered:
            self._llm_call_triggers += 1

        if oom_intercepted:
            self._oom_intercepts += 1
            logger.error("[context_audit] OOM intercepted during %s", operation)

        # Track template usage
        if template_name:
            self._template_usage[template_name] = self._template_usage.get(template_name, 0) + 1

        # Keep only recent events
        if len(self._compaction_events) > 50:
            self._compaction_events = self._compaction_events[-50:]

    def _handle_compact_requested(self, event: dict[str, Any], timestamp: float) -> None:
        """Handle compaction requested event.

        Args:
            event: The compaction requested event.
            timestamp: Event timestamp.
        """
        reason = event.get("reason", "")
        current_tokens = event.get("current_tokens", 0)
        threshold = event.get("threshold", 0)

        logger.info(
            "[context_audit] Compaction requested: reason=%s, tokens=%d, threshold=%d",
            reason,
            current_tokens,
            threshold,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get context management audit statistics.

        Returns:
            Dictionary with context management metrics.
        """
        base_stats = super().get_stats()

        return {
            **base_stats,
            "current_tokens": self._current_tokens,
            "max_tokens": self._max_tokens,
            "current_occupancy_pct": round(self._current_occupancy_pct, 2),
            "peak_occupancy_pct": round(self._peak_occupancy_pct, 2),
            "compaction_count": self._compaction_count,
            "eviction_count": self._eviction_count,
            "load_count": self._load_count,
            "render_count": self._render_count,
            "llm_call_triggers": self._llm_call_triggers,
            "oom_intercepts": self._oom_intercepts,
            "critical_occupancy_events_count": len(self._critical_occupancy_events),
            "compaction_events_recent": list(self._compaction_events[-10:]),
            "current_segment_breakdown": dict(self._current_segment_breakdown),
            "template_usage": dict(self._template_usage),
            "token_history_size": len(self._token_history),
        }

    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        super().reset_stats()
        self._current_tokens = 0
        self._max_tokens = 0
        self._current_occupancy_pct = 0.0
        self._peak_occupancy_pct = 0.0
        self._compaction_count = 0
        self._eviction_count = 0
        self._load_count = 0
        self._render_count = 0
        self._llm_call_triggers = 0
        self._oom_intercepts = 0
        self._critical_occupancy_events.clear()
        self._compaction_events.clear()
        self._current_segment_breakdown.clear()
        self._template_usage.clear()
        self._token_history.clear()
