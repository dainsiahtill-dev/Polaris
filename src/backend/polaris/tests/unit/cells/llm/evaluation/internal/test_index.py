"""Unit tests for polaris.cells.llm.evaluation.internal.index."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from polaris.cells.llm.evaluation.internal.index import (
    KernelFsReportsPort,
    _extract_ready_grade,
    _extract_run_id,
    _extract_suites,
    _extract_target,
    _get_path_lock,
    _get_reports_port,
    _load_index_file,
    _new_index_payload,
    _resolve_index_paths,
    _resolve_workspace_path,
    load_llm_test_index,
    reconcile_llm_test_index,
    reset_llm_test_index,
    set_reports_port,
    update_index_with_report,
)


class TestNewIndexPayload:
    """Tests for _new_index_payload function."""

    def test_structure(self) -> None:
        payload = _new_index_payload()
        assert payload["roles"] == {}
        assert payload["providers"] == {}
        assert payload["version"] == "2.0"


class TestResolveWorkspacePath:
    """Tests for _resolve_workspace_path function."""

    def test_none(self) -> None:
        assert _resolve_workspace_path(None) is None

    def test_empty_string(self) -> None:
        assert _resolve_workspace_path("") is None

    def test_whitespace(self) -> None:
        assert _resolve_workspace_path("   ") is None

    def test_string(self) -> None:
        assert _resolve_workspace_path("/tmp/ws") == "/tmp/ws"

    def test_object_with_workspace(self) -> None:
        obj = MagicMock()
        obj.workspace = "/tmp/ws"
        assert _resolve_workspace_path(obj) == "/tmp/ws"

    def test_object_without_workspace(self) -> None:
        obj = MagicMock()
        del obj.workspace
        assert _resolve_workspace_path(obj) is None


class TestResolveIndexPaths:
    """Tests for _resolve_index_paths function."""

    def test_returns_list(self) -> None:
        paths = _resolve_index_paths("/tmp/ws")
        assert isinstance(paths, list)
        assert len(paths) >= 1

    def test_unique_paths(self) -> None:
        paths = _resolve_index_paths("/tmp/ws")
        assert len(paths) == len(set(paths))


class TestLoadIndexFile:
    """Tests for _load_index_file function."""

    def test_not_found(self) -> None:
        assert _load_index_file("/nonexistent/path/index.json") is None

    def test_invalid_json(self, tmp_path) -> None:
        path = str(tmp_path / "bad.json")
        Path(path).write_text("not json", encoding="utf-8")
        assert _load_index_file(path) is None

    def test_not_dict(self, tmp_path) -> None:
        path = str(tmp_path / "list.json")
        Path(path).write_text("[1, 2, 3]", encoding="utf-8")
        assert _load_index_file(path) is None

    def test_valid(self, tmp_path) -> None:
        path = str(tmp_path / "valid.json")
        Path(path).write_text('{"roles": {}}', encoding="utf-8")
        result = _load_index_file(path)
        assert result == {"roles": {}}


class TestExtractTarget:
    """Tests for _extract_target function."""

    def test_from_target_dict(self) -> None:
        report = {"target": {"role": "pm", "provider_id": "p1", "model": "m1"}}
        assert _extract_target(report) == ("pm", "p1", "m1")

    def test_from_top_level(self) -> None:
        report = {"role": "architect", "provider_id": "p2", "model": "m2"}
        assert _extract_target(report) == ("architect", "p2", "m2")

    def test_fallback(self) -> None:
        assert _extract_target({}) == ("", "", "")


class TestExtractReadyGrade:
    """Tests for _extract_ready_grade function."""

    def test_from_final(self) -> None:
        report = {"final": {"ready": True, "grade": "PASS"}}
        assert _extract_ready_grade(report) == (True, "PASS")

    def test_from_summary(self) -> None:
        report = {"summary": {"ready": False}}
        assert _extract_ready_grade(report) == (False, "FAIL")

    def test_default(self) -> None:
        assert _extract_ready_grade({}) == (False, "UNKNOWN")

    def test_ready_without_grade(self) -> None:
        report = {"final": {"ready": True}}
        assert _extract_ready_grade(report) == (True, "PASS")


class TestExtractRunId:
    """Tests for _extract_run_id function."""

    def test_test_run_id(self) -> None:
        assert _extract_run_id({"test_run_id": "r1"}) == "r1"

    def test_run_id(self) -> None:
        assert _extract_run_id({"run_id": "r2"}) == "r2"

    def test_empty(self) -> None:
        assert _extract_run_id({}) == ""


class TestExtractSuites:
    """Tests for _extract_suites function."""

    def test_dict_format(self) -> None:
        report = {"suites": {"connectivity": {"ok": True}}}
        result = _extract_suites(report)
        assert result["connectivity"]["ok"] is True

    def test_list_format(self) -> None:
        report = {
            "suites": [
                {
                    "suite_name": "conn",
                    "total_cases": 10,
                    "passed_cases": 8,
                    "failed_cases": 2,
                },
            ],
        }
        result = _extract_suites(report)
        # ok is computed as passed >= total; 8 >= 10 is False
        assert result["conn"]["ok"] is False
        assert result["conn"]["failed_cases"] == 2

    def test_empty(self) -> None:
        assert _extract_suites({}) == {}


class TestPathLock:
    """Tests for _get_path_lock function."""

    def test_same_path_same_lock(self) -> None:
        lock1 = _get_path_lock("/tmp/test.json")
        lock2 = _get_path_lock("/tmp/test.json")
        assert lock1 is lock2

    def test_different_paths_different_locks(self) -> None:
        lock1 = _get_path_lock("/tmp/a.json")
        lock2 = _get_path_lock("/tmp/b.json")
        assert lock1 is not lock2


class TestReportsPort:
    """Tests for KernelFsReportsPort and related functions."""

    def test_set_and_get_port(self) -> None:
        mock_port = MagicMock(spec=KernelFsReportsPort)
        set_reports_port(mock_port)
        assert _get_reports_port() is mock_port
        # Reset to default
        set_reports_port(None)

    def test_default_port_list_json(self, tmp_path) -> None:
        set_reports_port(None)
        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
        port = _get_reports_port()
        files = port.list_json_files(str(tmp_path))
        assert "a.json" in files
        assert "b.txt" not in files

    def test_default_port_dir_exists(self, tmp_path) -> None:
        set_reports_port(None)
        port = _get_reports_port()
        assert port.dir_exists(str(tmp_path)) is True
        assert port.dir_exists(str(tmp_path / "nonexistent")) is False


class TestLoadLlmTestIndex:
    """Tests for load_llm_test_index function."""

    def test_no_index_exists(self, tmp_path) -> None:
        with patch.dict(os.environ, {"KERNELONE_WORKSPACE": str(tmp_path)}):
            result = load_llm_test_index()
        assert result["version"] == "2.0"

    def test_loads_existing(self, tmp_path) -> None:
        # Pre-create an index file at the workspace-local path
        index_dir = tmp_path / ".polaris"
        index_dir.mkdir()
        index_file = index_dir / "llm_test_index.json"
        index_file.write_text('{"custom": true}', encoding="utf-8")
        result = load_llm_test_index(str(tmp_path))
        assert result.get("custom") is True


class TestResetLlmTestIndex:
    """Tests for reset_llm_test_index function."""

    def test_creates_index(self, tmp_path) -> None:
        with patch.dict(os.environ, {"KERNELONE_WORKSPACE": str(tmp_path)}):
            reset_llm_test_index()
            result = load_llm_test_index()
        assert result["version"] == "2.0"
        assert "reset_at" in result


class TestReconcileLlmTestIndex:
    """Tests for reconcile_llm_test_index function."""

    def test_no_workspace(self) -> None:
        result = reconcile_llm_test_index(None)
        assert result["version"] == "2.0"

    def test_no_reports_dir(self, tmp_path) -> None:
        mock_port = MagicMock()
        mock_port.dir_exists.return_value = False
        set_reports_port(mock_port)
        try:
            result = reconcile_llm_test_index(str(tmp_path))
            assert result["version"] == "2.0"
        finally:
            set_reports_port(None)

    def test_with_reports(self, tmp_path) -> None:
        # Create report files
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        report = {
            "target": {"role": "pm", "provider_id": "ollama", "model": "llama3"},
            "final": {"ready": True, "grade": "PASS"},
            "test_run_id": "r1",
            "timestamp": "2024-01-01T00:00:00",
        }
        (reports_dir / "report_1.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )

        mock_port = MagicMock()
        mock_port.dir_exists.return_value = True
        mock_port.list_json_files.return_value = ["report_1.json"]
        set_reports_port(mock_port)

        try:
            with patch(
                "polaris.cells.llm.evaluation.internal.index.resolve_runtime_path",
                return_value=str(reports_dir),
            ):
                result = reconcile_llm_test_index(str(tmp_path))
            assert "pm" in result["roles"]
            assert "ollama" in result["providers"]
            assert result["last_reconcile"] is not None
        finally:
            set_reports_port(None)


class TestUpdateIndexWithReport:
    """Tests for update_index_with_report function."""

    def test_no_workspace(self) -> None:
        # Should not raise
        update_index_with_report(None, {})

    def test_updates_index(self, tmp_path) -> None:
        report = {
            "role": "pm",
            "provider_id": "ollama",
            "model": "llama3",
            "final": {"ready": True, "grade": "PASS"},
            "test_run_id": "r1",
            "timestamp": "2024-01-01T00:00:00",
            "suites": {},
        }
        update_index_with_report(str(tmp_path), report)
        index = load_llm_test_index(str(tmp_path))
        assert "pm" in index["roles"]
        assert "ollama" in index["providers"]
        assert index["last_update"] is not None
