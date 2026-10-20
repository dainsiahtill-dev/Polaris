"""File prewrite negatives bind real producers and cold owner observations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Literal

import pytest
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, FactStreamProvenanceV1, append_fact_event
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import production
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.kernelone.storage import resolve_workspace_runtime_identity


def _commit_owner(
    root: Path,
    attempt: Any,
    invocation: Any,
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    turn = batch["turn_id"]
    transition = "file-prewrite-transition"
    provenance = FactStreamProvenanceV1(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id=turn,
        transition_id=transition,
    )
    storage = resolve_workspace_runtime_identity(str(root))
    outcome = {
        "schema_version": "roles.kernel.turn_outcome.v1",
        "run_id": attempt.run_id,
        "turn_id": turn,
        "decision": {
            "metadata": {
                "native_tool_call_envelopes": [
                    {
                        "call_id": str(invocation.call_id),
                        "tool_name": invocation.tool_name,
                        "arguments_hash": stable_hash(native),
                    }
                ]
            }
        },
        "execution": {
            "batch_id": batch["batch_id"],
            "receipt": batch,
            "side_effect_class": "mutating",
            "invocations": [invocation.model_dump(mode="json")],
        },
    }
    if mutate:
        mutate(outcome)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(root),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            correlation_id=turn,
            provenance=provenance,
            strict_integrity=True,
            idempotency_key=":".join(
                (
                    "roles.kernel.turn_outcomes",
                    storage.token,
                    attempt.run_id,
                    attempt.external_task_id,
                    turn,
                    transition,
                )
            ),
            payload={
                "schema_version": "roles.kernel.turn_outcome_fact.v2",
                "run_id": attempt.run_id,
                "task_id": attempt.external_task_id,
                "turn_id": turn,
                "transition_id": transition,
                "provenance": provenance.to_record(),
                "storage_identity": storage.to_record(),
                "session_binding": {"session_id": attempt.session_id},
                "outcome": outcome,
                "outcome_hash": stable_hash(outcome),
            },
        )
    )


async def _file_rejection(
    root: Path,
    *,
    aliases: bool = False,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    settle: bool = True,
    expect_unchanged: bool = True,
) -> tuple[Any, dict[str, Any], Any]:
    attempt = production._setup_attempt(str(root))
    (root / "src").mkdir(exist_ok=True)
    path = root / "src/a.txt"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    args = {"file": "src/a.txt", "start": 7, "end": 9, "replace": ""}
    native = {"file": "src/a.txt", "startLine": 7, "endLine": 9, "newText": ""} if aliases else dict(args)
    turn, batch_id, call_id = "file-prewrite-turn", "file-prewrite-batch", "file-prewrite-call"
    candidate = production._candidate(
        attempt,
        ordinal=0,
        tool_call_id=call_id,
        normalized_tool_name="edit_blocks",
        normalized_arguments=tuple(sorted(args.items())),
        target_path="src/a.txt",
        target_exists=True,
        target_before_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        is_no_file_state=False,
        turn_id=turn,
        batch_id=batch_id,
        allowed_commands=(),
        allowed_paths=("src/",),
    )
    policy = production._WorkspaceObservingPolicyPort(root)
    authority = production._authority(attempt)
    ready = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id=turn,
        batch_id=batch_id,
        candidates=(candidate,),
    )
    assert ready.status == "ready", ready
    prepared = ready.prepared_batch
    assert prepared is not None
    fence = production.create_directed_effect_fence_ports()
    port = production.create_director_directed_effect_mutation_port(
        workspace=str(root), policy_snapshot_port=policy, fence_consume_port=fence.consume
    )
    runtime = production.ToolBatchRuntime(
        executor=production._reject_non_mutation_execution,
        directed_effect_runtime=production.DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy, fence_admin_port=fence.admin, mutation_port=port
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            (call_id, production._job_restriction_evidence(allowed_commands=(), allowed_paths=("src/",))),
        ),
        directed_effect_dispatch_call_ids=(call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(call_id), tool_name="edit_blocks", arguments=args, native_arguments=native
    )
    receipts = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId(batch_id), invocations=[invocation], serial_writes=[invocation]
        ),
        production.TurnId(turn),
    )
    batch = receipts[0].to_dict()
    _commit_owner(root, prepared.execution_attempt, invocation, batch, native, mutate=mutate)
    result = None
    if settle:
        result = settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(root),
                identity=prepared.execution_attempt,
                outcome="failed",
                summary="private file rejection; delivery failed",
            )
        )
    if expect_unchanged:
        assert path.read_text(encoding="utf-8") == "alpha\nbeta\n"
    return prepared.execution_attempt, batch, result


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
    from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor

    def uncertain(self: Any, *_: Any, **__: Any) -> dict[str, Any]:
        if case == "partial_io":
            (tmp_path / "src/a.txt").write_text("partial physical write\n", encoding="utf-8")
        if case == "lookalike_dict":
            return {
                "ok": True,
                "no_op": True,
                "schema_version": "kernelone.editing.prewrite_rejection.v1",
                "write_started": False,
                "reason": "untrusted dict",
            }
        return {"ok": False, "error": "opaque physical failure"}

    monkeypatch.setattr(DirectorToolExecutor, "_tool_edit_blocks", uncertain)
    attempt, batch, settled = await _file_rejection(tmp_path, settle=True, expect_unchanged=case != "partial_io")
    assert settled is not None and not settled.success
    row = batch["raw_results"][0]
    assert row["result"]["effect_recovery"]["state"] == "RECOVERY_PENDING"
    assert row["effect_receipt"] is row["effect_receipt_commit"] is None
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status != "closed"


async def _continue_legal_edit(root: Path, attempt: Any) -> tuple[Any, dict[str, Any]]:
    args = {"file": "src/a.txt", "blocks": "<<<< SEARCH\nbeta\n====\nBETA\n>>>> REPLACE\n"}
    turn, batch_id, call_id = "file-followup-turn", "file-followup-batch", "file-followup-call"
    candidate = production._candidate(
        attempt,
        ordinal=0,
        tool_call_id=call_id,
        normalized_tool_name="edit_blocks",
        normalized_arguments=tuple(sorted(args.items())),
        target_path="src/a.txt",
        target_exists=True,
        target_before_content_hash=hashlib.sha256((root / "src/a.txt").read_bytes()).hexdigest(),
        is_no_file_state=False,
        turn_id=turn,
        batch_id=batch_id,
        allowed_commands=(),
        allowed_paths=("src/",),
    )
    policy = production._WorkspaceObservingPolicyPort(root)
    authority = production._authority(attempt)
    ready = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id=turn,
        batch_id=batch_id,
        candidates=(candidate,),
    )
    assert ready.status == "ready", ready
    prepared = ready.prepared_batch
    assert prepared is not None
    fence = production.create_directed_effect_fence_ports()
    port = production.create_director_directed_effect_mutation_port(
        workspace=str(root), policy_snapshot_port=policy, fence_consume_port=fence.consume
    )
    runtime = production.ToolBatchRuntime(
        executor=production._reject_non_mutation_execution,
        directed_effect_runtime=production.DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy, fence_admin_port=fence.admin, mutation_port=port
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            (call_id, production._job_restriction_evidence(allowed_commands=(), allowed_paths=("src/",))),
        ),
        directed_effect_dispatch_call_ids=(call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(call_id), tool_name="edit_blocks", arguments=args, native_arguments=dict(args)
    )
    receipts = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId(batch_id), invocations=[invocation], serial_writes=[invocation]
        ),
        production.TurnId(turn),
    )
    batch = receipts[0].to_dict()
    _commit_owner(root, prepared.execution_attempt, invocation, batch, dict(args))
    return prepared.execution_attempt, batch


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
    from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
    from polaris.kernelone.editing.contracts import EditPrewriteRejectionV1

    original = DirectorToolExecutor._tool_edit_blocks

    def forged(self: Any, *args: Any, **kwargs: Any) -> Any:
        proof = original(self, *args, **kwargs)
        assert type(proof) is EditPrewriteRejectionV1
        object.__setattr__(proof, "arguments_hash", "f" * 64)
        return proof

    monkeypatch.setattr(DirectorToolExecutor, "_tool_edit_blocks", forged)
    attempt, batch, settled = await _file_rejection(tmp_path)
    assert settled is not None and not settled.success
    assert batch["raw_results"][0]["result"]["effect_recovery"]["state"] == "RECOVERY_PENDING"
    assert query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt)).status != "closed"
