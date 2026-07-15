from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import _resolve_durable_workspace
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.outcome_commit import (
    TURN_OUTCOME_STREAM,
    DurableTurnCommit,
    TurnOutcomeCommitError,
    commit_turn_result,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    OutcomeStatus,
    ResolutionCode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnFailureClass,
    TurnId,
)
from polaris.cells.roles.profile.public.service import RoleTurnRequest

if TYPE_CHECKING:
    from pathlib import Path


def _decision(turn_id: str = "turn-1") -> TurnDecision:
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.FINAL_ANSWER,
        visible_message="completed",
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )


def _bootstrap_workspace(workspace: Path) -> None:
    """Explicitly provision FactStream before direct outcome-commit test I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="roles_kernel_turn_outcome_test_setup",
        )
    )


def test_commit_turn_result_persists_canonical_fact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    ledger = TurnLedger(turn_id="turn-1")
    ledger.record_decision(_decision())
    ledger.finalize()

    committed = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-1",
        ledger=ledger,
        result={"kind": "final_answer", "visible_content": "completed"},
        completion_status="success",
    )

    assert committed.outcome.outcome_status == OutcomeStatus.COMPLETED
    assert committed.outcome.resolution_code == ResolutionCode.COMPLETED
    assert committed.outcome.commit_ref == committed.receipt
    assert committed.receipt.fact_event_seq == 1
    assert committed.receipt.outcome_hash
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=TURN_OUTCOME_STREAM,
            limit=10,
        )
    )
    assert queried.total == 1
    assert queried.events[0]["payload"]["outcome_hash"] == committed.receipt.outcome_hash
    provenance = queried.events[0]["payload"]["provenance"]
    assert provenance == {
        "workspace": str(workspace.resolve()),
        "run_id": "run-1",
        "task_id": "task-1",
        "turn_id": "turn-1",
        "transition_id": "transition-1",
    }
    assert "commit_ref" not in queried.events[0]["payload"]["outcome"]


def test_commit_turn_result_does_not_lazily_bootstrap_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = TurnLedger(turn_id="turn-no-lazy-bootstrap")
    ledger.record_decision(_decision("turn-no-lazy-bootstrap"))

    with pytest.raises(FactStreamError) as exc_info:
        commit_turn_result(
            workspace=str(workspace),
            run_id="run-1",
            task_id="task-1",
            transition_id="transition-no-lazy-bootstrap",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "completed"},
            completion_status="success",
        )
    assert exc_info.value.code == "lock_authority_missing"


def test_commit_turn_result_replay_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    ledger = TurnLedger(turn_id="turn-idempotent")
    ledger.record_decision(_decision("turn-idempotent"))
    result = {"kind": "final_answer", "visible_content": "completed"}

    first = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-idempotent",
        ledger=ledger,
        result=result,
        completion_status="success",
    )
    second = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-idempotent",
        ledger=ledger,
        result=result,
        completion_status="success",
    )

    assert second.receipt.fact_event_id == first.receipt.fact_event_id
    assert second.receipt.fact_event_seq == first.receipt.fact_event_seq


def test_commit_turn_result_rejects_conflicting_terminal_replay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    ledger = TurnLedger(turn_id="turn-conflict")
    ledger.record_decision(_decision("turn-conflict"))
    commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-conflict",
        ledger=ledger,
        result={"kind": "final_answer", "visible_content": "completed"},
        completion_status="success",
    )

    with pytest.raises(TurnOutcomeCommitError, match="idempotency conflict"):
        commit_turn_result(
            workspace=str(workspace),
            run_id="run-1",
            task_id="task-1",
            transition_id="transition-conflict",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "failed"},
            completion_status="error",
            failure_reason="late failure",
        )


def test_commit_turn_result_records_pre_decision_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    ledger = TurnLedger(turn_id="turn-provider-error")

    committed = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-provider-error",
        ledger=ledger,
        result=None,
        completion_status="error",
        failure_reason="provider unavailable",
    )

    assert committed.outcome.decision is None
    assert committed.outcome.outcome_status == OutcomeStatus.FAILED
    assert committed.outcome.failure_class == TurnFailureClass.RUNTIME_FAILURE
    assert committed.outcome.failure_reason == "provider unavailable"


def test_commit_tool_batch_preserves_top_level_effect_receipts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    turn_id = TurnId("turn-write")
    invocation = ToolInvocation(
        call_id=ToolCallId("call-write"),
        tool_name="write_file",
        arguments={"file": "result.txt", "content": "done"},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    decision = TurnDecision(
        turn_id=turn_id,
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id=BatchId("batch-write"),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    ledger = TurnLedger(turn_id=str(turn_id))
    ledger.record_decision(decision)
    effect_receipt = {
        "effect_id": "effect-write",
        "status": "success",
        "effect_type": "fs_write",
        "resource": "result.txt",
    }

    committed = commit_turn_result(
        workspace=str(workspace),
        run_id="run-write",
        task_id="task-write",
        transition_id="transition-write",
        ledger=ledger,
        result={
            "kind": "tool_batch_with_receipt",
            "batch_receipt": {
                "batch_id": "batch-write",
                "turn_id": str(turn_id),
                "results": [],
                "raw_results": [],
                "effect_receipts": [effect_receipt],
                "success_count": 1,
                "failure_count": 0,
                "pending_async_count": 0,
                "has_pending_async": False,
            },
        },
        completion_status="success",
    )

    execution = committed.outcome.execution
    assert execution is not None
    assert execution.receipt is not None
    assert execution.receipt.effect_receipts == [effect_receipt]


def test_turn_outcomes_are_isolated_for_fresh_workspaces_with_matching_names(tmp_path: Path) -> None:
    workspace_a = tmp_path / "fresh-a" / "l1-01"
    workspace_b = tmp_path / "fresh-b" / "l1-01"
    workspace_a.mkdir(parents=True)
    workspace_b.mkdir(parents=True)
    _bootstrap_workspace(workspace_a)
    _bootstrap_workspace(workspace_b)

    def commit(workspace: Path) -> DurableTurnCommit:
        ledger = TurnLedger(turn_id="turn-1")
        ledger.record_decision(_decision())
        return commit_turn_result(
            workspace=str(workspace),
            run_id="factory-run-1",
            task_id="TASK-1-foundation",
            transition_id="ce-transition-1",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": str(workspace)},
            completion_status="success",
        )

    first = commit(workspace_a)
    second = commit(workspace_b)

    assert first.receipt.fact_event_seq == 1
    assert second.receipt.fact_event_seq == 1
    first_event = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace_a), stream=TURN_OUTCOME_STREAM, limit=10)
    ).events[0]
    second_event = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace_b), stream=TURN_OUTCOME_STREAM, limit=10)
    ).events[0]
    assert first_event["payload"]["storage_identity"]["token"] != second_event["payload"]["storage_identity"]["token"]


def test_turn_outcomes_are_isolated_for_separate_factory_runs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace)

    for run_id in ("factory-run-a", "factory-run-b"):
        ledger = TurnLedger(turn_id="turn-1")
        ledger.record_decision(_decision())
        commit_turn_result(
            workspace=str(workspace),
            run_id=run_id,
            task_id="TASK-1-foundation",
            transition_id="ce-transition-1",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": run_id},
            completion_status="success",
        )

    queried = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream=TURN_OUTCOME_STREAM, limit=10))
    assert queried.total == 2
    assert {event["payload"]["provenance"]["run_id"] for event in queried.events} == {
        "factory-run-a",
        "factory-run-b",
    }


def test_turn_outcomes_are_isolated_for_distinct_attempts_of_the_same_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace)

    for transition_id, visible_content in (
        ("invocation-a-attempt-0", "first attempt"),
        ("invocation-a-attempt-1", "retry attempt"),
    ):
        ledger = TurnLedger(turn_id="turn-1")
        ledger.record_decision(_decision())
        commit_turn_result(
            workspace=str(workspace),
            run_id="factory-run-1",
            task_id="TASK-1-foundation",
            transition_id=transition_id,
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": visible_content},
            completion_status="success",
        )

    queried = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream=TURN_OUTCOME_STREAM, limit=10))
    assert queried.total == 2
    assert {event["payload"]["provenance"]["transition_id"] for event in queried.events} == {
        "invocation-a-attempt-0",
        "invocation-a-attempt-1",
    }


def test_turn_outcome_concurrent_identical_attempt_returns_one_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace)

    def commit() -> DurableTurnCommit:
        ledger = TurnLedger(turn_id="turn-1")
        ledger.record_decision(_decision())
        return commit_turn_result(
            workspace=str(workspace),
            run_id="factory-run-1",
            task_id="TASK-1-foundation",
            transition_id="invocation-a-attempt-0",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "completed"},
            completion_status="success",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        commits = list(executor.map(lambda _: commit(), range(16)))

    assert len({commit.receipt.fact_event_id for commit in commits}) == 1
    assert {commit.receipt.fact_event_seq for commit in commits} == {1}


def test_turn_outcome_requires_explicit_transition_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = TurnLedger(turn_id="turn-1")
    ledger.record_decision(_decision())

    with pytest.raises(TurnOutcomeCommitError, match="transition_id"):
        commit_turn_result(
            workspace=str(workspace),
            run_id="run-1",
            task_id="task-1",
            transition_id="",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "completed"},
            completion_status="success",
        )


def test_turn_outcome_rejects_unscoped_legacy_event_without_deleting_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="roles_kernel_legacy_fact_test",
        )
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream=TURN_OUTCOME_STREAM,
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            payload={
                "schema_version": "roles.kernel.turn_outcome_fact.v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "turn_id": "turn-1",
                "outcome": {"outcome_status": "completed"},
            },
            run_id="run-1",
            task_id="task-1",
            idempotency_key="run-1:task-1:turn-1",
        )
    )
    ledger = TurnLedger(turn_id="turn-1")
    ledger.record_decision(_decision())

    with pytest.raises(TurnOutcomeCommitError, match="explicit migration"):
        commit_turn_result(
            workspace=str(workspace),
            run_id="run-1",
            task_id="task-1",
            transition_id="transition-1",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "completed"},
            completion_status="success",
        )

    assert (
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream=TURN_OUTCOME_STREAM, limit=10)).total == 1
    )


def test_durable_turn_rejects_request_workspace_that_differs_from_kernel_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "fresh-workspace"
    canonical_workspace = tmp_path / "canonical-project"
    workspace.mkdir()
    canonical_workspace.mkdir()
    request = RoleTurnRequest(
        workspace=str(canonical_workspace),
        run_id="factory-run-1",
        task_id="TASK-1-foundation",
    )

    with pytest.raises(RuntimeError, match="workspace identity mismatch"):
        kernel = type("Kernel", (), {"workspace": str(workspace)})()
        _resolve_durable_workspace(request, kernel)
