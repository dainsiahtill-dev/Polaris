"""File prewrite negatives bind real producers and cold owner observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

import pytest
from polaris.cells.roles.adapters.tests.file_prewrite_failure_scenarios import inject_file_prewrite_failure
from polaris.cells.roles.kernel.tests.run_ledger_owner_scenarios import (
    continue_file_edit_owner_case,
    produce_file_prewrite_owner_case,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)


async def _file_rejection(
    root: Path,
    *,
    aliases: bool = False,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    settle: bool = True,
    expect_unchanged: bool = True,
) -> tuple[Any, dict[str, Any], Any]:
    case = await produce_file_prewrite_owner_case(
        root,
        aliases=aliases,
        mutate=mutate,
        settle=settle,
        expect_unchanged=expect_unchanged,
    )
    return case["attempt"], case["batch"], case["settled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("aliases", [False, True])
async def test_real_prewrite_file_rejection_closes_without_command_exit_or_mutation_receipt(
    tmp_path: Path, aliases: bool
) -> None:
    attempt, batch, settled = await _file_rejection(tmp_path, aliases=aliases)
    row = batch["raw_results"][0]
    assert row["result"]["failure_kind"] == "file_prewrite_rejected"
    assert "exit_code" not in row["result"]
    assert row["effect_receipt"] is row["effect_receipt_commit"] is None
    assert settled.success, settled
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "closed", observed
    assert observed.receipts == () and len(observed.terminal_failures) == 1
    assert observed.terminal_failures[0].exit_code is None
    assert observed.terminal_failures[0].failure_kind == "file_prewrite_rejected"
    assert observed.execution_authority is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "native_hash",
        "normalized_arguments",
        "frozen_snapshot",
        "proof_arguments_hash",
        "proof_target",
        "proof_before_hash",
        "proof_context_hash",
    ],
)
async def test_cold_file_negative_rejects_contradictory_owner_inputs(tmp_path: Path, defect: str) -> None:
    def corrupt(outcome: dict[str, Any]) -> None:
        if defect == "native_hash":
            outcome["decision"]["metadata"]["native_tool_call_envelopes"][0]["arguments_hash"] = "f" * 64
        elif defect == "normalized_arguments":
            outcome["execution"]["invocations"][0]["arguments"]["start"] = 1
        elif defect == "frozen_snapshot":
            outcome["execution"]["invocations"][0]["classification"]["snapshot"]["tool_spec_hash"] = "f" * 64
        else:
            field = {
                "proof_arguments_hash": "arguments_hash",
                "proof_target": "target_path",
                "proof_before_hash": "before_sha256",
                "proof_context_hash": "normalized_operation_hash",
            }[defect]
            value = "foreign.py" if field == "target_path" else "f" * 64
            for key in ("results", "raw_results"):
                outcome["execution"]["receipt"][key][0]["result"]["prewrite_rejection"][field] = value

    attempt, _, settled = await _file_rejection(tmp_path, aliases=True, mutate=corrupt)
    assert settled.success, settled
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "unknown", observed


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["generic_error", "lookalike_dict", "partial_io"])
async def test_generic_file_result_never_becomes_noeffect_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    with inject_file_prewrite_failure(tmp_path, case=case) as injected:
        attempt, batch, settled = await _file_rejection(tmp_path, settle=True, expect_unchanged=case != "partial_io")
    assert injected["calls"] == 1 and injected["synthetic_fault"] is True
    assert settled is not None and not settled.success
    row = batch["raw_results"][0]
    assert row["result"]["effect_recovery"]["state"] == "RECOVERY_PENDING"
    assert row["effect_receipt"] is row["effect_receipt_commit"] is None
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status != "closed"


async def _continue_legal_edit(root: Path, attempt: Any) -> tuple[Any, dict[str, Any]]:
    case = await continue_file_edit_owner_case(root, attempt)
    return case["attempt"], case["batch"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "completed"])
async def test_proven_bad_call_allows_same_owner_legal_next_batch_and_receipt(
    tmp_path: Path, outcome: Literal["failed", "completed"]
) -> None:
    attempt, bad, _ = await _file_rejection(tmp_path, aliases=True, settle=False)
    assert bad["raw_results"][0]["result"]["failure_kind"] == "file_prewrite_rejected"
    followup, good = await _continue_legal_edit(tmp_path, attempt)
    assert followup.session_id == attempt.session_id and followup.attempt == attempt.attempt
    assert good["raw_results"][0]["status"] == "success", good
    assert good["raw_results"][0]["effect_receipt_commit"]["code"] == "receipt_committed"
    assert (tmp_path / "src/a.txt").read_text(encoding="utf-8") == "alpha\nBETA\n"
    settled = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(tmp_path),
            identity=followup,
            outcome=outcome,
            summary="edit recovered; independent delivery acceptance remains failed",
        )
    )
    assert settled.success, settled
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), followup))
    assert observed.status == "closed", observed
    assert len(observed.receipts) == len(observed.terminal_failures) == 1


@pytest.mark.asyncio
async def test_live_typed_file_proof_with_foreign_actual_arguments_is_not_terminalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with inject_file_prewrite_failure(tmp_path, case="foreign_typed_arguments") as injected:
        attempt, batch, settled = await _file_rejection(tmp_path)
    assert injected["calls"] == 1 and injected["synthetic_fault"] is True
    assert settled is not None and not settled.success
    assert batch["raw_results"][0]["result"]["effect_recovery"]["state"] == "RECOVERY_PENDING"
    assert query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt)).status != "closed"
