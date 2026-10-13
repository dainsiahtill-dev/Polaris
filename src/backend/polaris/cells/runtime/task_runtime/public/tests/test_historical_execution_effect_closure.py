"""History queries certify owner facts after later attempts overwrite the session."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)


def _settle(root: Path, attempt: Any) -> None:
    result = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root), identity=attempt, outcome="failed", summary="delivery still failed"
        )
    )
    assert result.success, result


def _successor(root: Path, previous: Any, run_id: str) -> Any:
    # This is the real TaskRuntime reexecution owner used by Director. Only the
    # private fixture workspace is mutated; the observer never resets anything.
    service = TaskRuntimeService(str(root))
    reset = service.reset_task_rows_for_reexecution(source="historical-observer-fixture")
    assert reset["deleted_session_files"]
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(
        service.claim_execution(
            previous.task_id,
            worker_id="worker",
            role_id="director",
            run_id=run_id,
            external_task_id=previous.external_task_id,
            selection_source="test",
        )["execution_attempt"]
    )


def _facts(root: Path) -> list[dict[str, Any]]:
    page = query_fact_events(QueryFactEventsV1(workspace=str(root), stream="task_runtime.execution", limit=1000))
    return [
        {
            **event["payload"],
            "fact_event_id": event["event_id"],
            "fact_event_seq": event["seq"],
            "fact_stream": event["stream"],
        }
        for event in page.events
    ]


async def _history(root: Path) -> tuple[tuple[Any, ...], tuple[dict[str, Any], ...]]:
    (root / "src").mkdir()
    (root / "src/a.py").write_text("before", encoding="utf-8")
    first, first_rows = await _real_effect(root)
    _settle(root, first)
    second = _successor(root, first, "run-second")
    second, second_rows = await _real_effect(root, attempt=second)
    _settle(root, second)
    preflight = _successor(root, second, "run-preflight")
    _settle(root, preflight)
    return (first, second, preflight), (first_rows[0], second_rows[0])


@pytest.mark.asyncio
async def test_history_keeps_two_real_mutation_closures_after_latest_no_effect_preflight(tmp_path: Path) -> None:
    attempts, rows = await _history(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    results = [query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), a)) for a in attempts]
    assert [result.status for result in results] == ["closed", "closed", "closed"], results
    assert [len(result.receipts) for result in results] == [1, 1, 0]
    for result, row in zip(results[:2], rows, strict=True):
        assert result.receipts[0].receipt_hash == row["effect_receipt"]["receipt_hash"]
        assert result.receipts[0].receipt_binding_hash == row["effect_receipt"]["receipt_binding_hash"]
        assert result.receipts[0].commit_event_id == row["effect_receipt_commit"]["event_id"]
    assert all(result.execution_authority is False for result in results)
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.asyncio
async def test_run_ledger_observes_each_unique_attempt_instead_of_only_latest_task(tmp_path: Path) -> None:
    attempts, _rows = await _history(tmp_path)
    from polaris.cells.control_plane.run_ledger.public.service import _terminal_execution_effect_observations

    results = _terminal_execution_effect_observations(tmp_path, _facts(tmp_path))
    assert {result.execution_attempt.session_id for result in results} == {a.session_id for a in attempts}
    assert len(results) == 3
    assert all(result.status == "closed" for result in results), results


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["session_id", "run_id", "task_id", "external_task_id", "role_id", "worker_id", "attempt", "lease_expires_at"],
)
async def test_history_never_authenticates_a_forged_query_identity(tmp_path: Path, field: str) -> None:
    attempts, _rows = await _history(tmp_path)
    forged = replace(attempts[0], **{field: 99 if field in {"attempt", "task_id"} else "foreign"})
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), forged))
    assert result.status != "closed", result
    assert result.receipts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["source", "intent", "identity", "transition", "proof", "owner_status", "corrupt_digest"]
)
async def test_history_rejects_tampered_owner_terminal_fact_even_if_deo_receipts_are_valid(
    tmp_path: Path, attack: str
) -> None:
    attempts, _rows = await _history(tmp_path)
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    terminal = next(
        e for e in events if e["event_type"] == "failed" and e["payload"]["session_id"] == attempts[0].session_id
    )
    session = terminal["payload"]["task_row_snapshot"]["metadata"]["runtime_execution"]
    if attack == "source":
        terminal["source"] = "caller.forged"
    elif attack == "intent":
        session["metadata"]["pending_terminal_intent"]["identity_hash"] = "0" * 64
    elif attack == "identity":
        terminal["payload"]["run_id"] = "foreign"
    elif attack == "transition":
        terminal["payload"]["transition_id"] = "foreign-transition"
    elif attack == "proof":
        session["metadata"]["terminal_settlement_proof"]["terminal_intent_hash"] = "0" * 64
    elif attack == "owner_status":
        terminal["payload"].update(status="active", execution_state="active")
    else:
        terminal["integrity_digest"] = "0" * 64
    if attack != "corrupt_digest":
        # KernelOne digests detect damaged bytes; semantic binding validation
        # must also reject a locally rewritten, self-consistent envelope.
        from polaris.kernelone.events.sourcing.models import EventEnvelope

        terminal["integrity_digest"] = EventEnvelope.integrity_digest_for_record(terminal)
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempts[0]))
    assert result.status != "closed", result
    assert result.receipts == ()


@pytest.mark.asyncio
async def test_terminal_no_parent_proof_does_not_hide_an_unenrolled_registry_file(tmp_path: Path) -> None:
    attempts, _rows = await _history(tmp_path)
    preflight = attempts[-1]
    from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import _registry_stream_token
    from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

    token = _registry_stream_token(DirectedEffectParentRegistryIdentityV1.from_execution_attempt(preflight))
    path = tmp_path / f".polaris/runtime/events/{token}.jsonl"
    path.write_text("untrusted legacy physical facts\n", encoding="utf-8")
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), preflight))
    assert result.status == "unknown", result
    assert result.receipts == ()


@pytest.mark.asyncio
async def test_active_successor_is_not_closed_by_a_previous_owner_terminal_fact(tmp_path: Path) -> None:
    attempts, _rows = await _history(tmp_path)
    active = _successor(tmp_path, attempts[-1], "run-active")
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), active))
    assert result.status == "active", result
    assert result.receipts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["operation_digest", "parent_close_missing", "claim_binding"])
async def test_historical_terminal_fact_never_overrides_damaged_effect_owner_evidence(
    tmp_path: Path, attack: str
) -> None:
    attempts, _rows = await _history(tmp_path)
    events_root = tmp_path / ".polaris/runtime/events"
    from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import (
        DirectedEffectOperationRepository,
        _registry_stream_token,
    )
    from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

    identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempts[0])
    registry = DirectedEffectOperationRepository()._load_registry(str(tmp_path), identity)
    if attack == "parent_close_missing":
        path = events_root / f"{_registry_stream_token(identity)}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(lines[:-1]), encoding="utf-8")
    else:
        binding = next(iter(registry.bindings_by_id.values()))
        path = events_root / f"{binding.operation_stream_token}.jsonl"
        if attack == "operation_digest":
            path.write_text(
                path.read_text(encoding="utf-8").replace("receipt_hash", "forged_hash", 1), encoding="utf-8"
            )
        else:
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            claimed = next(event for event in events if event["event_type"].endswith(".effect_started"))
            claimed["payload"]["replay_descriptor"]["expected_receipt_binding_hash"] = "0" * 64
            from polaris.kernelone.events.sourcing.models import EventEnvelope

            claimed["integrity_digest"] = EventEnvelope.integrity_digest_for_record(claimed)
            path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempts[0]))
    assert result.status != "closed", result
    assert result.receipts == ()


@pytest.mark.asyncio
async def test_strict_owner_fact_failure_preserves_safe_diagnostics_without_granting_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts, _rows = await _history(tmp_path)
    from polaris.cells.events.fact_stream.public import FactStreamError
    from polaris.cells.runtime.task_runtime.internal import execution_effect_closure as observer

    def fail_strict_owner_read(_query: Any) -> Any:
        raise FactStreamError(
            "private provider text must not leave this failure",
            code="event_integrity_mismatch",
            details={
                "stream": "task_runtime.execution",
                "seq": 65,
                "storage_path": "runtime/events/task_runtime.execution.jsonl",
                "workspace": str(tmp_path),
                "token": "private-token",
                "content": "private provider content",
            },
        )

    monkeypatch.setattr(observer, "query_fact_events", fail_strict_owner_read)
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempts[0]))
    assert result.status == "unknown"
    assert result.receipts == ()
    assert result.execution_authority is False
    assert result.reason == "closure_observation_unavailable:event_integrity_mismatch"
    assert result.evidence_refs == (
        "fact_stream_error:stream=task_runtime.execution",
        "fact_stream_error:seq=65",
        "fact_stream_error:storage_path=runtime/events/task_runtime.execution.jsonl",
        f"fact_stream_error:workspace={tmp_path}",
    )
    assert "private" not in result.reason + "".join(result.evidence_refs)
