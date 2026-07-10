from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.outcome_commit import (
    TURN_OUTCOME_STREAM,
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


def test_commit_turn_result_persists_canonical_fact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = TurnLedger(turn_id="turn-1")
    ledger.record_decision(_decision())
    ledger.finalize()

    committed = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
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
    assert "commit_ref" not in queried.events[0]["payload"]["outcome"]


def test_commit_turn_result_replay_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = TurnLedger(turn_id="turn-idempotent")
    ledger.record_decision(_decision("turn-idempotent"))
    result = {"kind": "final_answer", "visible_content": "completed"}

    first = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        ledger=ledger,
        result=result,
        completion_status="success",
    )
    second = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        ledger=ledger,
        result=result,
        completion_status="success",
    )

    assert second.receipt.fact_event_id == first.receipt.fact_event_id
    assert second.receipt.fact_event_seq == first.receipt.fact_event_seq


def test_commit_turn_result_rejects_conflicting_terminal_replay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = TurnLedger(turn_id="turn-conflict")
    ledger.record_decision(_decision("turn-conflict"))
    commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
        ledger=ledger,
        result={"kind": "final_answer", "visible_content": "completed"},
        completion_status="success",
    )

    with pytest.raises(TurnOutcomeCommitError, match="idempotency conflict"):
        commit_turn_result(
            workspace=str(workspace),
            run_id="run-1",
            task_id="task-1",
            ledger=ledger,
            result={"kind": "final_answer", "visible_content": "failed"},
            completion_status="error",
            failure_reason="late failure",
        )


def test_commit_turn_result_records_pre_decision_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    ledger = TurnLedger(turn_id="turn-provider-error")

    committed = commit_turn_result(
        workspace=str(workspace),
        run_id="run-1",
        task_id="task-1",
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
