"""Tests for task market projection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.runtime.projection.task_market_projection import TaskMarketProjection
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord
from polaris.cells.runtime.task_market.public.projection_api import (
    get_dashboard,
    get_worker_load,
    list_active_items,
)

TEST_WORKSPACE = "/test/workspace"


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.load_items.return_value = {}
    store.load_dead_letters.return_value = []
    return store


@pytest.fixture
def projection(mock_store: MagicMock) -> TaskMarketProjection:
    with patch("polaris.cells.runtime.projection.task_market_projection.get_store") as mock_get_store:
        mock_get_store.return_value = mock_store
        proj = TaskMarketProjection(TEST_WORKSPACE)
        proj._store = mock_store
    return proj


@pytest.fixture
def sample_items() -> dict[str, TaskWorkItemRecord]:
    return {
        "task-1": TaskWorkItemRecord(
            task_id="task-1",
            trace_id="trace-a",
            run_id="run-1",
            workspace=TEST_WORKSPACE,
            stage="pending_exec",
            status="in_execution",
            priority="high",
            claimed_by="dir-1",
            claimed_role="director",
            created_at="2026-04-01T10:00:00",
            updated_at="2026-04-01T10:05:00",
        ),
        "task-2": TaskWorkItemRecord(
            task_id="task-2",
            trace_id="trace-a",
            run_id="run-1",
            workspace=TEST_WORKSPACE,
            stage="pending_design",
            status="pending_design",
            priority="medium",
            created_at="2026-04-01T09:00:00",
            updated_at="2026-04-01T09:30:00",
        ),
        "task-3": TaskWorkItemRecord(
            task_id="task-3",
            trace_id="trace-b",
            run_id="run-2",
            workspace=TEST_WORKSPACE,
            stage="pending_exec",
            status="in_execution",
            priority="low",
            claimed_by="dir-2",
            claimed_role="director",
            created_at="2026-04-01T08:00:00",
            updated_at="2026-04-01T08:10:00",
        ),
        "task-4": TaskWorkItemRecord(
            task_id="task-4",
            trace_id="trace-b",
            run_id="run-2",
            workspace=TEST_WORKSPACE,
            stage="dead_letter",
            status="dead_letter",
            priority="critical",
            created_at="2026-04-01T07:00:00",
            updated_at="2026-04-01T07:05:00",
        ),
        "task-5": TaskWorkItemRecord(
            task_id="task-5",
            trace_id="trace-c",
            run_id="run-3",
            workspace=TEST_WORKSPACE,
            stage="pending_qa",
            status="pending_qa",
            priority="high",
            created_at="2026-04-01T11:00:00",
            updated_at="2026-04-01T11:00:00",
        ),
        "task-other-ws": TaskWorkItemRecord(
            task_id="task-other-ws",
            trace_id="trace-d",
            run_id="run-4",
            workspace="/other/ws",
            stage="pending_exec",
            status="pending_exec",
            priority="medium",
            created_at="2026-04-01T06:00:00",
            updated_at="2026-04-01T06:00:00",
        ),
    }


class TestTaskMarketProjection:
    """Unit tests for TaskMarketProjection."""

    # ---- get_queue_depth_by_stage -------------------------------------------

    def test_queue_depth_counts_queue_stages(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_queue_depth_by_stage()

        # task-1 and task-3 have stage=pending_exec (status is in_execution but stage unchanged)
        # task-2 has stage=pending_design
        # task-5 has stage=pending_qa
        # task-4 is dead_letter (terminal, not in QUEUE_STAGES)
        assert result["pending_design"] == 1
        assert result["pending_exec"] == 2  # task-1 and task-3 have stage=pending_exec
        assert result["pending_qa"] == 1

    def test_queue_depth_excludes_other_workspace(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_queue_depth_by_stage()

        # task-1 and task-3 are from TEST_WORKSPACE and counted
        # task-other-ws is from /other/ws and excluded
        assert result["pending_exec"] == 2
        assert result.get("pending_design") == 1

    # ---- get_in_progress_count -----------------------------------------------

    def test_in_progress_counts_active_items(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_in_progress_count()

        assert result["in_execution"] == 2  # task-1 and task-3

    # ---- get_dead_letter_count -----------------------------------------------

    def test_dead_letter_count_returns_length(self, projection: TaskMarketProjection, mock_store: MagicMock) -> None:
        mock_store.load_dead_letters.return_value = [
            {"task_id": "a"},
            {"task_id": "b"},
            {"task_id": "c"},
        ]

        result = projection.get_dead_letter_count()

        assert result == 3

    # ---- get_active_work_items -----------------------------------------------

    def test_active_items_excludes_terminal(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_active_work_items()

        task_ids = [item["task_id"] for item in result]
        assert "task-4" not in task_ids  # dead_letter is terminal

    def test_active_items_filters_by_stage(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_active_work_items(stage="pending_design")

        assert len(result) == 1
        assert result[0]["task_id"] == "task-2"

    def test_active_items_respects_limit(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_active_work_items(limit=2)

        assert len(result) == 2

    def test_active_items_sorted_by_updated_at_desc(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_active_work_items()

        # task-5 has updated_at 2026-04-01T11:00:00 (latest among non-terminal)
        assert result[0]["task_id"] == "task-5"

    # ---- get_worker_load ----------------------------------------------------

    def test_worker_load_counts_claimed_tasks(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_worker_load()

        assert result["dir-1"]["task_count"] == 1
        assert result["dir-2"]["task_count"] == 1
        assert result["dir-1"]["role"] == "director"
        assert result["dir-2"]["role"] == "director"

    # ---- get_trace_timeline -------------------------------------------------

    def test_trace_timeline_returns_matching_items(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_trace_timeline("trace-a")

        assert len(result) == 2
        task_ids = [item["task_id"] for item in result]
        assert "task-1" in task_ids
        assert "task-2" in task_ids

    def test_trace_timeline_sorted_by_created_at(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        result = projection.get_trace_timeline("trace-a")

        # task-2 created at 09:00:00, task-1 at 10:00:00
        assert result[0]["task_id"] == "task-2"
        assert result[1]["task_id"] == "task-1"

    # ---- get_dashboard_summary ----------------------------------------------

    def test_dashboard_summary_contains_all_fields(
        self, projection: TaskMarketProjection, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items
        mock_store.load_dead_letters.return_value = [{"task_id": "task-4"}]

        result = projection.get_dashboard_summary()

        assert "workspace" in result
        assert "queue_depth" in result
        assert "in_progress" in result
        assert "dead_letter_count" in result
        assert "worker_load" in result
        assert "active_items" in result
        assert "total_active" in result


class TestProjectionAPI:
    """Unit tests for projection_api functions."""

    def test_get_dashboard_returns_summary(
        self, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items
        mock_store.load_dead_letters.return_value = []

        with patch("polaris.cells.runtime.projection.task_market_projection.get_store") as mock_get_store:
            mock_get_store.return_value = mock_store
            result = get_dashboard(TEST_WORKSPACE)

        assert "queue_depth" in result
        assert "in_progress" in result

    def test_list_active_items_returns_filtered(
        self, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        with patch("polaris.cells.runtime.projection.task_market_projection.get_store") as mock_get_store:
            mock_get_store.return_value = mock_store
            result = list_active_items(TEST_WORKSPACE, stage="pending_design")

        assert len(result) == 1
        assert result[0]["task_id"] == "task-2"

    def test_get_worker_load_returns_load_dict(
        self, mock_store: MagicMock, sample_items: dict[str, TaskWorkItemRecord]
    ) -> None:
        mock_store.load_items.return_value = sample_items

        with patch("polaris.cells.runtime.projection.task_market_projection.get_store") as mock_get_store:
            mock_get_store.return_value = mock_store
            result = get_worker_load(TEST_WORKSPACE)

        assert "dir-1" in result
        assert result["dir-1"]["task_count"] == 1
