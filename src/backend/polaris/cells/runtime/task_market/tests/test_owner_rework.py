"""Owner-rework routing invariants and lifecycle receipt ordering."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.runtime.task_market.internal.errors import StaleWriteConflictError
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord
from polaris.cells.runtime.task_market.internal.store import get_store
from polaris.cells.runtime.task_market.public.contracts import (
    OWNER_REWORK_HANDOFFS_METADATA_KEY,
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    OwnerReworkHandoffV1,
    OwnerReworkRouteReasonV1,
    PublishTaskWorkItemCommandV1,
    RouteOwnerReworkCommandV1,
    TaskMarketError,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


class _CommitTrackingStore:
    """Store adapter that marks completion of one selected atomic save."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.track_next_save = False
        self.route_commit_complete = False

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
        self._delegate.save_items_and_outbox_atomic(
            items=items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected_versions,
            dead_letter_records=dead_letter_records,
            human_review_records=human_review_records,
        )
        if self.track_next_save:
            self.route_commit_complete = True


class _StaleWriteStore(_CommitTrackingStore):
    """Raise the owner-rework CAS conflict without mutating the delegate."""

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
        if self.track_next_save:
            raise StaleWriteConflictError(
                "owner-rework CAS lost",
                task_id="requester",
                expected_version=1,
                actual_version=2,
            )
        super().save_items_and_outbox_atomic(
            items=items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected_versions,
            dead_letter_records=dead_letter_records,
            human_review_records=human_review_records,
        )


class _CommitAwareCognitiveRuntimeService:
    """Test double proving a route receipt follows the successful CAS."""

    def __init__(self, store: _CommitTrackingStore) -> None:
        self._store = store
        self.commands: list[Any] = []
        self.closed = False

    def record_runtime_receipt(self, command: Any) -> SimpleNamespace:
        assert self._store.route_commit_complete is True
        self.commands.append(command)
        return SimpleNamespace(
            ok=True,
            receipt=SimpleNamespace(receipt_id=f"receipt-{len(self.commands)}"),
            error_code="",
            error_message="",
        )

    def close(self) -> None:
        self.closed = True


def _publish(service: TaskMarketService, workspace: Path, task_id: str, *, priority: str = "medium") -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id=f"trace-{task_id}",
            run_id="run-owner-rework",
            task_id=task_id,
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": task_id},
            priority=priority,
        )
    )


def _claim(service: TaskMarketService, workspace: Path, task_id: str) -> str:
    result = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id=f"worker-{task_id}",
            worker_role="director",
            task_id=task_id,
        )
    )
    assert result.ok is True
    return result.lease_token


def _route_command(workspace: Path, requester_lease_token: str, *, max_reopen_count: int = 1) -> RouteOwnerReworkCommandV1:
    return RouteOwnerReworkCommandV1(
        workspace=str(workspace),
        owner_task_id="owner",
        requester_task_id="requester",
        requester_lease_token=requester_lease_token,
        handoff_id="handoff-owner-requester-1",
        failure_metadata={"error_code": "SCOPE_CONFLICT", "error_message": "owner must rework"},
        evidence_metadata={"source": "task-market-owner-rework-test"},
        metadata={"trigger": "owner-rework-test"},
        max_reopen_count=max_reopen_count,
    )


def _prepare_resolved_owner_route(service: TaskMarketService, workspace: Path) -> tuple[str, RouteOwnerReworkCommandV1]:
    _publish(service, workspace, "owner", priority="low")
    _publish(service, workspace, "requester", priority="critical")
    owner_lease_token = _claim(service, workspace, "owner")
    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="owner",
            lease_token=owner_lease_token,
            summary="owner completed before rework request",
        )
    )
    requester_lease_token = _claim(service, workspace, "requester")
    return requester_lease_token, _route_command(workspace, requester_lease_token)


def _set_item_state(
    workspace: Path,
    task_id: str,
    *,
    status: str,
    depends_on: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    store = get_store(str(workspace))
    items = store.load_items()
    item = items[task_id]
    expected_version = item.version
    item.status = status
    if depends_on is not None:
        item.depends_on = list(depends_on)
    if metadata is not None:
        item.metadata = dict(metadata)
    item.version += 1
    items[task_id] = item
    store.save_items_and_outbox_atomic(
        items={task_id: item},
        transitions=[],
        outbox_records=[],
        expected_versions={task_id: expected_version},
    )


def test_owner_rework_reopens_owner_blocks_requester_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = TaskMarketService()
    _, command = _prepare_resolved_owner_route(service, workspace)

    tracking_store = _CommitTrackingStore(get_store(str(workspace)))
    tracking_store.track_next_save = True
    service._get_store = lambda _workspace: tracking_store
    cognitive_runtime = _CommitAwareCognitiveRuntimeService(tracking_store)
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: cognitive_runtime,
    )

    routed = service.route_owner_rework(command)

    assert routed.ok is True
    assert routed.reason is OwnerReworkRouteReasonV1.ROUTED
    assert routed.owner_reopened is True
    assert routed.dependency_added is True
    assert len(cognitive_runtime.commands) == 1
    assert cognitive_runtime.commands[0].payload["event_type"] == "owner_rework_routed"
    assert cognitive_runtime.closed is True

    items = get_store(str(workspace)).load_items()
    owner = items["owner"]
    requester = items["requester"]
    assert owner.status == "pending_exec"
    assert owner.metadata["reopen_count"] == 1
    assert requester.status == "pending_exec"
    assert requester.lease_token == ""
    assert requester.depends_on == ["owner"]
    assert "owner_rework_dependencies" not in requester.metadata
    handoff = OwnerReworkHandoffV1.from_record(
        requester.metadata["owner_rework_handoffs"][command.handoff_id]
    )
    assert handoff.owner_task_id == owner.task_id
    assert handoff.requester_task_id == requester.task_id
    assert handoff.owner_reopened is True

    owner_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-scan",
            worker_role="director",
        )
    )
    assert owner_claim.ok is True
    assert owner_claim.task_id == "owner"

    service.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="owner",
            lease_token=owner_claim.lease_token,
            summary="owner rework resolved",
        )
    )
    requester_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-scan",
            worker_role="director",
        )
    )
    assert requester_claim.ok is True
    assert requester_claim.task_id == "requester"

    items_before_retry = get_store(str(workspace)).load_items()
    versions_before_retry = (
        items_before_retry["owner"].version,
        items_before_retry["requester"].version,
    )
    repeated = service.route_owner_rework(command)
    items_after_retry = get_store(str(workspace)).load_items()
    assert repeated.ok is True
    assert repeated.reason is OwnerReworkRouteReasonV1.ALREADY_ROUTED
    assert repeated.idempotent is True
    assert repeated.dependency_added is False
    assert len(cognitive_runtime.commands) == 4
    assert (items_after_retry["owner"].version, items_after_retry["requester"].version) == versions_before_retry


def test_owner_rework_stale_cas_never_records_success_lifecycle_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = TaskMarketService()
    _publish(service, workspace, "owner")
    _publish(service, workspace, "requester")
    requester_lease_token = _claim(service, workspace, "requester")
    command = _route_command(workspace, requester_lease_token)

    stale_store = _StaleWriteStore(get_store(str(workspace)))
    stale_store.track_next_save = True
    service._get_store = lambda _workspace: stale_store
    cognitive_runtime = _CommitAwareCognitiveRuntimeService(stale_store)
    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.get_cognitive_runtime_public_service",
        lambda: cognitive_runtime,
    )

    result = service.route_owner_rework(command)

    assert result.ok is False
    assert result.reason is OwnerReworkRouteReasonV1.STALE_WRITE_CONFLICT
    assert cognitive_runtime.commands == []
    persisted_requester = get_store(str(workspace)).load_items()["requester"]
    assert persisted_requester.status == "in_execution"
    assert persisted_requester.lease_token == requester_lease_token


def test_post_commit_projection_failure_does_not_negate_atomic_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transactional outbox remains authoritative after a projection failure."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = TaskMarketService()
    _, command = _prepare_resolved_owner_route(service, workspace)

    def fail_projection(**_kwargs: Any) -> dict[str, Any]:
        raise TaskMarketError(
            "cognitive projection unavailable",
            code="cognitive_runtime_receipt_failed",
        )

    monkeypatch.setattr(service, "_record_cognitive_runtime_lifecycle_receipt", fail_projection)

    result = service.route_owner_rework(command)

    assert result.ok is True
    assert result.reason is OwnerReworkRouteReasonV1.ROUTED
    assert result.post_commit_evidence == {
        "source": "runtime.task_market.transactional_outbox",
        "authoritative_commit_recorded": True,
        "cognitive_runtime_projection_recorded": False,
        "error_code": "cognitive_runtime_receipt_failed",
        "error_message": "cognitive projection unavailable",
    }
    persisted = get_store(str(workspace)).load_items()
    assert persisted["owner"].status == "pending_exec"
    assert persisted["requester"].status == "pending_exec"


def test_malformed_owner_handoff_blocks_claim_readiness() -> None:
    """Malformed dedicated handoff state cannot bypass the resolved-only gate."""

    service = TaskMarketService()
    owner_record = {
        "schema_version": "task-market.owner-rework-route/1",
        "handoff_id": "handoff-corrupt",
        "owner_task_id": "owner",
        "requester_task_id": "requester",
        "owner_previous_status": "resolved",
        "requester_previous_status": "in_execution",
        "owner_reopened": True,
        "dependency_mode": "resolved_only",
        "failure_metadata": 7,
        "evidence_metadata": {"source": "test"},
        "metadata": {},
        "routed_at": "2026-07-11T00:00:00+00:00",
    }
    requester = TaskWorkItemRecord(
        task_id="requester",
        trace_id="trace-requester",
        run_id="run-owner-rework",
        workspace="/workspace",
        stage="pending_exec",
        status="pending_exec",
        priority="medium",
        is_leaf=True,
        depends_on=["owner"],
        metadata={OWNER_REWORK_HANDOFFS_METADATA_KEY: {"handoff-corrupt": owner_record}},
        payload={},
    )
    owner = TaskWorkItemRecord(
        task_id="owner",
        trace_id="trace-owner",
        run_id="run-owner-rework",
        workspace="/workspace",
        stage="pending_qa",
        status="pending_qa",
        priority="medium",
        payload={},
    )

    assert service._exec_claim_ready(requester, {"owner": owner, "requester": requester}) is False


@pytest.mark.parametrize(
    ("owner_status", "owner_depends_on", "owner_metadata", "lease_token", "expected_reason"),
    [
        ("pending_exec", (), {}, "wrong-lease", OwnerReworkRouteReasonV1.REQUESTER_LEASE_MISMATCH),
        ("rejected", (), {}, "valid", OwnerReworkRouteReasonV1.OWNER_TERMINAL_UNRECOVERABLE),
        ("resolved", (), {"reopen_count": 1}, "valid", OwnerReworkRouteReasonV1.OWNER_REOPEN_BUDGET_EXCEEDED),
        ("pending_exec", ("requester",), {}, "valid", OwnerReworkRouteReasonV1.DEPENDENCY_CYCLE),
    ],
)
def test_owner_rework_fail_closed_for_lease_terminal_budget_and_cycle(
    tmp_path: Path,
    owner_status: str,
    owner_depends_on: tuple[str, ...],
    owner_metadata: dict[str, Any],
    lease_token: str,
    expected_reason: OwnerReworkRouteReasonV1,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = TaskMarketService()
    _publish(service, workspace, "owner")
    _publish(service, workspace, "requester")
    requester_lease_token = _claim(service, workspace, "requester")
    _set_item_state(
        workspace,
        "owner",
        status=owner_status,
        depends_on=list(owner_depends_on),
        metadata=owner_metadata,
    )
    command = _route_command(
        workspace,
        requester_lease_token if lease_token == "valid" else lease_token,
    )

    result = service.route_owner_rework(command)

    assert result.ok is False
    assert result.reason is expected_reason
    items = get_store(str(workspace)).load_items()
    assert items["requester"].status == "in_execution"
    assert items["requester"].lease_token == requester_lease_token
