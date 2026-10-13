from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    query_factory_settlement_barrier,
)
from polaris.cells.control_plane.run_ledger.tests.test_factory_settlement_barrier import (
    _append_task_lifecycle,
    _bootstrap_run_ledger_fact_streams,
)


def _failed_read_batch(
    workspace: Path,
    *,
    status: str = "failed",
    tool: str = "read_file",
    pending: int = 0,
    result_status: str = "error",
    include_batch: bool = True,
    factory_run_id: str = "factory-cleanup",
    run_id: str = "director-cleanup",
    task_id: str = "TASK-1",
    append_task: bool = True,
) -> None:
    _bootstrap_run_ledger_fact_streams(workspace)
    if append_task:
        _append_task_lifecycle(
            workspace, factory_run_id=factory_run_id, run_id=run_id, task_id=task_id, terminal_status=status
        )
    result = {
        "call_id": "read-call",
        "tool_name": tool,
        "status": result_status,
        "error": "read scope denied",
        "result": None,
        "effect_receipt": None,
        "effect_receipt_commit": None,
        "directed_effect_claim_status": None,
        "directed_effect_mutation_status": None,
    }
    batch = {
        "batch_id": "batch-cleanup",
        "turn_id": "turn-cleanup",
        "results": [result],
        "raw_results": [result],
        "success_count": int(result_status == "success"),
        "failure_count": int(result_status == "error"),
        "pending_async_count": pending,
        "has_pending_async": bool(pending),
        "effect_receipts": [],
    }
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id="turn-cleanup",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[batch],
    ).to_dict()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            task_id=task_id,
            turn_id="turn-cleanup",
            role="director",
            project_id="project-1",
            lifecycle_receipt=lifecycle,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": run_id,
                "stage": "pending_exec",
                "gate": {
                    "name": "tool_receipt",
                    "ok": False,
                    "summary": "tool batch denied",
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
                "job_token": {
                    "run_id": run_id,
                    "project_id": "project-1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["qa", "code", "command", "tool_receipt"],
                        "required_evidence_modalities": ["qa", "code", "command", "tool_receipt"],
                    },
                },
                "physical_evidence": {
                    "batch_receipt": batch if include_batch else {},
                    "tool_receipts": [],
                    "metadata": {"task_id": task_id, "turn_id": "turn-cleanup"},
                },
                "quiescent": True,
            },
        )
    )


def test_terminal_failed_read_batch_releases_without_future_qa(tmp_path: Path) -> None:
    _failed_read_batch(tmp_path)
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is True
    assert result.closed is True
    assert result.passed is False
    assert set(result.missing_required_modalities) == {"qa", "code", "command", "tool_receipt"}
    assert result.failed_required_modalities == ()
    assert result.expected_effect_count == 1
    assert result.effect_receipt_count == 0
    assert result.open_effect_count == 1
    assert "effect_receipts_open" in result.blocking_reasons
    assert "required_evidence_missing" in result.blocking_reasons


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "completed"},
        {"status": "in_progress"},
        {"tool": "write_file"},
        {"tool": "unknown_tool"},
        {"pending": 1},
        {"result_status": "success"},
        {"include_batch": False},
    ],
)
def test_missing_evidence_cleanup_rejects_success_active_or_unproven_effects(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    _failed_read_batch(tmp_path, **overrides)  # type: ignore[arg-type]
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is False
    assert result.passed is False
