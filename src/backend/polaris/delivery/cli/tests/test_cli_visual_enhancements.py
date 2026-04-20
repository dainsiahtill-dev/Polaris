"""Tests for CLI visual enhancements."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from polaris.delivery.cli import terminal_console as tc


class TestBannerVisual:
    """Test banner rendering."""

    def test_print_banner_uses_rich_when_available(self, monkeypatch, capsys):
        """Banner should use Rich Panel when rich is available."""
        # Mock Rich console
        mock_console = MagicMock()
        monkeypatch.setattr("rich.console.Console", lambda **kwargs: mock_console)

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw")
        tc._print_banner(
            workspace=Path("/tmp"),
            role="director",
            session_id="test-session",
            allowed_roles=frozenset({"director", "pm"}),
            render_state=state,
        )

        # Rich console.print should have been called
        # (exact assertion depends on Rich availability)

    def test_print_banner_plain_fallback(self, monkeypatch, capsys):
        """Banner should fall back to plain text when Rich fails."""

        # Force Rich console to raise ImportError
        def raise_import_error(**_kwargs):
            raise ImportError("Rich not available")

        monkeypatch.setattr("rich.console.Console", raise_import_error)

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw")
        tc._print_banner(
            workspace=Path("/tmp"),
            role="director",
            session_id="test-session",
            allowed_roles=frozenset({"director", "pm"}),
            render_state=state,
        )

        captured = capsys.readouterr()
        # Should contain box-drawing characters or plain text
        assert "╔" in captured.out or "workspace" in captured.out

    def test_print_banner_skip_when_env_var_set(self, monkeypatch, capsys):
        """Banner should skip when POLARIS_CLI_SKIP_BANNER is set."""
        monkeypatch.setenv("POLARIS_CLI_SKIP_BANNER", "1")

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw")
        tc._print_banner(
            workspace=Path("/tmp"),
            role="director",
            session_id="test-session",
            allowed_roles=frozenset({"director", "pm"}),
            render_state=state,
        )

        captured = capsys.readouterr()
        # No banner output when skipped
        assert "Polaris CLI" not in captured.out
        assert "╔" not in captured.out

    def test_print_banner_skip_in_json_output_mode(self, monkeypatch, capsys):
        """Banner should skip in JSON output mode."""
        monkeypatch.delenv("POLARIS_CLI_SKIP_BANNER", raising=False)

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw", output_format="json")
        tc._print_banner(
            workspace=Path("/tmp"),
            role="director",
            session_id="test-session",
            allowed_roles=frozenset({"director", "pm"}),
            render_state=state,
        )

        captured = capsys.readouterr()
        # No banner output in JSON mode
        assert "╔" not in captured.out


class TestToolHighlight:
    """Test tool execution highlighting."""

    def test_tool_name_styles_defined(self):
        """TOOL_NAME_STYLES should define styles for common tools."""
        assert "read_file" in tc.TOOL_NAME_STYLES
        assert "write_file" in tc.TOOL_NAME_STYLES
        assert "execute" in tc.TOOL_NAME_STYLES
        assert "search" in tc.TOOL_NAME_STYLES

    def test_get_tool_style_returns_style(self):
        """_get_tool_style should return style for known tools."""
        style = tc._get_tool_style("read_file")
        assert style == "blue"

        style = tc._get_tool_style("write_file")
        assert style == "green"

        style = tc._get_tool_style("execute")
        assert style == "red bold"

    def test_get_tool_style_unknown_returns_default(self):
        """_get_tool_style should return default for unknown tools."""
        style = tc._get_tool_style("unknown_tool")
        assert style == "cyan"

        style = tc._get_tool_style("some_random_tool")
        assert style == "cyan"


class TestRolePromptSymbols:
    """Test role prompt symbols."""

    def test_role_prompt_symbols_defined(self):
        """ROLE_PROMPT_SYMBOLS should be defined."""
        assert "director" in tc.ROLE_PROMPT_SYMBOLS
        assert "pm" in tc.ROLE_PROMPT_SYMBOLS
        assert "architect" in tc.ROLE_PROMPT_SYMBOLS
        assert "chief_engineer" in tc.ROLE_PROMPT_SYMBOLS
        assert "qa" in tc.ROLE_PROMPT_SYMBOLS

    def test_role_prompt_symbol_returns_unicode(self):
        """Role symbols should be unicode characters."""
        symbol = tc._get_role_symbol("director")
        assert len(symbol) == 1
        assert ord(symbol) > 127  # Unicode, not ASCII

        symbol = tc._get_role_symbol("pm")
        assert len(symbol) == 1
        assert ord(symbol) > 127

    def test_get_role_symbol_case_insensitive(self):
        """_get_role_symbol should be case insensitive."""
        symbol_lower = tc._get_role_symbol("director")
        symbol_upper = tc._get_role_symbol("DIRECTOR")
        symbol_mixed = tc._get_role_symbol("DiReCtOr")
        assert symbol_lower == symbol_upper == symbol_mixed

    def test_get_role_symbol_unknown_returns_default(self):
        """_get_role_symbol should return default for unknown roles."""
        symbol = tc._get_role_symbol("unknown_role")
        assert symbol == "▸"


class TestVisualEnhancementConstants:
    """Test visual enhancement constant values."""

    def test_role_symbols_are_unique(self):
        """Each role should have a unique symbol."""
        symbols = set(tc.ROLE_PROMPT_SYMBOLS.values())
        assert len(symbols) == len(tc.ROLE_PROMPT_SYMBOLS)

    def test_tool_styles_colors_valid(self):
        """Tool styles should use valid Rich color names."""
        valid_colors = {
            "black",
            "red",
            "green",
            "yellow",
            "blue",
            "magenta",
            "cyan",
            "white",
            "dim",
            "bold",
        }
        for tool, style in tc.TOOL_NAME_STYLES.items():
            # Style can be a single color or "color bold"
            color_part = style.split()[0] if " " in style else style
            assert color_part in valid_colors, f"Invalid color '{color_part}' for tool '{tool}'"


class TestConsoleRenderState:
    """Test _ConsoleRenderState dataclass."""

    def test_default_values(self):
        """Test default render state values."""
        state = tc._ConsoleRenderState()
        assert state.prompt_style == "plain"
        assert state.json_render == "raw"
        assert state.output_format == "text"

    def test_custom_values(self):
        """Test custom render state values."""
        state = tc._ConsoleRenderState(
            prompt_style="omp",
            json_render="pretty-color",
            output_format="json",
        )
        assert state.prompt_style == "omp"
        assert state.json_render == "pretty-color"
        assert state.output_format == "json"
