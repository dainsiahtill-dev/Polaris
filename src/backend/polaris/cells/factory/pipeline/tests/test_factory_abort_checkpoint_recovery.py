"""A failed Factory drain must not revoke an immutable CE owner checkpoint."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public.contracts import FactStreamError
from polaris.cells.factory.pipeline.internal.factory_stage_executor._executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_factory_recovery_task_identity import _failed_ce_owner
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import _settle_claimed_execution_attempt
from polaris.kernelone.events.sourcing.models import EventEnvelope


def _cancelled_owner(
    tmp_path: Path, *, removed: bool = False, reason: str = "factory_failed"
) -> tuple[TaskRuntimeService, dict[str, Any], dict[str, Any], dict[str, Any]]:
    service, owner, task = _failed_ce_owner(tmp_path)
    assert service.reopen_task_row(owner["id"], reason="authorized local rework") is not None
    claim = service.claim_execution(
        owner["id"], worker_id="director", role_id="director", run_id="director-preflight", external_task_id="WORK-A"
    )
    assert claim["success"]
    assert _settle_claimed_execution_attempt(
        service, claim, outcome="completed", summary="existing scope preflight; no new effects"
    )["success"]
    assert service.reopen_task_row(owner["id"], reason="authorized failed verifier rework") is not None
    assert (
        service.cancel_task_row_for_factory_abort(
            owner["id"], factory_run_id="factory-one", reason=reason, source="factory_terminal_drain"
        )["status"]
        == "cancelled"
    )
    assert service.get_task(owner["id"])["status"] == "cancelled"
    if removed:
        assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    return service, owner, task, claim


def _retry(tmp_path: Path, task: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
    executor = OrchestrationStageExecutor(tmp_path)
    executor._write_json_artifact(
        "runtime/state/blueprints/factory-one.review.json",
        {
            "factory_run_id": "factory-one",
            "blueprints": [{"task_id": "WORK-A", "blueprint_id": owner["metadata"]["blueprint_id"]}],
        },
    )
    return executor._materialize_pm_plan_taskboard(
        [task],
        run_id="factory-one",
        source_stage="director_dispatch",
        run_metadata={"retry_from_status": "failed", "retry_execution_stage": "director_dispatch"},
    )


@pytest.mark.parametrize("removed", [False, True])
def test_failed_factory_drain_recovers_exact_completed_session_owner(tmp_path: Path, removed: bool) -> None:
    service, owner, task, old_claim = _cancelled_owner(tmp_path, removed=removed)
    original_events = deepcopy(service._query_execution_fact_events(limit=500).events)
    result = _retry(tmp_path, task, owner)
    assert result["binding_failures"] == []
    restored = service.resolve_factory_task_row(external_task_id="WORK-A", factory_run_id="factory-one")
    assert restored is not None and restored["id"] == owner["id"], "retry cloned the drained logical owner"
    assert restored["status"] == "pending"
    fresh = service.claim_execution(
        restored["id"], worker_id="director", role_id="director", run_id="director-recovery", external_task_id="WORK-A"
    )
    assert fresh["success"]
    assert fresh["execution_attempt"]["session_id"] != old_claim["execution_attempt"]["session_id"]
    assert fresh["execution_attempt"]["attempt"] > old_claim["execution_attempt"]["attempt"]
    assert not _settle_claimed_execution_attempt(service, old_claim, outcome="failed", summary="stale")["success"]
    assert service._query_execution_fact_events(limit=500).events[: len(original_events)] == original_events


@pytest.mark.parametrize("removed", [False, True])
@pytest.mark.parametrize("reason", ["user_cancelled", "factory_cancelled"])
def test_user_cancellation_does_not_become_failure_recovery(tmp_path: Path, removed: bool, reason: str) -> None:
    service, owner, task, _ = _cancelled_owner(tmp_path, removed=removed, reason=reason)
    before = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert service._query_execution_fact_events(limit=500).events == before


@pytest.mark.parametrize("field", ["project_contract_hash", "projection_hash"])
def test_cancelled_owner_requires_original_ce_hashes(tmp_path: Path, field: str) -> None:
    service, owner, task, _ = _cancelled_owner(tmp_path)
    projection = dict(owner["metadata"]["task_completion_projection"])
    projection[field] = "f" * 64
    service.update_task_row(owner["id"], metadata={"task_completion_projection": projection})
    before = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert service._query_execution_fact_events(limit=500).events == before


def test_active_successor_owner_cannot_be_recovered(tmp_path: Path) -> None:
    service, owner, task, _ = _cancelled_owner(tmp_path)
    assert service.reopen_task_row(owner["id"], reason="separately authorized successor") is not None
    assert service.claim_execution(
        owner["id"], worker_id="successor", role_id="director", run_id="director-successor", external_task_id="WORK-A"
    )["success"]
    before = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert service._query_execution_fact_events(limit=500).events == before


def test_removed_cancelled_owner_rejects_foreign_factory(tmp_path: Path) -> None:
    service, owner, _task, _ = _cancelled_owner(tmp_path, removed=True)
    projection = owner["metadata"]["task_completion_projection"]
    before = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError):
        service.prepare_factory_task_recovery(
            external_task_id="WORK-A",
            factory_run_id="factory-foreign",
            project_contract_hash=projection["project_contract_hash"],
            projection_hash=projection["projection_hash"],
        )
    assert service._query_execution_fact_events(limit=500).events == before


@pytest.mark.parametrize("field", ["scope", "task_completion_projection"])
def test_cancelled_owner_rejects_uncommitted_physical_authority_drift(tmp_path: Path, field: str) -> None:
    service, owner, task, _ = _cancelled_owner(tmp_path)
    path = tmp_path / ".polaris/runtime/tasks" / f"task_{owner['id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metadata"][field] = {"foreign": "unauthorized"}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    events = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert path.read_bytes() == before
    assert service._query_execution_fact_events(limit=500).events == events


def test_unsigned_cancellation_is_not_a_recovery_authority(tmp_path: Path) -> None:
    _service, owner, task, _ = _cancelled_owner(tmp_path)
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[-1].pop("integrity_digest")
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(FactStreamError):
        _retry(tmp_path, task, owner)
    assert path.read_bytes() == before


@pytest.mark.parametrize("removed", [False, True])
def test_two_recovery_owners_cannot_reopen_the_same_cancelled_checkpoint(tmp_path: Path, removed: bool) -> None:
    _service, owner, _task, _ = _cancelled_owner(tmp_path, removed=removed)
    projection = owner["metadata"]["task_completion_projection"]
    peers = [TaskRuntimeService(str(tmp_path)), TaskRuntimeService(str(tmp_path))]

    def recover(service: TaskRuntimeService) -> int | None:
        try:
            return service.prepare_factory_task_recovery(
                external_task_id="WORK-A",
                factory_run_id="factory-one",
                project_contract_hash=projection["project_contract_hash"],
                projection_hash=projection["projection_hash"],
            )["id"]
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, peers))
    assert results.count(owner["id"]) == 1
    assert results.count(None) == 1
    assert len(TaskRuntimeService(str(tmp_path)).list_observable_task_rows()) == 1


@pytest.mark.parametrize("removed", [False, True])
def test_unenrolled_present_effect_registry_does_not_prove_absence(tmp_path: Path, removed: bool) -> None:
    from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import _registry_stream_token
    from polaris.cells.runtime.task_runtime.public.contracts import (
        DirectedEffectParentRegistryIdentityV1,
        TaskRuntimeExecutionAttemptIdentityV1,
    )

    service, owner, task, claim = _cancelled_owner(tmp_path, removed=removed)
    identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
        TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    )
    path = tmp_path / ".polaris/runtime/events" / f"{_registry_stream_token(identity)}.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    before = deepcopy(service._query_execution_fact_events(limit=500).events)
    with pytest.raises(RuntimeError, match="terminal_effects_unsettled"):
        _retry(tmp_path, task, owner)
    assert service._query_execution_fact_events(limit=500).events == before


@pytest.mark.parametrize("field", ["status", "transition_id"])
def test_signed_semantically_inconsistent_owner_terminal_fact_is_rejected(tmp_path: Path, field: str) -> None:
    _service, owner, task, claim = _cancelled_owner(tmp_path, removed=True)
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    terminal = next(
        event
        for event in events
        if event["event_type"] == "completed"
        and event["payload"].get("session_id") == claim["execution_attempt"]["session_id"]
    )
    terminal["payload"][field] = "foreign-terminal-value"
    terminal["integrity_digest"] = EventEnvelope.integrity_digest_for_record(terminal)
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="terminal_binding_invalid"):
        _retry(tmp_path, task, owner)
    assert path.read_bytes() == before
