"""Tests for polaris.delivery.cli.visualization.theme."""

from __future__ import annotations

from polaris.delivery.cli.textual.models import MessageType
from polaris.delivery.cli.visualization.theme import (
    ConsoleTheme,
    DiffTheme,
    MessageTheme,
    get_theme,
    list_themes,
)


class TestMessageTheme:
    def test_for_type_user(self) -> None:
        theme = MessageTheme.for_type(MessageType.USER)
        assert theme.msg_type == MessageType.USER
        assert theme.label == "USER"
        assert theme.color == "cyan"
        assert theme.default_collapsed is False

    def test_for_type_assistant(self) -> None:
        theme = MessageTheme.for_type(MessageType.ASSISTANT)
        assert theme.label == "AI"
        assert theme.color == "green"

    def test_for_type_thinking(self) -> None:
        theme = MessageTheme.for_type(MessageType.THINKING)
        assert theme.label == "THINK"
        assert theme.default_collapsed is True

    def test_for_type_tool_call(self) -> None:
        theme = MessageTheme.for_type(MessageType.TOOL_CALL)
        assert theme.label == "TOOL"
        assert theme.color == "blue"

    def test_for_type_error(self) -> None:
        theme = MessageTheme.for_type(MessageType.ERROR)
        assert theme.label == "ERROR"
        assert theme.color == "red"
        assert theme.style == "bold"
        assert theme.default_collapsed is False

    def test_for_type_unknown_fallback(self) -> None:
        theme = MessageTheme.for_type(MessageType.METADATA)
        assert theme.label == "META"


class TestDiffTheme:
    def test_defaults(self) -> None:
        theme = DiffTheme()
        assert theme.add_color == "green"
        assert theme.delete_color == "red"
        assert theme.context_color == "white"
        assert theme.add_prefix == "+"
        assert theme.delete_prefix == "-"
        assert theme.context_prefix == " "


class TestConsoleTheme:
    def test_default_theme(self) -> None:
        theme = ConsoleTheme.default()
        assert theme.name == "default"
        assert theme.message_themes is not None
        assert len(theme.message_themes) == len(MessageType)
        assert theme.diff_theme is not None

    def test_minimal_theme(self) -> None:
        theme = ConsoleTheme.minimal()
        assert theme.name == "minimal"
        assert theme.message_themes is not None
        for mt in theme.message_themes.values():
            assert mt.color == ""

    def test_dark_theme(self) -> None:
        theme = ConsoleTheme.dark()
        assert theme.name == "dark"

    def test_get_message_theme(self) -> None:
        theme = ConsoleTheme.default()
        mt = theme.get_message_theme(MessageType.USER)
        assert mt.label == "USER"

    def test_get_message_theme_fallback(self) -> None:
        theme = ConsoleTheme(name="empty", message_themes={})
        mt = theme.get_message_theme(MessageType.USER)
        assert mt.label == "USER"

    def test_get_fold_marker_collapsed(self) -> None:
        theme = ConsoleTheme.default()
        assert theme.get_fold_marker(True) == "[▶]"

    def test_get_fold_marker_expanded(self) -> None:
        theme = ConsoleTheme.default()
        assert theme.get_fold_marker(False) == "[▼]"

    def test_get_type_label(self) -> None:
        theme = ConsoleTheme.default()
        assert theme.get_type_label(MessageType.USER) == "USER"

    def test_get_type_color(self) -> None:
        theme = ConsoleTheme.default()
        assert theme.get_type_color(MessageType.USER) == "cyan"


class TestGetTheme:
    def test_default(self) -> None:
        theme = get_theme("default")
        assert theme.name == "default"

    def test_minimal(self) -> None:
        theme = get_theme("minimal")
        assert theme.name == "minimal"

    def test_unknown_fallback(self) -> None:
        theme = get_theme("nonexistent")
        assert theme.name == "default"


class TestListThemes:
    def test_returns_list(self) -> None:
        themes = list_themes()
        assert isinstance(themes, list)
        assert "default" in themes
        assert "minimal" in themes
        assert "dark" in themes
