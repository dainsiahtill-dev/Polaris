"""Tests for RoleExecutionKernel._commit_turn_to_snapshot().

验证：
1. Happy Path：事件正确追加到 snapshot
2. Edge Case：context_override 为 None 时不抛异常
3. Edge Case：context_os_snapshot 不是 dict 时不抛异常
4. Regression：transcript_log 是 append-only（历史事件不被修改）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleTurnRequest


class TestCommitTurnToSnapshot:
    """Happy path and regression tests for _commit_turn_to_snapshot."""

    def test_happy_path_appends_events(self) -> None:
        """Events are appended to transcript_log with correct sequence numbers."""
        request = MagicMock(spec=RoleTurnRequest)
        request.context_override = {
            "context_os_snapshot": {
                "transcript_log": [
                    {
                        "event_id": "e0",
                        "sequence": 0,
                        "role": "user",
                        "kind": "user_turn",
                        "route": "turn",
                        "content": "hello",
                    },
                ],
                "version": 1,
            }
        }

        turn_events_metadata = [
            {"event_id": "e1", "role": "assistant", "kind": "assistant_turn", "content": "hi"},
            {"event_id": "e2", "role": "tool", "kind": "tool_result", "content": "done"},
        ]

        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,  # type: ignore[arg-type]
            turn_id="t42",
            turn_history=[("user", "hello")],
            turn_events_metadata=turn_events_metadata,
            tool_results=[],
        )

        snapshot: dict[str, Any] = request.context_override["context_os_snapshot"]
        transcript_log: list[dict[str, Any]] = snapshot["transcript_log"]

        assert len(transcript_log) == 3
        assert transcript_log[0]["event_id"] == "e0"
        assert transcript_log[1]["event_id"] == "e1"
        assert transcript_log[1]["sequence"] == 1
        assert transcript_log[2]["event_id"] == "e2"
        assert transcript_log[2]["sequence"] == 2
        assert snapshot["version"] == 2
        assert "last_updated_at" in snapshot

    def test_append_only_preserves_history(self) -> None:
        """Regression: existing transcript_log entries must not be mutated."""
        original_event = {
            "event_id": "e0",
            "sequence": 0,
            "role": "user",
            "kind": "user_turn",
            "route": "turn",
            "content": "hello",
        }
        request = MagicMock(spec=RoleTurnRequest)
        request.context_override = {
            "context_os_snapshot": {
                "transcript_log": [original_event.copy()],
                "version": 5,
            }
        }

        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,  # type: ignore[arg-type]
            turn_id="t99",
            turn_history=[],
            turn_events_metadata=[
                {"event_id": "e1", "role": "assistant", "kind": "assistant_turn", "content": "reply"}
            ],
            tool_results=[],
        )

        snapshot: dict[str, Any] = request.context_override["context_os_snapshot"]
        transcript_log: list[dict[str, Any]] = snapshot["transcript_log"]

        # Original event must remain untouched
        assert transcript_log[0] == original_event
        assert len(transcript_log) == 2

    def test_none_context_override_returns_silently(self) -> None:
        """Edge case: context_override is None → function returns without error."""
        request = MagicMock(spec=RoleTurnRequest)
        request.context_override = None

        # Must not raise
        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,  # type: ignore[arg-type]
            turn_id="t1",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=[],
        )

    def test_non_dict_snapshot_returns_silently(self) -> None:
        """Edge case: context_os_snapshot is not a dict → function returns without error."""
        request = MagicMock(spec=RoleTurnRequest)
        request.context_override = {"context_os_snapshot": "not_a_dict"}

        # Must not raise
        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,  # type: ignore[arg-type]
            turn_id="t1",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=[],
        )

    def test_tool_results_written_to_working_state(self) -> None:
        """Tool results are stored in working_state when present."""
        request = MagicMock(spec=RoleTurnRequest)
        request.context_override = {
            "context_os_snapshot": {
                "transcript_log": [],
                "version": 0,
            }
        }

        tool_results = [{"tool": "read_file", "result": "content"}]

        RoleExecutionKernel._commit_turn_to_snapshot(
            request=request,  # type: ignore[arg-type]
            turn_id="t7",
            turn_history=[],
            turn_events_metadata=[],
            tool_results=tool_results,
        )

        snapshot: dict[str, Any] = request.context_override["context_os_snapshot"]
        working_state: dict[str, Any] = snapshot["working_state"]
        assert working_state["last_tool_results"] == tool_results
