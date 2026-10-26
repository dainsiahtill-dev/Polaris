"""Known file negatives close resources without accepting failed delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    query_factory_settlement_barrier,
)
from polaris.cells.roles.kernel.tests.run_ledger_owner_scenarios import produce_file_prewrite_owner_case
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    bind_runtime_task_to_factory_run,
)


def _publish_failed_lifecycle(root: Path, attempt: Any, batch: dict[str, Any]) -> str:
    factory_run = "factory-file-prewrite-negative"
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(root), task_id=str(attempt.task_id), factory_run_id=factory_run
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
            workspace=str(root),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id=batch["turn_id"],
            role="director",
            project_id="private-file-project",
            lifecycle_receipt=lifecycle,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(root),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": False, "summary": "file edit rejected; delivery failed"},
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "private-file-project",
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
    return factory_run


@pytest.mark.asyncio
async def test_real_file_prewrite_proof_closes_only_resources_without_receipt_credit(tmp_path: Path) -> None:
    case = await produce_file_prewrite_owner_case(
        tmp_path,
        aliases=True,
        factory_run_id="factory-file-prewrite-negative",
    )
    attempt, batch, settled = case["attempt"], case["batch"], case["settled"]
    assert settled.success, settled
    factory_run = _publish_failed_lifecycle(tmp_path, attempt, batch)
    observed = query_factory_settlement_barrier(tmp_path, factory_run)
    assert observed.closed and observed.release_allowed, observed
    assert observed.passed is False
    assert observed.effect_receipt_count == 0
    assert observed.expected_effect_count == 0
    assert observed.open_effect_count == 0
    assert set(observed.missing_required_modalities) == {"qa", "command"}


@pytest.mark.asyncio
async def test_missing_or_mismatched_file_negative_proof_keeps_resources_unknown(tmp_path: Path) -> None:
    def corrupt(outcome: dict[str, Any]) -> None:
        for key in ("results", "raw_results"):
            outcome["execution"]["receipt"][key][0]["result"].pop("prewrite_rejection")

    case = await produce_file_prewrite_owner_case(
        tmp_path,
        aliases=True,
        mutate=corrupt,
        factory_run_id="factory-file-prewrite-negative",
    )
    attempt, batch, settled = case["attempt"], case["batch"], case["settled"]
    assert settled.success, settled
    factory_run = _publish_failed_lifecycle(tmp_path, attempt, batch)
    observed = query_factory_settlement_barrier(tmp_path, factory_run)
    assert not observed.closed and not observed.release_allowed and not observed.passed
    assert "resource_closure_unproven" in observed.blocking_reasons
