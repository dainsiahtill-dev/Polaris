"""Malformed latest recovery/removal evidence must not authorize a new owner."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.tests.test_factory_abort_checkpoint_recovery import _retry
from polaris.cells.factory.pipeline.tests.test_factory_recovery_task_identity import _failed_ce_owner
from polaris.cells.runtime.task_runtime.public import TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests.test_historical_execution_effect_closure import _history
from polaris.kernelone.events.sourcing.models import EventEnvelope


@pytest.mark.parametrize(
    "defect",
    [
        "alias",
        "foreign_factory",
        "reference",
        "projection",
        "session",
        "attempt",
        "transition",
        "late_helper",
        "new_owner",
    ],
)
def test_invalid_failed_reset_checkpoint_never_creates_replacement(tmp_path: Path, defect: str) -> None:
    service, owner, task = _failed_ce_owner(tmp_path)
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    if defect == "new_owner":
        service.create_task_row(subject="Different physical owner", metadata=deepcopy(owner["metadata"]))
        projection = owner["metadata"]["task_completion_projection"]
        before = deepcopy(service.list_observable_task_rows())
        with pytest.raises(RuntimeError):
            service.prepare_factory_task_recovery(
                external_task_id="WORK-A",
                factory_run_id="factory-one",
                project_contract_hash=projection["project_contract_hash"],
                projection_hash=projection["projection_hash"],
            )
        assert service.list_observable_task_rows() == before
        return
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    removed = events[-1]
    snapshot = removed["payload"]["task_row_snapshot"]
    if defect == "late_helper":
        assert service._append_execution_event("heartbeat_renewed", task_row=deepcopy(snapshot), session=None).get(
            "fact_event_id"
        )
    else:
        if defect == "alias":
            snapshot["metadata"]["source_task_id"] = "foreign"
        elif defect == "foreign_factory":
            removed["payload"]["factory_run_id"] = "foreign"
        elif defect == "reference":
            snapshot["metadata"]["task_runtime_settlement"]["fact_event_id"] = "foreign"
        elif defect == "session":
            snapshot["metadata"]["runtime_execution"]["session_id"] = "foreign"
        elif defect == "attempt":
            removed["payload"]["attempt"] += 1
        elif defect == "transition":
            removed["payload"]["transition_id"] = "foreign"
        else:
            snapshot["metadata"]["task_completion_projection"]["project_contract_hash"] = "f" * 64
        removed["integrity_digest"] = EventEnvelope.integrity_digest_for_record(removed)
        path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        _retry(tmp_path, task, owner)
    assert path.read_bytes() == before
    assert not service.list_observable_task_rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign_successor", "missing_reference", "active_parent_file"])
async def test_old_closure_needs_authentic_quiescent_reset_successor(tmp_path: Path, defect: str) -> None:
    attempts, _rows = await _history(tmp_path)
    assert TaskRuntimeService(str(tmp_path)).reset_records(keep_plan=True)["ok"]
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    if defect == "active_parent_file":
        from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import _registry_stream_token
        from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

        token = _registry_stream_token(DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempts[-1]))
        (tmp_path / f".polaris/runtime/events/{token}.jsonl").write_text("untrusted active parent\n", encoding="utf-8")
    else:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        removed = events[-1]
        metadata = removed["payload"]["task_row_snapshot"]["metadata"]
        if defect == "foreign_successor":
            metadata["runtime_execution"]["external_task_id"] = "foreign"
        else:
            metadata["task_runtime_settlement"]["fact_event_id"] = "foreign"
        removed["integrity_digest"] = EventEnvelope.integrity_digest_for_record(removed)
        path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempts[0]))
    assert observed.status == "unknown", observed
    assert observed.execution_authority is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
