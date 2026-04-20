"""Polaris AI Platform - Stream Configuration and State

Immutable configuration, state machine, and result validation for streaming.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.constants import (
    DEFAULT_LLM_STREAM_BUFFER_SIZE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
)
from polaris.kernelone.llm._timeout_config import (
    get_stream_timeout as _get_stream_timeout_unified,
    get_token_timeout as _get_token_timeout_unified,
    reset_config as _reset_unified_config,
    set_stream_timeout as _set_stream_timeout_unified,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Stream Configuration (Immutable Dataclass - H-04 Fix)
# ============================================================================


@dataclass(frozen=True)
class StreamConfig:
    """Immutable stream executor configuration.

    Replaces module-level globals with injectable, thread-safe configuration.
    All values are positive numbers validated at construction time.
    """

    timeout_sec: float = DEFAULT_OPERATION_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    buffer_size: int = DEFAULT_LLM_STREAM_BUFFER_SIZE
    max_pending_calls: int = 100
    token_timeout_sec: float = 60.0

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.timeout_sec <= 0:
            object.__setattr__(self, "timeout_sec", DEFAULT_OPERATION_TIMEOUT_SECONDS)
        if self.max_retries < 0:
            object.__setattr__(self, "max_retries", DEFAULT_MAX_RETRIES)
        if self.buffer_size <= 0:
            object.__setattr__(self, "buffer_size", DEFAULT_LLM_STREAM_BUFFER_SIZE)
        if self.max_pending_calls <= 0:
            object.__setattr__(self, "max_pending_calls", 100)
        if self.token_timeout_sec <= 0:
            object.__setattr__(self, "token_timeout_sec", 60.0)

    @classmethod
    def from_env(cls) -> StreamConfig:
        """Create config from environment variables using unified timeout config."""
        return cls(
            timeout_sec=_get_stream_timeout_unified(),
            buffer_size=int(os.environ.get("KERNELONE_LLM_STREAM_BUFFER_SIZE", str(DEFAULT_LLM_STREAM_BUFFER_SIZE))),
            max_pending_calls=int(os.environ.get("KERNELONE_LLM_MAX_PENDING_CALLS", "100")),
            token_timeout_sec=_get_token_timeout_unified(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging/debugging."""
        return {
            "timeout_sec": self.timeout_sec,
            "max_retries": self.max_retries,
            "buffer_size": self.buffer_size,
            "max_pending_calls": self.max_pending_calls,
            "token_timeout_sec": self.token_timeout_sec,
        }


# ============================================================================
# Backward Compatibility Layer (deprecated globals - H-04 Fix)
# ============================================================================
# These globals are deprecated but maintained for backward compatibility
# Use StreamConfig for new code

# Default config for backward compatibility
_DEFAULT_CONFIG = StreamConfig()

# Expose defaults for external code that reads these globals
MAX_BUFFER_SIZE: int = _DEFAULT_CONFIG.buffer_size
_MAX_PENDING_TOOL_CALLS: int = _DEFAULT_CONFIG.max_pending_calls
_TOKEN_TIMEOUT: float = _DEFAULT_CONFIG.token_timeout_sec
_STREAM_TIMEOUT: float = _DEFAULT_CONFIG.timeout_sec


def get_stream_timeout() -> float:
    """Get the configured stream overall timeout in seconds.

    DEPRECATED: Use polaris.kernelone.llm._timeout_config.get_stream_timeout() instead.
    This function delegates to the unified timeout config for backward compatibility.
    """
    return _get_stream_timeout_unified()


def set_stream_timeout(timeout_sec: float) -> None:
    """Set stream overall timeout (for testing).

    DEPRECATED: Use polaris.kernelone.llm._timeout_config.set_stream_timeout() instead.
    """
    _set_stream_timeout_unified(timeout_sec)


def reset_stream_timeout() -> None:
    """Reset stream timeout to default from environment.

    DEPRECATED: Use polaris.kernelone.llm._timeout_config.reset_config() instead.
    """
    _reset_unified_config()


# ============================================================================
# Stream State Machine
# ============================================================================


class StreamState(Enum):
    """Streaming state machine states.

    Tracks the lifecycle of a streaming response for edge case handling
    and automatic recovery.
    """

    IDLE = "idle"
    IN_THINKING = "in_thinking"
    IN_TOOL_CALL = "in_tool_call"
    IN_CONTENT = "in_content"
    COMPLETE = "complete"
    ERROR = "error"

    def can_transition_to(self, next_state: StreamState) -> bool:
        """Validate state transitions.

        Args:
            next_state: The target state to transition to.

        Returns:
            True if the transition is valid, False otherwise.
        """
        valid_transitions: dict[StreamState, set[StreamState]] = {
            StreamState.IDLE: {
                StreamState.IN_THINKING,
                StreamState.IN_CONTENT,
                StreamState.IN_TOOL_CALL,
            },
            StreamState.IN_THINKING: {
                StreamState.IN_CONTENT,
                StreamState.IN_TOOL_CALL,
                StreamState.COMPLETE,
                StreamState.ERROR,
            },
            StreamState.IN_CONTENT: {
                StreamState.IN_TOOL_CALL,
                StreamState.COMPLETE,
                StreamState.ERROR,
            },
            StreamState.IN_TOOL_CALL: {
                StreamState.IN_CONTENT,
                StreamState.IN_TOOL_CALL,
                StreamState.COMPLETE,
                StreamState.ERROR,
            },
            StreamState.COMPLETE: set(),
            StreamState.ERROR: set(),
        }
        return next_state in valid_transitions.get(self, set())


# ============================================================================
# Stream Result Validation
# ============================================================================


@dataclass
class LLMStreamResult:
    """Streaming result with integrity validation for LLM operations.

    Captures the complete result of a streaming LLM operation along with
    validation state for detecting malformed sequences.

    Note: This is distinct from kernelone.process.async_contracts.StreamResult
    which represents process execution results (pid, exit_code, stdout, stderr).
    """

    events: list[Any] = field(default_factory=list)
    is_complete: bool = False
    validation_errors: list[str] = field(default_factory=list)
    collected_output: str = ""
    collected_reasoning: str = ""
    tool_calls_count: int = 0
    chunk_count: int = 0
    latency_ms: int = 0
    trace_id: str | None = None

    def add_validation_error(self, error: str) -> None:
        """Add a validation error to the result.

        Args:
            error: Description of the validation error.
        """
        self.validation_errors.append(error)
        logger.warning("[stream-executor] Validation error: %s", error)

    def to_dict(self) -> dict[str, Any]:
        """Serialize stream result to dict.

        Returns:
            Dictionary representation of the stream result.
        """
        return {
            "is_complete": self.is_complete,
            "validation_errors": list(self.validation_errors),
            "collected_output_length": len(self.collected_output),
            "collected_reasoning_length": len(self.collected_reasoning),
            "tool_calls_count": self.tool_calls_count,
            "chunk_count": self.chunk_count,
            "latency_ms": self.latency_ms,
            "trace_id": self.trace_id,
        }


def validate_stream_result(result: LLMStreamResult) -> bool:
    """Validate the completeness and integrity of a stream result.

    Args:
        result: The stream result to validate.

    Returns:
        True if the result is valid, False otherwise.
    """
    if not result.is_complete:
        result.add_validation_error("Stream did not complete")
        return False

    if len(result.validation_errors) > 0:
        logger.warning(
            "[stream-executor] Stream result has %d validation errors",
            len(result.validation_errors),
        )
        return False

    return True


def get_default_stream_config() -> StreamConfig:
    """Get the default stream configuration instance.

    Returns:
        The default StreamConfig instance used for backward compatibility.
    """
    return _DEFAULT_CONFIG


# Backward compatibility alias (deprecated, will be removed in future)
StreamResult = LLMStreamResult
