from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    query_factory_settlement_barrier,
)
from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import CommittedTurnReceiptObservationV1
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.control_plane.run_ledger.public.settlement_barrier import (
    _closed_artifact_receipt_reuse,
    _terminal_failure_has_closed_dispatches,
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
    committed_mutation: bool = False,
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
    if committed_mutation:
        from polaris.cells.control_plane.run_ledger.tests.test_tool_lifecycle_result_mirrors import _committed_row

        result = _committed_row()
    batch = {
        "batch_id": result["effect_receipt"]["batch_id"] if committed_mutation else "batch-cleanup",
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
    assert result.expected_effect_count == 0
    assert result.effect_receipt_count == 0
    assert result.open_effect_count == 0
    assert "effect_receipts_open" not in result.blocking_reasons
    assert "effect_receipt_missing" not in result.blocking_reasons
    assert "required_evidence_missing" in result.blocking_reasons


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "completed", "result_status": "success"},
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


def test_selfconsistent_commit_without_persisted_operation_cannot_close(tmp_path: Path) -> None:
    _failed_read_batch(tmp_path, committed_mutation=True, result_status="success")
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is False
    assert result.passed is False
    assert "required_evidence_missing" in result.blocking_reasons
    assert result.missing_required_modalities


async def _append_real_terminal_mutation(
    tmp_path: Path,
    *,
    factory_run_id: str = "factory-cleanup",
    transport_wrapper: bool = False,
    mirror_change: str = "",
    read_mirror_change: str = "",
) -> None:
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
    from polaris.cells.runtime.task_runtime.public import (
        BindRuntimeTaskToFactoryRunCommandV1,
        SettleTaskRuntimeExecutionAttemptCommandV1,
        bind_runtime_task_to_factory_run,
        settle_task_runtime_execution_attempt_typed,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before", encoding="utf-8")
    attempt, rows = await _real_effect(tmp_path)
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(attempt.task_id), factory_run_id=factory_run_id
        )
    ).ok
    assert settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(tmp_path), identity=attempt, outcome="failed", summary="delivery failed"
        )
    ).success
    from copy import deepcopy

    raw_rows = deepcopy(rows)
    if read_mirror_change:
        read_row = {
            "tool_name": "repo_tree",
            "call_id": "read-fixture",
            "status": "success",
            "result": {"entries": ["src/a.py"]},
            "error": None,
            "effect_receipt": None,
        }
        raw_read_row = {key: value for key, value in read_row.items() if key not in {"error", "effect_receipt"}}
        if read_mirror_change == "error":
            raw_read_row["error"] = "actual failure"
        elif read_mirror_change == "effect_receipt":
            raw_read_row["effect_receipt"] = {"operation_id": "foreign-operation"}
        elif read_mirror_change == "unknown_null":
            raw_read_row["unknown_field"] = None
        elif read_mirror_change == "nested_null":
            raw_read_row["result"] = {"entries": ["src/a.py"], "error": None}
        rows.append(read_row)
        raw_rows.append(raw_read_row)
    if mirror_change:
        raw_rows[0]["execution_time_ms"] = rows[0].get("execution_time_ms", 0) + 17
        if mirror_change != "execution_time_ms":
            if mirror_change == "nested_receipt":
                raw_rows[0]["effect_receipt"]["receipt_hash"] = "0" * 64
            else:
                raw_rows[0][mirror_change] = "forged"
    batch = {
        "batch_id": rows[0]["effect_receipt"]["batch_id"] + ("-transport" if transport_wrapper else ""),
        "turn_id": "turn-input-fixture",
        "results": rows,
        "raw_results": raw_rows,
        "success_count": len(rows),
        "failure_count": 0,
        "pending_async_count": 0,
        "has_pending_async": False,
        "effect_receipts": [rows[0]["effect_receipt"]],
    }
    receipt = build_tool_call_lifecycle_receipt(
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id="turn-input-fixture",
        role="director",
        native_tool_calls_count=len(rows),
        decoded_tool_calls_count=len(rows),
        dispatched_tool_calls_count=len(rows),
        receipts=[batch],
    ).to_dict()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id="turn-input-fixture",
            role="director",
            project_id="project-1",
            lifecycle_receipt=receipt,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": False, "summary": "verification never ran"},
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "project-1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "required_evidence_modalities": ["qa", "command"],
                        "enabled_evidence_modalities": ["qa", "command"],
                    },
                },
                "physical_evidence": {"batch_receipt": batch},
            },
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_wrapper", [False, True])
async def test_terminal_failed_real_mutation_can_close_with_missing_verifiers(
    tmp_path: Path, transport_wrapper: bool
) -> None:
    await _append_real_terminal_mutation(tmp_path, transport_wrapper=transport_wrapper)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is True, result
    assert result.passed is False
    assert result.missing_required_modalities == ("command", "qa")
    assert "required_evidence_missing" in result.blocking_reasons
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "execution_time_ms",
        "status",
        "error",
        "result",
        "effect_receipt",
        "effect_receipt_commit",
        "tool_name",
        "call_id",
        "nested_receipt",
        "new_field",
    ],
)
async def test_operational_cleanup_mirrors_ignore_only_declared_top_level_timing(tmp_path: Path, field: str) -> None:
    await _append_real_terminal_mutation(tmp_path, mirror_change=field)
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is (field == "execution_time_ms"), result
    assert result.passed is False
    assert result.missing_required_modalities == ("command", "qa")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["optional_null", "error", "effect_receipt", "unknown_null", "nested_null"])
async def test_terminal_cleanup_read_mirrors_preserve_authority_with_optional_nulls(
    tmp_path: Path, change: str
) -> None:
    await _append_real_terminal_mutation(tmp_path, read_mirror_change=change)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is (change == "optional_null"), result
    assert result.passed is False
    assert result.missing_required_modalities == ("command", "qa")
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "defect",
    [
        "",
        "workspace",
        "run",
        "task",
        "turn",
        "refs",
        "hash",
        "duplicate",
        "mutation",
        "pending",
        "unknown",
        "malformed",
        "native_ids",
        "ledger_conflict",
        "readonly_null",
        "nonnull_commit",
    ],
)
def test_committed_readonly_observation_closes_only_exact_missing_batch(tmp_path: Path, defect: str) -> None:
    row = {"call_id": "call-read", "tool_name": "read_file", "status": "error", "result": None}
    if defect == "mutation":
        row["tool_name"] = "execute_command"
    batch = {
        "batch_id": "batch-missing",
        "turn_id": "turn-missing",
        "results": [row],
        "raw_results": [dict(row)],
        "success_count": 0,
        "failure_count": 1,
        "pending_async_count": int(defect == "pending"),
        "has_pending_async": defect == "pending",
        "effect_receipts": [],
    }
    if defect in {"readonly_null", "nonnull_commit"}:
        batch["results"] = [
            {
                **row,
                "directed_effect_claim_status": None,
                "directed_effect_mutation_status": None,
                "effect_receipt_commit": None if defect == "readonly_null" else {"operation_id": "foreign"},
            }
        ]
    batch_hash = stable_hash(batch)
    observation = CommittedTurnReceiptObservationV1(
        workspace=str(tmp_path / "foreign") if defect == "workspace" else str(tmp_path),
        run_id="foreign" if defect == "run" else "run-missing",
        task_id="foreign" if defect == "task" else "TASK-1",
        turn_id="foreign" if defect == "turn" else "turn-missing",
        batch_id="batch-missing",
        status="unknown" if defect == "unknown" else "observed",
        reason="owner committed receipt",
        batch_receipt_json="{" if defect == "malformed" else json.dumps(batch),
        batch_receipt_hash="0" * 64 if defect == "hash" else batch_hash,
        batch_receipt_refs=(("batch-missing", "f" * 64 if defect == "refs" else batch_hash),),
        native_call_ids=("foreign" if defect == "native_ids" else "call-read",),
        effect_class="readonly",
        fact_event_id="owner-event",
        fact_event_seq=1,
        outcome_hash="a" * 64,
    )
    observations = (observation, observation) if defect == "duplicate" else (observation,)
    projection = {
        "tool_lifecycle": {
            "events": [
                {
                    "status": "failed",
                    "dispatched_tool_calls_count": 1,
                    "receipt": {
                        "run_id": "run-missing",
                        "task_id": "TASK-1",
                        "turn_id": "turn-missing",
                        "batch_receipt_refs": [{"batch_id": "batch-missing", "receipt_hash": batch_hash}],
                    },
                }
            ]
        }
    }
    assert _terminal_failure_has_closed_dispatches(
        [{"task_id": "TASK-1", "execution_state": "failed"}],
        projection,
        [{"physical_evidence": {"batch_receipt": {**batch, "pending_async_count": 1}}}]
        if defect == "ledger_conflict"
        else [],
        committed_turn_receipts=observations,
        workspace=str(tmp_path),
    ) is (defect in {"", "readonly_null"})


@pytest.mark.parametrize(
    "defect",
    [
        "",
        "active",
        "missing_owner",
        "foreign_owner",
        "native",
        "pending_batch",
        "command_ref",
        "unknown_source",
        "effect",
        "duplicate_owner",
    ],
)
def test_artifact_receipt_reuse_requires_exact_terminal_zero_effect_owner(defect: str) -> None:
    receipt = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "compatibility_mode": "receipt_reuse",
        "dispatch_status": "verified_existing_artifacts",
        "ok": True,
        "native_tool_calls_count": 0,
        "decoded_tool_calls_count": 0,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "batch_receipt_refs": [],
        "native_tool_call_envelope_refs": [],
        "dropped_tool_calls": [],
        "batch_receipt_hash": "",
        "provider_response_hash": "",
        "run_id": "preflight",
        "task_id": "TASK-1",
        "effect_receipt_count": 1,
        "effect_receipt_refs": [
            {
                "source": "runtime.execution_broker.project_artifact_receipt.v1",
                "receipt_outcome": "success",
                "receipt_ref": "execution-broker://project-verification/artifact/" + "a" * 64,
            }
        ],
    }
    if defect == "native":
        receipt["native_tool_calls_count"] = 1
    elif defect == "pending_batch":
        receipt["batch_receipt_refs"] = [{"batch_id": "pending"}]
    elif defect == "command_ref":
        receipt["effect_receipt_refs"][0]["receipt_ref"] = "execution-broker://project-verification/command/" + "a" * 64
    elif defect == "unknown_source":
        receipt["effect_receipt_refs"][0]["source"] = "caller"
    owner = SimpleNamespace(
        status="active" if defect == "active" else "closed",
        execution_attempt=SimpleNamespace(
            workspace="workspace",
            run_id="foreign" if defect == "foreign_owner" else "preflight",
            external_task_id="TASK-1",
        ),
        receipts=("effect",) if defect == "effect" else (),
    )
    closures = () if defect == "missing_owner" else (owner, owner) if defect == "duplicate_owner" else (owner,)
    assert _closed_artifact_receipt_reuse(receipt, closures, workspace="workspace") is (defect == "")


@pytest.mark.asyncio
async def test_real_dead_letter_nonzero_cli_releases_without_positive_effect_receipts(tmp_path: Path) -> None:
    from polaris.cells.runtime.task_runtime.public import (
        BindRuntimeTaskToFactoryRunCommandV1,
        bind_runtime_task_to_factory_run,
    )
    from polaris.cells.runtime.task_runtime.public.tests.test_dead_letter_execution_effect_closure import (
        _real_failed_cli,
    )

    attempt, batch = await _real_failed_cli(tmp_path)
    factory_run_id = "factory-dead-letter-cleanup"
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(attempt.task_id), factory_run_id=factory_run_id
        )
    ).ok
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id=batch["turn_id"],
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[batch],
    ).to_dict()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id=batch["turn_id"],
            role="director",
            project_id="project-1",
            lifecycle_receipt=lifecycle,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": False, "summary": "CLI failed"},
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "project-1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "required_evidence_modalities": ["qa", "command"],
                        "enabled_evidence_modalities": ["qa", "command"],
                    },
                },
                "physical_evidence": {"batch_receipt": batch},
            },
        )
    )
    result = query_factory_settlement_barrier(tmp_path, factory_run_id)
    assert result.closed is True, result
    assert result.release_allowed is True
    assert result.passed is False
    assert result.effect_receipt_count == 0
    assert set(result.missing_required_modalities) == {"qa", "command"}
