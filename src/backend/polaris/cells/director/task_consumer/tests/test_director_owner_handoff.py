"""Owner-handoff routing tests for the Director Task Market consumer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import polaris.cells.director.task_consumer.internal.director_consumer as director_consumer_module
import pytest
from polaris.cells.director.task_consumer.internal.director_consumer import DirectorExecutionConsumer
from polaris.cells.runtime.task_market.public.contracts import (
    ClaimTaskWorkItemCommandV1,
    OwnerReworkHandoffV1,
    OwnerReworkRouteReasonV1,
    OwnerReworkRouteResultV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService
from polaris.cells.runtime.task_runtime.public.contracts import (
    OwnerReworkExecutionPreparationResultV1,
)


def _claim(task_id: str = "requester-task", lease_token: str = "requester-lease") -> SimpleNamespace:
    return SimpleNamespace(task_id=task_id, lease_token=lease_token, payload={"blueprint_id": "bp-owner-handoff"})


def _handoff_request(owner_task_id: str = "owner-task", target_file: str = "src/owned.py") -> dict[str, Any]:
    return {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": target_file,
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_task_identifier_tokens": [owner_task_id],
    }


def _failure(*requests: dict[str, Any]) -> Any:
    return director_consumer_module._OwnerHandoffFailure(
        scope_payload={"ownership_handoff_requests": list(requests)},
        failure_class="scope_authority_owner_handoff_required",
        responsible_layer="scope_authority",
        failure_evidence=(
            {
                "code": "outside_declared_scope",
                "target_file": "src/owned.py",
            },
        ),
    )


def _route_result(
    *,
    ok: bool,
    reason: OwnerReworkRouteReasonV1,
    owner_task_id: str = "owner-task",
    requester_task_id: str = "requester-task",
    idempotent: bool = False,
) -> OwnerReworkRouteResultV1:
    return OwnerReworkRouteResultV1(
        ok=ok,
        reason=reason,
        handoff_id="owner-handoff-result",
        owner_task_id=owner_task_id,
        requester_task_id=requester_task_id,
        owner_status="pending_exec",
        requester_status="pending_exec",
        owner_version=4,
        requester_version=7,
        dependency_added=not idempotent,
        idempotent=idempotent,
    )


def _owner_rework_claim_evidence(
    *,
    task_role: str,
    worker_id: str,
    lease_token: str,
) -> tuple[SimpleNamespace, tuple[dict[str, Any], dict[str, Any]]]:
    """Return a public TaskMarket claim plus its two matching handoff rows."""

    handoff = OwnerReworkHandoffV1(
        schema_version="task-market.owner-rework-route/1",
        handoff_id="owner-rework-execution-handoff",
        owner_task_id="owner-task",
        requester_task_id="requester-task",
        owner_previous_status="resolved",
        requester_previous_status="in_execution",
        owner_reopened=True,
        dependency_mode="resolved_only",
        failure_metadata={"error_code": "SCOPE_CONFLICT"},
        evidence_metadata={"source": "director-owner-handoff-test"},
        metadata={"test": True},
        routed_at="2026-07-11T00:00:00+00:00",
    )
    task_id = "owner-task" if task_role == "owner" else "requester-task"
    counterparty_task_id = "requester-task" if task_role == "owner" else "owner-task"
    metadata = {"owner_rework_handoffs": {handoff.handoff_id: handoff.to_record()}}
    current = {
        "task_id": task_id,
        "status": "in_execution",
        "lease_token": lease_token,
        "claimed_by": worker_id,
        "metadata": metadata,
    }
    counterparty = {
        "task_id": counterparty_task_id,
        "status": "resolved" if task_role == "requester" else "pending_exec",
        "metadata": metadata,
    }
    claim = SimpleNamespace(
        task_id=task_id,
        lease_token=lease_token,
        status="in_execution",
        claimed_by=worker_id,
        payload={"blueprint_id": "bp-owner-handoff", "allow_no_changes": True},
    )
    return claim, (current, counterparty)


@pytest.fixture
def consumer_and_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> tuple[DirectorExecutionConsumer, MagicMock]:
    service = MagicMock()
    monkeypatch.setattr(director_consumer_module, "get_task_market_service", lambda: service)
    monkeypatch.setattr(
        director_consumer_module,
        "_validated_blueprint_handoff",
        lambda _workspace, _task_id, _payload: (True, "bp-owner-handoff", "ok", {}),
    )
    return DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="director-owner-handoff"), service


def _raise_owner_handoff(failure: Any) -> Any:
    return director_consumer_module._OwnerHandoffRoutingRequiredError(
        "adapter execution failed",
        failure=failure,
    )


def test_owner_handoff_detection_does_not_infer_from_adapter_prose() -> None:
    adapter_result = {
        "success": False,
        "error": "Please hand this task back to the owner task.",
        "metadata": {"message": "owner handoff required"},
    }

    assert director_consumer_module._owner_handoff_failure_from_adapter_failure(adapter_result) is None


def test_owner_handoff_routes_matched_owner_with_requester_lease(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))
    service.route_owner_rework.return_value = _route_result(
        ok=True,
        reason=OwnerReworkRouteReasonV1.ROUTED,
    )

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(_failure(_handoff_request()))):
        result = consumer._process_claim(_claim())

    assert result == {
        "task_id": "requester-task",
        "ok": True,
        "reason": "owner_rework_routed",
        "status": "pending_exec",
        "owner_task_id": "owner-task",
        "handoff_id": "owner-handoff-result",
        "idempotent": False,
    }
    service.fail_task_stage.assert_not_called()
    service.acknowledge_task_stage.assert_not_called()
    query = service.query_status.call_args.args[0]
    assert query.workspace == consumer._workspace
    assert query.include_payload is False
    route_command = service.route_owner_rework.call_args.args[0]
    assert route_command.requester_task_id == "requester-task"
    assert route_command.requester_lease_token == "requester-lease"
    assert route_command.owner_task_id == "owner-task"
    assert route_command.handoff_id.startswith("owner-handoff-")
    assert route_command.failure_metadata["failure_class"] == "scope_authority_owner_handoff_required"
    assert route_command.evidence_metadata["scope_authority"]["ownership_handoff_requests"] == [_handoff_request()]


def test_owner_handoff_routes_through_real_task_market_public_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    workspace = tmp_path / "owner-handoff-workspace"
    workspace.mkdir()
    service = TaskMarketService()
    monkeypatch.setattr(director_consumer_module, "get_task_market_service", lambda: service)
    monkeypatch.setattr(
        director_consumer_module,
        "_validated_blueprint_handoff",
        lambda _workspace, _task_id, _payload: (True, "bp-owner-handoff", "ok", {}),
    )
    for task_id in ("owner-task", "requester-task"):
        published = service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id=f"trace-{task_id}",
                run_id="run-owner-handoff",
                task_id=task_id,
                stage="pending_exec",
                source_role="chief_engineer",
                payload={"blueprint_id": "bp-owner-handoff", "title": task_id},
            )
        )
        assert published.ok is True
    claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            task_id="requester-task",
            worker_id="director-owner-handoff",
            worker_role="director",
        )
    )
    assert claim.ok is True

    def raise_structured_owner_handoff(
        _task_id: str,
        _payload: dict[str, Any],
        _lease_token: str,
    ) -> dict[str, Any]:
        raise _raise_owner_handoff(_failure(_handoff_request()))

    consumer = DirectorExecutionConsumer(
        workspace=str(workspace),
        worker_id="director-owner-handoff",
        task_executor=raise_structured_owner_handoff,
    )

    result = consumer._process_claim(claim)

    assert result["ok"] is True
    assert result["reason"] == "owner_rework_routed"
    rows = service.query_status(
        QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=False, limit=10)
    ).items
    items = {str(row["task_id"]): row for row in rows}
    assert items["requester-task"]["status"] == "pending_exec"
    assert items["requester-task"]["lease_token"] == ""
    assert items["requester-task"]["depends_on"] == ["owner-task"]
    assert items["owner-task"]["status"] == "pending_exec"


def test_owner_handoff_treats_already_routed_result_as_idempotent_success(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))
    service.route_owner_rework.return_value = _route_result(
        ok=True,
        reason=OwnerReworkRouteReasonV1.ALREADY_ROUTED,
        idempotent=True,
    )

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(_failure(_handoff_request()))):
        result = consumer._process_claim(_claim())

    assert result["ok"] is True
    assert result["reason"] == "owner_rework_already_routed"
    assert result["idempotent"] is True
    service.fail_task_stage.assert_not_called()
    service.acknowledge_task_stage.assert_not_called()


def test_owner_handoff_route_rejection_preserves_typed_failure_evidence(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    failure = _failure(_handoff_request())
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))
    service.route_owner_rework.return_value = _route_result(
        ok=False,
        reason=OwnerReworkRouteReasonV1.HANDOFF_STATE_CONFLICT,
    )

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(failure)):
        result = consumer._process_claim(_claim())

    assert result["reason"] == "owner_handoff_route_rejected"
    assert result["error_code"] == "OWNER_HANDOFF_ROUTE_REJECTED"
    assert result["route_reason"] == "owner_rework_handoff_state_conflict"
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.requeue_stage == "pending_exec"
    assert fail_command.metadata["failure_class"] == "scope_authority_owner_handoff_required"
    evidence = fail_command.metadata["owner_handoff_evidence"]
    assert evidence["scope_authority"] == failure.scope_payload
    assert evidence["failure_evidence"] == [dict(failure.failure_evidence[0])]
    assert evidence["owner_rework_route_result"]["reason"] == "owner_rework_handoff_state_conflict"
    service.acknowledge_task_stage.assert_not_called()


def test_owner_handoff_lease_mismatch_is_fenced_without_generic_failure(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))
    service.route_owner_rework.return_value = _route_result(
        ok=False,
        reason=OwnerReworkRouteReasonV1.REQUESTER_LEASE_MISMATCH,
    )

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(_failure(_handoff_request()))):
        result = consumer._process_claim(_claim())

    assert result["reason"] == "owner_handoff_route_fenced"
    assert result["route_reason"] == "requester_lease_mismatch"
    assert result["failure_class"] == "scope_authority_owner_handoff_required"
    service.fail_task_stage.assert_not_called()
    service.acknowledge_task_stage.assert_not_called()


def test_owner_handoff_unmatched_owner_returns_to_chief_engineer_with_evidence(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    failure = _failure(_handoff_request())
    service.query_status.return_value = SimpleNamespace(items=())

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(failure)):
        result = consumer._process_claim(_claim())

    assert result["reason"] == "owner_handoff_unresolved"
    assert result["error_code"] == "OWNER_HANDOFF_UNRESOLVED"
    service.route_owner_rework.assert_not_called()
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.requeue_stage == "pending_design"
    evidence = fail_command.metadata["owner_handoff_evidence"]
    assert evidence["owner_handoff_routing"]["unmatched_owner_handoff_count"] == 1
    assert evidence["ownership_handoff_request"] == _handoff_request()


def test_owner_handoff_multiple_owners_fails_closed_before_route(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    owner_a = _handoff_request("owner-a", "src/a.py")
    owner_b = _handoff_request("owner-b", "src/b.py")
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-a"}, {"task_id": "owner-b"}))

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(_failure(owner_a, owner_b))):
        result = consumer._process_claim(_claim())

    assert result["reason"] == "owner_handoff_ambiguous"
    assert result["error_code"] == "OWNER_HANDOFF_AMBIGUOUS"
    service.route_owner_rework.assert_not_called()
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.requeue_stage == "pending_design"
    assert fail_command.metadata["owner_handoff_evidence"]["owner_handoff_routing"]["matched_owner_handoff_count"] == 2


def test_owner_handoff_route_exception_is_observable_and_requeued(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))
    service.route_owner_rework.side_effect = OSError("task market unavailable")

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(_failure(_handoff_request()))):
        result = consumer._process_claim(_claim())

    assert result["reason"] == "owner_handoff_route_error"
    assert result["error_code"] == "OWNER_HANDOFF_ROUTE_ERROR"
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.requeue_stage == "pending_exec"
    route_error = fail_command.metadata["owner_handoff_evidence"]["owner_rework_route_error"]
    assert route_error == {"type": "OSError", "message": "task market unavailable"}


@pytest.mark.parametrize("task_role", ("owner", "requester"))
def test_owner_rework_preparation_precedes_director_adapter_execution(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    task_role: str,
) -> None:
    """A claimed owner or released requester must prepare before adapter work."""

    consumer, service = consumer_and_service
    claim, rows = _owner_rework_claim_evidence(
        task_role=task_role,
        worker_id="director-owner-handoff",
        lease_token="owner-rework-lease",
    )
    service.query_status.return_value = SimpleNamespace(items=rows)
    call_order: list[str] = []

    def prepare(command: Any) -> OwnerReworkExecutionPreparationResultV1:
        call_order.append("prepare")
        assert command.authorization.task_role == task_role
        assert command.authorization.claimed_item["lease_token"] == "owner-rework-lease"
        return OwnerReworkExecutionPreparationResultV1(
            ok=True,
            code="owner_rework_execution_prepared",
            reason="prepared",
            task_id=claim.task_id,
            handoff_id=str(command.authorization.handoff["handoff_id"]),
            task_role=task_role,
            runtime_task_id="42",
        )

    def execute(_task_id: str, _payload: dict[str, Any], _lease_token: str) -> dict[str, Any]:
        call_order.append("adapter")
        return {"changed_files": [], "duration": 0, "side_effects": []}

    consumer._task_executor = execute
    monkeypatch.setattr(director_consumer_module, "prepare_owner_rework_execution", prepare)

    consumer._process_claim(claim)

    assert call_order == ["prepare", "adapter"]
    service.fail_task_stage.assert_not_called()


def test_owner_rework_preparation_rejection_does_not_execute_adapter(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TaskRuntime preparation failures are requeued before adapter invocation."""

    consumer, service = consumer_and_service
    claim, rows = _owner_rework_claim_evidence(
        task_role="owner",
        worker_id="director-owner-handoff",
        lease_token="owner-rework-lease",
    )
    service.query_status.return_value = SimpleNamespace(items=rows)
    adapter = MagicMock(return_value={"changed_files": ["must-not-run.py"]})
    consumer._task_executor = adapter
    monkeypatch.setattr(
        director_consumer_module,
        "prepare_owner_rework_execution",
        lambda _command: OwnerReworkExecutionPreparationResultV1(
            ok=False,
            code="runtime_task_not_found",
            reason="TaskRuntime has no matching row",
            task_id=claim.task_id,
            handoff_id="owner-rework-execution-handoff",
            task_role="owner",
        ),
    )

    result = consumer._process_claim(claim)

    assert result["ok"] is False
    assert result["reason"] == "owner_rework_execution_preparation_rejected"
    adapter.assert_not_called()
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.error_code == "OWNER_REWORK_EXECUTION_PREPARATION_REJECTED"
    assert fail_command.requeue_stage == "pending_exec"
