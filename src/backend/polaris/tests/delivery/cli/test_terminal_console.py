"""Tests for polaris.delivery.cli.terminal_console module."""

from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

from polaris.delivery.cli.terminal_console import (
    _ANSI_GREEN,
    _ANSI_RED,
    SUPER_ROLE,
    _build_structured_json_event,
    _coerce_bool,
    _console_display_role,
    _ConsoleRenderState,
    _detect_keymode_from_shell,
    _director_output_suggests_more_work,
    _estimate_cost,
    _extract_diff_text,
    _extract_token_usage,
    _format_time,
    _get_current_model,
    _get_role_symbol,
    _get_tool_style,
    _has_diff_content,
    _json_event_packet,
    _json_event_text,
    _normalize_json_render,
    _normalize_output_format,
    _normalize_prompt_style,
    _normalize_role,
    _render_diff_ansi,
    _resolve_keymode,
    _resolve_output_format,
    _restore_infrastructure_logs,
    _safe_text,
    _set_current_model,
    _suppress_infrastructure_logs,
    _tool_error,
    _tool_name,
    _tool_path,
    _tool_status,
    _truncate_workspace,
    _TurnExecutionResult,
)


class TestRoleAndToolSymbols:
    def test_get_role_symbol_known(self):
        assert _get_role_symbol("director") == "◉"
        assert _get_role_symbol("pm") == "◆"
        assert _get_role_symbol("super") == "✦"

    def test_get_role_symbol_unknown(self):
        assert _get_role_symbol("unknown") == "▸"

    def test_get_tool_style_known(self):
        assert _get_tool_style("read_file") == "blue"
        assert _get_tool_style("write_file") == "green"
        assert _get_tool_style("execute") == "red bold"

    def test_get_tool_style_unknown(self):
        assert _get_tool_style("unknown_tool") == "cyan"


class TestSafeTextAndCoerce:
    def test_safe_text(self):
        assert _safe_text("hello") == "hello"
        assert _safe_text(None) == ""
        assert _safe_text(123) == "123"
        assert _safe_text("  trim  ") == "trim"

    def test_coerce_bool(self):
        assert _coerce_bool(True) is True
        assert _coerce_bool(False) is False
        assert _coerce_bool("true") is True
        assert _coerce_bool("yes") is True
        assert _coerce_bool("1") is True
        assert _coerce_bool("on") is True
        assert _coerce_bool("debug") is True
        assert _coerce_bool("false") is False
        assert _coerce_bool("") is False
        assert _coerce_bool(None) is False


class TestToolHelpers:
    def test_tool_name(self):
        assert _tool_name({"tool": "read"}) == "read"
        assert _tool_name({"result": {"tool": "write"}}) == "write"
        assert _tool_name({}) == "tool"

    def test_tool_path(self):
        assert _tool_path({"file_path": "dir/file.py"}) == "dir/file.py"
        assert _tool_path({"args": {"path": "test.py"}}) == "test.py"
        assert _tool_path({}) == ""

    def test_tool_status(self):
        assert _tool_status({"success": True}) == "ok"
        assert _tool_status({"success": False}) == "failed"
        assert _tool_status({"result": {"ok": True}}) == "ok"
        assert _tool_status({"error": "oops"}) == "failed"
        assert _tool_status({}) == "done"

    def test_tool_error(self):
        assert _tool_error({"error": "fail"}) == "fail"
        assert _tool_error({"result": {"message": "msg"}}) == "msg"
        assert _tool_error({}) == ""


class TestNormalization:
    def test_normalize_json_render(self):
        assert _normalize_json_render("raw") == "raw"
        assert _normalize_json_render("pretty") == "pretty"
        assert _normalize_json_render("invalid") == "raw"
        assert _normalize_json_render(None) == "raw"

    def test_normalize_prompt_style(self):
        assert _normalize_prompt_style("plain") == "plain"
        assert _normalize_prompt_style("omp") == "omp"
        assert _normalize_prompt_style("fancy") == "plain"

    def test_normalize_output_format(self):
        assert _normalize_output_format("text") == "text"
        assert _normalize_output_format("json") == "json"
        assert _normalize_output_format("json-stream") == "json"
        assert _normalize_output_format("xml") == "text"

    def test_normalize_role(self):
        assert _normalize_role("director") == "director"
        assert _normalize_role("  PM  ") == "pm"
        assert _normalize_role(None) == "director"
        assert _normalize_role("") == "director"


class TestResolveOutputFormat:
    def test_resolve_unset_non_tty(self):
        from polaris.delivery.cli.terminal_console import _UNSET

        with patch("polaris.delivery.cli.terminal_console._stdout_is_tty", return_value=False):
            assert _resolve_output_format(_UNSET) == "json"

    @pytest.mark.skipif(sys.platform == "win32", reason="TTY detection differs on Windows")
    def test_resolve_unset_tty(self):
        from polaris.delivery.cli.terminal_console import _UNSET

        with patch("polaris.delivery.cli.terminal_console._stdout_is_tty", return_value=True):
            assert _resolve_output_format(_UNSET) == "text"

    def test_resolve_none(self):
        assert _resolve_output_format(None) == "text"

    def test_resolve_explicit_string(self):
        assert _resolve_output_format("json") == "json"
        assert _resolve_output_format("json-pretty") == "json-pretty"


class TestJsonEventHelpers:
    def test_json_event_packet(self):
        packet = _json_event_packet("content_chunk", {"content": "hi"})
        assert packet["type"] == "content_chunk"
        assert packet["data"] == {"content": "hi"}

    def test_json_event_text_raw(self):
        packet = {"type": "test", "data": {}}
        text = _json_event_text(packet, mode="raw")
        assert "test" in text
        assert chr(10) not in text

    def test_json_event_text_pretty(self):
        packet = {"type": "test", "data": {}}
        text = _json_event_text(packet, mode="pretty")
        assert "test" in text
        assert chr(10) in text


class TestTokenUsage:
    def test_extract_token_usage_direct(self):
        payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        result = _extract_token_usage(payload)
        assert result == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    def test_extract_token_usage_token_usage_field(self):
        payload = {"token_usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
        result = _extract_token_usage(payload)
        assert result["prompt_tokens"] == 20

    def test_extract_token_usage_llm_usage_field(self):
        payload = {"llm_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        result = _extract_token_usage(payload)
        assert result["total_tokens"] == 5

    def test_extract_token_usage_none(self):
        assert _extract_token_usage({}) is None

    def test_estimate_cost_known_model(self):
        cost = _estimate_cost(1_000_000, 1_000_000, "claude-3-5-sonnet")
        assert cost.startswith("~$")
        assert "18.00" in cost

    def test_estimate_cost_unknown_model(self):
        assert _estimate_cost(100, 100, "unknown-model") == "n/a"


class TestDiffHelpers:
    def test_extract_diff_text(self):
        payload = {"result": {"diff": "--- a\n+++ b"}}
        assert _extract_diff_text(payload) == "--- a\n+++ b"

    def test_extract_diff_text_empty(self):
        assert _extract_diff_text({}) == ""

    def test_has_diff_content(self):
        assert _has_diff_content({"result": {"patch": "diff"}}) is True
        assert _has_diff_content({}) is False

    def test_render_diff_ansi_unified(self):
        diff = "--- old.py\n+++ new.py\n@@ -1 +1 @@\n-removed\n+added"
        rendered = _render_diff_ansi(diff)
        assert _ANSI_GREEN in rendered
        assert _ANSI_RED in rendered
        assert "added" in rendered

    def test_render_diff_ansi_non_unified_add(self):
        diff = "line1\nline2"
        rendered = _render_diff_ansi(diff, operation="add")
        assert _ANSI_GREEN in rendered

    def test_render_diff_ansi_non_unified_delete(self):
        diff = "line1\nline2"
        rendered = _render_diff_ansi(diff, operation="delete")
        assert _ANSI_RED in rendered

    def test_render_diff_ansi_truncate(self):
        parts = ["+line" + str(i) for i in range(250)]
        diff = chr(10).join(parts)
        rendered = _render_diff_ansi(diff, max_lines=200)
        assert "more lines" in rendered


class TestStructuredJsonEvent:
    def test_build_content_chunk(self):
        event = _build_structured_json_event("content_chunk", {"content": "hello"})
        assert event["type"] == "content_chunk"
        assert event["content"] == "hello"
        assert "timestamp" in event

    def test_build_tool_call(self):
        event = _build_structured_json_event("tool_call", {"tool": "read", "args": {"path": "f.py"}})
        assert event["type"] == "tool_call"
        assert event["tool"] == "read"
        assert event["args"] == {"path": "f.py"}

    def test_build_tool_result(self):
        event = _build_structured_json_event(
            "tool_result", {"tool": "write", "result": {"success": True, "duration_ms": 150}}
        )
        assert event["type"] == "tool_result"
        assert event["success"] is True
        assert event["duration_ms"] == 150

    def test_build_complete_with_tokens(self):
        event = _build_structured_json_event(
            "complete", {"content": "done", "tokens": {"prompt": 10, "completion": 5, "total": 15}}
        )
        assert event["type"] == "complete"
        assert event["tokens"] == {"prompt": 10, "completion": 5, "total": 15}

    def test_build_complete_inferred_total(self):
        event = _build_structured_json_event("complete", {"tokens": {"prompt": 10, "completion": 5}})
        assert event["tokens"]["total"] == 15

    def test_build_unknown_event_type(self):
        event = _build_structured_json_event("custom", {"foo": "bar"})
        assert event["type"] == "custom"
        assert event["data"] == {"foo": "bar"}
        assert "timestamp" in event


class TestKeymodeHelpers:
    def test_detect_keymode_from_shell_vi(self):
        with patch.dict(os.environ, {"SHELLOPTS": "vi emacs"}):
            assert _detect_keymode_from_shell() == "vi"

    def test_detect_keymode_from_shell_emacs(self):
        with patch.dict(os.environ, {"SHELLOPTS": "emacs"}):
            assert _detect_keymode_from_shell() == "emacs"

    def test_resolve_keymode(self):
        assert _resolve_keymode("vi") == "vi"
        assert _resolve_keymode("emacs") == "emacs"
        assert _resolve_keymode("invalid") == "emacs"
        assert _resolve_keymode(None) == "emacs"


class TestConsoleDisplayAndTruncate:
    def test_console_display_role_super(self):
        assert _console_display_role(role="director", super_mode=True) == SUPER_ROLE

    def test_console_display_role_normal(self):
        assert _console_display_role(role="pm", super_mode=False) == "pm"

    def test_truncate_workspace_short(self):
        result = _truncate_workspace(Path("/short"))
        assert "short" in result

    def test_truncate_workspace_long(self):
        long_path = "/very/long/path/" + "x" * 100
        result = _truncate_workspace(Path(long_path))
        assert len(result) <= 50
        assert result.startswith("...")

    def test_format_time(self):
        ts = datetime.datetime(2024, 1, 1, 12, 30, 45).timestamp()
        assert _format_time(ts) == "12:30:45"

    def test_format_time_none(self):
        assert _format_time(None) == ""


class TestDirectorOutputHeuristic:
    def test_director_output_suggests_more_work_done_marker(self):
        assert _director_output_suggests_more_work("all tasks complete") is False
        assert _director_output_suggests_more_work("all done") is False

    def test_director_output_suggests_more_work_continue_marker(self):
        assert _director_output_suggests_more_work("pending tasks remain") is True
        assert _director_output_suggests_more_work("next step is to...") is True

    def test_director_output_suggests_more_work_short_output(self):
        assert _director_output_suggests_more_work("ok") is True
        assert _director_output_suggests_more_work("a" * 500) is False


class TestInfrastructureLogSuppression:
    def test_suppress_and_restore(self):
        logger1 = logging.getLogger("polaris.infrastructure.llm.provider_bootstrap")
        logger1.setLevel(logging.DEBUG)
        prev = _suppress_infrastructure_logs()
        assert logger1.level == logging.WARNING
        _restore_infrastructure_logs(prev)
        assert logger1.level == logging.DEBUG

    def test_suppress_returns_previous_levels(self):
        logger1 = logging.getLogger("polaris.infrastructure.llm.provider_bootstrap")
        logger1.setLevel(logging.INFO)
        prev = _suppress_infrastructure_logs()
        assert "polaris.infrastructure.llm.provider_bootstrap" in prev
        assert prev["polaris.infrastructure.llm.provider_bootstrap"] == logging.INFO
        _restore_infrastructure_logs(prev)


class TestModelHelpers:
    def test_get_current_model(self):
        with patch.dict(os.environ, {"KERNELONE_PM_MODEL": "gpt-4o"}):
            assert _get_current_model() == "gpt-4o"

    def test_get_current_model_none(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _get_current_model() is None

    def test_set_current_model(self):
        with patch.dict(os.environ, {}, clear=True):
            _set_current_model("claude-3-opus")
            assert os.environ["KERNELONE_PM_MODEL"] == "claude-3-opus"


class TestConsoleRenderState:
    def test_default_values(self):
        state = _ConsoleRenderState()
        assert state.prompt_style == "plain"
        assert state.json_render == "raw"
        assert state.output_format == "text"
        assert state.omp_executable == "oh-my-posh"


class TestTurnExecutionResult:
    def test_default_values(self):
        result = _TurnExecutionResult(role="director", session_id="s1")
        assert result.role == "director"
        assert result.session_id == "s1"
        assert result.final_content == ""
        assert result.saw_error is False
