from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_market.internal.errors import FSMTransitionError, StaleLeaseTokenError
from polaris.cells.runtime.task_market.internal.store import get_store
from polaris.cells.runtime.task_market.public.contracts import (
    TASK_REQUEUE_RECEIPTS_METADATA_KEY,
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryChangeOrdersV1,
    QueryPendingHumanReviewsV1,
    QueryPlanRevisionsV1,
    QueryTaskMarketStatusV1,
    QueryTaskRequeueReceiptV1,
    RegisterPlanRevisionCommandV1,
    RenewTaskLeaseCommandV1,
    RequestHumanReviewCommandV1,
    RequeueTaskCommandV1,
    ResolveHumanReviewCommandV1,
    SubmitChangeOrderCommandV1,
    TaskMarketError,
    TaskRequeueReceiptV1,
    TaskWorkItemResultV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


class _FakeCognitiveRuntimeService:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.commands: list[Any] = []
        self.closed = False

    def record_runtime_receipt(self, command: Any) -> SimpleNamespace:
        self.commands.append(command)
        if not self.ok:
            return SimpleNamespace(
                ok=False,
                receipt=None,
                error_code="receipt_denied",
                error_message="receipt denied",
            )
        return SimpleNamespace(
            ok=True,
            receipt=SimpleNamespace(receipt_id=f"receipt-{len(self.commands)}"),
            error_code="",
            error_message="",
        )

    def close(self) -> None:
        self.closed = True


class _RecordingStore:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.atomic_expected_versions: list[dict[str, int]] = []
        self.atomic_transitions: list[list[dict[str, Any]]] = []
        self.atomic_outbox_records: list[list[dict[str, Any]]] = []
        self.atomic_dead_letter_records: list[list[dict[str, Any]]] = []
        self.atomic_human_review_records: list[list[dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def save_items_and_outbox_atomic(
        self,
        *,
        items: dict[str, Any],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
        expected_versions: dict[str, int] | None = None,
        dead_letter_records: list[dict[str, Any]] | None = None,
        human_review_records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.atomic_expected_versions.append(dict(expected_versions or {}))
        self.atomic_transitions.append([dict(row) for row in transitions])
        self.atomic_outbox_records.append([dict(row) for row in outbox_records])
        self.atomic_dead_letter_records.append([dict(row) for row in dead_letter_records or []])
        self.atomic_human_review_records.append([dict(row) for row in human_review_records or []])
        self._delegate.save_items_and_outbox_atomic(
            items=items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected_versions,
            dead_letter_records=dead_letter_records,
            human_review_records=human_review_records,
        )




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


def test_hot_mutation_paths_pass_expected_versions_to_atomic_store(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    recording_store = _RecordingStore(get_store(str(workspace)))
    service._get_store = lambda _workspace: recording_store

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-cas-service",
            run_id="run-cas-service",
            task_id="task-cas-service",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Implement API"},
        )
    )
    recording_store.atomic_expected_versions.clear()

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

    renewed = service.renew_task_lease(
        RenewTaskLeaseCommandV1(
            workspace=str(workspace),
            task_id="task-cas-service",
            lease_token=claimed.lease_token,
            visibility_timeout_seconds=60,
        )
    )
    assert renewed.ok is True

    acknowledged = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-cas-service",
            lease_token=claimed.lease_token,
            next_stage="pending_qa",
            summary="Execution complete",
        )
    )
    assert acknowledged.ok is True
    assert recording_store.atomic_expected_versions == [
        {"task-cas-service": 1},
        {"task-cas-service": 2},
        {"task-cas-service": 3},
    ]


def test_human_review_state_and_review_record_share_atomic_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    recording_store = _RecordingStore(get_store(str(workspace)))
    service._get_store = lambda _workspace: recording_store

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-hitl-atomic",
            run_id="run-hitl-atomic",
            task_id="task-hitl-atomic",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Needs review"},
        )
    )
    recording_store.atomic_expected_versions.clear()
    recording_store.atomic_transitions.clear()
    recording_store.atomic_outbox_records.clear()
    recording_store.atomic_human_review_records.clear()

    requested = service.request_human_review(
        RequestHumanReviewCommandV1(
            workspace=str(workspace),
            task_id="task-hitl-atomic",
            reason="needs human review",
            requested_by="director",
        )
    )

    assert requested.ok is True
    assert recording_store.atomic_expected_versions[-1] == {"task-hitl-atomic": 1}
    assert recording_store.atomic_human_review_records[-1][0]["task_id"] == "task-hitl-atomic"
    assert recording_store.atomic_transitions[-1][0]["event_type"] == "human_review_requested"
    assert recording_store.atomic_outbox_records[-1][0]["event_type"] == "task_market.human_review_requested"

    recording_store.atomic_expected_versions.clear()
    recording_store.atomic_transitions.clear()
    recording_store.atomic_outbox_records.clear()
    recording_store.atomic_human_review_records.clear()

    resolved = service.resolve_human_review(
        ResolveHumanReviewCommandV1(
            workspace=str(workspace),
            task_id="task-hitl-atomic",
            resolution="requeue_exec",
            resolved_by="director",
        )
    )

    assert resolved.ok is True
    assert recording_store.atomic_expected_versions[-1] == {"task-hitl-atomic": 2}
    assert recording_store.atomic_human_review_records[-1][0]["status"] == "resolved"
    assert recording_store.atomic_transitions[-1][0]["event_type"] == "human_review_resolved"
    assert recording_store.atomic_outbox_records[-1][0]["event_type"] == "task_market.human_review_resolved"


def test_dead_letter_record_shares_atomic_write_with_item_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    recording_store = _RecordingStore(get_store(str(workspace)))
    service._get_store = lambda _workspace: recording_store

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-dlq-atomic",
            run_id="run-dlq-atomic",
            task_id="task-dlq-atomic",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Dead letter me"},
        )
    )
    recording_store.atomic_expected_versions.clear()
    recording_store.atomic_dead_letter_records.clear()

    result = service.move_task_to_dead_letter(
        MoveTaskToDeadLetterCommandV1(
            workspace=str(workspace),
            task_id="task-dlq-atomic",
            reason="manual quarantine",
            error_code="manual_quarantine",
        )
    )

    assert result.status == "dead_letter"
    assert recording_store.atomic_expected_versions[-1] == {"task-dlq-atomic": 1}
    assert recording_store.atomic_dead_letter_records[-1][0]["task_id"] == "task-dlq-atomic"


def test_leaf_execution_ack_to_pending_exec_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-leaf-ack",
            run_id="run-leaf-ack",
            task_id="task-leaf-ack",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Leaf task"},
        )
    )
    claimed = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )

    with pytest.raises(TaskMarketError):
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="task-leaf-ack",
                lease_token=claimed.lease_token,
                next_stage="pending_exec",
                summary="try to silently requeue",
            )
        )


def test_ack_rejects_illegal_fsm_transition_without_persisting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-fsm",
            run_id="run-fsm",
            task_id="task-fsm",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Implement API"},
        )
    )
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

    with pytest.raises(FSMTransitionError):
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="task-fsm",
                lease_token=str(claimed.lease_token),
                summary="should not go back to design",
                next_stage="pending_design",
            )
        )

    stored = get_store(str(workspace)).load_items()["task-fsm"]
    assert stored.status == "in_execution"
    assert stored.stage == "pending_exec"


def test_qa_ack_waiting_human_from_in_qa_creates_review(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-hitl",
            run_id="run-hitl",
            task_id="task-hitl",
            stage="pending_qa",
            source_role="director",
            payload={"title": "Review work"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_qa",
            worker_id="qa-1",
            worker_role="qa",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True
    assert claim.status == "in_qa"

    acknowledged = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-hitl",
            lease_token=claim.lease_token,
            next_stage="waiting_human",
            summary="Needs human review",
        )
    )

    assert acknowledged.ok is True
    assert acknowledged.status == "waiting_human"
    reviews = service.query_pending_human_reviews(
        QueryPendingHumanReviewsV1(
            workspace=str(workspace),
            limit=10,
        )
    )
    assert len(reviews) == 1
    assert reviews[0]["task_id"] == "task-hitl"
    assert reviews[0]["status"] == "waiting"


def test_publish_claim_ack_records_cognitive_runtime_lifecycle_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cognitive_service = _FakeCognitiveRuntimeService()
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: cognitive_service,
    )
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-cognitive",
            run_id="run-cognitive",
            task_id="task-cognitive",
            stage="pending_exec",
            source_role="pm",
            payload={
                "title": "Implement API",
                "context_os_expected": True,
                "session_id": "role-session-task-cognitive",
            },
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
        )
    )
    assert claim.ok is True
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-cognitive",
            lease_token=claim.lease_token,
            next_stage="pending_qa",
            summary="Execution complete",
        )
    )

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    item = status.items[0]
    lifecycle = item["metadata"]["last_cognitive_runtime_lifecycle"]
    assert lifecycle["source"] == "runtime.task_market"
    assert lifecycle["event_type"] == "acknowledged"
    assert lifecycle["receipt_recorded"] is True
    assert lifecycle["context_os_expected"] is True
    assert item["metadata"]["cognitive_runtime_receipt_ids"] == ["receipt-1", "receipt-2", "receipt-3"]
    assert [command.receipt_type for command in cognitive_service.commands] == [
        "task_market_lifecycle",
        "task_market_lifecycle",
        "task_market_lifecycle",
    ]
    assert cognitive_service.commands[0].session_id == "role-session-task-cognitive"


def test_required_cognitive_runtime_receipt_failure_blocks_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cognitive_service = _FakeCognitiveRuntimeService(ok=False)
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: cognitive_service,
    )
    service = TaskMarketService()

    with pytest.raises(TaskMarketError, match="receipt denied"):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="trace-required",
                run_id="run-required",
                task_id="task-required",
                stage="pending_exec",
                source_role="pm",
                payload={
                    "title": "Implement API",
                    "cognitive_runtime_required": True,
                },
            )
        )

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    assert status.total == 0


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


def test_claim_skips_same_file_task_while_writer_lease_active(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    for task_id, target, priority in (
        ("writer", "main.js", "medium"),
        ("same-file", "main.js", "critical"),
        ("other-file", "style.css", "low"),
    ):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"trace-{task_id}",
                run_id="run-same-file-claim",
                task_id=task_id,
                stage="pending_exec",
                source_role="pm",
                priority=priority,
                payload={"construction_step": {"target_file": target}},
            )
        )

    first_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=60,
            task_id="writer",
        )
    )
    assert first_claim.ok is True
    assert first_claim.status == "in_execution"

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
    assert second_claim.task_id == "other-file"


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


def test_scope_conflict_requeue_does_not_consume_retry_budget(tmp_path: Path) -> None:
    """Transient safe-parallel file scope conflicts must wait, not dead-letter."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-scope-conflict",
            run_id="run-scope-conflict",
            task_id="task-scope-conflict",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Edit shared file", "scope_paths": ["services/user/main.py"]},
            max_attempts=3,
        )
    )

    for _ in range(4):
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
                task_id="task-scope-conflict",
                lease_token=claim.lease_token,
                error_code="SCOPE_CONFLICT",
                error_message="Scope conflict with other in-progress task",
                requeue_stage="pending_exec",
            )
        )
        assert failed.ok is True
        assert failed.status == "pending_exec"

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    by_id = {row["task_id"]: row for row in status.items}
    row = by_id["task-scope-conflict"]
    assert row["status"] == "pending_exec"
    assert row["stage"] == "pending_exec"
    assert row["attempts"] == 0
    assert status.counts.get("dead_letter", 0) == 0


def test_fail_requeue_preserves_interface_discrepancy_context_for_ce_claim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-interface-requeue",
            run_id="run-interface-requeue",
            task_id="task-interface-requeue",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": "Repair cross-file contract"},
            max_attempts=3,
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

    amendment_request = {
        "schema_version": "cross_artifact.contract_amendment_request.v1",
        "task_id": "task-interface-requeue",
        "requested_by": "director",
        "reason": "consumer imports symbols the owner task never exported",
    }
    interface_context = {
        "schema_version": "interface_discrepancy.context.v1",
        "recommended_owner": "chief_engineer",
        "interface_delta": [
            {
                "owner_file": "src/models/weather.py",
                "consumer_file": "src/engine/forecast.py",
                "missing_symbols": ["Weather", "WeatherKind"],
            }
        ],
    }
    failed = service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="task-interface-requeue",
            lease_token=claim.lease_token,
            error_code="INTERFACE_CONTRACT_AMENDMENT_REQUIRED",
            error_message="cross-artifact interface contract is missing",
            requeue_stage="pending_design",
            metadata={
                "amendment_request": amendment_request,
                "interface_discrepancy_context": interface_context,
                "internal_debug_blob": {"should_not": "reach_claim_payload"},
            },
        )
    )
    assert failed.ok is True
    assert failed.status == "pending_design"

    ce_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_design",
            worker_id="chief-engineer-1",
            worker_role="chief_engineer",
            visibility_timeout_seconds=60,
        )
    )
    assert ce_claim.ok is True
    assert ce_claim.payload["amendment_request"] == amendment_request
    assert ce_claim.payload["interface_discrepancy_context"] == interface_context
    assert ce_claim.payload["requeue_context"] == {
        "schema_version": "task_market.requeue_context.v1",
        "keys": ["amendment_request", "interface_discrepancy_context"],
    }
    assert "internal_debug_blob" not in ce_claim.payload


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
    # The parent is a REAL dead-letter: stage set and DLQ entry recorded
    # (a bare status write left it invisible to DLQ list/replay).
    assert by_id["epic-dlq"]["stage"] == "dead_letter"
    dlq_entries = {entry["task_id"]: entry for entry in get_store(str(workspace)).load_dead_letters(limit=50)}
    assert dlq_entries["epic-dlq"]["error_code"] == "child_terminal_failure"

    # Idempotent: a second reconcile must not re-dead-letter the parent.
    again = service.reconcile_parent_statuses(str(workspace))
    assert again["updated"] == 0


def test_reconcile_parent_dead_letter_with_mixed_resolved_children(tmp_path: Path) -> None:
    """dead_letter precedence over resolved in the parent merge: one dead
    child poisons the cluster even when siblings finished."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskMarketService()

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent-mixed",
            run_id="run-parent-mixed",
            task_id="epic-mixed",
            stage="pending_exec",
            source_role="pm",
            payload={"title": "Parent epic"},
            is_leaf=False,
        )
    )
    for child_id in ("child-ok", "child-dead"):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"trace-{child_id}",
                run_id="run-parent-mixed",
                task_id=child_id,
                stage="pending_exec",
                source_role="pm",
                payload={"title": child_id},
                parent_task_id="epic-mixed",
                root_task_id="epic-mixed",
                max_attempts=1,
            )
        )
    # Resolve child-ok through ack; dead-letter child-dead through fail.
    ok_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="child-ok",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="child-ok",
            lease_token=ok_claim.lease_token,
            terminal_status="resolved",
            summary="done",
            metadata={},
        )
    )
    dead_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            task_id="child-dead",
        )
    )
    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="child-dead",
            lease_token=dead_claim.lease_token,
            error_code="exec_failed",
            error_message="fatal",
        )
    )

    reconcile = service.reconcile_parent_statuses(str(workspace))
    assert reconcile["updated"] == 1
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
    by_id = {row["task_id"]: row for row in status.items}
    assert by_id["epic-mixed"]["status"] == "dead_letter"
    assert by_id["child-ok"]["status"] == "resolved"
    # No compensation from reconcile: the resolved sibling is untouched.
    assert "saga_compensation" not in (by_id["child-ok"].get("metadata") or {})


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
    monkeypatch.setenv("KERNELONE_TASK_MARKET_ENABLE_RECONCILIATION_LOOP", "1")
    monkeypatch.setenv("KERNELONE_TASK_MARKET_RECONCILIATION_INTERVAL_SECONDS", "0.05")

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


def test_outbox_relay_retry_after_mark_sent_failure_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.events.fact_stream.public.service import QueryFactEventsV1, query_fact_events

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="task_market_outbox_idempotency_test_setup",
        )
    )
    service = TaskMarketService()
    store = get_store(str(workspace))
    monkeypatch.setattr(service, "_get_store", lambda _workspace: store)

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-outbox-idem",
            run_id="run-outbox-idem",
            task_id="task-outbox-idem",
            stage="pending_design",
            source_role="pm",
            payload={"title": "outbox idem"},
        )
    )

    original_mark_sent = store.mark_outbox_message_sent
    calls = {"count": 0}

    def flaky_mark_sent(workspace_token: str, outbox_id: str, *, delivered_at: str = "") -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("mark sent failed after append")
        original_mark_sent(workspace_token, outbox_id, delivered_at=delivered_at)

    monkeypatch.setattr(store, "mark_outbox_message_sent", flaky_mark_sent)
    first_relay = service.relay_outbox_messages(str(workspace), limit=50)
    assert first_relay["failed"] >= 1
    assert calls["count"] == 1

    monkeypatch.setattr(store, "mark_outbox_message_sent", original_mark_sent)
    second_relay = service.relay_outbox_messages(str(workspace), limit=50)
    assert second_relay["sent"] >= 1

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_market.events",
            limit=50,
            offset=0,
            event_type="task_market.work_item_published",
            task_id="task-outbox-idem",
        )
    )
    assert queried.total == 1


def test_atomic_write_preserves_items_and_outbox_together(tmp_path: Path) -> None:
    """Verify that save_items_and_outbox_atomic writes items, transitions,
    and outbox records in a single SQLite transaction.

    If the outbox write fails mid-way, the items should NOT be persisted
    (rollback should undo all writes).
    """
    import os

    os.environ["KERNELONE_TASK_MARKET_STORE"] = "sqlite"
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

    os.environ["KERNELONE_TASK_MARKET_STORE"] = "sqlite"
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


class TestExecClaimReadinessGate:
    """Three-tier fission (I2): queue-scan claims at pending_exec must skip
    non-leaf supervision rows and dependency-unready steps."""

    @staticmethod
    def _publish(
        service: TaskMarketService,
        workspace: Path,
        task_id: str,
        *,
        is_leaf: bool = True,
        depends_on: tuple[str, ...] = (),
        parent: str = "",
    ) -> None:
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"tr-{task_id}",
                run_id="run-gate",
                task_id=task_id,
                stage="pending_exec",
                source_role="chief_engineer",
                payload={"title": task_id},
                is_leaf=is_leaf,
                depends_on=tuple(depends_on),
                parent_task_id=parent,
            )
        )

    @staticmethod
    def _scan_claim(service: TaskMarketService, workspace: Path) -> TaskWorkItemResultV1:
        return service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_exec",
                worker_id="dw-1",
                worker_role="director",
            )
        )

    def test_non_leaf_parent_skipped_on_scan(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "parent", is_leaf=False)
        self._publish(service, workspace, "step-1", parent="parent")
        claim = self._scan_claim(service, workspace)
        assert claim.ok is True
        assert claim.task_id == "step-1"

    def test_dependency_unready_blocks_scan_claim(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        first = self._scan_claim(service, workspace)
        assert first.task_id == "step-1"
        # step-1 leased (in progress) — step-2 must NOT be claimable.
        second = self._scan_claim(service, workspace)
        assert second.ok is False
        # advance step-1 to QA → dependency satisfied → step-2 claimable.
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=first.lease_token,
                next_stage="pending_qa",
                summary="done",
                metadata={},
            )
        )
        third = self._scan_claim(service, workspace)
        assert third.ok is True
        assert third.task_id == "step-2"

    def test_orphan_dependency_blocks(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-x", depends_on=("ghost",))
        claim = self._scan_claim(service, workspace)
        assert claim.ok is False

    def test_ack_metadata_demotes_parent_to_non_leaf(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "pm-task", is_leaf=True)
        claim = self._scan_claim(service, workspace)
        assert claim.task_id == "pm-task"
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="pm-task",
                lease_token=claim.lease_token,
                next_stage="pending_exec",
                summary="fissioned",
                metadata={"is_leaf": False, "fission_step_count": 2},
            )
        )
        again = self._scan_claim(service, workspace)
        assert again.ok is False  # demoted parent is a supervision row now


class TestDesignClaimOrderingGate:
    """组合律 (live I3-r14): a pending_design parent that depends_on another
    parent must not fission until the producer parent has left design, so the
    interface ledger is populated before the consumer reads it."""

    @staticmethod
    def _publish_design(
        service: TaskMarketService,
        workspace: Path,
        task_id: str,
        *,
        depends_on: tuple[str, ...] = (),
    ) -> None:
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"tr-{task_id}",
                run_id="run-design",
                task_id=task_id,
                stage="pending_design",
                source_role="pm",
                payload={"title": task_id},
                is_leaf=True,
                depends_on=tuple(depends_on),
            )
        )

    @staticmethod
    def _claim_design(service: TaskMarketService, workspace: Path) -> TaskWorkItemResultV1:
        return service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_design",
                worker_id="ce-1",
                worker_role="chief_engineer",
            )
        )

    def test_consumer_parent_waits_for_producer_to_leave_design(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish_design(service, workspace, "PM-1")
        self._publish_design(service, workspace, "PM-2", depends_on=("PM-1",))
        # Producer must be claimed first; consumer is gated while PM-1 is in design.
        first = self._claim_design(service, workspace)
        assert first.task_id == "PM-1"
        blocked = self._claim_design(service, workspace)
        assert blocked.ok is False
        # PM-1 fissions (advances to pending_exec) → PM-2 becomes claimable.
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="PM-1",
                lease_token=first.lease_token,
                next_stage="pending_exec",
                summary="fissioned",
                metadata={"is_leaf": False, "fission_step_count": 2},
            )
        )
        third = self._claim_design(service, workspace)
        assert third.ok is True
        assert third.task_id == "PM-2"

    def test_independent_parents_not_blocked(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish_design(service, workspace, "PM-A")
        self._publish_design(service, workspace, "PM-B")
        first = self._claim_design(service, workspace)
        assert first.ok is True
        second = self._claim_design(service, workspace)
        assert second.ok is True
        assert {first.task_id, second.task_id} == {"PM-A", "PM-B"}

    def test_orphan_design_dependency_does_not_block(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish_design(service, workspace, "PM-X", depends_on=("ghost",))
        claim = self._claim_design(service, workspace)
        assert claim.ok is True
        assert claim.task_id == "PM-X"


