"""Unit tests for director/helpers.py pure logic (no I/O, no filesystem).

Covers:
- _seq_parse_bool / _seq_resolve_bool
- _seq_resolve_int / _seq_resolve_str
- is_project_code_file
- preview_content_for_error
- summarize_tools_for_debug
- is_format_validation_failure / is_timeout_failure
- has_successful_write_tool
- is_empty_role_response
- looks_like_protocol_patch_response
- extract_kernel_tool_results
- coerce_task_record
- taskboard_snapshot_brief
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from polaris.cells.roles.adapters.internal.director.helpers import (
    _seq_parse_bool,
    _seq_resolve_bool,
    _seq_resolve_int,
    _seq_resolve_str,
    coerce_task_record,
    extract_kernel_tool_results,
    has_successful_write_tool,
    is_empty_role_response,
    is_format_validation_failure,
    is_project_code_file,
    is_timeout_failure,
    looks_like_protocol_patch_response,
    preview_content_for_error,
    summarize_tools_for_debug,
    taskboard_snapshot_brief,
)


# ---------------------------------------------------------------------------
# Boolean parsing
# ---------------------------------------------------------------------------


class TestSeqParseBool:
    def test_bool_passthrough(self) -> None:
        assert _seq_parse_bool(True, default=False) is True
        assert _seq_parse_bool(False, default=True) is False

    def test_string_true_values(self) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", " Yes "):
            assert _seq_parse_bool(val, default=False) is True

    def test_string_false_values(self) -> None:
        for val in ("0", "false", "no", "off", "FALSE", " No "):
            assert _seq_parse_bool(val, default=True) is False

    def test_invalid_returns_default(self) -> None:
        assert _seq_parse_bool("maybe", default=False) is False
        assert _seq_parse_bool("maybe", default=True) is True
        assert _seq_parse_bool(None, default=False) is False
        assert _seq_parse_bool(42, default=True) is True


class TestSeqResolveBool:
    def test_settings_takes_precedence(self) -> None:
        settings = MagicMock()
        settings.flag = True
        assert _seq_resolve_bool(settings, object(), "flag", "ENV", False) is True

    def test_env_fallback(self, monkeypatch: Any) -> None:
        settings = MagicMock()
        settings.flag = object()  # sentinel
        monkeypatch.setenv("ENV_KEY", "true")
        assert _seq_resolve_bool(settings, object(), "flag", "ENV_KEY", False) is True

    def test_default_fallback(self) -> None:
        settings = MagicMock()
        settings.flag = object()
        assert _seq_resolve_bool(settings, object(), "flag", "NONEXISTENT_ENV_XYZ", True) is True


# ---------------------------------------------------------------------------
# Integer / String resolution
# ---------------------------------------------------------------------------


class TestSeqResolveInt:
    def test_settings_value(self) -> None:
        settings = MagicMock()
        settings.num = 5
        assert _seq_resolve_int(settings, object(), "num", "ENV", 1) == 5

    def test_env_fallback(self, monkeypatch: Any) -> None:
        settings = MagicMock()
        settings.num = object()
        monkeypatch.setenv("ENV_NUM", "10")
        assert _seq_resolve_int(settings, object(), "num", "ENV_NUM", 1) == 10

    def test_minimum_enforced(self, monkeypatch: Any) -> None:
        settings = MagicMock()
        settings.num = object()
        monkeypatch.setenv("ENV_NUM", "0")
        assert _seq_resolve_int(settings, object(), "num", "ENV_NUM", 1, minimum=1) == 1

    def test_invalid_env_uses_default(self, monkeypatch: Any) -> None:
        settings = MagicMock()
        settings.num = object()
        monkeypatch.setenv("ENV_NUM", "abc")
        assert _seq_resolve_int(settings, object(), "num", "ENV_NUM", 3) == 3


class TestSeqResolveStr:
    def test_settings_value(self) -> None:
        settings = MagicMock()
        settings.name = "hello"
        assert _seq_resolve_str(settings, object(), "name", "ENV", "default") == "hello"

    def test_env_fallback(self, monkeypatch: Any) -> None:
        settings = MagicMock()
        settings.name = object()
        monkeypatch.setenv("ENV_NAME", "world")
        assert _seq_resolve_str(settings, object(), "name", "ENV_NAME", "default") == "world"

    def test_empty_settings_ignored(self) -> None:
        settings = MagicMock()
        settings.name = "   "
        assert _seq_resolve_str(settings, object(), "name", "ENV_NAME", "default") == "default"


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------


class TestIsProjectCodeFile:
    def test_known_extensions(self) -> None:
        assert is_project_code_file(".py") is True
        assert is_project_code_file(".ts") is True
        assert is_project_code_file(".tsx") is True
        assert is_project_code_file(".vue") is True

    def test_case_insensitive(self) -> None:
        assert is_project_code_file(".PY") is True
        assert is_project_code_file(".Js") is True

    def test_unknown_extension(self) -> None:
        assert is_project_code_file(".exe") is False
        assert is_project_code_file("") is False


# ---------------------------------------------------------------------------
# Content preview
# ---------------------------------------------------------------------------


class TestPreviewContentForError:
    def test_short_content_unchanged(self) -> None:
        assert preview_content_for_error("hello world") == "hello world"

    def test_whitespace_normalized(self) -> None:
        assert preview_content_for_error("a  b\n\nc") == "a b c"

    def test_truncation(self) -> None:
        long_text = "x" * 300
        result = preview_content_for_error(long_text, limit=50)
        assert result == "x" * 50 + "...(truncated)"

    def test_none_treated_as_empty(self) -> None:
        assert preview_content_for_error(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tool summary
# ---------------------------------------------------------------------------


class TestSummarizeToolsForDebug:
    def test_empty_list(self) -> None:
        assert summarize_tools_for_debug([]) == []

    def test_basic_summary(self) -> None:
        tools = [
            {
                "tool": "write_file",
                "success": True,
                "error": "",
                "result": {"file": "test.py", "path": "/tmp/test.py"},
            },
            {"tool": "read_file", "success": False, "error": "not found", "result": {}},
        ]
        result = summarize_tools_for_debug(tools)
        assert len(result) == 2
        assert result[0]["tool"] == "write_file"
        assert result[0]["success"] is True
        assert result[0]["file"] == "test.py"
        assert result[1]["error"] == "not found"

    def test_skips_non_dict_items(self) -> None:
        assert summarize_tools_for_debug(["not a dict"]) == []  # type: ignore[list-item]

    def test_limits_to_12(self) -> None:
        tools = [{"tool": f"tool_{i}", "success": True} for i in range(20)]
        result = summarize_tools_for_debug(tools)
        assert len(result) == 12

    def test_source_tool_extracted(self) -> None:
        tools = [{"tool": "edit_file", "success": True, "result": {"source_tool": "patch"}}]
        result = summarize_tools_for_debug(tools)
        assert result[0]["source_tool"] == "patch"


# ---------------------------------------------------------------------------
# Error detection
# ---------------------------------------------------------------------------


class TestIsFormatValidationFailure:
    def test_empty_returns_false(self) -> None:
        assert is_format_validation_failure("") is False
        assert is_format_validation_failure(None) is False  # type: ignore[arg-type]

    def test_chinese_hint(self) -> None:
        assert is_format_validation_failure("未找到有效的json或补丁") is True

    def test_english_hint(self) -> None:
        assert is_format_validation_failure("no valid json found in response") is True
        assert is_format_validation_failure("validation failed") is True

    def test_unrelated_error(self) -> None:
        assert is_format_validation_failure("timeout occurred") is False


class TestIsTimeoutFailure:
    def test_empty_returns_false(self) -> None:
        assert is_timeout_failure("") is False
        assert is_timeout_failure(None) is False  # type: ignore[arg-type]

    def test_timeout_variants(self) -> None:
        assert is_timeout_failure("timeout") is True
        assert is_timeout_failure("timed out after 30s") is True
        assert is_timeout_failure("LLM_TIMEOUT") is True

    def test_unrelated_error(self) -> None:
        assert is_timeout_failure("validation failed") is False


class TestHasSuccessfulWriteTool:
    def test_empty_returns_false(self) -> None:
        assert has_successful_write_tool([]) is False

    def test_successful_write(self) -> None:
        assert has_successful_write_tool([{"tool": "write_file", "success": True}]) is True
        assert has_successful_write_tool([{"tool": "edit_file", "success": True}]) is True
        assert has_successful_write_tool([{"tool": "patch_apply", "success": True}]) is True

    def test_unsuccessful_write(self) -> None:
        assert has_successful_write_tool([{"tool": "write_file", "success": False}]) is False

    def test_non_write_tool(self) -> None:
        assert has_successful_write_tool([{"tool": "read_file", "success": True}]) is False

    def test_skips_non_dict(self) -> None:
        assert has_successful_write_tool(["bad"]) is False  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Role response emptiness
# ---------------------------------------------------------------------------


class TestIsEmptyRoleResponse:
    def test_none_is_empty(self) -> None:
        assert is_empty_role_response(None) is True  # type: ignore[arg-type]

    def test_non_dict_is_empty(self) -> None:
        assert is_empty_role_response("string") is True  # type: ignore[arg-type]

    def test_content_present(self) -> None:
        assert is_empty_role_response({"content": "hello"}) is False

    def test_error_present(self) -> None:
        assert is_empty_role_response({"error": "oops"}) is False

    def test_tool_calls_in_raw(self) -> None:
        assert is_empty_role_response({"raw_response": {"tool_calls": [{"name": "x"}]}}) is False

    def test_tool_calls_top_level(self) -> None:
        assert is_empty_role_response({"tool_calls": [{"name": "x"}]}) is False

    def test_truly_empty(self) -> None:
        assert is_empty_role_response({}) is True
        assert is_empty_role_response({"content": "", "error": ""}) is True


# ---------------------------------------------------------------------------
# Protocol patch detection
# ---------------------------------------------------------------------------


class TestLooksLikeProtocolPatchResponse:
    def test_empty_returns_false(self) -> None:
        assert looks_like_protocol_patch_response("") is False
        assert looks_like_protocol_patch_response(None) is False  # type: ignore[arg-type]

    def test_patch_file_keyword(self) -> None:
        assert looks_like_protocol_patch_response("PATCH_FILE: foo.py") is True

    def test_delete_file_keyword(self) -> None:
        assert looks_like_protocol_patch_response("delete_file: foo.py") is True

    def test_search_replace_markers(self) -> None:
        text = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        assert looks_like_protocol_patch_response(text) is True

    def test_search_replace_labels(self) -> None:
        text = "\nsearch:\nold\nreplace:\nnew\n"
        assert looks_like_protocol_patch_response(text) is True

    def test_file_create_pattern(self) -> None:
        assert looks_like_protocol_patch_response("file: main.py") is True
        assert looks_like_protocol_patch_response("create: main.py") is True

    def test_plain_text(self) -> None:
        assert looks_like_protocol_patch_response("This is just a discussion.") is False


# ---------------------------------------------------------------------------
# Kernel tool results extraction
# ---------------------------------------------------------------------------


class TestExtractKernelToolResults:
    def test_empty_dict(self) -> None:
        assert extract_kernel_tool_results({}) == []

    def test_from_tool_results(self) -> None:
        resp = {
            "tool_results": [
                {"tool": "write_file", "success": True, "result": {"file": "a.py"}, "error": ""},
            ]
        }
        result = extract_kernel_tool_results(resp)
        assert len(result) == 1
        assert result[0]["tool"] == "write_file"
        assert result[0]["success"] is True

    def test_from_tool_calls_fallback(self) -> None:
        resp = {"tool_calls": [{"name": "read_file", "success": False, "error": "missing"}]}
        result = extract_kernel_tool_results(resp)
        assert len(result) == 1
        assert result[0]["tool"] == "read_file"
        assert result[0]["error"] == "missing"

    def test_from_raw_response_nested(self) -> None:
        resp = {"raw_response": {"tool_results": [{"tool": "x", "success": True}]}}
        result = extract_kernel_tool_results(resp)
        assert len(result) == 1

    def test_skips_non_dict_items(self) -> None:
        resp = {"tool_results": ["bad", {"tool": "ok", "success": True}]}
        result = extract_kernel_tool_results(resp)
        assert len(result) == 1

    def test_unknown_tool_name_fallback(self) -> None:
        resp = {"tool_results": [{"success": True}]}
        result = extract_kernel_tool_results(resp)
        assert result[0]["tool"] == "unknown"


# ---------------------------------------------------------------------------
# Coerce task record
# ---------------------------------------------------------------------------


class TestCoerceTaskRecord:
    def test_dict_passthrough(self) -> None:
        assert coerce_task_record({"id": 1}) == {"id": 1}

    def test_to_dict_method(self) -> None:
        class Obj:
            def to_dict(self) -> dict[str, Any]:
                return {"id": 2}

        assert coerce_task_record(Obj()) == {"id": 2}

    def test_to_dict_exception_returns_empty(self) -> None:
        class Obj:
            def to_dict(self) -> None:
                raise RuntimeError("fail")

        assert coerce_task_record(Obj()) == {}

    def test_attribute_fallback(self) -> None:
        class Obj:
            id = 3
            status = "ready"
            unknown = "x"

        result = coerce_task_record(Obj())
        assert result["id"] == 3
        assert result["status"] == "ready"
        assert "unknown" not in result

    def test_unknown_attribute_ignored(self) -> None:
        class Obj:
            custom = "value"

        assert coerce_task_record(Obj()) == {}


# ---------------------------------------------------------------------------
# Taskboard snapshot brief
# ---------------------------------------------------------------------------


class TestTaskboardSnapshotBrief:
    def test_non_dict_returns_unavailable(self) -> None:
        assert taskboard_snapshot_brief("bad") == "taskboard unavailable"  # type: ignore[arg-type]

    def test_all_zeros(self) -> None:
        snapshot = {"counts": {"total": 0, "ready": 0, "pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0}}
        result = taskboard_snapshot_brief(snapshot)
        assert "total=0" in result
        assert "ready=0" in result

    def test_mixed_counts(self) -> None:
        snapshot = {"counts": {"total": 10, "ready": 2, "pending": 3, "in_progress": 1, "completed": 4, "failed": 0, "blocked": 0}}
        result = taskboard_snapshot_brief(snapshot)
        assert "total=10" in result
        assert "ready=2" in result
        assert "pending=3" in result
        assert "completed=4" in result

    def test_missing_counts(self) -> None:
        assert taskboard_snapshot_brief({}) == "taskboard unavailable"
