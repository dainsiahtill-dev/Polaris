"""Message types for KernelOne.

This module provides:
- Part: Discriminated union of all part types
- MessageContent: Message with role and parts
- Part factories for common operations
- Serialization helpers
"""

from polaris.kernelone.messages.part_types import (
    AgentPart,
    CompactionPart,
    FilePart,
    FilePartSource,
    FileSource,
    MessageContent,
    # Message
    MessageRole,
    # Part types
    Part,
    PartBase,
    PartType,
    PatchPart,
    ReasoningPart,
    ResourceSource,
    RetryPart,
    SnapshotPart,
    StepFinishPart,
    StepStartPart,
    SubtaskPart,
    SymbolSource,
    TextPart,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStatePending,
    ToolStateRunning,
    # Tool state
    # Note: ToolStateUnion avoids collision with ToolState dataclass in state_machine.py
    ToolStateUnion,
    create_reasoning_part,
    # Factory functions
    create_text_part,
    create_tool_part,
    message_from_dict,
    message_to_dict,
    part_from_dict,
    # Serialization
    part_to_dict,
)

__all__ = [
    "AgentPart",
    "CompactionPart",
    "FilePart",
    "FilePartSource",
    "FileSource",
    "MessageContent",
    "MessageRole",
    # Part types
    "Part",
    "PartBase",
    "PartType",
    "PatchPart",
    "ReasoningPart",
    "ResourceSource",
    "RetryPart",
    "SnapshotPart",
    "StepFinishPart",
    "StepStartPart",
    "SubtaskPart",
    "SymbolSource",
    "TextPart",
    "ToolPart",
    "ToolStateCompleted",
    "ToolStateError",
    "ToolStatePending",
    "ToolStateRunning",
    # Tool state
    "ToolStateUnion",
    "create_reasoning_part",
    # Factory functions
    "create_text_part",
    "create_tool_part",
    "message_from_dict",
    "message_to_dict",
    "part_from_dict",
    # Serialization
    "part_to_dict",
]
