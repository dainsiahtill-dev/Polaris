"""Tests for Part Types module.

Test coverage:
- Normal: Part creation, serialization, discriminated unions
- Boundary: Invalid part types, missing fields
- Error: Validation errors, unknown part types
"""

from __future__ import annotations

from datetime import timezone
from typing import cast

import pytest
from polaris.kernelone.messages import (
    AgentPart,
    CompactionPart,
    FilePart,
    FileSource,
    MessageContent,
    # Message
    MessageRole,
    # Part types
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
    # Tool state
    ToolStatePending,
    ToolStateRunning,
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


class TestPartType:
    """Tests for PartType enum."""

    def test_all_part_types_defined(self) -> None:
        """Test all expected part types are defined."""
        assert PartType.TEXT.value == "text"
        assert PartType.TOOL.value == "tool"
        assert PartType.FILE.value == "file"
        assert PartType.REASONING.value == "reasoning"
        assert PartType.SUBTASK.value == "subtask"
        assert PartType.STEP_START.value == "step-start"
        assert PartType.STEP_FINISH.value == "step-finish"
        assert PartType.SNAPSHOT.value == "snapshot"
        assert PartType.PATCH.value == "patch"
        assert PartType.AGENT.value == "agent"
        assert PartType.RETRY.value == "retry"
        assert PartType.COMPACTION.value == "compaction"


class TestTextPart:
    """Tests for TextPart."""

    def test_create_text_part(self) -> None:
        """Test creating a text part."""
        part = TextPart(text="Hello, World!")

        assert part.text == "Hello, World!"
        assert part.part_type == PartType.TEXT
        assert part.synthetic is False
        assert part.ignored is False
        assert part.part_id is not None

    def test_text_part_with_metadata(self) -> None:
        """Test text part with optional fields."""
        part = TextPart(
            text="Test content",
            synthetic=True,
            ignored=True,
            metadata={"source": "test"},
            time={"start": 100, "end": 200},
        )

        assert part.synthetic is True
        assert part.ignored is True
        assert part.metadata == {"source": "test"}
        assert part.time == {"start": 100, "end": 200}

    def test_text_part_is_frozen(self) -> None:
        """Test that text part is immutable."""
        part = TextPart(text="test")

        with pytest.raises(Exception):
            part.text = "changed"  # type: ignore


class TestReasoningPart:
    """Tests for ReasoningPart."""

    def test_create_reasoning_part(self) -> None:
        """Test creating a reasoning part."""
        part = ReasoningPart(
            text="Let me think about this...",
            time={"start": 100},
        )

        assert part.text == "Let me think about this..."
        assert part.part_type == PartType.REASONING
        assert part.time == {"start": 100}

    def test_reasoning_part_with_end_time(self) -> None:
        """Test reasoning part with end time."""
        part = ReasoningPart(
            text="Reasoning...",
            time={"start": 100, "end": 300},
        )

        assert part.time == {"start": 100, "end": 300}


class TestToolState:
    """Tests for tool state types."""

    def test_tool_state_pending(self) -> None:
        """Test creating pending tool state."""
        state = ToolStatePending(
            input={"filename": "test.txt"},
            raw="raw_content",
        )

        assert state.status == "pending"
        assert state.input == {"filename": "test.txt"}
        assert state.raw == "raw_content"

    def test_tool_state_running(self) -> None:
        """Test creating running tool state."""
        state = ToolStateRunning(
            input={"filename": "test.txt"},
            title="Reading file",
            time={"start": 100},
        )

        assert state.status == "running"
        assert state.title == "Reading file"
        assert state.time == {"start": 100}

    def test_tool_state_completed(self) -> None:
        """Test creating completed tool state."""
        state = ToolStateCompleted(
            input={"filename": "test.txt"},
            output="file contents",
            title="Read file",
            time={"start": 100, "end": 200},
            attachments=[{"mime": "text/plain", "url": "file:///test"}],
        )

        assert state.status == "completed"
        assert state.output == "file contents"
        assert len(state.attachments or []) == 1

    def test_tool_state_error(self) -> None:
        """Test creating error tool state."""
        state = ToolStateError(
            input={"filename": "test.txt"},
            error="File not found",
            time={"start": 100, "end": 200},
        )

        assert state.status == "error"
        assert state.error == "File not found"


class TestToolPart:
    """Tests for ToolPart."""

    def test_create_tool_part(self) -> None:
        """Test creating a tool part."""
        state = ToolStatePending(input={"path": "/tmp/test"})
        part = ToolPart(
            call_id="call_123",
            tool="read_file",
            state=state,
        )

        assert part.call_id == "call_123"
        assert part.tool == "read_file"
        assert part.state.status == "pending"

    def test_tool_part_with_metadata(self) -> None:
        """Test tool part with metadata."""
        state = ToolStateRunning(input={}, time={"start": 100})
        part = ToolPart(
            call_id="call_456",
            tool="write_file",
            state=state,
            metadata={"priority": "high"},
        )

        assert part.metadata == {"priority": "high"}


class TestFilePart:
    """Tests for FilePart."""

    def test_create_file_part(self) -> None:
        """Test creating a file part."""
        part = FilePart(
            mime="image/png",
            filename="screenshot.png",
            url="file:///tmp/screenshot.png",
        )

        assert part.mime == "image/png"
        assert part.filename == "screenshot.png"
        assert part.url == "file:///tmp/screenshot.png"

    def test_file_part_with_source(self) -> None:
        """Test file part with source."""
        source = FileSource(
            path="/project/file.txt",
            text={"value": "content", "start": 0, "end": 7},
        )
        part = FilePart(
            mime="text/plain",
            url="file:///project/file.txt",
            source=source,
        )

        assert part.source is not None
        assert cast("FileSource", part.source).path == "/project/file.txt"


class TestSubtaskPart:
    """Tests for SubtaskPart."""

    def test_create_subtask_part(self) -> None:
        """Test creating a subtask part."""
        part = SubtaskPart(
            prompt="Analyze this code",
            description="Code analysis task",
            agent="analyzer",
        )

        assert part.prompt == "Analyze this code"
        assert part.description == "Code analysis task"
        assert part.agent == "analyzer"


class TestStepParts:
    """Tests for step parts."""

    def test_step_start_part(self) -> None:
        """Test creating step start part."""
        part = StepStartPart(snapshot="state_snapshot")

        assert part.part_type == PartType.STEP_START
        assert part.snapshot == "state_snapshot"

    def test_step_finish_part(self) -> None:
        """Test creating step finish part."""
        part = StepFinishPart(
            reason="completed",
            cost=0.001,
            tokens={"total": 100, "input": 50, "output": 50, "cache": {"read": 0, "write": 0}},
        )

        assert part.reason == "completed"
        assert part.cost == 0.001
        assert part.tokens["total"] == 100


class TestOtherParts:
    """Tests for other part types."""

    def test_snapshot_part(self) -> None:
        """Test creating snapshot part."""
        part = SnapshotPart(snapshot="current_state")

        assert part.part_type == PartType.SNAPSHOT
        assert part.snapshot == "current_state"

    def test_patch_part(self) -> None:
        """Test creating patch part."""
        part = PatchPart(
            hash="abc123",
            files=["file1.txt", "file2.txt"],
        )

        assert part.hash == "abc123"
        assert len(part.files) == 2

    def test_agent_part(self) -> None:
        """Test creating agent part."""
        part = AgentPart(name="code_agent")

        assert part.name == "code_agent"

    def test_retry_part(self) -> None:
        """Test creating retry part."""
        part = RetryPart(
            attempt=2,
            error={"message": "Rate limited", "statusCode": 429},
            time={"created": 1000},
        )

        assert part.attempt == 2
        assert part.error["statusCode"] == 429

    def test_compaction_part(self) -> None:
        """Test creating compaction part."""
        part = CompactionPart(auto=True, overflow=False)

        assert part.auto is True
        assert part.overflow is False


class TestMessageContent:
    """Tests for MessageContent."""

    def test_create_message(self) -> None:
        """Test creating a message."""
        message = MessageContent(role=MessageRole.USER)

        assert message.role == MessageRole.USER
        assert message.parts == []
        assert message.message_id is not None

    def test_message_with_parts(self) -> None:
        """Test message with parts."""
        text_part = TextPart(text="Hello")
        tool_state = ToolStatePending(input={})
        tool_part = ToolPart(call_id="c1", tool="test", state=tool_state)

        message = MessageContent(
            role=MessageRole.ASSISTANT,
            parts=[text_part, tool_part],
        )

        assert len(message.parts) == 2
        assert message.parts[0].part_type == PartType.TEXT
        assert message.parts[1].part_type == PartType.TOOL

    def test_message_timestamp(self) -> None:
        """Test message has timestamp."""
        message = MessageContent(role=MessageRole.USER)

        assert message.timestamp.tzinfo == timezone.utc

    def test_add_text(self) -> None:
        """Test adding text to message."""
        original = MessageContent(role=MessageRole.ASSISTANT)
        updated = original.add_text("Hello, World!")

        assert len(updated.parts) == 1
        assert cast("TextPart", updated.parts[0]).text == "Hello, World!"

    def test_add_tool(self) -> None:
        """Test adding tool to message."""
        state = ToolStatePending(input={"path": "/test"})
        original = MessageContent(role=MessageRole.ASSISTANT)
        updated = original.add_tool("read_file", "call_123", state)

        assert len(updated.parts) == 1
        assert cast("ToolPart", updated.parts[0]).tool == "read_file"


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_text_part(self) -> None:
        """Test create_text_part factory."""
        part = create_text_part("Test text", synthetic=True)

        assert part.text == "Test text"
        assert part.synthetic is True
        assert part.part_type == PartType.TEXT

    def test_create_tool_part(self) -> None:
        """Test create_tool_part factory."""
        state = ToolStateRunning(input={}, time={"start": 100})
        part = create_tool_part("write_file", "call_xyz", state)

        assert part.tool == "write_file"
        assert part.call_id == "call_xyz"
        assert part.state.status == "running"

    def test_create_reasoning_part(self) -> None:
        """Test create_reasoning_part factory."""
        part = create_reasoning_part("Thinking...", start_time=100, end_time=300)

        assert part.text == "Thinking..."
        assert part.time["start"] == 100
        assert part.time["end"] == 300


class TestSerialization:
    """Tests for serialization helpers."""

    def test_part_to_dict(self) -> None:
        """Test converting part to dict."""
        part = TextPart(text="Hello")
        data = part_to_dict(part)

        assert data["text"] == "Hello"
        assert data["part_type"] == "text"

    def test_part_from_dict(self) -> None:
        """Test creating part from dict."""
        data = {"part_type": "text", "text": "Hello"}
        part = part_from_dict(data)

        assert isinstance(part, TextPart)
        assert part.text == "Hello"

    def test_part_from_dict_unknown_type(self) -> None:
        """Test error on unknown part type."""
        data = {"part_type": "unknown_type", "text": "test"}

        with pytest.raises(ValueError) as exc_info:
            part_from_dict(data)

        assert "Unknown part type" in str(exc_info.value)

    def test_part_from_dict_missing_type(self) -> None:
        """Test error on missing part type."""
        data = {"text": "test"}

        with pytest.raises(ValueError) as exc_info:
            part_from_dict(data)

        assert "Missing 'part_type'" in str(exc_info.value)

    def test_message_to_dict(self) -> None:
        """Test converting message to dict."""
        message = MessageContent(
            role=MessageRole.USER,
            parts=[TextPart(text="Test")],
        )
        data = message_to_dict(message)

        assert data["role"] == "user"
        assert len(data["parts"]) == 1
        assert "timestamp" in data

    def test_message_from_dict(self) -> None:
        """Test creating message from dict."""
        data = {
            "message_id": "msg_123",
            "role": "assistant",
            "parts": [{"part_type": "text", "text": "Response"}],
            "timestamp": "2024-01-01T12:00:00+00:00",
        }
        message = message_from_dict(data)

        assert message.message_id == "msg_123"
        assert message.role == MessageRole.ASSISTANT
        assert len(message.parts) == 1

    def test_message_roundtrip(self) -> None:
        """Test message serialization roundtrip."""
        original = MessageContent(
            role=MessageRole.ASSISTANT,
            parts=[
                TextPart(text="Hello"),
                ReasoningPart(text="Thinking...", time={"start": 0}),
            ],
        )

        data = message_to_dict(original)
        restored = message_from_dict(data)

        assert restored.role == original.role
        assert len(restored.parts) == len(original.parts)
        assert cast("TextPart", restored.parts[0]).text == cast("TextPart", original.parts[0]).text


class TestMessageRole:
    """Tests for MessageRole enum."""

    def test_all_roles_defined(self) -> None:
        """Test all expected roles are defined."""
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.SYSTEM.value == "system"


class TestFilePartSources:
    """Tests for file part sources."""

    def test_file_source(self) -> None:
        """Test FileSource creation."""
        source = FileSource(
            path="/project/file.txt",
            text={"value": "content", "start": 0, "end": 7},
        )

        assert source.type == "file"
        assert source.path == "/project/file.txt"

    def test_symbol_source(self) -> None:
        """Test SymbolSource creation."""
        source = SymbolSource(
            path="/project/file.py",
            range={"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 10}},
            name="function_name",
            kind=12,  # Function
        )

        assert source.type == "symbol"
        assert source.name == "function_name"

    def test_resource_source(self) -> None:
        """Test ResourceSource creation."""
        source = ResourceSource(
            client_name="VSCode",
            uri="file:///project/file.txt",
        )

        assert source.type == "resource"
        assert source.client_name == "VSCode"
