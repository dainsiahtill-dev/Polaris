"""Minimum test suite for `director.execution` public contracts.

Tests cover:
- ExecuteDirectorTaskCommandV1: construction, field validation, immutability
- RetryDirectorTaskCommandV1: boundary values
- GetDirectorTaskStatusQueryV1: optional run_id
- DirectorTaskStartedEventV1 / DirectorTaskCompletedEventV1: event guards
- DirectorExecutionResultV1: ok/error invariant, evidence_paths coercion
- DirectorExecutionError: structured exception attributes
- patch_apply_engine helpers: parse_full_file_blocks, parse_search_replace_blocks
  (pure text transformations, no I/O)
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.director.execution.internal.patch_apply_engine import (
    parse_full_file_blocks,
    parse_search_replace_blocks,
)
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionError,
    DirectorExecutionResultV1,
    DirectorTaskCompletedEventV1,
    DirectorTaskStartedEventV1,
    ExecuteDirectorTaskCommandV1,
    GetDirectorTaskStatusQueryV1,
    RetryDirectorTaskCommandV1,
)

# ---------------------------------------------------------------------------
# Happy path: command construction
# ---------------------------------------------------------------------------


class TestExecuteDirectorTaskCommandV1HappyPath:
    """ExecuteDirectorTaskCommandV1 is constructed and validated correctly."""

    def test_minimal_fields_accepted(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t-001", workspace="/ws", instruction="Fix the bug")
        assert cmd.task_id == "t-001"
        assert cmd.workspace == "/ws"
        assert cmd.instruction == "Fix the bug"

    def test_default_attempt_is_one(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t-002", workspace="/ws", instruction="Do task")
        assert cmd.attempt == 1

    def test_metadata_dict_is_copied(self) -> None:
        meta: dict[str, Any] = {"env": "prod"}
        cmd = ExecuteDirectorTaskCommandV1(task_id="t-003", workspace="/ws", instruction="x", metadata=meta)
        assert cmd.metadata == meta
        meta["injected"] = True
        assert "injected" not in cmd.metadata

    def test_run_id_optional_default_none(self) -> None:
        cmd = ExecuteDirectorTaskCommandV1(task_id="t-004", workspace="/ws", instruction="y")
        assert cmd.run_id is None


class TestRetryDirectorTaskCommandV1HappyPath:
    """RetryDirectorTaskCommandV1 boundary values."""

    def test_default_max_attempts(self) -> None:
        cmd = RetryDirectorTaskCommandV1(task_id="t-005", workspace="/ws", reason="LLM timeout")
        assert cmd.max_attempts == 3

    def test_custom_max_attempts(self) -> None:
        cmd = RetryDirectorTaskCommandV1(task_id="t-006", workspace="/ws", reason="flaky tool", max_attempts=5)
        assert cmd.max_attempts == 5


# ---------------------------------------------------------------------------
# Edge cases: empty-string guard and boundary values
# ---------------------------------------------------------------------------


class TestExecuteDirectorTaskCommandV1EdgeCases:
    """Empty / whitespace fields must be rejected."""

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            ExecuteDirectorTaskCommandV1(task_id="", workspace="/ws", instruction="x")

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ExecuteDirectorTaskCommandV1(task_id="t-007", workspace="   ", instruction="x")

    def test_empty_instruction_raises(self) -> None:
        with pytest.raises(ValueError, match="instruction"):
            ExecuteDirectorTaskCommandV1(task_id="t-008", workspace="/ws", instruction="")

    def test_attempt_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            ExecuteDirectorTaskCommandV1(task_id="t-009", workspace="/ws", instruction="x", attempt=0)


class TestRetryDirectorTaskCommandV1EdgeCases:
    """Boundary: max_attempts must be >= 1."""

    def test_max_attempts_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryDirectorTaskCommandV1(task_id="t-010", workspace="/ws", reason="x", max_attempts=0)

    def test_max_attempts_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryDirectorTaskCommandV1(task_id="t-011", workspace="/ws", reason="x", max_attempts=-1)


# ---------------------------------------------------------------------------
# GetDirectorTaskStatusQueryV1
# ---------------------------------------------------------------------------


class TestGetDirectorTaskStatusQueryV1:
    """Query accepts optional run_id."""

    def test_query_without_run_id(self) -> None:
        q = GetDirectorTaskStatusQueryV1(task_id="t-012", workspace="/ws")
        assert q.run_id is None

    def test_query_with_run_id(self) -> None:
        q = GetDirectorTaskStatusQueryV1(task_id="t-013", workspace="/ws", run_id="run-1")
        assert q.run_id == "run-1"


# ---------------------------------------------------------------------------
# Event contracts
# ---------------------------------------------------------------------------


class TestDirectorTaskEvents:
    """Event dataclasses enforce non-empty required fields."""

    def test_started_event_valid(self) -> None:
        ev = DirectorTaskStartedEventV1(
            event_id="e-1", task_id="t-014", workspace="/ws", started_at="2026-01-01T00:00:00Z"
        )
        assert ev.event_id == "e-1"

    def test_started_event_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            DirectorTaskStartedEventV1(event_id="", task_id="t-015", workspace="/ws", started_at="2026-01-01T00:00:00Z")

    def test_completed_event_valid(self) -> None:
        ev = DirectorTaskCompletedEventV1(
            event_id="e-2", task_id="t-016", workspace="/ws", status="completed", completed_at="2026-01-01T00:00:01Z"
        )
        assert ev.status == "completed"

    def test_completed_event_empty_status_raises(self) -> None:
        with pytest.raises(ValueError):
            DirectorTaskCompletedEventV1(
                event_id="e-3", task_id="t-017", workspace="/ws", status="", completed_at="2026-01-01T00:00:01Z"
            )


# ---------------------------------------------------------------------------
# DirectorExecutionResultV1 invariant
# ---------------------------------------------------------------------------


class TestDirectorExecutionResultV1:
    """ok/error invariant and evidence_paths coercion."""

    def test_success_result_accepts_no_error(self) -> None:
        result = DirectorExecutionResultV1(ok=True, task_id="t-018", workspace="/ws", status="completed")
        assert result.ok is True
        assert result.error_code is None

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            DirectorExecutionResultV1(ok=False, task_id="t-019", workspace="/ws", status="failed")

    def test_failed_result_with_error_code_valid(self) -> None:
        result = DirectorExecutionResultV1(
            ok=False, task_id="t-020", workspace="/ws", status="failed", error_code="TIMEOUT"
        )
        assert result.error_code == "TIMEOUT"

    def test_evidence_paths_coerced_to_tuple(self) -> None:
        result = DirectorExecutionResultV1(
            ok=True,
            task_id="t-021",
            workspace="/ws",
            status="ok",
            evidence_paths=("/ws/runtime/f1.json", "/ws/runtime/f2.json"),
        )
        assert isinstance(result.evidence_paths, tuple)
        assert len(result.evidence_paths) == 2

    def test_evidence_paths_default_empty_tuple(self) -> None:
        result = DirectorExecutionResultV1(ok=True, task_id="t-022", workspace="/ws", status="ok")
        assert result.evidence_paths == ()


# ---------------------------------------------------------------------------
# DirectorExecutionError
# ---------------------------------------------------------------------------


class TestDirectorExecutionError:
    """Structured exception carries code and details."""

    def test_default_code(self) -> None:
        err = DirectorExecutionError("task failed")
        assert err.code == "director_execution_error"

    def test_custom_code_and_details(self) -> None:
        err = DirectorExecutionError("blueprint missing", code="BLUEPRINT_NOT_FOUND", details={"task_id": "t-023"})
        assert err.code == "BLUEPRINT_NOT_FOUND"
        assert err.details == {"task_id": "t-023"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            DirectorExecutionError("")


# ---------------------------------------------------------------------------
# Failure path: patch_apply_engine pure-text helpers
# These are thin wrappers around ProtocolParser; test return type and shape.
# ---------------------------------------------------------------------------


class TestPatchApplyEnginePureTextHelpers:
    """parse_full_file_blocks and parse_search_replace_blocks are pure getters."""

    def test_parse_full_file_blocks_returns_list(self) -> None:
        # Returns a list regardless of input validity
        result = parse_full_file_blocks("random text with no markers")
        assert isinstance(result, list)

    def test_parse_full_file_blocks_returns_file_operations(self) -> None:
        # With correctly formatted FILE input, returns FileOperation list
        text = 'FILE: src/example.py\ndef hello():\n    return "world"\nEND FILE'
        ops = parse_full_file_blocks(text)
        assert len(ops) == 1
        op = ops[0]
        assert op.path == "src/example.py"
        assert hasattr(op, "replace")
        assert hasattr(op, "edit_type")

    def test_parse_full_file_blocks_empty_input_returns_empty_list(self) -> None:
        ops = parse_full_file_blocks("")
        assert ops == []

    def test_parse_search_replace_blocks_returns_list(self) -> None:
        result = parse_search_replace_blocks("random text with no markers")
        assert isinstance(result, list)

    def test_parse_search_replace_blocks_no_match_returns_empty(self) -> None:
        ops = parse_search_replace_blocks("no blocks here")
        assert ops == []

    def test_parse_full_file_blocks_multiple_blocks(self) -> None:
        # Correct FILE: format (not <<<<<< FILE:)
        multi = "FILE: a.py\ncontent_a\nEND FILE\nFILE: b.py\ncontent_b\nEND FILE"
        ops = parse_full_file_blocks(multi)
        assert len(ops) == 2
        paths = {op.path for op in ops}
        assert "a.py" in paths
        assert "b.py" in paths
