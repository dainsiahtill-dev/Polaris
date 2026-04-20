"""Tests for webhook callback notification via outbox pattern."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.internal.store import get_store
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
)


def _publish_task(service: TaskMarketService, workspace: str, task_id: str = "task-1") -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload={"title": "webhook test"},
        )
    )


def test_webhook_outbox_on_review_request(tmp_path) -> None:
    """When callback_url is set, request_human_review should create an outbox record."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_task(service, workspace)

    service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            trace_id="trace-1",
            reason="webhook test",
            requested_by="director",
            callback_url="https://example.com/hooks/review",
        )
    )

    store = get_store(workspace)
    outbox = store.load_outbox_messages(workspace, statuses=("pending", "failed"), limit=100)

    webhook_messages = [m for m in outbox if m.get("event_type") == "task_market.human_review_callback"]
    assert len(webhook_messages) >= 1
    payload = webhook_messages[0].get("payload", {})
    assert payload["callback_url"] == "https://example.com/hooks/review"
    assert payload["action"] == "requested"
    assert payload["task_id"] == "task-1"


def test_webhook_outbox_on_review_resolve(tmp_path) -> None:
    """When callback_url is set, resolve_human_review should create an outbox record."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_task(service, workspace)

    service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            trace_id="trace-1",
            reason="webhook test",
            requested_by="director",
        )
    )

    service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            resolution="force_resolve",
            resolved_by="director:bot-1",
            callback_url="https://example.com/hooks/resolve",
        )
    )

    store = get_store(workspace)
    outbox = store.load_outbox_messages(workspace, statuses=("pending", "failed"), limit=100)

    webhook_messages = [m for m in outbox if m.get("event_type") == "task_market.human_review_callback"]
    resolve_webhooks = [m for m in webhook_messages if m.get("payload", {}).get("action") == "resolved"]
    assert len(resolve_webhooks) >= 1
    assert resolve_webhooks[0]["payload"]["callback_url"] == "https://example.com/hooks/resolve"


def test_no_webhook_when_callback_url_empty(tmp_path) -> None:
    """When callback_url is empty, no webhook outbox record should be created."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _publish_task(service, workspace)

    service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            trace_id="trace-1",
            reason="no webhook",
            requested_by="director",
        )
    )

    store = get_store(workspace)
    outbox = store.load_outbox_messages(workspace, statuses=("pending", "failed"), limit=100)

    webhook_messages = [m for m in outbox if m.get("event_type") == "task_market.human_review_callback"]
    assert len(webhook_messages) == 0
