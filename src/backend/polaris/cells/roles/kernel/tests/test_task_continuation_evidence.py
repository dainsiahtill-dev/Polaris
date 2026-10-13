from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.outcome_commit import commit_turn_result
from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecision, TurnDecisionKind, TurnId


def test_turn_commit_binds_authenticated_session_and_source_state(tmp_path: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path), streams=fact_stream_bootstrap_streams(), maintenance_reason="continuation_test"
        )
    )
    ledger = TurnLedger(turn_id="turn-1")
    ledger.record_decision(
        TurnDecision(
            turn_id=TurnId("turn-1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            finalize_mode=FinalizeMode.NONE,
            domain="code",
        )
    )
    committed = commit_turn_result(
        workspace=str(tmp_path),
        run_id="run-1",
        task_id="task-1",
        transition_id="transition-1",
        ledger=ledger,
        result={"kind": "final_answer", "visible_content": "done"},
        completion_status="success",
        session_id="session-1",
        source_state_before={"answer.py": "a" * 64},
        source_state_after={"answer.py": "a" * 64},
    )
    from polaris.cells.roles.kernel.internal.transaction.continuation_evidence import (
        ContinuationReceiptReferences,
        load_committed_continuation_rows,
    )

    references = ContinuationReceiptReferences.from_receipts("session-1", (committed.receipt.to_dict(),))
    assert (
        load_committed_continuation_rows(
            references,
            workspace=str(tmp_path),
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            source_targets=("answer.py",),
        )
        == []
    )
    with pytest.raises(RuntimeError, match="continuation_identity_mismatch"):
        load_committed_continuation_rows(
            references,
            workspace=str(tmp_path),
            run_id="run-other",
            task_id="task-1",
            session_id="session-1",
            source_targets=("answer.py",),
        )
    for task_id, session_id in (("foreign-task", "session-1"), ("task-1", "foreign-session")):
        with pytest.raises(RuntimeError, match="continuation_identity_mismatch"):
            load_committed_continuation_rows(
                references,
                workspace=str(tmp_path),
                run_id="run-1",
                task_id=task_id,
                session_id=session_id,
                source_targets=("answer.py",),
            )
    foreign = tmp_path / "foreign-workspace"
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(foreign),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="foreign_continuation_test",
        )
    )
    with pytest.raises(RuntimeError, match="continuation_identity_mismatch"):
        load_committed_continuation_rows(
            references,
            workspace=str(foreign),
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            source_targets=("answer.py",),
        )


def test_continuation_claim_dict_is_not_receipt_authority(tmp_path: Path) -> None:
    from polaris.cells.roles.kernel.internal.transaction.continuation_evidence import load_committed_continuation_rows

    with pytest.raises(RuntimeError, match="continuation_evidence_not_kernel_owned"):
        load_committed_continuation_rows(
            {"session_id": "session-1", "mutation_satisfied": True},
            workspace=str(tmp_path),
            run_id="run-1",
            task_id="task-1",
            session_id="session-1",
            source_targets=(),
        )


@pytest.mark.parametrize("required,expected", [(None, ["answer.py"]), ([], []), (["answer.py"], ["answer.py"])])
def test_required_artifacts_keep_legacy_absence_and_explicit_empty_distinct(required: Any, expected: list[str]) -> None:
    from polaris.cells.roles.kernel.internal.kernel.task_boundary import director_task_boundary_target_files

    assert (
        director_task_boundary_target_files({"target_files": ["answer.py"], "required_write_targets": required})
        == expected
    )
