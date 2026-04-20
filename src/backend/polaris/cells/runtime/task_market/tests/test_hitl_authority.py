"""Tests for HITL authority model — role verification on resolve_review."""

from __future__ import annotations

import pytest
from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
)


def _create_review(service: TaskMarketService, workspace: str, task_id: str = "task-1") -> None:
    """Helper: publish a task and request human review."""
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload={"title": "auth test"},
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


def test_resolve_by_matching_role_succeeds(tmp_path) -> None:
    """Director resolving when current_role=director should succeed."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    result = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            resolution="force_resolve",
            resolved_by="director:bot-1",
            note="authorized",
        )
    )
    assert result.ok is True


def test_resolve_by_human_always_succeeds(tmp_path) -> None:
    """'human' is the terminal role and always has authority."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    result = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            resolution="force_resolve",
            resolved_by="human",
            note="human override",
        )
    )
    assert result.ok is True


def test_resolve_by_mismatched_role_raises(tmp_path) -> None:
    """Chief engineer resolving when current_role=director should fail."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    with pytest.raises(Exception) as exc_info:
        service.resolve_human_review(
            ResolveHumanReviewCommandV1(
                workspace=workspace,
                task_id="task-1",
                resolution="force_resolve",
                resolved_by="chief_engineer:bot-1",
                note="unauthorized",
            )
        )
    assert getattr(exc_info.value, "code", "") == "unauthorized_role"


def test_resolve_by_unknown_identity_succeeds(tmp_path) -> None:
    """Unknown identity (no role prefix) falls through for backward compat."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    result = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            resolution="force_resolve",
            resolved_by="some_user",
            note="unknown user",
        )
    )
    assert result.ok is True


def test_resolve_after_escalation_requires_new_role(tmp_path) -> None:
    """After escalation to chief_engineer, only chief_engineer or human can resolve."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _create_review(service, workspace)

    # Escalate from director → chief_engineer.
    service.advance_human_review_escalation(
        workspace=workspace,
        task_id="task-1",
        escalated_by="system",
    )

    # Director should no longer have authority.
    with pytest.raises(Exception) as exc_info:
        service.resolve_human_review(
            ResolveHumanReviewCommandV1(
                workspace=workspace,
                task_id="task-1",
                resolution="requeue_design",
                resolved_by="director:bot-1",
                note="too late",
            )
        )
    assert getattr(exc_info.value, "code", "") == "unauthorized_role"

    # Chief engineer should succeed.
    result = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=workspace,
            task_id="task-1",
            resolution="requeue_design",
            resolved_by="chief_engineer:bot-2",
            note="authorized after escalation",
        )
    )
    assert result.ok is True
