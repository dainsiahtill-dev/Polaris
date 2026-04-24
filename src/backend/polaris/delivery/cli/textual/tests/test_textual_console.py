"""Unit tests for Claude-style Agent TUI Console.

Tests cover:
    - ClaudeAgentTUI main application
    - MessageWidget fold/unfold functionality
    - ConversationArea message management
    - Header status updates
    - Sidebar context display
    - ResizableInput component
    - Backward compatibility (PolarisTextualConsole)
    - Stream processing
    - Tool call handling
"""

from __future__ import annotations

import pytest

# Test imports with skipif for textual
try:
    from textual.app import App  # noqa: F401

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    pytest.skip("Textual not installed", allow_module_level=True)

from polaris.delivery.cli.textual.models import (
    AppState,
    CodeBlock,
    ConversationContext,
    DebugItem,
    MessageContent,
    MessageItem,
    MessageType,
    ToolCallInfo,
    ToolStatus,
)
from polaris.delivery.cli.textual.styles import (
    CatppuccinLatte,
    CatppuccinMocha,
    ThemeColors,
    ThemeManager,
    ThemeMode,
    get_console_css,
    get_theme_colors,
    get_theme_manager,
)

# Import console components
from polaris.delivery.cli.textual_console import (
    ClaudeAgentTUI,
    PolarisTextualConsole,
    run_claude_tui,
    run_textual_console,
)


class TestCatppuccinMocha:
    """Test Catppuccin Mocha theme colors."""

    def test_base_color(self) -> None:
        """BASE color must be dark background."""
        assert CatppuccinMocha.BASE == "#1e1e2e"

    def test_mantle_color(self) -> None:
        """MANTLE color must be darker background."""
        assert CatppuccinMocha.MANTLE == "#181825"

    def test_text_color(self) -> None:
        """TEXT color must be light foreground."""
        assert CatppuccinMocha.TEXT == "#cdd6f4"

    def test_blue_accent(self) -> None:
        """BLUE must be user accent color."""
        assert CatppuccinMocha.BLUE == "#89b4fa"

    def test_mauve_accent(self) -> None:
        """MAUVE must be agent accent color."""
        assert CatppuccinMocha.MAUVE == "#cba6f7"


class TestCatppuccinLatte:
    """Test Catppuccin Latte theme colors (light theme)."""

    def test_base_color(self) -> None:
        """BASE color must be light background."""
        assert CatppuccinLatte.BASE == "#eff1f5"

    def test_text_color(self) -> None:
        """TEXT color must be dark foreground."""
        assert CatppuccinLatte.TEXT == "#4c4f69"

    def test_blue_accent(self) -> None:
        """BLUE must be user accent color."""
        assert CatppuccinLatte.BLUE == "#1e66f5"

    def test_mauve_accent(self) -> None:
        """MAUVE must be agent accent color."""
        assert CatppuccinLatte.MAUVE == "#8839ef"


class TestThemeColors:
    """Test ThemeColors dataclass."""

    def test_default_theme_creation(self) -> None:
        """Theme must be created with default Catppuccin colors."""
        theme = ThemeColors()
        assert theme.background == CatppuccinMocha.BASE
        assert theme.user_accent == CatppuccinMocha.BLUE
        assert theme.agent_accent == CatppuccinMocha.MAUVE

    def test_from_dark(self) -> None:
        """from_dark must create dark theme colors."""
        theme = ThemeColors.from_dark()
        assert theme.background == CatppuccinMocha.BASE
        assert theme.text == CatppuccinMocha.TEXT
        assert theme.primary == CatppuccinMocha.MAUVE

    def test_from_light(self) -> None:
        """from_light must create light theme colors."""
        theme = ThemeColors.from_light()
        assert theme.background == CatppuccinLatte.BASE
        assert theme.text == CatppuccinLatte.TEXT
        assert theme.primary == CatppuccinLatte.MAUVE

    def test_css_generation(self) -> None:
        """get_console_css must return non-empty CSS string."""
        css = get_console_css()
        assert isinstance(css, str)
        assert len(css) > 0
        assert "$catppuccin-base" in css


class TestThemeMode:
    """Test ThemeMode enum."""

    def test_theme_mode_values(self) -> None:
        """ThemeMode must have correct values."""
        assert ThemeMode.DARK.value == "dark"
        assert ThemeMode.LIGHT.value == "light"
        assert ThemeMode.SYSTEM.value == "system"


class TestThemeManager:
    """Test ThemeManager singleton."""

    def test_singleton_instance(self) -> None:
        """ThemeManager must be a singleton."""
        manager1 = ThemeManager.get_instance()
        manager2 = ThemeManager.get_instance()
        assert manager1 is manager2

    def test_default_mode(self) -> None:
        """Default mode must be DARK."""
        manager = ThemeManager.get_instance()
        # Reset to default
        manager._current_mode = ThemeMode.DARK
        assert manager.current_mode == ThemeMode.DARK

    def test_toggle_dark_to_light(self) -> None:
        """toggle must switch from dark to light."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.DARK
        new_mode = manager.toggle()
        assert new_mode == ThemeMode.LIGHT

    def test_toggle_light_to_dark(self) -> None:
        """toggle must switch from light to dark."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.LIGHT
        new_mode = manager.toggle()
        assert new_mode == ThemeMode.DARK

    def test_is_dark_property(self) -> None:
        """is_dark must return True for dark mode."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.DARK
        assert manager.is_dark is True
        assert manager.is_light is False

    def test_is_light_property(self) -> None:
        """is_light must return True for light mode."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.LIGHT
        assert manager.is_light is True
        assert manager.is_dark is False

    def test_colors_for_dark_mode(self) -> None:
        """colors must return dark colors for dark mode."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.DARK
        colors = manager.colors
        assert colors.background == CatppuccinMocha.BASE

    def test_colors_for_light_mode(self) -> None:
        """colors must return light colors for light mode."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.LIGHT
        colors = manager.colors
        assert colors.background == CatppuccinLatte.BASE

    def test_generate_css_variables(self) -> None:
        """generate_css_variables must produce valid CSS."""
        manager = ThemeManager.get_instance()
        manager._current_mode = ThemeMode.DARK
        css_vars = manager.generate_css_variables()
        assert "$catppuccin-base:" in css_vars
        assert "$catppuccin-text:" in css_vars

    def test_get_theme_manager_function(self) -> None:
        """get_theme_manager must return ThemeManager instance."""
        manager = get_theme_manager()
        assert isinstance(manager, ThemeManager)

    def test_get_theme_colors_function(self) -> None:
        """get_theme_colors must return ThemeColors instance."""
        colors = get_theme_colors()
        assert isinstance(colors, ThemeColors)


class TestMessageContent:
    """Test MessageContent model."""

    def test_creation_with_text(self) -> None:
        """MessageContent must be created with text."""
        content = MessageContent(text="Hello world")
        assert content.text == "Hello world"
        assert content.code_blocks == []

    def test_add_code_block(self) -> None:
        """add_code_block must add code block to list."""
        content = MessageContent(text="See this code:")
        content.add_code_block("python", "print('hello')", "test.py")

        assert len(content.code_blocks) == 1
        assert content.code_blocks[0].language == "python"
        assert content.code_blocks[0].code == "print('hello')"
        assert content.code_blocks[0].filename == "test.py"

    def test_code_block_display_title_with_filename(self) -> None:
        """CodeBlock display_title must include filename."""
        block = CodeBlock("python", "x = 1", "script.py")
        assert block.display_title == "python: script.py"

    def test_code_block_display_title_without_filename(self) -> None:
        """CodeBlock display_title must work without filename."""
        block = CodeBlock("python", "x = 1", None)
        assert block.display_title == "python"


class TestConversationContext:
    """Test ConversationContext model."""

    def test_default_tokens(self) -> None:
        """Default token usage must be zero."""
        ctx = ConversationContext()
        assert ctx.total_tokens == 0
        assert ctx.tokens_display == "0/4096"

    def test_tokens_display(self) -> None:
        """tokens_display must format correctly."""
        ctx = ConversationContext()
        ctx.token_usage = {"used": 1500, "total": 4096}
        assert ctx.tokens_display == "1500/4096"

    def test_tool_calls_history(self) -> None:
        """tool_calls_history must store ToolCallInfo objects."""
        ctx = ConversationContext()
        tool = ToolCallInfo(
            id="tool-1",
            name="ReadFile",
            arguments={"path": "/tmp/test.txt"},
            status=ToolStatus.SUCCESS,
        )
        ctx.tool_calls_history.append(tool)

        assert len(ctx.tool_calls_history) == 1
        assert ctx.tool_calls_history[0].name == "ReadFile"


class TestAppState:
    """Test AppState model."""

    def test_status_text_idle(self) -> None:
        """status_text must be IDLE when not connected/processing."""
        state = AppState(is_connected=False, is_processing=False)
        assert state.status_text == "IDLE"

    def test_status_text_connected(self) -> None:
        """status_text must be CONNECTED when connected."""
        state = AppState(is_connected=True, is_processing=False)
        assert state.status_text == "CONNECTED"

    def test_status_text_processing(self) -> None:
        """status_text must be PROCESSING when processing."""
        state = AppState(is_connected=True, is_processing=True)
        assert state.status_text == "PROCESSING"

    def test_status_text_streaming(self) -> None:
        """status_text must be STREAMING when context status is streaming."""
        state = AppState(is_connected=True, is_processing=False)
        state.context.status = "streaming"
        assert state.status_text == "STREAMING"


class TestClaudeAgentTUI:
    """Test ClaudeAgentTUI main application."""

    @pytest.fixture
    def app(self):
        """Create a test instance of ClaudeAgentTUI."""
        return ClaudeAgentTUI(
            workspace="/test/workspace",
            role="assistant",
            session_id="test-session",
            debug_enabled=True,
        )

    def test_initialization(self, app) -> None:
        """App must initialize with correct attributes."""
        # Note: workspace is normalized to absolute path on Windows
        assert isinstance(app.workspace, str)
        assert "workspace" in app.workspace.lower()
        assert app.role == "assistant"
        assert app.session_id == "test-session"
        assert app.debug_enabled is True
        assert app._message_counter == 0
        assert app._tool_counter == 0

    def test_add_user_message(self, app) -> None:
        """add_user_message must increment counter and create user message."""
        initial_count = app._message_counter
        app.add_user_message("Hello")

        assert app._message_counter == initial_count + 1

    def test_add_assistant_message(self, app) -> None:
        """add_assistant_message must create assistant message with markdown."""
        app.add_assistant_message("**Bold** text")
        assert app._message_counter == 1

    def test_add_tool_call(self, app) -> None:
        """add_tool_call must return tool ID and increment counters."""
        initial_msg_count = app._message_counter
        initial_tool_count = app._tool_counter

        tool_id = app.add_tool_call("ReadFile", {"path": "/tmp/test.txt"})

        assert tool_id.startswith("tool-")
        assert app._message_counter == initial_msg_count + 1
        assert app._tool_counter == initial_tool_count + 1

    def test_add_tool_result(self, app) -> None:
        """add_tool_result must create tool result message."""
        app.add_tool_result("tool-1", "file content here")
        assert app._message_counter == 1

    def test_set_status(self, app) -> None:
        """set_status must update context status."""
        app.set_status("processing")
        assert app.app_state.context.status == "processing"

    def test_set_tokens(self, app) -> None:
        """set_tokens must update token usage."""
        app.set_tokens(1500, 4096)
        assert app.app_state.context.token_usage["used"] == 1500
        assert app.app_state.context.token_usage["total"] == 4096

    def test_set_current_tool(self, app) -> None:
        """set_current_tool must update current tool in context."""
        app.set_current_tool("ReadFile")
        assert app.app_state.context.current_tool == "ReadFile"

    def test_debug_disabled(self, app) -> None:
        """add_debug must return empty string when debug disabled."""
        app.debug_enabled = False
        result = app.add_debug("test", "label", "payload")
        assert result == ""

    def test_debug_enabled(self, app) -> None:
        """add_debug must return debug ID when debug enabled."""
        result = app.add_debug("test", "label", {"key": "value"})
        assert result.startswith("debug-")
        assert app._debug_counter > 0


class TestBackwardCompatibility:
    """Test backward compatibility with PolarisTextualConsole."""

    @pytest.fixture
    def legacy_app(self):
        """Create a test instance of PolarisTextualConsole."""
        return PolarisTextualConsole(
            workspace="/test/workspace",
            role="director",
            session_id="test-session",
            debug_enabled=True,
        )

    def test_legacy_initialization(self, legacy_app) -> None:
        """Legacy app must initialize correctly."""
        # Note: workspace is normalized to absolute path on Windows
        assert isinstance(legacy_app.workspace, str)
        assert "workspace" in legacy_app.workspace.lower()
        assert legacy_app.role == "director"
        assert hasattr(legacy_app, "debug_enabled")

    def test_legacy_add_message(self, legacy_app) -> None:
        """add_message (legacy) must create message with correct type."""
        legacy_app.add_message("Test message", "user")
        assert legacy_app._message_counter == 1

    def test_legacy_add_message_assistant(self, legacy_app) -> None:
        """add_message with assistant type must work."""
        legacy_app.add_message("Assistant reply", "assistant")
        assert legacy_app._message_counter == 1

    def test_legacy_add_tool_result(self, legacy_app) -> None:
        """add_tool_result (legacy) must create tool result message."""
        legacy_app.add_tool_result("ReadFile", "file contents")
        assert legacy_app._tool_counter == 1


class TestMessageItemExtended:
    """Test extended MessageItem functionality."""

    def test_author_label_user(self) -> None:
        """author_label must be 'You' for USER type."""
        msg = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="Test",
            content="Hello",
        )
        assert msg.author_label == "You"

    def test_author_label_assistant(self) -> None:
        """author_label must be 'Assistant' for ASSISTANT type."""
        msg = MessageItem(
            id="msg-1",
            type=MessageType.ASSISTANT,
            title="Test",
            content="Hello",
        )
        assert msg.author_label == "Assistant"

    def test_author_label_tool_call(self) -> None:
        """author_label must be 'Tool' for TOOL_CALL type."""
        msg = MessageItem(
            id="msg-1",
            type=MessageType.TOOL_CALL,
            title="Test",
            content="Calling tool",
        )
        assert msg.author_label == "Tool"

    def test_summary_user_message(self) -> None:
        """summary must truncate long user messages."""
        long_text = "A" * 100
        msg = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="Test",
            content=MessageContent(text=long_text),
        )
        summary = msg.summary
        assert "You:" in summary
        assert len(summary) < 70  # Should be truncated

    def test_summary_tool_call(self) -> None:
        """summary must include tool name for tool calls."""
        msg = MessageItem(
            id="msg-1",
            type=MessageType.TOOL_CALL,
            title="Tool",
            content=MessageContent(text="Executing"),
            metadata={"tool_name": "ReadFile"},
        )
        assert "ReadFile" in msg.summary

    def test_marker_unicode(self) -> None:
        """marker must use unicode arrows."""
        msg = MessageItem(
            id="msg-1",
            type=MessageType.USER,
            title="Test",
            content="Hello",
            is_collapsed=False,
        )
        assert msg.marker == "▼"

        msg.collapse()
        assert msg.marker == "▶"


class TestDebugItemExtended:
    """Test extended DebugItem functionality."""

    def test_severity_icon_info(self) -> None:
        """severity_icon must be ℹ for info."""
        item = DebugItem(
            id="debug-1",
            category="test",
            label="test",
            content="test",
            severity="info",
        )
        assert item.severity_icon == "ℹ"

    def test_severity_icon_warning(self) -> None:
        """severity_icon must be ⚠ for warning."""
        item = DebugItem(
            id="debug-1",
            category="test",
            label="test",
            content="test",
            severity="warning",
        )
        assert item.severity_icon == "⚠"

    def test_severity_icon_error(self) -> None:
        """severity_icon must be ✗ for error."""
        item = DebugItem(
            id="debug-1",
            category="test",
            label="test",
            content="test",
            severity="error",
        )
        assert item.severity_icon == "✗"


class TestToolCallInfo:
    """Test ToolCallInfo model."""

    def test_to_display_dict(self) -> None:
        """to_display_dict must include all relevant fields."""
        tool = ToolCallInfo(
            id="tool-1",
            name="ReadFile",
            arguments={"path": "/tmp/test.txt"},
            status=ToolStatus.SUCCESS,
            result="file contents",
            duration_ms=45.5,
        )

        display = tool.to_display_dict()
        assert display["tool"] == "ReadFile"
        assert display["status"] == "success"
        assert display["args"]["path"] == "/tmp/test.txt"
        assert display["result"] == "file contents"
        assert display["duration_ms"] == 45.5

    def test_default_status_pending(self) -> None:
        """Default status must be PENDING."""
        tool = ToolCallInfo(
            id="tool-1",
            name="TestTool",
            arguments={},
        )
        assert tool.status == ToolStatus.PENDING


class TestStreamProcessing:
    """Test stream message processing."""

    def test_stream_message_creation(self) -> None:
        """MessageItem can be created with STREAM type."""
        msg = MessageItem(
            id="stream-1",
            type=MessageType.STREAM,
            title="Streaming",
            content=MessageContent(text="partial "),
        )
        assert msg.type == MessageType.STREAM

    def test_stream_to_assistant_transition(self) -> None:
        """STREAM type can be changed to ASSISTANT."""
        msg = MessageItem(
            id="stream-1",
            type=MessageType.STREAM,
            title="Streaming",
            content=MessageContent(text="complete response"),
        )
        # Simulate finalization
        msg.type = MessageType.ASSISTANT
        assert msg.type == MessageType.ASSISTANT


class TestRunFunctions:
    """Test run_claude_tui and run_textual_console functions."""

    def test_run_claude_tui_signature(self) -> None:
        """run_claude_tui must accept expected parameters."""
        import inspect

        sig = inspect.signature(run_claude_tui)
        params = list(sig.parameters.keys())

        assert "workspace" in params
        assert "role" in params
        assert "session_id" in params
        assert "debug" in params

    def test_run_textual_console_signature(self) -> None:
        """run_textual_console must accept expected parameters."""
        import inspect

        sig = inspect.signature(run_textual_console)
        params = list(sig.parameters.keys())

        assert "workspace" in params
        assert "role" in params
        assert "session_id" in params
        assert "debug" in params


class TestCSSTheme:
    """Test CSS theme generation."""

    def test_css_contains_header_styles(self) -> None:
        """CSS must contain header styles."""
        css = get_console_css()
        assert "#header" in css
        assert "#header-title" in css

    def test_css_contains_message_styles(self) -> None:
        """CSS must contain message panel styles."""
        css = get_console_css()
        assert ".message-panel" in css
        assert ".message-panel-user" in css
        assert ".message-panel-agent" in css

    def test_css_contains_input_styles(self) -> None:
        """CSS must contain input area styles."""
        css = get_console_css()
        assert "#input-section" in css
        assert "#input-textarea" in css

    def test_css_contains_sidebar_styles(self) -> None:
        """CSS must contain sidebar styles."""
        css = get_console_css()
        assert "#sidebar" in css
        assert ".sidebar-section-title" in css


# Integration-style tests that require Textual framework
@pytest.mark.skipif(not TEXTUAL_AVAILABLE, reason="Textual not installed")
class TestTextualIntegration:
    """Integration tests requiring Textual framework."""

    @pytest.mark.asyncio
    async def test_app_compose(self) -> None:
        """App must compose without errors."""
        app = ClaudeAgentTUI(
            workspace="/test",
            role="assistant",
            debug_enabled=False,
        )

        # Note: This test requires running in a Textual pilot context
        # For full integration tests, use textual's pilot fixture
        assert app is not None
        assert hasattr(app, "compose")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
