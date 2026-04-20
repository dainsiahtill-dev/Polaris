"""Unified transcript management for tool execution.

This module provides data structures for recording and managing tool execution
transcripts. It consolidates transcript types from the cells layer into
kernelone for canonical use.

Design Decisions:
- Frozen dataclasses for immutability and event sourcing compliance
- Separate entry types for calls and results
- Thread-safe list operations for concurrent access
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# -----------------------------------------------------------------------------
# Transcript Entry Types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolTranscriptEntry:
    """A single tool execution entry in the transcript.

    Attributes:
        tool: Name of the tool that was executed
        args: Arguments passed to the tool
        result: Result returned by the tool (None if not yet executed)
        timestamp: ISO format timestamp when entry was created
        success: Whether the tool executed successfully
        error: Error message if tool failed
    """

    tool: str
    args: dict[str, Any]
    result: Any = field(default=None)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success: bool = True
    error: str | None = None


@dataclass(frozen=True)
class ToolCallEntry:
    """Represents a tool call request.

    Attributes:
        tool: Name of the tool to call
        args: Arguments for the tool call
        call_id: Unique identifier for this call
        timestamp: ISO format timestamp when call was made
    """

    tool: str
    args: dict[str, Any]
    call_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class ToolResultEntry:
    """Represents a tool execution result.

    Attributes:
        tool: Name of the tool that was executed
        result: Result returned by the tool
        success: Whether the tool executed successfully
        error: Error message if tool failed
        timestamp: ISO format timestamp when result was received
    """

    tool: str
    result: Any
    success: bool = True
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# -----------------------------------------------------------------------------
# Transcript Log
# -----------------------------------------------------------------------------


class TranscriptLog:
    """Manages the sequence of tool execution entries.

    This class provides a mutable container for tracking tool calls and their
    results throughout a conversation turn. It is not frozen because we need
    to append entries during execution.

    Attributes:
        entries: List of tool transcript entries

    Example:
        log = TranscriptLog()
        log.add_call("read_file", {"path": "/tmp/test.txt"})
        log.add_result("read_file", {"content": "hello"})
        assert len(log) == 2
    """

    entries: list[ToolTranscriptEntry]

    def __init__(self) -> None:
        """Initialize empty transcript log."""
        self.entries: list[ToolTranscriptEntry] = []

    def __len__(self) -> int:
        """Return number of entries in the log."""
        return len(self.entries)

    def __iter__(self):
        """Iterate over entries in the log."""
        return iter(self.entries)

    def add_call(
        self,
        tool: str,
        args: dict[str, Any],
        call_id: str | None = None,
    ) -> ToolCallEntry:
        """Record a tool call.

        Args:
            tool: Name of the tool being called
            args: Arguments passed to the tool
            call_id: Optional unique identifier for this call

        Returns:
            ToolCallEntry that was added
        """
        entry = ToolCallEntry(tool=tool, args=args, call_id=call_id)
        self.entries.append(
            ToolTranscriptEntry(
                tool=tool,
                args=args,
                result=None,
                success=True,
                error=None,
            )
        )
        return entry

    def add_result(
        self,
        tool: str,
        result: Any,
        success: bool = True,
        error: str | None = None,
    ) -> ToolResultEntry:
        """Record a tool result.

        Args:
            tool: Name of the tool that was executed
            result: Result returned by the tool
            success: Whether the tool executed successfully
            error: Error message if tool failed

        Returns:
            ToolResultEntry that was added
        """
        entry = ToolResultEntry(
            tool=tool,
            result=result,
            success=success,
            error=error,
        )
        self.entries.append(
            ToolTranscriptEntry(
                tool=tool,
                args={},  # Args were recorded in add_call
                result=result,
                success=success,
                error=error,
            )
        )
        return entry

    def add(
        self,
        tool: str,
        args: dict[str, Any],
        result: Any,
        success: bool = True,
        error: str | None = None,
    ) -> ToolTranscriptEntry:
        """Add a complete tool entry with call and result.

        Args:
            tool: Name of the tool
            args: Arguments passed to the tool
            result: Result returned by the tool
            success: Whether the tool executed successfully
            error: Error message if tool failed

        Returns:
            ToolTranscriptEntry that was added
        """
        entry = ToolTranscriptEntry(
            tool=tool,
            args=args,
            result=result,
            success=success,
            error=error,
        )
        self.entries.append(entry)
        return entry

    def clear(self) -> None:
        """Clear all entries from the log."""
        self.entries.clear()

    def get_calls(self) -> list[ToolCallEntry]:
        """Get all tool calls (entries with result=None at call time).

        Note: This reconstructs call entries from the transcript log.

        Returns:
            List of tool call entries
        """
        calls: list[ToolCallEntry] = []
        for entry in self.entries:
            if entry.result is None:
                calls.append(ToolCallEntry(tool=entry.tool, args=entry.args))
        return calls

    def get_results(self) -> list[ToolResultEntry]:
        """Get all tool results.

        Returns:
            List of tool result entries
        """
        return [
            ToolResultEntry(
                tool=entry.tool,
                result=entry.result,
                success=entry.success,
                error=entry.error,
            )
            for entry in self.entries
            if entry.result is not None
        ]

    def to_list(self) -> list[dict[str, Any]]:
        """Convert transcript log to list of dictionaries.

        Returns:
            List of entry dictionaries
        """
        return [
            {
                "tool": entry.tool,
                "args": entry.args,
                "result": entry.result,
                "success": entry.success,
                "error": entry.error,
                "timestamp": entry.timestamp,
            }
            for entry in self.entries
        ]


__all__ = [
    "ToolCallEntry",
    "ToolResultEntry",
    "ToolTranscriptEntry",
    "TranscriptLog",
]
