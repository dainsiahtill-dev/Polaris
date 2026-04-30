"""Tests for polaris.delivery.cli.terminal_console module."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

from polaris.delivery.cli.terminal_console import (
    _ANSI_BOLD,
    _ANSI_CYAN,
    _ANSI_DIM,
    _ANSI_GREEN,
    _ANSI_RED,
    _ANSI_RESET,
    _ANSI_YELLOW,
    _EXIT_COMMANDS,
    _HELP_COMMANDS,
    _JSON_RENDER_MODES,
    _OUTPUT_FORMAT_MODES,
    _PROMPT_STYLES,
    _UNSET,
    _build_render_state,
    _coerce_bool,
    _ConsoleRenderState,
    _detect_keymode_from_shell,
    _estimate_cost,
    _extract_diff_text,
    _extract_token_usage,
    _get_current_model,
    _get_role_symbol,
    _get_tool_style,
    _has_diff_content,
    _json_event_packet,
    _json_event_text,
    _normalize_json_render,
    _normalize_output_format,
    _normalize_prompt_style,
    _resolve_keymode,
    _resolve_output_format,
    _safe_text,
    _set_current_model,
    _stdout_is_tty,
    _style_debug_line,
    _supports_dim_debug,
    _tool_error,
    _tool_name,
    _tool_path,
    _tool_status,
)


class TestRolePromptSymbols:
    """Tests for role prompt symbols."""

    def test_director_symbol(self) -> None:
        """Test director symbol lookup."""
        assert _get_role_symbol("director") == "◉"

    def test_pm_symbol(self) -> None:
        """Test pm symbol lookup."""
        assert _get_role_symbol("pm") == "◆"

    def test_architect_symbol(self) -> None:
        """Test architect symbol lookup."""
        assert _get_role_symbol("architect") == "◇"

    def test_chief_engineer_symbol(self) -> None:
        """Test chief_engineer symbol lookup."""
        assert _get_role_symbol("chief_engineer") == "◈"

    def test_qa_symbol(self) -> None:
        """Test qa symbol lookup."""
        assert _get_role_symbol("qa") == "◎"

    def test_super_symbol(self) -> None:
        """Test super symbol lookup."""
        assert _get_role_symbol("super") == "✦"

    def test_unknown_role_default(self) -> None:
        """Test unknown role returns default symbol."""
        assert _get_role_symbol("unknown") == "▸"

    def test_case_insensitive(self) -> None:
        """Test case insensitive lookup."""
        assert _get_role_symbol("DIRECTOR") == "◉"
        assert _get_role_symbol("Pm") == "◆"

    def test_empty_string(self) -> None:
        """Test empty string returns default."""
        assert _get_role_symbol("") == "▸"


class TestToolNameStyles:
    """Tests for tool name styles."""

    def test_read_file_style(self) -> None:
        """Test read_file style lookup."""
        assert _get_tool_style("read_file") == "blue"

    def test_write_file_style(self) -> None:
        """Test write_file style lookup."""
        assert _get_tool_style("write_file") == "green"

    def test_edit_file_style(self) -> None:
        """Test edit_file style lookup."""
        assert _get_tool_style("edit_file") == "yellow"

    def test_bash_style(self) -> None:
        """Test bash style lookup."""
        assert _get_tool_style("bash") == "red bold"

    def test_search_style(self) -> None:
        """Test search style lookup."""
        assert _get_tool_style("search") == "cyan"

    def test_unknown_tool_default(self) -> None:
        """Test unknown tool returns default style."""
        assert _get_tool_style("unknown_tool") == "cyan"

    def test_empty_string(self) -> None:
        """Test empty string returns default."""
        assert _get_tool_style("") == "cyan"


class TestSafeText:
    """Tests for _safe_text function."""

    def test_string_input(self) -> None:
        """Test string input."""
        assert _safe_text("hello") == "hello"

    def test_none_input(self) -> None:
        """Test None input."""
        assert _safe_text(None) == ""

    def test_empty_string(self) -> None:
        """Test empty string input."""
        assert _safe_text("") == ""

    def test_whitespace_string(self) -> None:
        """Test whitespace string is stripped."""
        assert _safe_text("  hello  ") == "hello"

    def test_integer_input(self) -> None:
        """Test integer input converted to string."""
        assert _safe_text(42) == "42"


class TestCoerceBool:
    """Tests for _coerce_bool function."""

    def test_true_boolean(self) -> None:
        """Test True boolean."""
        assert _coerce_bool(True) is True

    def test_false_boolean(self) -> None:
        """Test False boolean."""
        assert _coerce_bool(False) is False

    def test_string_true(self) -> None:
        """Test string 'true'."""
        assert _coerce_bool("true") is True

    def test_string_1(self) -> None:
        """Test string '1'."""
        assert _coerce_bool("1") is True

    def test_string_yes(self) -> None:
        """Test string 'yes'."""
        assert _coerce_bool("yes") is True

    def test_string_on(self) -> None:
        """Test string 'on'."""
        assert _coerce_bool("on") is True

    def test_string_debug(self) -> None:
        """Test string 'debug'."""
        assert _coerce_bool("debug") is True

    def test_string_false(self) -> None:
        """Test string 'false'."""
        assert _coerce_bool("false") is False

    def test_string_0(self) -> None:
        """Test string '0'."""
        assert _coerce_bool("0") is False

    def test_none_input(self) -> None:
        """Test None input."""
        assert _coerce_bool(None) is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert _coerce_bool("") is False


class TestExtractTokenUsage:
    """Tests for _extract_token_usage function."""

    def test_direct_usage_field(self) -> None:
        """Test extraction from 'usage' field."""
        payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        result = _extract_token_usage(payload)
        assert result == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_token_usage_field(self) -> None:
        """Test extraction from 'token_usage' field."""
        payload = {"token_usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
        result = _extract_token_usage(payload)
        assert result == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    def test_llm_usage_field(self) -> None:
        """Test extraction from 'llm_usage' field."""
        payload = {"llm_usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}}
        result = _extract_token_usage(payload)
        assert result == {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}

    def test_no_usage_returns_none(self) -> None:
        """Test no usage data returns None."""
        payload = {"other": "data"}
        assert _extract_token_usage(payload) is None

    def test_none_payload(self) -> None:
        """Test None payload."""
        assert _extract_token_usage({}) is None

    def test_non_mapping_usage(self) -> None:
        """Test non-mapping usage field."""
        payload = {"usage": "not a dict"}
        assert _extract_token_usage(payload) is None


class TestEstimateCost:
    """Tests for _estimate_cost function."""

    def test_known_model_claude_sonnet(self) -> None:
        """Test cost estimation for claude-3-5-sonnet."""
        result = _estimate_cost(1_000_000, 0, "claude-3-5-sonnet")
        assert result == "~$3.0000"

    def test_known_model_gpt4o(self) -> None:
        """Test cost estimation for gpt-4o."""
        result = _estimate_cost(1_000_000, 1_000_000, "gpt-4o")
        assert result == "~$12.5000"

    def test_unknown_model(self) -> None:
        """Test unknown model returns 'n/a'."""
        assert _estimate_cost(1000, 1000, "unknown-model") == "n/a"

    def test_case_insensitive_model(self) -> None:
        """Test model name case insensitivity."""
        result = _estimate_cost(1_000_000, 0, "CLAUDE-3-5-SONNET")
        assert result == "~$3.0000"

    def test_zero_tokens(self) -> None:
        """Test zero tokens."""
        result = _estimate_cost(0, 0, "gpt-4o")
        assert result == "~$0.0000"


class TestModelEnvironment:
    """Tests for model environment functions."""

    def test_get_current_model_none(self) -> None:
        """Test get current model when not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert _get_current_model() is None

    def test_get_current_model_set(self) -> None:
        """Test get current model when set."""
        with patch.dict(os.environ, {"KERNELONE_PM_MODEL": "gpt-4o"}):
            assert _get_current_model() == "gpt-4o"

    def test_set_current_model(self) -> None:
        """Test set current model."""
        with patch.dict(os.environ, {}, clear=True):
            _set_current_model("claude-3-5-sonnet")
            assert os.environ.get("KERNELONE_PM_MODEL") == "claude-3-5-sonnet"


class TestNormalizeFunctions:
    """Tests for normalize functions."""

    def test_normalize_json_render_raw(self) -> None:
        """Test normalize 'raw' render mode."""
        assert _normalize_json_render("raw") == "raw"

    def test_normalize_json_render_pretty(self) -> None:
        """Test normalize 'pretty' render mode."""
        assert _normalize_json_render("pretty") == "pretty"

    def test_normalize_json_render_pretty_color(self) -> None:
        """Test normalize 'pretty-color' render mode."""
        assert _normalize_json_render("pretty-color") == "pretty-color"

    def test_normalize_json_render_invalid(self) -> None:
        """Test normalize invalid render mode defaults to 'raw'."""
        assert _normalize_json_render("invalid") == "raw"

    def test_normalize_json_render_none(self) -> None:
        """Test normalize None render mode."""
        assert _normalize_json_render(None) == "raw"

    def test_normalize_prompt_style_plain(self) -> None:
        """Test normalize 'plain' prompt style."""
        assert _normalize_prompt_style("plain") == "plain"

    def test_normalize_prompt_style_omp(self) -> None:
        """Test normalize 'omp' prompt style."""
        assert _normalize_prompt_style("omp") == "omp"

    def test_normalize_prompt_style_invalid(self) -> None:
        """Test normalize invalid prompt style defaults to 'plain'."""
        assert _normalize_prompt_style("invalid") == "plain"

    def test_normalize_output_format_text(self) -> None:
        """Test normalize 'text' output format."""
        assert _normalize_output_format("text") == "text"

    def test_normalize_output_format_json(self) -> None:
        """Test normalize 'json' output format."""
        assert _normalize_output_format("json") == "json"

    def test_normalize_output_format_json_stream_alias(self) -> None:
        """Test 'json-stream' alias normalized to 'json'."""
        assert _normalize_output_format("json-stream") == "json"

    def test_normalize_output_format_invalid(self) -> None:
        """Test normalize invalid format defaults to 'text'."""
        assert _normalize_output_format("invalid") == "text"


class TestResolveOutputFormat:
    """Tests for _resolve_output_format function."""

    def test_unset_non_tty(self) -> None:
        """Test _UNSET with non-TTY returns 'json'."""
        with patch("polaris.delivery.cli.terminal_console._stdout_is_tty", return_value=False):
            assert _resolve_output_format(_UNSET) == "json"

    def test_unset_tty(self) -> None:
        """Test _UNSET with TTY returns 'text'."""
        with patch("polaris.delivery.cli.terminal_console._stdout_is_tty", return_value=True):
            assert _resolve_output_format(_UNSET) == "text"

    def test_explicit_none(self) -> None:
        """Test explicit None returns 'text'."""
        assert _resolve_output_format(None) == "text"

    def test_explicit_string(self) -> None:
        """Test explicit format string."""
        assert _resolve_output_format("json") == "json"
        assert _resolve_output_format("text") == "text"


class TestStdoutIsTty:
    """Tests for _stdout_is_tty function."""

    def test_no_stdout(self) -> None:
        """Test when stdout is None."""
        with patch.object(sys, "stdout", None):
            assert _stdout_is_tty() is False

    def test_stdout_isatty_raises_runtime_error(self) -> None:
        """Test when stdout.isatty raises RuntimeError."""
        mock_stdout = MagicMock()
        mock_stdout.isatty.side_effect = RuntimeError("not a tty")
        with patch.object(sys, "stdout", mock_stdout):
            assert _stdout_is_tty() is False

    def test_stdout_isatty_raises_value_error(self) -> None:
        """Test when stdout.isatty raises ValueError."""
        mock_stdout = MagicMock()
        mock_stdout.isatty.side_effect = ValueError("bad fd")
        with patch.object(sys, "stdout", mock_stdout):
            assert _stdout_is_tty() is False

    def test_stdout_isatty_true(self) -> None:
        """Test when stdout is a TTY."""
        mock_stdout = MagicMock()
        mock_stdout.isatty.return_value = True
        with patch.object(sys, "stdout", mock_stdout):
            assert _stdout_is_tty() is True

    def test_stdout_isatty_false(self) -> None:
        """Test when stdout is not a TTY."""
        mock_stdout = MagicMock()
        mock_stdout.isatty.return_value = False
        with patch.object(sys, "stdout", mock_stdout):
            assert _stdout_is_tty() is False


class TestJsonEventHelpers:
    """Tests for JSON event helper functions."""

    def test_json_event_packet(self) -> None:
        """Test _json_event_packet creates correct structure."""
        result = _json_event_packet("test", {"key": "value"})
        assert result == {"type": "test", "data": {"key": "value"}}

    def test_json_event_text_raw(self) -> None:
        """Test raw JSON event text."""
        packet = {"type": "test", "data": {"key": "value"}}
        result = _json_event_text(packet, mode="raw")
        assert "test" in result
        assert "key" in result

    def test_json_event_text_pretty(self) -> None:
        """Test pretty JSON event text."""
        packet = {"type": "test", "data": {"key": "value"}}
        result = _json_event_text(packet, mode="pretty")
        assert "test" in result
        assert "  " in result  # Has indentation


class TestDiffHelpers:
    """Tests for diff helper functions."""

    def test_extract_diff_text_from_patch(self) -> None:
        """Test extracting diff from patch field."""
        payload = {"patch": "diff content"}
        assert _extract_diff_text(payload) == "diff content"

    def test_extract_diff_text_from_diff(self) -> None:
        """Test extracting diff from diff field."""
        payload = {"diff": "diff content"}
        assert _extract_diff_text(payload) == "diff content"

    def test_no_diff_content(self) -> None:
        """Test no diff content returns empty string."""
        payload = {"other": "data"}
        assert _extract_diff_text(payload) == ""

    def test_has_diff_content_true(self) -> None:
        """Test has_diff_content returns True."""
        payload = {"patch": "some diff"}
        assert _has_diff_content(payload) is True

    def test_has_diff_content_false(self) -> None:
        """Test has_diff_content returns False."""
        payload = {"other": "data"}
        assert _has_diff_content(payload) is False

    def test_has_diff_content_whitespace_only(self) -> None:
        """Test whitespace-only diff is treated as no diff."""
        payload = {"patch": "   \n  "}
        assert _has_diff_content(payload) is False


class TestToolHelpers:
    """Tests for tool helper functions."""

    def test_tool_name_from_tool_field(self) -> None:
        """Test tool name from 'tool' field."""
        payload = {"tool": "read_file"}
        assert _tool_name(payload) == "read_file"

    def test_tool_name_from_result(self) -> None:
        """Test tool name from result field."""
        payload = {"result": {"tool": "write_file"}}
        assert _tool_name(payload) == "write_file"

    def test_tool_name_default(self) -> None:
        """Test tool name default."""
        payload = {}
        assert _tool_name(payload) == "tool"

    def test_tool_path_from_file_path(self) -> None:
        """Test tool path from file_path."""
        payload = {"file_path": "test.py"}
        assert _tool_path(payload) == "test.py"

    def test_tool_path_from_path(self) -> None:
        """Test tool path from path field."""
        payload = {"path": "test.py"}
        assert _tool_path(payload) == "test.py"

    def test_tool_path_backslash_replacement(self) -> None:
        """Test backslash replacement in path."""
        payload = {"file_path": "dir\\test.py"}
        assert _tool_path(payload) == "dir/test.py"

    def test_tool_path_empty(self) -> None:
        """Test empty tool path."""
        assert _tool_path({}) == ""

    def test_tool_status_success(self) -> None:
        """Test tool status success."""
        payload = {"success": True}
        assert _tool_status(payload) == "ok"

    def test_tool_status_failed(self) -> None:
        """Test tool status failed."""
        payload = {"success": False}
        assert _tool_status(payload) == "failed"

    def test_tool_status_ok_field(self) -> None:
        """Test tool status from ok field."""
        payload = {"ok": True}
        assert _tool_status(payload) == "ok"

    def test_tool_status_error_fallback(self) -> None:
        """Test tool status from error field fallback."""
        payload = {"error": "Something went wrong"}
        assert _tool_status(payload) == "failed"

    def test_tool_status_default(self) -> None:
        """Test tool status default."""
        assert _tool_status({}) == "done"

    def test_tool_error_direct(self) -> None:
        """Test tool error from direct field."""
        payload = {"error": "error message"}
        assert _tool_error(payload) == "error message"

    def test_tool_error_from_result(self) -> None:
        """Test tool error from result field."""
        payload = {"result": {"error": "result error"}}
        assert _tool_error(payload) == "result error"

    def test_tool_error_empty(self) -> None:
        """Test empty tool error."""
        assert _tool_error({}) == ""


class TestKeymodeHelpers:
    """Tests for keymode helper functions."""

    def test_detect_keymode_vi(self) -> None:
        """Test detect vi keymode from SHELLOPTS."""
        with patch.dict(os.environ, {"SHELLOPTS": "vi emacs"}):
            assert _detect_keymode_from_shell() == "vi"

    def test_detect_keymode_emacs(self) -> None:
        """Test detect emacs keymode from SHELLOPTS."""
        with patch.dict(os.environ, {"SHELLOPTS": "emacs"}):
            assert _detect_keymode_from_shell() == "emacs"

    def test_detect_keymode_default(self) -> None:
        """Test default keymode when SHELLOPTS not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert _detect_keymode_from_shell() == "emacs"

    def test_resolve_keymode_none(self) -> None:
        """Test resolve None keymode."""
        assert _resolve_keymode(None) == "emacs"

    def test_resolve_keymode_valid(self) -> None:
        """Test resolve valid keymode."""
        assert _resolve_keymode("vi") == "vi"
        assert _resolve_keymode("emacs") == "emacs"
        assert _resolve_keymode("auto") == "auto"

    def test_resolve_keymode_invalid(self) -> None:
        """Test resolve invalid keymode defaults to emacs."""
        assert _resolve_keymode("invalid") == "emacs"

    def test_resolve_keymode_case_insensitive(self) -> None:
        """Test resolve keymode is case-insensitive."""
        assert _resolve_keymode("VI") == "vi"


class TestDebugHelpers:
    """Tests for debug helper functions."""

    def test_supports_dim_debug_no_color(self) -> None:
        """Test NO_COLOR env disables dim debug."""
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            assert _supports_dim_debug() is False

    def test_style_debug_line_no_color(self) -> None:
        """Test style debug line without color support."""
        with patch("polaris.delivery.cli.terminal_console._supports_dim_debug", return_value=False):
            assert _style_debug_line("test") == "test"

    def test_style_debug_line_with_color(self) -> None:
        """Test style debug line with color support."""
        with patch("polaris.delivery.cli.terminal_console._supports_dim_debug", return_value=True):
            result = _style_debug_line("test")
            assert _ANSI_DIM in result
            assert "test" in result


class BuildRenderState:
    """Tests for _build_render_state function."""

    def test_defaults(self) -> None:
        """Test default render state."""
        with patch.dict(os.environ, {}, clear=True):
            state = _build_render_state(
                prompt_style=None,
                omp_config=None,
                json_render=None,
                output_format=None,
            )
            assert isinstance(state, _ConsoleRenderState)
            assert state.prompt_style == "plain"
            assert state.json_render == "raw"
            assert state.output_format == "text"

    def test_env_override(self) -> None:
        """Test environment variable override."""
        env = {
            "KERNELONE_CLI_PROMPT_STYLE": "omp",
            "KERNELONE_CLI_JSON_RENDER": "pretty",
            "KERNELONE_CLI_OUTPUT_FORMAT": "json",
        }
        with patch.dict(os.environ, env, clear=True):
            state = _build_render_state(
                prompt_style=None,
                omp_config=None,
                json_render=None,
                output_format=None,
            )
            assert state.prompt_style == "omp"
            assert state.json_render == "pretty"
            assert state.output_format == "json"


class TestConstants:
    """Tests for module constants."""

    def test_exit_commands(self) -> None:
        """Test exit commands set."""
        assert "/exit" in _EXIT_COMMANDS
        assert "/quit" in _EXIT_COMMANDS
        assert ":q" in _EXIT_COMMANDS

    def test_help_commands(self) -> None:
        """Test help commands set."""
        assert "/help" in _HELP_COMMANDS
        assert "/?" in _HELP_COMMANDS

    def test_json_render_modes(self) -> None:
        """Test JSON render modes set."""
        assert "raw" in _JSON_RENDER_MODES
        assert "pretty" in _JSON_RENDER_MODES
        assert "pretty-color" in _JSON_RENDER_MODES

    def test_output_format_modes(self) -> None:
        """Test output format modes set."""
        assert "text" in _OUTPUT_FORMAT_MODES
        assert "json" in _OUTPUT_FORMAT_MODES
        assert "json-pretty" in _OUTPUT_FORMAT_MODES
        assert "json-stream" in _OUTPUT_FORMAT_MODES

    def test_prompt_styles(self) -> None:
        """Test prompt styles set."""
        assert "plain" in _PROMPT_STYLES
        assert "omp" in _PROMPT_STYLES

    def test_ansi_constants(self) -> None:
        """Test ANSI escape constants."""
        assert _ANSI_GREEN == "\x1b[32m"
        assert _ANSI_RED == "\x1b[31m"
        assert _ANSI_RESET == "\x1b[0m"
        assert _ANSI_BOLD == "\x1b[1m"
        assert _ANSI_DIM == "\x1b[2m"
        assert _ANSI_YELLOW == "\x1b[33m"
        assert _ANSI_CYAN == "\x1b[36m"
