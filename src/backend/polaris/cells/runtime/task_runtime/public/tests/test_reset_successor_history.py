"""Governed reset keeps historical closure and exact current recovery separate."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.tests.test_factory_abort_checkpoint_recovery import _retry
from polaris.cells.factory.pipeline.tests.test_factory_recovery_task_identity import _failed_ce_owner
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1, TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests.test_historical_execution_effect_closure import _history
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import _settle_claimed_execution_attempt


@pytest.mark.asyncio
async def test_governed_reset_successor_keeps_all_three_actual_prior_attempts_closed(tmp_path: Path) -> None:
    attempts, _rows = await _history(tmp_path)
    assert TaskRuntimeService(str(tmp_path)).reset_records(keep_plan=True)["ok"]
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    observed = [
        query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt)) for attempt in attempts
    ]
    assert [row.status for row in observed] == ["closed", "closed", "closed"], observed
    assert [len(row.receipts) for row in observed] == [1, 1, 0]
    assert all(row.execution_authority is False for row in observed)
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_old_auxiliary_heartbeat_cannot_poison_exact_failed_owner_checkpoint(tmp_path: Path) -> None:
    service, owner, task = _failed_ce_owner(tmp_path)
    assert service.reopen_task_row(owner["id"], reason="authorized local rework") is not None
    claim = service.claim_execution(
        owner["id"], worker_id="director", role_id="director", run_id="director-preflight", external_task_id="WORK-A"
    )
    assert claim["success"]
    snapshot = deepcopy(service.get_task(owner["id"]))
    metadata = snapshot["metadata"]
    for name in ("external_task_id", "source_task_id", "pm_task_id"):
        metadata.pop(name, None)
    metadata["task_id"] = "WORK-A"
    auxiliary = service._append_execution_event(
        "heartbeat_renewed", task_row=snapshot, session=service._read_session(owner["id"])
    )
    assert auxiliary.get("fact_event_id")
    assert _settle_claimed_execution_attempt(
        service, claim, outcome="completed", summary="existing scope preflight no effects"
    )["success"]
    assert service.reopen_task_row(owner["id"], reason="authorized failed verifier rework") is not None
    assert (
        service.cancel_task_row_for_factory_abort(
            owner["id"], factory_run_id="factory-one", reason="factory_failed", source="factory_terminal_drain"
        )["status"]
        == "cancelled"
    )
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    history = deepcopy(service._query_execution_fact_events(limit=1000).events)
    result = _retry(tmp_path, task, owner)
    assert result["binding_failures"] == []
    restored = service.resolve_factory_task_row(external_task_id="WORK-A", factory_run_id="factory-one")
    assert restored is not None and restored["id"] == owner["id"] and restored["status"] == "pending"
    assert service._query_execution_fact_events(limit=1000).events[: len(history)] == history


@pytest.mark.asyncio
async def test_governed_failed_terminal_reset_restores_same_ce_owner_without_cancel(tmp_path: Path) -> None:
    service, owner, task = _failed_ce_owner(tmp_path)
    before = deepcopy(service._query_execution_fact_events(limit=1000).events)
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    result = _retry(tmp_path, task, owner)
    assert result["binding_failures"] == []
    restored = service.resolve_factory_task_row(external_task_id="WORK-A", factory_run_id="factory-one")
    assert restored is not None and restored["id"] == owner["id"] and restored["status"] == "pending"
    assert service._query_execution_fact_events(limit=1000).events[: len(before)] == before
    claim = service.claim_execution(
        restored["id"], worker_id="director", role_id="director", run_id="director-recovery", external_task_id="WORK-A"
    )
    assert claim["success"]
    attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before\n", encoding="utf-8")
    attempt, effects = await _real_effect(tmp_path, attempt=attempt)
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
    assert effects[0]["effect_receipt_commit"]["event_id"]
    assert effects[0]["effect_receipt"]["receipt_hash"]
    assert attempt.task_id == owner["id"] and attempt.external_task_id == "WORK-A"
    assert _settle_claimed_execution_attempt(
        service, claim, outcome="failed", summary="physical effect preserved no delivery claim"
    )["success"]
