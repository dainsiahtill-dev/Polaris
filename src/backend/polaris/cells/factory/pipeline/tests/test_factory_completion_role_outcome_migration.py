"""Completion consumes committed role results for the current terminal owner only."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.roles.kernel.internal.transaction import outcome_commit as owner
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
    settle_task_runtime_execution_attempt_typed,
)


def _attempt(root: Path, external: str, child: str = "director-child"):
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(root), streams=fact_stream_bootstrap_streams(), maintenance_reason="factory-completion-test"
        )
    )
    service = TaskRuntimeService(str(root))
    row = service.create_task_row(subject=external)
    bound = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(root), str(row["id"]), "factory-parent")
    )
    assert bound.ok
    claimed = service.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id=child,
        external_task_id=external,
        selection_source="test",
    )
    assert claimed["success"]
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])


def _legacy(root: Path, enabled: bool):
    if enabled:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(root),
                stream=owner.TURN_OUTCOME_STREAM,
                event_type="legacy_marker",
                payload={"schema_version": "legacy_marker.v1"},
                source="test",
                strict_integrity=True,
            )
        )


def _commit(root: Path, attempt, *, status="failed", run_id=None, task_id=None, session_id=None, turn="terminal-turn"):
    # The canonical producer creates actual prepare+commit facts; no mocked reader.
    from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecision, TurnDecisionKind, TurnId

    ledger = TurnLedger(turn_id=turn)
    ledger.record_decision(
        TurnDecision(
            turn_id=TurnId(turn),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="actual terminal role result",
            finalize_mode=FinalizeMode.NONE,
            domain="code",
        )
    )
    ledger.finalize()
    committed = owner.commit_turn_result(
        workspace=str(root),
        run_id=run_id or attempt.run_id,
        task_id=task_id or attempt.external_task_id,
        session_id=attempt.session_id if session_id is None else session_id,
        transition_id=turn + "-transition",
        ledger=ledger,
        result={"kind": "final_answer", "visible_content": "actual terminal role result"},
        completion_status=status,
        failure_reason="actual role failure" if status == "failed" else None,
    )
    assert committed.outcome.outcome_status.value == ("completed" if status == "success" else "failed")
    return committed


def _settle(root: Path, attempt, outcome="completed"):
    verdict = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root), identity=attempt, outcome=outcome, summary="independent TaskRuntime terminal owner"
        )
    )
    assert verdict.success
    projection = TaskRuntimeService(str(root)).query_observable_task_rows_projection()
    assert projection.authoritative and not projection.degraded


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("parent_outcome", [False, True])
def test_actual_committed_failure_vetoes_terminal_task_for_current_owner(tmp_path, legacy, parent_outcome):
    attempt = _attempt(tmp_path, "TASK1")
    _legacy(tmp_path, legacy)
    commit = _commit(tmp_path, attempt, run_id="factory-parent" if parent_outcome else attempt.run_id)
    _settle(tmp_path, attempt)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "failed"
    assert result.metadata["task_runtime_status"] == "completed"
    assert result.metadata["turn_outcome_status"] == "failed"
    assert result.metadata["fact_event_id"] == commit.receipt.fact_event_id
    assert result.metadata["fact_event_seq"] == commit.receipt.fact_event_seq
    assert result.metadata["outcome_hash"] == commit.receipt.outcome_hash


@pytest.mark.parametrize("legacy", [False, True])
def test_other_task_success_cannot_overwrite_current_task_failure(tmp_path, legacy):
    failed = _attempt(tmp_path, "TASK1")
    successful = _attempt(tmp_path, "TASK2")
    _legacy(tmp_path, legacy)
    _commit(tmp_path, failed, run_id="factory-parent", turn="failed-turn")
    _commit(tmp_path, successful, status="success", run_id="factory-parent", turn="successful-turn")
    _settle(tmp_path, failed)
    _settle(tmp_path, successful)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id="factory-parent", process_terminal=True)
    assert result is not None and result.status == "failed"


@pytest.mark.parametrize("legacy", [False, True])
def test_prepared_only_is_not_terminal_role_truth(tmp_path, monkeypatch, legacy):
    attempt = _attempt(tmp_path, "TASK1")
    _legacy(tmp_path, legacy)
    original = owner.append_fact_event if legacy else owner.append_payload_fact_event

    def fail_commit(command):
        if command.event_type == owner.TURN_OUTCOME_EVENT_TYPE:
            raise FactStreamError("test interrupted final commit", code="test_interrupted")
        return original(command)

    monkeypatch.setattr(owner, "append_fact_event" if legacy else "append_payload_fact_event", fail_commit)
    with pytest.raises(owner.TurnOutcomeCommitError):
        _commit(tmp_path, attempt)
    _settle(tmp_path, attempt)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "completed"
    assert result.metadata["terminal_source"] == "task_runtime.execution_fact"


@pytest.mark.parametrize("foreign", ["task", "run", "session"])
def test_foreign_or_stale_committed_failure_does_not_veto_current_owner(tmp_path, foreign):
    attempt = _attempt(tmp_path, "TASK1")
    values = (
        {"task_id": "OTHER"}
        if foreign == "task"
        else {"run_id": "OTHER"}
        if foreign == "run"
        else {"session_id": "stale-session"}
    )
    _commit(tmp_path, attempt, **values)
    _settle(tmp_path, attempt)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "completed"


def test_missing_current_session_binding_is_unknown_not_a_role_veto(tmp_path):
    attempt = _attempt(tmp_path, "TASK1")
    _commit(tmp_path, attempt, session_id="")
    _settle(tmp_path, attempt)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "blocked"
    assert result.metadata["reason"] == "role_turn_outcome_observation_unknown"
    assert result.metadata["canonical_authoritative"] is False


def test_role_commit_cannot_complete_a_live_task(tmp_path):
    attempt = _attempt(tmp_path, "TASK1")
    _commit(tmp_path, attempt, status="success")
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is None


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_actual_v3_blob_damage_blocks_completion_instead_of_losing_veto(tmp_path, damage):
    from polaris.cells.events.fact_stream.public import QueryPayloadFactEventsV1, query_payload_fact_events

    attempt = _attempt(tmp_path, "TASK1")
    _commit(tmp_path, attempt)
    _settle(tmp_path, attempt)
    event = query_payload_fact_events(
        QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=owner.TURN_OUTCOME_PAYLOAD_STREAM)
    ).events[-1]
    digest = event["payload"]["payload_ref"]["sha256"]
    paths = list(tmp_path.rglob(digest + ".json"))
    assert len(paths) == 1
    if damage == "missing":
        paths[0].unlink()
    else:
        paths[0].write_text("{}", encoding="utf-8")
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "blocked"
    assert result.metadata["reason"] == "role_turn_outcome_observation_unknown"
    assert result.metadata["canonical_authoritative"] is False


@pytest.mark.parametrize("legacy", [False, True])
def test_real_record_with_wrong_outcome_hash_is_not_canonical_truth(tmp_path, legacy):
    from polaris.cells.events.fact_stream.public import (
        AppendPayloadFactEventCommandV1,
        FactStreamProvenanceV1,
        QueryFactEventsV1,
        QueryPayloadFactEventsV1,
        StoreFactPayloadCommandV1,
        append_payload_fact_event,
        query_fact_events,
        query_payload_fact_events,
        store_fact_payload,
    )

    attempt = _attempt(tmp_path, "TASK1")
    _legacy(tmp_path, legacy)
    _commit(tmp_path, attempt)
    _settle(tmp_path, attempt)
    if legacy:
        event = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream=owner.TURN_OUTCOME_STREAM,
                event_type=owner.TURN_OUTCOME_EVENT_TYPE,
                strict_integrity=True,
            )
        ).events[-1]
        payload = dict(event["payload"])
    else:
        event = query_payload_fact_events(
            QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=owner.TURN_OUTCOME_PAYLOAD_STREAM)
        ).events[-1]
        payload = dict(event["expanded_payload"])
    payload["outcome_hash"] = "0" * 64
    payload["transition_id"] = "tampered-transition"
    payload["provenance"] = {**payload["provenance"], "transition_id": payload["transition_id"]}
    stream = owner.TURN_OUTCOME_STREAM if legacy else owner.TURN_OUTCOME_PAYLOAD_STREAM
    key = ":".join(
        (
            stream,
            payload["storage_identity"]["token"],
            attempt.run_id,
            attempt.external_task_id,
            payload["turn_id"],
            payload["transition_id"],
        )
    )
    if legacy:
        provenance = FactStreamProvenanceV1(**payload["provenance"])
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream=stream,
                event_type=owner.TURN_OUTCOME_EVENT_TYPE,
                payload=payload,
                source=owner.TURN_OUTCOME_SOURCE,
                run_id=attempt.run_id,
                task_id=attempt.external_task_id,
                provenance=provenance,
                idempotency_key=key,
                strict_integrity=True,
                durability="fsync",
            )
        )
    else:
        ref = store_fact_payload(
            StoreFactPayloadCommandV1(
                workspace=str(tmp_path),
                payload=payload,
                payload_schema=payload["schema_version"],
                maintenance_reason="intentional private corrupt semantic witness",
            )
        )
        append_payload_fact_event(
            AppendPayloadFactEventCommandV1(
                workspace=str(tmp_path),
                logical_stream=stream,
                event_type=owner.TURN_OUTCOME_EVENT_TYPE,
                source=owner.TURN_OUTCOME_SOURCE,
                payload_ref=ref,
                idempotency_key=key,
            )
        )
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "blocked"
    assert result.metadata["observation_error"] == "committed_role_outcome_hash_or_identity_invalid"


def test_current_owner_replaced_during_role_query_is_not_a_frozen_completion(tmp_path, monkeypatch):
    from polaris.cells.events.fact_stream import public as facts
    from polaris.cells.runtime.task_runtime.public import reset_runtime_task_records

    attempt = _attempt(tmp_path, "TASK1")
    _commit(tmp_path, attempt)
    _settle(tmp_path, attempt)
    query = facts.query_payload_fact_events
    changed = False

    def change_owner(command):
        nonlocal changed
        result = query(command)
        if not changed:
            changed = True
            reset = reset_runtime_task_records(str(tmp_path), factory_run_id="factory-parent")
            assert reset.get("ok") is True, reset
            assert reset.get("tombstone_count") == 1, reset
        return result

    monkeypatch.setattr(facts, "query_payload_fact_events", change_owner)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == "blocked"
    assert result.metadata["observation_error"] == "current_task_owner_changed_during_role_query"
    assert result.metadata["canonical_authoritative"] is False


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_same_status_keeps_real_role_record_references_without_granting_completion(tmp_path, legacy, terminal):
    from polaris.cells.events.fact_stream.public import (
        QueryFactEventsV1,
        QueryPayloadFactEventsV1,
        query_fact_events,
        query_payload_fact_events,
    )

    attempt = _attempt(tmp_path, "TASK1")
    _legacy(tmp_path, legacy)
    commit = _commit(tmp_path, attempt, status="success" if terminal == "completed" else "failed")
    _settle(tmp_path, attempt, outcome=terminal)
    result = RunCompletionWaiter(tmp_path).canonical_terminal_result(run_id=attempt.run_id, process_terminal=True)
    assert result is not None and result.status == terminal
    assert result.metadata["task_runtime_status"] == terminal
    assert result.metadata["turn_outcome_status"] == terminal
    proof = result.metadata.get("turn_outcome_observation")
    assert isinstance(proof, dict), "A matrix tie must retain the already verified role record"
    assert proof["fact_event_id"] == commit.receipt.fact_event_id
    assert proof["fact_event_seq"] == commit.receipt.fact_event_seq
    assert proof["outcome_hash"] == commit.receipt.outcome_hash
    if legacy:
        event = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream=owner.TURN_OUTCOME_STREAM,
                event_type=owner.TURN_OUTCOME_EVENT_TYPE,
                strict_integrity=True,
            )
        ).events[-1]
        assert proof["fact_event_hash"] == event["integrity_digest"]
    else:
        event = query_payload_fact_events(
            QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=owner.TURN_OUTCOME_PAYLOAD_STREAM)
        ).events[-1]
        assert proof["fact_event_hash"] == event["event_hash"]
        assert proof["payload_ref"] == event["payload"]
        assert proof["fact_event_hash"] != event["payload"]["payload_ref"]["sha256"]
