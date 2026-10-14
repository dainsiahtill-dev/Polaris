from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.tests.test_factory_abort_checkpoint_recovery import _retry
from polaris.cells.factory.pipeline.tests.test_factory_recovery_task_identity import _failed_ce_owner
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import _settle_claimed_execution_attempt


@pytest.mark.asyncio
async def test_completed_governed_reset_restores_original_ce_owner_then_claims_real_effect(tmp_path: Path) -> None:
    service, owner, task = _failed_ce_owner(tmp_path)
    service.reopen_task_row(owner["id"], reason="authorized continuation")
    claim = service.claim_execution(
        owner["id"], worker_id="director", role_id="director", run_id="director-completed", external_task_id="WORK-A"
    )
    assert claim["success"]
    assert _settle_claimed_execution_attempt(
        service, claim, outcome="completed", summary="child stage complete; project QA remains"
    )["success"]
    before = deepcopy(service._query_execution_fact_events(limit=1000).events)
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    result = _retry(tmp_path, task, owner)
    assert result["binding_failures"] == []
    restored = service.resolve_factory_task_row(external_task_id="WORK-A", factory_run_id="factory-one")
    assert restored["id"] == owner["id"] == 1 and restored["status"] == "pending"
    assert restored["metadata"]["task_completion_projection"] == owner["metadata"]["task_completion_projection"]
    assert service._query_execution_fact_events(limit=1000).events[: len(before)] == before
    new = service.claim_execution(
        restored["id"], worker_id="director", role_id="director", run_id="director-recovery", external_task_id="WORK-A"
    )
    assert new["success"]
    attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(new["execution_attempt"])
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before\n", encoding="utf-8")
    attempt, rows = await _real_effect(tmp_path, attempt=attempt)
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
    assert rows[0]["effect_receipt_commit"]["event_id"]
    assert attempt.task_id == 1 and attempt.external_task_id == "WORK-A"
    assert _settle_claimed_execution_attempt(
        service, new, outcome="failed", summary="real effect; no projectpass claim"
    )["success"]


@pytest.mark.parametrize(
    "defect", ["reference", "hash", "foreign_reset", "attempt", "session", "new_owner", "active", "unknown_parent"]
)
def test_completed_reset_requires_exact_current_quiescent_owner(tmp_path: Path, defect: str) -> None:
    import json

    from polaris.kernelone.events.sourcing.models import EventEnvelope

    service, owner, task = _failed_ce_owner(tmp_path)
    service.reopen_task_row(owner["id"], reason="authorized continuation")
    claim = service.claim_execution(
        owner["id"], worker_id="director", role_id="director", run_id="director-completed", external_task_id="WORK-A"
    )
    assert claim["success"]
    if defect == "active":
        projection = owner["metadata"]["task_completion_projection"]
        with pytest.raises(RuntimeError):
            service.prepare_factory_task_recovery(
                external_task_id="WORK-A",
                factory_run_id="factory-one",
                project_contract_hash=projection["project_contract_hash"],
                projection_hash=projection["projection_hash"],
            )
        assert service.get_task(owner["id"])["status"] == "in_progress"
        return
    assert _settle_claimed_execution_attempt(service, claim, outcome="completed", summary="child done")["success"]
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    if defect == "new_owner":
        service.create_task_row(subject="foreign new owner", metadata=deepcopy(owner["metadata"]))
        projection = owner["metadata"]["task_completion_projection"]
        with pytest.raises(RuntimeError):
            service.prepare_factory_task_recovery(
                external_task_id="WORK-A",
                factory_run_id="factory-one",
                project_contract_hash=projection["project_contract_hash"],
                projection_hash=projection["projection_hash"],
            )
        return
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    removed = events[-1]
    metadata = removed["payload"]["task_row_snapshot"]["metadata"]
    if defect == "unknown_parent":
        from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import _registry_stream_token
        from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

        identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
        token = _registry_stream_token(DirectedEffectParentRegistryIdentityV1.from_execution_attempt(identity))
        (tmp_path / f".polaris/runtime/events/{token}.jsonl").write_text("unknown parent evidence\n", encoding="utf-8")
    else:
        if defect == "reference":
            metadata["task_runtime_settlement"]["fact_event_id"] = "foreign"
        elif defect == "hash":
            metadata["task_completion_projection"]["project_contract_hash"] = "f" * 64
        elif defect == "foreign_reset":
            removed["payload"]["details"]["reset_factory_run_id"] = "foreign"
        elif defect == "attempt":
            removed["payload"]["attempt"] += 1
        else:
            metadata["runtime_execution"]["session_id"] = "foreign"
        removed["integrity_digest"] = EventEnvelope.integrity_digest_for_record(removed)
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert path.read_bytes() == before
    assert not service.list_observable_task_rows()
