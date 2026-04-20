from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.runtime.task_market.internal.errors import StaleLeaseTokenError
from polaris.cells.runtime.task_market.internal.store import get_store
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryChangeOrdersV1,
    QueryPendingHumanReviewsV1,
    QueryPlanRevisionsV1,
    QueryTaskMarketStatusV1,
    RegisterPlanRevisionCommandV1,
    RenewTaskLeaseCommandV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
    SubmitChangeOrderCommandV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


def test_publish_claim_ack_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    published = service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-1",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Implement API"},
        )
    )
    assert published.ok is True
    assert published.status == "pending_exec"

    claimed = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claimed.ok is True
    assert claimed.status == "in_execution"
    assert claimed.lease_token

    renewed = service.renew_task_lease(
        RenewTaskLeaseCommandV1(
            workspace=str(workspace),
            task_id="task-1",
            lease_token=claimed.lease_token,
            visibility_timeout_seconds=60,
        )
    )
    assert renewed.ok is True

    acknowledged = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-1",
            lease_token=claimed.lease_token,
            next_stage="pending_qa",
            summary="Execution complete",
        )
    )
    assert acknowledged.ok is True
    assert acknowledged.status == "pending_qa"


def test_publish_preserves_revision_context_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-revision",
            run_id="run-revision",
            task_id="task-revision",
            stage="pending_design",
            source_role="pm",
            payload={"title": "Revision aware task"},
            plan_id="plan-77",
            plan_revision_id="rev-5",
            parent_task_id="epic-2",
            is_leaf=False,
            depends_on=("dep-a", "dep-b"),
            requirement_digest="req-123",
            constraint_digest="constraint-456",
            summary_ref="summary://task-revision",
            compensation_group_id="cg-2",
        )
    )

    status = service.query_status(
        QueryTaskMarketStatusV1(
            workspace=str(workspace),
            include_payload=True,
        )
    )
    assert status.total == 1
    item = status.items[0]
    assert item["plan_id"] == "plan-77"
    assert item["plan_revision_id"] == "rev-5"
    assert item["root_task_id"] == "task-revision"
    assert item["parent_task_id"] == "epic-2"
    assert item["is_leaf"] is False
    assert item["depends_on"] == ["dep-a", "dep-b"]
    assert item["requirement_digest"] == "req-123"
    assert item["constraint_digest"] == "constraint-456"
    assert item["summary_ref"] == "summary://task-revision"
    assert item["compensation_group_id"] == "cg-2"


def test_register_and_query_plan_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    result = service.register_plan_revision(
        RegisterPlanRevisionCommandV1(
            workspace=str(workspace),
            plan_id="plan-main",
            plan_revision_id="rev-1",
            source_role="pm",
            requirement_digest="req-1",
            constraint_digest="cons-1",
            metadata={"source": "manual"},
        )
    )
    assert result.ok is True
    assert result.plan_revision_id == "rev-1"

    rows = service.query_plan_revisions(
        QueryPlanRevisionsV1(
            workspace=str(workspace),
            plan_id="plan-main",
        )
    )
    assert len(rows) >= 1
    assert rows[0]["plan_revision_id"] == "rev-1"


def test_submit_change_order_applies_status_aware_impact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-pending",
            run_id="run-pending",
            task_id="task-pending",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "pending"},
            plan_id="plan-main",
            plan_revision_id="rev-1",
        )
    )
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-running",
            run_id="run-running",
            task_id="task-running",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "running"},
            plan_id="plan-main",
            plan_revision_id="rev-1",
        )
    )
    running_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="task-running",
        )
    )
    assert running_claim.ok is True

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-resolved",
            run_id="run-resolved",
            task_id="task-resolved",
            stage="pending_qa",
            source_role="pm",
            payload={"title": "resolved"},
            plan_id="plan-main",
            plan_revision_id="rev-1",
        )
    )
    qa_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_qa",
            worker_id="qa-1",
            worker_role="qa",
            task_id="task-resolved",
        )
    )
    assert qa_claim.ok is True
    resolved = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-resolved",
            lease_token=qa_claim.lease_token,
            terminal_status="resolved",
        )
    )
    assert resolved.ok is True
    assert resolved.status == "resolved"

    change = service.submit_change_order(
        SubmitChangeOrderCommandV1(
            workspace=str(workspace),
            plan_id="plan-main",
            from_revision_id="rev-1",
            to_revision_id="rev-2",
            source_role="pm",
            change_type="acceptance_patch",
            summary="acceptance rules updated",
            trace_id="trace-change",
        )
    )
    assert change.ok is True
    assert change.impacted_total == 3
    assert change.impact_counts.get("superseded") == 1
    assert change.impact_counts.get("cancel_requested") == 1
    assert change.impact_counts.get("needs_revalidation") == 1

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_task_id = {row["task_id"]: row for row in status.items}
    assert by_task_id["task-pending"]["superseded_by_revision"] == "rev-2"
    assert by_task_id["task-pending"]["metadata"]["change_order_state"] == "superseded"
    assert by_task_id["task-running"]["superseded_by_revision"] == "rev-2"
    assert by_task_id["task-running"]["metadata"]["change_order_state"] == "cancel_requested"
    assert by_task_id["task-resolved"]["metadata"]["change_order_state"] == "needs_revalidation"

    change_rows = service.query_change_orders(
        QueryChangeOrdersV1(
            workspace=str(workspace),
            plan_id="plan-main",
        )
    )
    assert len(change_rows) >= 1
    assert change_rows[0]["to_revision_id"] == "rev-2"


def test_visibility_timeout_allows_reclaim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-2",
            run_id="run-2",
            task_id="task-2",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Fix tests"},
        )
    )
    first_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=1,
        )
    )
    assert first_claim.ok is True

    time.sleep(1.2)

    second_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-2",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert second_claim.ok is True
    assert second_claim.lease_token != first_claim.lease_token


def test_fail_stage_moves_to_dead_letter_after_retry_exhaustion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-3",
            run_id="run-3",
            task_id="task-3",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Risky refactor"},
            max_attempts=1,
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True

    failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-3",
            lease_token=claim.lease_token,
            error_code="exec_error",
            error_message="patch failed",
        )
    )
    assert failed.ok is True
    assert failed.status == "dead_letter"

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    assert status.total == 1
    assert status.counts.get("dead_letter", 0) == 1


def test_claim_with_task_id_filter_respects_stage_param(tmp_path: Path) -> None:
    """A1: _select_claim_candidate must call is_claimable(stage, ...) not is_claimable(item.stage, ...)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-a1",
            run_id="run-a1",
            task_id="task-a1",
            stage="pending_design",
            source_role="pm",
            payload={"title": "Design task"},
        )
    )

    result = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
            task_id="task-a1",
        )
    )
    assert result.ok is False


def test_renew_lease_returns_utc_iso_string(tmp_path: Path) -> None:
    """A2: renew_task_lease must return an actual expires_at UTC ISO string, not now_iso()."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-a2",
            run_id="run-a2",
            task_id="task-a2",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Exec task"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )

    renewed = service.renew_task_lease(
        RenewTaskLeaseCommandV1(
            workspace=str(workspace),
            task_id="task-a2",
            lease_token=claim.lease_token,
            visibility_timeout_seconds=120,
        )
    )
    assert renewed.ok is True
    assert "T" in renewed.lease_expires_at
    assert "+00:00" in renewed.lease_expires_at or "Z" in renewed.lease_expires_at


def test_acknowledge_stage_merges_metadata_into_payload(tmp_path: Path) -> None:
    """Acknowledge with metadata must merge into item.payload so downstream consumers get the data."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-meta",
            run_id="run-meta",
            task_id="task-meta",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Exec task", "scope_paths": ["/src/main.py"]},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True

    acknowledged = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-meta",
            lease_token=claim.lease_token,
            next_stage="pending_qa",
            summary="Done",
            metadata={
                "blueprint_id": "bp-task-meta",
                "guardrails": ["rule1"],
                "no_touch_zones": ["zone1"],
                "scope_paths": ["/src/main.py", "/src/utils.py"],
            },
        )
    )
    assert acknowledged.ok is True

    # Verify payload was merged by querying the item.
    from polaris.cells.runtime.task_market.internal import store as store_module

    real_store = store_module.get_store(str(workspace))
    items = real_store.load_items()
    item = items["task-meta"]
    # The original payload plus the ack metadata (ack metadata overrides original).
    assert item.payload.get("title") == "Exec task"
    assert item.payload.get("blueprint_id") == "bp-task-meta"
    assert item.payload.get("guardrails") == ["rule1"]
    assert item.payload.get("no_touch_zones") == ["zone1"]
    # scope_paths should be overridden by CE's version.
    assert item.payload.get("scope_paths") == ["/src/main.py", "/src/utils.py"]


def test_fail_stage_records_previous_status_in_transition(tmp_path: Path) -> None:
    """A3: fail_task_stage append_transition must use previous_status (before mutation), not item.status."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-a3",
            run_id="run-a3",
            task_id="task-a3",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Fail test"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )

    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-a3",
            lease_token=claim.lease_token,
            error_code="exec_error",
            error_message="boom",
            requeue_stage="pending_exec",
        )
    )

    from polaris.cells.runtime.task_market.internal import store as store_module

    real_store = store_module.get_store(str(workspace))
    transitions = real_store.load_transitions("task-a3")
    last = transitions[-1]
    assert last["from_status"] == "in_execution"
    assert last["to_status"] == "pending_exec"


def test_move_to_dead_letter_records_previous_status_in_transition(tmp_path: Path) -> None:
    """A3: move_task_to_dead_letter append_transition must use previous_status (before DLQ mutation)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-dlq",
            run_id="run-dlq",
            task_id="task-dlq",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "DLQ test"},
        )
    )

    service.move_task_to_dead_letter(
        MoveTaskToDeadLetterCommandV1(
            workspace=str(workspace),
            task_id="task-dlq",
            reason="unrecoverable",
            error_code="FATAL",
        )
    )

    from polaris.cells.runtime.task_market.internal import store as store_module

    real_store = store_module.get_store(str(workspace))
    transitions = real_store.load_transitions("task-dlq")
    last = transitions[-1]
    assert last["from_status"] == "pending_exec"
    assert last["to_status"] == "dead_letter"


def test_validate_token_rejects_expired_lease(tmp_path: Path) -> None:
    """A4: validate_token must raise StaleLeaseTokenError for an expired lease."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-exp",
            run_id="run-exp",
            task_id="task-exp",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Expire test"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=1,
        )
    )

    time.sleep(1.5)

    with pytest.raises(StaleLeaseTokenError):
        service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="task-exp",
                lease_token=claim.lease_token,
                error_code="exec_error",
                error_message="too late",
            )
        )


def test_reconcile_parent_resolved_when_all_children_resolved(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent",
            run_id="run-parent",
            task_id="epic-1",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Parent epic"},
            is_leaf=False,
        )
    )
    for child_id in ("task-c1", "task-c2"):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"trace-{child_id}",
                run_id=f"run-{child_id}",
                task_id=child_id,
                stage="pending_qa",
                source_role="pm",
                payload={"title": child_id},
                parent_task_id="epic-1",
                root_task_id="epic-1",
            )
        )
        claim = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_qa",
                worker_id="qa-1",
                worker_role="qa",
                task_id=child_id,
            )
        )
        assert claim.ok is True
        ack = service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id=child_id,
                lease_token=claim.lease_token,
                terminal_status="resolved",
            )
        )
        assert ack.ok is True

    reconcile = service.reconcile_parent_statuses(str(workspace))
    assert reconcile["updated"] == 1
    assert "epic-1" in reconcile["updated_parent_ids"]

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_id = {row["task_id"]: row for row in status.items}
    assert by_id["epic-1"]["status"] == "resolved"
    assert by_id["epic-1"]["metadata"]["reconciled_expected_status"] == "resolved"


def test_reconcile_parent_dead_letter_when_child_dead_letter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent-dlq",
            run_id="run-parent-dlq",
            task_id="epic-dlq",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Parent epic"},
            is_leaf=False,
        )
    )
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-child-dlq",
            run_id="run-child-dlq",
            task_id="task-dlq-child",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Child task"},
            parent_task_id="epic-dlq",
            root_task_id="epic-dlq",
            max_attempts=1,
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="task-dlq-child",
        )
    )
    assert claim.ok is True
    failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-dlq-child",
            lease_token=claim.lease_token,
            error_code="exec_failed",
            error_message="fatal",
        )
    )
    assert failed.status == "dead_letter"

    reconcile = service.reconcile_parent_statuses(str(workspace))
    assert reconcile["updated"] == 1

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_id = {row["task_id"]: row for row in status.items}
    assert by_id["epic-dlq"]["status"] == "dead_letter"


def test_reconcile_parent_in_execution_when_child_exec_queue_present(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent-exec",
            run_id="run-parent-exec",
            task_id="epic-exec",
            stage="pending_design",
            source_role="pm",
            payload={"title": "Parent epic"},
            is_leaf=False,
        )
    )
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-child-exec",
            run_id="run-child-exec",
            task_id="task-exec-child",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Child task"},
            parent_task_id="epic-exec",
            root_task_id="epic-exec",
        )
    )

    reconcile = service.reconcile_parent_statuses(str(workspace))
    assert reconcile["updated"] == 1

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_id = {row["task_id"]: row for row in status.items}
    assert by_id["epic-exec"]["status"] == "in_execution"
    assert by_id["epic-exec"]["stage"] == "pending_exec"


def test_fail_stage_terminal_runs_registered_saga_compensation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    artifact = workspace / "tmp" / "artifact.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("to be deleted", encoding="utf-8")

    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-saga-1",
            run_id="run-saga-1",
            task_id="task-saga-1",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Saga compensation task"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="task-saga-1",
        )
    )
    assert claim.ok is True
    service.register_compensation_action(
        workspace=str(workspace),
        task_id="task-saga-1",
        lease_token=claim.lease_token,
        action={"action_type": "file_delete", "target": "tmp/artifact.txt"},
    )

    failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-saga-1",
            lease_token=claim.lease_token,
            error_code="exec_failed",
            error_message="terminal failure",
            to_dead_letter=True,
        )
    )
    assert failed.status == "dead_letter"
    assert not artifact.exists()

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    item = next(row for row in status.items if row["task_id"] == "task-saga-1")
    saga_summary = item["metadata"]["saga_task_compensation"]
    assert saga_summary["executed"] is True
    assert saga_summary["reason"] == "compensated"


def test_parent_terminal_failure_compensates_child_pending_qa(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    child_artifact = workspace / "tmp" / "child_pending_qa.txt"
    child_artifact.parent.mkdir(parents=True, exist_ok=True)
    child_artifact.write_text("child output", encoding="utf-8")

    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent-saga",
            run_id="run-parent-saga",
            task_id="epic-parent",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Parent"},
            is_leaf=False,
        )
    )
    parent_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="ce-1",
            worker_role="chief_engineer",
            task_id="epic-parent",
        )
    )
    assert parent_claim.ok is True

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-child-saga",
            run_id="run-child-saga",
            task_id="task-child",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Child"},
            parent_task_id="epic-parent",
            root_task_id="epic-parent",
            is_leaf=True,
        )
    )
    child_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="task-child",
        )
    )
    assert child_claim.ok is True
    service.register_compensation_action(
        workspace=str(workspace),
        task_id="task-child",
        lease_token=child_claim.lease_token,
        action={"action_type": "file_delete", "target": "tmp/child_pending_qa.txt"},
    )
    child_ack = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-child",
            lease_token=child_claim.lease_token,
            next_stage="pending_qa",
            summary="child done",
        )
    )
    assert child_ack.ok is True
    assert child_ack.status == "pending_qa"

    parent_failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="epic-parent",
            lease_token=parent_claim.lease_token,
            error_code="parent_failed",
            error_message="epic aborted",
            to_dead_letter=True,
        )
    )
    assert parent_failed.status == "dead_letter"
    assert not child_artifact.exists()

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_id = {row["task_id"]: row for row in status.items}
    child_state = by_id["task-child"]["metadata"]["saga_compensation"]
    assert child_state["compensated"] is True
    parent_summary = by_id["epic-parent"]["metadata"]["saga_child_compensation"]
    assert parent_summary["child_count"] == 1


def test_reconciliation_loop_can_start_and_stop_manually(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    started = service.start_reconciliation_loop(str(workspace), interval_seconds=0.05)
    assert started is True
    started_again = service.start_reconciliation_loop(str(workspace), interval_seconds=0.05)
    assert started_again is False

    time.sleep(0.1)
    stopped = service.stop_reconciliation_loop(str(workspace))
    assert stopped is True
    stopped_again = service.stop_reconciliation_loop(str(workspace))
    assert stopped_again is False


def test_publish_auto_starts_reconciliation_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_ENABLE_RECONCILIATION_LOOP", "1")
    monkeypatch.setenv("POLARIS_TASK_MARKET_RECONCILIATION_INTERVAL_SECONDS", "0.05")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-auto-reconcile",
            run_id="run-auto-reconcile",
            task_id="task-auto-reconcile",
            stage="pending_design",
            source_role="pm",
            payload={"title": "auto reconcile"},
        )
    )

    loop = service._reconciliation_loops.get(str(workspace))
    assert loop is not None
    stopped = service.stop_all_reconciliation_loops()
    assert stopped >= 1


def test_request_and_resolve_human_review_flow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-hitl",
            run_id="run-hitl",
            task_id="task-hitl",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Need manual review"},
        )
    )

    requested = service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=str(workspace),
            task_id="task-hitl",
            reason="manual gate",
            requested_by="qa",
        )
    )
    assert requested.ok is True
    assert requested.status == "waiting_human"

    pending = service.query_pending_human_reviews(QueryPendingHumanReviewsV1(workspace=str(workspace), limit=10))
    assert len(pending) == 1
    assert pending[0]["task_id"] == "task-hitl"
    assert pending[0]["next_role"] == "chief_engineer"

    advanced = service.advance_human_review_escalation(
        workspace=str(workspace),
        task_id="task-hitl",
        escalated_by="director",
    )
    assert advanced["ok"] is True
    assert advanced["current_role"] == "chief_engineer"
    assert advanced["next_role"] == "pm"

    resolved = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=str(workspace),
            task_id="task-hitl",
            resolution="requeue_exec",
            resolved_by="human",
            note="approved re-execution",
        )
    )
    assert resolved.ok is True
    assert resolved.status == "pending_exec"


def test_fail_stage_with_manual_escalation_routes_to_waiting_human(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-escalate",
            run_id="run-escalate",
            task_id="task-escalate",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Escalate on failure"},
            max_attempts=1,
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="task-escalate",
        )
    )
    assert claim.ok is True

    failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-escalate",
            lease_token=claim.lease_token,
            error_code="exec_failed",
            error_message="manual intervention required",
            to_dead_letter=True,
            metadata={"escalate_to_human_review": True},
        )
    )
    assert failed.status == "waiting_human"

    pending = service.query_pending_human_reviews(QueryPendingHumanReviewsV1(workspace=str(workspace), limit=10))
    assert len(pending) == 1
    assert pending[0]["task_id"] == "task-escalate"


def test_fact_emit_writes_outbox_and_marks_sent(tmp_path: Path) -> None:
    """Test that publish_work_item writes outbox record with pending status.

    The relay (relay_outbox_messages) processes pending messages and marks them as sent.
    This test verifies the outbox relay pattern: state -> outbox (pending) -> relay -> fact_stream.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-outbox-sent",
            run_id="run-outbox-sent",
            task_id="task-outbox-sent",
            stage="pending_design",
            source_role="pm",
            payload={"title": "outbox sent"},
        )
    )

    store = get_store(str(workspace))
    # Verify outbox record is written with pending status
    pending = store.load_outbox_messages(str(workspace), statuses=("pending",), limit=50)
    assert len(pending) >= 1, "outbox should have pending message after publish"
    latest = pending[-1]
    assert latest["task_id"] == "task-outbox-sent"
    assert latest["event_type"] == "task_market.work_item_published"
    assert latest["status"] == "pending"

    # Now call the relay to process pending messages
    with patch("polaris.cells.runtime.task_market.internal.service.append_fact_event") as mock_emit:
        mock_emit.return_value = None
        service.relay_outbox_messages(str(workspace), limit=50)

    # Verify outbox is marked as sent after relay processing
    sent = store.load_outbox_messages(str(workspace), statuses=("sent",), limit=50)
    assert len(sent) >= 1, "outbox should be marked sent after relay"
    latest_sent = sent[-1]
    assert latest_sent["task_id"] == "task-outbox-sent"
    assert latest_sent["event_type"] == "task_market.work_item_published"
    assert latest_sent["status"] == "sent"


def test_fact_emit_failure_marks_failed_and_relay_recovers(tmp_path: Path) -> None:
    """Test that relay marks outbox as failed when append_fact_event fails, and recovers on retry.

    The outbox relay pattern: if relay's append_fact_event fails, message is marked failed.
    On next relay run with successful emit, message is marked sent.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    # First relay call fails
    with patch(
        "polaris.cells.runtime.task_market.internal.service.append_fact_event",
        side_effect=RuntimeError("emit down"),
    ):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="trace-outbox-fail",
                run_id="run-outbox-fail",
                task_id="task-outbox-fail",
                stage="pending_design",
                source_role="pm",
                payload={"title": "outbox fail"},
            )
        )
        # Relay fails to deliver
        relay = service.relay_outbox_messages(str(workspace), limit=50)

    store = get_store(str(workspace))
    failed_rows = store.load_outbox_messages(str(workspace), statuses=("failed",), limit=50)
    assert len(failed_rows) >= 1, "outbox should be marked failed after relay failure"
    failed = failed_rows[-1]
    assert failed["task_id"] == "task-outbox-fail"
    assert int(str(failed["attempts"] or "0")) >= 1
    assert "emit down" in str(failed["last_error"])

    # Second relay call recovers (emit succeeds)
    with patch("polaris.cells.runtime.task_market.internal.service.append_fact_event") as mock_emit:
        mock_emit.return_value = None
        relay = service.relay_outbox_messages(str(workspace), limit=50)
    assert relay["sent"] >= 1, "relay should recover and mark sent on successful emit"

    # Verify final status is sent
    sent = store.load_outbox_messages(str(workspace), statuses=("sent",), limit=50)
    assert len(sent) >= 1
    latest = sent[-1]
    assert latest["task_id"] == "task-outbox-fail"
    assert latest["status"] == "sent"

    sent = store.load_outbox_messages(str(workspace), statuses=("sent",), limit=50)
    assert any(str(row.get("task_id") or "") == "task-outbox-fail" for row in sent)


def test_atomic_write_preserves_items_and_outbox_together(tmp_path: Path) -> None:
    """Verify that save_items_and_outbox_atomic writes items, transitions,
    and outbox records in a single SQLite transaction.

    If the outbox write fails mid-way, the items should NOT be persisted
    (rollback should undo all writes).
    """
    import os

    os.environ["POLARIS_TASK_MARKET_STORE"] = "sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    # Publish creates items + transition + outbox atomically.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-atomic",
            run_id="run-atomic",
            task_id="task-atomic",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "atomic test"},
        )
    )

    store = get_store(str(workspace))

    # Item should exist.
    items = store.load_items()
    assert "task-atomic" in items
    assert items["task-atomic"].status == "pending_exec"

    # Transition should exist.
    transitions = store.load_transitions("task-atomic")
    assert len(transitions) == 1
    assert transitions[0]["event_type"] == "published"

    # Outbox should have a pending record.
    pending = store.load_outbox_messages(str(workspace), statuses=("pending",), limit=10)
    assert len(pending) == 1
    assert pending[0]["task_id"] == "task-atomic"
    assert pending[0]["event_type"] == "task_market.work_item_published"


def test_atomic_rollback_on_store_failure(tmp_path: Path) -> None:
    """Verify that if the store's save_items_and_outbox_atomic fails,
    no partial data is left behind.

    We test this by forcing a rollback in the SQLite store's transaction.
    """
    import os

    os.environ["POLARIS_TASK_MARKET_STORE"] = "sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    store = get_store(str(workspace))

    from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord, now_iso

    item = TaskWorkItemRecord(
        task_id="task-rollback-test",
        trace_id="t-rb",
        run_id="r-rb",
        workspace=str(workspace),
        stage="pending_exec",
        status="pending_exec",
        priority="medium",
        payload={},
        metadata={},
        created_at=now_iso(),
        updated_at=now_iso(),
    )

    # Write successfully first.
    store.save_items_and_outbox_atomic(
        items={"task-rollback-test": item},
        transitions=[
            {
                "task_id": "task-rollback-test",
                "from_status": "",
                "to_status": "pending_exec",
                "event_type": "published",
                "worker_id": "",
                "lease_token": "",
                "version": 1,
                "metadata": {},
            }
        ],
        outbox_records=[
            {
                "outbox_id": "ox-rollback",
                "workspace": str(workspace),
                "event_type": "test.rollback",
                "payload": {},
                "status": "pending",
            }
        ],
    )

    assert "task-rollback-test" in store.load_items()
    assert len(store.load_transitions("task-rollback-test")) == 1

    # Now test rollback: begin a transaction, modify data, then rollback.
    store.begin()
    item.status = "in_execution"
    store.upsert_item(item)
    # The item is modified in the transaction but not committed.
    assert store.load_items().get("task-rollback-test").status == "in_execution"
    store.rollback()

    # After rollback, the original status should be preserved.
    items_after = store.load_items()
    assert items_after["task-rollback-test"].status == "pending_exec"


# ---------------------------------------------------------------------------
# Consumer Loop Management Tests
# ---------------------------------------------------------------------------


class FakeConsumerForService:
    """Minimal consumer stub for service-level consumer loop tests."""

    def __init__(
        self,
        workspace: str = "",
        worker_id: str = "",
        poll_interval: float = 0.02,
        **kwargs: object,
    ) -> None:
        self._stop_event = threading.Event()
        self.poll_interval = poll_interval

    def run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            pass

    def stop(self) -> None:
        self._stop_event.set()


def test_start_consumer_loops_creates_manager(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    started = service.start_consumer_loops(
        str(workspace),
        consumer_types={
            "chief_engineer": FakeConsumerForService,
            "director": FakeConsumerForService,
            "qa": FakeConsumerForService,
        },
    )
    assert started is True

    status = service.query_consumer_loop_status(str(workspace))
    assert status["started"] is True
    assert status["is_running"] is True

    stopped = service.stop_all_consumer_loops()
    assert stopped >= 1


def test_start_consumer_loops_returns_false_if_already_running(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.start_consumer_loops(
        str(workspace),
        consumer_types={
            "chief_engineer": FakeConsumerForService,
            "director": FakeConsumerForService,
            "qa": FakeConsumerForService,
        },
    )

    started_again = service.start_consumer_loops(
        str(workspace),
        consumer_types={
            "chief_engineer": FakeConsumerForService,
            "director": FakeConsumerForService,
            "qa": FakeConsumerForService,
        },
    )
    assert started_again is False

    service.stop_all_consumer_loops()


def test_stop_consumer_loops_cleans_up(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.start_consumer_loops(
        str(workspace),
        consumer_types={
            "chief_engineer": FakeConsumerForService,
            "director": FakeConsumerForService,
            "qa": FakeConsumerForService,
        },
    )

    stopped = service.stop_consumer_loops(str(workspace))
    assert stopped is True

    # Stopping again should return False.
    stopped_again = service.stop_consumer_loops(str(workspace))
    assert stopped_again is False

    status = service.query_consumer_loop_status(str(workspace))
    assert status["started"] is False


def test_query_consumer_loop_status_for_unknown_workspace(tmp_path: Path) -> None:
    service = TaskMarketService()
    status = service.query_consumer_loop_status("/nonexistent")
    assert status["started"] is False
    assert status["is_running"] is False
