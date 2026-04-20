"""Unit tests for Textual TUI models.

Tests cover:
    - DebugItem creation and properties
    - MessageItem toggle/expand/collapse
    - from_payload factory method
"""

from __future__ import annotations

from polaris.delivery.cli.textual.models import DebugItem, MessageItem, MessageType


class TestDebugItem:
    """Test DebugItem model."""

    def test_creation_defaults_to_collapsed(self) -> None:
        """DebugItem must default to collapsed state."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        assert item.is_collapsed is True

    def test_marker_collapsed(self) -> None:
        """Collapsed DebugItem must show [▶]."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        assert item.is_collapsed is True
        assert item.marker == "[▶]"

    def test_marker_expanded(self) -> None:
        """Expanded DebugItem must show [▼]."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        item.expand()
        assert item.is_collapsed is False
        assert item.marker == "[▼]"

    def test_toggle(self) -> None:
        """toggle() must flip collapsed state."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        assert item.is_collapsed is True
        item.toggle()
        assert item.is_collapsed is False
        item.toggle()
        assert item.is_collapsed is True

    def test_expand(self) -> None:
        """expand() must set is_collapsed to False."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        item.expand()
        assert item.is_collapsed is False

    def test_collapse(self) -> None:
        """collapse() must set is_collapsed to True."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="test content",
        )
        item.expand()
        item.collapse()
        assert item.is_collapsed is True

    def test_title_with_all_parts(self) -> None:
        """title must include category, label, source, and tags."""
        item = DebugItem(
            id="debug-1",
            category="fs",
            label="read",
            source="kernelone",
            tags={"file": "config.json", "size": 1024},
            content="test",
        )
        title = item.title
        assert "[fs]" in title
        assert "[read]" in title
        assert "[kernelone]" in title
        assert "file=config.json" in title
        assert "size=1024" in title

    def test_title_without_source(self) -> None:
        """title must work without source."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="response",
            source="",
            tags={},
            content="test",
        )
        title = item.title
        assert "[llm]" in title
        assert "[response]" in title
        assert "kernelone" not in title

    def test_line_count_single_line(self) -> None:
        """line_count must return 1 for single line content."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="single line",
        )
        assert item.line_count == 1

    def test_line_count_multi_line(self) -> None:
        """line_count must return correct count for multi-line content."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="line1\nline2\nline3",
        )
        assert item.line_count == 3

    def test_line_count_empty(self) -> None:
        """line_count must return 0 for empty content."""
        item = DebugItem(
            id="debug-1",
            category="llm",
            label="request",
            source="openai",
            tags={},
            content="",
        )
        assert item.line_count == 0

    def test_from_payload_dict(self) -> None:
        """from_payload must format dict as JSON."""
        item = DebugItem.from_payload(
            id="debug-1",
            category="tool",
            label="execute",
            source="kernelone",
            tags={"cmd": "ls"},
            payload={"file": "test.py", "bytes": 100},
        )
        assert item.id == "debug-1"
        assert item.category == "tool"
        assert item.label == "execute"
        assert item.source == "kernelone"
        assert '"file": "test.py"' in item.content
        assert '"bytes": 100' in item.content

    def test_from_payload_string(self) -> None:
        """from_payload must preserve string payload."""
        item = DebugItem.from_payload(
            id="debug-1",
            category="llm",
            label="response",
            source="openai",
            tags={},
            payload="plain text response",
        )
        assert item.content == "plain text response"

    def test_from_payload_none(self) -> None:
        """from_payload must handle None payload."""
        item = DebugItem.from_payload(
            id="debug-1",
            category="llm",
            label="response",
            source="openai",
            tags={},
            payload=None,
        )
        assert item.content == ""

    def test_from_payload_other(self) -> None:
        """from_payload must convert non-string/dict to string."""
        item = DebugItem.from_payload(
            id="debug-1",
            category="tool",
            label="result",
            source="kernelone",
            tags={},
            payload=42,
        )
        assert item.content == "42"


class TestMessageItem:
    """Test MessageItem model."""

    def test_creation(self) -> None:
        """MessageItem must be created with required fields."""
        item = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="User input",
            content="Hello world",
        )
        assert item.id == "msg-1"
        assert item.type == MessageType.USER
        assert item.title == "User input"
        assert item.content == "Hello world"

    def test_marker_expanded(self) -> None:
        """Expanded MessageItem must show [▼]."""
        item = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="User input",
            content="Hello",
            is_collapsed=False,
        )
        assert item.marker == "[▼]"

    def test_marker_collapsed(self) -> None:
        """Collapsed MessageItem must show [▶]."""
        item = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="User input",
            content="Hello",
            is_collapsed=True,
        )
        assert item.marker == "[▶]"

    def test_toggle(self) -> None:
        """toggle() must flip collapsed state."""
        item = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="User input",
            content="Hello",
            is_collapsed=False,
        )
        item.toggle()
        assert item.is_collapsed is True


class TestMessageType:
    """Test MessageType enum."""

    def test_all_types_exist(self) -> None:
        """All expected message types must exist."""
        expected = {
            "USER",
            "ASSISTANT",
            "THINKING",
            "TOOL_CALL",
            "TOOL_RESULT",
            "DEBUG",
            "SYSTEM",
            "ERROR",
            "METADATA",
        }
        actual = {t.name for t in MessageType}
        assert expected.issubset(actual)

    def test_type_values(self) -> None:
        """Message types must have correct string values."""
        assert MessageType.USER.value == "user"
        assert MessageType.ASSISTANT.value == "assistant"
        assert MessageType.ERROR.value == "error"
