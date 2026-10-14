"""Canonical reset removals retain strict historical resource closure proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import production
from polaris.cells.runtime.task_runtime.public import TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests.test_historical_execution_effect_closure import _settle


def test_governed_reset_retains_closed_no_effect_owner_observation(tmp_path: Path) -> None:
    attempt = production._setup_attempt(str(tmp_path))
    _settle(tmp_path, attempt)
    runtime = TaskRuntimeService(str(tmp_path))
    reset = runtime.reset_records(keep_plan=True)
    assert reset["ok"] is True, reset
    assert reset["tombstone_count"] == 1
    session_path = tmp_path / ".polaris" / "runtime" / "tasks" / f"task_{attempt.task_id}.session.json"
    assert not session_path.exists()
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "closed", observed
    assert observed.receipts == ()
    assert observed.execution_authority is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_missing_session_without_reset_fact_stays_unknown(tmp_path: Path) -> None:
    attempt = production._setup_attempt(str(tmp_path))
    _settle(tmp_path, attempt)
    session_path = tmp_path / ".polaris" / "runtime" / "tasks" / f"task_{attempt.task_id}.session.json"
    session_path.unlink()
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "unknown"
    assert observed.execution_authority is False


@pytest.mark.parametrize("defect", ["reference", "attempt", "transition", "session", "successor"])
def test_removed_session_requires_exact_latest_owner_witness(tmp_path: Path, defect: str) -> None:
    attempt = production._setup_attempt(str(tmp_path))
    _settle(tmp_path, attempt)
    reset = TaskRuntimeService(str(tmp_path)).reset_records(keep_plan=True)
    assert reset["ok"] is True
    # Damage only this isolated private fixture; recompute its envelope digest
    # to exercise semantic owner checks rather than byte-integrity rejection.
    path = tmp_path / ".polaris" / "runtime" / "events" / "task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    removal = next(event for event in events if event["event_type"] == "runtime_reset_removed")
    if defect == "reference":
        removal["payload"]["task_row_snapshot"]["metadata"]["task_runtime_settlement"]["fact_event_id"] = "foreign"
    elif defect == "attempt":
        removal["payload"]["attempt"] += 1
    elif defect == "transition":
        removal["payload"]["transition_id"] = "foreign"
    elif defect == "session":
        removal["payload"]["session_id"] = "foreign"
    else:
        removal["event_type"] = removal["payload"]["event_type"] = "claimed"
    from polaris.kernelone.events.sourcing.models import EventEnvelope

    removal["integrity_digest"] = EventEnvelope.integrity_digest_for_record(removal)
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "unknown", observed
    assert observed.execution_authority is False
