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




class TestDependencyTerminalCascade:
    """Dependents of a terminally-failed dependency must cascade into the
    DLQ at claim time instead of stranding as permanently-unclaimable
    ``pending_exec`` rows (live I3-r7)."""

    _publish = staticmethod(TestExecClaimReadinessGate._publish)
    _scan_claim = staticmethod(TestExecClaimReadinessGate._scan_claim)

    @staticmethod
    def _claim_targeted(service: TaskMarketService, workspace: Path, task_id: str) -> TaskWorkItemResultV1:
        return service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_exec",
                worker_id="dw-1",
                worker_role="director",
                task_id=task_id,
            )
        )

    def _dead_letter(self, service: TaskMarketService, workspace: Path, task_id: str) -> None:
        claim = self._claim_targeted(service, workspace, task_id)
        assert claim.ok is True
        failed = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id=task_id,
                lease_token=claim.lease_token,
                error_code="exec_failed",
                error_message="fatal",
                to_dead_letter=True,
            )
        )
        assert failed.status == "dead_letter"

    def test_dead_lettered_dependency_cascades_dependent_to_dlq(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        self._dead_letter(service, workspace, "step-1")

        claim = self._scan_claim(service, workspace)
        assert claim.ok is False
        assert claim.reason == "no_claimable_work_item"

        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "dead_letter"
        assert by_id["step-2"]["stage"] == "dead_letter"
        dlq_entries = {entry["task_id"]: entry for entry in get_store(str(workspace)).load_dead_letters(limit=50)}
        assert dlq_entries["step-2"]["error_code"] == "dependency_terminal_failure"
        assert "step-1" in dlq_entries["step-2"]["reason"]

    def test_child_dead_letter_reconciles_parent_during_dependency_sweep(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "parent", is_leaf=False)
        self._publish(service, workspace, "step-1", parent="parent")
        self._publish(service, workspace, "step-2", parent="parent", depends_on=("step-1",))
        self._dead_letter(service, workspace, "step-1")

        claim = self._scan_claim(service, workspace)
        assert claim.ok is False
        assert claim.reason == "no_claimable_work_item"

        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "dead_letter"
        assert by_id["parent"]["status"] == "dead_letter"
        assert by_id["parent"]["stage"] == "dead_letter"
        dlq_entries = {entry["task_id"]: entry for entry in get_store(str(workspace)).load_dead_letters(limit=50)}
        assert dlq_entries["parent"]["error_code"] == "child_terminal_failure"

    def test_rejected_dependency_cascades_dependent(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        claim = self._claim_targeted(service, workspace, "step-1")
        # No requeue stage and attempts < max ⇒ terminal "rejected".
        failed = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=claim.lease_token,
                error_code="exec_failed",
                error_message="unrecoverable",
            )
        )
        assert failed.status == "rejected"

        scan = self._scan_claim(service, workspace)
        assert scan.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "dead_letter"

    def test_same_task_local_retry_ignores_transport_attempt_exhaustion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        clock = [1_000.0]
        monkeypatch.setattr(
            "polaris.cells.runtime.task_market.internal._service_lifecycle.now_epoch",
            lambda: clock[0],
        )
        service = TaskMarketService()
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="tr-local-repair",
                run_id="run-local-repair",
                task_id="step-1",
                stage="pending_exec",
                source_role="chief_engineer",
                payload={"title": "repair current task"},
                max_attempts=1,
            )
        )
        for _round in range(3):
            claim = self._claim_targeted(service, workspace, "step-1")
            assert claim.ok is True
            failed = service.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=str(workspace),
                    task_id="step-1",
                    lease_token=claim.lease_token,
                    error_code="EXEC_TIMEOUT",
                    error_message="repairable verifier timeout",
                    requeue_stage="pending_exec",
                    failure_disposition="same_task_local_retry",
                )
            )
            assert failed.status == "pending_exec"
            deferred = self._claim_targeted(service, workspace, "step-1")
            assert deferred.ok is False
            assert service.next_local_retry_delay(str(workspace), "pending_exec") is not None
            clock[0] += 61.0

        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        assert status.items[0]["attempts"] == 0
        assert get_store(str(workspace)).load_dead_letters(limit=10) == []

    def test_pending_qa_local_retry_is_deferred_without_burning_attempts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws-qa-local-retry"
        workspace.mkdir()
        clock = [2_000.0]
        monkeypatch.setattr(
            "polaris.cells.runtime.task_market.internal._service_lifecycle.now_epoch",
            lambda: clock[0],
        )
        service = TaskMarketService()
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="tr-qa-local-retry",
                run_id="run-qa-local-retry",
                task_id="step-qa",
                stage="pending_qa",
                source_role="director",
                payload={"title": "retry exact verifier"},
                max_attempts=1,
            )
        )
        claim = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_qa",
                task_id="step-qa",
                worker_id="qa",
                worker_role="qa",
            )
        )
        assert claim.ok is True
        failed = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-qa",
                lease_token=claim.lease_token,
                error_code="QA_TRANSIENT",
                error_message="receipt commit unavailable",
                requeue_stage="pending_qa",
                failure_disposition="same_task_local_retry",
            )
        )
        assert failed.status == "pending_qa"
        assert service.next_local_retry_delay(str(workspace), "pending_qa") == pytest.approx(1.0)
        deferred = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_qa",
                task_id="step-qa",
                worker_id="qa",
                worker_role="qa",
            )
        )
        assert deferred.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        assert status.items[0]["attempts"] == 0

    def test_local_retry_budget_exhaustion_parks_without_dlq_or_dependency_cascade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "ws-local-retry-park"
        workspace.mkdir()
        clock = [3_000.0]
        monkeypatch.setattr(
            "polaris.cells.runtime.task_market.internal._service_lifecycle.now_epoch",
            lambda: clock[0],
        )
        service = TaskMarketService()
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="tr-step-1",
                run_id="run-gate",
                task_id="step-1",
                stage="pending_qa",
                source_role="director",
                payload={"title": "step-1"},
            )
        )
        self._publish(service, workspace, "step-2", depends_on=("step-1",))

        def claim_qa() -> TaskWorkItemResultV1:
            return service.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=str(workspace),
                    stage="pending_qa",
                    task_id="step-1",
                    worker_id="qa",
                    worker_role="qa",
                )
            )

        for _round in range(6):
            claim = claim_qa()
            failed = service.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=str(workspace),
                    task_id="step-1",
                    lease_token=claim.lease_token,
                    error_code="QA_TRANSIENT",
                    error_message="owner receipt temporarily unavailable",
                    requeue_stage="pending_qa",
                    failure_disposition="same_task_local_retry",
                )
            )
            assert failed.status == "pending_qa"
            assert claim_qa().ok is False
            clock[0] += 61.0

        final_claim = claim_qa()
        parked = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=final_claim.lease_token,
                error_code="QA_TRANSIENT",
                error_message="owner receipt still unavailable",
                requeue_stage="pending_qa",
                failure_disposition="same_task_local_retry",
            )
        )

        assert parked.status == "rejected"
        assert parked.reason == "control_plane_blocked"
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        park = by_id["step-1"]["metadata"]["task_local_retry_control_plane_park"]
        assert park["status"] == "CONTROL_PLANE_BLOCKED"
        assert park["owner_qualification_required"] is True
        assert by_id["step-2"]["status"] == "pending_exec"
        assert service.next_local_retry_delay(str(workspace), "pending_qa") is None
        assert get_store(str(workspace)).load_dead_letters(limit=10) == []

    def test_owner_qualified_model_ceiling_is_terminal_without_cascade(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws-model-ceiling"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        claim = self._claim_targeted(service, workspace, "step-1")
        stopped = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=claim.lease_token,
                error_code="MODEL_CEILING_QUALIFIED",
                error_message="owner evidence proves no executable tool use",
                failure_disposition="model_ceiling",
                metadata={"owner_qualification_receipt": "owner://model-ceiling/1"},
            )
        )
        assert stopped.status == "rejected"
        scan = self._scan_claim(service, workspace)
        assert scan.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "pending_exec"
        assert get_store(str(workspace)).load_dead_letters(limit=10) == []

    def test_isolated_contract_blocker_never_cascades_or_compensates(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        claim = self._claim_targeted(service, workspace, "step-1")
        blocked = service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=claim.lease_token,
                error_code="CONTRACT_AUTHORITY_CONTRADICTION",
                error_message="declared scope cannot authorize requested write",
                failure_disposition="isolated_contract_blocker",
                metadata={"automatic_upstream_replan": False},
            )
        )
        assert blocked.status == "rejected"
        assert blocked.reason == "contract_blocked"

        scan = self._scan_claim(service, workspace)
        assert scan.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-1"]["status"] == "rejected"
        assert by_id["step-1"]["metadata"]["dependency_terminal_cascade_suppressed"] is True
        assert "saga_task_compensation" not in by_id["step-1"]["metadata"]
        assert by_id["step-2"]["status"] == "pending_exec"
        assert get_store(str(workspace)).load_dead_letters(limit=10) == []

    def test_cascade_is_transitive_across_dependency_chain(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        self._publish(service, workspace, "step-3", depends_on=("step-2",))
        self._dead_letter(service, workspace, "step-1")

        scan = self._scan_claim(service, workspace)
        assert scan.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "dead_letter"
        assert by_id["step-3"]["status"] == "dead_letter"
        # The single sweep emitted one cascade DLQ entry per dependent.
        dlq_entries = [
            entry
            for entry in get_store(str(workspace)).load_dead_letters(limit=50)
            if entry["error_code"] == "dependency_terminal_failure"
        ]
        assert {entry["task_id"] for entry in dlq_entries} == {"step-2", "step-3"}

    def test_failed_requeued_dependency_does_not_cascade(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        claim = self._claim_targeted(service, workspace, "step-1")
        service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-1",
                lease_token=claim.lease_token,
                error_code="exec_failed",
                error_message="transient",
                requeue_stage="pending_exec",
            )
        )
        # step-1 recovered into the queue: step-2 blocked but NOT cascaded,
        # and step-1 itself is claimable again.
        scan = self._scan_claim(service, workspace)
        assert scan.ok is True
        assert scan.task_id == "step-1"
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-2"]["status"] == "pending_exec"

    def test_in_flight_dependent_not_swept(self, tmp_path: Path) -> None:
        """A claimed step keeps stage pending_exec with status in_execution —
        the sweep must never yank its live lease even when its dependency
        fails terminally at QA after the (legal) claim."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-a")
        self._publish(service, workspace, "step-b", depends_on=("step-a",))
        a_claim = self._scan_claim(service, workspace)
        assert a_claim.task_id == "step-a"
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-a",
                lease_token=a_claim.lease_token,
                next_stage="pending_qa",
                summary="done",
                metadata={},
            )
        )
        # Dep at QA passes the readiness gate: B is legally claimed.
        b_claim = self._scan_claim(service, workspace)
        assert b_claim.task_id == "step-b"
        # QA terminally fails A while B is mid-execution.
        qa_claim = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_qa",
                worker_id="qa-1",
                worker_role="qa",
                task_id="step-a",
            )
        )
        service.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-a",
                lease_token=qa_claim.lease_token,
                error_code="qa_failed",
                error_message="fatal",
                to_dead_letter=True,
            )
        )
        # The next queue-scan sweeps nothing: B is in-flight, not queued.
        scan = self._scan_claim(service, workspace)
        assert scan.ok is False
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        assert by_id["step-b"]["status"] == "in_execution"
        # B's lease survived: the executing worker can still acknowledge.
        ack = service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="step-b",
                lease_token=b_claim.lease_token,
                next_stage="pending_qa",
                summary="done",
                metadata={},
            )
        )
        assert ack.ok is True

    @pytest.mark.parametrize("error_origin", ["public_contract", "internal"])
    def test_receipt_failure_skips_item_without_poisoning_claims(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_origin: str
    ) -> None:
        """A required-receipt failure on one cascade candidate must not abort
        the sweep, wedge the claim, or leave the item half-dead. Both
        TaskMarketError classes (same name, different modules) must be
        absorbed: the service raises the public-contract one, internal
        collaborators raise internal.errors'."""
        from polaris.cells.runtime.task_market.internal.errors import (
            TaskMarketError as InternalTaskMarketError,
        )

        error_type: type[Exception] = TaskMarketError if error_origin == "public_contract" else InternalTaskMarketError

        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        self._publish(service, workspace, "step-free")
        self._dead_letter(service, workspace, "step-1")

        original = TaskMarketService._record_cognitive_runtime_lifecycle_receipt

        def poisoned(self_: TaskMarketService, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("event_type") == "dependency_terminal_cascade":
                raise error_type("receipt backend down")
            return original(self_, **kwargs)

        monkeypatch.setattr(TaskMarketService, "_record_cognitive_runtime_lifecycle_receipt", poisoned)
        scan = self._scan_claim(service, workspace)
        assert scan.ok is True
        assert scan.task_id == "step-free"
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
        by_id = {row["task_id"]: row for row in status.items}
        # Skipped cleanly: still queued, no partial dead-letter state.
        assert by_id["step-2"]["status"] == "pending_exec"

    def test_replay_unblocks_cascaded_dependent(self, tmp_path: Path) -> None:
        from polaris.cells.runtime.task_market.public import dlq_api

        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        self._publish(service, workspace, "step-1")
        self._publish(service, workspace, "step-2", depends_on=("step-1",))
        self._dead_letter(service, workspace, "step-1")
        scan = self._scan_claim(service, workspace)
        assert scan.ok is False  # cascade swept step-2

        # Recovery loop: replay both, finish the dependency, claim the
        # dependent.
        assert dlq_api.replay_dlq_item(workspace=str(workspace), task_id="step-1", target_stage="pending_exec")["ok"]
        assert dlq_api.replay_dlq_item(workspace=str(workspace), task_id="step-2", target_stage="pending_exec")["ok"]
        first = self._scan_claim(service, workspace)
        assert first.task_id == "step-1"
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
        second = self._scan_claim(service, workspace)
        assert second.ok is True
        assert second.task_id == "step-2"


def test_json_store_dead_letter_append_is_idempotent_by_task_id(tmp_path: Path) -> None:
    """A retried dead-letter (replayed cascade sweep) must replace, not
    duplicate — duplicates eventually evict unrelated entries past the
    10k window (matches the SQLite backend's INSERT OR REPLACE)."""
    from polaris.cells.runtime.task_market.internal.store import TaskMarketJSONStore

    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = TaskMarketJSONStore(str(workspace))
    store.append_dead_letter({"task_id": "T-1", "reason": "first", "error_code": "x"})
    store.append_dead_letter({"task_id": "T-2", "reason": "other", "error_code": "x"})
    store.append_dead_letter({"task_id": "T-1", "reason": "second", "error_code": "x"})
    entries = store.load_dead_letters(limit=50)
    by_id = {str(entry["task_id"]): entry for entry in entries}
    assert len(entries) == 2
    assert by_id["T-1"]["reason"] == "second"
    assert by_id["T-2"]["reason"] == "other"


def test_json_store_skips_corrupt_work_item_records(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from polaris.cells.runtime.task_market.internal.store import TaskMarketJSONStore

    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = TaskMarketJSONStore(str(workspace))
    store.items_path.parent.mkdir(parents=True, exist_ok=True)
    store.items_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "task_id": "task-valid",
                        "trace_id": "trace-1",
                        "run_id": "run-1",
                        "workspace": str(workspace),
                        "stage": "pending_exec",
                        "status": "pending_exec",
                        "priority": "medium",
                        "payload": {"goal": "run"},
                    },
                    {
                        "task_id": "task-missing-status",
                        "trace_id": "trace-2",
                        "run_id": "run-1",
                        "workspace": str(workspace),
                        "stage": "pending_exec",
                        "priority": "medium",
                        "payload": {"goal": "bad"},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        items = store.load_items()

    assert list(items) == ["task-valid"]
    assert "skipping corrupt work item record" in caplog.text
    assert "status" in caplog.text


def test_stage_advance_resets_attempt_budget(tmp_path: Path) -> None:
    """Live I3-r9: a step that succeeded on its final exec attempt was
    retry-exhausted-killed by the QA queue claim before QA ever judged it.
    Each stage advance opens a fresh attempt budget."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-budget",
            run_id="run-budget",
            task_id="step-budget",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": "step"},
            max_attempts=2,
        )
    )

    def _claim() -> Any:
        return service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_exec",
                worker_id="dw-1",
                worker_role="director",
            )
        )

    first = _claim()
    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-budget",
            lease_token=first.lease_token,
            error_code="exec_failed",
            error_message="transient",
            requeue_stage="pending_exec",
        )
    )
    second = _claim()
    assert second.ok is True  # attempts now == max_attempts (2)
    ack = service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-budget",
            lease_token=second.lease_token,
            next_stage="pending_qa",
            summary="succeeded on final exec attempt",
            metadata={},
        )
    )
    assert ack.ok is True

    qa_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_qa",
            worker_id="qa-1",
            worker_role="qa",
        )
    )
    # Pre-fix this dead-lettered with reason retry_exhausted_on_claim.
    assert qa_claim.ok is True
    assert qa_claim.task_id == "step-budget"


def test_requeue_carries_failure_teaching_and_advance_clears_it(tmp_path: Path) -> None:
    """Live I3-r10: claim results expose payload (not last_error), so a
    requeued item's worker retried blind. The failure summary must ride the
    payload and be retired on the next successful stage advance."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-teach",
            run_id="run-teach",
            task_id="step-teach",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": "step"},
        )
    )

    def _claim() -> Any:
        return service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=str(workspace),
                stage="pending_exec",
                worker_id="dw-1",
                worker_role="director",
            )
        )

    first = _claim()
    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-teach",
            lease_token=first.lease_token,
            error_code="QA_step_verify_failed",
            error_message="step verify failed (exit 1): grep -q 'id=\"levelDisplay\"'",
            requeue_stage="pending_exec",
        )
    )
    second = _claim()
    teaching = second.payload.get("last_failure")
    assert isinstance(teaching, dict)
    assert teaching["error_code"] == "QA_step_verify_failed"
    assert "levelDisplay" in teaching["error_message"]

    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-teach",
            lease_token=second.lease_token,
            next_stage="pending_qa",
            summary="fixed",
            metadata={},
        )
    )
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    row = {item["task_id"]: item for item in status.items}["step-teach"]
    assert "last_failure" not in (row.get("payload") or {})


def test_requeue_task_can_teach_next_claim_without_worker_lease(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-integration-qa",
            run_id="run-integration-qa",
            task_id="step-integration-qa",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-integration-qa",
            target_stage="pending_exec",
            reason="integration QA failed",
            metadata={
                "last_failure": {
                    "error_code": "INTEGRATION_QA_FAILED",
                    "error_message": "pytest failed after Director success",
                    "source": "pm_dispatch.integration_qa",
                }
            },
        )
    )

    assert requeued.ok is True
    claimed = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-retry",
            worker_role="director",
        )
    )
    assert claimed.ok is True
    teaching = claimed.payload.get("last_failure")
    assert isinstance(teaching, dict)
    assert teaching["error_code"] == "INTEGRATION_QA_FAILED"
    assert teaching["source"] == "pm_dispatch.integration_qa"


def test_requeue_task_action_id_is_atomic_and_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-project-completion",
            run_id="run-project-completion",
            task_id="step-owner",
            stage="pending_exec",
            source_role="chief_engineer_dispatch",
            payload={"title": "owner"},
        )
    )
    action_id = "a" * 64
    fingerprint = "b" * 64
    command = RequeueTaskCommandV1(
        workspace=str(workspace),
        task_id="step-owner",
        target_stage="pending_exec",
        reason="required verifier failed",
        metadata={
            "source": "project_completion.verification_failure",
            "last_failure": {"error_code": "BUILD_FAILED", "error_message": "cargo test failed"},
            "verification_failure_report": {"modality": "test", "exit_code": 101},
        },
        idempotency_key=action_id,
        idempotency_fingerprint=fingerprint,
    )

    first = service.requeue_task(command)
    second = service.requeue_task(command)
    receipt = service.query_task_requeue_receipt(
        QueryTaskRequeueReceiptV1(
            workspace=str(workspace),
            task_id="step-owner",
            idempotency_key=action_id,
        )
    )

    assert first.ok is True
    assert first.reason == "requeued"
    assert second.ok is True
    assert second.reason == "already_requeued"
    assert second.version == first.version
    assert isinstance(receipt, TaskRequeueReceiptV1)
    assert receipt.status == "accepted"
    assert receipt.idempotency_key == action_id
    assert receipt.idempotency_fingerprint == fingerprint
    assert receipt.effect_hash == command.effect_hash
    assert receipt.transition_version == first.version
    assert len(receipt.receipt_hash) == 64


def test_publish_rejects_forged_runtime_owned_requeue_receipts(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match="runtime-owned"):
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-forged-requeue-receipt",
            run_id="run-forged-requeue-receipt",
            task_id="step-owner",
            stage="pending_exec",
            source_role="untrusted-caller",
            payload={"title": "owner"},
            metadata={
                TASK_REQUEUE_RECEIPTS_METADATA_KEY: {
                    "forged": {"status": "accepted"},
                }
            },
        )


def test_requeue_task_rejects_action_id_reuse_with_different_fingerprint(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-project-completion-conflict",
            run_id="run-project-completion-conflict",
            task_id="step-owner",
            stage="pending_exec",
            source_role="chief_engineer_dispatch",
            payload={"title": "owner"},
        )
    )
    action_id = "c" * 64
    first = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-owner",
            target_stage="pending_exec",
            reason="verification failed",
            metadata={"source": "project_completion.verification_failure"},
            idempotency_key=action_id,
            idempotency_fingerprint="d" * 64,
        )
    )
    conflict = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-owner",
            target_stage="pending_exec",
            reason="different residual",
            metadata={"source": "project_completion.verification_failure"},
            idempotency_key=action_id,
            idempotency_fingerprint="e" * 64,
        )
    )

    assert first.ok is True
    assert conflict.ok is False
    assert conflict.reason == "idempotency_conflict"
    assert conflict.version == first.version


def test_requeue_task_rejects_same_fingerprint_for_different_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-project-completion-effect-conflict",
            run_id="run-project-completion-effect-conflict",
            task_id="step-owner",
            stage="pending_exec",
            source_role="chief_engineer_dispatch",
            payload={"title": "owner"},
        )
    )
    action_id = "4" * 64
    fingerprint = "5" * 64
    first = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-owner",
            target_stage="pending_exec",
            reason="test verifier failed",
            idempotency_key=action_id,
            idempotency_fingerprint=fingerprint,
        )
    )
    conflict = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-owner",
            target_stage="pending_qa",
            reason="pretend verifier passed",
            idempotency_key=action_id,
            idempotency_fingerprint=fingerprint,
        )
    )

    assert first.ok is True
    assert conflict.ok is False
    assert conflict.reason == "idempotency_conflict"
    assert conflict.version == first.version


def test_requeue_task_effect_hash_binds_metadata_and_reopen_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-project-completion-effect-payload",
            run_id="run-project-completion-effect-payload",
            task_id="step-owner",
            stage="pending_exec",
            source_role="chief_engineer_dispatch",
            payload={"title": "owner"},
        )
    )
    common = {
        "workspace": str(workspace),
        "task_id": "step-owner",
        "target_stage": "pending_exec",
        "reason": "verifier failed",
        "idempotency_key": "6" * 64,
        "idempotency_fingerprint": "7" * 64,
    }
    first = service.requeue_task(
        RequeueTaskCommandV1(
            **common,
            metadata={"source": "verification.failure", "exit_code": 1},
            reopen_policy={"max_reopen_count": 2},
        )
    )
    metadata_conflict = service.requeue_task(
        RequeueTaskCommandV1(
            **common,
            metadata={"source": "verification.failure", "exit_code": 0},
            reopen_policy={"max_reopen_count": 2},
        )
    )
    policy_conflict = service.requeue_task(
        RequeueTaskCommandV1(
            **common,
            metadata={"source": "verification.failure", "exit_code": 1},
            reopen_policy={"max_reopen_count": 3},
        )
    )

    assert first.ok is True
    assert metadata_conflict.ok is False
    assert metadata_conflict.reason == "idempotency_conflict"
    assert policy_conflict.ok is False
    assert policy_conflict.reason == "idempotency_conflict"
    assert metadata_conflict.version == first.version
    assert policy_conflict.version == first.version


def test_concurrent_requeue_wakes_consume_one_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    published = service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-project-completion-concurrent",
            run_id="run-project-completion-concurrent",
            task_id="step-owner",
            stage="pending_exec",
            source_role="chief_engineer_dispatch",
            payload={"title": "owner"},
        )
    )
    command = RequeueTaskCommandV1(
        workspace=str(workspace),
        task_id="step-owner",
        target_stage="pending_exec",
        reason="required verifier failed",
        metadata={"source": "project_completion.verification_failure"},
        idempotency_key="f" * 64,
        idempotency_fingerprint="1" * 64,
    )
    results: list[TaskWorkItemResultV1] = []
    start = threading.Barrier(3)

    def invoke() -> None:
        start.wait()
        results.append(service.requeue_task(command))

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert sorted(result.reason for result in results) == ["already_requeued", "requeued"]
    assert {result.version for result in results} == {published.version + 1}


def test_task_projection_refresh_preserves_requeue_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    publish = PublishTaskWorkItemCommandV1(
        workspace=str(workspace),
        trace_id="tr-refresh",
        run_id="run-refresh",
        task_id="step-owner",
        stage="pending_exec",
        source_role="chief_engineer_dispatch",
        payload={"title": "owner"},
    )
    service.publish_work_item(publish)
    action_id = "2" * 64
    command = RequeueTaskCommandV1(
        workspace=str(workspace),
        task_id="step-owner",
        target_stage="pending_exec",
        reason="required verifier failed",
        metadata={"source": "project_completion.verification_failure"},
        idempotency_key=action_id,
        idempotency_fingerprint="3" * 64,
    )
    service.requeue_task(command)

    service.publish_work_item(publish)
    duplicate = service.requeue_task(command)
    receipt = service.query_task_requeue_receipt(
        QueryTaskRequeueReceiptV1(
            workspace=str(workspace),
            task_id="step-owner",
            idempotency_key=action_id,
        )
    )

    assert duplicate.ok is True
    assert duplicate.reason == "already_requeued"
    assert receipt is not None


def test_requeue_task_rejects_actively_leased_work(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-active",
            run_id="run-active",
            task_id="step-active",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    claimed = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-active",
            worker_role="director",
        )
    )

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-active",
            target_stage="pending_exec",
            reason="integration QA failed",
        )
    )

    assert requeued.ok is False
    assert requeued.reason == "active_lease"
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    row = {item["task_id"]: item for item in status.items}["step-active"]
    assert row["status"] == "in_execution"
    assert row["lease_token"] == claimed.lease_token


def test_requeue_task_rejects_terminal_and_legacy_terminal_statuses(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()

    for task_id, status in (
        ("step-rejected", "rejected"),
        ("step-dead", "dead_letter"),
        ("step-completed", "completed"),
        ("step-cancelled", "cancelled"),
    ):
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"tr-{task_id}",
                run_id="run-terminal",
                task_id=task_id,
                stage="pending_exec",
                source_role="pm_dispatch",
                payload={"title": task_id},
            )
        )
        store = get_store(str(workspace))
        items = store.load_items()
        item = items[task_id]
        item.status = status
        items[task_id] = item
        store.save_items_and_outbox_atomic(items=items, transitions=[], outbox_records=[])

        requeued = service.requeue_task(
            RequeueTaskCommandV1(
                workspace=str(workspace),
                task_id=task_id,
                target_stage="pending_exec",
                reason="generic retry",
            )
        )

        assert requeued.ok is False
        assert requeued.reason in {"terminal_status", "unsupported_status"}


def test_requeue_task_rejects_legacy_integration_qa_reopen_without_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-resolved-integration",
            run_id="run-resolved-integration",
            task_id="step-resolved-integration",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director",
            worker_role="director",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-integration",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="done",
        )
    )

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-integration",
            target_stage="pending_exec",
            reason="integration QA failed",
            metadata={
                "source": "pm_dispatch.integration_qa",
                "last_failure": {
                    "error_code": "INTEGRATION_QA_FAILED",
                    "error_message": "pytest failed",
                },
            },
        )
    )

    assert requeued.ok is False
    assert requeued.reason == "terminal_status"
    assert requeued.status == "resolved"


def test_requeue_task_rejects_resolved_work_without_reopen_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-resolved-generic",
            run_id="run-resolved-generic",
            task_id="step-resolved-generic",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director",
            worker_role="director",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-generic",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="done",
        )
    )

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-generic",
            target_stage="pending_exec",
            reason="verification failed",
            metadata={
                "source": "verification.failure",
                "last_failure": {
                    "error_code": "BUILD_FAILED",
                    "error_message": "npm run build failed",
                },
            },
        )
    )

    assert requeued.ok is False
    assert requeued.reason == "terminal_status"
    assert requeued.status == "resolved"


def test_requeue_task_allows_verification_policy_to_reopen_resolved_work(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-resolved-verification",
            run_id="run-resolved-verification",
            task_id="step-resolved-verification",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director",
            worker_role="director",
        )
    )
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-verification",
            lease_token=claim.lease_token,
            terminal_status="resolved",
            summary="done",
        )
    )

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-verification",
            target_stage="pending_exec",
            reason="verification build failed",
            metadata={
                "source": "verification.failure",
                "last_failure": {
                    "error_code": "BUILD_FAILED",
                    "error_message": "npm run build failed",
                },
                "verification_failure_report": {
                    "gate": "build_test_lint",
                    "command": ["npm", "run", "build"],
                    "exit_code": 2,
                },
            },
            reopen_policy={
                "allowed_source_prefixes": ["verification."],
                "max_reopen_count": 2,
                "requires_failure_report": True,
            },
        )
    )

    assert requeued.ok is True
    assert requeued.status == "pending_exec"
    store = get_store(str(workspace))
    item = store.load_items()["step-resolved-verification"]
    assert item.metadata["reopen_count"] == 1
    assert item.metadata["last_reopen_source"] == "verification.failure"
    assert item.payload["last_failure"]["error_code"] == "BUILD_FAILED"


def test_requeue_task_enforces_verification_reopen_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-resolved-limit",
            run_id="run-resolved-limit",
            task_id="step-resolved-limit",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    store = get_store(str(workspace))
    items = store.load_items()
    item = items["step-resolved-limit"]
    item.status = "resolved"
    item.metadata = {**dict(item.metadata), "reopen_count": 1}
    items[item.task_id] = item
    store.save_items_and_outbox_atomic(items=items, transitions=[], outbox_records=[])

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-resolved-limit",
            target_stage="pending_exec",
            reason="verification build failed again",
            metadata={
                "source": "verification.failure",
                "last_failure": {
                    "error_code": "BUILD_FAILED",
                    "error_message": "npm run build failed",
                },
                "verification_failure_report": {
                    "gate": "build_test_lint",
                    "exit_code": 2,
                },
            },
            reopen_policy={
                "allowed_source_prefixes": ["verification."],
                "max_reopen_count": 1,
            },
        )
    )

    assert requeued.ok is False
    assert requeued.reason == "reopen_limit_exceeded"
    assert requeued.status == "resolved"


def test_requeue_task_allows_expired_lease_recovery(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-expired",
            run_id="run-expired",
            task_id="step-expired",
            stage="pending_exec",
            source_role="pm_dispatch",
            payload={"title": "step"},
        )
    )
    service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-expired",
            worker_role="director",
        )
    )
    store = get_store(str(workspace))
    items = store.load_items()
    item = items["step-expired"]
    item.lease_expires_at = 0.0
    items[item.task_id] = item
    store.save_items_and_outbox_atomic(items=items, transitions=[], outbox_records=[])

    requeued = service.requeue_task(
        RequeueTaskCommandV1(
            workspace=str(workspace),
            task_id="step-expired",
            target_stage="pending_exec",
            reason="supervisor recovery",
        )
    )

    assert requeued.ok is True
    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    row = {entry["task_id"]: entry for entry in status.items}["step-expired"]
    assert row["status"] == "pending_exec"
    assert row["lease_token"] == ""


class TestCrossParentFileOwnershipSerialization:
    """I3-r18 FIX-1 load-bearing proof: the market serializes two same-file writers
    via depends_on, so a later writer (the level-progression step) only runs AFTER
    its owner (the game-loop step) has left the exec queue and created the file.

    _exec_claim_ready reads only .stage/.is_leaf/.depends_on/.status, so stubs suffice.
    """

    @staticmethod
    def _stub(stage: str, *, status: str = "", is_leaf: bool = True, depends_on=()) -> SimpleNamespace:
        return SimpleNamespace(stage=stage, status=status, is_leaf=is_leaf, depends_on=list(depends_on))

    def test_dependent_blocked_until_owner_leaves_exec_queue(self) -> None:
        owner = self._stub("pending_exec")  # PM-0001-1-S4 game loop
        dependent = self._stub("pending_exec", depends_on=["owner"])  # PM-0001-2 main.js (level progression)
        items = {"owner": owner, "dependent": dependent}

        # While the owner is still executing, the dependent CANNOT claim -> no
        # concurrent clobber; the owner creates main.js first.
        assert TaskMarketService._exec_claim_ready(dependent, items) is False
        # Owner advances to QA (main.js now exists) -> dependent becomes claimable and EDITs it.
        owner.stage = "pending_qa"
        assert TaskMarketService._exec_claim_ready(dependent, items) is True
        # Owner resolved -> still claimable.
        owner.stage, owner.status = "done", "resolved"
        assert TaskMarketService._exec_claim_ready(dependent, items) is True

    def test_failed_owner_requeue_keeps_dependent_blocked(self) -> None:
        # Fail-closed: an owner that failed and requeued to pending_exec blocks the
        # dependent (it must not edit a half-written / reverted file).
        owner = self._stub("pending_exec", status="")
        dependent = self._stub("pending_exec", depends_on=["owner"])
        assert TaskMarketService._exec_claim_ready(dependent, {"owner": owner, "dependent": dependent}) is False

    def test_no_dependency_makes_both_concurrently_claimable_regression(self) -> None:
        # The bug FIX-1 closes: with EMPTY depends_on (pre-fix), two steps writing
        # the same file are BOTH immediately claimable -> run concurrently ->
        # last-write-wins -> incoherent product (r18 main.js).
        s1 = self._stub("pending_exec", depends_on=[])
        s2 = self._stub("pending_exec", depends_on=[])
        items = {"s1": s1, "s2": s2}
        assert TaskMarketService._exec_claim_ready(s1, items) is True
        assert TaskMarketService._exec_claim_ready(s2, items) is True
