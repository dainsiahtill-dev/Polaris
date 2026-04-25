"""Unit tests for DirectorStateTracker pure logic (no I/O, no filesystem).

Covers:
- _derive_qa_state (static)
- sanitize_task_description (static)
- build_taskboard_task_ref
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from polaris.cells.roles.adapters.internal.director.state_tracking import DirectorStateTracker


# ---------------------------------------------------------------------------
# QA state derivation
# ---------------------------------------------------------------------------


class TestDeriveQaState:
    def test_no_qa_required_empty(self) -> None:
        assert DirectorStateTracker._derive_qa_state({}, {}, "ready") == ""

    def test_qa_passed_true(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": True},
            {},
            "completed",
        )
        assert result == "passed"

    def test_qa_passed_false_completed(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": False},
            {},
            "completed",
        )
        assert result == "failed"

    def test_qa_passed_false_in_progress(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": False},
            {},
            "in_progress",
        )
        assert result == "rework"

    def test_qa_passed_none_completed(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": None},
            {},
            "completed",
        )
        assert result == "pending"

    def test_qa_passed_none_in_progress(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": None},
            {},
            "in_progress",
        )
        assert result == ""

    def test_metadata_rework_requested(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {},
            {"qa_rework_requested": True},
            "ready",
        )
        assert result == "rework"

    def test_failed_and_exhausted(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {},
            {"qa_rework_exhausted": True},
            "failed",
        )
        assert result == "exhausted"

    def test_adapter_result_takes_precedence_over_metadata(self) -> None:
        result = DirectorStateTracker._derive_qa_state(
            {"qa_required_for_final_verdict": True, "qa_passed": True},
            {"qa_rework_requested": True},
            "completed",
        )
        assert result == "passed"


# ---------------------------------------------------------------------------
# Task description sanitization
# ---------------------------------------------------------------------------


class TestSanitizeTaskDescription:
    def test_empty_returns_empty(self) -> None:
        assert DirectorStateTracker.sanitize_task_description("") == ""
        assert DirectorStateTracker.sanitize_task_description("   ") == ""

    def test_crlf_normalized(self) -> None:
        assert DirectorStateTracker.sanitize_task_description("line1\r\nline2") == "line1 line2"

    def test_heading_stripped(self) -> None:
        assert DirectorStateTracker.sanitize_task_description("# Header text") == "Header text"
        assert DirectorStateTracker.sanitize_task_description("## Another") == "Another"

    def test_list_prefix_stripped(self) -> None:
        assert DirectorStateTracker.sanitize_task_description("- item") == "item"
        assert DirectorStateTracker.sanitize_task_description("* item") == "item"
        assert DirectorStateTracker.sanitize_task_description("1. item") == "item"
        assert DirectorStateTracker.sanitize_task_description("(a) item") == "item"

    def test_code_blocks_skipped(self) -> None:
        assert DirectorStateTracker.sanitize_task_description("```python\ncode\n```") == ""

    def test_truncation(self) -> None:
        long_text = "word " * 100
        result = DirectorStateTracker.sanitize_task_description(long_text, max_chars=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_line_limit(self) -> None:
        text = "\n".join(f"line{i}" for i in range(10))
        result = DirectorStateTracker.sanitize_task_description(text)
        # Should take at most 6 lines
        assert len(result.split(" ")) <= 6


# ---------------------------------------------------------------------------
# Taskboard task reference
# ---------------------------------------------------------------------------


class TestBuildTaskboardTaskRef:
    def test_non_dict_task_returns_empty(self, tmp_path: Any) -> None:
        tracker = DirectorStateTracker(str(tmp_path))
        assert tracker.build_taskboard_task_ref("t1", lambda _: "not a dict") == {}  # type: ignore[arg-type,return-value]

    def test_basic_fields(self, tmp_path: Any) -> None:
        tracker = DirectorStateTracker(str(tmp_path))

        def _get_task(_tid: str) -> dict[str, Any]:
            return {
                "id": 42,
                "subject": "Fix bug",
                "status": "ready",
                "raw_status": "READY",
                "qa_state": "passed",
                "claimed_by": "director",
                "execution_backend": "code_edit",
                "resume_state": "idle",
                "session_id": "sess-1",
                "workflow_run_id": "run-1",
            }

        result = tracker.build_taskboard_task_ref("42", _get_task)
        assert result["id"] == "42"
        assert result["subject"] == "Fix bug"
        assert result["status"] == "ready"
        assert result["raw_status"] == "ready"
        assert result["qa_state"] == "passed"
        assert result["claimed_by"] == "director"
        assert result["execution_backend"] == "code_edit"
        assert result["resume_state"] == "idle"
        assert result["session_id"] == "sess-1"
        assert result["workflow_run_id"] == "run-1"

    def test_metadata_fallback(self, tmp_path: Any) -> None:
        tracker = DirectorStateTracker(str(tmp_path))

        def _get_task(_tid: str) -> dict[str, Any]:
            return {
                "id": 1,
                "metadata": {
                    "claimed_by": "architect",
                    "execution_backend": "projection_generate",
                    "execution_backend_source": "auto",
                    "resume_state": "paused",
                    "workflow_run_id": "run-2",
                    "projection": {"scenario_id": "sc-1", "experiment_id": "exp-1"},
                },
            }

        result = tracker.build_taskboard_task_ref("1", _get_task)
        assert result["claimed_by"] == "architect"
        assert result["execution_backend"] == "projection_generate"
        assert result["execution_backend_source"] == "auto"
        assert result["resume_state"] == "paused"
        assert result["session_id"] == ""
        assert result["workflow_run_id"] == "run-2"
        assert result["projection_scenario"] == "sc-1"
        assert result["projection_experiment_id"] == "exp-1"

    def test_runtime_execution_session_id(self, tmp_path: Any) -> None:
        tracker = DirectorStateTracker(str(tmp_path))

        def _get_task(_tid: str) -> dict[str, Any]:
            return {
                "id": 1,
                "metadata": {
                    "runtime_execution": {"session_id": "sess-2"},
                },
            }

        result = tracker.build_taskboard_task_ref("1", _get_task)
        assert result["session_id"] == "sess-2"

    def test_title_fallback(self, tmp_path: Any) -> None:
        tracker = DirectorStateTracker(str(tmp_path))

        def _get_task(_tid: str) -> dict[str, Any]:
            return {"id": 1, "title": "My Title"}

        result = tracker.build_taskboard_task_ref("1", _get_task)
        assert result["subject"] == "My Title"
