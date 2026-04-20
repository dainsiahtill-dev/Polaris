"""Unit tests for orchestration.pm_dispatch internal shangshuling_registry.

Tests LocalShangshulingPort by mocking the registry persistence layer
(_load_registry / _save_registry) for deterministic isolated behavior.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry import (
    LocalShangshulingPort,
    _load_registry,
    _normalize_task,
    _priority_value,
    _task_identity,
    get_shangshuling_port,
)

# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


class TestTaskIdentity:
    def test_prefers_id_field(self) -> None:
        task = {"id": "T01", "legacy_id": "old-1"}
        assert _task_identity(task) == "T01"

    def test_falls_back_to_legacy_id(self) -> None:
        task = {"legacy_id": "legacy-1", "title": "Build login"}
        assert _task_identity(task) == "legacy-1"

    def test_falls_back_to_metadata_legacy_id(self) -> None:
        task = {"metadata": {"legacy_id": "meta-legacy"}, "title": "x"}
        assert _task_identity(task) == "meta-legacy"

    def test_empty_when_no_identity(self) -> None:
        task = {"title": "no identity"}
        assert _task_identity(task) == ""

    def test_whitespace_stripped(self) -> None:
        task = {"id": "  T01  "}
        assert _task_identity(task) == "T01"


class TestPriorityValue:
    def test_integer(self) -> None:
        assert _priority_value({"priority": 3}) == 3
        assert _priority_value({"priority": 0}) == 0
        assert _priority_value({"priority": 9}) == 9

    def test_alias_critical(self) -> None:
        assert _priority_value({"priority": "critical"}) == 0
        assert _priority_value({"priority": "CRITICAL"}) == 0

    def test_alias_high(self) -> None:
        assert _priority_value({"priority": "high"}) == 1

    def test_alias_medium(self) -> None:
        assert _priority_value({"priority": "medium"}) == 5

    def test_alias_low(self) -> None:
        assert _priority_value({"priority": "low"}) == 9

    def test_unknown_defaults_to_medium(self) -> None:
        assert _priority_value({"priority": "urgent-ish"}) == 5

    def test_missing_priority(self) -> None:
        assert _priority_value({}) == 5

    def test_string_number(self) -> None:
        assert _priority_value({"priority": "3"}) == 3


class TestNormalizeTask:
    def test_normalizes_id_status_priority(self) -> None:
        task = {
            "id": "  T01  ",
            "status": "done",
            "priority": "high",
            "title": "Test",
        }
        result = _normalize_task(task)
        assert result["id"] == "T01"
        assert result["status"] == "done"
        assert result["priority"] == 1

    def test_sets_default_metadata(self) -> None:
        task = {"id": "t1", "title": "T"}
        result = _normalize_task(task)
        assert result["metadata"] == {}
        assert isinstance(result["metadata"], dict)

    def test_replaces_non_dict_metadata(self) -> None:
        task = {"id": "t1", "metadata": "not a dict"}
        result = _normalize_task(task)
        assert result["metadata"] == {}


# ---------------------------------------------------------------------------
# Registry persistence (private helpers)
# ---------------------------------------------------------------------------


class TestLoadSaveRegistry:
    def test_empty_file_returns_empty_registry(self, tmp_path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text("", encoding="utf-8")
        result = _load_registry(str(reg_path))
        assert result["tasks"] == []

    def test_corrupted_json_returns_empty(self, tmp_path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text("not valid json{{{", encoding="utf-8")
        result = _load_registry(str(reg_path))
        assert result["tasks"] == []

    def test_non_dict_returns_empty(self, tmp_path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text('["a", "b"]', encoding="utf-8")
        result = _load_registry(str(reg_path))
        assert result["tasks"] == []

    def test_missing_tasks_field_defaults_to_empty(self, tmp_path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text('{"version": 1}', encoding="utf-8")
        result = _load_registry(str(reg_path))
        assert result["tasks"] == []

    def test_non_list_tasks_defaults_to_empty(self, tmp_path) -> None:
        reg_path = tmp_path / "registry.json"
        reg_path.write_text('{"tasks": "bad"}', encoding="utf-8")
        result = _load_registry(str(reg_path))
        assert result["tasks"] == []


# ---------------------------------------------------------------------------
# LocalShangshulingPort – mocked persistence layer
# ---------------------------------------------------------------------------


def _make_mock_registry(tasks: list[dict]) -> dict:
    return {"version": 1, "workspace": "", "updated_at": "", "tasks": tasks}


class TestLocalShangshulingPortSync:
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_empty_tasks_returns_zero(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        assert port.sync_tasks_to_shangshuling("/ws", []) == 0
        assert port.sync_tasks_to_shangshuling("/ws", "not a list") == 0  # type: ignore[arg-type]

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_single_task(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        tasks = [{"id": "T01", "status": "todo", "priority": 1}]
        count = port.sync_tasks_to_shangshuling("/ws", tasks)
        assert count == 1
        mock_save.assert_called()
        call_registry = mock_save.call_args[0][1]
        assert len(call_registry["tasks"]) == 1
        assert call_registry["tasks"][0]["id"] == "T01"

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_normalizes_priority_and_status(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        tasks = [{"id": "T01", "status": "done", "priority": "high"}]
        port.sync_tasks_to_shangshuling("/ws", tasks)
        call_registry = mock_save.call_args[0][1]
        assert call_registry["tasks"][0]["status"] == "done"
        assert call_registry["tasks"][0]["priority"] == 1  # "high" -> 1

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_upserts_same_task(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([{"id": "T01", "status": "todo"}])
        port = LocalShangshulingPort()
        port.sync_tasks_to_shangshuling("/ws", [{"id": "T01", "status": "done"}])
        call_registry = mock_save.call_args[0][1]
        assert len(call_registry["tasks"]) == 1
        assert call_registry["tasks"][0]["status"] == "done"

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_skips_tasks_without_id(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        tasks = [{"status": "todo"}, {"id": "T01", "status": "done"}]
        count = port.sync_tasks_to_shangshuling("/ws", tasks)
        assert count == 1  # only valid task counted

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_merges_multiple_tasks(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        tasks = [
            {"id": "T01", "status": "todo", "priority": 1},
            {"id": "T02", "status": "in_progress", "priority": 3},
        ]
        port.sync_tasks_to_shangshuling("/ws", tasks)
        call_registry = mock_save.call_args[0][1]
        assert len(call_registry["tasks"]) == 2

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_sync_skips_non_dict_items(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        tasks = ["not a dict", 123, {"id": "T01", "status": "todo"}]  # type: ignore[list-item]
        count = port.sync_tasks_to_shangshuling("/ws", tasks)  # type: ignore[arg-type]
        assert count == 1


class TestLocalShangshulingPortReady:
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    def test_excludes_terminal_statuses(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry(
            [
                {"id": "T01", "status": "done"},
                {"id": "T02", "status": "failed"},
                {"id": "T03", "status": "blocked"},
                {"id": "T04", "status": "todo"},
            ]
        )
        port = LocalShangshulingPort()
        ready = port.get_shangshuling_ready_tasks("/ws")
        assert len(ready) == 1
        assert ready[0]["id"] == "T04"

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    def test_sorted_by_priority(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry(
            [
                {"id": "T03", "status": "todo", "priority": 9},  # low
                {"id": "T01", "status": "todo", "priority": 1},  # high
                {"id": "T02", "status": "todo", "priority": 5},  # medium
            ]
        )
        port = LocalShangshulingPort()
        ready = port.get_shangshuling_ready_tasks("/ws")
        ids = [t["id"] for t in ready]
        assert ids == ["T01", "T02", "T03"]

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    def test_respects_limit(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry(
            [{"id": f"T{i:02d}", "status": "todo", "priority": i} for i in range(10)]
        )
        port = LocalShangshulingPort()
        ready = port.get_shangshuling_ready_tasks("/ws", limit=3)
        assert len(ready) == 3

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    def test_zero_limit_returns_all(self, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry(
            [
                {"id": "T01", "status": "todo"},
                {"id": "T02", "status": "in_progress"},
            ]
        )
        port = LocalShangshulingPort()
        ready = port.get_shangshuling_ready_tasks("/ws", limit=0)
        assert len(ready) == 2


class TestLocalShangshulingPortRecordCompletion:
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_records_done(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([{"id": "T01", "status": "todo"}])
        port = LocalShangshulingPort()
        ok = port.record_shangshuling_task_completion("/ws", "T01", success=True, metadata={"duration": 10})
        assert ok is True
        call_registry = mock_save.call_args[0][1]
        assert call_registry["tasks"][0]["status"] == "done"

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_records_failed(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([{"id": "T01", "status": "todo"}])
        port = LocalShangshulingPort()
        ok = port.record_shangshuling_task_completion("/ws", "T01", success=False, metadata={"error": "timeout"})
        assert ok is True
        call_registry = mock_save.call_args[0][1]
        assert call_registry["tasks"][0]["status"] == "failed"

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_returns_false_for_unknown_task(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([])
        port = LocalShangshulingPort()
        ok = port.record_shangshuling_task_completion("/ws", "UNKNOWN", success=True, metadata={})
        assert ok is False

    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._load_registry")
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry._save_registry")
    def test_records_by_legacy_id(self, mock_save: MagicMock, mock_load: MagicMock) -> None:
        mock_load.return_value = _make_mock_registry([{"legacy_id": "legacy-T01", "status": "todo"}])
        port = LocalShangshulingPort()
        ok = port.record_shangshuling_task_completion("/ws", "legacy-T01", success=True, metadata={})
        assert ok is True


class TestLocalShangshulingPortArchiveHistory:
    @patch("polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry.append_jsonl")
    def test_archives_record_to_jsonl(self, mock_append: MagicMock) -> None:
        port = LocalShangshulingPort()
        port.archive_task_history(
            workspace_full="/fake",
            cache_root_full="/cache",
            run_id="run-1",
            iteration=1,
            normalized={"tasks": []},
            director_result={"status": "done"},
            timestamp="2026-03-23T00:00:00Z",
        )
        mock_append.assert_called_once()
        call_args = mock_append.call_args[0]
        assert call_args[1]["run_id"] == "run-1"
        assert call_args[1]["iteration"] == 1


class TestGetShangshulingPort:
    def test_returns_local_port_instance(self) -> None:
        port = get_shangshuling_port()
        assert isinstance(port, LocalShangshulingPort)
