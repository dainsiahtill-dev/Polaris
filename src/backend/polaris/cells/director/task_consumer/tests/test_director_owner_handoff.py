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
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


def _claim(task_id: str = "requester-task", lease_token: str = "requester-lease") -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        lease_token=lease_token,
        payload={
            "blueprint_id": "bp-owner-handoff",
            "completion_contract_hash": "completion-contract-hash",
            "run_id": "run-owner-handoff",
            "trace_id": "trace-requester-task",
        },
    )


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


def test_legacy_owner_rework_preparation_bypass_is_removed() -> None:
    assert not hasattr(director_consumer_module, "prepare_owner_rework_execution")
    assert not hasattr(DirectorExecutionConsumer, "_owner_rework_preparation_plan")


def test_owner_handoff_detection_does_not_infer_from_adapter_prose() -> None:
    adapter_result = {
        "success": False,
        "error": "Please hand this task back to the owner task.",
        "metadata": {"message": "owner handoff required"},
    }

    assert director_consumer_module._owner_handoff_failure_from_adapter_failure(adapter_result) is None


def test_owner_handoff_exact_match_is_isolated_without_cross_task_route(
    consumer_and_service: tuple[DirectorExecutionConsumer, MagicMock],
) -> None:
    consumer, service = consumer_and_service
    failure = _failure(_handoff_request())
    service.query_status.return_value = SimpleNamespace(items=({"task_id": "owner-task"},))

    with patch.object(consumer, "_execute_task", side_effect=_raise_owner_handoff(failure)):
        result = consumer._process_claim(_claim())

    assert result["ok"] is False
    assert result["reason"] == "owner_handoff_cross_task_repair_forbidden"
    assert result["error_code"] == "OWNER_HANDOFF_CROSS_TASK_REPAIR_FORBIDDEN"
    service.route_owner_rework.assert_not_called()
    service.acknowledge_task_stage.assert_not_called()
    fail_command = service.fail_task_stage.call_args.args[0]
    assert fail_command.requeue_stage is None
    assert fail_command.failure_disposition == "isolated_contract_blocker"
    blocker = fail_command.metadata["structured_blocker"]
    assert blocker["blocker_kind"] == "contract_or_authority_contradiction"
    assert blocker["automatic_upstream_replan"] is False
    assert blocker["automatic_escalation"] is False
    assert blocker["identity_complete"] is True
    evidence = fail_command.metadata["owner_handoff_evidence"]
    assert evidence["scope_authority"] == failure.scope_payload
    assert evidence["owner_handoff_routing"]["selected_owner_task_id"] == "owner-task"


def test_owner_handoff_real_task_market_never_reopens_owner_or_adds_dependency(
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

    assert result["reason"] == "owner_handoff_cross_task_repair_forbidden"
    rows = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True, limit=10)).items
    items = {str(row["task_id"]): row for row in rows}
    assert items["owner-task"]["status"] == "pending_exec"
    assert items["requester-task"]["status"] == "rejected"
    assert items["requester-task"]["depends_on"] == []
    assert items["requester-task"]["metadata"]["dependency_terminal_cascade_suppressed"] is True


def test_owner_handoff_unmatched_owner_isolated_without_upstream_replan(
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
    assert fail_command.requeue_stage is None
    assert fail_command.failure_disposition == "isolated_contract_blocker"
    blocker = fail_command.metadata["structured_blocker"]
    assert blocker["blocker_kind"] == "contract_or_authority_contradiction"
    assert blocker["automatic_upstream_replan"] is False
    assert blocker["automatic_escalation"] is False
    assert blocker["identity_complete"] is True
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
    assert fail_command.requeue_stage is None
    assert fail_command.failure_disposition == "isolated_contract_blocker"
    blocker = fail_command.metadata["structured_blocker"]
    assert blocker["blocker_kind"] == "contract_or_authority_contradiction"
    assert blocker["automatic_upstream_replan"] is False
    assert blocker["automatic_escalation"] is False
    assert blocker["identity_complete"] is True
    assert fail_command.metadata["owner_handoff_evidence"]["owner_handoff_routing"]["matched_owner_handoff_count"] == 2
