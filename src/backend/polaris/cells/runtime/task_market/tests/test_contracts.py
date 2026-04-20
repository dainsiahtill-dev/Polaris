from __future__ import annotations

import pytest
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryPendingHumanReviewsV1,
    QueryTaskMarketStatusV1,
    RegisterPlanRevisionCommandV1,
    RenewTaskLeaseCommandV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
    SubmitChangeOrderCommandV1,
)


def test_publish_contract_validates_required_fields() -> None:
    command = PublishTaskWorkItemCommandV1(
        workspace="/tmp/ws",
        trace_id="trace-1",
        run_id="run-1",
        task_id="task-1",
        stage="pending_exec",
        source_role="pm",
        payload={"title": "Implement endpoint"},
    )
    assert command.stage == "pending_exec"
    assert command.priority == "medium"


def test_publish_contract_normalizes_revision_fields() -> None:
    command = PublishTaskWorkItemCommandV1(
        workspace="/tmp/ws",
        trace_id="trace-1",
        run_id="run-1",
        task_id="task-1",
        stage="pending_design",
        source_role="pm",
        payload={"title": "Plan task"},
        plan_id=" plan-alpha ",
        plan_revision_id=" rev-002 ",
        parent_task_id=" epic-1 ",
        is_leaf=False,
        depends_on=(" dep-1 ", "", "dep-2", "dep-1"),
        requirement_digest=" req-digest ",
        constraint_digest=" constraint-digest ",
        summary_ref=" summary://task-1 ",
        change_policy="",
        compensation_group_id=" comp-1 ",
    )
    assert command.plan_id == "plan-alpha"
    assert command.plan_revision_id == "rev-002"
    assert command.parent_task_id == "epic-1"
    assert command.is_leaf is False
    assert command.depends_on == ("dep-1", "dep-2")
    assert command.requirement_digest == "req-digest"
    assert command.constraint_digest == "constraint-digest"
    assert command.summary_ref == "summary://task-1"
    assert command.change_policy == "strict"
    assert command.compensation_group_id == "comp-1"


def test_publish_contract_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError):
        PublishTaskWorkItemCommandV1(
            workspace="/tmp/ws",
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-1",
            stage="unknown",
            source_role="pm",
            payload={"x": 1},
        )


def test_claim_contract_requires_positive_visibility_timeout() -> None:
    with pytest.raises(ValueError):
        ClaimTaskWorkItemCommandV1(
            workspace="/tmp/ws",
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=0,
        )


def test_ack_contract_defaults_terminal_to_resolved() -> None:
    command = AcknowledgeTaskStageCommandV1(
        workspace="/tmp/ws",
        task_id="task-1",
        lease_token="lease-token",
    )
    assert command.terminal_status == "resolved"


def test_query_contract_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        QueryTaskMarketStatusV1(workspace="/tmp/ws", limit=0)


def test_renew_contract_requires_non_empty_lease_token() -> None:
    with pytest.raises(ValueError):
        RenewTaskLeaseCommandV1(
            workspace="/tmp/ws",
            task_id="task-1",
            lease_token="",
        )


def test_register_plan_revision_requires_non_empty_plan_id() -> None:
    with pytest.raises(ValueError):
        RegisterPlanRevisionCommandV1(
            workspace="/tmp/ws",
            plan_id="",
            plan_revision_id="rev-1",
            source_role="pm",
        )


def test_submit_change_order_normalizes_type_and_ids() -> None:
    command = SubmitChangeOrderCommandV1(
        workspace="/tmp/ws",
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
        source_role="pm",
        change_type="DOC_PATCH",
        affected_task_ids=(" task-1 ", "", "task-2", "task-1"),
    )
    assert command.change_type == "doc_patch"
    assert command.affected_task_ids == ("task-1", "task-2")


def test_submit_change_order_rejects_same_revision() -> None:
    with pytest.raises(ValueError):
        SubmitChangeOrderCommandV1(
            workspace="/tmp/ws",
            plan_id="plan-1",
            from_revision_id="rev-1",
            to_revision_id="rev-1",
            source_role="pm",
            change_type="scope_add",
        )


def test_request_human_review_requires_reason() -> None:
    with pytest.raises(ValueError):
        RequestHumanReviewCommandV1(
            workspace="/tmp/ws",
            task_id="task-1",
            reason="",
        )


def test_resolve_human_review_normalizes_resolution() -> None:
    command = ResolveHumanReviewCommandV1(
        workspace="/tmp/ws",
        task_id="task-1",
        resolution="REQUEUE_EXEC",
    )
    assert command.resolution == "requeue_exec"


def test_query_pending_human_reviews_requires_positive_limit() -> None:
    with pytest.raises(ValueError):
        QueryPendingHumanReviewsV1(workspace="/tmp/ws", limit=0)
