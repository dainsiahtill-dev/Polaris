"""Unified safety policy for tool loop execution.

This module provides ToolLoopSafetyPolicy for transport-level safety limits
in multi-turn tool execution. It consolidates safety configuration from
the cells layer into kernelone for canonical use.

Design Decisions:
- Frozen dataclass for immutability and event sourcing compliance
- Environment variable overrides for operational flexibility
- Sensible defaults that prevent infinite loops while allowing sufficient throughput
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from polaris.kernelone._runtime_config import resolve_env_float, resolve_env_int

# -----------------------------------------------------------------------------
# Configuration Constants
# -----------------------------------------------------------------------------

_MAX_RESULT_STRING_CHARS = 1600
_MAX_RESULT_ERROR_CHARS = 480
_MAX_RESULT_LIST_ITEMS = 100
_MAX_RESULT_OBJECT_KEYS = 12
_MAX_RESULT_DEPTH = 6
_MAX_READ_FILE_CONTENT_CHARS = resolve_env_int("tool_loop_read_file_content_chars") or 16000
_DEFAULT_CONTEXT_WINDOW_TOKENS = 8000
_READ_FILE_PROMOTION_HEADROOM_RATIO = max(
    0.1,
    min(
        0.9,
        resolve_env_float("tool_loop_read_file_headroom_ratio") or 0.35,
    ),
)


# -----------------------------------------------------------------------------
# Tool Loop Safety Policy
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolLoopSafetyPolicy:
    """Transport-level safety policy for multi-turn tool execution.

    Attributes:
        max_total_tool_calls: Maximum number of tool calls allowed in a single
            turn before aborting. Set to 0 to disable this limit.
        max_stall_cycles: Maximum number of consecutive identical tool cycles
            before aborting. This prevents infinite loops where the LLM repeats
            the same tool call without making progress.
        max_wall_time_seconds: Maximum wall-clock time in seconds for a single
            turn before aborting. Set to 0 to disable this limit.
    """

    max_total_tool_calls: int = 64
    max_stall_cycles: int = 2
    max_wall_time_seconds: int = 900

    def is_allowed(
        self,
        tool_name: str,
        call_count: int,
        consecutive: int,
    ) -> bool:
        """Check if a tool call is allowed under this policy.

        Args:
            tool_name: Name of the tool being called
            call_count: Total number of tool calls in current turn
            consecutive: Number of consecutive calls to the same tool

        Returns:
            True if the call is allowed, False otherwise
        """
        del tool_name  # Reserved for future per-tool policies

        if self.max_total_tool_calls > 0 and call_count > self.max_total_tool_calls:
            return False
        return consecutive <= self.max_stall_cycles


def read_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read and validate an integer environment variable.

    Args:
        name: Environment variable name
        default: Default value if not set or invalid
        minimum: Minimum allowed value
        maximum: Maximum allowed value

    Returns:
        Validated integer value
    """
    raw = str(os.environ.get(name, str(default))).strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


__all__ = [
    "_DEFAULT_CONTEXT_WINDOW_TOKENS",
    "_MAX_READ_FILE_CONTENT_CHARS",
    "_MAX_RESULT_DEPTH",
    "_MAX_RESULT_ERROR_CHARS",
    "_MAX_RESULT_LIST_ITEMS",
    "_MAX_RESULT_OBJECT_KEYS",
    # Configuration constants (for advanced tuning)
    "_MAX_RESULT_STRING_CHARS",
    "_READ_FILE_PROMOTION_HEADROOM_RATIO",
    "ToolLoopSafetyPolicy",
    "read_int_env",
]
