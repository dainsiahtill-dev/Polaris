"""Tests for auto-escalation timeout sweep."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    RequestHumanReviewCommandV1,
)


def _create_review(service: TaskMarketService, workspace: str, task_id: str = "task-1") -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload={"title": "escalation test"},
        )
    )
    service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=workspace,
            task_id=task_id,
            trace_id="trace-1",
            reason="test review",
            requested_by="director",
        )
    )


def test_review_has_escalation_deadline(tmp_path) -> None:
    """A newly created review should have an escalation_deadline set."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    reviews = service.query_pending_human_reviews(
        __import__(
            "polaris.cells.runtime.task_market.public.contracts", fromlist=["QueryPendingHumanReviewsV1"]
        ).QueryPendingHumanReviewsV1(
            workspace=workspace,
        )
    )
    assert len(reviews) == 1
    assert reviews[0].get("escalation_deadline", "") != ""


def test_sweep_does_nothing_when_not_expired(tmp_path) -> None:
    """Sweep should not escalate reviews with future deadlines."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    result = service.sweep_escalation_timeouts(workspace)
    assert result["escalated_count"] == 0


def test_sweep_escalates_when_deadline_passed(tmp_path) -> None:
    """Sweep should auto-escalate reviews with past deadlines."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    # Manually set escalation_deadline to the past.
    from polaris.cells.runtime.task_market.internal.store import get_store

    store = get_store(workspace)
    reviews = store.load_human_review_requests(workspace)
    assert len(reviews) >= 1
    review = dict(reviews[0])
    past_deadline = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    review["escalation_deadline"] = past_deadline
    store.upsert_human_review_request(review)

    result = service.sweep_escalation_timeouts(workspace)
    assert result["escalated_count"] == 1
    assert "task-1" in result["escalated"]

    # Verify the review was escalated.
    reviews_after = store.load_human_review_requests(workspace)
    assert reviews_after[0]["current_role"] == "chief_engineer"
    assert reviews_after[0]["escalation_deadline"] != past_deadline


def test_sweep_stops_at_terminal_role(tmp_path) -> None:
    """When current_role is 'human' (terminal), sweep should not escalate."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    # Escalate all the way to the terminal role.
    for _ in range(4):  # director → ce → pm → architect → human
        service.advance_human_review_escalation(
            workspace=workspace,
            task_id="task-1",
            escalated_by="system",
        )

    # Set deadline to the past.
    from polaris.cells.runtime.task_market.internal.store import get_store

    store = get_store(workspace)
    reviews = store.load_human_review_requests(workspace)
    review = dict(reviews[0])
    review["escalation_deadline"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.upsert_human_review_request(review)

    result = service.sweep_escalation_timeouts(workspace)
    # Terminal role — should end up in terminal list.
    assert result["terminal_count"] >= 1 or result["escalated_count"] == 0


def test_reconciler_calls_sweep(tmp_path) -> None:
    """TaskReconciliationLoop.run_once should include escalation_sweep."""
    from polaris.cells.runtime.task_market.internal.reconciler import TaskReconciliationLoop

    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    loop = TaskReconciliationLoop(service=service, workspace=workspace, interval_seconds=1.0)
    result = loop.run_once()
    assert "escalation_sweep" in result
    assert isinstance(result["escalation_sweep"], dict)
