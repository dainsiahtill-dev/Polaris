"""Tests for drift-driven auto-requeue via reconciliation."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    RegisterPlanRevisionCommandV1,
)


def _publish(
    service: TaskMarketService,
    workspace: str,
    task_id: str,
    *,
    plan_id: str = "plan-1",
    plan_revision_id: str = "rev-1",
    stage: str = "pending_design",
) -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id=f"trace-{task_id}",
            run_id="run-1",
            task_id=task_id,
            stage=stage,
            source_role="pm",
            plan_id=plan_id,
            plan_revision_id=plan_revision_id,
            payload={"title": task_id},
        )
    )


def test_drift_requeue_requeues_lagged_items(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    # Register rev-1 and publish task on rev-1.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-1",
            source_role="pm",
        )
    )
    _publish(service, workspace, "task-1", plan_revision_id="rev-1")

    # Register rev-2 — task-1 is now drifted.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-2",
            parent_revision_id="rev-1",
            source_role="pm",
        )
    )

    result = service.requeue_drifted_items(workspace)
    assert result["requeued_count"] == 1
    assert "task-1" in result["requeued_ids"]


def test_drift_requeue_updates_revision_and_stage(tmp_path) -> None:
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
    _publish(service, workspace, "task-1", plan_revision_id="rev-1")

    # Advance task to in_design via claim + ack.
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

    # Register rev-2 and requeue.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-2",
            parent_revision_id="rev-1",
            source_role="pm",
        )
    )
    service.requeue_drifted_items(workspace)

    # Verify task is back at pending_design with latest revision.
    from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1

    status = service.query_status(QueryTaskMarketStatusV1(workspace=workspace))
    item = status.items[0]
    assert item["status"] == "pending_design"
    assert item["stage"] == "pending_design"
    assert item["plan_revision_id"] == "rev-2"


def test_drift_requeue_skips_terminal_and_dead_letter(tmp_path) -> None:
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
    _publish(service, workspace, "task-resolved", plan_revision_id="rev-1")
    _publish(service, workspace, "task-dead", plan_revision_id="rev-1")

    # Resolve task-resolved.
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=workspace,
            stage="pending_design",
            task_id="task-resolved",
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

    # Move task-dead to dead_letter.
    from polaris.cells.runtime.task_market.public.contracts import MoveTaskToDeadLetterCommandV1

    service.move_task_to_dead_letter(
        MoveTaskToDeadLetterCommandV1(
            workspace=workspace,
            task_id="task-dead",
            reason="test",
            error_code="test",
        )
    )

    # Register rev-2.
    service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=workspace,
            plan_id="plan-1",
            plan_revision_id="rev-2",
            parent_revision_id="rev-1",
            source_role="pm",
        )
    )

    result = service.requeue_drifted_items(workspace)
    assert result["requeued_count"] == 0


def test_drift_requeue_no_drift_returns_zero(tmp_path) -> None:
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
    _publish(service, workspace, "task-1", plan_revision_id="rev-1")

    # No new revision — no drift.
    result = service.requeue_drifted_items(workspace)
    assert result["requeued_count"] == 0


def test_drift_requeue_empty_workspace(tmp_path) -> None:
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    result = service.requeue_drifted_items(workspace)
    assert result["requeued_count"] == 0
    assert result["workspace"] == workspace


def test_reconciler_run_once_calls_drift_requeue(tmp_path) -> None:
    """Verify reconciler.run_once calls requeue_drifted_items and returns result."""
    from polaris.cells.runtime.task_market.internal.reconciler import TaskReconciliationLoop

    class _TrackingService:
        def __init__(self) -> None:
            self.drift_calls = 0

        def reconcile_parent_statuses(self, workspace: str, *, limit: int = 5000) -> dict[str, object]:
            return {"workspace": workspace, "updated": 0}

        def sweep_escalation_timeouts(self, workspace: str) -> dict[str, object]:
            return {"escalated_count": 0}

        def requeue_drifted_items(self, workspace: str) -> dict[str, object]:
            self.drift_calls += 1
            return {"requeued_count": 0}

    svc = _TrackingService()
    loop = TaskReconciliationLoop(service=svc, workspace=str(tmp_path / "ws"), interval_seconds=5.0)
    result = loop.run_once()

    assert svc.drift_calls == 1
    assert "drift_requeue" in result
    assert result["drift_requeue"]["requeued_count"] == 0
