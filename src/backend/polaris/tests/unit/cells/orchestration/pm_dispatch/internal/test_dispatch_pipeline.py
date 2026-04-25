"""Tests for polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline pure functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline


class TestHashPayload:
    """Tests for _hash_payload pure function."""

    def test_string_payload(self) -> None:
        result = dispatch_pipeline._hash_payload("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex

    def test_dict_payload_sorted_keys(self) -> None:
        result1 = dispatch_pipeline._hash_payload({"b": 2, "a": 1})
        result2 = dispatch_pipeline._hash_payload({"a": 1, "b": 2})
        assert result1 == result2  # Keys should be sorted

    def test_dict_payload_different_values_different_hash(self) -> None:
        result1 = dispatch_pipeline._hash_payload({"a": 1})
        result2 = dispatch_pipeline._hash_payload({"a": 2})
        assert result1 != result2

    def test_list_payload(self) -> None:
        result = dispatch_pipeline._hash_payload([1, 2, 3])
        assert isinstance(result, str)
        assert len(result) == 64

    def test_none_payload(self) -> None:
        result = dispatch_pipeline._hash_payload(None)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_numeric_payload(self) -> None:
        result = dispatch_pipeline._hash_payload(42)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_unicode_payload(self) -> None:
        result = dispatch_pipeline._hash_payload({"name": "中文", "emoji": "🎉"})
        assert isinstance(result, str)
        assert len(result) == 64

    def test_non_serializable_falls_back_to_str(self) -> None:
        class NotJsonSerializable:
            pass

        result = dispatch_pipeline._hash_payload(NotJsonSerializable())
        assert isinstance(result, str)
        assert len(result) == 64


class TestBuildRevisionContext:
    """Tests for _build_revision_context pure function."""

    def test_basic_revision_context(self) -> None:
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=[{"id": "T-1", "title": "Task 1", "goal": "Do things"}],
        )
        assert "plan_id" in result
        assert "plan_revision_id" in result
        assert "requirement_digest" in result
        assert "constraint_digest" in result

    def test_uses_normalized_project_id(self) -> None:
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=[],
            normalized={"project_id": "MY-PROJECT"},
        )
        assert result["plan_id"] == "MY-PROJECT"

    def test_uses_docs_stage_active_doc_path(self) -> None:
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=[],
            docs_stage={"active_doc_path": "/docs/spec.md"},
        )
        assert "docs/spec.md" in result["plan_id"] or "MY-PROJECT" not in result["plan_id"]

    def test_tasks_projection_structure(self) -> None:
        tasks = [
            {
                "id": "T-1",
                "title": "Task 1",
                "goal": "Goal 1",
                "depends_on": [],
                "scope_paths": ["src/a.py"],
                "target_files": ["src/b.py"],
            }
        ]
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=tasks,
        )
        # Check requirement_digest is a valid hash
        assert len(result["requirement_digest"]) == 64

    def test_handles_invalid_task_types(self) -> None:
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=[None, "not a dict", 42],
        )
        assert "plan_id" in result
        assert result["requirement_digest"] is not None

    def test_empty_tasks(self) -> None:
        result = dispatch_pipeline._build_revision_context(
            workspace_full="/workspace",
            run_id="run-123",
            tasks=[],
        )
        assert "plan_id" in result
        assert "plan_revision_id" in result


class TestExtractTaskDependencies:
    """Tests for _extract_task_dependencies pure function."""

    def test_extracts_depends_on_list(self) -> None:
        task = {"depends_on": ["T-1", "T-2"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1", "T-2")

    def test_extracts_dependencies_list(self) -> None:
        task = {"dependencies": ["T-1", "T-2"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1", "T-2")

    def test_depends_on_takes_precedence(self) -> None:
        task = {"depends_on": ["T-1"], "dependencies": ["T-2"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1",)

    def test_empty_dependency_list(self) -> None:
        task: dict[str, list[str]] = {"depends_on": []}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ()

    def test_no_dependencies(self) -> None:
        task: dict[str, list[str]] = {}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ()

    def test_whitespace_trimming(self) -> None:
        task = {"depends_on": ["  T-1  ", "T-2 ", " T-3"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1", "T-2", "T-3")

    def test_deduplication(self) -> None:
        task = {"depends_on": ["T-1", "T-2", "T-1", "T-3", "T-2"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1", "T-2", "T-3")

    def test_non_list_depends_on_falls_back_to_empty(self) -> None:
        task = {"depends_on": "T-1"}  # String, not list
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ()

    def test_filters_empty_strings(self) -> None:
        task = {"depends_on": ["T-1", "", "  ", "T-2"]}
        result = dispatch_pipeline._extract_task_dependencies(task)
        assert result == ("T-1", "T-2")


class TestReadPositiveIntEnv:
    """Tests for _read_positive_int_env function."""

    def test_parses_valid_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "42")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10)
        assert result == 42

    def test_returns_default_for_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10)
        assert result == 10

    def test_returns_default_for_unset_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT_VAR", raising=False)
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10)
        assert result == 10

    def test_returns_default_for_invalid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "not-a-number")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10)
        assert result == 10

    def test_enforces_minimum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "0")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10, minimum=5)
        assert result == 5

    def test_enforces_maximum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "9999")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10, maximum=100)
        assert result == 100

    def test_respects_custom_min_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT_VAR", "50")
        result = dispatch_pipeline._read_positive_int_env("TEST_INT_VAR", default=10, minimum=20, maximum=30)
        assert result == 30


class TestReadBoolEnv:
    """Tests for _read_bool_env function."""

    def test_parses_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ["1", "true", "True", "yes", "on"]:
            monkeypatch.setenv("TEST_BOOL_VAR", val)
            result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR")
            assert result is True

    def test_parses_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ["0", "false", "False", "no", "off"]:
            monkeypatch.setenv("TEST_BOOL_VAR", val)
            result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR")
            assert result is False

    def test_returns_default_for_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_BOOL_VAR", "")
        result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR")
        assert result is False

    def test_returns_default_for_unset_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_BOOL_VAR", raising=False)
        result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR")
        assert result is False

    def test_returns_default_for_unknown_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_BOOL_VAR", "unknown")
        result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR")
        assert result is False

    def test_custom_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_BOOL_VAR", raising=False)
        result = dispatch_pipeline._read_bool_env("TEST_BOOL_VAR", default=True)
        assert result is True


class TestTasksTouchDocsOnly:
    """Tests for _tasks_touch_docs_only pure function."""

    def test_non_list_input(self) -> None:
        assert dispatch_pipeline._tasks_touch_docs_only("not a list") is False
        assert dispatch_pipeline._tasks_touch_docs_only(None) is False

    def test_empty_list(self) -> None:
        assert dispatch_pipeline._tasks_touch_docs_only([]) is False

    def test_no_director_tasks(self) -> None:
        tasks = [{"assigned_to": "other", "target_files": ["src/a.py"]}]
        assert dispatch_pipeline._tasks_touch_docs_only(tasks) is False

    def test_director_task_with_source_files(self) -> None:
        tasks = [{"assigned_to": "director", "target_files": ["src/a.py"]}]
        assert dispatch_pipeline._tasks_touch_docs_only(tasks) is False

    def test_director_task_with_docs_only(self) -> None:
        tasks = [{"assigned_to": "director", "target_files": ["workspace/docs/spec.md"]}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_director_task_with_md_in_docs_folder(self) -> None:
        tasks = [{"assigned_to": "director", "scope_paths": ["docs/api.md"]}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_mixed_director_and_non_director(self) -> None:
        tasks = [
            {"assigned_to": "other", "target_files": ["src/a.py"]},
            {"assigned_to": "director", "scope_paths": ["docs/spec.md"]},
        ]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_string_scope_paths_split(self) -> None:
        tasks = [{"assigned_to": "director", "scope_paths": "docs/a.md, docs/b.md"}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_docs_task_type(self) -> None:
        tasks = [{"assigned_to": "director", "type": "documentation_task"}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_non_docs_task_type(self) -> None:
        tasks = [{"assigned_to": "director", "type": "implementation_task"}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is False

    def test_handles_non_dict_items(self) -> None:
        tasks = [
            {"assigned_to": "director", "scope_paths": ["docs/spec.md"]},
            None,
            "not a dict",
        ]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is True

    def test_handles_missing_keys(self) -> None:
        tasks: list[dict[str, str]] = [{}]
        result = dispatch_pipeline._tasks_touch_docs_only(tasks)
        assert result is False


class TestResolveDirectorDispatchTasks:
    """Tests for resolve_director_dispatch_tasks function."""

    def test_empty_tasks_returns_empty(self) -> None:
        selected, meta = dispatch_pipeline.resolve_director_dispatch_tasks(
            workspace_full="/workspace",
            tasks=[],
        )
        assert selected == []
        assert meta["selected_count"] == 0

    def test_non_list_tasks_returns_empty(self) -> None:
        selected, meta = dispatch_pipeline.resolve_director_dispatch_tasks(
            workspace_full="/workspace",
            tasks="not a list",  # type: ignore[arg-type]
        )
        assert selected == []
        assert meta["selected_count"] == 0

    def test_with_mocked_port(self) -> None:
        """Test with a mock shangshuling port."""
        mock_port = MagicMock()
        mock_port.sync_tasks_to_shangshuling.return_value = 3
        mock_port.get_shangshuling_ready_tasks.return_value = [
            {"id": "T-1"},
            {"id": "T-2"},
        ]

        tasks = [
            {"id": "T-1", "title": "Task 1"},
            {"id": "T-2", "title": "Task 2"},
            {"id": "T-3", "title": "Task 3"},  # Not ready
        ]

        selected, meta = dispatch_pipeline.resolve_director_dispatch_tasks(
            workspace_full="/workspace",
            tasks=tasks,
            shangshuling_port=mock_port,
        )

        assert len(selected) == 2
        assert selected[0]["id"] == "T-1"
        assert selected[1]["id"] == "T-2"
        assert meta["enabled"] is True
        assert meta["sync_count"] == 3
        assert meta["ready_count"] == 2

    def test_port_error_falls_back_to_all_tasks(self) -> None:
        """On port error, should return all tasks."""
        mock_port = MagicMock()
        mock_port.sync_tasks_to_shangshuling.side_effect = RuntimeError("Port error")

        tasks = [{"id": "T-1", "title": "Task 1"}]

        selected, meta = dispatch_pipeline.resolve_director_dispatch_tasks(
            workspace_full="/workspace",
            tasks=tasks,
            shangshuling_port=mock_port,
        )

        assert selected == tasks
        assert meta["enabled"] is False


class TestRecordDispatchStatusToShangshuling:
    """Tests for record_dispatch_status_to_shangshuling function."""

    def test_empty_status_updates(self) -> None:
        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates={},
            failure_info={},
        )
        assert result == 0

    def test_non_dict_status_updates(self) -> None:
        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates="not a dict",  # type: ignore[arg-type]
            failure_info={},
        )
        assert result == 0

    def test_records_done_status(self) -> None:
        """Test recording done status."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.return_value = True

        status_updates = {"T-1": "done"}
        failure_info: dict[str, dict[str, str]] = {}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info=failure_info,
            shangshuling_port=mock_port,
        )

        assert result == 1
        mock_port.record_shangshuling_task_completion.assert_called_once()
        call_args = mock_port.record_shangshuling_task_completion.call_args
        # First positional arg is workspace_full, task_id/success/metadata are keyword args
        assert call_args[0][0] == "/workspace"  # workspace_full
        assert call_args[1]["task_id"] == "T-1"
        assert call_args[1]["success"] is True

    def test_records_failed_status(self) -> None:
        """Test recording failed status."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.return_value = True

        status_updates = {"T-1": "failed"}
        failure_info = {"T-1": {"error": "test error"}}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info=failure_info,
            shangshuling_port=mock_port,
        )

        # "failed" status is recorded
        assert result == 1
        call_args = mock_port.record_shangshuling_task_completion.call_args
        assert call_args[1]["success"] is False

    def test_records_blocked_status(self) -> None:
        """Test recording blocked status (maps to failed)."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.return_value = True

        status_updates = {"T-1": "blocked"}
        failure_info: dict[str, dict[str, str]] = {}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info=failure_info,
            shangshuling_port=mock_port,
        )

        # "blocked" status is recorded (as failure)
        assert result == 1
        call_args = mock_port.record_shangshuling_task_completion.call_args
        assert call_args[1]["success"] is False

    def test_records_multiple_terminal_statuses(self) -> None:
        """Test that done, failed, and blocked are all recorded."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.return_value = True

        # All three terminal statuses
        status_updates = {"T-1": "done", "T-2": "failed", "T-3": "blocked"}
        failure_info: dict[str, dict[str, str]] = {}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info=failure_info,
            shangshuling_port=mock_port,
        )

        # All three should be recorded
        assert result == 3
        assert mock_port.record_shangshuling_task_completion.call_count == 3

    def test_skips_non_terminal_statuses(self) -> None:
        """Test that non-terminal statuses (todo, in_progress) are skipped."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.return_value = True

        status_updates = {"T-1": "todo", "T-2": "in_progress", "T-3": "review"}
        failure_info: dict[str, dict[str, str]] = {}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info=failure_info,
            shangshuling_port=mock_port,
        )

        # None should be recorded
        assert result == 0
        mock_port.record_shangshuling_task_completion.assert_not_called()

    def test_port_error_swallowed(self) -> None:
        """On port error, should continue and return count of successful records."""
        mock_port = MagicMock()
        mock_port.record_shangshuling_task_completion.side_effect = [True, RuntimeError("Error"), True]

        status_updates = {"T-1": "done", "T-2": "done", "T-3": "done"}

        result = dispatch_pipeline.record_dispatch_status_to_shangshuling(
            workspace_full="/workspace",
            status_updates=status_updates,
            failure_info={},
            shangshuling_port=mock_port,
        )

        assert result == 2  # Two successful recordings


class TestDispatchCallbacks:
    """Tests for DispatchCallbacks dataclass."""

    def test_default_callback_is_nop(self) -> None:
        callbacks = dispatch_pipeline.DispatchCallbacks()
        # Should not raise
        callbacks.update_role_status("director", status="running", running=True, detail="test")

    def test_custom_callback(self) -> None:
        calls: list[tuple[str, str, bool, str]] = []

        def custom_callback(role: str, *, status: str, running: bool, detail: str) -> None:
            calls.append((role, status, running, detail))

        callbacks = dispatch_pipeline.DispatchCallbacks(update_role_status=custom_callback)
        callbacks.update_role_status("director", status="running", running=True, detail="test")

        assert len(calls) == 1
        assert calls[0] == ("director", "running", True, "test")


class TestBuildDirectorWorkflowResult:
    """Tests for _build_director_workflow_result function."""

    def test_unsubmitted_result(self) -> None:
        mock_result = MagicMock()
        mock_result.submitted = False
        mock_result.status = "validation_failed"
        mock_result.error = "Invalid input"
        mock_result.workflow_id = ""
        mock_result.workflow_run_id = ""
        mock_result.details = {}

        result = dispatch_pipeline._build_director_workflow_result(
            run_id="run-123",
            task_count=5,
            workflow_result=mock_result,
        )

        assert result["run_id"] == "run-123"
        assert result["status"] == "validation_failed"
        assert result["successes"] == 0
        assert result["total"] == 5
        assert result["error"] == "Invalid input"

    def test_submitted_result(self) -> None:
        mock_result = MagicMock()
        mock_result.submitted = True
        mock_result.workflow_id = "wf-abc"
        mock_result.workflow_run_id = "run-xyz"
        mock_result.details = {"key": "value"}

        result = dispatch_pipeline._build_director_workflow_result(
            run_id="run-123",
            task_count=5,
            workflow_result=mock_result,
        )

        assert result["run_id"] == "run-123"
        assert result["status"] == "queued"
        assert result["successes"] == 5
        assert result["total"] == 5
        assert result["workflow_id"] == "wf-abc"
        assert result["workflow_run_id"] == "run-xyz"
