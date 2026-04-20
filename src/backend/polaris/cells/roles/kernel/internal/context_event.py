"""Context event types and safety policy for tool-loop controller.

This module provides the core data types for event-sourcing compliant
transcript management in the role kernel's tool execution loop.

Design Decisions:
- ContextEvent: SSOT-compliant event type replacing (role, content) tuples
- ToolLoopSafetyPolicy: Transport-level safety limits for multi-turn execution
  (delegated to polaris.kernelone.tool_state.safety)
- Configuration constants: Tunable limits for result compaction and context windows
  (delegated to polaris.kernelone.tool_state.safety)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Import ToolLoopSafetyPolicy and constants from kernelone.tool
# (delegated to canonical implementation in kernelone)
from polaris.kernelone.tool_state.safety import (
    _DEFAULT_CONTEXT_WINDOW_TOKENS,
    _MAX_READ_FILE_CONTENT_CHARS,
    _MAX_RESULT_DEPTH,
    _MAX_RESULT_ERROR_CHARS,
    _MAX_RESULT_LIST_ITEMS,
    _MAX_RESULT_OBJECT_KEYS,
    _MAX_RESULT_STRING_CHARS,
    _READ_FILE_PROMOTION_HEADROOM_RATIO,
    ToolLoopSafetyPolicy,
)

# -----------------------------------------------------------------------------
# ContextEvent: SSOT-compliant event type replacing (role, content) tuples
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """Standard context event type preserving full metadata.

    Replaces the legacy (role, content) tuple to ensure Context OS SSOT.
    All event metadata (event_id, sequence, kind, route, dialog_act, source_turns,
    artifact_id, created_at) is preserved throughout the pipeline.

    Design decisions:
    - frozen=True: Ensures immutability for Event Sourcing compliance
    - slots=True: Memory optimization for high-frequency event creation
    - to_tuple(): Backward-compatible interface for legacy consumers

    Note on kind field:
    - The Blueprint defines 'kind' as the semantic event type discriminator
    - Values: user_turn, assistant_turn, tool_call, tool_result, file_reference,
      retrieval_result, state_patch, episode_sealed
    - Since ContextEvent is frozen, kind is stored in metadata and accessed
      via the .kind property
    """

    event_id: str
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str
    sequence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str]:
        """Backward-compatible tuple representation for legacy interfaces.

        Warning: This loses metadata. Use only when legacy APIs require
        the tuple format. Prefer passing ContextEvent directly where possible.
        """
        return (self.role, self.content)

    @classmethod
    def from_tuple(cls, tuple_event: tuple[str, str], sequence: int) -> ContextEvent:
        """Create ContextEvent from legacy (role, content) tuple.

        Note: metadata will be empty since tuples don't carry it.
        """
        role, content = tuple_event
        return cls(
            event_id=f"legacy_{id(tuple_event)}_{sequence}",
            role=role,
            content=content,
            sequence=sequence,
            metadata={},
        )

    @property
    def dialog_act(self) -> str:
        """Extract dialog_act from metadata if present."""
        return str(self.metadata.get("dialog_act") or "")

    @property
    def route(self) -> str:
        """Extract route from metadata if present."""
        return str(self.metadata.get("route") or "")

    @property
    def kind(self) -> str:
        """Extract kind from metadata if present.

        Kind is the semantic event type discriminator per Blueprint:
        - user_turn: User message
        - assistant_turn: Assistant response without tool calls
        - tool_call: Assistant requesting tool execution
        - tool_result: Result of tool execution
        - file_reference: Reference to file artifact
        - retrieval_result: Result of memory search
        - state_patch: Working state update
        - episode_sealed: Episode closure marker
        """
        return str(self.metadata.get("kind") or "")


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
    "ContextEvent",
    "ToolLoopSafetyPolicy",
]
