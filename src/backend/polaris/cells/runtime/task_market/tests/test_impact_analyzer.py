"""Tests for read-only change order impact analyzer."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    RegisterPlanRevisionCommandV1,
)


def _publish_and_claim(
    service: TaskMarketService,
    workspace: str,
    task_id: str,
    *,
    plan_id: str = "plan-1",
    plan_revision_id: str = "rev-1",
    stage: str = "pending_design",
    source_role: str = "pm",
) -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id=f"trace-{task_id}",
            run_id="run-1",
            task_id=task_id,
            stage=stage,
            source_role=source_role,
            plan_id=plan_id,
            plan_revision_id=plan_revision_id,
            payload={"title": task_id},
        )
    )


def test_impact_analyzer_pending_items_are_superseded(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    _publish_and_claim(service, workspace, "task-1")

    result = service.analyze_change_order_impact(
        workspace,
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
    )

    assert result["candidates_total"] == 1
    assert result["impact_counts"].get("superseded") == 1
    # Verify no mutations occurred — item should still be pending_design.
    from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1

    status = service.query_status(QueryTaskMarketStatusV1(workspace=workspace))
    assert status.items[0]["status"] == "pending_design"


def test_impact_analyzer_resolved_needs_revalidation(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    _publish_and_claim(service, workspace, "task-1")

    # Claim and resolve.
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=workspace,
            stage="pending_design",
            worker_id="ce-1",
            worker_role="chief_engineer",
            visibility_timeout_seconds=60,
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=workspace,
            task_id="task-1",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="done",
        )
    )

    result = service.analyze_change_order_impact(
        workspace,
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
    )

    assert result["impact_counts"].get("needs_revalidation") == 1


def test_impact_analyzer_in_progress_cancel_requested(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    _publish_and_claim(service, workspace, "task-1")

    # Claim → in_design.
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=workspace,
            stage="pending_design",
            worker_id="ce-1",
            worker_role="chief_engineer",
            visibility_timeout_seconds=60,
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=workspace,
            task_id="task-1",
            lease_token=claim.lease_token,
            next_stage="pending_exec",
            summary="design done",
        )
    )
    # Claim → in_execution.
    service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=workspace,
            stage="pending_exec",
            worker_id="d-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    # Don't ack — item stays in_execution (claimed).

    result = service.analyze_change_order_impact(
        workspace,
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
    )

    # in_design or in_execution should be cancel_requested.
    assert result["impact_counts"].get("cancel_requested") >= 1


def test_impact_analyzer_with_task_filter(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    _publish_and_claim(service, workspace, "task-1")
    _publish_and_claim(service, workspace, "task-2")

    result = service.analyze_change_order_impact(
        workspace,
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
        affected_task_ids=("task-1",),
    )

    assert result["candidates_total"] == 1


def test_impact_analyzer_empty_workspace(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    result = service.analyze_change_order_impact(
        workspace,
        plan_id="plan-1",
        from_revision_id="rev-1",
        to_revision_id="rev-2",
    )

    assert result["candidates_total"] == 0
    assert result["impact_counts"] == {}
