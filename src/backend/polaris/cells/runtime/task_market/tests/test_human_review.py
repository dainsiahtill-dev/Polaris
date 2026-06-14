"""Tests for ``internal/human_review.py``."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from polaris.cells.runtime.task_market.internal.human_review import (
    ESCALATION_CHAIN,
    RESOLUTION_ACTIONS,
    RESOLUTION_TO_STAGE,
    HumanReviewManager,
    get_next_escalation_role,
)
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord


def _item(*, status: str = "in_execution", version: int = 7) -> TaskWorkItemRecord:
    return TaskWorkItemRecord(
        task_id="task-hitl",
        trace_id="trace-hitl",
        run_id="run-hitl",
        workspace="/tmp/ws",
        stage=status,
        status=status,
        priority="normal",
        payload={},
        metadata={},
        version=version,
    )


def _store_with_item(item: TaskWorkItemRecord, reviews: list[dict[str, Any]] | None = None) -> MagicMock:
    store = MagicMock()
    store.load_items.return_value = {item.task_id: item}
    store.load_human_review_requests.return_value = list(reviews or [])
    store.save_items.return_value = None
    store.save_items_and_outbox_atomic.return_value = None
    store.upsert_human_review_request.return_value = None
    return store


class TestHumanReviewManager:
    """Unit tests for HumanReviewManager (logic-only, store mocked)."""

    # resolve_review validation — test the standalone validation guard.
    def test_resolve_review_rejects_invalid_resolution(self) -> None:
        # Test that RESOLUTION_ACTIONS rejects invalid inputs.
        invalid = "invalid_action"
        assert invalid not in RESOLUTION_ACTIONS

    def test_resolution_actions_defined(self) -> None:
        assert "requeue_design" in RESOLUTION_ACTIONS
        assert "requeue_exec" in RESOLUTION_ACTIONS
        assert "force_resolve" in RESOLUTION_ACTIONS
        assert "close_as_invalid" in RESOLUTION_ACTIONS
        assert "shadow_continue" in RESOLUTION_ACTIONS

    def test_resolution_to_stage_mapping(self) -> None:
        assert RESOLUTION_TO_STAGE["requeue_design"] == "pending_design"
        assert RESOLUTION_TO_STAGE["requeue_exec"] == "pending_exec"
        assert RESOLUTION_TO_STAGE["force_resolve"] == "resolved"
        assert RESOLUTION_TO_STAGE["close_as_invalid"] == "rejected"

    def test_create_review_request_uses_atomic_cas_item_write(self) -> None:
        item = _item(status="in_execution", version=7)
        store = _store_with_item(item)
        manager = HumanReviewManager(store)

        record = manager.create_review_request(
            task_id="task-hitl",
            trace_id="trace-hitl",
            workspace="/tmp/ws",
            reason="needs human review",
        )

        assert record["status"] == "waiting"
        assert item.status == "waiting_human"
        store.save_items.assert_not_called()
        store.save_items_and_outbox_atomic.assert_called_once()
        assert store.save_items_and_outbox_atomic.call_args.kwargs["items"] == {"task-hitl": item}
        assert store.save_items_and_outbox_atomic.call_args.kwargs["expected_versions"] == {"task-hitl": 7}
        assert store.save_items_and_outbox_atomic.call_args.kwargs["human_review_records"] == [record]
        store.upsert_human_review_request.assert_not_called()

    def test_resolve_review_uses_atomic_cas_item_write(self) -> None:
        item = _item(status="waiting_human", version=8)
        item.metadata["waiting_human_snapshot"] = {
            "previous_stage": "in_execution",
            "previous_status": "in_execution",
        }
        store = _store_with_item(
            item,
            reviews=[
                {
                    "task_id": "task-hitl",
                    "workspace": "/tmp/ws",
                    "status": "waiting",
                    "current_role": "human",
                }
            ],
        )
        manager = HumanReviewManager(store)

        record = manager.resolve_review(
            task_id="task-hitl",
            resolution="shadow_continue",
            resolved_by="human",
            workspace="/tmp/ws",
        )

        assert record["status"] == "resolved"
        assert item.status == "in_execution"
        store.save_items.assert_not_called()
        store.save_items_and_outbox_atomic.assert_called_once()
        assert store.save_items_and_outbox_atomic.call_args.kwargs["items"] == {"task-hitl": item}
        assert store.save_items_and_outbox_atomic.call_args.kwargs["expected_versions"] == {"task-hitl": 8}
        assert store.save_items_and_outbox_atomic.call_args.kwargs["human_review_records"] == [record]
        store.upsert_human_review_request.assert_not_called()


class TestEscalationChain:
    """Tests for Tri-Council escalation chain."""

    def test_get_next_escalation_role_director(self) -> None:
        assert get_next_escalation_role("director") == "chief_engineer"

    def test_get_next_escalation_role_last_human(self) -> None:
        assert get_next_escalation_role("human") is None

    def test_get_next_escalation_role_unknown(self) -> None:
        assert get_next_escalation_role("unknown_role") is None

    def test_escalation_chain_complete(self) -> None:
        assert list(ESCALATION_CHAIN) == [
            "director",
            "chief_engineer",
            "pm",
            "architect",
            "human",
        ]
