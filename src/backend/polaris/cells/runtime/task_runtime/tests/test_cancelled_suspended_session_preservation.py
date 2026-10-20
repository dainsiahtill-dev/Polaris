"""Factory row cancellation preserves a fulfilled attempt's closure proof.

All workspaces and owner facts are private module fixtures. Only the existing
nonauthoritative realtime publisher is disabled; claims, settlement, persistence
and strict closure observations use the real owners.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.public import (
    EnrollDirectedEffectOperationStreamCommandV1,
    TaskRuntimeService,
    enroll_directed_effect_operation_stream,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.tests.test_execution_attempt_settlement import (
    _admit_parent,
    _claim_attempt,
    _enroll_parent_registry,
    _settle,
)
from polaris.kernelone.storage import resolve_runtime_path


@pytest.fixture(autouse=True)
def _no_realtime_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the existing publish-false seam without replacing durable owners."""
    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            return False

    monkeypatch.setattr(publisher, "get_log_jetstream_publisher", Publisher)


def _facts(workspace: Path) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution", strict_integrity=True)
    ).events


def _session_path(workspace: Path, task_id: int) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.session.json"))


def test_cancel_preserves_current_fulfilled_suspension_and_cold_terminal_authority(tmp_path: Path) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    settled = _settle(service, identity, "suspended")
    assert settled["success"] is True
    session_path = _session_path(tmp_path, task_id)
    session_bytes = session_path.read_bytes()
    before_facts = _facts(tmp_path)
    before = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), identity))
    assert before.status == "closed" and before.receipts == ()

    cancelled = service.cancel_task_row_for_factory_abort(task_id, factory_run_id="settlement-run")

    assert cancelled is not None and cancelled["status"] == "cancelled"
    after = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), identity))
    assert after.status == "closed", after
    assert after.execution_authority is False and after.receipts == ()
    assert session_path.read_bytes() == session_bytes
    after_facts = _facts(tmp_path)
    assert after_facts[: len(before_facts)] == before_facts
    assert len(after_facts) == len(before_facts) + 1
    assert after_facts[-1]["event_type"] == "cancelled"

    cold = TaskRuntimeService(str(tmp_path))
    stable_facts = _facts(tmp_path)
    rejected = cold.claim_execution(
        task_id,
        worker_id=identity.worker_id,
        role_id=identity.role_id,
        run_id=identity.run_id,
        external_task_id=identity.external_task_id,
        selection_source="cancelled-checkpoint-must-not-resume",
    )
    assert rejected["success"] is False and rejected["reason"] == "task_terminal"
    assert session_path.read_bytes() == session_bytes
    assert _facts(tmp_path) == stable_facts
    repeated = cold.cancel_task_row_for_factory_abort(task_id, factory_run_id="settlement-run")
    assert repeated is not None and repeated.get("already_terminal") is True
    assert session_path.read_bytes() == session_bytes
    assert _facts(tmp_path) == stable_facts
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), identity))
    assert observed.status == "closed" and observed.execution_authority is False


@pytest.mark.parametrize("damage", ("missing_proof", "wrong_intent_hash"))
def test_cancel_rejects_unfulfilled_suspended_proof_without_changing_owner(tmp_path: Path, damage: str) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    assert _settle(service, identity, "suspended")["success"] is True
    path = _session_path(tmp_path, task_id)
    session = json.loads(path.read_text(encoding="utf-8"))
    # Explicit synthetic negative evidence, never a replacement success proof.
    if damage == "missing_proof":
        session["metadata"].pop("terminal_settlement_proof")
    else:
        session["metadata"]["terminal_settlement_proof"]["terminal_intent_hash"] = "0" * 64
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    before_bytes, before_facts = path.read_bytes(), _facts(tmp_path)

    rejected = service.cancel_task_row_for_factory_abort(task_id, factory_run_id="settlement-run")

    assert rejected is not None and rejected.get("ok") is False
    assert path.read_bytes() == before_bytes and _facts(tmp_path) == before_facts
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), identity))
    assert observed.status == "unknown" and observed.receipts == ()


def test_unknown_child_effect_cannot_be_suspended_or_closed_by_factory_cancel(tmp_path: Path) -> None:
    service, task_id, identity = _claim_attempt(tmp_path)
    _enroll_parent_registry(identity)
    parent = _admit_parent(identity)
    assert enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(execution_attempt=identity, parent_binding=parent)
    ).ok
    # This real private FactStream record deliberately represents an uncertain
    # legacy effect. No physical success or receipt is fabricated.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream=parent.operation_stream_token,
            event_type="fixture.legacy_effect_started",
            source="fixture",
            payload={"state": "EFFECT_STARTED"},
            expected_seq=1,
            strict_integrity=True,
            durability="fsync",
        )
    )
    settled = _settle(service, identity, "suspended")
    assert settled["success"] is False and settled["code"] == "settlement_directed_effect_unresolved"
    before = _session_path(tmp_path, task_id).read_bytes(), _facts(tmp_path)

    rejected = service.cancel_task_row_for_factory_abort(task_id, factory_run_id="settlement-run")

    assert rejected is not None and rejected.get("ok") is False
    assert (_session_path(tmp_path, task_id).read_bytes(), _facts(tmp_path)) == before
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), identity))
    assert observed.status == "active" and observed.receipts == ()
