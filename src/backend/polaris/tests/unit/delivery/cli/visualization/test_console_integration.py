"""Tests for polaris.delivery.cli.visualization.console_integration."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from polaris.delivery.cli.textual.models import MessageItem, MessageType
from polaris.delivery.cli.visualization.console_integration import (
    DebugMessage,
    VisualConsoleMixin,
    create_debug_handler,
)
from polaris.delivery.cli.visualization.keyboard import FoldShortcut


class TestDebugMessage:
    def test_defaults(self) -> None:
        debug = DebugMessage()
        assert debug.category == "debug"
        assert debug.label == "event"
        assert debug.source == ""
        assert debug.tags == {}
        assert debug.payload is None
        assert isinstance(debug.timestamp, datetime)

    def test_to_message_item(self) -> None:
        debug = DebugMessage(
            category="fs",
            label="read",
            source="kernelone",
            tags={"file": "test.py"},
            payload="data",
        )
        item = debug.to_message_item()
        assert item.type == MessageType.DEBUG
        assert "fs" in item.title
        assert "read" in item.title
        assert "kernelone" in item.title
        assert item.content.text == "data"
        assert item.is_collapsed is True

    def test_to_message_item_no_source(self) -> None:
        debug = DebugMessage(category="test", label="event")
        item = debug.to_message_item()
        assert "[test][event]" in item.title

    def test_to_message_item_with_tags(self) -> None:
        debug = DebugMessage(tags={"a": 1, "b": 2})
        item = debug.to_message_item()
        assert "a=1" in item.title
        assert "b=2" in item.title

    def test_to_message_item_none_payload(self) -> None:
        debug = DebugMessage(payload=None)
        item = debug.to_message_item()
        assert item.content.text == ""


class TestVisualConsoleMixin:
    def test_init(self) -> None:
        console = VisualConsoleMixin()
        assert console.theme is not None
        assert console.message_group is not None

    def test_add_message(self) -> None:
        console = VisualConsoleMixin()
        item = MagicMock()
        console.add_message(item)
        assert len(console.message_group.items) == 1

    def test_add_debug(self) -> None:
        console = VisualConsoleMixin()
        item = console.add_debug(category="fs", label="read", payload="data")
        assert item.type == MessageType.DEBUG
        assert len(console.message_group.items) == 1

    def test_add_debug_no_tags(self) -> None:
        console = VisualConsoleMixin()
        item = console.add_debug()
        assert item.type == MessageType.DEBUG

    def test_get_debug_count(self) -> None:
        console = VisualConsoleMixin()
        console.add_debug()
        console.add_debug()
        console.add_message(MessageItem(id="1", type=MessageType.USER, title="T", content="C"))
        assert console.get_debug_count() == 2

    def test_clear(self) -> None:
        console = VisualConsoleMixin()
        console.add_debug()
        console.clear()
        assert len(console.message_group.items) == 0

    def test_expand_all_debug(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=True)
        console.add_message(item)
        console.expand_all_debug()
        assert item.is_collapsed is False

    def test_collapse_all_debug(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=False)
        console.add_message(item)
        console.collapse_all_debug()
        assert item.is_collapsed is True

    def test_toggle_debug_visible_expand(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=True)
        console.add_message(item)
        console.toggle_debug_visible()
        assert item.is_collapsed is False

    def test_toggle_debug_visible_collapse(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=False)
        console.add_message(item)
        console.toggle_debug_visible()
        assert item.is_collapsed is True

    def test_toggle_debug_visible_no_debug(self) -> None:
        console = VisualConsoleMixin()
        # Should not raise
        console.toggle_debug_visible()

    def test_handle_shortcut_expand_debug(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=True)
        console.add_message(item)
        result = console.handle_shortcut(FoldShortcut.EXPAND_ALL_DEBUG)
        assert result is True
        assert item.is_collapsed is False

    def test_handle_shortcut_collapse_debug(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.DEBUG, title="T", content="C", is_collapsed=False)
        console.add_message(item)
        result = console.handle_shortcut(FoldShortcut.COLLAPSE_ALL_DEBUG)
        assert result is True
        assert item.is_collapsed is True

    def test_handle_shortcut_expand_all(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.USER, title="T", content="C", is_collapsed=True)
        console.add_message(item)
        result = console.handle_shortcut(FoldShortcut.EXPAND_ALL)
        assert result is True
        assert item.is_collapsed is False

    def test_handle_shortcut_collapse_all(self) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.USER, title="T", content="C", is_collapsed=False)
        console.add_message(item)
        result = console.handle_shortcut(FoldShortcut.COLLAPSE_ALL)
        assert result is True
        assert item.is_collapsed is True

    def test_handle_shortcut_unknown(self) -> None:
        console = VisualConsoleMixin()
        result = console.handle_shortcut(FoldShortcut.SEARCH)
        assert result is False

    def test_print_message_item_collapsed(self, capsys) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.USER, title="Title", content="Body", is_collapsed=True)
        console.print_message_item(item)
        captured = capsys.readouterr()
        assert "Title" in captured.out
        assert "Body" not in captured.out

    def test_print_message_item_expanded(self, capsys) -> None:
        console = VisualConsoleMixin()
        item = MessageItem(id="1", type=MessageType.USER, title="Title", content="Body", is_collapsed=False)
        console.print_message_item(item)
        captured = capsys.readouterr()
        assert "Title" in captured.out
        assert "Body" in captured.out

    def test_print_all_messages(self, capsys) -> None:
        console = VisualConsoleMixin()
        console.add_message(MessageItem(id="1", type=MessageType.USER, title="T1", content="C1", is_collapsed=True))
        console.add_message(MessageItem(id="2", type=MessageType.USER, title="T2", content="C2", is_collapsed=True))
        console.print_all_messages()
        captured = capsys.readouterr()
        assert "T1" in captured.out
        assert "T2" in captured.out


class TestCreateDebugHandler:
    def test_handler(self) -> None:
        console = VisualConsoleMixin()
        handler = create_debug_handler(console)
        handler(
            {
                "category": "fs",
                "label": "read",
                "source": "kernelone",
                "tags": {"file": "test.py"},
                "payload": "data",
            }
        )
        assert console.get_debug_count() == 1

    def test_handler_defaults(self) -> None:
        console = VisualConsoleMixin()
        handler = create_debug_handler(console)
        handler({})
        assert console.get_debug_count() == 1
