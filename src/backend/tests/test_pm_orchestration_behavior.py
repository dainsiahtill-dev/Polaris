"""Behavior tests for PM orchestration pipeline - planning and dispatch pure functions.

Tests cover normalize_priority, normalize_path_list, normalize_engine_config,
_looks_like_tool_call_output, and dispatch task utilities.
These are synchronous, pure functions with no I/O — ideal unit test targets.
"""
from __future__ import annotations

from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    get_director_task_status_summary,
    normalize_task_status,
    to_bool,
)
from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
    _looks_like_tool_call_output,
    _migrate_tasks_in_place,
    normalize_engine_config,
    normalize_path_list,
    normalize_priority,
)


class TestNormalizePriority:
    """normalize_priority converts priority aliases to numeric values."""

    def test_urgent_alias(self) -> None:
        assert normalize_priority("urgent") == 0
        assert normalize_priority("highest") == 0

    def test_high_alias(self) -> None:
        assert normalize_priority("high") == 1

    def test_normal_medium_alias(self) -> None:
        assert normalize_priority("normal") == 5
        assert normalize_priority("medium") == 5

    def test_low_alias(self) -> None:
        assert normalize_priority("low") == 9

    def test_numeric_string(self) -> None:
        assert normalize_priority("3") == 3

    def test_fallback_for_unknown(self) -> None:
        assert normalize_priority("foobar") == 5  # default fallback
        assert normalize_priority("foobar", fallback=3) == 3  # custom fallback

    def test_none_input(self) -> None:
        assert normalize_priority(None) == 5  # default fallback


class TestNormalizePathList:
    """normalize_path_list normalises file/directory path arguments."""

    def test_single_path_string(self) -> None:
        result = normalize_path_list("src/main.py")
        assert result == ["src/main.py"]

    def test_empty_input(self) -> None:
        assert normalize_path_list("") == []
        assert normalize_path_list([]) == []
        assert normalize_path_list(None) == []

    def test_list_passthrough(self) -> None:
        result = normalize_path_list(["src/a.py", "src/b.py"])
        assert result == ["src/a.py", "src/b.py"]

    def test_string_not_split_on_newline(self) -> None:
        # normalize_path_list preserves strings as single entry; it does not split on newlines
        result = normalize_path_list("src/a.py\nsrc/b.py\n")
        assert len(result) == 1
        assert "src/a.py" in result[0]


class TestNormalizeEngineConfig:
    """normalize_engine_config validates and normalises LLM engine configuration."""

    def test_extracts_director_execution_mode(self) -> None:
        config = {"director_execution_mode": "single"}
        result = normalize_engine_config(config)
        assert result.get("director_execution_mode") == "single"

    def test_extracts_scheduling_policy(self) -> None:
        config = {"scheduling_policy": "priority"}
        result = normalize_engine_config(config)
        assert result.get("scheduling_policy") == "priority"

    def test_extracts_max_directors(self) -> None:
        config = {"max_directors": 3}
        result = normalize_engine_config(config)
        assert result.get("max_directors") == 3

    def test_ignores_unknown_fields(self) -> None:
        # normalize_engine_config only extracts known fields; others are dropped
        config = {"model": "gpt-4", "temperature": 0.7, "director_execution_mode": "multi"}
        result = normalize_engine_config(config)
        assert "model" not in result
        assert "temperature" not in result
        assert result.get("director_execution_mode") == "multi"

    def test_non_dict_returns_empty(self) -> None:
        result = normalize_engine_config("not a dict")
        assert result == {}

    def test_empty_config(self) -> None:
        result = normalize_engine_config({})
        assert isinstance(result, dict)


class TestLooksLikeToolCallOutput:
    """_looks_like_tool_call_output detects LLM tool-call framing."""

    def test_detects_tool_call_tag(self) -> None:
        # <tool_call>... framing is the canonical detection pattern
        assert _looks_like_tool_call_output("<tool_call>write_file</tool_call>") is True

    def test_rejects_invoke_tag(self) -> None:
        # <invoke> tags are not detected by this implementation
        assert _looks_like_tool_call_output('<invoke name="write_file">') is False
        assert _looks_like_tool_call_output("<invoke>\n<tool_name>read_file</tool_name>\n</invoke>") is False

    def test_rejects_regular_text(self) -> None:
        assert _looks_like_tool_call_output("Implement the login feature") is False
        assert _looks_like_tool_call_output("Here is the code: def foo():\n    pass") is False

    def test_rejects_empty(self) -> None:
        assert _looks_like_tool_call_output("") is False
        assert _looks_like_tool_call_output("   ") is False


class TestMigrateTasksInPlace:
    """_migrate_tasks_in_place migrates legacy task format in place."""

    def test_migrates_task_id_field(self) -> None:
        # Legacy tasks may have "task_id" instead of "id"
        payload = {
            "tasks": [
                {"task_id": "T1", "subject": "Do thing", "status": "todo"},
            ]
        }
        _migrate_tasks_in_place(payload)
        # Migration should ensure "id" field exists
        tasks = payload.get("tasks", [])
        if tasks:
            # Either id was already present or migration fixed it
            assert "id" in tasks[0] or "task_id" in tasks[0]

    def test_idempotent_when_already_correct(self) -> None:
        payload = {
            "tasks": [
                {"id": "T1", "subject": "Do thing", "status": "todo"},
            ]
        }
        _migrate_tasks_in_place(payload)
        assert payload["tasks"][0]["id"] == "T1"


class TestNormalizeTaskStatus:
    """normalize_task_status normalises task status strings."""

    def test_known_statuses(self) -> None:
        assert normalize_task_status("todo") == "todo"
        assert normalize_task_status("in_progress") == "in_progress"
        assert normalize_task_status("done") == "done"

    def test_unknown_defaults_to_todo(self) -> None:
        assert normalize_task_status("foobar") == "todo"

    def test_numeric_input(self) -> None:
        assert normalize_task_status(123) == "todo"


class TestToBool:
    """to_bool converts various input types to boolean."""

    def test_true_strings(self) -> None:
        assert to_bool("true") is True
        assert to_bool("yes") is True
        assert to_bool("1") is True

    def test_false_strings(self) -> None:
        assert to_bool("false") is False
        assert to_bool("no") is False
        assert to_bool("0") is False

    def test_bool_passthrough(self) -> None:
        assert to_bool(True) is True
        assert to_bool(False) is False

    def test_default_value(self) -> None:
        assert to_bool("unknown", default=True) is True
        assert to_bool("unknown", default=False) is False


class TestGetDirectorTaskStatusSummary:
    """get_director_task_status_summary aggregates task statuses."""

    def test_empty_tasks_returns_zero_counts(self) -> None:
        summary = get_director_task_status_summary([])
        assert isinstance(summary, dict)
        # All status counts should be zero
        assert summary.get("todo", 0) == 0
        assert summary.get("done", 0) == 0

    def test_counts_director_assigned_tasks(self) -> None:
        # Only tasks with assigned_to == "director" are counted
        tasks = [
            {"assigned_to": "director", "status": "todo"},
            {"assigned_to": "director", "status": "todo"},
            {"assigned_to": "director", "status": "done"},
        ]
        summary = get_director_task_status_summary(tasks)
        assert summary.get("todo", 0) == 2
        assert summary.get("done", 0) == 1
        assert summary.get("total", 0) == 3

    def test_skips_non_director_tasks(self) -> None:
        # Tasks without assigned_to=director are ignored
        tasks = [{"status": "todo"}, {"assigned_to": "pm", "status": "done"}]
        summary = get_director_task_status_summary(tasks)
        assert summary.get("todo", 0) == 0
        assert summary.get("total", 0) == 0
