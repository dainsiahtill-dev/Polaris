"""Tests for ``internal/dlq.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.runtime.task_market.internal.dlq import DLQManager
from polaris.cells.runtime.task_market.internal.errors import TaskMarketError, TaskNotFoundError
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord


class TestDLQManager:
    """Unit tests for DLQManager."""

    @pytest.fixture
    def mock_store(self) -> MagicMock:
        store = MagicMock()
        store.load_items.return_value = {}
        store.save_items.return_value = None
        store.append_dead_letter.return_value = None
        return store

    @pytest.fixture
    def item(self) -> TaskWorkItemRecord:
        return TaskWorkItemRecord(
            task_id="task-dlq",
            trace_id="trace-1",
            run_id="run-1",
            workspace="/tmp/ws",
            stage="pending_exec",
            status="in_execution",
            priority="high",
            payload={},
            metadata={},
            version=3,
            attempts=2,
            max_attempts=3,
            lease_token="tok",
            lease_expires_at=9999.0,
            claimed_by="dir-1",
            claimed_role="director",
            last_error={},
        )

    @pytest.fixture
    def dlq(self, mock_store: MagicMock) -> DLQManager:
        return DLQManager(mock_store)

    # ---- move_to_dead_letter -----------------------------------------------

    def test_move_to_dead_letter_updates_item(
        self, dlq: DLQManager, item: TaskWorkItemRecord, mock_store: MagicMock
    ) -> None:
        dlq.move_to_dead_letter(
            item=item,
            reason="exec_failed",
            error_code="ERR_EXEC",
            metadata={"foo": "bar"},
        )
        assert item.stage == "dead_letter"
        assert item.status == "dead_letter"
        assert item.lease_token == ""
        assert item.lease_expires_at == 0.0
        assert item.claimed_by == ""
        assert item.claimed_role == ""

    def test_move_to_dead_letter_calls_append(
        self, dlq: DLQManager, item: TaskWorkItemRecord, mock_store: MagicMock
    ) -> None:
        dlq.move_to_dead_letter(
            item=item,
            reason="exec_failed",
            error_code="ERR_EXEC",
            metadata={"foo": "bar"},
        )
        mock_store.append_dead_letter.assert_called_once()
        entry = mock_store.append_dead_letter.call_args[0][0]
        assert entry["task_id"] == "task-dlq"
        assert entry["error_code"] == "ERR_EXEC"
        assert entry["attempts"] == 2

    # ---- load_dlq_items ----------------------------------------------------

    def test_load_dlq_items_delegates(self, dlq: DLQManager, mock_store: MagicMock) -> None:
        mock_store.load_dead_letters.return_value = [
            {"task_id": "a", "reason": "x"},
            {"task_id": "b", "reason": "y"},
        ]
        result = dlq.load_dlq_items(workspace="/tmp/ws", limit=50)
        mock_store.load_dead_letters.assert_called_once_with(limit=50)
        assert len(result) == 2

    # ---- replay_item -------------------------------------------------------

    def test_replay_item_resets_attempts(
        self, dlq: DLQManager, item: TaskWorkItemRecord, mock_store: MagicMock
    ) -> None:
        item.status = "dead_letter"
        item.attempts = 3
        mock_store.load_items.return_value = {"task-dlq": item}

        replayed = dlq.replay_item(
            workspace="/tmp/ws",
            task_id="task-dlq",
            target_stage="pending_exec",
            reason="retry_after_fix",
        )
        assert replayed.attempts == 0
        assert replayed.stage == "pending_exec"
        assert replayed.status == "pending_exec"

    def test_replay_item_raises_if_not_dead_letter(
        self, dlq: DLQManager, item: TaskWorkItemRecord, mock_store: MagicMock
    ) -> None:
        item.status = "pending_exec"
        mock_store.load_items.return_value = {"task-dlq": item}

        with pytest.raises(TaskMarketError) as exc_info:
            dlq.replay_item(
                workspace="/tmp/ws",
                task_id="task-dlq",
                target_stage="pending_exec",
                reason="",
            )
        assert "not_in_dead_letter" in exc_info.value.code

    def test_replay_item_raises_if_not_found(self, dlq: DLQManager, mock_store: MagicMock) -> None:
        mock_store.load_items.return_value = {}

        with pytest.raises(TaskNotFoundError):
            dlq.replay_item(
                workspace="/tmp/ws",
                task_id="nonexistent",
                target_stage="pending_exec",
                reason="",
            )

    # ---- get_dlq_stats ----------------------------------------------------

    def test_get_dlq_stats_aggregates(self, dlq: DLQManager, mock_store: MagicMock) -> None:
        mock_store.load_dead_letters.return_value = [
            {"task_id": "a", "error_code": "ERR_A"},
            {"task_id": "b", "error_code": "ERR_B"},
            {"task_id": "c", "error_code": "ERR_A"},
        ]
        stats = dlq.get_dlq_stats("/tmp/ws")
        assert stats["total"] == 3
        assert stats["by_error_code"]["ERR_A"] == 2
        assert stats["by_error_code"]["ERR_B"] == 1
