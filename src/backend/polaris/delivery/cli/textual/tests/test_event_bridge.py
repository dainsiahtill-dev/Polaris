"""Unit tests for Textual Event Bridge.

Tests cover:
    - EventStreamConfig dataclass
    - Helper functions (format_payload_as_json, extract_debug_info)
    - TextualEventBridge event processing (sync parts)
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.cli.textual.event_bridge import (
    EventStreamConfig,
    TextualEventBridge,
    extract_debug_info,
    format_payload_as_json,
)


class MockApp:
    """Mock PolarisTextualConsole for testing."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.debugs: list[dict] = []
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.errors: list[str] = []

    def add_message(self, content: str, msg_type: str = "assistant") -> None:
        self.messages.append((msg_type, content))

    def add_debug(
        self,
        category: str,
        label: str,
        source: str = "",
        tags=None,
        payload=None,
    ) -> None:
        self.debugs.append(
            {
                "category": category,
                "label": label,
                "source": source,
                "tags": tags or {},
                "payload": payload,
            }
        )

    def add_tool_call(self, tool: str, args=None) -> None:
        self.tool_calls.append({"tool": tool, "args": args})

    def add_tool_result(self, tool: str, result=None, success: bool = True) -> None:
        self.tool_results.append({"tool": tool, "result": result, "success": success})

    def add_error(self, message: str) -> None:
        self.errors.append(message)


class TestEventStreamConfig:
    """Test EventStreamConfig dataclass."""

    def test_creation(self) -> None:
        """EventStreamConfig must accept required fields."""
        config = EventStreamConfig(
            workspace="/tmp",
            role="director",
            session_id="sess-1",
        )
        assert config.workspace == "/tmp"
        assert config.role == "director"
        assert config.session_id == "sess-1"
        assert config.debug_enabled is True

    def test_defaults(self) -> None:
        """EventStreamConfig must have correct defaults."""
        config = EventStreamConfig(workspace="/tmp", role="pm")
        assert config.session_id is None
        assert config.debug_enabled is True

    def test_debug_disabled(self) -> None:
        """debug_enabled can be set to False."""
        config = EventStreamConfig(workspace="/tmp", role="director", debug_enabled=False)
        assert config.debug_enabled is False


class TestFormatPayloadAsJson:
    """Test format_payload_as_json helper."""

    def test_dict_payload(self) -> None:
        """format_payload_as_json must format dict as JSON."""
        result = format_payload_as_json({"key": "value", "num": 42})
        assert '"key": "value"' in result
        assert '"num": 42' in result

    def test_string_payload(self) -> None:
        """format_payload_as_json must return string as-is."""
        result = format_payload_as_json("plain text")
        assert result == "plain text"

    def test_none_payload(self) -> None:
        """format_payload_as_json must return empty string for None."""
        result = format_payload_as_json(None)
        assert result == ""

    def test_other_payload(self) -> None:
        """format_payload_as_json must wrap other types."""
        result = format_payload_as_json(123)
        assert "123" in result or "value" in result

    def test_nested_dict(self) -> None:
        """format_payload_as_json must handle nested dicts."""
        result = format_payload_as_json({"outer": {"inner": "value"}})
        assert '"outer"' in result
        assert '"inner"' in result


class TestExtractDebugInfo:
    """Test extract_debug_info helper."""

    def test_full_event(self) -> None:
        """extract_debug_info must extract all fields."""
        event = {
            "type": "debug",
            "data": {
                "payload": {
                    "category": "llm",
                    "label": "request",
                    "source": "openai",
                    "tags": {"model": "gpt-4"},
                    "payload": {"tokens": 100},
                }
            },
        }
        info = extract_debug_info(event)
        assert info["category"] == "llm"
        assert info["label"] == "request"
        assert info["source"] == "openai"
        assert info["tags"] == {"model": "gpt-4"}

    def test_minimal_event(self) -> None:
        """extract_debug_info must handle missing fields."""
        event = {"type": "debug", "data": {}}
        info = extract_debug_info(event)
        assert info["category"] == "debug"
        assert info["label"] == "event"
        assert info["source"] == ""
        assert info["tags"] == {}

    def test_no_data_key(self) -> None:
        """extract_debug_info must handle missing data key."""
        event: dict[str, Any] = {}
        info = extract_debug_info(event)
        assert info["category"] == "debug"
        assert info["label"] == "event"


class TestTextualEventBridge:
    """Test TextualEventBridge class."""

    @pytest.fixture
    def mock_app(self) -> MockApp:
        """Create a mock app for testing."""
        return MockApp()

    @pytest.fixture
    def bridge(self, mock_app: MockApp) -> TextualEventBridge:
        """Create a bridge for testing."""
        config = EventStreamConfig(workspace="/tmp", role="director")
        return TextualEventBridge(mock_app, config)

    @pytest.mark.asyncio
    async def test_process_event_content_chunk(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add assistant message for content_chunk."""
        await bridge._process_event({"type": "content_chunk", "data": {"content": "Hello!"}})
        assert len(mock_app.messages) == 1
        assert mock_app.messages[0] == ("assistant", "Hello!")

    @pytest.mark.asyncio
    async def test_process_event_content_chunk_empty(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must not add message for empty content_chunk."""
        await bridge._process_event({"type": "content_chunk", "data": {"content": ""}})
        assert len(mock_app.messages) == 0

    @pytest.mark.asyncio
    async def test_process_event_thinking_chunk(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add debug for thinking_chunk."""
        await bridge._process_event({"type": "thinking_chunk", "data": {"content": "Let me think..."}})
        assert len(mock_app.debugs) == 1
        assert mock_app.debugs[0]["category"] == "llm"
        assert mock_app.debugs[0]["label"] == "thinking"
        assert mock_app.debugs[0]["source"] == "model"

    @pytest.mark.asyncio
    async def test_process_event_tool_call(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add tool call."""
        await bridge._process_event(
            {"type": "tool_call", "data": {"tool": "ReadFile", "args": {"path": "/tmp/test.txt"}}}
        )
        assert len(mock_app.tool_calls) == 1
        assert mock_app.tool_calls[0]["tool"] == "ReadFile"
        assert mock_app.tool_calls[0]["args"] == {"path": "/tmp/test.txt"}

    @pytest.mark.asyncio
    async def test_process_event_tool_call_no_args(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must handle tool_call with non-dict args."""
        await bridge._process_event({"type": "tool_call", "data": {"tool": "ReadFile", "args": "not a dict"}})
        assert len(mock_app.tool_calls) == 1
        assert mock_app.tool_calls[0]["args"] is None

    @pytest.mark.asyncio
    async def test_process_event_tool_result(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add tool result."""
        await bridge._process_event(
            {
                "type": "tool_result",
                "data": {"tool": "ReadFile", "result": {"content": "file contents"}, "success": True},
            }
        )
        assert len(mock_app.tool_results) == 1
        assert mock_app.tool_results[0]["tool"] == "ReadFile"
        assert mock_app.tool_results[0]["success"] is True

    @pytest.mark.asyncio
    async def test_process_event_tool_result_failed(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must handle failed tool result."""
        await bridge._process_event(
            {"type": "tool_result", "data": {"tool": "ReadFile", "result": {}, "success": False}}
        )
        assert mock_app.tool_results[0]["success"] is False

    @pytest.mark.asyncio
    async def test_process_event_error(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add error."""
        await bridge._process_event({"type": "error", "data": {"error": "Something went wrong"}})
        assert len(mock_app.errors) == 1
        assert "Something went wrong" in mock_app.errors[0]

    @pytest.mark.asyncio
    async def test_process_event_error_message_field(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must also read 'message' field for errors."""
        await bridge._process_event({"type": "error", "data": {"message": "Another error"}})
        assert len(mock_app.errors) == 1
        assert "Another error" in mock_app.errors[0]

    @pytest.mark.asyncio
    async def test_process_event_complete_with_content(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must add message for complete with content."""
        await bridge._process_event({"type": "complete", "data": {"content": "Final response", "thinking": ""}})
        assert len(mock_app.messages) == 1
        assert mock_app.messages[0] == ("assistant", "Final response")

    @pytest.mark.asyncio
    async def test_process_event_complete_with_thinking_only(
        self, bridge: TextualEventBridge, mock_app: MockApp
    ) -> None:
        """_process_event must add debug for complete with thinking only (no content)."""
        await bridge._process_event({"type": "complete", "data": {"thinking": "My thoughts", "content": ""}})
        assert len(mock_app.debugs) == 1
        assert mock_app.debugs[0]["category"] == "llm"
        assert mock_app.debugs[0]["label"] == "thinking"

    @pytest.mark.asyncio
    async def test_process_event_debug_event(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must handle debug events."""
        await bridge._process_event(
            {
                "type": "debug",
                "data": {
                    "payload": {
                        "category": "fs",
                        "label": "read",
                        "source": "kernelone",
                        "tags": {"file": "config.json"},
                        "payload": {"size": 1024},
                    }
                },
            }
        )
        assert len(mock_app.debugs) == 1
        assert mock_app.debugs[0]["category"] == "fs"
        assert mock_app.debugs[0]["label"] == "read"

    @pytest.mark.asyncio
    async def test_process_event_unknown_type(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must handle unknown event types gracefully."""
        await bridge._process_event({"type": "unknown_type", "data": {"something": "else"}})
        # No messages should be added for unknown types
        assert len(mock_app.messages) == 0
        assert len(mock_app.debugs) == 0
        assert len(mock_app.errors) == 0

    @pytest.mark.asyncio
    async def test_process_event_empty_type(self, bridge: TextualEventBridge, mock_app: MockApp) -> None:
        """_process_event must handle missing type field."""
        await bridge._process_event({"data": {}})
        # Should not raise, no messages added
        assert len(mock_app.messages) == 0
