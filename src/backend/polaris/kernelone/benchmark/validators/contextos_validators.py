"""ContextOS Reliability & Stability Validators

Specialized validators for verifying ContextOS behavior:
- Long session compression (长会话压缩)
- Context desynchronization detection (上下文失焦检测)
- Incorrect truncation detection (错误截断检测)
- Context loss detection (上下文丢失检测)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.kernelone.benchmark.unified_judge import ValidatorPort
from polaris.kernelone.events.constants import (
    EVENT_TYPE_LLM_CALL_END,
    EVENT_TYPE_LLM_CALL_START,
)

# -----------------------------------------------------------------------------
# Context OS Specific Validators
# -----------------------------------------------------------------------------


@dataclass
class ContextTraceEvent:
    """Context trace event from LLM caller events

    Attributes:
        event_type: Type of event (llm_call_start, llm_call_end) - uses EVENT_TYPE_LLM_CALL_START/EVENT_TYPE_LLM_CALL_END
        context_tokens_before: Token count before the call
        context_tokens_after: Token count after the call
        compression_strategy: Strategy used for compression
        compression_applied: Whether compression was applied
        prompt_tokens: Prompt token count
        completion_tokens: Completion token count
        turn_index: Turn index number
        tool_calls_count: Number of tool calls in this turn
    """

    event_type: str = ""
    context_tokens_before: int | None = None
    context_tokens_after: int | None = None
    compression_strategy: str | None = None
    compression_applied: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    turn_index: int = 0
    tool_calls_count: int = 0


# Alias for backward compatibility
ContextOSTraceEvent = ContextTraceEvent


class ContextOSTraceAnalyzer:
    """Context trace analyzer for ContextOS validators"""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.traces: list[ContextTraceEvent] = self._parse_events()

    def _parse_events(self) -> list[ContextTraceEvent]:
        """Parse raw events into structured traces"""
        traces = []
        for event in self.events:
            if event.get("event") not in (EVENT_TYPE_LLM_CALL_START, EVENT_TYPE_LLM_CALL_END):
                continue

            data = event.get("data", {})
            traces.append(
                ContextTraceEvent(
                    event_type=event.get("event", ""),
                    context_tokens_before=data.get("context_tokens_before"),
                    context_tokens_after=data.get("context_tokens_after"),
                    compression_strategy=data.get("compression_strategy"),
                    compression_applied=data.get("metadata", {}).get("compression_applied"),
                    prompt_tokens=data.get("prompt_tokens"),
                    completion_tokens=data.get("completion_tokens"),
                    turn_index=data.get("iteration", 0),
                    tool_calls_count=data.get("tool_calls_count", 0),
                )
            )
        return traces

    def get_turn_traces(self) -> dict[int, list[ContextTraceEvent]]:
        """Group traces by turn index"""
        turns: dict[int, list[ContextTraceEvent]] = {}
        for trace in self.traces:
            turns.setdefault(trace.turn_index, []).append(trace)
        return turns

    def calculate_token_change(self, start: ContextTraceEvent, end: ContextTraceEvent) -> int:
        """Calculate token change between start and end of a turn"""
        start_tokens = start.context_tokens_before or start.prompt_tokens or 0
        end_tokens = end.context_tokens_after or (start_tokens + (end.completion_tokens or 0))
        return end_tokens - start_tokens


class ContextOSLongSessionValidator(ValidatorPort):
    """Long session compression validator

    Verifies that ContextOS properly handles long sessions (>50 turns)
    without infinite growth of context.
    """

    name: str = "contextos_long_session_compression"
    category: str = "reliability"
    critical: bool = True

    def __init__(self, max_turns: int = 50, max_growth_threshold: float = 0.3) -> None:
        self.max_turns = max_turns
        self.max_growth_threshold = max_growth_threshold

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Validate long session compression behavior"""
        if not hasattr(observed, "tool_calls"):
            return True, "not a ContextOS run (no tool calls)"

        traces = self._extract_traces(observed)

        # Each turn has 2 traces (llm_call_start + llm_call_end), so 50 turns = 100 traces
        # If we have fewer than 2*max_turns traces, session is too short
        min_required_traces = self.max_turns * 2
        if len(traces) < min_required_traces:
            return True, f"session too short ({len(traces) // 2} turns < {self.max_turns})"

        # Get start tokens from the first llm_call_start event
        start_tokens = None
        for trace in traces:
            if trace.event_type == EVENT_TYPE_LLM_CALL_START and trace.context_tokens_before is not None:
                start_tokens = trace.context_tokens_before
                break

        if start_tokens is None:
            return False, "ContextOS: no llm_call_start with context_tokens_before found - must track tokens from start"

        # Get end tokens from the last llm_call_end event
        end_tokens = None
        for trace in reversed(traces):
            if trace.event_type == EVENT_TYPE_LLM_CALL_END:
                end_tokens = trace.context_tokens_after or trace.context_tokens_before
                break

        if end_tokens is None:
            return False, "ContextOS: no llm_call_end event found"

        growth_ratio = (end_tokens - start_tokens) / max(start_tokens, 1)

        if growth_ratio > self.max_growth_threshold:
            return False, (f"ContextOS: context grew by {growth_ratio:.1%} ({start_tokens} → {end_tokens} tokens)")

        return True, f"long session compression OK (growth: {growth_ratio:.1%})"

    def _extract_traces(self, observed: Any) -> list[ContextTraceEvent]:
        """Extract traces from observed run"""
        # Try direct attribute
        if hasattr(observed, "event_traces"):
            return observed.event_traces

        # Try metadata first (legacy)
        if hasattr(observed, "metadata") and "event_traces" in observed.metadata:
            return observed.metadata["event_traces"]

        # Try fingerprint (primary for ObservedBenchmarkRun)
        fingerprint = getattr(observed, "fingerprint", None)
        if isinstance(fingerprint, dict) and "event_traces" in fingerprint:
            return fingerprint["event_traces"]

        return []


class ContextOSDesynchronizationValidator(ValidatorPort):
    """Context desynchronization validator

    Detects when ContextOS loses synchronization with actual conversation state.
    """

    name: str = "contextos_desynchronization"
    category: str = "reliability"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Detect context desynchronization"""
        traces = self._extract_traces(observed)

        if len(traces) < 3:
            return True, "insufficient traces for desynchronization check"

        # Check for inconsistent token tracking
        desync_issues: list[str] = []
        for i in range(len(traces) - 1):
            current = traces[i]
            next_trace = traces[i + 1]

            # Get current's end tokens - only llm_call_end events have context_tokens_after
            # For llm_call_start, context_tokens_before represents the start
            if current.event_type == EVENT_TYPE_LLM_CALL_END:
                current_end = current.context_tokens_after or current.context_tokens_before
            else:
                # For llm_call_start events, we can't determine end tokens reliably
                # This trace is not useful for gap detection
                continue

            next_start = next_trace.context_tokens_before

            if current_end is not None and next_start is not None:
                gap = abs(next_start - current_end)
                threshold = max(100, current_end * 0.1)  # 10% tolerance

                if gap > threshold:
                    desync_issues.append(
                        f"index={i}(turn={current.turn_index}): {current_end} → {next_start} (gap: {gap})"
                    )

        if desync_issues:
            return False, (
                f"ContextOS: token desynchronization detected at {len(desync_issues)} location(s) - "
                f"{'; '.join(desync_issues)}"
            )

        return True, "no context desynchronization detected"

    def _extract_traces(self, observed: Any) -> list[ContextTraceEvent]:
        """Extract traces from observed run"""
        # Try direct attribute
        if hasattr(observed, "event_traces"):
            return observed.event_traces

        # Try metadata first (legacy)
        if hasattr(observed, "metadata") and "event_traces" in observed.metadata:
            return observed.metadata["event_traces"]

        # Try fingerprint (primary for ObservedBenchmarkRun)
        fingerprint = getattr(observed, "fingerprint", None)
        if isinstance(fingerprint, dict) and "event_traces" in fingerprint:
            return fingerprint["event_traces"]

        return []


class ContextOSIncorrectTruncationValidator(ValidatorPort):
    """Context incorrect truncation validator

    Detects when ContextOS truncates context inappropriately,
    losing critical conversation history.
    """

    name: str = "contextos_incorrect_truncation"
    category: str = "reliability"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Detect incorrect truncation patterns"""
        traces = self._extract_traces(observed)

        if len(traces) < 2:
            return True, "insufficient traces for truncation check"

        # Check for unexpected token drops without compression
        truncation_issues: list[str] = []
        for i in range(1, len(traces)):
            prev = traces[i - 1]
            curr = traces[i]

            # Only check drops that occur after an llm_call_end event
            # (llm_call_end has context_tokens_after which represents the turn end state)
            if prev.event_type != EVENT_TYPE_LLM_CALL_END:
                continue

            prev_end = prev.context_tokens_after or prev.context_tokens_before
            curr_start = curr.context_tokens_before

            # Check if compression was applied in the previous turn
            prev_compression_applied = prev.compression_applied or False

            # If compression was not applied, tokens should not drop significantly
            if prev_end is not None and curr_start is not None and not prev_compression_applied:
                drop_ratio = (prev_end - curr_start) / max(prev_end, 1)

                # >30% drop without compression = suspicious
                if drop_ratio > 0.3:
                    truncation_issues.append(
                        f"index={i}(turn={curr.turn_index}): {prev_end} → {curr_start} (drop: {drop_ratio:.1%})"
                    )

        if truncation_issues:
            return False, (
                f"ContextOS: unexpected token drop at {len(truncation_issues)} location(s) - "
                f"{'; '.join(truncation_issues)}"
            )

        return True, "no incorrect truncation detected"

    def _extract_traces(self, observed: Any) -> list[ContextTraceEvent]:
        """Extract traces from observed run"""
        # Try direct attribute
        if hasattr(observed, "event_traces"):
            return observed.event_traces

        # Try metadata first (legacy)
        if hasattr(observed, "metadata") and "event_traces" in observed.metadata:
            return observed.metadata["event_traces"]

        # Try fingerprint (primary for ObservedBenchmarkRun)
        fingerprint = getattr(observed, "fingerprint", None)
        if isinstance(fingerprint, dict) and "event_traces" in fingerprint:
            return fingerprint["event_traces"]

        return []


class ContextOSLossValidator(ValidatorPort):
    """Context loss validator

    Detects when ContextOS loses entire turns or significant content.
    """

    name: str = "contextos_loss"
    category: str = "reliability"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Detect context loss"""
        traces = self._extract_traces(observed)

        # CRITICAL: Any null context_tokens_before is ALWAYS a failure
        # ContextOS must track tokens for every LLM call
        null_indices: list[int] = []
        for i, trace in enumerate(traces):
            if trace.context_tokens_before is None:
                null_indices.append(i)

        if null_indices:
            failed_traces = [f"index={i}(turn={traces[i].turn_index})" for i in null_indices]
            return False, (
                f"ContextOS: context_tokens_before is null at {len(null_indices)} trace(s) - "
                f"indices: {', '.join(failed_traces)} "
                f"CRITICAL: ContextOS must track tokens on every LLM call"
            )

        if len(traces) < 2:
            return True, "insufficient traces for context loss check (but no null tokens found)"

        return True, "no context loss detected"

    def _extract_traces(self, observed: Any) -> list[ContextTraceEvent]:
        """Extract traces from observed run"""
        # Try direct attribute
        if hasattr(observed, "event_traces"):
            return observed.event_traces

        # Try metadata first (legacy)
        if hasattr(observed, "metadata") and "event_traces" in observed.metadata:
            return observed.metadata["event_traces"]

        # Try fingerprint (primary for ObservedBenchmarkRun)
        fingerprint = getattr(observed, "fingerprint", None)
        if isinstance(fingerprint, dict) and "event_traces" in fingerprint:
            return fingerprint["event_traces"]

        return []


# -----------------------------------------------------------------------------
# Built-in Validator Registry
# -----------------------------------------------------------------------------


__all__ = [
    "ContextOSDesynchronizationValidator",
    "ContextOSIncorrectTruncationValidator",
    "ContextOSLongSessionValidator",
    "ContextOSLossValidator",
    "ContextOSTraceAnalyzer",
    "ContextTraceEvent",
    "get_contextos_validators",
]


def get_contextos_validators() -> list[ValidatorPort]:
    """Get all ContextOS validators"""
    return [
        ContextOSLongSessionValidator(),
        ContextOSDesynchronizationValidator(),
        ContextOSIncorrectTruncationValidator(),
        ContextOSLossValidator(),
    ]
