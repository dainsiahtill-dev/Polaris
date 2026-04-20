"""Tests for revision drift detection."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    RegisterPlanRevisionCommandV1,
)


def _setup_workspace(tmp_path, service: TaskMarketService, workspace: str) -> None:
    """Helper: publish tasks and register revisions for drift testing."""
    # Register two revisions for plan-1.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-2",
            parent_revision_id="rev-1",
            source_role="pm",
        )
    )

    # Task on old revision.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-old",
            stage="pending_design",
            source_role="pm",
            plan_id="plan-1",
            plan_revision_id="rev-1",
            payload={"title": "old task"},
        )
    )

    # Task on latest revision.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-2",
            run_id="run-1",
            task_id="task-latest",
            stage="pending_design",
            source_role="pm",
            plan_id="plan-1",
            plan_revision_id="rev-2",
            payload={"title": "latest task"},
        )
    )


def test_detect_revision_drift_finds_lagged_items(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")
    _setup_workspace(tmp_path, service, workspace)

    result = service.detect_revision_drift(workspace)

    assert result["drifted_count"] == 1
    assert result["drifted_items"][0]["task_id"] == "task-old"
    assert result["drifted_items"][0]["current_revision"] == "rev-1"
    assert result["drifted_items"][0]["latest_revision"] == "rev-2"
    assert result["latest_revision_by_plan"]["plan-1"] == "rev-2"


def test_detect_revision_drift_no_drift(tmp_path) -> None:
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
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-1",
            stage="pending_design",
            source_role="pm",
            plan_id="plan-1",
            plan_revision_id="rev-1",
            payload={"title": "test"},
        )
    )

    result = service.detect_revision_drift(workspace)
    assert result["drifted_count"] == 0


def test_detect_revision_drift_filters_by_plan(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    # Two plans with different revisions.
    for plan_id, rev_id in [("plan-a", "rev-a1"), ("plan-b", "rev-b1")]:
        service.register_plan_revision(
            RegisterPlanRevisionCommandV1(
                workspace=workspace,
                plan_id=plan_id,
                plan_revision_id=rev_id,
                source_role="pm",
            )
        )
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=workspace,
                trace_id=f"trace-{plan_id}",
                run_id="run-1",
                task_id=f"task-{plan_id}",
                stage="pending_design",
                source_role="pm",
                plan_id=plan_id,
                plan_revision_id=rev_id,
                payload={"title": f"task for {plan_id}"},
            )
        )

    # Upgrade plan-a to rev-a2; task-a is now drifted.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-a",
            plan_revision_id="rev-a2",
            parent_revision_id="rev-a1",
            source_role="pm",
        )
    )

    # Filter to plan-a only.
    result = service.detect_revision_drift(workspace, plan_id="plan-a")
    assert result["drifted_count"] == 1
    assert result["drifted_items"][0]["task_id"] == "task-plan-a"

    # Filter to plan-b — no drift.
    result_b = service.detect_revision_drift(workspace, plan_id="plan-b")
    assert result_b["drifted_count"] == 0


def test_detect_revision_drift_skips_terminal(tmp_path) -> None:
    """Terminal tasks should not be flagged as drifted."""
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
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-2",
            parent_revision_id="rev-1",
            source_role="pm",
        )
    )

    # Publish as resolved.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-resolved",
            stage="pending_design",
            source_role="pm",
            plan_id="plan-1",
            plan_revision_id="rev-1",
            payload={"initial_status": "resolved", "title": "test"},
        )
    )
    # Manually set to resolved.
    from polaris.cells.runtime.task_market.public.contracts import (
        AcknowledgeTaskStageCommandV1,
        ClaimTaskWorkItemCommandV1,
    )

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
            task_id="task-resolved",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="done",
        )
    )

    result = service.detect_revision_drift(workspace)
    assert result["drifted_count"] == 0


def test_detect_revision_drift_empty_workspace(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    result = service.detect_revision_drift(workspace)
    assert result["drifted_count"] == 0
    assert result["drifted_items"] == ()
