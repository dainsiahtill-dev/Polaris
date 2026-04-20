"""Tests for ``public/dlq_api.py``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord
from polaris.cells.runtime.task_market.public import dlq_api


class TestReplayDlqItem:
    """Unit tests for ``replay_dlq_item``."""

    @pytest.fixture
    def mock_store(self) -> MagicMock:
        store = MagicMock()
        store.load_items.return_value = {}
        store.save_items.return_value = None
        store.append_dead_letter.return_value = None
        store.load_dead_letters.return_value = []
        return store

    @pytest.fixture
    def dlq_item(self) -> TaskWorkItemRecord:
        return TaskWorkItemRecord(
            task_id="task-replay",
            trace_id="trace-1",
            run_id="run-1",
            workspace="/tmp/ws",
            stage="dead_letter",
            status="dead_letter",
            priority="high",
            payload={},
            metadata={},
            version=5,
            attempts=3,
            max_attempts=3,
            lease_token="tok",
            lease_expires_at=9999.0,
            claimed_by="dir-1",
            claimed_role="director",
            last_error={"code": "ERR_EXEC"},
        )

    def test_replay_item_to_pending_design(self, mock_store: MagicMock, dlq_item: TaskWorkItemRecord) -> None:
        dlq_item.status = "dead_letter"
        mock_store.load_items.return_value = {"task-replay": dlq_item}

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            result = dlq_api.replay_dlq_item(
                workspace="/tmp/ws",
                task_id="task-replay",
                target_stage="pending_design",
            )

        assert result["ok"] is True
        assert result["task_id"] == "task-replay"
        assert result["target_stage"] == "pending_design"
        assert result["status"] == "pending_design"
        mock_store.save_items.assert_called_once()

    def test_replay_item_to_pending_exec(self, mock_store: MagicMock, dlq_item: TaskWorkItemRecord) -> None:
        dlq_item.status = "dead_letter"
        mock_store.load_items.return_value = {"task-replay": dlq_item}

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            result = dlq_api.replay_dlq_item(
                workspace="/tmp/ws",
                task_id="task-replay",
                target_stage="pending_exec",
            )

        assert result["ok"] is True
        assert result["task_id"] == "task-replay"
        assert result["target_stage"] == "pending_exec"
        assert result["status"] == "pending_exec"

    def test_replay_item_invalid_target_stage(self, mock_store: MagicMock, dlq_item: TaskWorkItemRecord) -> None:
        dlq_item.status = "dead_letter"
        mock_store.load_items.return_value = {"task-replay": dlq_item}

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            result = dlq_api.replay_dlq_item(
                workspace="/tmp/ws",
                task_id="task-replay",
                target_stage="invalid_stage",
            )

        assert result["ok"] is False
        assert "Invalid target_stage" in result["reason"]

    def test_replay_item_not_in_dlq(self, mock_store: MagicMock, dlq_item: TaskWorkItemRecord) -> None:
        dlq_item.status = "pending_exec"  # not in dead_letter
        mock_store.load_items.return_value = {"task-replay": dlq_item}

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            result = dlq_api.replay_dlq_item(
                workspace="/tmp/ws",
                task_id="task-replay",
                target_stage="pending_exec",
            )

        assert result["ok"] is False
        assert "not dead_letter" in result["reason"]

    def test_replay_item_not_found(self, mock_store: MagicMock) -> None:
        mock_store.load_items.return_value = {}

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            result = dlq_api.replay_dlq_item(
                workspace="/tmp/ws",
                task_id="nonexistent",
                target_stage="pending_exec",
            )

        assert result["ok"] is False
        assert "not found" in result["reason"].lower()


class TestGetDlqStats:
    """Unit tests for ``get_dlq_stats``."""

    def test_get_dlq_stats_returns_counts(self) -> None:
        mock_store = MagicMock()
        mock_store.load_dead_letters.return_value = [
            {"task_id": "a", "error_code": "ERR_A"},
            {"task_id": "b", "error_code": "ERR_B"},
            {"task_id": "c", "error_code": "ERR_A"},
        ]

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            stats = dlq_api.get_dlq_stats("/tmp/ws")

        assert stats["total"] == 3
        assert stats["by_error_code"]["ERR_A"] == 2
        assert stats["by_error_code"]["ERR_B"] == 1

    def test_get_dlq_stats_empty(self) -> None:
        mock_store = MagicMock()
        mock_store.load_dead_letters.return_value = []

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            stats = dlq_api.get_dlq_stats("/tmp/ws")

        assert stats["total"] == 0
        assert stats["by_error_code"] == {}


class TestListDlqItems:
    """Unit tests for ``list_dlq_items``."""

    def test_list_dlq_items_returns_items(self) -> None:
        mock_store = MagicMock()
        mock_store.load_dead_letters.return_value = [
            {"task_id": "a", "error_code": "ERR_A"},
            {"task_id": "b", "error_code": "ERR_B"},
        ]

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            items = dlq_api.list_dlq_items("/tmp/ws", limit=50)

        assert len(items) == 2
        mock_store.load_dead_letters.assert_called_once_with(limit=50)

    def test_list_dlq_items_default_limit(self) -> None:
        mock_store = MagicMock()
        mock_store.load_dead_letters.return_value = []

        with patch.object(dlq_api, "get_store", return_value=mock_store):
            dlq_api.list_dlq_items("/tmp/ws")

        mock_store.load_dead_letters.assert_called_once_with(limit=200)
