from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    FactorySettlementBarrierQueryError,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    query_factory_settlement_barrier,
)
from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, append_fact_event
from polaris.cells.events.fact_stream.public.contracts import (
    BootstrapFactStreamWorkspaceCommandV1,
)
from polaris.cells.events.fact_stream.public.workspace_bootstrap import (
    bootstrap_fact_stream_workspace,
)


def _bootstrap_run_ledger_fact_streams(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="factory_settlement_barrier_tests",
        )
    )


@pytest.fixture(autouse=True)
def _bootstrap_default_run_ledger_fact_streams(tmp_path: Path) -> None:
    _bootstrap_run_ledger_fact_streams(tmp_path)


def _append_task_fact(
    workspace: Path,
    *,
    factory_run_id: str,
    run_id: str,
    task_id: str,
    event_type: str,
    status: str,
) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
            source="factory_settlement_barrier_test",
            run_id=run_id,
            task_id=task_id,
            payload={
                "event_type": event_type,
                "factory_run_id": factory_run_id,
                "factory_bench_project_id": "project-1",
                "run_id": run_id,
                "task_id": task_id,
                "status": status,
                "execution_state": status,
                "task_row_snapshot": {
                    "id": task_id,
                    "status": status,
                    "metadata": {
                        "external_task_id": task_id,
                        "factory_run_id": factory_run_id,
                        "factory_bench_project_id": "project-1",
                        "target_files": ["src/app.py"],
                        "task_contract": {"target_files": ["src/app.py"]},
                        "runtime_execution": {"role_id": "director"},
                    },
                },
            },
        )
    )


def _append_task_lifecycle(
    workspace: Path,
    *,
    factory_run_id: str,
    run_id: str,
    task_id: str,
    terminal_status: str,
) -> None:
    _append_task_fact(
        workspace,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        event_type="claimed",
        status="claimed",
    )
    _append_task_fact(
        workspace,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        event_type=terminal_status,
        status=terminal_status,
    )


def _append_gate(
    workspace: Path,
    *,
    run_id: str,
    required_modality: str = "command",
    modality_present: bool = True,
    modality_ok: bool = True,
) -> None:
    modalities = (
        {
            required_modality: {
                "present": True,
                "ok": modality_ok,
                "detail": f"{required_modality} evidence",
            }
        }
        if modality_present
        else {}
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": run_id,
                "stage": "qa_verifier",
                "gate": {
                    "name": "qa_verifier",
                    "ok": modality_present and modality_ok,
                    "summary": "terminal QA gate",
                },
                "job_token": {
                    "token_id": f"token-{run_id}",
                    "run_id": run_id,
                    "project_id": "project-1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": [required_modality],
                        "required_evidence_modalities": [required_modality],
                    },
                },
                "physical_evidence": {"modalities": modalities},
            },
        )
    )


def _append_tool_lifecycle(
    workspace: Path,
    *,
    run_id: str,
    task_id: str,
    include_effect_receipt: bool = True,
) -> None:
    tool_result: dict[str, object] = {
        "call_id": f"call-{run_id}",
        "tool_name": "write_file",
        "status": "success",
    }
    if include_effect_receipt:
        tool_result["effect_receipt"] = {
            "operation": "write_file",
            "file": "src/app.py",
            "before_hash": "before-hash",
            "after_hash": f"after-{run_id}",
        }
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=f"turn-{run_id}",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": f"batch-{run_id}",
                "results": [tool_result],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            task_id=task_id,
            turn_id=f"turn-{run_id}",
            role="director",
            project_id="project-1",
            lifecycle_receipt=lifecycle,
        )
    )


def _append_failed_no_dispatch_lifecycle(
    workspace: Path,
    *,
    run_id: str,
    task_id: str,
) -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=run_id,
        task_id=task_id,
        turn_id=f"turn-{run_id}",
        role="director",
        native_tool_calls_count=0,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="failed",
        failure_class="EARLY_PLATFORM_FAILURE",
        reason="execution failed before tool dispatch",
    ).to_dict()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            task_id=task_id,
            turn_id=f"turn-{run_id}",
            role="director",
            project_id="project-1",
            lifecycle_receipt=lifecycle,
        )
    )


def _append_terminal_run(
    workspace: Path,
    *,
    factory_run_id: str,
    run_id: str,
    terminal_status: str = "completed",
    modality_present: bool = True,
    modality_ok: bool = True,
    include_effect_receipt: bool = True,
) -> None:
    task_id = f"task-{run_id}"
    _append_task_lifecycle(
        workspace,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        terminal_status=terminal_status,
    )
    _append_gate(
        workspace,
        run_id=run_id,
        modality_present=modality_present,
        modality_ok=modality_ok,
    )
    _append_tool_lifecycle(
        workspace,
        run_id=run_id,
        task_id=task_id,
        include_effect_receipt=include_effect_receipt,
    )


def test_query_rejects_empty_factory_scope(tmp_path: Path) -> None:
    with pytest.raises(FactorySettlementBarrierQueryError, match="factory_run_id"):
        query_factory_settlement_barrier(tmp_path, "")


def test_missing_and_failed_required_evidence_are_distinct(tmp_path: Path) -> None:
    _append_terminal_run(
        tmp_path,
        factory_run_id="factory-missing",
        run_id="run-missing",
        modality_present=False,
    )
    _append_terminal_run(
        tmp_path,
        factory_run_id="factory-failed",
        run_id="run-failed-evidence",
        modality_ok=False,
    )

    missing = query_factory_settlement_barrier(tmp_path, "factory-missing")
    failed = query_factory_settlement_barrier(tmp_path, "factory-failed")

    assert missing.missing_required_modalities == ("command",)
    assert missing.failed_required_modalities == ()
    assert missing.closed is False
    assert "required_evidence_missing" in missing.blocking_reasons
    assert failed.missing_required_modalities == ()
    assert failed.failed_required_modalities == ("command",)
    assert failed.closed is True
    assert failed.passed is False
    assert failed.release_allowed is True
    assert "required_evidence_failed" in failed.blocking_reasons


def test_active_task_lifecycle_keeps_barrier_open(tmp_path: Path) -> None:
    factory_run_id = "factory-active"
    run_id = "run-active"
    task_id = "task-active"
    _append_task_fact(
        tmp_path,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        event_type="claimed",
        status="in_progress",
    )
    _append_gate(tmp_path, run_id=run_id)
    _append_tool_lifecycle(tmp_path, run_id=run_id, task_id=task_id)

    result = query_factory_settlement_barrier(tmp_path, factory_run_id)

    assert result.active_lifecycle_count == 1
    assert result.open_lifecycle_count == 1
    assert result.closed is False
    assert result.release_allowed is False
    assert "lifecycle_open" in result.blocking_reasons


def test_missing_effect_receipt_blocks_release(tmp_path: Path) -> None:
    _append_terminal_run(
        tmp_path,
        factory_run_id="factory-no-effect",
        run_id="run-no-effect",
        include_effect_receipt=False,
    )

    result = query_factory_settlement_barrier(tmp_path, "factory-no-effect")

    assert result.expected_effect_count == 1
    assert result.effect_receipt_count == 0
    assert result.open_effect_count == 1
    assert result.closed is False
    assert "effect_receipt_missing" in result.blocking_reasons
    assert "effect_receipts_open" in result.blocking_reasons


def test_terminal_early_failure_without_dispatch_effects_or_gates_can_settle(
    tmp_path: Path,
) -> None:
    factory_run_id = "factory-early-failure"
    run_id = "run-early-failure"
    task_id = "task-early-failure"
    _append_task_lifecycle(
        tmp_path,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        terminal_status="failed",
    )
    _append_failed_no_dispatch_lifecycle(
        tmp_path,
        run_id=run_id,
        task_id=task_id,
    )

    result = query_factory_settlement_barrier(tmp_path, factory_run_id)

    assert result.expected_effect_count == 0
    assert result.effect_receipt_count == 0
    assert result.open_effect_count == 0
    assert result.open_lifecycle_count == 0
    assert result.closed is True
    assert result.release_allowed is True
    assert result.passed is False
    assert "run_ledger_gate_missing" not in result.blocking_reasons
    assert "effect_receipt_missing" not in result.blocking_reasons
    assert "lifecycle_failed" in result.blocking_reasons


def test_failed_task_run_is_closed_but_cannot_pass(tmp_path: Path) -> None:
    _append_terminal_run(
        tmp_path,
        factory_run_id="factory-task-failed",
        run_id="run-task-failed",
        terminal_status="failed",
    )

    result = query_factory_settlement_barrier(tmp_path, "factory-task-failed")

    assert result.closed is True
    assert result.failed_lifecycle_count == 1
    assert result.passed is False
    assert result.release_allowed is True
    assert "lifecycle_failed" in result.blocking_reasons


def test_removed_task_runtime_tombstone_is_terminal_for_release(tmp_path: Path) -> None:
    """TaskRuntime reset emits status=removed; barrier must treat it as closed."""
    factory_run_id = "factory-removed-tombstone"
    run_id = "run-removed-tombstone"
    task_id = "task-removed-tombstone"
    _append_task_fact(
        tmp_path,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        event_type="created",
        status="pending",
    )
    _append_task_fact(
        tmp_path,
        factory_run_id=factory_run_id,
        run_id=run_id,
        task_id=task_id,
        event_type="runtime_reset_removed",
        status="removed",
    )

    result = query_factory_settlement_barrier(tmp_path, factory_run_id)

    assert result.open_lifecycle_count == 0
    assert result.closed is True
    assert result.release_allowed is True
    assert result.passed is False


def test_factory_scoped_pending_tasks_without_director_run_id_are_in_scope(
    tmp_path: Path,
) -> None:
    """PM-bound open rows may only carry factory_run_id (no director run_id)."""
    factory_run_id = "factory-pm-only"
    task_id = "task-pm-only"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="factory_run_bound",
            source="factory_settlement_barrier_test",
            task_id=task_id,
            payload={
                "event_type": "factory_run_bound",
                "factory_run_id": factory_run_id,
                "task_id": task_id,
                "status": "pending",
                "execution_state": "pending",
                "task_row_snapshot": {
                    "id": task_id,
                    "status": "pending",
                    "metadata": {"factory_run_id": factory_run_id},
                },
            },
        )
    )

    open_result = query_factory_settlement_barrier(tmp_path, factory_run_id)
    assert open_result.open_lifecycle_count == 1
    assert "lifecycle_open" in open_result.blocking_reasons
    assert "factory_run_not_found" not in open_result.blocking_reasons
    assert open_result.release_allowed is False

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="cancelled",
            source="factory_settlement_barrier_test",
            task_id=task_id,
            payload={
                "event_type": "cancelled",
                "factory_run_id": factory_run_id,
                "task_id": task_id,
                "status": "cancelled",
                "execution_state": "cancelled",
                "task_row_snapshot": {
                    "id": task_id,
                    "status": "cancelled",
                    "metadata": {"factory_run_id": factory_run_id},
                },
            },
        )
    )

    closed = query_factory_settlement_barrier(tmp_path, factory_run_id)
    assert closed.open_lifecycle_count == 0
    assert closed.closed is True
    assert closed.release_allowed is True
    assert "factory_run_not_found" not in closed.blocking_reasons


def test_barrier_hash_is_idempotent_for_unchanged_facts(tmp_path: Path) -> None:
    _append_terminal_run(
        tmp_path,
        factory_run_id="factory-stable",
        run_id="run-stable",
    )
    files_before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    first = query_factory_settlement_barrier(tmp_path, "factory-stable")
    second = query_factory_settlement_barrier(tmp_path, "factory-stable")
    files_after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert first == second
    assert files_after == files_before
    assert len(first.barrier_hash) == 64
    assert first.closed is True
    assert first.passed is True
    assert first.release_allowed is True


def test_query_isolated_across_workspace_and_factory_run(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    _bootstrap_run_ledger_fact_streams(workspace_a)
    _bootstrap_run_ledger_fact_streams(workspace_b)
    _append_terminal_run(
        workspace_a,
        factory_run_id="factory-a",
        run_id="run-a",
    )
    _append_terminal_run(
        workspace_a,
        factory_run_id="factory-b",
        run_id="run-b",
    )
    _append_terminal_run(
        workspace_b,
        factory_run_id="factory-a",
        run_id="run-a",
    )

    factory_a = query_factory_settlement_barrier(workspace_a, "factory-a")
    factory_b = query_factory_settlement_barrier(workspace_a, "factory-b")
    other_workspace = query_factory_settlement_barrier(workspace_b, "factory-a")

    assert factory_a.consumed_run_ids == ("run-a",)
    assert factory_b.consumed_run_ids == ("run-b",)
    assert all("run-b" not in ref for ref in factory_a.evidence_refs)
    assert factory_a.barrier_hash != factory_b.barrier_hash
    assert factory_a.workspace != other_workspace.workspace
    assert factory_a.barrier_hash != other_workspace.barrier_hash
