"""A prepared canonical outcome survives final append failure without re-execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
    QueryPayloadFactEventsV1,
    append_fact_event,
    query_fact_events,
    query_fact_stream_head,
    query_payload_fact_events,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.internal.transaction import outcome_commit as owner
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


async def _real_read_outcome(workspace: Path):
    attempt = _setup_attempt(str(workspace))
    fs = KernelFileSystem(str(workspace), get_default_adapter())
    fs.workspace_write_text("source.txt", "actual durable readonly result", encoding="utf-8")
    calls = []

    async def read(tool, args):
        calls.append((tool, args))
        return {"content": fs.workspace_read_text(args["file_path"], encoding="utf-8")}

    turn = TurnId("owned-read-turn")
    invocation = ToolInvocation(
        call_id=ToolCallId("owned-read-call"), tool_name="read_file", arguments={"file_path": "source.txt"}
    )
    definition = ToolBatch(batch_id=BatchId("owned-read-batch"), invocations=[invocation], readonly_serial=[invocation])
    batch = (await ToolBatchRuntime(executor=read).execute_batch(definition, turn))[0].to_dict()
    definition = definition.model_copy(update={"batch_id": BatchId(batch["batch_id"])})
    ledger = TurnLedger(turn_id=str(turn))
    ledger.record_invocation_batch(batch["batch_id"], [invocation])
    ledger.record_decision(
        TurnDecision(
            turn_id=turn,
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="read actual source",
            tool_batch=definition,
            finalize_mode=FinalizeMode.NONE,
            domain="code",
        )
    )
    return attempt, ledger, {"batch_receipt": batch}, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_durable_prepare_exists_after_actual_final_append_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    attempt, ledger, result, calls = await _real_read_outcome(tmp_path)
    prefix = b""
    if legacy:
        marker = append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream=owner.TURN_OUTCOME_STREAM,
                event_type="legacy_marker",
                payload={"schema_version": "legacy_marker.v1"},
                source="test_owned_legacy_setup",
                strict_integrity=True,
            )
        )
        prefix = KernelFileSystem(str(tmp_path), get_default_adapter()).read_bytes(marker.storage_path)
    real_append = owner.append_fact_event if legacy else owner.append_payload_fact_event

    def fail_final(command):
        if command.event_type == owner.TURN_OUTCOME_EVENT_TYPE:
            raise FactStreamError("injected final outcome append failure", code="test_final_append_failed")
        return real_append(command)

    monkeypatch.setattr(owner, "append_fact_event" if legacy else "append_payload_fact_event", fail_final)
    with pytest.raises(owner.TurnOutcomeCommitError, match="injected final") as failure:
        owner.commit_turn_result(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            transition_id="owned-transition",
            ledger=ledger,
            result=result,
            completion_status="success",
            session_id=attempt.session_id,
        )
    if legacy:
        page = query_fact_events(
            QueryFactEventsV1(workspace=str(tmp_path), stream=owner.TURN_OUTCOME_STREAM, strict_integrity=True)
        )
        rows = page.events
        path = query_fact_stream_head(
            QueryFactStreamHeadV1(workspace=str(tmp_path), stream=owner.TURN_OUTCOME_STREAM)
        ).storage_path
        assert KernelFileSystem(str(tmp_path), get_default_adapter()).read_bytes(path).startswith(prefix)
    else:
        rows = query_payload_fact_events(
            QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
        ).events
    prepared = [row for row in rows if row["event_type"] == "turn_outcome_prepared"]
    committed = [row for row in rows if row["event_type"] == owner.TURN_OUTCOME_EVENT_TYPE]
    assert len(prepared) == 1, "Final failure must retain one authentic durable prepared outcome"
    assert committed == []
    assert getattr(failure.value, "prepared_turn_outcome", None) is not None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_other_v2_transition_commit_cannot_bypass_stale_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.public.outcome_recovery import (
        ReplayPreparedTurnOutcomeCommandV1,
        replay_prepared_turn_outcome,
    )

    attempt, ledger, result, calls = await _real_read_outcome(tmp_path)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream=owner.TURN_OUTCOME_STREAM,
            event_type="legacy_marker",
            payload={"schema_version": "legacy_marker.v1"},
            source="test_owned_legacy_setup",
            strict_integrity=True,
        )
    )
    append = owner.append_fact_event

    def fail_one(command):
        if (
            command.event_type == owner.TURN_OUTCOME_EVENT_TYPE
            and command.provenance.transition_id == "owned-transition"
        ):
            raise FactStreamError("injected final outcome append failure", code="test_final_append_failed")
        return append(command)

    monkeypatch.setattr(owner, "append_fact_event", fail_one)
    with pytest.raises(owner.TurnOutcomeCommitError) as failure:
        owner.commit_turn_result(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            transition_id="owned-transition",
            ledger=ledger,
            result=result,
            completion_status="success",
            session_id=attempt.session_id,
        )
    prepared = failure.value.prepared_turn_outcome
    assert prepared is not None
    owner.commit_turn_result(
        workspace=str(tmp_path),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        transition_id="other-transition",
        ledger=ledger,
        result=result,
        completion_status="success",
        session_id=attempt.session_id,
    )
    monkeypatch.setattr(owner, "append_fact_event", append)
    before = query_fact_stream_head(
        QueryFactStreamHeadV1(workspace=str(tmp_path), stream=owner.TURN_OUTCOME_STREAM, strict_integrity=True)
    )
    with pytest.raises(owner.TurnOutcomeCommitError, match="head mismatch"):
        replay_prepared_turn_outcome(
            ReplayPreparedTurnOutcomeCommandV1(prepared, prepared.prepared_event_seq, attempt.session_id)
        )
    after = query_fact_stream_head(
        QueryFactStreamHeadV1(workspace=str(tmp_path), stream=owner.TURN_OUTCOME_STREAM, strict_integrity=True)
    )
    assert before == after
    assert len(calls) == 1


async def _pending(workspace: Path, monkeypatch: pytest.MonkeyPatch, *, legacy: bool = False):
    attempt, ledger, result, calls = await _real_read_outcome(workspace)
    if legacy:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream=owner.TURN_OUTCOME_STREAM,
                event_type="legacy_marker",
                payload={"schema_version": "legacy_marker.v1"},
                source="test_owned_legacy_setup",
                strict_integrity=True,
            )
        )
    name = "append_fact_event" if legacy else "append_payload_fact_event"
    append = getattr(owner, name)

    def fail_final(command):
        if command.event_type == owner.TURN_OUTCOME_EVENT_TYPE:
            raise FactStreamError("injected final outcome append failure", code="test_final_append_failed")
        return append(command)

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, fail_final)
        with pytest.raises(owner.TurnOutcomeCommitError) as failure:
            owner.commit_turn_result(
                workspace=str(workspace),
                run_id=attempt.run_id,
                task_id=attempt.external_task_id,
                transition_id="owned-transition",
                ledger=ledger,
                result={**result, "kind": "tool_batch_with_receipt", "visible_content": "original caller result"},
                completion_status="success",
                session_id=attempt.session_id,
            )
    assert failure.value.prepared_turn_outcome is not None
    return attempt, failure.value.prepared_turn_outcome, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_fresh_process_replays_original_prepared_payload_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    import json
    import os
    import subprocess
    import sys

    from polaris.cells.roles.kernel.public.outcome_recovery import (
        QueryPreparedTurnOutcomeV1,
        ReplayPreparedTurnOutcomeCommandV1,
        query_prepared_turn_outcome,
        replay_prepared_turn_outcome,
    )

    attempt, original, calls = await _pending(tmp_path, monkeypatch, legacy=legacy)
    code = """
import json,sys
from polaris.cells.events.fact_stream.public import FactStreamProvenanceV1,QueryFactStreamHeadV1,QuerySegmentedFactLedgerHeadV1,query_fact_stream_head,query_segmented_fact_ledger_head
from polaris.cells.roles.kernel.public.outcome_recovery import QueryPreparedTurnOutcomeV1,ReplayPreparedTurnOutcomeCommandV1,query_prepared_turn_outcome,replay_prepared_turn_outcome
p=FactStreamProvenanceV1(workspace=sys.argv[1],run_id=sys.argv[2],task_id=sys.argv[3],turn_id='owned-read-turn',transition_id='owned-transition')
ref=query_prepared_turn_outcome(QueryPreparedTurnOutcomeV1(p,sys.argv[4]))
assert ref is not None
head=query_fact_stream_head(QueryFactStreamHeadV1(workspace=sys.argv[1],stream=ref.stream,strict_integrity=True)).current_seq if ref.stream=='roles.kernel.turn_outcomes' else query_segmented_fact_ledger_head(QuerySegmentedFactLedgerHeadV1(workspace=sys.argv[1],logical_stream=ref.stream)).global_seq
result=replay_prepared_turn_outcome(ReplayPreparedTurnOutcomeCommandV1(ref,head,sys.argv[5]))
print(json.dumps({'event_id':result.receipt.fact_event_id,'seq':result.receipt.fact_event_seq,'projection':result.result_projection,'content':result.outcome.execution.receipt.results[0].result}))
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(tmp_path),
            attempt.run_id,
            attempt.external_task_id,
            original.stream,
            attempt.session_id,
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert process.returncode == 0, process.stderr
    returned = json.loads(process.stdout.splitlines()[-1])
    ref = query_prepared_turn_outcome(QueryPreparedTurnOutcomeV1(original.provenance, original.stream))
    assert ref == original
    replay = replay_prepared_turn_outcome(ReplayPreparedTurnOutcomeCommandV1(ref, 0, attempt.session_id))
    assert (replay.receipt.fact_event_id, replay.receipt.fact_event_seq) == (returned["event_id"], returned["seq"])
    assert replay.receipt.fact_event_seq > ref.prepared_event_seq
    assert returned["projection"] == {
        "kind": "tool_batch_with_receipt",
        "visible_content": "original caller result",
        "completion_status": "success",
    }
    assert returned["content"]["content"] == "actual durable readonly result"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "foreign_session",
        "missing_expected_session",
        "wrong_event",
        "wrong_sequence",
        "wrong_scope",
        "wrong_hash",
        "corrupt_blob",
        "missing_blob",
        "stale_head",
    ],
)
async def test_invalid_prepared_replay_never_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    from dataclasses import replace

    from polaris.cells.events.fact_stream.public import QuerySegmentedFactLedgerHeadV1, query_segmented_fact_ledger_head
    from polaris.cells.roles.kernel.public.outcome_recovery import (
        ReplayPreparedTurnOutcomeCommandV1,
        replay_prepared_turn_outcome,
    )

    attempt, prepared, calls = await _pending(tmp_path, monkeypatch)
    head = query_segmented_fact_ledger_head(
        QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=prepared.stream)
    )
    expected_session = attempt.session_id
    expected_head = head.global_seq
    if defect == "foreign_session":
        expected_session = "foreign-session"
    elif defect == "missing_expected_session":
        expected_session = None
    elif defect == "wrong_event":
        prepared = replace(prepared, prepared_event_id="not-the-owner-event")
    elif defect == "wrong_sequence":
        prepared = replace(prepared, prepared_event_seq=prepared.prepared_event_seq + 1)
    elif defect == "wrong_scope":
        prepared = replace(prepared, provenance=replace(prepared.provenance, task_id="FOREIGN"))
    elif defect == "wrong_hash":
        prepared = replace(prepared, payload_ref=replace(prepared.payload_ref, sha256="a" * 64))
    elif defect == "corrupt_blob":
        KernelFileSystem(str(tmp_path), get_default_adapter()).write_text(
            prepared.payload_ref.logical_path, "{}", encoding="utf-8"
        )
    elif defect == "stale_head":
        expected_head = 0
    if defect == "missing_blob":
        from polaris.kernelone.storage import resolve_workspace_runtime_identity

        path = Path(
            resolve_workspace_runtime_identity(str(tmp_path)).runtime_root
        ) / prepared.payload_ref.logical_path.removeprefix("runtime/")
        path.unlink()
    with pytest.raises((RuntimeError, ValueError)):
        replay_prepared_turn_outcome(ReplayPreparedTurnOutcomeCommandV1(prepared, expected_head, expected_session))
    assert (
        query_segmented_fact_ledger_head(
            QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=prepared.stream)
        )
        == head
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_opaque_caller_result_cannot_claim_durable_preparation(tmp_path: Path) -> None:
    attempt, ledger, result, calls = await _real_read_outcome(tmp_path)
    with pytest.raises(owner.TurnOutcomeCommitError):
        owner.commit_turn_result(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            transition_id="owned-transition",
            ledger=ledger,
            result={**result, "workflow_context": {"opaque": object()}},
            completion_status="success",
            session_id=attempt.session_id,
        )
    rows = query_payload_fact_events(
        QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
    ).events
    assert rows == ()
    assert len(calls) == 1
