"""Part type system for KernelOne.

This module provides a discriminated union of Part types for representing
message content in a structured way.

Reference: OpenCode packages/opencode/src/session/message-v2.ts (Part types)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Part Type Discriminator
# =============================================================================


class PartType(str, Enum):
    """Discriminator for Part types."""

    TEXT = "text"
    TOOL = "tool"
    FILE = "file"
    REASONING = "reasoning"
    SUBTASK = "subtask"
    STEP_START = "step-start"
    STEP_FINISH = "step-finish"
    SNAPSHOT = "snapshot"
    PATCH = "patch"
    AGENT = "agent"
    RETRY = "retry"
    COMPACTION = "compaction"


# =============================================================================
# Base Part
# =============================================================================


class PartBase(BaseModel):
    """Base class for all Part types.

    Provides common fields for all message parts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    part_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str | None = None
    message_id: str | None = None


# =============================================================================
# Text Part
# =============================================================================


class TextPart(PartBase):
    """Text content part.

    Represents a piece of text content in a message.
    """

    part_type: Literal[PartType.TEXT] = PartType.TEXT
    text: str
    synthetic: bool = False
    ignored: bool = False
    time: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


# =============================================================================
# Reasoning Part
# =============================================================================


class ReasoningPart(PartBase):
    """Reasoning/thinking content part.

    Represents LLM reasoning or thinking process.
    """

    part_type: Literal[PartType.REASONING] = PartType.REASONING
    text: str
    metadata: dict[str, Any] | None = None
    time: dict[str, int]  # Required: {start: number, end?: number}


# =============================================================================
# Tool State (for ToolPart)
# =============================================================================


class ToolStatePending(BaseModel):
    """Tool state when pending."""

    status: Literal["pending"] = "pending"
    input: dict[str, Any]
    raw: str | None = None


class ToolStateRunning(BaseModel):
    """Tool state when running."""

    status: Literal["running"] = "running"
    input: dict[str, Any]
    title: str | None = None
    metadata: dict[str, Any] | None = None
    time: dict[str, int]  # {start: number}


class ToolStateCompleted(BaseModel):
    """Tool state when completed."""

    status: Literal["completed"] = "completed"
    input: dict[str, Any]
    output: str
    title: str | None = None
    metadata: dict[str, Any] | None = None
    time: dict[str, int]  # {start: number, end: number, compacted?: number}
    attachments: list[dict[str, Any]] | None = None


class ToolStateError(BaseModel):
    """Tool state when errored."""

    status: Literal["error"] = "error"
    input: dict[str, Any]
    error: str
    metadata: dict[str, Any] | None = None
    time: dict[str, int]  # {start: number, end: number}


# Tool state discriminated union
# Note: Named ToolStateUnion to avoid collision with ToolState dataclass in state_machine.py
ToolStateUnion = Annotated[
    ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError,
    Field(discriminator="status"),
]


# =============================================================================
# Tool Part
# =============================================================================


class ToolPart(PartBase):
    """Tool invocation part.

    Represents a tool call with its state.
    """

    part_type: Literal[PartType.TOOL] = PartType.TOOL
    call_id: str
    tool: str
    state: ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError
    metadata: dict[str, Any] | None = None


# =============================================================================
# File Part
# =============================================================================


class FileSource(BaseModel):
    """File source for file part."""

    type: Literal["file"] = "file"
    path: str
    text: dict[str, Any]  # {value: string, start: number, end: number}


class SymbolSource(BaseModel):
    """Symbol source for file part."""

    type: Literal["symbol"] = "symbol"
    path: str
    range: dict[str, Any]  # LSP Range
    name: str
    kind: int


class ResourceSource(BaseModel):
    """Resource source for file part."""

    type: Literal["resource"] = "resource"
    client_name: str
    uri: str


FilePartSource = FileSource | SymbolSource | ResourceSource


class FilePart(PartBase):
    """File attachment part.

    Represents a file or resource attachment.
    """

    part_type: Literal[PartType.FILE] = PartType.FILE
    mime: str
    filename: str | None = None
    url: str
    source: FilePartSource | None = None


# =============================================================================
# Subtask Part
# =============================================================================


class SubtaskPart(PartBase):
    """Subtask execution part.

    Represents a subtask to be executed.
    """

    part_type: Literal[PartType.SUBTASK] = PartType.SUBTASK
    prompt: str
    description: str
    agent: str
    model: dict[str, str] | None = None  # {providerID: string, modelID: string}
    command: str | None = None


# =============================================================================
# Step Parts
# =============================================================================


class StepStartPart(PartBase):
    """Step start part.

    Marks the beginning of a step.
    """

    part_type: Literal[PartType.STEP_START] = PartType.STEP_START
    snapshot: str | None = None


class StepFinishPart(PartBase):
    """Step finish part.

    Marks the completion of a step.
    """

    part_type: Literal[PartType.STEP_FINISH] = PartType.STEP_FINISH
    reason: str
    snapshot: str | None = None
    cost: float
    tokens: dict[str, Any]  # Token usage info


# =============================================================================
# Snapshot Part
# =============================================================================


class SnapshotPart(PartBase):
    """Snapshot content part.

    Represents a snapshot of the current state.
    """

    part_type: Literal[PartType.SNAPSHOT] = PartType.SNAPSHOT
    snapshot: str


# =============================================================================
# Patch Part
# =============================================================================


class PatchPart(PartBase):
    """Patch/diff content part.

    Represents a set of file changes.
    """

    part_type: Literal[PartType.PATCH] = PartType.PATCH
    hash: str
    files: list[str]


# =============================================================================
# Agent Part
# =============================================================================


class AgentPart(PartBase):
    """Agent invocation part.

    Represents an agent sub-call.
    """

    part_type: Literal[PartType.AGENT] = PartType.AGENT
    name: str
    source: dict[str, Any] | None = None  # {value: string, start: number, end: number}


# =============================================================================
# Retry Part
# =============================================================================


class RetryPart(PartBase):
    """Retry attempt part.

    Represents a retry attempt after an error.
    """

    part_type: Literal[PartType.RETRY] = PartType.RETRY
    attempt: int
    error: dict[str, Any]  # APIError-like structure
    time: dict[str, int]  # {created: number}


# =============================================================================
# Compaction Part
# =============================================================================


class CompactionPart(PartBase):
    """Context compaction part.

    Represents a context compaction event.
    """

    part_type: Literal[PartType.COMPACTION] = PartType.COMPACTION
    auto: bool
    overflow: bool = False


# =============================================================================
# Part Discriminated Union
# =============================================================================


# Main Part discriminated union type
Part = Annotated[
    (
        TextPart
        | SubtaskPart
        | ReasoningPart
        | FilePart
        | ToolPart
        | StepStartPart
        | StepFinishPart
        | SnapshotPart
        | PatchPart
        | AgentPart
        | RetryPart
        | CompactionPart
    ),
    Field(discriminator="part_type"),
]


# =============================================================================
# Message Content
# =============================================================================


class MessageRole(str, Enum):
    """Message role types."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageContent(BaseModel):
    """Message content with parts.

    Represents a message that contains multiple parts.

    Design Pattern: Immutable with Functional Updates
    -----------------------------------------------
    This class uses `frozen=True` to enforce immutability. Methods like
    `add_text()` and `add_tool()` return NEW instances rather than mutating.
    This is intentional to support functional update patterns:

        msg = MessageContent(role=MessageRole.USER, parts=[])
        msg = msg.add_text("Hello")  # Returns new instance
        msg = msg.add_tool("read", "call_1", ...)  # Returns new instance

    Benefits:
    - Thread-safe (no mutation)
    - Predictable state changes
    - Easy to trace history with immutable snapshots
    - Compatible with event sourcing patterns

    Note: Individual Part classes (TextPart, ToolPart, etc.) are also frozen
    and immutable. Use factory functions to create parts with specific states.
    """

    model_config = ConfigDict(frozen=True)

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str | None = None
    role: MessageRole
    parts: list[Part] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_text(self, text: str, **kwargs: Any) -> MessageContent:
        """Create a new MessageContent with added text part.

        Returns a NEW MessageContent instance (does not mutate self).
        """
        text_part = TextPart(text=text, **kwargs)
        return MessageContent(
            message_id=self.message_id,
            session_id=self.session_id,
            role=self.role,
            parts=[*self.parts, text_part],
            timestamp=self.timestamp,
        )

    def add_tool(
        self,
        tool: str,
        call_id: str,
        state: ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError,
        **kwargs: Any,
    ) -> MessageContent:
        """Create a new MessageContent with added tool part.

        Returns a NEW MessageContent instance (does not mutate self).
        """
        tool_part = ToolPart(tool=tool, call_id=call_id, state=state, **kwargs)
        return MessageContent(
            message_id=self.message_id,
            session_id=self.session_id,
            role=self.role,
            parts=[*self.parts, tool_part],
            timestamp=self.timestamp,
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_text_part(
    text: str,
    part_id: str | None = None,
    synthetic: bool = False,
    metadata: dict[str, Any] | None = None,
) -> TextPart:
    """Create a text part.

    Args:
        text: The text content
        part_id: Optional part ID
        synthetic: Whether this is synthetic content
        metadata: Optional metadata

    Returns:
        TextPart instance
    """
    return TextPart(
        part_id=part_id or uuid.uuid4().hex[:12],
        text=text,
        synthetic=synthetic,
        metadata=metadata,
    )


def create_tool_part(
    tool: str,
    call_id: str,
    state: ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError,
    part_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolPart:
    """Create a tool part.

    Args:
        tool: Tool name
        call_id: Call ID
        state: Tool state
        part_id: Optional part ID
        metadata: Optional metadata

    Returns:
        ToolPart instance
    """
    return ToolPart(
        part_id=part_id or uuid.uuid4().hex[:12],
        call_id=call_id,
        tool=tool,
        state=state,
        metadata=metadata,
    )


def create_reasoning_part(
    text: str,
    start_time: int,
    end_time: int | None = None,
    part_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReasoningPart:
    """Create a reasoning part.

    Args:
        text: Reasoning text
        start_time: Start timestamp
        end_time: Optional end timestamp
        part_id: Optional part ID
        metadata: Optional metadata

    Returns:
        ReasoningPart instance
    """
    time: dict[str, int] = {"start": start_time}
    if end_time is not None:
        time["end"] = end_time

    return ReasoningPart(
        part_id=part_id or uuid.uuid4().hex[:12],
        text=text,
        time=time,
        metadata=metadata,
    )


# =============================================================================
# Serialization Helpers
# =============================================================================


def part_to_dict(part: Part) -> dict[str, Any]:
    """Convert a part to dictionary representation.

    Args:
        part: The part to convert

    Returns:
        Dictionary representation
    """
    return part.model_dump()


def part_from_dict(data: dict[str, Any]) -> Part:
    """Create a part from dictionary representation.

    Args:
        data: Dictionary representation

    Returns:
        Part instance
    """
    part_type = data.get("part_type")
    if part_type is None:
        raise ValueError("Missing 'part_type' field in part data")

    part_class = _PART_TYPE_MAP.get(part_type)
    if part_class is None:
        raise ValueError(f"Unknown part type: {part_type}")

    # Use model_validate for Pydantic models
    result = part_class.model_validate(data)
    return result  # type: ignore[return-value]


_PART_TYPE_MAP: dict[str, type[PartBase]] = {
    PartType.TEXT: TextPart,
    PartType.TOOL: ToolPart,
    PartType.FILE: FilePart,
    PartType.REASONING: ReasoningPart,
    PartType.SUBTASK: SubtaskPart,
    PartType.STEP_START: StepStartPart,
    PartType.STEP_FINISH: StepFinishPart,
    PartType.SNAPSHOT: SnapshotPart,
    PartType.PATCH: PatchPart,
    PartType.AGENT: AgentPart,
    PartType.RETRY: RetryPart,
    PartType.COMPACTION: CompactionPart,
}


def message_to_dict(message: MessageContent) -> dict[str, Any]:
    """Convert a message to dictionary representation.

    Args:
        message: The message to convert

    Returns:
        Dictionary representation
    """
    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": message.role.value,
        "parts": [part_to_dict(p) for p in message.parts],
        "timestamp": message.timestamp.isoformat(),
    }


def message_from_dict(data: dict[str, Any]) -> MessageContent:
    """Create a message from dictionary representation.

    Args:
        data: Dictionary representation

    Returns:
        MessageContent instance
    """
    return MessageContent(
        message_id=data["message_id"],
        session_id=data.get("session_id"),
        role=MessageRole(data["role"]),
        parts=[part_from_dict(p) for p in data.get("parts", [])],
        timestamp=datetime.fromisoformat(data["timestamp"])
        if isinstance(data.get("timestamp"), str)
        else datetime.now(timezone.utc),
    )
