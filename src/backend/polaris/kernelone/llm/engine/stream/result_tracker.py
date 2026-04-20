"""Polaris AI Platform - Stream Result Tracker

Tracks streaming progress with enhanced timeout management.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..contracts import AIStreamEvent, StreamEventType
from .config import LLMStreamResult, StreamConfig, StreamState, get_default_stream_config

logger = logging.getLogger(__name__)


class _StreamResultTracker:
    """Tracks streaming progress with enhanced timeout management.

    Monitors both token-level timeouts (no new token received) and
    overall stream timeouts.

    Uses StreamConfig for default timeout values (H-04 Fix).
    """

    def __init__(
        self,
        trace_id: str,
        token_timeout: float | None = None,
        stream_timeout: float | None = None,
        config: StreamConfig | None = None,
    ) -> None:
        """Initialize the stream result tracker.

        Args:
            trace_id: Trace ID for this stream.
            token_timeout: Maximum seconds without receiving a new token. Defaults to config value.
            stream_timeout: Maximum total duration for the entire stream. Defaults to config value.
            config: Stream configuration for default values.
        """
        cfg = config or get_default_stream_config()
        self.trace_id = trace_id
        self.token_timeout = token_timeout if token_timeout is not None else cfg.token_timeout_sec
        self.stream_timeout = stream_timeout if stream_timeout is not None else cfg.timeout_sec

        # State tracking
        self.state = StreamState.IDLE
        self.last_token_time: float | None = None
        self.stream_start_time: float | None = None

        # Event tracking
        self.events: list[AIStreamEvent] = []
        self.chunk_count = 0
        self.tool_calls_count = 0

        # Validation state
        self._has_received_content = False
        self._has_completed_cleanly = False
        self._has_error = False
        self._validation_errors: list[str] = []

    def start(self) -> None:
        """Mark the start of streaming."""
        self.stream_start_time = time.time()
        self.last_token_time = self.stream_start_time

    def record_event(self, event: AIStreamEvent) -> None:
        """Record an event and update state machine.

        Args:
            event: The stream event to record.
        """
        self.last_token_time = time.time()
        self.events.append(event)

        # Update state machine
        self._update_state(event)

        # Track event counts
        if event.type == StreamEventType.CHUNK and event.chunk:
            self.chunk_count += 1
            self._has_received_content = True
        elif event.type == StreamEventType.REASONING_CHUNK and event.reasoning:
            self._update_state_for_reasoning()
        elif event.type == StreamEventType.TOOL_CALL and event.tool_call:
            self.tool_calls_count += 1
        elif event.type == StreamEventType.ERROR:
            self._has_error = True
            self.state = StreamState.ERROR
            if event.error:
                self._validation_errors.append(f"Stream error: {event.error}")
        elif event.type == StreamEventType.COMPLETE:
            self._has_completed_cleanly = True
            self.state = StreamState.COMPLETE

    def _update_state(self, event: AIStreamEvent) -> None:
        """Update state machine based on event type.

        Args:
            event: The event to process.
        """
        new_state: StreamState | None = None

        if event.type == StreamEventType.REASONING_CHUNK:
            new_state = StreamState.IN_THINKING
        elif event.type == StreamEventType.TOOL_CALL:
            new_state = StreamState.IN_TOOL_CALL
        elif event.type == StreamEventType.CHUNK:
            new_state = StreamState.IN_CONTENT

        if new_state is not None:
            if self.state.can_transition_to(new_state):
                self.state = new_state
            else:
                logger.debug(
                    "[stream-tracker] Invalid state transition from %s to %s for trace %s",
                    self.state.value,
                    new_state.value,
                    self.trace_id,
                )

    def _update_state_for_reasoning(self) -> None:
        """Update state when reasoning is detected.

        Uses can_transition_to() validation to ensure valid state transitions.
        """
        target_state = StreamState.IN_THINKING
        if self.state.can_transition_to(target_state):
            self.state = target_state
        else:
            logger.warning(
                "[stream-tracker] Invalid reasoning state transition from %s",
                self.state.value,
            )

    def check_timeouts(self) -> tuple[bool, str | None]:
        """Check if any timeout conditions have been violated.

        Returns:
            Tuple of (is_timeout, error_message).
        """
        now = time.time()

        # Check overall stream timeout
        if self.stream_start_time is not None:
            elapsed = now - self.stream_start_time
            if elapsed > self.stream_timeout:
                error_msg = f"Stream timeout: exceeded {self.stream_timeout}s total duration"
                self._validation_errors.append(error_msg)
                logger.warning(
                    "[stream-tracker] Stream timeout for trace %s: %.1fs elapsed",
                    self.trace_id,
                    elapsed,
                )
                return True, error_msg

        # Check token timeout (stall detection)
        if self.last_token_time is not None:
            time_since_last_token = now - self.last_token_time
            if time_since_last_token > self.token_timeout and not self._has_completed_cleanly:
                error_msg = f"Token timeout: no new token received for {self.token_timeout}s"
                self._validation_errors.append(error_msg)
                logger.warning(
                    "[stream-tracker] Token timeout for trace %s: %.1fs since last token",
                    self.trace_id,
                    time_since_last_token,
                )
                return True, error_msg

        return False, None

    def build_result(self, latency_ms: int) -> LLMStreamResult:
        """Build an LLMStreamResult from tracked state.

        Args:
            latency_ms: Total latency in milliseconds.

        Returns:
            A complete LLMStreamResult with all tracking data.
        """
        return LLMStreamResult(
            events=list(self.events),
            is_complete=self._has_completed_cleanly and not self._has_error,
            validation_errors=list(self._validation_errors),
            collected_output="",  # Filled by caller
            collected_reasoning="",  # Filled by caller
            tool_calls_count=self.tool_calls_count,
            chunk_count=self.chunk_count,
            latency_ms=latency_ms,
            trace_id=self.trace_id,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get tracking statistics.

        Returns:
            Dictionary with tracking stats.
        """
        now = time.time()
        elapsed = (now - self.stream_start_time) if self.stream_start_time else 0
        time_since_last = (now - self.last_token_time) if self.last_token_time else 0

        return {
            "trace_id": self.trace_id,
            "state": self.state.value,
            "elapsed_seconds": elapsed,
            "seconds_since_last_token": time_since_last,
            "chunk_count": self.chunk_count,
            "tool_calls_count": self.tool_calls_count,
            "has_received_content": self._has_received_content,
            "has_completed_cleanly": self._has_completed_cleanly,
            "has_error": self._has_error,
            "validation_errors_count": len(self._validation_errors),
        }
